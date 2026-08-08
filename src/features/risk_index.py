"""Etapa Trusted: ÚNICA tabla de hechos del proyecto.

Aquí, y solo aquí, se cruzan las fuentes (AEMET + INE) con el maestro
de umbrales y se calcula el índice de riesgo. Staging (la etapa
anterior) nunca cruza fuentes ni calcula nada — solo limpia y acumula
cada fuente por separado. Ver docs/decisiones_tecnicas.md para el
porqué de esta separación.

Fórmula del índice:
    exceso_sobre_umbral = temp_max_prevista - umbral_zona
    indice_riesgo = 0.7 * exceso_normalizado + 0.3 * vulnerabilidad_normalizada

Es una aproximación PROPIA, inspirada en el Índice Kairós oficial del
Plan Nacional de Actuaciones Preventivas — NO es una réplica: el
Kairós real usa un modelo estadístico ajustado con datos de mortalidad
diaria (MoMo), a los que este proyecto no tiene acceso público. Aquí
se combina, de forma simple y documentada, el exceso térmico sobre el
umbral oficial de la zona con la vulnerabilidad demográfica.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from src.transformation.staging import calcular_pct_poblacion_vulnerable
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class RiskIndexError(Exception):
    """Error específico de esta etapa — se usa para que el pipeline
    (src/pipeline.py) pueda distinguir un fallo de configuración o
    cálculo aquí de un fallo en otra etapa distinta."""


def cargar_umbrales(path: str = "config/umbrales_meteosalud.yaml") -> dict[str, float]:
    """Lee el maestro de umbrales y lo convierte en un diccionario
    directo {municipio_id: umbral_temperatura_c}.

    El fichero YAML organiza los datos por ZONA (cada zona agrupa
    varios municipios), pero para calcular el índice es más cómodo
    tener el umbral ya indexado por municipio — esa "traducción" es
    lo único que hace esta función.

    Salvaguarda importante: si el fichero todavía tiene el valor de
    ejemplo (umbral_temperatura_c: null, tal como viene sin configurar
    la primera vez), esta función lanza un error explicativo en vez de
    dejar que el pipeline calcule un índice falso con datos de mentira.
    Preferimos que el pipeline se pare con un mensaje claro a que
    produzca un número que parezca real sin serlo.
    """
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    umbral_por_municipio: dict[str, float] = {}
    for zona in cfg.get("zonas_meteosalud", []):
        if zona.get("umbral_temperatura_c") is None:
            raise RiskIndexError(
                "config/umbrales_meteosalud.yaml todavía tiene valores de "
                "ejemplo (umbral_temperatura_c: null). Rellena los umbrales "
                "reales antes de calcular el índice — ver instrucciones "
                "dentro de ese mismo fichero."
            )
        for municipio_id in zona.get("municipios_incluidos", []):
            umbral_por_municipio[municipio_id] = zona["umbral_temperatura_c"]

    if not umbral_por_municipio:
        raise RiskIndexError(
            "No hay ninguna zona de meteosalud configurada con municipios "
            "asignados en config/umbrales_meteosalud.yaml."
        )
    return umbral_por_municipio


def calcular_indice_riesgo(
    df_staging: pd.DataFrame,
    umbrales: dict[str, float],
    peso_exceso: float = 0.7,
    peso_vulnerabilidad: float = 0.3,
) -> pd.DataFrame:
    """Calcula el índice de riesgo (0-100) y el nivel de alerta.

    Args:
        df_staging: filas ya cruzadas de AEMET (temp_max_prevista) e
            INE (pct_pob_65_mas), una fila por municipio+fecha.
        umbrales: diccionario municipio_id -> umbral de temperatura,
            tal como lo devuelve cargar_umbrales().
        peso_exceso / peso_vulnerabilidad: pesos del índice compuesto.
            Se dejan como parámetros explícitos (no ocultos dentro del
            cálculo) para que sean fáciles de encontrar, discutir y
            cambiar sin tener que leer la lógica interna. Deben sumar
            1.0 — si no, la función lanza un error en vez de calcular
            algo con un peso total incorrecto.

    Cómo se calcula cada pieza:
        1. exceso_sobre_umbral = cuántos grados por encima del umbral
           oficial de su zona está la temperatura prevista. Puede ser
           negativo (por debajo del umbral, sin riesgo térmico).
        2. exceso_normalizado = ese exceso, recortado a un rango de
           0 a 10 grados (más de 10 grados de exceso no suma más
           riesgo del que ya suma un exceso de 10 — es un límite
           razonable, no un dato validado estadísticamente) y
           reescalado a 0-100.
        3. vulnerabilidad_normalizada = directamente el % de
           población 65+ del municipio (ya está en escala 0-100).
        4. indice_riesgo = media ponderada de las dos anteriores.
        5. nivel_alerta = el índice se traduce a "bajo"/"moderado"/
           "alto" cortando en los tercios (33 y 66).

    Los municipios sin umbral configurado se excluyen (con aviso en el
    log), no se inventan un umbral por defecto.
    """
    if abs((peso_exceso + peso_vulnerabilidad) - 1.0) > 1e-6:
        raise RiskIndexError("peso_exceso + peso_vulnerabilidad debe sumar 1.0")

    df = df_staging.copy()

    df["umbral_zona"] = df["municipio_id"].map(umbrales)
    sin_umbral = df["umbral_zona"].isna()
    if sin_umbral.any():
        municipios_sin_umbral = df.loc[sin_umbral, "municipio_id"].unique().tolist()
        logger.warning("Municipios sin umbral configurado, se excluyen del índice: %s", municipios_sin_umbral)
        df = df.loc[~sin_umbral].copy()

    if df.empty:
        raise RiskIndexError("Ningún municipio tiene umbral configurado — no hay nada que calcular.")

    df["exceso_sobre_umbral"] = df["temp_max_prevista"] - df["umbral_zona"]

    exceso_normalizado = (df["exceso_sobre_umbral"].clip(lower=0, upper=10) / 10) * 100
    vulnerabilidad_normalizada = df["pct_pob_65_mas"].clip(lower=0, upper=100)

    df["indice_riesgo"] = (
        peso_exceso * exceso_normalizado + peso_vulnerabilidad * vulnerabilidad_normalizada
    ).round(1)

    df["nivel_alerta"] = pd.cut(
        df["indice_riesgo"],
        bins=[-0.1, 33, 66, 100],
        labels=["bajo", "moderado", "alto"],
    )

    return df


def run_trusted(config_path: str = "config/settings.yaml") -> Path:
    """Orquesta la etapa Trusted completa: lee las dos tablas de
    Staging (AEMET e INE, cada una acumulada por separado), las cruza,
    calcula el índice, y hace UPSERT sobre la tabla de hechos final.

    Pasos, en orden:
    1. Lee Staging AEMET (histórico de TODAS las extracciones) y se
       queda solo con la más reciente por municipio+fecha — esta
       selección es la única lógica de "negocio" que toca Staging
       AEMET, y por eso vive aquí, no en la etapa de Staging.
    2. Lee Staging INE y calcula el % de población vulnerable.
    3. Cruza ambas por municipio_id.
    4. Carga los umbrales del maestro y calcula el índice.
    5. Hace upsert en Trusted: si ya existe una fila para ese
       municipio+fecha, se sustituye por la nueva (keep="last"); si no
       existía, se añade. Las fechas ya pasadas quedan como histórico
       fijo (nadie las vuelve a tocar); las fechas futuras se
       actualizan cada vez que llega una predicción más reciente de
       AEMET.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    staging_dir = Path(cfg["paths"]["staging_dir"])
    trusted_dir = Path(cfg["paths"]["trusted_dir"])
    trusted_dir.mkdir(parents=True, exist_ok=True)

    staging_aemet_file = staging_dir / "staging_aemet.parquet"
    staging_ine_file = staging_dir / "staging_ine.parquet"
    if not staging_aemet_file.exists():
        raise RiskIndexError(f"No existe {staging_aemet_file}. Ejecuta primero run_staging_aemet().")
    if not staging_ine_file.exists():
        raise RiskIndexError(f"No existe {staging_ine_file}. Ejecuta primero run_staging_ine().")

    # --- 1. AEMET: quedarnos con la predicción más reciente por fecha ---
    df_aemet = pd.read_parquet(staging_aemet_file)
    df_aemet = df_aemet.sort_values("timestamp_extraccion", ascending=False)
    df_aemet = df_aemet.drop_duplicates(subset=["municipio_id", "fecha"], keep="first")

    # --- 2. INE: % de población vulnerable ---
    df_ine = pd.read_parquet(staging_ine_file)
    df_vulnerabilidad = calcular_pct_poblacion_vulnerable(df_ine)

    # --- 3. Cruce de fuentes ---
    df_cruzado = df_aemet.merge(df_vulnerabilidad, on="municipio_id", how="left")
    sin_poblacion = df_cruzado[df_cruzado["pct_pob_65_mas"].isna()]["municipio_id"].unique()
    if len(sin_poblacion) > 0:
        logger.warning("Municipios sin dato de población cruzado (revisar): %s", list(sin_poblacion))

    # --- 4. Cálculo del índice ---
    umbrales = cargar_umbrales()
    df_calculado = calcular_indice_riesgo(df_cruzado, umbrales)

    # --- 5. Upsert en Trusted ---
    out_file = trusted_dir / "riesgo_municipio_dia.parquet"
    if out_file.exists():
        df_previo = pd.read_parquet(out_file)
        df_trusted = pd.concat([df_previo, df_calculado], ignore_index=True)
        df_trusted = df_trusted.drop_duplicates(subset=["municipio_id", "fecha"], keep="last")
    else:
        df_trusted = df_calculado

    df_trusted.to_parquet(out_file, index=False)
    logger.info(
        "Trusted guardado: %s (%d filas calculadas esta ejecución, %d filas totales acumuladas)",
        out_file, len(df_calculado), len(df_trusted),
    )
    return out_file


if __name__ == "__main__":
    run_trusted()
