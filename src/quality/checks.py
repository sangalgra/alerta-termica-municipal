"""Control de calidad sobre la tabla Trusted.

Motivación real: el 2026-08-01, la ingesta de AEMET falló para
Benidorm (03031) tras 3 reintentos por un error de red puntual. El
pipeline siguió adelante sin avisar de forma visible. Este módulo
distingue explícitamente:
- WARNING: problemas de completitud/frescura que se resuelven solos
  en la siguiente ejecución (una fuente caída puntualmente).
- ERROR: problemas de integridad del dato (nulos, rangos imposibles,
  duplicados) que sí indican un fallo real de lógica.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class QualityCheckError(Exception):
    pass


@dataclass
class QualityReport:
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def resumen(self) -> str:
        return f"{len(self.errors)} error(es), {len(self.warnings)} aviso(s)"


TEMP_MIN_PLAUSIBLE = -10.0
TEMP_MAX_PLAUSIBLE = 50.0
COLUMNAS_CRITICAS = ["municipio_id", "fecha", "temp_max_prevista", "indice_riesgo", "nivel_alerta"]


def verificar_completitud_municipios(df: pd.DataFrame, municipios_esperados: set[str], report: QualityReport) -> None:
    """¿Están todos los municipios del ámbito del proyecto en el resultado?

    Si falta alguno (como pasó con Benidorm el 2026-08-01), es un
    WARNING, no un ERROR — suele ser una caída puntual de la fuente
    que se resuelve sola en la siguiente ejecución diaria. Pero debe
    quedar dicho explícitamente en vez de enterrado en el log de otra
    etapa, que es justo lo que pasaba antes de crear este módulo.
    """
    presentes = set(df["municipio_id"].unique())
    faltantes = municipios_esperados - presentes
    if faltantes:
        report.warnings.append(
            f"Municipios ausentes en Trusted (probable fallo puntual de "
            f"ingesta, revisar logs de la etapa 1): {sorted(faltantes)}"
        )


def verificar_nulos_criticos(df: pd.DataFrame, report: QualityReport) -> None:
    """Nulos en columnas críticas SÍ son un error de integridad, no una
    caída temporal — si una fila existe en Trusted, debe estar completa."""
    columnas_presentes = [c for c in COLUMNAS_CRITICAS if c in df.columns]
    for columna in columnas_presentes:
        n_nulos = df[columna].isna().sum()
        if n_nulos > 0:
            report.errors.append(f"{n_nulos} nulo(s) en la columna crítica '{columna}'")


def verificar_rango_temperatura(df: pd.DataFrame, report: QualityReport) -> None:
    """Temperaturas fuera de un rango físicamente plausible en España
    indican un fallo de parseo (p. ej. mezclar grados con otra unidad,
    o leer un campo equivocado), no una ola de calor real por extrema
    que sea — de ahí el rango tan amplio (-10 a 50°C)."""
    fuera_de_rango = df[(df["temp_max_prevista"] < TEMP_MIN_PLAUSIBLE) | (df["temp_max_prevista"] > TEMP_MAX_PLAUSIBLE)]
    if not fuera_de_rango.empty:
        report.errors.append(
            f"{len(fuera_de_rango)} fila(s) con temp_max_prevista fuera del "
            f"rango plausible [{TEMP_MIN_PLAUSIBLE}, {TEMP_MAX_PLAUSIBLE}]°C."
        )


def verificar_duplicados(df: pd.DataFrame, report: QualityReport) -> None:
    """Trusted debe tener como mucho una fila por municipio+fecha — el
    upsert de risk_index.py está diseñado para garantizar esto. Si
    aparece un duplicado, es una señal de que ese upsert se ha roto."""
    duplicados = df.duplicated(subset=["municipio_id", "fecha"]).sum()
    if duplicados > 0:
        report.errors.append(
            f"{duplicados} fila(s) duplicada(s) por municipio+fecha — "
            f"revisar el upsert en src/features/risk_index.py."
        )


def verificar_frescura(df: pd.DataFrame, report: QualityReport, dias_maximos: int = 3) -> None:
    """¿La predicción más reciente en Trusted es de los últimos
    `dias_maximos` días? Si todo el histórico es antiguo, el pipeline
    lleva tiempo sin ejecutarse bien aunque no haya dado ningún error
    explícito en la última ejecución."""
    fecha_mas_reciente = pd.to_datetime(df["fecha"]).max()
    dias_desde_ultima = (pd.Timestamp.now().normalize() - fecha_mas_reciente).days
    if dias_desde_ultima > dias_maximos:
        report.warnings.append(
            f"La fecha más reciente en Trusted es de hace {dias_desde_ultima} "
            f"días — revisar si el pipeline lleva tiempo sin ejecutarse bien."
        )


def run_quality_checks(df_trusted: pd.DataFrame, municipios_esperados: set[str]) -> QualityReport:
    """Ejecuta las 5 comprobaciones de arriba sobre Trusted y agrupa el
    resultado en un QualityReport.

    Lanza QualityCheckError solo si hay errores críticos de
    integridad — la ausencia puntual de un municipio o un histórico
    algo desactualizado (warnings) no detienen el pipeline; un dato
    corrupto (nulos, rangos imposibles, duplicados) sí."""
    report = QualityReport()
    if df_trusted.empty:
        report.errors.append("Trusted está completamente vacío.")
        raise QualityCheckError(f"Control de calidad falló: {report.resumen()} — {report.errors}")

    verificar_completitud_municipios(df_trusted, municipios_esperados, report)
    verificar_nulos_criticos(df_trusted, report)
    verificar_rango_temperatura(df_trusted, report)
    verificar_duplicados(df_trusted, report)
    verificar_frescura(df_trusted, report)

    for w in report.warnings:
        logger.warning("[Calidad] %s", w)
    for e in report.errors:
        logger.error("[Calidad] %s", e)

    if not report.ok:
        raise QualityCheckError(f"Control de calidad falló: {report.resumen()}")

    logger.info("[Calidad] Comprobaciones superadas: %s", report.resumen())
    return report
