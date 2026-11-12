# -*- coding: utf-8 -*-
"""
AQUA TRACK — prototipo (piloto) · Hackathon Sunass "Lideresas por el Agua"
Desafío 1 · Gestión comercial (Sedapal) · Clientes especiales con telemetría.

    pip install streamlit pandas numpy        (pydeck ya viene con streamlit)
    streamlit run app.py

AVISO DE DATOS: todo es SIMULADO con las cifras agregadas del expediente
(1,703 medidores · 78 antenas · 1,611 suministros · 130 medidores planos ·
69 con consumo nocturno · 2,600 retrocesos en 407 suministros · 249 sin
transmitir >72 h). NO contiene datos reales de Sedapal (uso exclusivo de la
hackathon). Tipo de local, contactos, coordenadas y resultados de
verificación son simulados y declarados, como recomendó Sunass (11-nov).
Sin IA generativa: cada alerta sale de una regla de Python explicable.
"""
from __future__ import annotations

import html
from datetime import date, timedelta

import altair as alt            # viene instalado con streamlit
import numpy as np
import pandas as pd
import streamlit as st

# =============================================================================
# 0. PALETA OFICIAL (muestreada de los PDF: bases del concurso y logo
#    "Hackathon Sunass · Lideresas por el Agua")
# =============================================================================
VIOLETA = "#6D61EB"        # fondo periwinkle de las bases
VIOLETA_DOC = "#713BCE"    # título del formato del documento técnico
LILA = "#D09EFF"           # relleno del logo
VERDE_CLARO = "#CBEE79"    # verde-amarillo del logo
AMARILLO = "#CEDB76"       # amarillo-verdoso del logo (segundo tono)
AZUL_OSCURO = "#232E38"    # contorno del logo
CREMA = "#F5E9F0"          # tarjeta de las bases
ROSA = "#F96CCC"           # punto de ventana del PDF
VERDE_OK = "#97D514"       # punto verde del PDF
# Funcionales (NO son del logo): solo para estados de alerta, siempre con texto/ícono.
ALERTA_AMBAR = "#F2B84B"
ALERTA_ROJO = "#D6336C"

st.set_page_config(page_title="AQUA TRACK · Lideresas por el Agua",
                   page_icon="💧", layout="wide")

# =============================================================================
# 1. PARÁMETROS DEL MOTOR (umbrales explicables; se recalibran con cada cierre)
# =============================================================================
FECHA_INICIO = date(2026, 8, 1)
FECHA_CORTE = date(2026, 10, 24)           # "hoy" de la demo (mes en curso)
SLOTS_DIA = 8                              # lectura cada 3 h (≈7/día tras huecos)
HORA_NOCHE = (0, 6)                        # franja de madrugada 00–06 h

UMBRALES = {
    "noches_min": 30,            # mínimo de noches observadas para evaluar
    "frac_noches_con_consumo": 0.95,   # ≥95 % de las noches con consumo > 0
    "ratio_noche_vs_esperado": 2.0,    # share nocturno observado ≥ 2× el esperado del tipo de local
    "m3_noche_min": 1.0,         # m³/noche mínimos para no alertar ruido
    "dias_congelado": 30,        # índice sin cambio ≥ N días → posible medidor detenido
    "horas_sin_transmitir": 72,  # >72 h sin lectura → falla de comunicación
    "frac_gateway_caida": 0.60,  # ≥60 % de los medidores de una antena callados a la vez
    "min_medidores_gateway": 5,
    "caida_sostenida": 0.50,     # últimos 10 días ≤ 50 % del nivel previo → cierre/cambio de uso
    "nota_baja": 50,             # nota de confiabilidad < 50 → revisión Sedapal
    "nota_media": 80,
}

# Share nocturno (00–06 h / consumo diario) ESPERADO por tipo de local.
# SUPUESTO declarado (no está en el expediente): un hospital sí consume de noche,
# un colegio no. Se calibra con el ESCE en las mentorías.
SHARE_NOCHE_ESPERADO = {"Hospital": 0.22, "Industria": 0.12, "Comercio": 0.04,
                        "Entidad pública": 0.03, "Colegio": 0.01}
PRIORIDAD_LOCAL = {"Hospital": 0, "Colegio": 1, "Entidad pública": 2,
                   "Comercio": 3, "Industria": 4}

# Tarifa Sedapal feb-2026, Grupo 1 (DS04a; S/ por m³, sin IGV). PARÁMETRO ACTUALIZABLE:
# Sunass avisó que las tarifas pueden variar.
TARIFA = {  # categoría: (agua, saneamiento)
    "Estatal": (5.80, 2.68),
    "Comercial": (8.82, 4.21),
    "Industrial": (9.46, 4.51),
}
CARGO_FIJO = 6.30
IGV = 0.18
# SUPUESTO simulado: hospitales/colegios/entidades → Estatal (si fueran privados serían Comercial).
CATEGORIA_TARIFA = {"Hospital": "Estatal", "Colegio": "Estatal", "Entidad pública": "Estatal",
                    "Comercio": "Comercial", "Industria": "Industrial"}

# Perfil horario: 8 tramos de 3 h. Slot j = consumo del tramo que TERMINA a las 3·(j+1) h
# (0 = 00→03, 1 = 03→06, 2 = 06→09 … 7 = 21→24). SUPUESTO simulado por tipo de local.
_PERFIL = {
    "Hospital":        np.array([.11, .10, .12, .15, .16, .15, .12, .09]),
    "Colegio":         np.array([.00, .00, .20, .40, .35, .05, .00, .00]),
    "Comercio":        np.array([.01, .01, .10, .22, .28, .24, .10, .04]),
    "Entidad pública": np.array([.01, .01, .12, .30, .30, .20, .05, .01]),
    "Industria":       np.array([.06, .05, .12, .18, .20, .18, .12, .09]),
}
# Fin de semana: colegios/entidades casi sin uso.
_FACTOR_FINDE = {"Hospital": 1.0, "Colegio": 0.02, "Comercio": 0.8,
                 "Entidad pública": 0.1, "Industria": 0.6}

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
ESTADOS = ["Detectada", "Asignada", "Avisada", "Verificada", "Cerrada"]
RESPONSABLE = {"FUGA_NOCTURNA": "Gestora del local",
               "SIN_TRANSMISION": "Sedapal (ESCE)",
               "CAIDA_SOSTENIDA": "Gestora del local",
               "MEDIDOR_DETENIDO": "Sedapal (ESCE)",
               "MEDIDOR_RETROCESOS": "Sedapal (ESCE)",
               "ANTENA_CAIDA": "Comunicaciones"}
NOMBRE_ANOMALIA = {"FUGA_NOCTURNA": "Posible fuga (consumo nocturno continuo)",
                   "SIN_TRANSMISION": "Medidor sin transmitir (>72 h)",
                   "CAIDA_SOSTENIDA": "Caída sostenida (¿cierre o cambio de uso?)",
                   "MEDIDOR_DETENIDO": "Posible medidor detenido (índice plano)",
                   "MEDIDOR_RETROCESOS": "Lecturas que retroceden",
                   "ANTENA_CAIDA": "Antena sin transmitir"}
COLOR_ANOMALIA = {"FUGA_NOCTURNA": LILA, "SIN_TRANSMISION": "#8FAEE0", "CAIDA_SOSTENIDA": AMARILLO,
                  "MEDIDOR_DETENIDO": ALERTA_AMBAR, "MEDIDOR_RETROCESOS": ROSA,
                  "ANTENA_CAIDA": ALERTA_ROJO}


