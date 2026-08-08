"""Test de integración: valida el flujo Load → Staging → Trusted a lo
largo de varias ejecuciones (mismo día repetido, y días distintos).

Casos que cubre, todos motivados por comportamiento REAL observado:
1. Ejecutar dos veces el mismo día con el mismo dato (AEMET/INE sin
   cambios): no debe haber error, no debe haber filas duplicadas.
2. Ejecutar en un día distinto con una predicción AEMET nueva para la
   misma fecha objetivo: Staging debe acumular (más filas = más
   histórico), Trusted debe hacer upsert (mismo nº de filas por
   municipio+fecha, con el valor más reciente).
3. El bug real detectado el 2026-08-07: si TODAS las fuentes de una
   ejecución vienen "sin cambios" (deduplicadas en Raw), no debe
   haber ningún fichero pendiente que procesar — Staging debe
   reconocerlo como "nada que hacer" y no como un fallo fatal,
   siempre que ya exista histórico previo válido.
"""

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.features.risk_index import run_trusted
from src.transformation.staging import StagingError, run_staging_aemet, run_staging_ine


@pytest.fixture
def _shim_parquet(monkeypatch):
    """Este proyecto usa Parquet real (pyarrow) en producción. Aquí se
    sustituye por CSV solo para que el test no dependa de tener
    pyarrow instalado en cualquier máquina que ejecute los tests —
    la lógica de acumulación/upsert es idéntica en ambos formatos."""
    monkeypatch.setattr(pd.DataFrame, "to_parquet", lambda self, path, index=False: self.to_csv(path, index=index))
    monkeypatch.setattr(pd, "read_parquet", lambda path: pd.read_csv(path, dtype={"municipio_id": str}))


def _crear_entorno(tmp_path: Path) -> str:
    for sub in ["raw/aemet", "raw/ine", "staging", "trusted"]:
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    cfg = {
        "paths": {
            "raw_dir": str(tmp_path / "raw/aemet"),
            "raw_dir_ine": str(tmp_path / "raw/ine"),
            "staging_dir": str(tmp_path / "staging"),
            "trusted_dir": str(tmp_path / "trusted"),
        },
        "municipios_mvp": [{"id": "03014", "nombre": "Alicante"}],
    }
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(yaml.dump(cfg))
    return str(config_path)


def _escribir_raw_aemet(tmp_path: Path, ts: str, temp: float, fecha: str = "2026-08-10") -> None:
    r = {
        "metadatos_extraccion": {"municipio_id": "03014", "timestamp_extraccion_utc": ts},
        "datos": [{"prediccion": {"dia": [{"fecha": fecha, "temperatura": {"maxima": temp}}]}}],
    }
    (tmp_path / "raw/aemet" / f"03014_{ts}.json").write_text(json.dumps(r))


def _escribir_raw_ine(tmp_path: Path, filename: str = "tabla_33591.json") -> None:
    registros = [{
        "Sexo": "Total", "Provincias": "03 Alicante/Alacant", "Municipios": "03014 Alicante/Alacant",
        "Edad (año a año)": "65 años", "Periodo": "1 de enero de 2024", "Total": 300,
    }]
    r = {"metadatos_extraccion": {"tabla_id": "33591"}, "datos": registros}
    (tmp_path / "raw/ine" / filename).write_text(json.dumps(r))


