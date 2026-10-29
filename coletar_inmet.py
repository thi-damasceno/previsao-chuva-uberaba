"""
coletar_inmet.py — atualiza dados/inmet_A568_horario.csv com as medições recentes da estação A568.

Ordem de tentativas:
  1. CSVs colocados manualmente em dados/importar/ (ex.: baixados da página TEMPO) -> importados e movidos.
  2. Página TEMPO aberta num navegador automatizado (Playwright): gera a tabela dos últimos dias
     e baixa o CSV pelo botão "Baixar CSV". Se a página expuser o JSON da API, ele também é usado.

Nunca derruba a execução: se a coleta falhar, a previsão usa o JMA nos dias sem medição.
Grava um diagnóstico em dados/diagnostico/ (enviado como artefato pelo GitHub Actions).
"""
import json
import re
import shutil
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import inmet_obs as io_

RAIZ = Path(__file__).resolve().parent
ARQ_BASE = RAIZ / "dados" / "inmet_A568_horario.csv"
DIR_IMPORTAR = RAIZ / "dados" / "importar"
DIR_DIAG = RAIZ / "dados" / "diagnostico"
ESTACAO = "A568"
URL = f"https://tempo.inmet.gov.br/TabelaEstacoes/{ESTACAO}"
FUSO = ZoneInfo("America/Sao_Paulo")
DIAS_ATRAS = 3            # pede alguns dias para trás e recompõe horas que faltaram em coletas anteriores


def importar_manuais(base):
    arquivos = sorted(DIR_IMPORTAR.glob("*.csv")) + sorted(DIR_IMPORTAR.glob("*.CSV"))
    for arq in arquivos:
        try:
            novos = io_.ler_csv_tempo(arq)
            base = io_.mesclar(base, novos)
            destino = DIR_IMPORTAR / "processados"
            destino.mkdir(parents=True, exist_ok=True)
            shutil.move(str(arq), destino / arq.name)
            print(f"Importado manualmente: {arq.name} ({novos.dropna(how='all').shape[0]} horas)")
        except Exception as e:
            print(f"Falha ao importar {arq.name}: {e}")
    return base


def _preencher_datas(page, ini, fim, diag):
    """Tenta preencher o período na página. Registra o que encontrou para o diagnóstico."""
    entradas = page.locator("input")
    info = []
    for i in range(entradas.count()):
        e = entradas.nth(i)
        try:
            info.append({"type": e.get_attribute("type"), "name": e.get_attribute("name"),
                         "placeholder": e.get_attribute("placeholder"), "value": e.input_value()})
        except Exception:
            pass
    diag["inputs"] = info

    datas = page.locator("input[type=date]")
    if datas.count() >= 2:
        datas.nth(0).fill(ini.strftime("%Y-%m-%d"))
        datas.nth(1).fill(fim.strftime("%Y-%m-%d"))
        diag["datas_preenchidas"] = "input[type=date]"
        return
    # campos de texto com data dd/mm/aaaa ou aaaa-mm-dd
    alvos = [i for i, x in enumerate(info)
             if re.match(r"^(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})$", (x.get("value") or "").strip())]
    if len(alvos) >= 2:
        fmt = "%d/%m/%Y" if "/" in info[alvos[0]]["value"] else "%Y-%m-%d"
        for i, d in zip(alvos[:2], (ini, fim)):
            campo = entradas.nth(i)
            campo.click()
            campo.fill(d.strftime(fmt))
            campo.press("Enter")
        diag["datas_preenchidas"] = f"texto ({fmt})"


def coletar_pagina():
    from playwright.sync_api import sync_playwright

    DIR_DIAG.mkdir(parents=True, exist_ok=True)
    diag = {"inicio": datetime.now(FUSO).isoformat(timespec="seconds"), "url": URL, "respostas_json": []}
    hoje_local = datetime.now(FUSO).date()
    ini, fim = hoje_local - timedelta(days=DIAS_ATRAS), hoje_local
    jsons, csv_bytes = [], None

    with sync_playwright() as p:
        nav = p.chromium.launch()
        ctx = nav.new_context(locale="pt-BR", timezone_id="America/Sao_Paulo", accept_downloads=True,
                              viewport={"width": 1400, "height": 1000})
        page = ctx.new_page()

        def ao_responder(resp):
            try:
                tipo = resp.headers.get("content-type", "")
                if "json" in tipo and "inmet" in resp.url:
                    corpo = resp.json()
                    diag["respostas_json"].append({"url": resp.url, "status": resp.status,
                                                   "n": len(corpo) if isinstance(corpo, list) else None})
                    if isinstance(corpo, list) and corpo and isinstance(corpo[0], dict) and "DT_MEDICAO" in corpo[0]:
                        jsons.append(corpo)
            except Exception:
                pass

        page.on("response", ao_responder)
        try:
            page.goto(URL, wait_until="networkidle", timeout=90_000)
            page.wait_for_timeout(2_000)
            try:
                _preencher_datas(page, ini, fim, diag)
            except Exception as e:
                diag["erro_datas"] = str(e)[:200]

            # Botão que gera a tabela (texto pode variar)
            for rotulo in ("Gerar Tabela", "Gerar tabela", "Gerar", "Pesquisar", "Buscar", "Consultar"):
                b = page.get_by_role("button", name=re.compile(rotulo, re.I))
                if b.count():
                    b.first.click()
                    diag["botao_gerar"] = rotulo
                    break
            page.wait_for_load_state("networkidle", timeout=60_000)
            page.wait_for_timeout(3_000)

            link = page.get_by_text(re.compile(r"Baixar CSV", re.I))
            if link.count():
                with page.expect_download(timeout=60_000) as dl:
                    link.first.click()
                caminho = dl.value.path()
                csv_bytes = Path(caminho).read_bytes()
                (DIR_DIAG / "ultimo_download.csv").write_bytes(csv_bytes)
                diag["csv_bytes"] = len(csv_bytes)
            else:
                diag["csv"] = "botão 'Baixar CSV' não encontrado"
        except Exception as e:
            diag["erro"] = f"{type(e).__name__}: {str(e)[:300]}"
        finally:
            try:
                page.screenshot(path=str(DIR_DIAG / "pagina.png"), full_page=True)
                diag["botoes"] = [t.strip() for t in page.locator("button, a.button, .ui.button").all_inner_texts()][:40]
            except Exception:
                pass
            nav.close()

    novos = []
    if csv_bytes:
        try:
            novos.append(io_.ler_csv_tempo(csv_bytes))
        except Exception as e:
            diag["erro_csv"] = str(e)[:200]
    for j in jsons:
        novos.append(io_.ler_json_api(j))
    diag["horas_coletadas"] = int(sum(n.dropna(how="all").shape[0] for n in novos))
    (DIR_DIAG / "coleta.json").write_text(json.dumps(diag, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in diag.items() if k != "inputs"}, ensure_ascii=False, indent=1))
    return pd.concat(novos) if novos else None


def main():
    base = io_.carregar_base(ARQ_BASE)
    antes = base.dropna(how="all").shape[0]
    base = importar_manuais(base)
    try:
        novos = coletar_pagina()
        base = io_.mesclar(base, novos)
    except Exception:
        traceback.print_exc()
        print("Coleta automática falhou — a previsão usará o JMA nos dias sem medição.")
    io_.salvar_base(base, ARQ_BASE)
    depois = base.dropna(how="all")
    print(f"Base INMET: {antes} -> {depois.shape[0]} horas | última hora: {depois.index.max()} UTC")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
    sys.exit(0)          # a coleta nunca interrompe o workflow