# =============================================================================
# 2. DATOS SIMULADOS (mock) con la estructura del expediente
# =============================================================================
@st.cache_data(show_spinner="Simulando el expediente…")
def generar_datos(seed: int = 2026):
    """Devuelve (maestro, lecturas).

    maestro  : 1,703 medidores · 78 antenas · coordenadas · tipo de local (simulado)
    lecturas : índice acumulado entero (m³) cada 3 h, ago→24-oct-2026, de 1,611
               suministros; 92 medidores sin lecturas. Refleja:
               130 planos · 69 nocturnos · 2,600 retrocesos en 407 · 249 >72 h.
    """
    rng = np.random.default_rng(seed)
    N_MED, N_SUM, N_GW = 1703, 1611, 78

    # ---- Maestro -------------------------------------------------------------
    # Antenas repartidas en Lima Metropolitana (caja aprox.; coordenadas SIMULADAS).
    gw_lat = rng.uniform(-12.20, -11.92, N_GW)
    gw_lon = rng.uniform(-77.12, -76.93, N_GW)
    # Tamaños desiguales de antena (suman 1,703).
    pesos = rng.dirichlet(np.full(N_GW, 2.0))
    cuota = np.maximum(5, np.round(pesos * N_MED)).astype(int)
    cuota[np.argmax(cuota)] += N_MED - cuota.sum()
    gateway = np.repeat(np.arange(N_GW), cuota)[:N_MED]
    rng.shuffle(gateway)
    lat = gw_lat[gateway] + rng.normal(0, 0.006, N_MED)
    lon = gw_lon[gateway] + rng.normal(0, 0.006, N_MED)

    tipos = rng.choice(list(PRIORIDAD_LOCAL), N_MED,
                       p=[.05, .12, .15, .45, .23])
    sum_ids = np.array([f"Suministro_{i:04d}" for i in range(1, N_SUM + 1)])
    sum_de_medidor = np.full(N_MED, None, dtype=object)
    con_lectura = np.sort(rng.choice(N_MED, N_SUM, replace=False))  # 92 sin lecturas
    sum_de_medidor[con_lectura] = sum_ids
    maestro = pd.DataFrame({
        "medidor": [f"MED-{i:05d}" for i in range(1, N_MED + 1)],
        "suministro": sum_de_medidor,
        "gateway": [f"GW-{g + 1:02d}" for g in gateway],
        "lat": lat, "lon": lon,
        "tipo_local": tipos,
        "fecha_registro": pd.to_datetime("2025-06-01")
                          + pd.to_timedelta(rng.integers(0, 520, N_MED), unit="D"),
    })
    # Contactos y alcantarillado: simulados (en la vida real los tiene Sedapal).
    maestro["contacto_simulado"] = [f"gestora{i:04d}@ejemplo.pe" for i in range(N_MED)]

    m = maestro.dropna(subset=["suministro"]).reset_index(drop=True)
    n = len(m)  # 1,611

    # ---- Selección de casos sintéticos (disjuntos) ---------------------------
    idx = rng.permutation(n)
    i_plano = idx[:130]                       # 130 medidores con índice plano
    i_noct = idx[130:199]                     # 69 consumo casi todas las noches
    i_caida = idx[199:214]                    # 15 caídas sostenidas (cierre/cambio de uso)
    resto = idx[214:]
    # 249 sin transmitir >72 h: 5 antenas completas (AQUA CLUSTER) + sueltos.
    gw_caidas = rng.choice(N_GW, 5, replace=False)
    m_gw = m["gateway"].str[3:].astype(int).to_numpy() - 1
    i_gw = np.where(np.isin(m_gw, gw_caidas) & ~np.isin(np.arange(n), np.r_[i_plano, i_noct, i_caida]))[0]
    resto_libre = np.setdiff1d(resto, i_gw)
    i_sueltos = rng.choice(resto_libre, max(0, 249 - len(i_gw)), replace=False)
    i_silencio = np.r_[i_gw, i_sueltos]
    # 407 con retrocesos (se pueden solapar con cualquiera salvo planos).
    i_retro = rng.choice(np.setdiff1d(np.arange(n), i_plano), 407, replace=False)

    # ---- Demanda base ---------------------------------------------------------
    dias = pd.date_range(FECHA_INICIO, FECHA_CORTE, freq="D")
    nd = len(dias)
    t_local = m["tipo_local"].to_numpy()
    # m³/día: lognormal con mediana 4.5 y p90 ≈ 17.5 (cifras del expediente).
    base = rng.lognormal(np.log(4.5), 1.06, n)
    base *= np.where(t_local == "Hospital", 6.0, 1.0)
    base *= np.where(t_local == "Industria", 1.6, 1.0)
    dow = np.array([d.weekday() for d in dias])           # 0=lun … 6=dom
    finde = dow >= 5

    perfil = np.stack([_PERFIL[t] for t in t_local])       # (n, 8)
    perfil = perfil / perfil.sum(1, keepdims=True)
    f_finde = np.array([_FACTOR_FINDE[t] for t in t_local])
    factor_dia = np.where(finde[None, :], f_finde[:, None], 1.0)  # (n, nd)
    lam = base[:, None, None] * factor_dia[:, :, None] * perfil[:, None, :]  # m³ esperados por tramo
    cons = rng.poisson(lam).astype(np.int32)               # (n, nd, 8) enteros como el índice real

    # 69 nocturnos: fuga continua en los tramos 00→03 y 03→06 (slots 0 y 1).
    fuga = rng.uniform(0.7, 2.2, len(i_noct))
    fuga[0] = 4.6                                          # caso extremo: nunca bajó de 9 m³/noche
    for k, i in enumerate(i_noct):
        extra = rng.poisson(fuga[k], (nd, 2))
        extra = np.maximum(extra, 1)                       # nunca cero → "casi todas las noches"
        cons[i, :, 0] += extra[:, 0]
        cons[i, :, 1] += extra[:, 1]

    # 15 caídas sostenidas: últimos 10 días a ~15 % del nivel.
    cons[i_caida, -10:, :] = rng.poisson(lam[i_caida, -10:, :] * 0.15)

    # 130 planos: índice sin cambio.
    cons[i_plano, :, :] = 0

    # Índice acumulado entero (partiendo de un valor inicial arbitrario).
    flat = cons.reshape(n, nd * SLOTS_DIA)
    indice = np.cumsum(flat, axis=1) + rng.integers(50, 4000, n)[:, None]

    # Marcas de tiempo (lectura a las 00,03,…,21 h; el valor es el índice al cierre del tramo).
    horas = ((np.arange(SLOTS_DIA) + 1) * 3).astype("timedelta64[h]")   # 03,06,…,24 h (fin de cada tramo)
    ts = pd.DatetimeIndex((dias.to_numpy()[:, None] + horas[None, :]).ravel())
    T = len(ts)

    # Huecos de transmisión: ~12 % al azar → ≈7 lecturas/día.
    presente = rng.random((n, T)) > 0.12
    # Instalación tardía: parte de los medidores empieza después del 1-ago.
    inst = np.zeros(n, dtype=int)
    tarde = rng.choice(np.setdiff1d(np.arange(n), i_plano), 150, replace=False)  # los planos sí tienen ≥30 d de serie
    inst[tarde] = rng.integers(5 * SLOTS_DIA, 60 * SLOTS_DIA, len(tarde))
    for i in tarde:
        presente[i, :inst[i]] = False
    # Medidores planos dados de alta en 2026 (85) = instalados tarde sin consumo.
    # Silencio >72 h: dejan de transmitir en los últimos 4–20 días.
    corte_silencio = np.full(n, T)
    corte_silencio[i_silencio] = T - rng.integers(4 * SLOTS_DIA, 20 * SLOTS_DIA, len(i_silencio))
    for i in i_gw:                                         # antenas caídas: TODAS a la vez
        corte_silencio[i] = T - 6 * SLOTS_DIA
    for i in i_silencio:
        presente[i, corte_silencio[i]:] = False

    filas = np.where(presente)
    ind_obs = indice[filas[0], filas[1]].astype(np.int64)
    # Retrocesos: 2,600 en 407 suministros. Se inyectan sobre la serie OBSERVADA: la lectura
    # reporta un índice menor al anterior (error de lectura / flujo inverso).
    inicio = np.searchsorted(filas[0], np.arange(n))
    fin = np.searchsorted(filas[0], np.arange(n), side="right")
    n_retro = rng.multinomial(2600 - 407, np.full(407, 1 / 407)) + 1
    orig = ind_obs.copy()
    for i, k in zip(i_retro, n_retro):
        cand = np.arange(inicio[i] + 2, fin[i], 3)           # separadas ≥3 lecturas
        pos = rng.choice(cand, min(k, len(cand)), replace=False)
        ind_obs[pos] = orig[pos - 1] - rng.integers(1, 4, len(pos))
    lecturas = pd.DataFrame({
        "suministro": m["suministro"].to_numpy()[filas[0]],
        "ts": ts[filas[1]],
        "indice": ind_obs,
    })
    return maestro, lecturas


# =============================================================================
# 3. MOTOR DE REGLAS EXPLICABLES  (sin IA generativa)
#    huella horaria · mínimo de madrugada · firma del medidor · antena
# =============================================================================
def limpiar_lecturas(lec: pd.DataFrame) -> pd.DataFrame:
    """Paso 1: convierte el índice acumulado en consumo por tramo, trata huecos y retrocesos.

    - Un retroceso (índice menor al anterior) NO es consumo negativo: se marca y se descarta.
    - Una lectura con retroceso tiene el índice dudoso: ni ella ni el tramo siguiente se usan.
    - Un hueco largo (>6 h) NO es "consumo cero": el tramo queda NaN (falta de dato ≠ cero).
    """
    lec = lec.sort_values(["suministro", "ts"]).copy()
    g = lec.groupby("suministro", sort=False)
    lec["d_indice"] = g["indice"].diff()
    lec["d_horas"] = g["ts"].diff().dt.total_seconds() / 3600
    lec["retroceso"] = lec["d_indice"] < 0
    lec["post_retro"] = lec.groupby("suministro", sort=False)["retroceso"].shift(1, fill_value=False)
    ok = (~lec["retroceso"]) & (~lec["post_retro"]) & (lec["d_horas"] <= 6)
    lec["m3"] = np.where(ok, lec["d_indice"], np.nan)
    lec["fecha"] = (lec["ts"] - pd.Timedelta(minutes=1)).dt.floor("D")   # día al que pertenece el tramo
    lec["hora_fin"] = lec["ts"].dt.hour                                    # 0 = tramo 21→24
    return lec


