"""Tests unitarios del cliente AEMET. No llaman a la API real."""

import json
from pathlib import Path

import pytest

from src.ingestion.aemet_client import AemetClient, AemetClientError, AemetConfig


@pytest.fixture
def config(tmp_path: Path) -> AemetConfig:
    return AemetConfig(
        base_url="https://opendata.aemet.es/opendata/api",
        prediccion_municipio_endpoint="/prediccion/especifica/municipio/diaria/{municipio_id}",
        rate_limit_requests_per_minute=50,
        request_delay_seconds=0,
        timeout_seconds=5,
        max_retries=1,
        raw_dir=tmp_path / "raw",
    )


def test_client_requires_api_key(config: AemetConfig, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AEMET_API_KEY", raising=False)
    with pytest.raises(AemetClientError):
        AemetClient(config, api_key=None)


def test_save_raw_writes_file_with_metadata(config: AemetConfig):
    client = AemetClient(config, api_key="fake-token")
    payload = {
        "metadata_response": {"estado": 200, "datos": "https://example.com/data"},
        "data": {"origen": {"municipio": "Alicante"}, "prediccion": {"dia": []}},
    }
    out_file = client.save_raw("03014", payload)
    assert out_file is not None
    assert out_file.exists()
    saved = json.loads(out_file.read_text(encoding="utf-8"))
    assert saved["metadatos_extraccion"]["municipio_id"] == "03014"
    assert saved["metadatos_extraccion"]["fuente"] == "AEMET OpenData"
    assert "content_hash" in saved["metadatos_extraccion"]
    assert saved["datos"] == payload["data"]


def test_save_raw_deduplicates_identical_content(config: AemetConfig):
    client = AemetClient(config, api_key="fake-token")
    payload = {
        "metadata_response": {"estado": 200, "datos": "https://example.com/data"},
        "data": {"origen": {"municipio": "Alicante"}, "prediccion": {"dia": []}},
    }
    first_file = client.save_raw("03014", payload)
    second_file = client.save_raw("03014", payload)
    assert first_file is not None
    assert second_file is None
    files_in_raw = list(config.raw_dir.glob("03014_*.json"))
    assert len(files_in_raw) == 1


def test_save_raw_keeps_new_file_when_content_changes(config: AemetConfig):
    client = AemetClient(config, api_key="fake-token")
    payload_v1 = {
        "metadata_response": {"estado": 200, "datos": "https://example.com/data"},
        "data": {"prediccion": {"dia": [{"temp_max": 30}]}},
    }
    payload_v2 = {
        "metadata_response": {"estado": 200, "datos": "https://example.com/data"},
        "data": {"prediccion": {"dia": [{"temp_max": 34}]}},
    }
    first_file = client.save_raw("03014", payload_v1)
    second_file = client.save_raw("03014", payload_v2)
    assert first_file is not None
    assert second_file is not None
    assert first_file != second_file
