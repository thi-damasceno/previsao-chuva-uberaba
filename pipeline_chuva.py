"""
pipeline_chuva.py — funções comuns ao notebook de preparação e ao script diário.

Fluxo da previsão diária (dia D = hoje, alvo = D+1):
  1. Busca no Open-Meteo (modelo JMA GSM) as variáveis horárias dos últimos ~40 dias
     (previstas 24 h antes, "_previous_day1") e a previsão mais recente para amanhã.
  2. Converte o JMA em variáveis diárias com os mesmos nomes da base do INMET.
  3. Aplica o mapeamento de quantis JMA → INMET (ajustado no histórico 2017–2023),
     para que as variáveis fiquem na escala que o modelo conheceu no treino.
  4. Calcula as features exatamente como no tratamento original (lags, acumulados...).
  5. Junta as features da previsão numérica para amanhã (nwp_jma_gsm_*).

Uberaba: dia local = UTC deslocado 3 h (UTC-3 fixo), igual ao tratamento da base INMET.
"""
import time

import numpy as np
import pandas as pd
import requests

LAT, LON = -19.71, -47.96            # estação INMET A568 — Uberaba-MG
MODELO = "jma_gsm"
URL_PREV = "https://previous-runs-api.open-meteo.com/v1/forecast"

# Variáveis horárias do Open-Meteo (velocidade do vento em km/h — padrão da API,
# o mesmo usado no treino das features nwp_*)
VARIAVEIS = ["precipitation", "cloud_cover", "relative_humidity_2m", "dew_point_2m",
             "pressure_msl", "temperature_2m", "wind_speed_10m", "wind_direction_10m",
             "shortwave_radiation"]

# Variáveis diárias da base INMET que o JMA substitui
VARS_INMET = ["precip_mm", "precip_max_1h_mm", "horas_chuva", "temp_media_c", "temp_max_c",
              "temp_min_c", "orvalho_medio_c", "umid_media_pct", "umid_min_pct", "umid_max_pct",
              "pressao_media_mb", "pressao_amp_mb", "radiacao_mjm2", "vento_vel_ms",
              "vento_rajada_ms", "vento_u_ms", "vento_v_ms"]


# ---------------------------------------------------------------------------
# 1. Download
# ---------------------------------------------------------------------------
def _get(params, tentativas=4):
    for t in range(tentativas):
        try:
            r = requests.get(URL_PREV, params=params, timeout=120)
        except requests.RequestException:
            time.sleep(10 * (t + 1)); continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(30 * (t + 1)); continue
        return r.json()
    raise RuntimeError("Open-Meteo não respondeu após várias tentativas")


def _para_df(js, sufixo):
    h = pd.DataFrame(js["hourly"])
    h["time"] = pd.to_datetime(h["time"])
    h = h.set_index("time")
    cols = {}
    for c in h.columns:
        base = c.replace(f"_{MODELO}", "")
        if sufixo and base.endswith(sufixo):
            cols[c] = base[: -len(sufixo)]
        elif not sufixo and not base.endswith("_previous_day1"):
            cols[c] = base
    return h[list(cols)].rename(columns=cols)


def baixar_historico(inicio, fim, variaveis=VARIAVEIS, progresso=None):
    """Histórico horário (UTC) das previsões do JMA feitas 24 h antes, ano a ano.
    Variáveis que a API recusar são descartadas (com aviso)."""
    variaveis = list(variaveis)
    anos = range(int(inicio[:4]), int(fim[:4]) + 1)
    partes = []
    for ano in (progresso(anos) if progresso else anos):
        params = {"latitude": LAT, "longitude": LON, "timezone": "UTC", "models": MODELO,
                  "start_date": max(f"{ano}-01-01", inicio), "end_date": min(f"{ano}-12-31", fim)}
        while variaveis:
            params["hourly"] = ",".join(f"{v}_previous_day1" for v in variaveis)
            js = _get(params)
            if not js.get("error"):
                break
            motivo = js.get("reason", "")
            ruim = [v for v in variaveis if v in motivo]
            if not ruim:
                raise RuntimeError(f"Open-Meteo: {motivo}")
            print(f"  aviso: variável recusada pela API, removida: {ruim}")
            variaveis = [v for v in variaveis if v not in ruim]
        partes.append(_para_df(js, "_previous_day1"))
        time.sleep(1)
    out = pd.concat([p for p in partes if len(p)]).sort_index()
    out.index.name = "time"
    return out