def consumo_diario(serie: pd.DataFrame, corte: pd.Timestamp) -> pd.Series:
    """m³ por día de un suministro: el índice (sin lecturas dudosas) se interpola linealmente
    entre lecturas, así los huecos de transmisión no se leen como consumo cero."""
    v = serie[~serie["retroceso"]].sort_values("ts")
    if len(v) < 2:
        return pd.Series(dtype=float)
    t0 = pd.Timestamp("2026-01-01")                              # (evita depender de la unidad datetime64)
    x = ((v["ts"] - t0) / pd.Timedelta(hours=1)).to_numpy(dtype=float)   # horas desde t0
    y = v["indice"].to_numpy(dtype=float)
    dias = pd.date_range(v["ts"].min().floor("D"), corte.floor("D"), freq="D")   # fronteras de día (00:00)
    xb = ((dias - t0) / pd.Timedelta(hours=1)).to_numpy(dtype=float)
    yb = np.interp(xb, x, y)
    yb[xb > x[-1]] = np.nan                                     # después de la última lectura: sin dato
    yb[xb < x[0]] = np.nan
    return pd.Series(np.diff(yb), index=dias[:-1])


def regla_firma_medidor(lec: pd.DataFrame, maestro: pd.DataFrame, corte: pd.Timestamp) -> pd.DataFrame:
    """AQUA TRUST — nota de confiabilidad 0–100 por suministro (firma del medidor).

    Penaliza: índice plano, retrocesos, horas sin transmitir, transmisión incompleta.
    Un medidor con nota baja va a revisión de Sedapal; no se interpreta "sin lectura" como cero.
    """
    u = UMBRALES
    g = lec.groupby("suministro")
    r = pd.DataFrame({
        "primera": g["ts"].min(), "ultima": g["ts"].max(),
        "n_lect": g.size(), "retrocesos": g["retroceso"].sum(),
        "idx_min": g["indice"].min(), "idx_max": g["indice"].max(),
    })
    # días con índice sin cambio al final de la serie
    cambios = lec[lec["d_indice"].fillna(0) != 0].groupby("suministro")["ts"].max()
    r["ultimo_cambio"] = cambios.reindex(r.index)
    r["ultimo_cambio"] = r["ultimo_cambio"].fillna(r["primera"])
    r["dias_plano"] = (corte - r["ultimo_cambio"]).dt.total_seconds() / 86400
    r["plano_total"] = r["idx_max"] == r["idx_min"]
    r["horas_sin_tx"] = (corte - r["ultima"]).dt.total_seconds() / 3600
    r["dias_serie"] = ((corte - r["primera"]).dt.total_seconds() / 86400).clip(lower=1)
    dias_con = lec.groupby("suministro")["fecha"].nunique()
    r["pct_dias_tx"] = (dias_con / r["dias_serie"]).clip(upper=1)

    nota = np.full(len(r), 100.0)
    nota -= np.where(r["dias_plano"] >= u["dias_congelado"], 60, 0)       # firma: índice plano
    nota -= np.where(r["plano_total"], 10, 0)                              # plano toda la serie
    nota -= np.minimum(55, r["retrocesos"] * 6)                            # firma: retrocesos
    nota -= np.where(r["horas_sin_tx"] > u["horas_sin_transmitir"], 25, 0) # no transmite
    nota -= np.where(r["pct_dias_tx"] < 0.75, 15, 0)                       # transmisión incompleta
    r["nota_confiabilidad"] = np.clip(nota, 0, 100).round(0).astype(int)
    r["nivel_confiabilidad"] = pd.cut(r["nota_confiabilidad"],
                                      [-1, u["nota_baja"] - 1, u["nota_media"] - 1, 100],
                                      labels=["Baja", "Media", "Alta"])
    def motivo(x):
        m = []
        if x.dias_plano >= u["dias_congelado"]:
            m.append(f"índice plano {int(x.dias_plano)} d")
        if x.retrocesos > 0:
            m.append(f"{int(x.retrocesos)} retrocesos")
        if x.horas_sin_tx > u["horas_sin_transmitir"]:
            m.append(f"sin transmitir {int(x.horas_sin_tx)} h")
        if x.pct_dias_tx < 0.75:
            m.append(f"transmite {x.pct_dias_tx:.0%} de los días")
        return " · ".join(m) if m else "—"
    r["motivo_nota"] = r.apply(motivo, axis=1)
    return r.reset_index()


def regla_huella_y_madrugada(lec: pd.DataFrame, tipo_por_sum: pd.Series) -> pd.DataFrame:
    """Huella horaria + mínimo de madrugada (00–06 h), comparados con el tipo de local.

    Consumo de la noche = índice a las 06:00 − índice a las 00:00 (solo si ambas lecturas
    existen y no son dudosas). Marca FUGA si el suministro consume casi todas las noches
    (≥95 % de ≥30 noches) Y su share nocturno supera ≥2× lo esperado para su tipo de local.
    Un hospital se compara con su propio nivel esperado, no con cero.
    """
    u = UMBRALES
    v = lec[~lec["retroceso"]].copy()
    v["dia"] = v["ts"].dt.floor("D")
    a0 = v[v["hora_fin"] == 0].set_index(["suministro", "dia"])["indice"].rename("i0")
    a6 = v[v["hora_fin"] == 6].set_index(["suministro", "dia"])["indice"].rename("i6")
    nd = pd.concat([a0, a6], axis=1).dropna()
    nd["m3_noche"] = nd["i6"] - nd["i0"]
    # consumo del día = índice 00:00 del día siguiente − índice 00:00 del día
    sig = a0.reset_index()
    sig["dia"] = sig["dia"] - pd.Timedelta(days=1)
    dia = a0.reset_index().merge(sig.rename(columns={"i0": "i0_sig"}), on=["suministro", "dia"])
    dia["m3_dia"] = dia["i0_sig"] - dia["i0"]
    nd = nd.reset_index().merge(dia[["suministro", "dia", "m3_dia"]], on=["suministro", "dia"])
    nd = nd[(nd["m3_noche"] >= 0) & (nd["m3_dia"] >= 0)]
    g = nd.groupby("suministro")
    r = pd.DataFrame({
        "noches": g.size(),
        "noches_con_consumo": g["m3_noche"].apply(lambda s: int((s > 0).sum())),
        "min_noche": g["m3_noche"].min(),
        "mediana_noche": g["m3_noche"].median(),
        "m3_dia_medio": g["m3_dia"].mean(),
    })
    r["frac_noches"] = r["noches_con_consumo"] / r["noches"]
    r["share_noche"] = r["mediana_noche"] / r["m3_dia_medio"].replace(0, np.nan)
    r["tipo_local"] = tipo_por_sum.reindex(r.index).to_numpy()
    r["share_esperado"] = r["tipo_local"].map(SHARE_NOCHE_ESPERADO)
    r["fuga_nocturna"] = ((r["noches"] >= u["noches_min"])
                          & (r["frac_noches"] >= u["frac_noches_con_consumo"])
                          & (r["mediana_noche"] >= u["m3_noche_min"])
                          & (r["share_noche"] >= u["ratio_noche_vs_esperado"] * r["share_esperado"]))
    # m³/día en exceso sobre lo esperado para su tipo → volumen en riesgo por día
    r["m3_exceso_dia"] = (r["mediana_noche"] - r["share_esperado"] * r["m3_dia_medio"]).clip(lower=0)

    # Fuga vs. cambio de actividad: una caída sostenida del consumo diario parece cierre o
    # cambio de uso (no fuga); una subida sostenida de madrugada sí parece fuga.
    def caida(s):
        x = s.sort_values("dia")["m3_dia"].to_numpy()
        if len(x) < 30:
            return False
        prev, rec = x[:-8].mean(), x[-8:].mean()
        return bool(prev > 2 and rec <= u["caida_sostenida"] * prev)
    r["caida_sostenida"] = nd.groupby("suministro")[["dia", "m3_dia"]].apply(caida).reindex(r.index).fillna(False)
    return r.reset_index()


def regla_antena(maestro: pd.DataFrame, nota: pd.DataFrame) -> pd.DataFrame:
    """AQUA CLUSTER — si ≥60 % de los medidores de una antena dejan de transmitir a la vez,
    el problema es la comunicación (antena/gateway), no cada medidor."""
    u = UMBRALES
    d = maestro.dropna(subset=["suministro"]).merge(
        nota[["suministro", "horas_sin_tx"]], on="suministro", how="left")
    d["silencio"] = d["horas_sin_tx"] > u["horas_sin_transmitir"]
    g = d.groupby("gateway")
    a = pd.DataFrame({"medidores": g.size(), "en_silencio": g["silencio"].sum(),
                      "lat": g["lat"].mean(), "lon": g["lon"].mean()})
    a["pct_silencio"] = a["en_silencio"] / a["medidores"]
    a["antena_caida"] = (a["pct_silencio"] >= u["frac_gateway_caida"]) & (a["medidores"] >= u["min_medidores_gateway"])
    return a.reset_index()


