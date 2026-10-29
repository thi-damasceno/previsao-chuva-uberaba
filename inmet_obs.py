"""
inmet_obs.py — observações da estação INMET A568 (Uberaba) para o dashboard.

- Lê o CSV exportado pela página TEMPO (tempo.inmet.gov.br/TabelaEstacoes/A568)
  e, se aparecer, o JSON da API usada pela página.
- Mantém a base horária no repositório: dados/inmet_A568_horario.csv (UTC).
- Aplica EXATAMENTE o mesmo tratamento usado na base de treino
  (limites físicos, interpolação de lacunas curtas, dia local UTC-3, cobertura mínima).
"""
import io
from pathlib import Path

import numpy as np
import pandas as pd

COLS = ["precip_mm", "pressao_mb", "pressao_max_mb", "pressao_min_mb", "radiacao_kjm2",
        "temp_c", "orvalho_c", "temp_max_c", "temp_min_c", "orvalho_max_c", "orvalho_min_c",
        "umid_max_pct", "umid_min_pct", "umid_pct", "vento_dir_graus", "vento_rajada_ms", "vento_vel_ms"]

# Cabeçalhos do CSV da página TEMPO -> nomes da base de treino
MAPA_CSV = {
    "Temp. Ins. (C)": "temp_c", "Temp. Max. (C)": "temp_max_c", "Temp. Min. (C)": "temp_min_c",
    "Umi. Ins. (%)": "umid_pct", "Umi. Max. (%)": "umid_max_pct", "Umi. Min. (%)": "umid_min_pct",
    "Pto Orvalho Ins. (C)": "orvalho_c", "Pto Orvalho Max. (C)": "orvalho_max_c",
    "Pto Orvalho Min. (C)": "orvalho_min_c",
    "Pressao Ins. (hPa)": "pressao_mb", "Pressao Max. (hPa)": "pressao_max_mb",
    "Pressao Min. (hPa)": "pressao_min_mb",
    "Vel. Vento (m/s)": "vento_vel_ms", "Dir. Vento (m/s)": "vento_dir_graus",   # rótulo do INMET; é direção em graus
    "Raj. Vento (m/s)": "vento_rajada_ms", "Radiacao (KJ/m²)": "radiacao_kjm2", "Chuva (mm)": "precip_mm",
}

# Chaves do JSON da API do INMET (formato conhecido da apitempo) -> nomes da base
MAPA_JSON = {
    "CHUVA": "precip_mm", "PRE_INS": "pressao_mb", "PRE_MAX": "pressao_max_mb", "PRE_MIN": "pressao_min_mb",
    "RAD_GLO": "radiacao_kjm2", "TEM_INS": "temp_c", "PTO_INS": "orvalho_c", "TEM_MAX": "temp_max_c",
    "TEM_MIN": "temp_min_c", "PTO_MAX": "orvalho_max_c", "PTO_MIN": "orvalho_min_c", "UMD_MAX": "umid_max_pct",
    "UMD_MIN": "umid_min_pct", "UMD_INS": "umid_pct", "VEN_DIR": "vento_dir_graus", "VEN_RAJ": "vento_rajada_ms",
    "VEN_VEL": "vento_vel_ms",
}


def _num(s):
    s = s.astype(str).str.strip().str.replace(",", ".", regex=False)
    return pd.to_numeric(s.replace({"": np.nan, "None": np.nan, "null": np.nan, "nan": np.nan}), errors="coerce")


def ler_csv_tempo(fonte):
    """CSV da página TEMPO (caminho, bytes ou texto) -> DataFrame horário UTC com os nomes da base."""
    if isinstance(fonte, (str, Path)) and Path(str(fonte)).exists():
        texto = Path(fonte).read_bytes().decode("utf-8-sig", errors="replace")
    elif isinstance(fonte, bytes):
        texto = fonte.decode("utf-8-sig", errors="replace")
    else:
        texto = str(fonte).lstrip("﻿")
    df = pd.read_csv(io.StringIO(texto), sep=";", dtype=str, keep_default_na=False)
    df.columns = [c.strip().strip('"') for c in df.columns]
    data = pd.to_datetime(df["Data"].str.strip(), format="%d/%m/%Y", errors="coerce")
    hora = df["Hora (UTC)"].str.extract(r"(\d{1,2})")[0].astype(float)
    idx = data + pd.to_timedelta(hora, unit="h")
    out = pd.DataFrame({dst: _num(df[src]) for src, dst in MAPA_CSV.items() if src in df.columns})
    out.index = idx
    out = out[out.index.notna()]
    return _padronizar(out)


def ler_json_api(registros):
    """Lista de dicts da API do INMET -> DataFrame horário UTC com os nomes da base."""
    df = pd.DataFrame(registros)
    if df.empty or "DT_MEDICAO" not in df:
        return _padronizar(pd.DataFrame(columns=COLS))
    hora = df["HR_MEDICAO"].astype(str).str.extract(r"(\d{1,2})")[0].astype(float)
    idx = pd.to_datetime(df["DT_MEDICAO"], errors="coerce") + pd.to_timedelta(hora, unit="h")
    out = pd.DataFrame({dst: _num(df[src]) for src, dst in MAPA_JSON.items() if src in df.columns})
    out.index = idx
    return _padronizar(out[out.index.notna()])


def _padronizar(df):
    df = df.reindex(columns=COLS)
    df.index = pd.DatetimeIndex(df.index, name="datahora_utc")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.replace(-9999, np.nan)


