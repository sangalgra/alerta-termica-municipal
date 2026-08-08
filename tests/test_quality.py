"""Tests del control de calidad, incluyendo el caso real que lo motivó
(fallo puntual de AEMET para un municipio, como pasó con Benidorm)."""

import pandas as pd
import pytest

from src.quality.checks import QualityCheckError, run_quality_checks


def _df_base() -> pd.DataFrame:
    # Se usa la fecha de HOY (no una fecha fija escrita a mano) para
    # que el test de frescura no dependa de cuándo se ejecute — una
    # fecha fija "caduca" con el paso de los días reales y el test
    # empieza a fallar solo sin que el código tenga ningún fallo (bug
    # real detectado el 2026-08-07: el test usaba "2026-08-01" y dejó
    # de pasar en cuanto quedaron más de 3 días de diferencia).
    hoy = pd.Timestamp.now().normalize().strftime("%Y-%m-%d")
    return pd.DataFrame([
        {"municipio_id": "03014", "fecha": hoy, "temp_max_prevista": 32.0, "indice_riesgo": 10.0, "nivel_alerta": "bajo"},
        {"municipio_id": "03065", "fecha": hoy, "temp_max_prevista": 33.0, "indice_riesgo": 12.0, "nivel_alerta": "bajo"},
        {"municipio_id": "03099", "fecha": hoy, "temp_max_prevista": 31.0, "indice_riesgo": 9.0, "nivel_alerta": "bajo"},
    ])


def test_municipio_ausente_es_warning_no_bloquea():
    """Caso real: Benidorm (03031) falló la ingesta un día por un error
    de red puntual. Debe avisar, no bloquear el pipeline."""
    df = _df_base()
    report = run_quality_checks(df, {"03014", "03031", "03065", "03099"})
    assert report.ok
    assert len(report.warnings) == 1
    assert "03031" in report.warnings[0]


def test_temperatura_fuera_de_rango_bloquea():
    df = _df_base()
    df.loc[0, "temp_max_prevista"] = 150.0
    with pytest.raises(QualityCheckError):
        run_quality_checks(df, {"03014", "03065", "03099"})


def test_duplicados_municipio_fecha_bloquea():
    df = pd.concat([_df_base(), _df_base().iloc[[0]]], ignore_index=True)
    with pytest.raises(QualityCheckError):
        run_quality_checks(df, {"03014", "03065", "03099"})


def test_nulos_en_columna_critica_bloquea():
    df = _df_base()
    df.loc[0, "indice_riesgo"] = None
    with pytest.raises(QualityCheckError):
        run_quality_checks(df, {"03014", "03065", "03099"})


def test_todos_los_municipios_presentes_sin_avisos():
    df = _df_base()
    report = run_quality_checks(df, {"03014", "03065", "03099"})
    assert report.ok
    assert len(report.warnings) == 0
