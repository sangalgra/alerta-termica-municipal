# Definición funcional

## Problema
Los avisos meteorológicos de AEMET y el marco de riesgo sanitario del
Ministerio de Sanidad (Plan Nacional de Actuaciones Preventivas de los
Efectos del Exceso de Temperaturas) informan **por zona de
meteosalud** — una zona agrupa varios municipios — y no cruzan ese
riesgo térmico con la estructura demográfica real de cada población.
Un técnico municipal no tiene, hoy, una vista que le diga "en tu
municipio concreto, con tu perfil de población, esto es lo que
significa el aviso de hoy".

## Usuario objetivo
Técnico de protección civil o de servicios sociales de un ayuntamiento
mediano, o de una diputación con varios municipios a cargo.

## Decisión que habilita
Priorizar la comunicación preventiva (avisos, apertura de centros
climatizados, refuerzo de atención a domicilio) según qué municipios
combinan riesgo térmico alto con población vulnerable, con antelación
de 1 a 7 días (según lo que dé la predicción de AEMET disponible ese
día).

## Alcance de este MVP (v0.1)
- 4 municipios de la provincia de Alicante: Alicante/Alacant,
  Benidorm, Elche/Elx, Orihuela.
- Índice de riesgo propio (no oficial), documentado como aproximación.
- Ejecución manual del pipeline (sin automatización todavía).
- Aplicación interactiva de solo lectura (Streamlit).

## Fuera de alcance, a propósito
- Toda España o toda la Comunitat Valenciana — evolución planificada,
  ver `docs/decisiones_tecnicas.md`.
- Cualquier componente predictivo propio (sin datos de mortalidad
  pública, un modelo predictivo añadiría complejidad sin rigor real).
- Notificaciones automáticas a los técnicos municipales.
- Sustituir o replicar el Índice Kairós oficial.

## Criterios de éxito del MVP
- Pipeline reproducible, ejecutable con un solo comando, sin
  intervención manual salvo la configuración inicial.
- Metodología auditable: cualquier persona puede ver de dónde sale
  cada número y por qué se calculó así.
- Producto interactivo que un técnico municipal (perfil no técnico)
  podría entender sin explicación adicional.
- Cobertura de tests que demuestre que el pipeline se comporta bien
  ante ejecuciones repetidas, no solo en el caso feliz de una sola
  ejecución.

## Roles y responsabilidad de cada capa (resumen funcional)
| Capa | Qué contesta |
|---|---|
| Load (Raw) | "¿Qué dijo la fuente exactamente, y cuándo?" |
| Staging | "¿Qué ha dicho la fuente a lo largo del tiempo?" (histórico limpio, sin mezclar fuentes) |
| Trusted | "¿Cuál es el riesgo, hoy, para cada municipio?" (la única tabla que cruza y calcula) |
| Calidad | "¿Me puedo fiar de este resultado antes de mostrarlo?" |
| App | "¿Cómo se lo explico a alguien que no ha visto el código?" |
