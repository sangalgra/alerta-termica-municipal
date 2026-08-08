"""App Streamlit — Alerta temprana de vulnerabilidad térmica municipal.

Responsabilidad de este fichero: SOLO leer y presentar. No hace ningún
cálculo de negocio — el índice de riesgo se calcula en
src/features/risk_index.py (capa Trusted) y la pirámide de edad se
deriva aquí mismo con una agregación simple sobre datos ya limpios de
Staging (no se recalcula nada del INE, solo se agrupa para visualizar).

Dos fuentes de datos, cada una para una cosa distinta:
- data/trusted/riesgo_municipio_dia.parquet → estado de riesgo por
  municipio y fecha (una fila por combinación, con histórico).
- data/staging/staging_ine.parquet → población por municipio y edad
  (año a año), para el desglose por franja de edad que esta versión
  añade — no estaba antes.

Por qué hay un selector de fecha: Trusted acumula histórico (varias
fechas por municipio, ver docs/decisiones_tecnicas.md). La primera
versión de esta app ignoraba ese histórico y mostraba siempre "hoy" a
pelo. Se corrige aquí para que el histórico que se guarda en el
pipeline sirva realmente para algo visible.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Alerta térmica municipal — Alicante", layout="wide")

TRUSTED_PATH = Path("data/trusted/riesgo_municipio_dia.parquet")
STAGING_INE_PATH = Path("data/staging/staging_ine.parquet")

NOMBRES_MUNICIPIO = {
    "03014": "Alicante/Alacant",
    "03031": "Benidorm",
    "03065": "Elche/Elx",
    "03099": "Orihuela",
}

COLOR_ALERTA = {"bajo": "🟢", "moderado": "🟡", "alto": "🔴"}

# Franjas de edad usadas por el propio Índice Kairós oficial del Plan
# Nacional (ver docs/decisiones_tecnicas.md) — se reutilizan aquí para
# que el desglose de la app sea comparable con el marco de referencia
# oficial, no una agrupación inventada sin criterio.
FRANJAS_EDAD = [
    (0, 14, "0-14"),
    (15, 44, "15-44"),
    (45, 64, "45-64"),
    (65, 74, "65-74"),
    (75, 84, "75-84"),
    (85, 200, "85+"),
]


# ---------------------------------------------------------------------
# Carga de datos (cacheada para no releer el fichero en cada interacción)
# ---------------------------------------------------------------------

@st.cache_data(ttl=600)
def cargar_trusted() -> pd.DataFrame | None:
    """Carga la tabla de hechos (riesgo por municipio y fecha).

    Devuelve None si el pipeline todavía no se ha ejecutado nunca —
    la app debe poder arrancar igualmente y explicar qué falta, en
    vez de romperse con un error críptico.
    """
    if not TRUSTED_PATH.exists():
        return None
    df = pd.read_parquet(TRUSTED_PATH)
    df["fecha"] = pd.to_datetime(df["fecha"])
    df["municipio_nombre"] = df["municipio_id"].map(NOMBRES_MUNICIPIO).fillna(df["municipio_id"])
    return df


@st.cache_data(ttl=600)
def cargar_piramide_edad() -> pd.DataFrame | None:
    """Carga población por municipio y edad (año a año) desde Staging
    INE, y la agrupa en las franjas de FRANJAS_EDAD.

    Nota: se lee de Staging, no de Trusted, porque la pirámide de edad
    no es parte del índice de riesgo — es información demográfica de
    apoyo, y Staging ya tiene el dato limpio (municipio_id, edad,
    poblacion) sin necesidad de involucrar AEMET ni el maestro de
    umbrales para nada.
    """
    if not STAGING_INE_PATH.exists():
        return None
    df = pd.read_parquet(STAGING_INE_PATH)

    def asignar_franja(edad: int) -> str:
        for minimo, maximo, etiqueta in FRANJAS_EDAD:
            if minimo <= edad <= maximo:
                return etiqueta
        return "sin clasificar"

    df["franja_edad"] = df["edad"].apply(asignar_franja)
    agrupado = df.groupby(["municipio_id", "franja_edad"], as_index=False)["poblacion"].sum()
    agrupado["municipio_nombre"] = agrupado["municipio_id"].map(NOMBRES_MUNICIPIO).fillna(agrupado["municipio_id"])
    return agrupado


def seleccionar_fecha_por_defecto(fechas_disponibles: list[pd.Timestamp]) -> pd.Timestamp:
    """Elige qué fecha mostrar por defecto al abrir la app: hoy si
    existe, si no la próxima fecha futura disponible, si no la más
    reciente que haya (misma lógica que antes, ahora solo decide el
    valor INICIAL del selector — el usuario puede cambiarlo)."""
    hoy = pd.Timestamp.now().normalize()
    if hoy in fechas_disponibles:
        return hoy
    futuras = sorted(f for f in fechas_disponibles if f >= hoy)
    if futuras:
        return futuras[0]
    return max(fechas_disponibles)


# ---------------------------------------------------------------------
# Cuerpo de la app
# ---------------------------------------------------------------------

st.title("🌡️ Alerta temprana de vulnerabilidad térmica municipal")
st.caption("Provincia de Alicante — proyecto de portfolio. No es un servicio oficial de alertas.")

df_trusted = cargar_trusted()

if df_trusted is None or df_trusted.empty:
    st.error("No hay datos en Trusted todavía. Ejecuta `python -m src.pipeline` primero.")
    st.stop()

# --- Selector de fecha: aquí es donde el histórico acumulado se usa de verdad ---
fechas_disponibles = sorted(df_trusted["fecha"].unique())
fecha_por_defecto = seleccionar_fecha_por_defecto(fechas_disponibles)
indice_por_defecto = fechas_disponibles.index(fecha_por_defecto)

fecha_seleccionada = st.selectbox(
    "Fecha a consultar (escribe para buscar — el pipeline guarda histórico, puedes moverte entre todas las fechas disponibles)",
    options=fechas_disponibles,
    index=indice_por_defecto,
    format_func=lambda f: f.strftime("%Y-%m-%d"),
)

df_dia = df_trusted[df_trusted["fecha"] == fecha_seleccionada].sort_values("municipio_nombre")

st.subheader(f"Estado por municipio — {fecha_seleccionada.date()}")

if df_dia.empty:
    st.info("No hay municipios con datos para esta fecha (probable fallo puntual de ingesta ese día).")
else:
    columnas = st.columns(len(df_dia))
    for columna, (_, fila) in zip(columnas, df_dia.iterrows()):
        with columna:
            emoji = COLOR_ALERTA.get(fila["nivel_alerta"], "⚪")
            st.metric(
                label=f"{emoji} {fila['municipio_nombre']}",
                value=f"{fila['temp_max_prevista']:.1f}°C",
                delta=f"{fila['exceso_sobre_umbral']:+.1f}°C vs umbral",
                delta_color="inverse",
            )
            st.caption(f"Nivel: **{fila['nivel_alerta']}** · Índice: {fila['indice_riesgo']:.0f}/100")

municipios_ausentes = set(NOMBRES_MUNICIPIO.values()) - set(df_dia["municipio_nombre"])
if municipios_ausentes:
    st.caption(f"⚠️ Sin dato para esta fecha: {', '.join(sorted(municipios_ausentes))} (probable fallo puntual de ingesta).")

st.divider()

# --- Detalle por municipio: temperatura vs umbral a lo largo de TODO el histórico ---
municipio_sel = st.selectbox("Selecciona un municipio para ver el detalle", sorted(df_trusted["municipio_nombre"].unique()))
df_municipio = df_trusted[df_trusted["municipio_nombre"] == municipio_sel].sort_values("fecha")
fila_seleccionada = df_municipio[df_municipio["fecha"] == fecha_seleccionada]

st.subheader(f"Detalle — {municipio_sel}")

if fila_seleccionada.empty:
    st.info(f"No hay dato de {municipio_sel} para la fecha seleccionada ({fecha_seleccionada.date()}).")
else:
    fila = fila_seleccionada.iloc[0]
    c1, c2, c3 = st.columns(3)
    c1.metric("Temperatura máxima prevista", f"{fila['temp_max_prevista']:.1f}°C")
    c2.metric("Umbral de la zona (Plan Nacional)", f"{fila['umbral_zona']:.1f}°C")
    c3.metric("% población 65+ años", f"{fila['pct_pob_65_mas']:.1f}%")

st.caption("Predicción vs. umbral a lo largo de todo el histórico acumulado por el pipeline (no solo la fecha seleccionada arriba):")
st.line_chart(df_municipio.set_index("fecha")[["temp_max_prevista", "umbral_zona"]])

with st.expander("¿Por qué se guarda histórico si la app antes solo mostraba 'hoy'?"):
    st.markdown(
        """
        El pipeline acumula histórico a propósito (ver
        `docs/decisiones_tecnicas.md`), pensado para dos usos: que esta
        app pueda mostrar tendencia a lo largo de varios días (lo que
        ves en el selector de fecha y en el gráfico de arriba), y para
        poder comparar en el futuro las predicciones de AEMET con lo
        que acabó pasando realmente. El selector de fecha es lo que
        hace que ese histórico, que ya se guardaba, sirva para algo
        visible en la app.
        """
    )

st.divider()

# --- Pirámide de edad: el diferencial que faltaba respecto a la herramienta oficial ---
st.subheader(f"Estructura de edad — {municipio_sel}")
st.caption(
    "El Ministerio de Sanidad da el nivel de alerta por zona, sin cruzarlo con la "
    "demografía del municipio. Aquí sí: franjas de edad iguales a las que usa el "
    "propio Índice Kairós oficial, para que sea comparable con el marco de referencia."
)

df_piramide = cargar_piramide_edad()

if df_piramide is None:
    st.info("No hay datos de población por edad todavía (ejecuta `python -m src.pipeline`).")
else:
    codigo_municipio_sel = next((cod for cod, nombre in NOMBRES_MUNICIPIO.items() if nombre == municipio_sel), None)
    df_piramide_municipio = df_piramide[df_piramide["municipio_id"] == codigo_municipio_sel].copy()

    if df_piramide_municipio.empty:
        st.info(f"No hay datos de población por edad para {municipio_sel} todavía.")
    else:
        orden_franjas = [etiqueta for _, _, etiqueta in FRANJAS_EDAD]
        df_piramide_municipio["franja_edad"] = pd.Categorical(
            df_piramide_municipio["franja_edad"], categories=orden_franjas, ordered=True
        )
        df_piramide_municipio = df_piramide_municipio.sort_values("franja_edad")
        st.bar_chart(df_piramide_municipio.set_index("franja_edad")["poblacion"])

st.divider()
st.subheader("⚠️ Limitaciones")
st.markdown(
    """
- Este índice es una **aproximación propia**, inspirada en el Índice
  Kairós oficial del Plan Nacional de Actuaciones Preventivas —
  **no una réplica**: no se dispone de datos de mortalidad diaria.
- Ámbito del MVP: solo 4 municipios de la provincia de Alicante.
- **Temperatura**: predicción de AEMET, no una observación real.
- **Índice de riesgo**: estimación que combina exceso térmico (70%) y
  % de población mayor de 65 años (30%) — no es un diagnóstico
  sanitario ni sustituye a las alertas oficiales.
- La pirámide de edad usa el padrón más reciente disponible en el
  INE, no una proyección en tiempo real.
- Fuente oficial de referencia: [Ministerio de Sanidad — Meteosalud](https://www.sanidad.gob.es/excesoTemperaturas/meteosalud.do)
"""
)

st.caption("Metodología completa y decisiones técnicas documentadas en docs/decisiones_tecnicas.md del repositorio.")