def baixar_operacional(past_days=40, forecast_days=3, variaveis=VARIAVEIS):
    """Para a previsão do dia:
       obs  -> passado e hoje, valores previstos 24 h antes (mesma fonte do mapeamento)
       prev -> rodada mais recente (usada para amanhã)."""
    params = {"latitude": LAT, "longitude": LON, "timezone": "UTC", "models": MODELO,
              "past_days": past_days, "forecast_days": forecast_days,
              "hourly": ",".join(variaveis + [f"{v}_previous_day1" for v in variaveis])}
    js = _get(params)
    if js.get("error"):
        raise RuntimeError(f"Open-Meteo: {js.get('reason')}")
    return _para_df(js, "_previous_day1"), _para_df(js, "")


# ---------------------------------------------------------------------------
# 2. JMA horário -> variáveis diárias no formato INMET
# ---------------------------------------------------------------------------
def _local(h):
    h = h.copy()
    h.index = h.index - pd.Timedelta(hours=3)
    return h


def jma_para_inmet_diario(h_utc):
    """Agrega o JMA horário nas mesmas variáveis diárias da base INMET (antes do mapeamento)."""
    h = _local(h_utc)
    g = h.resample("D")
    n = h["temperature_2m"].resample("D").count()
    vel = h["wind_speed_10m"] / 3.6                     # km/h -> m/s
    d = pd.DataFrame({
        "precip_mm":        h["precipitation"].resample("D").sum(min_count=20),
        "precip_max_1h_mm": g["precipitation"].max(),
        "horas_chuva":      (h["precipitation"] >= 0.2).astype(float)
                            .where(h["precipitation"].notna()).resample("D").sum(min_count=20),
        "temp_media_c":     g["temperature_2m"].mean(),
        "temp_max_c":       g["temperature_2m"].max(),
        "temp_min_c":       g["temperature_2m"].min(),
        "orvalho_medio_c":  g["dew_point_2m"].mean(),
        "umid_media_pct":   g["relative_humidity_2m"].mean(),
        "umid_min_pct":     g["relative_humidity_2m"].min(),
        "umid_max_pct":     g["relative_humidity_2m"].max(),
        "pressao_media_mb": g["pressure_msl"].mean(),
        "pressao_amp_mb":   g["pressure_msl"].max() - g["pressure_msl"].min(),
        "vento_vel_ms":     vel.resample("D").mean(),
        "vento_rajada_ms":  vel.resample("D").max(),     # sem rajada no JMA: usa o vento máximo
    })
    if "shortwave_radiation" in h:
        d["radiacao_mjm2"] = h["shortwave_radiation"].resample("D").sum(min_count=20) * 3600 / 1e6
    else:
        d["radiacao_mjm2"] = np.nan
    if "wind_direction_10m" in h:
        rad = np.deg2rad(h["wind_direction_10m"])
        d["vento_u_ms"] = (-vel * np.sin(rad)).resample("D").mean()
        d["vento_v_ms"] = (-vel * np.cos(rad)).resample("D").mean()
    else:
        d["vento_u_ms"] = d["vento_v_ms"] = np.nan
    d.loc[n < 20, [c for c in d.columns if c not in ("precip_mm", "horas_chuva")]] = np.nan
    return d[VARS_INMET]


# ---------------------------------------------------------------------------
# 3. Mapeamento de quantis JMA -> INMET
# ---------------------------------------------------------------------------
QUANTIS = np.linspace(0, 1, 101)


def ajustar_mapeamento(jma_d, inmet_d, inicio, fim):
    """Para cada variável, guarda os quantis do JMA e do INMET nos mesmos dias (período de treino)."""
    mapa = {}
    for v in VARS_INMET:
        par = pd.concat([jma_d[v], inmet_d[v]], axis=1, keys=["j", "i"]).loc[inicio:fim].dropna()
        if len(par) < 200:
            continue
        mapa[v] = {"q_jma": np.quantile(par["j"], QUANTIS).round(4).tolist(),
                   "q_inmet": np.quantile(par["i"], QUANTIS).round(4).tolist(),
                   "n": int(len(par))}
    return mapa


def _interp(x, xp, fp):
    xp, fp = np.asarray(xp, float), np.asarray(fp, float)
    u, inv = np.unique(xp, return_inverse=True)          # quantis repetidos (ex.: muitos zeros de chuva)
    fu = np.bincount(inv, weights=fp) / np.bincount(inv)
    if len(u) == 1:
        return np.where(np.isnan(x), np.nan, fu[0])
    y = np.interp(x, u, fu)
    # fora da faixa: extrapola pela diferença (mantém a cauda sem achatar)
    y = np.where(x > u[-1], fu[-1] + (x - u[-1]), y)
    y = np.where(x < u[0], fu[0] + (x - u[0]), y)
    return np.where(np.isnan(x), np.nan, y)