# ---------------------------------------------------------------------------
# Base horária no repositório
# ---------------------------------------------------------------------------
def carregar_base(caminho):
    caminho = Path(caminho)
    if not caminho.exists():
        return _padronizar(pd.DataFrame(columns=COLS))
    df = pd.read_csv(caminho, parse_dates=["datahora_utc"], index_col="datahora_utc")
    return _padronizar(df)


def mesclar(base, novos):
    """Valores novos (não vazios) prevalecem; horas que a coleta nova não trouxe são mantidas."""
    if novos is None or novos.empty:
        return base
    return _padronizar(novos.combine_first(base))


def salvar_base(df, caminho):
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    df = df.dropna(how="all")
    df.to_csv(caminho, float_format="%.2f")


# ---------------------------------------------------------------------------
# Tratamento + agregação diária — mesmo critério da base de treino
# ---------------------------------------------------------------------------
LIMITES = {
    "precip_mm": (0, 150),
    "pressao_mb": (880, 960), "pressao_max_mb": (880, 960), "pressao_min_mb": (880, 960),
    "radiacao_kjm2": (0, 5000),
    "temp_c": (-5, 45), "temp_max_c": (-5, 45), "temp_min_c": (-5, 45),
    "orvalho_c": (-20, 35), "orvalho_max_c": (-20, 35), "orvalho_min_c": (-20, 35),
    "umid_pct": (1, 100), "umid_max_pct": (1, 100), "umid_min_pct": (1, 100),
    "vento_dir_graus": (0, 360), "vento_rajada_ms": (0, 50), "vento_vel_ms": (0, 40),
}
CONTINUAS = ["pressao_mb", "pressao_max_mb", "pressao_min_mb", "temp_c", "orvalho_c",
             "temp_max_c", "temp_min_c", "orvalho_max_c", "orvalho_min_c",
             "umid_pct", "umid_max_pct", "umid_min_pct", "vento_vel_ms", "vento_rajada_ms"]


def diario(h_utc):
    """Horário UTC -> diário local (UTC-3) com as mesmas variáveis e regras do treino."""
    if h_utc.empty:
        return pd.DataFrame()
    h = h_utc.copy()
    h = h.reindex(pd.date_range(h.index.min().floor("D"), h.index.max().ceil("D") - pd.Timedelta(hours=1), freq="h"))

    h["radiacao_kjm2"] = h["radiacao_kjm2"].clip(lower=0)
    hora_local = (h.index - pd.Timedelta(hours=3)).hour
    noite = ((hora_local >= 20) | (hora_local <= 5)) & h["temp_c"].notna()
    h.loc[noite, "radiacao_kjm2"] = 0.0

    for c, (lo, hi) in LIMITES.items():
        h.loc[(h[c] < lo) | (h[c] > hi), c] = np.nan
    h.loc[h["orvalho_c"] > h["temp_c"] + 0.5, "orvalho_c"] = np.nan
    h[CONTINUAS] = h[CONTINUAS].interpolate(method="time", limit=3, limit_area="inside")

    h.index = h.index - pd.Timedelta(hours=3)          # dia local
    g = h.resample("D")
    d = pd.DataFrame({
        "precip_mm":        g["precip_mm"].sum(min_count=1),
        "precip_max_1h_mm": g["precip_mm"].max(),
        "horas_chuva":      g["precip_mm"].apply(lambda s: (s >= 0.2).sum()),
        "temp_media_c":     g["temp_c"].mean(),
        "temp_max_c":       g["temp_max_c"].max(),
        "temp_min_c":       g["temp_min_c"].min(),
        "orvalho_medio_c":  g["orvalho_c"].mean(),
        "umid_media_pct":   g["umid_pct"].mean(),
        "umid_min_pct":     g["umid_min_pct"].min(),
        "umid_max_pct":     g["umid_max_pct"].max(),
        "pressao_media_mb": g["pressao_mb"].mean(),
        "pressao_amp_mb":   g["pressao_max_mb"].max() - g["pressao_min_mb"].min(),
        "radiacao_mjm2":    g["radiacao_kjm2"].sum(min_count=1) / 1000,
        "vento_vel_ms":     g["vento_vel_ms"].mean(),
        "vento_rajada_ms":  g["vento_rajada_ms"].max(),
        "n_horas_precip":   g["precip_mm"].count(),
        "n_horas_temp":     g["temp_c"].count(),
        "n_horas_rad_dia":  h.between_time("06:00", "18:00")["radiacao_kjm2"].resample("D").count(),
    })
    rad = np.deg2rad(h["vento_dir_graus"])
    d["vento_u_ms"] = (-h["vento_vel_ms"] * np.sin(rad)).resample("D").mean()
    d["vento_v_ms"] = (-h["vento_vel_ms"] * np.cos(rad)).resample("D").mean()

    meteo = [c for c in d.columns if c not in ("precip_mm", "precip_max_1h_mm", "horas_chuva",
                                               "n_horas_precip", "n_horas_temp", "n_horas_rad_dia")]
    d.loc[d["n_horas_temp"] < 20, meteo] = np.nan
    d.loc[d["n_horas_precip"] < 20, ["precip_mm", "precip_max_1h_mm", "horas_chuva"]] = np.nan
    d.loc[d["n_horas_rad_dia"] < 11, "radiacao_mjm2"] = np.nan
    return d