def tarifa_m3(tipo_local: str) -> float:
    ag, sa = TARIFA[CATEGORIA_TARIFA[tipo_local]]
    return ag + sa


def recibo(m3: float, tipo_local: str) -> float:
    """Recibo estimado con la estructura tarifaria feb-2026 (DS04a), con IGV.
    Comercial: 0–1000 m³ y >1000 m³ por rangos; Estatal/Industrial: tarifa plana."""
    cat = CATEGORIA_TARIFA[tipo_local]
    if cat == "Comercial":
        a1, s1, a2, s2 = 8.82, 4.21, 9.46, 4.51
        v1, v2 = min(m3, 1000), max(m3 - 1000, 0)
        base = v1 * (a1 + s1) + v2 * (a2 + s2)
    else:
        base = m3 * tarifa_m3(tipo_local)
    return (base + CARGO_FIJO) * (1 + IGV)


@st.cache_data(show_spinner="Corriendo el motor de reglas…")
def correr_motor(seed: int = 2026):
    """Orquesta el motor y arma la tabla de casos (anomalía con responsable, estado y reloj)."""
    maestro, lecturas = generar_datos(seed)
    corte = pd.Timestamp(FECHA_CORTE) + pd.Timedelta(hours=24)
    lec = limpiar_lecturas(lecturas)
    nota = regla_firma_medidor(lec, maestro, corte)
    tipo_por_sum = maestro.dropna(subset=["suministro"]).set_index("suministro")["tipo_local"]
    huella = regla_huella_y_madrugada(lec, tipo_por_sum)
    antenas = regla_antena(maestro, nota)

    base = (maestro.dropna(subset=["suministro"])
            .merge(nota, on="suministro", how="left")
            .merge(huella[["suministro", "fuga_nocturna", "caida_sostenida", "m3_exceso_dia", "m3_dia_medio",
                           "mediana_noche", "share_noche", "frac_noches", "noches"]],
                   on="suministro", how="left")
            .merge(antenas[["gateway", "antena_caida", "pct_silencio"]], on="gateway", how="left"))
    for c in ("fuga_nocturna", "caida_sostenida", "antena_caida"):
        base[c] = base[c].fillna(False).astype(bool)
    u = UMBRALES
    base["plano"] = base["dias_plano"] >= u["dias_congelado"]
    base["sin_tx"] = base["horas_sin_tx"] > u["horas_sin_transmitir"]
    base["retro_alto"] = base["retrocesos"] >= 5
    base["nota_baja"] = base["nota_confiabilidad"] < u["nota_baja"]

    # ---- Clasificación con precedencia explicable ----------------------------
    # 1) antena caída → comunicaciones (no culpar al medidor ni al cliente)
    # 2) medidor plano / nota baja → Sedapal (no alertar al cliente con un medidor dudoso)
    # 3) medidor sin transmitir (>72 h, antena sana) → Sedapal
    # 4) fuga nocturna o caída sostenida → gestora (solo si el medidor es confiable y transmite)
    def clasificar(x):
        if x.antena_caida and x.sin_tx:
            return "ANTENA_CAIDA"
        if x.plano:
            return "MEDIDOR_DETENIDO"
        if x.nota_baja and x.retro_alto:
            return "MEDIDOR_RETROCESOS"
        if x.sin_tx:                    # sin lecturas recientes no se alerta al cliente con datos viejos
            return "SIN_TRANSMISION"
        if x.fuga_nocturna and not x.nota_baja:
            return "FUGA_NOCTURNA"
        if x.caida_sostenida and not x.nota_baja:
            return "CAIDA_SOSTENIDA"
        return None
    base["anomalia"] = base.apply(clasificar, axis=1)

    # ---- Casos: responsable, estado, reloj, riesgo en m³ y S/ ----------------
    rng = np.random.default_rng(7)
    c = base[base["anomalia"].notna()].copy().reset_index(drop=True)
    c["caso"] = [f"AT-{i + 1:04d}" for i in range(len(c))]
    c["responsable"] = c["anomalia"].map(RESPONSABLE)
    # Reloj: la fecha de detección es la primera vez que se cumplió la regla.
    # En el mock se simula entre 1 y 40 días antes del corte (declarado).
    c["dias_abiertos"] = rng.integers(1, 41, len(c))
    c["detectada"] = pd.Timestamp(FECHA_CORTE) - pd.to_timedelta(c["dias_abiertos"], unit="D")
    # Estado (simulado, sesgado a abiertos); los cerrados alimentan la precisión de alertas.
    c["estado"] = rng.choice(ESTADOS, len(c), p=[.24, .26, .26, .14, .10])
    c.loc[c["estado"] == "Cerrada", "dias_abiertos"] = rng.integers(2, 20, (c["estado"] == "Cerrada").sum())
    # Volumen en riesgo por día:
    #   fuga   → exceso nocturno sobre lo esperado
    #   plano  → consumo medio de su tipo de local no facturado (estimación, hipótesis)
    #   antena/retrocesos → m³ promedio diario del tipo de local (estimación)
    # m³/día de referencia por tipo de local = mediana de los suministros SIN anomalía (dato del mock).
    sin_anom = base[base["anomalia"].isna() & (base["m3_dia_medio"] > 0)]
    med_tipo = sin_anom.groupby("tipo_local")["m3_dia_medio"].median()
    m3_ref = c["tipo_local"].map(med_tipo).fillna(med_tipo.median())
    m3_dia = np.where(c["anomalia"] == "FUGA_NOCTURNA", c["m3_exceso_dia"].fillna(1).clip(lower=0.5),
                      np.where(c["anomalia"] == "CAIDA_SOSTENIDA", 0.0, m3_ref))
    c["m3_riesgo_dia"] = m3_dia
    dias_riesgo = np.where(c["anomalia"].isin(["MEDIDOR_DETENIDO"]), c["dias_plano"].clip(upper=90),
                           c["dias_abiertos"])
    c["m3_riesgo"] = (c["m3_riesgo_dia"] * dias_riesgo).round(1)
    c["soles_riesgo"] = [round(x * tarifa_m3(t) * (1 + IGV), 2) for x, t in zip(c["m3_riesgo"], c["tipo_local"])]
    # Resultado de cierre simulado (calibra la precisión): fuga / medidor / falsa alarma.
    cerr = c["estado"] == "Cerrada"
    c["resultado_cierre"] = None
    c.loc[cerr, "resultado_cierre"] = rng.choice(["Fuga confirmada", "Medidor reparado", "Falsa alarma"],
                                                  cerr.sum(), p=[.45, .35, .20])
    c["prioridad"] = c["tipo_local"].map(PRIORIDAD_LOCAL)
    return maestro, lecturas, base, c, antenas


def serie_suministro(lecturas: pd.DataFrame, suministro: str) -> pd.DataFrame:
    """Serie limpia de un suministro (para las gráficas de la vista cliente)."""
    s = limpiar_lecturas(lecturas[lecturas["suministro"] == suministro])
    return s


