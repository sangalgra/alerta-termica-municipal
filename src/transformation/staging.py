"""Etapa de Staging: UNA tabla acumulada POR FUENTE (AEMET, INE).

Regla de oro de esta etapa: Staging NUNCA cruza fuentes entre sí y
NUNCA calcula nada de negocio (ni el índice de riesgo, ni siquiera el
% de población vulnerable). Solo limpia cada fuente por separado y
acumula su histórico. El cruce AEMET+INE y todo el cálculo viven en
Trusted (src/features/risk_index.py) — ver docs/decisiones_tecnicas.md
para el porqué de esta separación (fue una corrección de diseño real,
no la arquitectura original).

Claves naturales de cada tabla de Staging:
- staging_aemet: municipio_id + fecha + timestamp_extraccion (se
  guarda cada extracción por separado, aunque sean predicciones
  distintas para la misma fecha objetivo — la selección de "cuál es
  la vigente" es un cálculo, y por eso se hace en Trusted, no aquí).
- staging_ine: municipio_id + edad + periodo (año).

Los ficheros Raw ya procesados se mueven a data/raw/<fuente>/bkp/ en
vez de borrarse, para poder auditar el dato origen sin reprocesarlo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class StagingError(Exception):
    """Error específico de esta etapa (parseo, columnas inesperadas,
    o ausencia de datos con los que trabajar)."""


def _latest_files(directory: Path, pattern: str) -> list[Path]:
    """Lista los ficheros de una carpeta que encajan con un patrón,
    ordenados por nombre (que a su vez incluye el timestamp de
    extracción, así que el orden por nombre es también orden
    cronológico). El nombre de la función es heredado de una versión
    anterior — hoy devuelve TODOS los ficheros que encajan, no solo
    el último; quien la llama decide qué hacer con la lista completa."""
    return sorted(directory.glob(pattern))


# ---------------------------------------------------------------------
# Parseo de AEMET
# ---------------------------------------------------------------------

def parse_aemet_raw_file(filepath: Path) -> list[dict[str, Any]]:
    """Extrae de un fichero Raw de AEMET una fila por día de predicción.

    Formato esperado del Raw (ver src/ingestion/aemet_client.py): un
    diccionario con "metadatos_extraccion" (de dónde y cuándo vino el
    dato) y "datos" (la respuesta real de AEMET, una lista con un
    elemento por municipio solicitado — para una petición de un solo
    municipio, esa lista tiene longitud 1).

    Devuelve una lista de diccionarios, cada uno con:
    municipio_id, fecha, temp_max_prevista, timestamp_extraccion.
    El timestamp se conserva porque puede haber varias extracciones
    con predicciones distintas para la misma fecha objetivo — hace
    falta para poder elegir luego, en Trusted, cuál es la vigente.
    """
    record = json.loads(filepath.read_text(encoding="utf-8"))
    municipio_id = record["metadatos_extraccion"]["municipio_id"]
    datos = record["datos"]

    if isinstance(datos, list):
        if not datos:
            raise StagingError(f"Respuesta AEMET vacía en {filepath}")
        prediccion_municipio = datos[0]
    else:
        prediccion_municipio = datos

    try:
        dias = prediccion_municipio["prediccion"]["dia"]
    except KeyError as exc:
        raise StagingError(f"Estructura inesperada en {filepath}: no se encuentra prediccion.dia.") from exc

    filas = []
    for dia in dias:
        fecha = dia.get("fecha")
        temp_max = None
        # La temperatura máxima puede venir como escalar o como
        # diccionario según el endpoint exacto de AEMET; se contemplan
        # ambos casos en vez de asumir uno solo.
        temperatura = dia.get("temperatura")
        if isinstance(temperatura, dict):
            temp_max = temperatura.get("maxima")
        elif isinstance(temperatura, (int, float)):
            temp_max = temperatura

        if fecha is None or temp_max is None:
            logger.warning("Día sin fecha o temperatura máxima en %s, municipio %s — se omite.", filepath, municipio_id)
            continue

        filas.append({
            "municipio_id": municipio_id,
            "fecha": fecha,
            "temp_max_prevista": float(temp_max),
            "timestamp_extraccion": record["metadatos_extraccion"]["timestamp_extraccion_utc"],
        })

    return filas


# ---------------------------------------------------------------------
# Parseo de INE (formato CSV — ver src/ingestion/ine_client.py)
# ---------------------------------------------------------------------

_MUNICIPIO_CODIGO_RE = re.compile(r"^(\d{5})\s")


def parse_ine_raw_file(filepath: Path, municipios_objetivo: set[str]) -> pd.DataFrame:
    """Extrae población por municipio y edad de un fichero Raw del INE.

    Espera las columnas reales confirmadas contra datos reales el
    2026-07-30: Sexo, Provincias, Municipios, "Edad (año a año)",
    Periodo, Total.

    Pasos:
    1. Filtra solo la fila de sexo "Total" (evita sumar por error
       Hombres + Mujeres + Total si esas tres categorías coexisten).
    2. Extrae el código de municipio (5 dígitos) del texto de la
       columna Municipios y filtra solo los del ámbito del proyecto.
    3. Convierte el texto de edad ("65 años", "menos de 1 año"...) a
       un número entero, descartando filas agregadas como "Todas las
       edades" que no tienen un valor de edad individual.
    4. Si hay varios años de datos para el mismo municipio+edad
       (padrón de más de un año), se queda con el año más reciente.

    Devuelve un DataFrame con columnas: municipio_id, edad, poblacion.
    """
    record = json.loads(filepath.read_text(encoding="utf-8"))
    registros = record["datos"]

    if not isinstance(registros, list) or not registros:
        raise StagingError(f"Fichero INE vacío o con formato inesperado: {filepath}")

    df = pd.DataFrame(registros)

    columnas_esperadas = {"Municipios", "Edad (año a año)", "Periodo", "Total"}
    faltantes = columnas_esperadas - set(df.columns)
    if faltantes:
        raise StagingError(
            f"Faltan columnas esperadas en {filepath}: {faltantes}. "
            f"Columnas presentes: {df.columns.tolist()}. Revisar si el "
            f"formato del CSV del INE ha cambiado."
        )

    if "Sexo" in df.columns:
        valores_sexo = set(df["Sexo"].dropna().unique())
        candidatos_total = [v for v in valores_sexo if str(v).strip().lower() in ("total", "ambos sexos")]
        if candidatos_total:
            df = df[df["Sexo"] == candidatos_total[0]]
        elif len(valores_sexo) > 1:
            raise StagingError(
                f"La columna Sexo tiene varios valores ({valores_sexo}) y ninguno "
                f"parece ser el total agregado — no se puede filtrar con seguridad "
                f"sin sumar por error."
            )

    df["municipio_id"] = df["Municipios"].astype(str).str.extract(_MUNICIPIO_CODIGO_RE)
    df = df[df["municipio_id"].isin(municipios_objetivo)].copy()

    if df.empty:
        raise StagingError(f"No se extrajo ninguna fila de {filepath} para los municipios objetivo {municipios_objetivo}.")

    df["edad"] = df["Edad (año a año)"].apply(_extraer_edad)
    df = df[df["edad"].notna()]  # descarta filas "Todas las edades" u otras agregadas

    df["anyo"] = df["Periodo"].astype(str).str.extract(r"(\d{4})").astype(float)
    df["poblacion"] = pd.to_numeric(df["Total"], errors="coerce")
    df = df.dropna(subset=["anyo", "poblacion"])

    if df.empty:
        raise StagingError(f"Tras filtrar por edad numérica y periodo válido, no quedan filas utilizables en {filepath}.")

    # Nos quedamos con el año más reciente disponible por municipio+edad
    idx_ultimo_anyo = df.groupby(["municipio_id", "edad"])["anyo"].idxmax()
    df_ultimo = df.loc[idx_ultimo_anyo, ["municipio_id", "edad", "poblacion"]]

    return df_ultimo.reset_index(drop=True)


def _extraer_edad(nombre_serie: str) -> int | None:
    """Convierte el texto de la columna "Edad (año a año)" a un
    entero. Devuelve None para textos no reconocibles (p. ej. "Todas
    las edades"), que luego se descartan.

    Nota de corrección real: el caso especial "menos de 1 año" se
    comprueba ANTES que la regla general, porque el patrón general
    "\\d+ años?" también encaja dentro de esa frase (matchea el "1
    año") y devolvía 1 en vez de 0 — bug detectado con un test real de
    pytest y corregido invirtiendo el orden de las comprobaciones.
    """
    if "menos de 1 año" in nombre_serie.lower():
        return 0
    match = re.search(r"(\d{1,3})\s+años?", nombre_serie)
    if match:
        return int(match.group(1))
    return None


def calcular_pct_poblacion_vulnerable(df_ine: pd.DataFrame) -> pd.DataFrame:
    """Agrega población por municipio y calcula el % de población de
    65+ y 75+ años (los tramos de edad más relevantes para el índice
    de riesgo térmico).

    Espera un DataFrame con columnas municipio_id, edad, poblacion
    (una fila por edad individual, año a año) — la salida de
    parse_ine_raw_file(). Se llama desde Trusted (risk_index.py), no
    desde Staging, porque calcular un % es un cálculo derivado, no
    una limpieza de datos.
    """
    resultado = []
    for municipio_id, grupo in df_ine.groupby("municipio_id"):
        total = grupo["poblacion"].sum()
        if total == 0:
            logger.warning("Población total 0 para municipio %s — se omite.", municipio_id)
            continue
        pob_65_mas = grupo.loc[grupo["edad"] >= 65, "poblacion"].sum()
        pob_75_mas = grupo.loc[grupo["edad"] >= 75, "poblacion"].sum()
        resultado.append({
            "municipio_id": municipio_id,
            "poblacion_total": total,
            "pct_pob_65_mas": round(100 * pob_65_mas / total, 2),
            "pct_pob_75_mas": round(100 * pob_75_mas / total, 2),
        })
    return pd.DataFrame(resultado)


# ---------------------------------------------------------------------
# Orquestación — dos tablas de Staging separadas, cada una se acumula
# ---------------------------------------------------------------------

def run_staging_aemet(config_path: str = "config/settings.yaml") -> Path:
    """Procesa los ficheros Raw de AEMET pendientes y actualiza
    data/staging/staging_aemet.parquet.

    Comportamiento clave (motivado por un fallo real observado el
    2026-08-07): si no hay ningún fichero pendiente porque la fuente
    no cambió desde la última ejecución (deduplicada en Raw), esto NO
    es un error — se conserva el histórico ya existente y se sigue
    adelante. Solo se lanza StagingError si de verdad no hay ningún
    dato, ni nuevo ni histórico, del que partir.

    Tras procesar con éxito, los ficheros usados se mueven a
    data/raw/aemet/bkp/ (nunca se borran).
    """
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    raw_dir = Path(cfg["paths"]["raw_dir"])
    staging_dir = Path(cfg["paths"]["staging_dir"])
    staging_dir.mkdir(parents=True, exist_ok=True)
    out_file = staging_dir / "staging_aemet.parquet"

    pendientes = [f for f in _latest_files(raw_dir, "*.json") if f.parent.name != "bkp"]
    if not pendientes:
        if out_file.exists():
            logger.info(
                "Staging AEMET: sin ficheros Raw nuevos que procesar (sin "
                "cambios desde la última ejecución) — se mantiene el "
                "histórico existente."
            )
            return out_file
        raise StagingError(
            f"No hay ficheros Raw de AEMET pendientes en {raw_dir} y tampoco "
            f"existe histórico previo en Staging — no hay ningún dato del "
            f"que partir. Revisa si la ingesta AEMET llegó a tener éxito "
            f"alguna vez para estos municipios."
        )

    filas = []
    for f in pendientes:
        try:
            filas.extend(parse_aemet_raw_file(f))
        except StagingError as exc:
            logger.error("Error parseando %s: %s", f, exc)
            continue
    df_nuevo = pd.DataFrame(filas)
    if df_nuevo.empty:
        raise StagingError("No se obtuvo ninguna fila válida de AEMET tras el parseo.")

    if out_file.exists():
        # Se ACUMULA con el histórico ya existente — drop_duplicates()
        # aquí solo elimina filas EXACTAMENTE repetidas (por ejemplo si
        # el pipeline se ejecutara dos veces sobre el mismo fichero sin
        # moverlo a bkp/ entre medias), nunca filtra por municipio+fecha:
        # el histórico de distintas extracciones se conserva a propósito.
        df = pd.concat([pd.read_parquet(out_file), df_nuevo], ignore_index=True).drop_duplicates()
    else:
        df = df_nuevo
    df.to_parquet(out_file, index=False)
    logger.info("Staging AEMET: %s (%d filas nuevas, %d totales)", out_file, len(df_nuevo), len(df))

    bkp_dir = raw_dir / "bkp"
    bkp_dir.mkdir(exist_ok=True)
    for f in pendientes:
        f.rename(bkp_dir / f.name)
    return out_file


def run_staging_ine(config_path: str = "config/settings.yaml") -> Path:
    """Procesa el fichero Raw del INE pendiente (si lo hay) y
    actualiza data/staging/staging_ine.parquet.

    Mismo criterio que run_staging_aemet(): sin fichero pendiente pero
    con histórico ya guardado no es un error, es lo normal (el INE
    solo cambia una vez al año).
    """
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    raw_dir = Path(cfg["paths"]["raw_dir_ine"])
    staging_dir = Path(cfg["paths"]["staging_dir"])
    staging_dir.mkdir(parents=True, exist_ok=True)
    municipios_objetivo = {m["id"] for m in cfg["municipios_mvp"]}
    out_file = staging_dir / "staging_ine.parquet"

    pendientes = [f for f in _latest_files(raw_dir, "*.json") if f.parent.name != "bkp"]
    if not pendientes:
        if out_file.exists():
            logger.info(
                "Staging INE: sin ficheros Raw nuevos que procesar (sin "
                "cambios desde la última ejecución) — se mantiene el "
                "histórico existente."
            )
            return out_file
        raise StagingError(
            f"No hay ficheros Raw de INE pendientes en {raw_dir} y tampoco "
            f"existe histórico previo en Staging — no hay ningún dato del que partir."
        )

    df_nuevo = parse_ine_raw_file(pendientes[-1], municipios_objetivo)

    if out_file.exists():
        df = pd.concat([pd.read_parquet(out_file), df_nuevo], ignore_index=True).drop_duplicates()
    else:
        df = df_nuevo
    df.to_parquet(out_file, index=False)
    logger.info("Staging INE: %s (%d filas nuevas, %d totales)", out_file, len(df_nuevo), len(df))

    bkp_dir = raw_dir / "bkp"
    bkp_dir.mkdir(exist_ok=True)
    for f in pendientes:
        f.rename(bkp_dir / f.name)
    return out_file


if __name__ == "__main__":
    run_staging_aemet()
    run_staging_ine()
