"""Cliente de ingesta para la API AEMET OpenData."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class AemetClientError(Exception):
    """Error específico de este cliente (fallo de red tras agotar
    reintentos, respuesta inesperada de AEMET, o falta de token)."""


@dataclass
class AemetConfig:
    base_url: str
    prediccion_municipio_endpoint: str
    rate_limit_requests_per_minute: int
    request_delay_seconds: float
    timeout_seconds: int
    max_retries: int
    raw_dir: Path

    @classmethod
    def from_yaml(cls, path: str = "config/settings.yaml") -> "AemetConfig":
        """Construye la configuración leyendo la sección `aemet:` de
        config/settings.yaml — así los parámetros técnicos (URLs,
        rate limit, reintentos) no quedan escritos a mano en el código."""
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        aemet_cfg = cfg["aemet"]
        return cls(
            base_url=aemet_cfg["base_url"],
            prediccion_municipio_endpoint=aemet_cfg["prediccion_municipio_endpoint"],
            rate_limit_requests_per_minute=aemet_cfg["rate_limit_requests_per_minute"],
            request_delay_seconds=aemet_cfg["request_delay_seconds"],
            timeout_seconds=aemet_cfg["timeout_seconds"],
            max_retries=aemet_cfg["max_retries"],
            raw_dir=Path(cfg["paths"]["raw_dir"]),
        )


class AemetClient:
    def __init__(self, config: AemetConfig, api_key: str | None = None):
        """El token se lee de la variable de entorno AEMET_API_KEY si
        no se pasa explícitamente — nunca se escribe como literal en
        el código ni se versiona (ver .env.example)."""
        self.config = config
        self.api_key = api_key or os.environ.get("AEMET_API_KEY")
        if not self.api_key:
            raise AemetClientError(
                "No se ha encontrado AEMET_API_KEY. Define la variable de "
                "entorno (ver .env.example) antes de ejecutar el cliente."
            )
        self._last_request_time: float | None = None

    def _respect_rate_limit(self) -> None:
        """Espera lo necesario entre peticiones para no superar el
        límite de 50 peticiones/minuto documentado por AEMET. No es un
        limitador de precisión (no lleva ventana deslizante), pero con
        el retraso configurado el volumen del MVP queda muy por debajo
        del límite."""
        if self._last_request_time is not None:
            elapsed = time.monotonic() - self._last_request_time
            wait = self.config.request_delay_seconds - elapsed
            if wait > 0:
                time.sleep(wait)
        self._last_request_time = time.monotonic()

    def _get_with_retries(self, url: str, headers: dict | None = None, params: dict | None = None) -> requests.Response:
        """GET con reintentos y backoff exponencial simple (2, 4, 8...
        segundos entre intentos)."""
        last_exc: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            self._respect_rate_limit()
            try:
                response = requests.get(url, headers=headers, params=params, timeout=self.config.timeout_seconds)
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("Intento %s/%s fallido para %s: %s", attempt, self.config.max_retries, url, exc)
                time.sleep(2 ** attempt)
        raise AemetClientError(f"Fallaron {self.config.max_retries} intentos contra {url}: {last_exc}")

    def _resolve_two_step_call(self, first_call_url: str) -> dict[str, Any]:
        """Resuelve el modelo de doble llamada de AEMET: la primera
        petición devuelve un JSON con una URL temporal ("datos") donde
        reside el contenido real; hay que hacer una segunda petición a
        esa URL para obtener la predicción de verdad. La cabecera
        api_key solo hace falta en la primera llamada."""
        headers = {"api_key": self.api_key, "Accept": "application/json"}
        first_response = self._get_with_retries(first_call_url, headers=headers)
        first_json = first_response.json()
        if first_json.get("estado") != 200 or "datos" not in first_json:
            raise AemetClientError(f"Respuesta inesperada de AEMET en primera llamada: {first_json}")
        data_url = first_json["datos"]
        second_response = self._get_with_retries(data_url)
        try:
            final_data = second_response.json()
        except ValueError as exc:
            raise AemetClientError(f"La URL de datos no devolvió JSON válido: {data_url}") from exc
        return {"metadata_response": first_json, "data": final_data}

    def get_prediccion_municipio(self, municipio_id: str) -> dict[str, Any]:
        """Descarga la predicción diaria específica para un municipio.

        Args:
            municipio_id: código INE de 5 dígitos del municipio.
        """
        endpoint = self.config.prediccion_municipio_endpoint.format(municipio_id=municipio_id)
        url = f"{self.config.base_url}{endpoint}"
        logger.info("Solicitando predicción AEMET para municipio %s", municipio_id)
        return self._resolve_two_step_call(url)

    def save_raw(self, municipio_id: str, payload: dict[str, Any]) -> Path | None:
        """Guarda el payload en la capa Raw con metadatos de
        trazabilidad (fuente, timestamp, hash de contenido), y evita
        duplicar un fichero si el contenido es idéntico al de la
        última extracción de ese municipio.

        Devuelve la ruta del fichero guardado, o None si no se guardó
        nada por ser un duplicado exacto."""
        self.config.raw_dir.mkdir(parents=True, exist_ok=True)
        content_str = json.dumps(payload["data"], ensure_ascii=False, sort_keys=True)
        content_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()
        existing_hash_file = self.config.raw_dir / f"{municipio_id}_last_hash.txt"
        if existing_hash_file.exists():
            previous_hash = existing_hash_file.read_text(encoding="utf-8").strip()
            if previous_hash == content_hash:
                logger.info("Sin cambios para municipio %s respecto a la última extracción — no se duplica en Raw.", municipio_id)
                return None
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        out_file = self.config.raw_dir / f"{municipio_id}_{timestamp}.json"
        record = {
            "metadatos_extraccion": {
                "fuente": "AEMET OpenData",
                "endpoint": self.config.prediccion_municipio_endpoint,
                "municipio_id": municipio_id,
                "timestamp_extraccion_utc": timestamp,
                "content_hash": content_hash,
            },
            "respuesta_intermedia": payload["metadata_response"],
            "datos": payload["data"],
        }
        out_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        existing_hash_file.write_text(content_hash, encoding="utf-8")
        logger.info("Guardado en Raw: %s", out_file)
        return out_file


def ingest_municipios(municipios: list[dict[str, str]], config_path: str = "config/settings.yaml") -> None:
    """Punto de entrada de la ingesta AEMET: descarga la predicción de
    cada municipio de la lista y la guarda en Raw.

    Un fallo en UN municipio (p. ej. un error 429 o 500 puntual de la
    API) no detiene la ingesta del resto — se registra el error y se
    continúa, porque no tiene sentido perder los municipios que sí
    funcionaron por culpa de uno que falló."""
    from dotenv import load_dotenv
    load_dotenv()
    config = AemetConfig.from_yaml(config_path)
    client = AemetClient(config)
    for municipio in municipios:
        try:
            payload = client.get_prediccion_municipio(municipio["id"])
            client.save_raw(municipio["id"], payload)
        except AemetClientError as exc:
            logger.error("Fallo al ingerir municipio %s (%s): %s", municipio["id"], municipio["nombre"], exc)
            continue


if __name__ == "__main__":
    import yaml as _yaml
    with open("config/settings.yaml", "r", encoding="utf-8") as f:
        _cfg = _yaml.safe_load(f)
    ingest_municipios(_cfg["municipios_mvp"])
