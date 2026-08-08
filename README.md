# 🌡️ Alerta temprana de vulnerabilidad térmica municipal

**Estado: MVP (v0.1) funcional — pipeline completo con datos reales y aplicación interactiva.**
No es un servicio oficial de alertas ni sustituye a las fuentes oficiales (ver [Limitaciones](#limitaciones)).

## El problema
Los avisos meteorológicos de AEMET y el marco de riesgo sanitario del Ministerio de Sanidad informan **por zona de meteosalud**, no por municipio, y no cruzan ese riesgo con la estructura demográfica real de cada población.

**Usuario objetivo:** técnico de protección civil o servicios sociales de un ayuntamiento.
**Decisión que habilita:** priorizar comunicación preventiva ante riesgo térmico, por municipio concreto, con antelación de 1-7 días. Definición funcional completa en [`docs/DEFINICION_FUNCIONAL.md`](docs/DEFINICION_FUNCIONAL.md).

**Ámbito de este MVP:** 4 municipios de la provincia de Alicante (Alicante/Alacant, Benidorm, Elche/Elx, Orihuela). Diseñado para escalar a toda la Comunitat Valenciana sin cambios de arquitectura.

## Producto interactivo
```
streamlit run app/app.py
```
Estado por municipio con nivel de alerta, comparación temperatura prevista vs. umbral oficial, selector de fecha sobre el histórico acumulado, y desglose de población por franja de edad (mismas franjas que usa el Índice Kairós oficial).

## Arquitectura de datos
```
Load (Raw) → Staging (una tabla por fuente, histórico acumulado) →
Trusted (única tabla que cruza fuentes y calcula, con upsert) → Control de calidad
```
- AEMET e INE entran por separado, cada uno con su propia tabla de Staging — nunca se mezclan antes de Trusted.
- Trusted cruza AEMET + INE + el maestro de umbrales, calcula el índice, y hace *upsert* por municipio+fecha: el histórico de fechas pasadas queda fijo, las futuras se actualizan si llega una predicción más reciente.
- Los ficheros Raw ya procesados se mueven a `bkp/` en vez de borrarse.

Cada decisión, con su alternativa descartada y el motivo, en [`docs/decisiones_tecnicas.md`](docs/decisiones_tecnicas.md).

## Gobierno del dato
- Trazabilidad completa: cada fila en Raw lleva fuente, timestamp de extracción y hash de contenido.
- Deduplicación: no se guarda una extracción idéntica a la anterior.
- Umbrales como maestro documentado: valores reales del Plan Nacional, verificados manualmente contra la fuente oficial, con salvaguarda explícita — el pipeline se niega a calcular el índice si el umbral no está confirmado.
- Decisiones registradas, no solo el código final, incluyendo cambios de rumbo reales (p. ej. de API JSON del INE a descarga CSV) documentados con la fecha en que se detectó el problema.

## Calidad del dato
Módulo explícito (`src/quality/checks.py`), motivado por un fallo real: una caída puntual de la API de AEMET dejó un municipio sin datos, sin que quedara visible más allá de un log. El control distingue avisos (fuente caída puntual, se recupera sola) de errores críticos (nulos, rangos imposibles, duplicados) que sí detienen el pipeline.

Validado con tests de integración que reproducen ejecuciones repetidas el mismo día y en días distintos, comprobando que no hay duplicados y que el *upsert* funciona por las claves correctas (`tests/test_pipeline_integration.py`).

## Metodología del índice
Aproximación propia **inspirada** en el Índice Kairós oficial del Plan Nacional — **no una réplica**: no hay acceso público a datos de mortalidad diaria. Combina exceso térmico sobre el umbral de zona (70%) y % de población mayor de 65 años (30%), con pesos documentados y ajustables.

## Puesta en marcha
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # rellenar con token gratuito de AEMET OpenData
pytest tests/ -v
python -m src.pipeline
streamlit run app/app.py
```
Si algo falla, revisa primero [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) — recoge errores reales encontrados durante el desarrollo, con su solución.

## Limitaciones
- MVP: solo 4 municipios de Alicante, no toda España ni toda la Comunitat Valenciana.
- El índice de riesgo es una aproximación propia, no un modelo epidemiológico validado.
- La temperatura es una predicción de AEMET a 1-7 días, no una observación.
- Sin automatización todavía (ejecución manual); GitHub Actions es el siguiente paso natural.
- Sin componente predictivo propio, a propósito (ver justificación en `docs/decisiones_tecnicas.md`).

## Fuentes de datos
- [AEMET OpenData](https://opendata.aemet.es/) — predicción meteorológica municipal.
- [INE — Estadística del Padrón Continuo](https://ine.es/jaxiT3/Datos.htm?t=33591) — población por municipio y edad.
- [Ministerio de Sanidad — Plan Nacional de Actuaciones Preventivas](https://www.sanidad.gob.es/excesoTemperaturas/meteosalud.do) — umbrales de riesgo por zona.

## Estructura del repositorio
```
alerta-termica-municipal/
├── config/                      # settings.yaml, umbrales_meteosalud.yaml (maestro)
├── data/{raw,staging,trusted}   # capas del pipeline (no versionadas)
├── src/
│   ├── ingestion/                (aemet_client.py, ine_client.py)
│   ├── transformation/staging.py (una tabla acumulada por fuente)
│   ├── features/risk_index.py    (Trusted: cruce, cálculo, upsert)
│   ├── quality/checks.py         (control de calidad)
│   └── pipeline.py               (orquestador)
├── app/app.py                   # producto interactivo (Streamlit)
├── tests/                       # unitarios + integración
└── docs/                        # definición funcional, decisiones técnicas, troubleshooting
```

## Documentación completa
- [`docs/DEFINICION_FUNCIONAL.md`](docs/DEFINICION_FUNCIONAL.md) — problema, usuario, decisión, alcance.
- [`docs/decisiones_tecnicas.md`](docs/decisiones_tecnicas.md) — cada decisión técnica con su alternativa descartada.
- [`docs/como_funciona_el_repo.md`](docs/como_funciona_el_repo.md) — guía de cada carpeta y fichero.
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) — errores reales y su solución.
