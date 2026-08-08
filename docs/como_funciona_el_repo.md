# Cómo funciona este repositorio (guía técnica)

## Idea general
Las carpetas se organizan por **tipo de función**, no por orden de
ejecución. El orden real lo marca `src/pipeline.py`.

## Carpeta por carpeta

### `src/`
- **`src/ingestion/`** — descarga datos: `aemet_client.py` (predicción
  meteorológica), `ine_client.py` (población por municipio y edad).
- **`src/transformation/staging.py`** — limpia CADA fuente por
  separado y acumula histórico (`staging_aemet.parquet`,
  `staging_ine.parquet`). No cruza fuentes, no calcula nada de negocio.
- **`src/features/risk_index.py`** — la ÚNICA capa que cruza fuentes
  (AEMET+INE+umbrales) y calcula el índice. Hace upsert en Trusted.
- **`src/quality/checks.py`** — comprueba Trusted después de
  calcularlo: completitud de municipios, nulos, rangos plausibles,
  duplicados, frescura del dato.
- **`src/utils/logging_config.py`** — logging compartido por todos los
  módulos, escribe a `logs/pipeline.log` y a consola.
- **`src/pipeline.py`** — orquestador, único fichero que ejecutas para
  correr todo de principio a fin.

### `app/`
- **`app/app.py`** — producto interactivo (Streamlit). Solo lee de
  Trusted y de Staging INE; no recalcula nada, toda la lógica de
  negocio vive en `src/`.

### `data/`
- **`data/raw/aemet/`** y **`data/raw/ine/`** — lo descargado hoy,
  pendiente de procesar. Tras procesarse, se mueve a `bkp/` (nunca se
  borra) para poder auditar el dato origen.
- **`data/staging/`** — histórico acumulado, una tabla por fuente.
- **`data/trusted/`** — la tabla de hechos final, con upsert por
  municipio+fecha.

### `config/`
- `settings.yaml` — endpoints, municipios, rutas.
- `umbrales_meteosalud.yaml` — umbrales reales del Plan Nacional
  (maestro, sin Staging/Trusted propios). Es un dato ANUAL, no diario
  — no hace falta tocarlo en cada ejecución.

### `tests/`
- `test_aemet_client.py`, `test_staging.py`, `test_risk_index.py`,
  `test_quality.py` — unitarios, cada uno centrado en un módulo.
- `test_pipeline_integration.py` — valida el flujo completo a lo
  largo de varias ejecuciones (mismo día repetido, días distintos):
  sin duplicados, con acumulación correcta de histórico, con upsert
  correcto por las claves definidas.

### `docs/`
- `DEFINICION_FUNCIONAL.md` — problema, usuario, decisión, alcance.
- `decisiones_tecnicas.md` — cada decisión técnica con su alternativa
  descartada y el motivo.
- `TROUBLESHOOTING.md` — errores reales encontrados y su solución.
- este fichero — cómo está organizado el código.

## Qué se ejecuta, y en qué orden
1. `pip install -r requirements.txt` — una sola vez (o cada vez que
   cambie `requirements.txt`).
2. `.env` con token AEMET — una vez por entorno virtual creado.
3. `pytest tests/ -v` — siempre que quieras comprobar que nada se rompió.
4. `python -m src.pipeline` — ejecuta todo: ingesta → staging → trusted → calidad.
5. `streamlit run app/app.py` — abre el producto interactivo.

## Si algo falla
Antes de nada, mira `docs/TROUBLESHOOTING.md` — recoge errores reales
que ya nos han pasado a nosotros construyendo esto, con su solución.
