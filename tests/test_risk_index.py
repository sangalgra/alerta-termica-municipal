"""Tests del cálculo del índice de riesgo (Trusted)."""

from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.features.risk_index import RiskIndexError, calcular_indice_riesgo, cargar_umbrales


def test_cargar_umbrales_rechaza_placeholder(tmp_path: Path):
    placeholder = {"zonas_meteosalud": [{"zona_id": "EJEMPLO", "umbral_temperatura_c": None, "municipios_incluidos": []}]}
    f = tmp_path / "umbrales.yaml"
    f.write_text(yaml.dump(placeholder), encoding="utf-8")
    with pytest.raises(RiskIndexError, match="valores de ejemplo"):
        cargar_umbrales(str(f))


def test_cargar_umbrales_reales(tmp_path: Path):
    reales = {"zonas_meteosalud": [{"zona_id": "ZONA_ALC_1", "umbral_temperatura_c": 38.0, "municipios_incluidos": ["03014", "03031"]}]}
    f = tmp_path / "umbrales.yaml"
    f.write_text(yaml.dump(reales), encoding="utf-8")
    umbrales = cargar_umbrales(str(f))
    assert umbrales == {"03014": 38.0, "03031": 38.0}


def test_calcular_indice_riesgo_basico():
    df_staging = pd.DataFrame([
        {"municipio_id": "03014", "fecha": "2026-07-30", "temp_max_prevista": 40.0, "pct_pob_65_mas": 20.0},
        {"municipio_id": "03014", "fecha": "2026-07-31", "temp_max_prevista": 30.0, "pct_pob_65_mas": 20.0},
    ])
    resultado = calcular_indice_riesgo(df_staging, {"03014": 38.0})
    assert len(resultado) == 2
    fila_alta = resultado[resultado["fecha"] == "2026-07-30"].iloc[0]
    fila_baja = resultado[resultado["fecha"] == "2026-07-31"].iloc[0]
    assert fila_alta["exceso_sobre_umbral"] == 2.0
    assert fila_alta["indice_riesgo"] > fila_baja["indice_riesgo"]


def test_calcular_indice_riesgo_pesos_deben_sumar_uno():
    df_staging = pd.DataFrame([{"municipio_id": "03014", "fecha": "2026-07-30", "temp_max_prevista": 40.0, "pct_pob_65_mas": 20.0}])
    with pytest.raises(RiskIndexError):
        calcular_indice_riesgo(df_staging, {"03014": 38.0}, peso_exceso=0.5, peso_vulnerabilidad=0.6)


def test_calcular_indice_riesgo_excluye_municipios_sin_umbral():
    df_staging = pd.DataFrame([
        {"municipio_id": "03014", "fecha": "2026-07-30", "temp_max_prevista": 40.0, "pct_pob_65_mas": 20.0},
        {"municipio_id": "99999", "fecha": "2026-07-30", "temp_max_prevista": 40.0, "pct_pob_65_mas": 20.0},
    ])
    resultado = calcular_indice_riesgo(df_staging, {"03014": 38.0})
    assert len(resultado) == 1
    assert resultado.iloc[0]["municipio_id"] == "03014"


def test_calcular_indice_riesgo_sin_municipios_validos_lanza_error():
    df_staging = pd.DataFrame([{"municipio_id": "99999", "fecha": "2026-07-30", "temp_max_prevista": 40.0, "pct_pob_65_mas": 20.0}])
    with pytest.raises(RiskIndexError):
        calcular_indice_riesgo(df_staging, {"03014": 38.0})