# =============================================================================
# 4. TEMA VISUAL (CSS con la paleta del logo)
# =============================================================================
def inyectar_css():
    st.markdown(f"""
<style>
:root {{
  --violeta:{VIOLETA}; --violeta-doc:{VIOLETA_DOC}; --lila:{LILA}; --verde:{VERDE_CLARO};
  --amarillo:{AMARILLO}; --azul:{AZUL_OSCURO}; --crema:{CREMA}; --rosa:{ROSA};
}}
/* Fondo y tipografía */
.stApp {{ background: var(--crema); color: var(--azul); }}
html, body, [class*="css"] {{ font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif; }}
.block-container {{ padding: 2.2rem 3rem 3rem; max-width: 100% !important; }}

/* Encabezados: estilo logo (relleno lila, contorno azul oscuro) */
h1, h2, h3 {{ color: var(--azul); letter-spacing: .01em; }}
h1 {{ font-weight: 900; }}
h2 {{ border-bottom: 3px dashed var(--violeta); padding-bottom: .25rem; }}
h3 {{ color: var(--violeta-doc); }}
.at-hero {{ background: var(--violeta); border: 3px solid var(--azul); border-radius: 16px;
  box-shadow: 6px 6px 0 var(--azul); padding: 1.1rem 1.4rem; margin-bottom: 1.2rem; }}
.at-hero h1 {{ margin: 0; color: var(--lila); -webkit-text-stroke: 1.2px var(--azul);
  text-shadow: 3px 3px 0 var(--azul); font-size: 2.3rem; }}
.at-hero p {{ margin: .25rem 0 0; color: var(--crema); font-size: 1.02rem; }}
.at-pill {{ display:inline-block; background: var(--verde); color: var(--azul); font-weight: 800;
  font-size: .72rem; letter-spacing: .08em; padding: .12rem .6rem; border: 2px solid var(--azul);
  border-radius: 6px; margin-right:.4rem; }}

/* Barra lateral */
[data-testid="stSidebar"] {{ background: var(--violeta); border-right: 3px solid var(--azul); }}
[data-testid="stSidebar"] .stMarkdown, [data-testid="stSidebar"] p,
[data-testid="stSidebar"] label, [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3, [data-testid="stSidebar"] span, [data-testid="stSidebar"] small {{ color: var(--crema); }}
[data-testid="stSidebar"] .at-brand {{ font-size: 1.9rem; font-weight: 900; color: var(--lila);
  -webkit-text-stroke: 1.2px var(--azul); text-shadow: 3px 3px 0 var(--azul); line-height: 1; }}
[data-testid="stSidebar"] .at-sub {{ background: var(--lila); color: var(--azul) !important; font-size: .68rem;
  font-weight: 800; letter-spacing: .1em; padding: .1rem .5rem; border: 2px solid var(--azul);
  border-radius: 5px; display:inline-block; margin: .35rem 0 1rem; }}
[data-testid="stSidebar"] [role="radiogroup"] label {{ background: rgba(245,233,240,.14);
  border: 2px solid var(--azul); border-radius: 10px; padding: .45rem .7rem; margin-bottom: .4rem; }}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {{ background: var(--verde);
  box-shadow: 3px 3px 0 var(--azul); }}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p {{ color: var(--azul) !important; font-weight: 800; }}
[data-testid="stSidebar"] [data-baseweb="select"] * {{ color: var(--azul) !important; }}
[data-testid="stSidebar"] input {{ color: var(--azul) !important; }}
/* Campos (selectbox, multiselect, texto): fondo claro y texto azul oscuro, aunque el navegador esté en modo oscuro */
[data-baseweb="select"] > div, [data-baseweb="input"], [data-baseweb="base-input"],
[data-baseweb="input"] > div, [data-baseweb="select"] input {{ background: #fff !important; color: var(--azul) !important; }}
[data-baseweb="select"] > div, [data-baseweb="input"] {{ border: 2.5px solid var(--azul) !important; border-radius: 10px !important; }}
[data-baseweb="select"] *, [data-baseweb="input"] input {{ color: var(--azul) !important; -webkit-text-fill-color: var(--azul) !important; }}
[data-baseweb="select"] svg {{ fill: var(--azul) !important; }}
[data-baseweb="tag"] {{ background: var(--lila) !important; }}
[data-baseweb="tag"] * {{ color: var(--azul) !important; }}
/* Lista desplegable (se dibuja fuera de la página, en un popover) */
[data-baseweb="popover"] [data-baseweb="menu"], [data-baseweb="popover"] ul {{ background: #fff !important; }}
[data-baseweb="popover"] li, [data-baseweb="popover"] li * {{ color: var(--azul) !important; background: #fff; }}
[data-baseweb="popover"] li:hover, [data-baseweb="popover"] li[aria-selected="true"] {{ background: var(--lila) !important; }}
/* Casillas y etiquetas del área principal */
.stApp [data-testid="stWidgetLabel"] p, .stApp [data-testid="stCheckbox"] p {{ color: var(--azul); }}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {{ color: var(--crema) !important; }}
/* Expanders de la barra lateral: fondo blanco → texto azul oscuro (legible) */
[data-testid="stSidebar"] [data-testid="stExpander"] summary,
[data-testid="stSidebar"] [data-testid="stExpander"] summary *,
[data-testid="stSidebar"] [data-testid="stExpander"] p,
[data-testid="stSidebar"] [data-testid="stExpander"] span,
[data-testid="stSidebar"] [data-testid="stExpander"] label,
[data-testid="stSidebar"] [data-testid="stExpander"] td,
[data-testid="stSidebar"] [data-testid="stExpander"] th,
[data-testid="stSidebar"] [data-testid="stExpander"] small,
[data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stCaptionContainer"] * {{ color: var(--azul) !important; }}
[data-testid="stSidebar"] [data-testid="stExpander"] summary svg {{ fill: var(--azul) !important; }}
[data-testid="stSidebar"] [data-testid="stExpander"] summary {{ font-weight: 700; }}

/* Métricas */
[data-testid="stMetric"] {{ background: #fff; border: 2.5px solid var(--azul); border-left: 10px solid var(--lila);
  border-radius: 12px; padding: .8rem 1rem; box-shadow: 4px 4px 0 var(--violeta); }}
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] * {{ white-space: normal !important; overflow: visible !important; text-overflow: clip !important; }}
[data-testid="stMetricLabel"] p {{ color: var(--violeta-doc); font-weight: 700; font-size: .85rem; }}
[data-testid="stMetricValue"], [data-testid="stMetricValue"] * {{ color: var(--azul); font-weight: 900; white-space: normal !important;
  overflow: visible !important; text-overflow: clip !important; }}
[data-testid="stMetricValue"] {{ font-size: clamp(1.3rem, 1.9vw, 2.1rem); line-height: 1.15; }}

/* Botones */
.stButton > button, .stDownloadButton > button {{ background: var(--verde); color: var(--azul);
  font-weight: 800; border: 2.5px solid var(--azul); border-radius: 10px; box-shadow: 3px 3px 0 var(--azul);
  transition: transform .08s; }}
.stButton > button:hover {{ background: var(--lila); color: var(--azul); border-color: var(--azul); transform: translate(-1px,-1px); }}
.stButton > button:focus-visible {{ outline: 4px solid var(--violeta-doc); outline-offset: 2px; }}

/* Tarjetas de alerta (qué está raro / qué revisar) */
.at-card {{ background:#fff; border:2.5px solid var(--azul); border-radius:14px; padding:1rem 1.2rem;
  box-shadow:5px 5px 0 var(--violeta); margin-bottom:1rem; }}
.at-card .tag {{ display:inline-block; font-weight:800; font-size:.72rem; letter-spacing:.07em;
  padding:.1rem .55rem; border:2px solid var(--azul); border-radius:6px; margin-bottom:.4rem; }}
.at-card h4 {{ margin:.1rem 0 .3rem; color:var(--azul); font-size:1.12rem; }}
.at-card .que {{ background: var(--crema); border-left: 6px solid var(--violeta); padding:.5rem .8rem;
  border-radius:6px; margin:.4rem 0; }}
.at-card .accion {{ background: var(--verde); border:2px solid var(--azul); padding:.5rem .8rem;
  border-radius:8px; margin-top:.4rem; font-weight:600; }}
.at-ok {{ background:#fff; border:2.5px solid var(--azul); border-left:10px solid {VERDE_OK};
  border-radius:12px; padding:.8rem 1.1rem; box-shadow:4px 4px 0 var(--violeta); }}

/* Línea de tiempo del caso */
.at-track {{ display:flex; align-items:flex-start; margin:.6rem 0 .2rem; }}
.at-step {{ flex:1; text-align:center; position:relative; }}
.at-step .dot {{ width:34px; height:34px; border-radius:50%; margin:0 auto; border:3px solid var(--azul);
  background:#fff; display:flex; align-items:center; justify-content:center; font-weight:900; color:var(--azul); position:relative; z-index:2; }}
.at-step.done .dot {{ background: var(--violeta); color:#fff; }}
.at-step.now .dot {{ background: var(--verde); box-shadow:0 0 0 5px var(--lila); }}
.at-step .lbl {{ font-size:.82rem; margin-top:.35rem; font-weight:700; color:var(--azul); }}
.at-step.todo .lbl {{ color:#6b6b78; font-weight:500; }}
.at-step::before {{ content:""; position:absolute; top:15px; left:-50%; width:100%; height:5px;
  background: repeating-linear-gradient(90deg, var(--azul) 0 6px, transparent 6px 11px); z-index:1; }}
.at-step:first-child::before {{ display:none; }}
.at-step.done::before, .at-step.now::before {{ background: var(--violeta); height:5px; }}
.at-reloj {{ display:inline-block; background:var(--azul); color:var(--verde); font-weight:800;
  padding:.25rem .8rem; border-radius:8px; margin-top:.3rem; }}

/* Independiente del modo claro/oscuro del navegador: superficies claras y texto oscuro explícitos */
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stMain"], [data-testid="stMain"] p, [data-testid="stMain"] li,
[data-testid="stMain"] label, [data-testid="stMain"] h1, [data-testid="stMain"] h2 {{ color: var(--azul); }}
[data-testid="stMain"] [data-testid="stCaptionContainer"], [data-testid="stMain"] [data-testid="stCaptionContainer"] * {{ color: #4a4f5c !important; }}
.at-hero p, .at-hero .at-pill {{ color: inherit; }}
.at-hero h1 {{ color: var(--lila) !important; }}
.at-hero p {{ color: var(--crema) !important; }}
[data-testid="stProgress"] [role="progressbar"] > div, [data-testid="stProgress"] div[data-baseweb="progress-bar"] > div > div {{ background: var(--violeta) !important; }}
[data-testid="stAlert"] * {{ color: var(--azul) !important; }}
/* Pestañas */
.stTabs [data-baseweb="tab"], .stTabs [data-baseweb="tab"] * {{ color: var(--azul) !important; font-weight: 700; }}
.stTabs [data-baseweb="tab-highlight"] {{ background: var(--violeta-doc) !important; height: 4px; }}
.stTabs [data-baseweb="tab-border"] {{ background: var(--azul) !important; opacity: .25; }}
/* Tablas y pestañas */
[data-testid="stDataFrame"] {{ border: 2.5px solid var(--azul); border-radius: 10px; overflow: hidden; }}
.stTabs [data-baseweb="tab"] {{ font-weight: 700; color: var(--azul); }}
.stTabs [aria-selected="true"] {{ background: var(--lila); border-radius: 8px 8px 0 0; }}
[data-testid="stExpander"] {{ border: 2.5px solid var(--azul); border-radius: 10px; background:#fff; }}
.at-leyenda span {{ display:inline-block; margin-right:1rem; font-size:.85rem; font-weight:600; }}
.at-leyenda i {{ display:inline-block; width:13px; height:13px; border-radius:50%; border:2px solid var(--azul); margin-right:.35rem; vertical-align:-2px; }}
</style>
""", unsafe_allow_html=True)