def aplicar_mapeamento(jma_d, mapa):
    out = jma_d.copy()
    for v, m in mapa.items():
        if v in out:
            out[v] = _interp(out[v].values.astype(float), m["q_jma"], m["q_inmet"])
    for v in ("precip_mm", "precip_max_1h_mm", "horas_chuva", "radiacao_mjm2", "pressao_amp_mb",
              "vento_vel_ms", "vento_rajada_ms"):
        out[v] = out[v].clip(lower=0)
    for v in ("umid_media_pct", "umid_min_pct", "umid_max_pct"):
        out[v] = out[v].clip(0, 100)
    out["horas_chuva"] = out["horas_chuva"].clip(upper=24)
    return out


# ---------------------------------------------------------------------------
# 4. Features — idênticas ao tratamento da base INMET (seção 4 do script original)
# ---------------------------------------------------------------------------
def derivar_features(dia):
    dia = dia.copy()
    dia["amplitude_termica_c"] = dia["temp_max_c"] - dia["temp_min_c"]
    dia["deficit_orvalho_c"] = dia["temp_media_c"] - dia["orvalho_medio_c"]
    doy = dia.index.dayofyear
    dia["mes"] = dia.index.month
    dia["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    dia["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    dia["estacao_chuvosa"] = dia["mes"].isin([10, 11, 12, 1, 2, 3]).astype(int)
    for c in ["precip_mm", "temp_media_c", "temp_max_c", "temp_min_c", "umid_media_pct",
              "umid_min_pct", "pressao_media_mb", "radiacao_mjm2", "vento_vel_ms",
              "deficit_orvalho_c"]:
        for k in (1, 2, 3):
            dia[f"{c}_lag{k}"] = dia[c].shift(k)
    dia["pressao_tend_24h_mb"] = dia["pressao_media_mb"] - dia["pressao_media_mb"].shift(1)
    p = dia["precip_mm"].shift(1)
    for w in (3, 7, 15, 30):
        dia[f"precip_acum_{w}d"] = p.rolling(w, min_periods=int(w * 0.8)).sum()
    for c in ("temp_media_c", "umid_media_pct"):
        dia[f"{c}_mm7"] = dia[c].shift(1).rolling(7, min_periods=5).mean()
    seco = (dia["precip_mm"] < 1).astype(float).where(dia["precip_mm"].notna())
    streak, cont = [], 0
    for x in seco.shift(1):
        if np.isnan(x):
            streak.append(np.nan); cont = 0
        elif x == 1:
            cont += 1; streak.append(cont)
        else:
            cont = 0; streak.append(0)
    dia["dias_sem_chuva"] = streak
    return dia


def features_nwp(h_utc):
    """Features da previsão numérica por dia local (mesmas do notebook de treino, nwp_jma_gsm_*)."""
    h = _local(h_utc)
    p = h["precipitation"]
    g = lambda v: h[v].resample("D")
    pre = f"nwp_{MODELO}_"
    return pd.DataFrame({
        pre + "precip_mm":     p.resample("D").sum(min_count=20),
        pre + "precip_max_1h": p.resample("D").max(),
        pre + "horas_chuva":   (p >= 0.2).astype(float).where(p.notna()).resample("D").sum(min_count=20),
        pre + "nuvens_pct":    g("cloud_cover").mean(),
        pre + "umid_pct":      g("relative_humidity_2m").mean(),
        pre + "orvalho_c":     g("dew_point_2m").mean(),
        pre + "pressao_mb":    g("pressure_msl").mean(),
        pre + "temp_max_c":    g("temperature_2m").max(),
        pre + "vento_kmh":     g("wind_speed_10m").mean(),
    })


def montar_tabela(h_obs, h_amanha, mapa):
    """Tabela diária completa: linha D = 'tempo' de D (JMA mapeado para INMET) + previsão para D+1."""
    dia = derivar_features(aplicar_mapeamento(jma_para_inmet_diario(h_obs), mapa))
    nwp = features_nwp(h_amanha).shift(-1)                 # linha D recebe a previsão de D+1
    return dia.join(nwp, how="left")


def prever(pacote, tabela, dia_base):
    """Probabilidade calibrada de chuva (>= 1 mm) em dia_base + 1."""
    X = tabela.loc[[dia_base], pacote["features"]]
    bruto = pacote["modelo"].predict_proba(X)[:, 1]
    prob = float(np.clip(pacote["calibrador"].predict(bruto)[0], 0, 1))
    return prob, X.iloc[0]
