# Parser Generator Agent

Componente de la arquitectura multi-agente responsable de generar automáticamente el DSL declarativo (`parser.json`) a partir de una muestra de archivo Excel bancario.

## Propósito

El Parser Engine (ver [`parser.md`](./parser.md)) requiere un archivo `parser.json` determinista para procesar los Excels de cada banco de forma segura y veloz en un entorno aislado. Escribir este JSON a mano es propenso a errores y poco escalable al intentar integrar múltiples bancos. 

El **Parser Generator Agent** resuelve este desafío al utilizar un LLM para analizar un Excel de muestra y derivar sus reglas de parseo. Tiene un **rol dual** en el sistema:
1. **Creación inicial (Mapper de datos):** Se ejecuta *una sola vez por banco* durante el setup inicial para generar el primer `parser.json` a partir del Excel de muestra.
2. **Auto-reparación (Remapper de datos):** Si el banco cambia la estructura del Excel en el futuro (ej. añade una columna, cambia la fila de inicio), el parser determinista fallará (`schema_drift`). El Judge enrutará el Excel fallido a este agente para que genere un nuevo `parser.json` adaptado a la nueva estructura.

En ambos casos, delega las corridas regulares diarias al Parser Engine determinista sin uso de LLM.

## Entradas y Salidas

**Inputs:**
1. **Sample Data:** Un archivo Excel real (generalmente extraído por el agente Mapper), el cual suele ser pre-procesado a un subset representativo (CSV o Markdown con las primeras 100 filas) para ajustarse de manera eficiente al contexto del LLM.
2. **Target Schema:** El JSON Schema que describe nuestro modelo unificado Pydantic (`Transaction`, `AccountMetadata`).
3. **DSL Reference:** El listado de helpers whitelisted disponibles en el engine (ej. `extract_regex`, `lookup_table`, `parse_date`, `coalesce`).

**Outputs:**
- Un archivo `parser.json` completo, seguro y válido, listo para ser almacenado en el repositorio de maps.

## Proceso de Inferencia y Generación

Al recibir la muestra del archivo, el LLM deduce la configuración realizando los siguientes análisis:

1. **Estructura Base:** Encuentra `header_row` y la `data_start_row`. 
2. **Límites de los datos:** Descubre heurísticas para `data_end_marker` (por ejemplo, detectar la palabra "Saldo final" o encontrar la primera "row empty").
3. **Mapeo de Columnas (`column_map`):** Asigna cada columna presente en la muestra a un campo del schema objetivo, inyectando las transformaciones (pipelines) necesarias usando *únicamente* los helpers permitidos.
4. **Metadatos e Inferencia:** Construye el dict de `account_metadata_extraction` (ej. extrae el número de cuenta de la celda `B3` si el texto es "Cuenta de Ahorros: ...") y deduce reglas para `account_type_inference` a partir de los nombres de columnas encontrados.

## Loop de Validación y Auto-Corrección

Dado que el `parser.json` será inyectado a un motor determinista, no basta con un enfoque zero-shot confiando ciegamente en el LLM. El agente ejecuta un pipeline de retroalimentación cerrado:

1. **Static Schema Validation:** El candidato `parser.json` generado se evalúa contra el JSON Schema estricto. Revisa que todas las transformaciones usen helpers existentes con argumentos del tipo correcto.
2. **Dry-Run (Ejecución de Prueba):** Se lanza localmente una instancia del Parser Engine invocando `execute(workbook, candidato_parser.json)` contra el Excel original de muestra.
3. **Data Quality Check:**
   - ¿Existen excepciones del linter o expresiones regulares incompatibles con re2?
   - ¿Pudo extraer `account_metadata`?
   - ¿Las transacciones resultantes satisfacen la validación del schema en Pydantic?
   - ¿El porcentaje de filas descartadas es menor al límite aceptable (ej. < 5%)?
4. **Corrección Activa:** Cualquier traza de error de validación o ejecución se devuelve al prompt del LLM en forma de feedback, ordenándole ajustar el JSON candidato. Este loop se iterará hasta un máximo estipulado (ej. 3 intentos).

## Rol en la Arquitectura Multi-Agente

Este agente tiene una relación simétrica con los agentes de navegación web, actuando tanto en la fase de creación como en la de mantenimiento:

1. **Fase de Creación (vs Mapper):** Mientras el Mapper se encarga de sortear el frontend (Vision, DOM, login, clicks) para llegar al archivo, el **Parser Generator Agent** se especializa puramente en interpretar la semántica de datos del Excel y convertirlos en un conjunto de instrucciones legibles para máquinas.
2. **Fase de Reparación (vs Remapper):** Si un selector web se rompe, el Judge llama al Remapper (Vision). Si la estructura del Excel cambia (`ParserError`, `SchemaValidationError`), el Judge llama al **Parser Generator Agent** para hacer *self-healing* del parser.

Separar estas responsabilidades abarata costos y aísla fallos, pues permite re-generar el parser en el futuro ante cambios menores en un archivo, sin invocar toda la travesía de browser automation del Mapper.

### Reglas de Auto-Apply vs HITL

Al igual que las reparaciones web, las reparaciones de datos están sujetas a las reglas de [ADR-0013](../adr/0013-confidence-threshold-remap.md):
- Si el nuevo `parser.json` se genera con alta confianza (pasa todas las validaciones internas del loop de dry-run sin errores y mapea todas las columnas obligatorias), se **auto-aplica** y el workflow se reanuda automáticamente.
- Si hay dudas (ej. desapareció una columna que parece importante, o el dry-run arroja warnings), el sistema emite un webhook `job.remap_proposed` y pausa la ejecución esperando aprobación humana (HITL - Human-in-the-Loop).