def _hero(titulo: str, sub: str, pill: str):
    st.markdown(f'<div class="at-hero"><span class="at-pill">{html.escape(pill)}</span>'
                f'<h1>{html.escape(titulo)}</h1><p>{html.escape(sub)}</p></div>',
                unsafe_allow_html=True)


def _df(df, **kw):
    """st.dataframe compatible con versiones nuevas (width='stretch') y antiguas."""
    try:
        st.dataframe(df, width="stretch", **kw)
    except Exception:
        st.dataframe(df, use_container_width=True, **kw)


def _grafico(chart, alto: int = 320):
    """Gráfico con colores explícitos: se ve igual en modo claro y oscuro (no hereda el tema)."""
    chart = (chart.properties(height=alto)
             .configure(background="#FFFFFF")
             .configure_view(stroke=None)
             .configure_axis(labelColor=AZUL_OSCURO, titleColor=AZUL_OSCURO, gridColor="#E6DAE1",
                             domainColor=AZUL_OSCURO, tickColor=AZUL_OSCURO)
             .configure_legend(labelColor=AZUL_OSCURO, titleColor=AZUL_OSCURO, orient="top"))
    try:
        st.altair_chart(chart, theme=None, width="stretch")
    except Exception:
        st.altair_chart(chart, theme=None, use_container_width=True)


def _fmt(x, dec=0):
    return f"{x:,.{dec}f}"


def _fmt_soles(x):
    return f"S/ {x:,.2f}"


# =============================================================================
# 5. VISTA A — CLIENTE (gestora del local)
# =============================================================================
ACCION = {
    "FUGA_NOCTURNA": ("Su local consume agua toda la noche, cuando debería estar casi en cero.",
                      "Revise tanque, cisterna e inodoros. Prueba rápida: cierre las llaves del local "
                      "por la noche y vea si el medidor sigue avanzando."),
    "CAIDA_SOSTENIDA": ("Su consumo bajó a menos de la mitad y lleva varios días así.",
                        "Confirme si el local cerró o cambió de uso. Si no es así, puede ser un problema "
                        "del medidor: avísenos con un toque."),
    "SIN_TRANSMISION": ("El medidor de su local dejó de enviar lecturas hace más de 3 días.",
                        "Sedapal verificará el medidor o su comunicación. No hace falta que usted haga nada; "
                        "su consumo no se interpreta como cero."),
    "MEDIDOR_DETENIDO": ("El medidor de su local no registra consumo hace semanas.",
                         "Este caso lo atiende Sedapal; usted no necesita hacer nada. "
                         "Si el local sí usa agua, confírmelo con un toque."),
    "MEDIDOR_RETROCESOS": ("El medidor envía lecturas que bajan en vez de subir.",
                           "Sedapal revisará el medidor. No hace falta que usted haga nada."),
    "ANTENA_CAIDA": ("La antena de su zona no está enviando datos; no es falla de su local.",
                     "Comunicaciones de Sedapal ya tiene el caso. Su consumo no se interpreta como cero."),
}


def linea_tiempo(estado: str, dias: int, caso: str, responsable: str):
    i = ESTADOS.index(estado)
    pasos = []
    for k, e in enumerate(ESTADOS):
        cls = "done" if k < i else ("now" if k == i else "todo")
        marca = "✓" if k < i else str(k + 1)
        pasos.append(f'<div class="at-step {cls}"><div class="dot">{marca}</div><div class="lbl">{e}</div></div>')
    st.markdown(f'<div class="at-track">{"".join(pasos)}</div>', unsafe_allow_html=True)
    st.progress((i + 1) / len(ESTADOS))
    reloj = "Caso cerrado" if estado == "Cerrada" else f"⏱ {dias} días abiertos"
    st.markdown(f'<span class="at-reloj">{reloj}</span> &nbsp; <b>Caso {html.escape(caso)}</b> · '
                f'Responsable ahora: <b>{html.escape(responsable)}</b>', unsafe_allow_html=True)


