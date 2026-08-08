# Registro de decisiones técnicas y de producto

## Usuario y decisión
**Usuario:** técnico de protección civil/servicios sociales municipal.
**Decisión que habilita:** priorizar comunicación preventiva ante
riesgo térmico, por municipio, con antelación de 1-3 días.

## Arquitectura Load / Staging / Trusted
**Decisión:** Staging es una tabla ACUMULADA POR FUENTE, sin cruzar
entre sí. Trusted es la única tabla que cruza + calcula, con upsert.
**Alternativa descartada:** una única tabla de Staging ya mezclada.
**Por qué se corrigió:** mezclar fuentes en Staging rompe el patrón
estándar y sobrescribía Trusted entero en cada ejecución.

## Python + scripts, en vez de dbt Core (MVP)
Con 4 municipios y dos fuentes, dbt añadiría abstracción sin reducir
complejidad real. Se revisará al escalar a toda la Comunitat Valenciana.

## CSV directo del INE, en vez de la API JSON Tempus3
La API JSON falló en dos puntos reales (tabla inicial de Murcia, no
España; endpoint de valores inexistente). El CSV es más simple y
verificado contra datos reales.

## Índice de riesgo propio, en vez de replicar el Índice Kairós oficial
El modelo oficial requiere datos de mortalidad diaria no públicos.
Aproximación inspirada, documentada como tal.

## Control de calidad explícito (src/quality/)
**Motivación real:** el 2026-08-01 la ingesta de AEMET falló para
Benidorm por un error de red puntual, sin aviso visible más allá de un
WARNING enterrado en el log.
**Decisión:** distinguir avisos (municipio ausente, se recupera solo)
de errores críticos (nulos, rangos imposibles, duplicados) que sí
detienen el pipeline.

## Qué queda fuera del MVP a propósito
- Toda la Comunitat Valenciana (solo 4 municipios de Alicante).
- Automatización con GitHub Actions.
- Cualquier componente predictivo.
- Notificaciones automáticas.

## Backlog para próximas versiones (anotado, no implementado)
- **Ampliar municipios/comunidades autónomas**: siguiente paso de escalado natural (ver `docs/DEFINICION_FUNCIONAL.md`).
- **Repensar la sección de edad**: el desglose actual (varias gráficas de barras sueltas) no comunica bien por sí solo — evaluar una única visualización combinada (p. ej. población vulnerable superpuesta directamente sobre la tarjeta de riesgo del municipio, en vez de una sección aparte) para que aporte lectura, no solo datos.
