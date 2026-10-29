"""
Previsão diária de chuva para Uberaba-MG — roda no GitHub Actions todo dia (~23h30, horário de Brasília),
logo depois de coletar_inmet.py.

Entradas do modelo:
  - medições da estação INMET A568 (hoje e dias anteriores) — dados/inmet_A568_horario.csv
  - previsão do modelo JMA GSM para amanhã (Open-Meteo)
  - nos dias sem medição do INMET, o JMA cobre a lacuna (indicado no dashboard)

Grava: docs/data/previsao.json  (previsão de amanhã + condições medidas)
       docs/data/historico.json (previsões emitidas + chuva observada depois, para verificação)
"""
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import pandas as pd

import inmet_obs as io_
import pipeline_chuva as pc

RAIZ = Path(__file__).resolve().parent
ARQ_MODELO = RAIZ / "modelo" / "modelo_chuva_amanha.joblib"
ARQ_MAPA = RAIZ / "modelo" / "mapeamento_jma_inmet.json"
ARQ_OBS = RAIZ / "dados" / "inmet_A568_horario.csv"
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

    # Observações medidas (INMET) e JMA
    obs_h = io_.carregar_base(ARQ_OBS)
    obs_d = io_.diario(obs_h.loc[hoje - pd.Timedelta(days=45):]) if len(obs_h) else pd.DataFrame()
    h_obs_jma, h_prev = pc.baixar_operacional(past_days=40, forecast_days=3)
    tabela, fonte, dia = pc.montar_tabela_hibrida(obs_d, h_obs_jma, h_prev, mapa_json["variaveis"])

    # Limiar: o do modelo quando hoje é medido pelo INMET (cenário do treino);
    # o limiar do cenário JMA quando hoje não tem medição.
    fonte_hoje = fonte.get(hoje, "JMA")
    limiar = pacote["limiar"] if fonte_hoje == "INMET" else mapa_json.get("limiar_dashboard", pacote["limiar"])

    prob, X = pc.prever(pacote, tabela, hoje)
    n_faltando = int(X.isna().sum())

    # Previsão do JMA para amanhã, hora a hora (horário local)
    loc = h_prev.copy()
    loc.index = loc.index - pd.Timedelta(hours=3)
    dia_am = loc.loc[str(amanha.date())]
    horario = [{"hora": t.strftime("%H:%M"),
                "chuva_mm": num(r["precipitation"], 2),
                "nuvens_pct": num(r["cloud_cover"], 0),
                "temp_c": num(r["temperature_2m"], 1)} for t, r in dia_am.iterrows()]

    # Últimos 14 dias: chuva medida (INMET); JMA só onde não houver medição
    ult = dia.loc[hoje - pd.Timedelta(days=13): hoje]
    recentes = [{"data": d.strftime("%Y-%m-%d"), "chuva_mm": num(r["precip_mm"]),
                 "fonte": "INMET" if pd.notna(obs_d["precip_mm"].get(d) if len(obs_d) else None) else "JMA",
                 "temp_max_c": num(r["temp_max_c"]), "umid_media_pct": num(r["umid_media_pct"], 0)}
                for d, r in ult.iterrows()]
    ultima_hora = obs_h.dropna(how="all").index.max() if len(obs_h) else None

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
            "chuva_mm": num(X.get("precip_mm")),
            "chuva_acum_7d_mm": num(ult["precip_mm"].iloc[-7:].sum(min_count=1)),
            "chuva_acum_30d_mm": num(dia.loc[hoje - pd.Timedelta(days=29): hoje, "precip_mm"].sum(min_count=1)),
            "dias_sem_chuva": num(X.get("dias_sem_chuva"), 0),
            "umid_media_pct": num(X.get("umid_media_pct"), 0),
            "temp_max_c": num(X.get("temp_max_c")),
        },
        "observacoes": {
            "fonte_hoje": fonte_hoje,
            "dias_inmet_30d": int((fonte.loc[hoje - pd.Timedelta(days=29): hoje] == "INMET").sum()),
            "ultima_medicao_utc": str(ultima_hora) if ultima_hora is not None else None,
        },
        "horario_amanha": horario,
        "ultimos_dias": recentes,
        "features_ausentes": n_faltando,
        "modelo": {"treinado_ate": pacote.get("treinado_ate"),
                   "fonte": "INMET A568 (medições) + JMA GSM via Open-Meteo (previsão para amanhã)"},
    }
    salvar_json(ARQ_PREV, previsao)

    # Histórico: uma linha por data-alvo; preenche a chuva observada quando o INMET tiver medido o dia
    hist = json.loads(ARQ_HIST.read_text(encoding="utf-8")) if ARQ_HIST.exists() else []
    hist = [h for h in hist if h["data_alvo"] != previsao["data_alvo"]]
    hist.append({"data_alvo": previsao["data_alvo"], "probabilidade": previsao["probabilidade"],
                 "limiar": limiar, "jma_chuva_mm": previsao["jma_amanha"]["chuva_mm"],
                 "gerado_em": previsao["gerado_em"]})
    for h in hist:
        d = pd.Timestamp(h["data_alvo"])
        if d < hoje and len(obs_d) and pd.notna(obs_d["precip_mm"].get(d)):
            h["chuva_obs_mm"] = num(obs_d["precip_mm"][d])
            h["choveu"] = bool(obs_d["precip_mm"][d] >= 1)
    hist = sorted(hist, key=lambda h: h["data_alvo"])[-730:]
    salvar_json(ARQ_HIST, hist)

    print(f"{previsao['data_alvo']}: {prob:.0%} ({previsao['categoria']}) | hoje medido por: {fonte_hoje} | "
          f"dias INMET nos últimos 30: {previsao['observacoes']['dias_inmet_30d']} | features ausentes: {n_faltando}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        anterior = json.loads(ARQ_PREV.read_text(encoding="utf-8")) if ARQ_PREV.exists() else {}
        anterior.update({"status": "erro", "erro": str(e)[:300],
                         "erro_em": datetime.now(FUSO).isoformat(timespec="minutes")})
        salvar_json(ARQ_PREV, anterior)
        sys.exit(1)
