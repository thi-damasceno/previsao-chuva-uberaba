"""
Previsão diária de chuva para Uberaba-MG — roda no GitHub Actions todo dia (~20h, horário de Brasília).

Lê:   modelo/modelo_chuva_amanha.joblib, modelo/mapeamento_jma_inmet.json
Grava: docs/data/previsao.json   (previsão de amanhã, usada no topo do dashboard)
       docs/data/historico.json  (todas as previsões já emitidas)
"""
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd

import pipeline_chuva as pc

RAIZ = Path(__file__).resolve().parent
ARQ_MODELO = RAIZ / "modelo" / "modelo_chuva_amanha.joblib"
ARQ_MAPA = RAIZ / "modelo" / "mapeamento_jma_inmet.json"
DIR_DADOS = RAIZ / "docs" / "data"
ARQ_PREV = DIR_DADOS / "previsao.json"
ARQ_HIST = DIR_DADOS / "historico.json"
FUSO = ZoneInfo("America/Sao_Paulo")


def categoria(p, limiar):
    """Faixas coerentes com o limiar do modelo: a partir dele, a chuva é ao menos 'Possível'."""
    for lim, txt in [(limiar / 2, "Improvável"), (limiar, "Pouco provável"), (0.6, "Possível"), (0.8, "Provável")]:
        if p < lim:
            return txt
    return "Muito provável"


def num(x, casas=1):
    return None if x is None or pd.isna(x) else round(float(x), casas)


def salvar_json(caminho, obj):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    agora = datetime.now(FUSO)
    hoje = pd.Timestamp(agora.date())
    amanha = hoje + pd.Timedelta(days=1)

    pacote = joblib.load(ARQ_MODELO)
    mapa_json = json.loads(ARQ_MAPA.read_text(encoding="utf-8"))
    mapa = mapa_json["variaveis"]
    # limiar escolhido para o cenário JMA (notebook de preparação); senão, o do modelo
    limiar = mapa_json.get("limiar_dashboard", pacote["limiar"])

    h_obs, h_prev = pc.baixar_operacional(past_days=40, forecast_days=3)
    tabela = pc.montar_tabela(h_obs, h_prev, mapa)
    prob, X = pc.prever(pacote, tabela, hoje)
    n_faltando = int(X.isna().sum())

    # Detalhes da previsão numérica para amanhã (rodada mais recente do JMA), hora a hora, horário local
    loc = h_prev.copy()
    loc.index = loc.index - pd.Timedelta(hours=3)
    dia_am = loc.loc[str(amanha.date())]
    horario = [{"hora": t.strftime("%H:%M"),
                "chuva_mm": num(r["precipitation"], 2),
                "nuvens_pct": num(r["cloud_cover"], 0),
                "temp_c": num(r["temperature_2m"], 1)} for t, r in dia_am.iterrows()]

    # Últimos 14 dias segundo o JMA convertido para a escala da estação (substitui o INMET)
    estim = pc.aplicar_mapeamento(pc.jma_para_inmet_diario(h_obs), mapa)
    ult = estim.loc[hoje - pd.Timedelta(days=13): hoje]
    recentes = [{"data": d.strftime("%Y-%m-%d"), "chuva_est_mm": num(r["precip_mm"]),
                 "temp_max_c": num(r["temp_max_c"]), "umid_media_pct": num(r["umid_media_pct"], 0)}
                for d, r in ult.iterrows()]

    nwp = f"nwp_{pc.MODELO}_"
    previsao = {
        "status": "ok",
        "gerado_em": agora.isoformat(timespec="minutes"),
        "data_base": str(hoje.date()),
        "data_alvo": str(amanha.date()),
        "probabilidade": round(prob, 3),
        "categoria": categoria(prob, limiar),
        "limiar": limiar,
        "vai_chover": bool(prob >= limiar),
        "jma_amanha": {
            "chuva_mm": num(X.get(nwp + "precip_mm")),
            "horas_chuva": num(X.get(nwp + "horas_chuva"), 0),
            "nuvens_pct": num(X.get(nwp + "nuvens_pct"), 0),
            "umid_pct": num(X.get(nwp + "umid_pct"), 0),
            "temp_max_c": num(X.get(nwp + "temp_max_c")),
        },
        "condicoes_hoje": {
            "chuva_est_mm": num(X.get("precip_mm")),
            "chuva_acum_7d_mm": num(X.get("precip_acum_7d")),
            "chuva_acum_30d_mm": num(X.get("precip_acum_30d")),
            "dias_sem_chuva": num(X.get("dias_sem_chuva"), 0),
            "umid_media_pct": num(X.get("umid_media_pct"), 0),
            "temp_max_c": num(X.get("temp_max_c")),
        },
        "horario_amanha": horario,
        "ultimos_dias": recentes,
        "features_ausentes": n_faltando,
        "modelo": {"treinado_ate": pacote.get("treinado_ate"), "fonte": "JMA GSM via Open-Meteo",
                   "observacoes": "JMA convertido para a escala da estação INMET A568 (mapeamento de quantis)"},
    }
    salvar_json(ARQ_PREV, previsao)

    # Histórico: uma linha por data-alvo (se rodar duas vezes no dia, fica a mais recente)
    hist = json.loads(ARQ_HIST.read_text(encoding="utf-8")) if ARQ_HIST.exists() else []
    hist = [h for h in hist if h["data_alvo"] != previsao["data_alvo"]]
    hist.append({"data_alvo": previsao["data_alvo"], "probabilidade": previsao["probabilidade"],
                 "jma_chuva_mm": previsao["jma_amanha"]["chuva_mm"], "gerado_em": previsao["gerado_em"]})
    hist = sorted(hist, key=lambda h: h["data_alvo"])[-730:]
    salvar_json(ARQ_HIST, hist)

    print(f"{previsao['data_alvo']}: {prob:.0%} ({previsao['categoria']}) | features ausentes: {n_faltando}")
    if n_faltando > 10:
        print("AVISO: muitas features ausentes — verifique o Open-Meteo.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        # Mantém a última previsão válida e registra o erro para o dashboard exibir
        anterior = json.loads(ARQ_PREV.read_text(encoding="utf-8")) if ARQ_PREV.exists() else {}
        anterior.update({"status": "erro", "erro": str(e)[:300],
                         "erro_em": datetime.now(FUSO).isoformat(timespec="minutes")})
        salvar_json(ARQ_PREV, anterior)
        sys.exit(1)
