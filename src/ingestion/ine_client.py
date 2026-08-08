"""Cliente de ingesta para la descarga CSV del INE."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yaml

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class IneClientError(Exception):
    """Error específico de este cliente (descarga fallida, columnas
    inesperadas en el CSV, o municipios no encontrados en la tabla)."""


@dataclass
class IneConfig:
    base_url_csv: str
    tabla_id: str
    request_delay_seconds: float
    timeout_seconds: int
    max_retries: int
    raw_dir: Path

    @classmethod
    def from_yaml(cls, path: str = "config/settings.yaml") -> "IneConfig":
        """Lee la sección `ine:` de config/settings.yaml."""
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        ine_cfg = cfg["ine"]
        return cls(
            base_url_csv=ine_cfg["base_url_csv"],
            tabla_id=ine_cfg["tabla_id"],
            request_delay_seconds=ine_cfg["request_delay_seconds"],
            timeout_seconds=ine_cfg["timeout_seconds"],
            max_retries=ine_cfg["max_retries"],
            raw_dir=Path(cfg["paths"]["raw_dir_ine"]),
        )


class IneClient:
    def __init__(self, config: IneConfig):
        self.config = config

    def descargar_y_filtrar(self, municipio_ids: list[str]) -> pd.DataFrame:
        """Descarga el CSV completo de la tabla del INE (toda una
        provincia, del orden de cientos de miles de filas) y se queda
        solo con las filas de los municipios objetivo — no tiene
        sentido arrastrar el resto de la provincia en cada ejecución.

        Se usa CSV en vez de la API JSON del INE porque esta última se
        demostró frágil en el uso real: la primera tabla elegida
        (33859) resultó ser de la Región de Murcia, no de España, y el
        endpoint de navegación de variables/valores dio errores. Ver
        docs/decisiones_tecnicas.md para el detalle completo.

        Args:
            municipio_ids: códigos INE de 5 dígitos a conservar.
        """
        url = f"{self.config.base_url_csv}/{self.config.tabla_id}.csv"
        logger.info("Descargando CSV del INE: %s (puede tardar, es una tabla completa)", url)
        last_exc: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                df = pd.read_csv(url, sep=";", encoding="utf-8-sig", low_memory=False)
                break
            except Exception as exc:
                last_exc = exc
                logger.warning("Intento %s/%s fallido: %s", attempt, self.config.max_retries, exc)
                time.sleep(2 ** attempt)
        else:
            raise IneClientError(f"Fallaron {self.config.max_retries} intentos descargando {url}: {last_exc}")

        if "Municipios" not in df.columns:
            raise IneClientError(f"Columna 'Municipios' no encontrada. Columnas reales: {df.columns.tolist()}.")

        mask = df["Municipios"].astype(str).str.startswith(tuple(municipio_ids))
        df_filtrado = df[mask].copy()
        if df_filtrado.empty:
            raise IneClientError(f"No se encontró ninguna fila para los municipios {municipio_ids} en la tabla {self.config.tabla_id}.")

        logger.info("Filas filtradas para municipios objetivo: %d de %d totales", len(df_filtrado), len(df))
        return df_filtrado

    def save_raw(self, df_filtrado: pd.DataFrame) -> Path | None:
        """Guarda el subconjunto ya filtrado en Raw como JSON, con la
        misma trazabilidad y deduplicación por hash que el cliente de
        AEMET (ver aemet_client.py)."""
        self.config.raw_dir.mkdir(parents=True, exist_ok=True)
        registros = df_filtrado.to_dict(orient="records")
        content_str = json.dumps(registros, ensure_ascii=False, sort_keys=True, default=str)
        content_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()
        hash_file = self.config.raw_dir / f"tabla_{self.config.tabla_id}_last_hash.txt"
        if hash_file.exists() and hash_file.read_text(encoding="utf-8").strip() == content_hash:
            logger.info("Sin cambios en la tabla INE %s — no se duplica en Raw.", self.config.tabla_id)
            return None
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        out_file = self.config.raw_dir / f"tabla_{self.config.tabla_id}_{timestamp}.json"
        record = {
            "metadatos_extraccion": {
                "fuente": "INE — descarga CSV directa",
                "tabla_id": self.config.tabla_id,
                "url_origen": f"{self.config.base_url_csv}/{self.config.tabla_id}.csv",
                "timestamp_extraccion_utc": timestamp,
                "content_hash": content_hash,
                "num_filas": len(registros),
            },
            "datos": registros,
        }
        out_file.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        hash_file.write_text(content_hash, encoding="utf-8")
        logger.info("Guardado en Raw: %s", out_file)
        return out_file


def ingest_poblacion(config_path: str = "config/settings.yaml") -> Path | None:
    """Punto de entrada de la ingesta INE: descarga, filtra a los
    municipios del proyecto, y guarda en Raw."""
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    config = IneConfig.from_yaml(config_path)
    client = IneClient(config)
    municipio_ids = [m["id"] for m in cfg["municipios_mvp"]]
    df_filtrado = client.descargar_y_filtrar(municipio_ids)
    return client.save_raw(df_filtrado)


if __name__ == "__main__":
    ingest_poblacion()