def vista_cliente(maestro, lecturas, base, casos):
    _hero("Cliente · Gestora del local",
          "Su consumo del mes, qué está raro y qué revisar. Sin contraseñas: entra por el link del aviso.",
          "VISTA A")

    # --- Acceso: link con código de caso, o código de suministro / DNI (simulado) ---
    qp = st.query_params
    codigos = casos["caso"].tolist()
    caso_url = qp.get("caso")
    with st.sidebar:
        st.markdown("**Entrar a mi local**")
        modo = st.radio("Cómo entra", ["Link del aviso", "Código de suministro"],
                        label_visibility="collapsed")
        if modo == "Link del aviso":
            pref = codigos.index(caso_url) if caso_url in codigos else 0
            elegido = st.selectbox("Caso (el link trae este código)", codigos, index=pref)
            fila = casos[casos["caso"] == elegido].iloc[0]
            sum_sel = fila["suministro"]
        else:
            sum_sel = st.selectbox("Código de suministro (o DNI del titular)",
                                   sorted(base["suministro"].unique())[:300], index=0)
            st.caption("DNI/código simulados. Acceso real: código de suministro o DNI del titular.")

    info = base[base["suministro"] == sum_sel].iloc[0]
    tipo = info["tipo_local"]
    caso = casos[casos["suministro"] == sum_sel]
    caso = caso.iloc[0] if len(caso) else None

    serie = serie_suministro(lecturas, sum_sel)
    corte = pd.Timestamp(FECHA_CORTE) + pd.Timedelta(days=1)
    d = consumo_diario(serie, corte)                       # m³/día (índice interpolado en los huecos)
    mes_ini = pd.Timestamp(FECHA_CORTE).replace(day=1)
    mes_ant_ini = (mes_ini - pd.Timedelta(days=1)).replace(day=1)
    m3_mes = float(d[d.index >= mes_ini].sum())
    m3_ant = float(d[(d.index >= mes_ant_ini) & (d.index < mes_ini)].sum())
    dias_pasados = (pd.Timestamp(FECHA_CORTE) - mes_ini).days + 1
    dias_mes = pd.Timestamp(FECHA_CORTE).days_in_month
    ult14 = d[d.index > pd.Timestamp(FECHA_CORTE) - pd.Timedelta(days=14)].dropna()
    prom14 = float(ult14.mean()) if len(ult14) else 0.0
    m3_proy = m3_mes + prom14 * (dias_mes - dias_pasados)    # proyección: promedio de los últimos 14 días
    soles_mes = recibo(m3_mes, tipo) if m3_mes > 0 else 0.0
    soles_proy = recibo(m3_proy, tipo)

    st.markdown(f"### {html.escape(str(sum_sel))} · {html.escape(tipo)} (tipo simulado)")
    st.caption(f"Antena {info['gateway']} · Medidor {info['medidor']} · "
               f"Corte de datos: {FECHA_CORTE:%d-%m-%Y} · Tarifa Sedapal feb-2026 (con IGV)")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Consumo del mes (a hoy)", f"{_fmt(m3_mes, 1)} m³",
              help=f"Mes anterior completo: {_fmt(m3_ant, 1)} m³.")
    c2.metric("Consumo del mes en soles", _fmt_soles(soles_mes),
              help="Con la estructura tarifaria Sedapal feb-2026 (DS04a) e IGV 18 %. Parámetro actualizable.")
    c3.metric("Proyección a fin de mes", f"{_fmt(m3_proy, 1)} m³")
    c4.metric("Recibo estimado", _fmt_soles(soles_proy),
              help="Proyección con el promedio de los últimos 14 días. Estimado, no es el recibo oficial.")

    # --- Panel de acción ---
    st.markdown("## ¿Qué está raro?")
    if caso is None:
        st.markdown('<div class="at-ok"><b>✅ Todo en orden.</b> Su consumo está dentro de lo normal para un '
                    f'{html.escape(tipo.lower())}. Le avisaremos solo si algo cambia.</div>', unsafe_allow_html=True)
    else:
        que, accion = ACCION[caso["anomalia"]]
        color = COLOR_ANOMALIA[caso["anomalia"]]
        icono = {"FUGA_NOCTURNA": "💧", "CAIDA_SOSTENIDA": "📉", "MEDIDOR_DETENIDO": "⚙️",
                 "MEDIDOR_RETROCESOS": "⚙️", "ANTENA_CAIDA": "📡", "SIN_TRANSMISION": "📡"}[caso["anomalia"]]
        riesgo_txt = (f"{_fmt_soles(caso['soles_riesgo'])} · {_fmt(caso['m3_riesgo'], 1)} m³ (estimado)"
                      if caso["m3_riesgo"] > 0 else "por confirmar")
        evidencia = ""
        if caso["anomalia"] == "FUGA_NOCTURNA":
            evidencia = (f"Consumió de madrugada (00–06 h) en {caso['frac_noches']:.0%} de {int(caso['noches'])} noches; "
                         f"mediana {caso['mediana_noche']:.1f} m³ por noche.")
        elif caso["anomalia"] == "MEDIDOR_DETENIDO":
            evidencia = f"Índice sin cambio hace {int(caso['dias_plano'])} días."
        elif caso["anomalia"] == "MEDIDOR_RETROCESOS":
            evidencia = f"{int(caso['retrocesos'])} lecturas retrocedieron."
        elif caso["anomalia"] == "SIN_TRANSMISION":
            evidencia = f"Última lectura hace {int(caso['horas_sin_tx'])} horas."
        elif caso["anomalia"] == "CAIDA_SOSTENIDA":
            evidencia = "Su consumo diario de los últimos 8 días es menos de la mitad del habitual."
        elif caso["anomalia"] == "ANTENA_CAIDA":
            evidencia = f"{caso['pct_silencio']:.0%} de los medidores de la antena {caso['gateway']} dejaron de transmitir a la vez."
        st.markdown(f"""
<div class="at-card">
  <span class="tag" style="background:{color}">{icono} {html.escape(NOMBRE_ANOMALIA[caso['anomalia']]).upper()}</span>
  <h4>{html.escape(que)}</h4>
  <div class="que"><b>Lo que vimos:</b> {html.escape(evidencia) or '—'}<br>
  <b>Dinero en riesgo:</b> {riesgo_txt}</div>
  <div class="accion">✅ <b>Qué revisar:</b> {html.escape(accion)}</div>
</div>""", unsafe_allow_html=True)

        # Respuesta opcional de un toque (el sistema funciona aunque no responda)
        st.caption("Opcional · un toque. Si no responde, el caso sigue su curso igual.")
        b1, b2, b3 = st.columns(3)
        if "respuestas" not in st.session_state:
            st.session_state["respuestas"] = {}
        if b1.button("🔧 Ya lo revisé", key=f"rev_{caso['caso']}"):
            st.session_state["respuestas"][caso["caso"]] = "Verificada"
        if b2.button("🏫 El local no usa agua", key=f"sin_{caso['caso']}"):
            st.session_state["respuestas"][caso["caso"]] = "Verificada"
        if b3.button("🙋 Necesito ayuda", key=f"ayu_{caso['caso']}"):
            st.session_state["respuestas"][caso["caso"]] = "Asignada"

    # --- Rastreo del caso ---
    st.markdown("## Rastreo del caso")
    if caso is None:
        st.info("No hay un caso abierto para este suministro.")
    else:
        estado = st.session_state.get("respuestas", {}).get(caso["caso"], caso["estado"])
        linea_tiempo(estado, int(caso["dias_abiertos"]), caso["caso"], caso["responsable"])

    # --- Gráficas: consumo diario y huella horaria (por qué es raro) ---
    st.markdown("## Su consumo")
    t1, t2 = st.tabs(["Consumo diario (m³)", "Huella por horario"])
    with t1:
        ult = d[d.index > pd.Timestamp(FECHA_CORTE) - pd.Timedelta(days=45)]
        dfc = ult.rename_axis("fecha").reset_index(name="m3")
        dfc["día"] = dfc["fecha"].map(lambda f: f"{f.day} {MESES[f.month - 1]}")      # etiquetas en español
        dfc["dia_sem"] = dfc["fecha"].map(lambda f: DIAS[f.weekday()])
        _grafico(alt.Chart(dfc).mark_bar(color=VIOLETA).encode(
            x=alt.X("día:N", title="Día", sort=None, axis=alt.Axis(labelAngle=-60, labelOverlap=True)),
            y=alt.Y("m3:Q", title="Consumo (m³ por día)"),
            tooltip=[alt.Tooltip("día:N", title="Día"), alt.Tooltip("dia_sem:N", title="Día de la semana"),
                     alt.Tooltip("m3:Q", title="Consumo (m³)", format=".1f")]))
    with t2:
        v = serie.dropna(subset=["m3"]).copy()
        v["franja"] = v["hora_fin"].map(lambda h: f"{(h - 3) % 24:02d}–{(h - 1) % 24 + 1:02d} h")
        corte_sem = pd.Timestamp(FECHA_CORTE) - pd.Timedelta(days=7)
        hist = v[v["ts"] <= corte_sem].groupby("franja")["m3"].mean()
        rec = v[v["ts"] > corte_sem].groupby("franja")["m3"].mean()
        h = pd.DataFrame({"Historial propio": hist, "Últimos 7 días": rec}).sort_index()
        hl = h.rename_axis("franja").reset_index().melt("franja", var_name="serie", value_name="m3")
        _grafico(alt.Chart(hl).mark_line(point=True, strokeWidth=3).encode(
            x=alt.X("franja:N", title="Franja horaria (cada 3 h)", sort=None, axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("m3:Q", title="Consumo promedio (m³ por tramo)"),
            color=alt.Color("serie:N", title=None, scale=alt.Scale(
                domain=["Historial propio", "Últimos 7 días"], range=[VIOLETA, ALERTA_ROJO])),
            tooltip=[alt.Tooltip("franja:N", title="Franja"), alt.Tooltip("serie:N", title="Serie"),
                     alt.Tooltip("m3:Q", title="Consumo (m³)", format=".2f")]))
        st.caption("Promedio de m³ por franja de 3 h. Si la línea roja queda sobre la violeta en la "
                   "madrugada (00–06 h), algo consume agua cuando el local debería estar quieto.")


# =============================================================================
# 6. VISTA B — SEDAPAL (ESCE)
# =============================================================================
def vista_sedapal(maestro, base, casos, antenas):
    _hero("Sedapal · ESCE",
          "Casos abiertos con dueño y reloj, soles en riesgo y medidores que no son confiables.",
          "VISTA B")

    # Aplicar respuestas del cliente / cierres hechos en la demo
    c = casos.copy()
    for k, v in st.session_state.get("respuestas", {}).items():
        c.loc[c["caso"] == k, "estado"] = v
    for k in st.session_state.get("cerrados", []):
        c.loc[c["caso"] == k, "estado"] = "Cerrada"
    abiertos = c[c["estado"] != "Cerrada"]

    # --- Filtros ---
    f1, f2, f3 = st.columns(3)
    sel_tipo = f1.multiselect("Tipo de local", list(PRIORIDAD_LOCAL), default=list(PRIORIDAD_LOCAL))
    sel_resp = f2.multiselect("Responsable", sorted(RESPONSABLE.values()),
                              default=sorted(RESPONSABLE.values()))
    sel_est = f3.multiselect("Estado", ESTADOS[:-1], default=ESTADOS[:-1])
    ab = abiertos[abiertos["tipo_local"].isin(sel_tipo) & abiertos["responsable"].isin(sel_resp)
                  & abiertos["estado"].isin(sel_est)]

    # --- KPIs críticos ---
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Casos sin cerrar", _fmt(len(ab)))
    k2.metric("Días abiertos (prom.)", f"{ab['dias_abiertos'].mean():.0f}" if len(ab) else "0",
              help=f"Máximo: {ab['dias_abiertos'].max():.0f} días." if len(ab) else None)
    k3.metric("Soles en riesgo", f"S/ {ab['soles_riesgo'].sum():,.0f}")
    k4.metric("m³ en riesgo", _fmt(ab["m3_riesgo"].sum(), 0))
    cerr = c[c["estado"] == "Cerrada"]
    if len(cerr):
        acierto = (cerr["resultado_cierre"] != "Falsa alarma").mean()
        k5.metric("Alertas que acertaron", f"{acierto:.0%}",
                  help=f"{len(cerr)} cierres (simulados). Hoy Sedapal no mide esto; cada cierre registra el resultado y recalibra los umbrales.")
    else:
        k5.metric("Alertas que acertaron", "—")
    st.caption("Reloj de AQUA TRACK: cuenta los días desde la detección hasta el cierre. "
               "Línea base del expediente: 5–7 meses sin revisión · Meta a validar: < 30 días.")

    # --- Mapa interactivo (pydeck, viene con Streamlit) ---
    st.markdown("## Mapa de casos abiertos")
    import pydeck as pdk
    mp = ab.copy()
    mp["color"] = mp["anomalia"].map(lambda a: [int(COLOR_ANOMALIA[a][i:i + 2], 16) for i in (1, 3, 5)] + [220])
    mp["radio"] = 100 + 10 * np.sqrt(mp["soles_riesgo"].clip(lower=1))
    mp["tipo_txt"] = mp["anomalia"].map(NOMBRE_ANOMALIA)
    mp["riesgo_txt"] = mp["soles_riesgo"].map(lambda x: f"S/ {x:,.0f}")
    capas = [pdk.Layer("ScatterplotLayer", data=mp, get_position="[lon, lat]", get_fill_color="color",
                       get_radius="radio", radius_min_pixels=5, radius_max_pixels=26, pickable=True, stroked=True, get_line_color=[35, 46, 56],
                       line_width_min_pixels=2, opacity=0.85)]
    ant = antenas.copy()
    ant["caida_txt"] = np.where(ant["antena_caida"], "caída", "operativa")
    ant["color"] = ant["antena_caida"].map(lambda x: [214, 51, 108, 255] if x else [109, 97, 235, 90])
    ant_caidas = ant[ant["antena_caida"]]
    if len(ant_caidas):
        capas.append(pdk.Layer("ScatterplotLayer", data=ant_caidas, get_position="[lon, lat]",
                               get_fill_color=[0, 0, 0, 0], get_line_color=[214, 51, 108, 255],
                               get_radius=2200, stroked=True, line_width_min_pixels=3, pickable=False))
    vista = pdk.ViewState(latitude=-12.06, longitude=-77.03, zoom=9.6, pitch=0)
    deck = pdk.Deck(
        layers=capas, initial_view_state=vista,
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        tooltip={"html": "<b>{caso}</b> · {tipo_local}<br/>{tipo_txt}<br/>Estado: {estado} · {dias_abiertos} d abiertos"
                         "<br/>En riesgo: {riesgo_txt}<br/>Responsable: {responsable}",
                 "style": {"backgroundColor": AZUL_OSCURO, "color": CREMA}})
    try:
        st.pydeck_chart(deck, height=560)
    except TypeError:                      # versiones antiguas de Streamlit sin parámetro height
        st.pydeck_chart(deck)
    leyenda = "".join(f'<span><i style="background:{COLOR_ANOMALIA[a]}"></i>{html.escape(NOMBRE_ANOMALIA[a])}</span>'
                      for a in NOMBRE_ANOMALIA)
    st.markdown(f'<div class="at-leyenda">{leyenda}<span><i style="background:transparent;border-color:{ALERTA_ROJO}"></i>'
                f'Antena caída (círculo)</span></div>', unsafe_allow_html=True)
    st.caption("Tamaño del punto = soles en riesgo. Coordenadas simuladas.")

    # --- Tabla de confiabilidad ---
    st.markdown("## Tabla de confiabilidad")
    st.caption("Prioridad: hospitales y colegios primero, luego nota de confiabilidad más baja. "
               "AQUA TRUST = nota del medidor · AQUA CLUSTER = falla de antena.")
    tb = base.copy()
    tb["AQUA CLUSTER"] = np.where(tb["antena_caida"] & tb["sin_tx"], "📡 Antena caída", "")
    tb["AQUA TRUST"] = np.where(tb["nota_baja"], "⚠️ Nota baja", np.where(tb["nivel_confiabilidad"] == "Media", "Media", "OK"))
    tb["prioridad"] = tb["tipo_local"].map(PRIORIDAD_LOCAL)
    cA, cB, cC = st.columns([2, 2, 3])
    solo_exc = cA.checkbox("Solo excepciones del motor", value=True)
    solo_prio = cB.checkbox("Solo hospitales y colegios", value=False)
    buscar = cC.text_input("Buscar suministro o antena", placeholder="Suministro_0012 o GW-07")
    if solo_exc:
        tb = tb[tb["antena_caida"] & tb["sin_tx"] | tb["nota_baja"]]
    if solo_prio:
        tb = tb[tb["tipo_local"].isin(["Hospital", "Colegio"])]
    if buscar:
        b = buscar.strip().lower()
        tb = tb[tb["suministro"].str.lower().str.contains(b, regex=False) | tb["gateway"].str.lower().str.contains(b, regex=False)]
    tb = tb.sort_values(["prioridad", "nota_confiabilidad"])
    cols = ["suministro", "tipo_local", "gateway", "nota_confiabilidad", "AQUA TRUST", "AQUA CLUSTER",
            "motivo_nota", "retrocesos", "dias_plano", "horas_sin_tx"]
    vis = tb[cols].rename(columns={"suministro": "Suministro", "tipo_local": "Tipo de local (sim.)",
                                   "gateway": "Antena", "nota_confiabilidad": "Nota (0–100)",
                                   "motivo_nota": "Por qué", "retrocesos": "Retrocesos",
                                   "dias_plano": "Días con índice plano", "horas_sin_tx": "Horas sin transmitir"})
    vis["Días con índice plano"] = vis["Días con índice plano"].round(0)
    vis["Horas sin transmitir"] = vis["Horas sin transmitir"].round(0)
    _df(vis.head(500), hide_index=True, height=420, column_config={
        "Nota (0–100)": st.column_config.ProgressColumn("Nota (0–100)", min_value=0, max_value=100, format="%d"),
    })
    st.caption(f"{len(tb):,} suministros en la tabla (se muestran hasta 500).")

    # --- Fallas de antena (AQUA CLUSTER) y casos ---
    t1, t2 = st.tabs(["📡 Antenas (AQUA CLUSTER)", "📋 Casos abiertos"])
    with t1:
        a = antenas.sort_values("pct_silencio", ascending=False).head(15).copy()
        a["pct_silencio"] = (a["pct_silencio"] * 100).round(0)
        a["Estado"] = np.where(a["antena_caida"], "📡 Caída: revisar comunicaciones", "Operativa")
        _df(a[["gateway", "medidores", "en_silencio", "pct_silencio", "Estado"]].rename(columns={
            "gateway": "Antena", "medidores": "Medidores", "en_silencio": "En silencio >72 h",
            "pct_silencio": "% en silencio"}), hide_index=True, column_config={
            "% en silencio": st.column_config.ProgressColumn("% en silencio", min_value=0, max_value=100, format="%d%%")})
        st.caption("Si ≥60 % de una antena calla a la vez, el problema es la comunicación, no cada medidor.")
    with t2:
        tc = ab.sort_values(["prioridad", "dias_abiertos"], ascending=[True, False])
        _df(tc[["caso", "suministro", "tipo_local", "anomalia", "responsable", "estado",
                "dias_abiertos", "soles_riesgo"]].rename(columns={
            "caso": "Caso", "suministro": "Suministro", "tipo_local": "Tipo (sim.)", "anomalia": "Anomalía",
            "responsable": "Responsable", "estado": "Estado", "dias_abiertos": "Días abiertos",
            "soles_riesgo": "Soles en riesgo"}), hide_index=True, height=360)
        if len(tc):
            sel = st.selectbox("Cerrar caso (demo): resultado se registra y recalibra umbrales", tc["caso"].tolist())
            if st.button("Cerrar caso"):
                st.session_state.setdefault("cerrados", []).append(sel)
                st.rerun()


# =============================================================================
# 7. APP: menú lateral + parámetros + verificación contra el expediente
# =============================================================================
def main():
    inyectar_css()
    maestro, lecturas, base, casos, antenas = correr_motor()

    with st.sidebar:
        st.markdown('<div class="at-brand">AQUA<br>TRACK</div>'
                    '<div class="at-sub">LIDERESAS POR EL AGUA</div>', unsafe_allow_html=True)
        vista = st.radio("Vista", ["A · Cliente (gestora del local)", "B · Sedapal (ESCE)"],
                         label_visibility="collapsed")
        st.markdown("---")

    if vista.startswith("A"):
        vista_cliente(maestro, lecturas, base, casos)
    else:
        vista_sedapal(maestro, base, casos, antenas)

    with st.sidebar:
        st.markdown("---")
        with st.expander("Reglas del motor (umbrales)"):
            st.table(pd.DataFrame({"Umbral": list(UMBRALES), "Valor": [str(v) for v in UMBRALES.values()]}))
        with st.expander("Datos simulados vs. expediente"):
            st.write(pd.DataFrame({
                "Dato": ["Medidores (maestro)", "Antenas", "Suministros con lecturas", "Lecturas",
                         "Planos (índice sin cambio)", "Consumo nocturno continuo (reglas)",
                         "Suministros con retrocesos", "Retrocesos", ">72 h sin transmitir"],
                "Expediente": [1703, 78, 1611, 768764, 130, 69, 407, 2600, 249],
                "Mock": [len(maestro), maestro["gateway"].nunique(), lecturas["suministro"].nunique(),
                         len(lecturas), int(base["plano"].sum()), int(base["fuga_nocturna"].sum()),
                         int((base["retrocesos"] > 0).sum()), int(base["retrocesos"].sum()),
                         int(base["sin_tx"].sum())],
            }))
            st.caption("Lecturas cada 3 h del 1-ago al 24-oct con huecos. Todo es simulado y declarado.")
        st.caption("Datos simulados con las cifras del expediente. Sin IA generativa en el motor. "
                   "Tarifa feb-2026 (DS04a) como parámetro actualizable.")


if __name__ == "__main__":
    main()
