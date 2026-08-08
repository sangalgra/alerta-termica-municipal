"""Orquestador del pipeline completo — este es el único fichero que
hace falta ejecutar para correr el proyecto de principio a fin.

Etapas, en orden:
    0. Comprobación de configuración — falla YA si los umbrales no
       están rellenados, para no gastar llamadas a las APIs reales en
       una ejecución que de todas formas iba a fallar al final.
    1. Ingesta AEMET (predicción por municipio)
    2. Ingesta INE (población por municipio y edad)
    3. Staging — una tabla acumulada por fuente, sin cruzar nada
    4. Trusted — cruce de fuentes + maestro de umbrales + cálculo del
       índice, con upsert sobre el histórico
    5. Control de calidad sobre el resultado de Trusted

Uso: python -m src.pipeline

Manejo de errores: un fallo en las etapas 0, 3, 4 o 5 detiene el
pipeline entero (sys.exit(1)) porque construir sobre datos a medias no
tiene sentido. Dentro de la etapa 1 (ingesta AEMET), sin embargo, un
fallo en UN municipio no detiene a los demás — ver
src/ingestion/aemet_client.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

from src.features.risk_index import RiskIndexError, cargar_umbrales, run_trusted
from src.ingestion.aemet_client import ingest_municipios
from src.ingestion.ine_client import ingest_poblacion
from src.quality.checks import QualityCheckError, run_quality_checks
from src.transformation.staging import StagingError, run_staging_aemet, run_staging_ine
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


def run_pipeline(config_path: str = "config/settings.yaml") -> None:
    """Ejecuta las 6 etapas (0 a 5) descritas en el docstring del
    módulo, en orden, parando con un mensaje claro en la primera que
    falle de forma crítica."""
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    logger.info("=== 0/5 Comprobación de configuración (umbrales) ===")
    try:
        cargar_umbrales()
    except RiskIndexError as exc:
        logger.error("Pipeline detenido antes de empezar — configuración incompleta: %s", exc)
        sys.exit(1)

    logger.info("=== 1/5 Ingesta AEMET ===")
    ingest_municipios(cfg["municipios_mvp"], config_path)

    logger.info("=== 2/5 Ingesta INE ===")
    ingest_poblacion(config_path)

    logger.info("=== 3/5 Staging (AEMET + INE, por separado) ===")
    try:
        run_staging_aemet(config_path)
        run_staging_ine(config_path)
    except StagingError as exc:
        logger.error("Pipeline detenido en Staging: %s", exc)
        sys.exit(1)

    logger.info("=== 4/5 Cálculo del índice de riesgo (Trusted) ===")
    try:
        run_trusted(config_path)
    except RiskIndexError as exc:
        logger.error("Pipeline detenido en Trusted: %s", exc)
        sys.exit(1)

    logger.info("=== 5/5 Control de calidad ===")
    trusted_file = Path(cfg["paths"]["trusted_dir"]) / "riesgo_municipio_dia.parquet"
    df_trusted = pd.read_parquet(trusted_file)
    municipios_esperados = {m["id"] for m in cfg["municipios_mvp"]}
    try:
        run_quality_checks(df_trusted, municipios_esperados)
    except QualityCheckError as exc:
        logger.error("Pipeline detenido en control de calidad: %s", exc)
        sys.exit(1)

    logger.info("=== Pipeline completado correctamente ===")


if __name__ == "__main__":
    run_pipeline()