def test_reejecucion_mismo_dia_sin_cambios_no_falla_ni_duplica(tmp_path: Path, _shim_parquet):
    """Caso real del 2026-08-07: dos ejecuciones seguidas donde la
    fuente no cambió (Raw deduplicado, sin ficheros pendientes en la
    segunda). No debe lanzar StagingError, y Staging/Trusted no deben
    ganar filas de más."""
    config_path = _crear_entorno(tmp_path)

    _escribir_raw_aemet(tmp_path, "20260807T100000000000Z", 32.0)
    _escribir_raw_ine(tmp_path)
    run_staging_aemet(config_path)
    run_staging_ine(config_path)
    run_trusted(config_path)

    df_stg_aemet_1 = pd.read_parquet(Path(tmp_path / "staging/staging_aemet.parquet"))
    df_trusted_1 = pd.read_parquet(Path(tmp_path / "trusted/riesgo_municipio_dia.parquet"))
    assert len(df_stg_aemet_1) == 1
    assert len(df_trusted_1) == 1

    # Segunda ejecución el MISMO día: no se escriben ficheros Raw
    # nuevos (simula que AEMET/INE devolvieron "sin cambios" y el
    # cliente de ingesta no creó nada nuevo) — exactamente el
    # escenario real que falló.
    run_staging_aemet(config_path)  # no debe lanzar StagingError
    run_staging_ine(config_path)    # no debe lanzar StagingError
    run_trusted(config_path)

    df_stg_aemet_2 = pd.read_parquet(Path(tmp_path / "staging/staging_aemet.parquet"))
    df_trusted_2 = pd.read_parquet(Path(tmp_path / "trusted/riesgo_municipio_dia.parquet"))
    assert len(df_stg_aemet_2) == 1, "no debe duplicar filas cuando no hay nada nuevo"
    assert len(df_trusted_2) == 1, "Trusted no debe crecer si no hay dato nuevo"


def test_ejecucion_dia_distinto_acumula_staging_y_hace_upsert_en_trusted(tmp_path: Path, _shim_parquet):
    """Día 1: predicción de 32°C para el 2026-08-10.
    Día 2: nueva predicción de 38°C para la MISMA fecha objetivo.
    Staging debe acumular (2 filas: las dos extracciones). Trusted
    debe seguir teniendo 1 fila para esa fecha, con el valor más
    reciente (upsert por municipio_id+fecha)."""
    config_path = _crear_entorno(tmp_path)

    _escribir_raw_aemet(tmp_path, "20260807T100000000000Z", 32.0)
    _escribir_raw_ine(tmp_path, "tabla_v1.json")
    run_staging_aemet(config_path)
    run_staging_ine(config_path)
    run_trusted(config_path)

    _escribir_raw_aemet(tmp_path, "20260808T100000000000Z", 38.0)
    run_staging_aemet(config_path)
    run_staging_ine(config_path)  # sin fichero nuevo -> debe ser un no-op, no un error
    run_trusted(config_path)

    df_stg_aemet = pd.read_parquet(Path(tmp_path / "staging/staging_aemet.parquet"))
    df_trusted = pd.read_parquet(Path(tmp_path / "trusted/riesgo_municipio_dia.parquet"))

    assert len(df_stg_aemet) == 2, "Staging debe conservar el histórico de ambas extracciones"
    assert len(df_trusted) == 1, "Trusted no debe duplicar la fila de esa fecha"
    assert df_trusted.iloc[0]["temp_max_prevista"] == 38.0, "Trusted debe reflejar la predicción más reciente (38, no 32)"


def test_staging_ine_sin_pendientes_pero_con_historico_no_es_error(tmp_path: Path, _shim_parquet):
    """El bug exacto del log real: sin ficheros pendientes en Raw,
    pero con histórico ya guardado en Staging, no debe fallar."""
    config_path = _crear_entorno(tmp_path)
    _escribir_raw_ine(tmp_path)
    run_staging_ine(config_path)  # crea el histórico inicial

    # Ninguna ingesta nueva esta vez (carpeta Raw ya vacía tras el bkp)
    out_file = run_staging_ine(config_path)
    assert out_file.exists()
    df = pd.read_parquet(out_file)
    assert len(df) == 1, "no debe duplicar ni perder el histórico existente"


def test_staging_sin_pendientes_y_sin_historico_previo_si_es_error(tmp_path: Path, _shim_parquet):
    """Si de verdad no hay ni Raw pendiente ni histórico previo, no
    hay ningún dato del que partir — esto SÍ debe fallar."""
    config_path = _crear_entorno(tmp_path)
    with pytest.raises(StagingError):
        run_staging_aemet(config_path)
