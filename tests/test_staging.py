"""Tests de parseo de Staging (AEMET e INE), con datos sintéticos."""

import json
from pathlib import Path

import pandas as pd
import pytest

from src.transformation.staging import (
    StagingError,
    _extraer_edad,
    calcular_pct_poblacion_vulnerable,
    parse_aemet_raw_file,
    parse_ine_raw_file,
)


def _write_aemet_fixture(tmp_path: Path) -> Path:
    record = {
        "metadatos_extraccion": {"municipio_id": "03014", "timestamp_extraccion_utc": "20260730T100000000000Z"},
        "datos": [{
            "nombre": "Alicante/Alacant",
            "prediccion": {"dia": [
                {"fecha": "2026-07-30", "temperatura": {"maxima": 34, "minima": 24}},
                {"fecha": "2026-07-31", "temperatura": {"maxima": 36, "minima": 25}},
            ]},
        }],
    }
    f = tmp_path / "03014_20260730T100000000000Z.json"
    f.write_text(json.dumps(record), encoding="utf-8")
    return f


def test_parse_aemet_raw_file(tmp_path: Path):
    fixture = _write_aemet_fixture(tmp_path)
    filas = parse_aemet_raw_file(fixture)
    assert len(filas) == 2
    assert filas[0]["municipio_id"] == "03014"
    assert filas[0]["fecha"] == "2026-07-30"
    assert filas[0]["temp_max_prevista"] == 34.0
    assert "timestamp_extraccion" in filas[0]


def test_parse_aemet_raw_file_missing_prediccion_raises(tmp_path: Path):
    record = {"metadatos_extraccion": {"municipio_id": "03014", "timestamp_extraccion_utc": "x"}, "datos": [{"nombre": "X"}]}
    f = tmp_path / "bad.json"
    f.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(StagingError):
        parse_aemet_raw_file(f)


def test_extraer_edad():
    assert _extraer_edad("03014 Alicante. 65 años.") == 65
    assert _extraer_edad("03014 Alicante. menos de 1 año.") == 0
    assert _extraer_edad("sin edad reconocible") is None


def _write_ine_fixture(tmp_path: Path) -> Path:
    registros = []
    for edad, poblacion in {60: 500, 65: 300, 70: 200, 75: 150, 80: 100}.items():
        registros.append({
            "Sexo": "Total",
            "Provincias": "03 Alicante/Alacant",
            "Municipios": "03014 Alicante/Alacant",
            "Edad (año a año)": f"{edad} años",
            "Periodo": "1 de enero de 2024",
            "Total": poblacion,
        })
    record = {"metadatos_extraccion": {"tabla_id": "33591"}, "datos": registros}
    f = tmp_path / "tabla_33591_20260730T000000000000Z.json"
    f.write_text(json.dumps(record), encoding="utf-8")
    return f


def test_parse_ine_raw_file(tmp_path: Path):
    fixture = _write_ine_fixture(tmp_path)
    df = parse_ine_raw_file(fixture, municipios_objetivo={"03014"})
    assert len(df) == 5
    assert set(df["municipio_id"]) == {"03014"}
    assert df[df["edad"] == 65]["poblacion"].iloc[0] == 300


def test_parse_ine_raw_file_filters_municipios_fuera_de_ambito(tmp_path: Path):
    fixture = _write_ine_fixture(tmp_path)
    with pytest.raises(StagingError):
        parse_ine_raw_file(fixture, municipios_objetivo={"46250"})


def test_parse_ine_raw_file_usa_anyo_mas_reciente(tmp_path: Path):
    registros = [
        {"Sexo": "Total", "Provincias": "03 Alicante/Alacant", "Municipios": "03014 Alicante/Alacant",
         "Edad (año a año)": "65 años", "Periodo": "1 de enero de 2023", "Total": 280},
        {"Sexo": "Total", "Provincias": "03 Alicante/Alacant", "Municipios": "03014 Alicante/Alacant",
         "Edad (año a año)": "65 años", "Periodo": "1 de enero de 2024", "Total": 300},
    ]
    record = {"metadatos_extraccion": {"tabla_id": "33591"}, "datos": registros}
    f = tmp_path / "multi_anyo.json"
    f.write_text(json.dumps(record), encoding="utf-8")
    df = parse_ine_raw_file(f, municipios_objetivo={"03014"})
    assert len(df) == 1
    assert df.iloc[0]["poblacion"] == 300


def test_calcular_pct_poblacion_vulnerable():
    df_ine = pd.DataFrame([
        {"municipio_id": "03014", "edad": 60, "poblacion": 500},
        {"municipio_id": "03014", "edad": 65, "poblacion": 300},
        {"municipio_id": "03014", "edad": 75, "poblacion": 200},
    ])
    resultado = calcular_pct_poblacion_vulnerable(df_ine)
    assert len(resultado) == 1
    fila = resultado.iloc[0]
    assert fila["poblacion_total"] == 1000
    assert fila["pct_pob_65_mas"] == 50.0
    assert fila["pct_pob_75_mas"] == 20.0
