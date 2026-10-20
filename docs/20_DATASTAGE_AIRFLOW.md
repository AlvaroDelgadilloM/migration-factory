# 20 — IBM DataStage → Apache Airflow

Adaptador del contrato `migration-factory-datastage-airflow/` (docs 51–68). Usa la misma plataforma que Java/Camel:
mismos orígenes, jobs, cola, auditoría y artefactos. Lo que cambia es el `migrationType` del proyecto:
`JAVA_CAMEL` o `DATASTAGE_AIRFLOW`.

## Flujo

```text
DSX / ZIP → parser → IR (datastage-ir.json) → análisis (inventario, grafo, oleadas, complejidad) → registry por etapa
→ generación (DAGs, SQL, config, pruebas) → gates → reporte → airflow-<nombre>.zip
```

Código en `backend/mf/datastage/`:

| Módulo | Qué hace |
|---|---|
| `parser.py` | Tokeniza el árbol `BEGIN/END` del DSX: valores entre comillas con escapes y bloques `=+=+=+=`. Arma el IR por OLEType y orden de pines, y separa las propiedades XML de los conectores (`Usage/SQL/...`). Deja registradas como advertencias las secciones y tipos desconocidos, y los jobs truncados (`PARTIAL`). Redacta las credenciales literales antes de que lleguen al IR (`SECRET_FINDING`). |
| `model.py` | IR: `DataStageProject`, `Job`, `Stage`, `Link`, `Parameter`, `SourceEvidence`. Las etapas posteriores no vuelven a leer el DSX. |
| `expressions.py` | Traductor determinístico de expresiones DataStage a SQL Oracle: columnas, parámetros, `If/Then/Else`, nulos, funciones verificadas y `LAG()` para «valor de la fila anterior». Lo que no reconoce lo rechaza, no lo adivina. |
| `registry.py` | `DataStageAirflowRegistry`: una decisión por etapa (SUPPORTED … UNSUPPORTED) tomada de sus propiedades y expresiones. Está separado de los registries de Camel. |
| `analysis.py` | Grafo de dependencias: secuencia→JobActivity, encadenamiento y productor/consumidor. Con él calcula oleadas topológicas, puntaje de complejidad por factores nombrados, migrabilidad, confianza (nivel, no porcentaje) y el piloto del doc 67. |
| `sqlgen.py` | Pushdown a Oracle: por cada etapa escritora arma un SELECT anidado hacia atrás. Si una etapa no tiene traducción verificada, el paso queda MANUAL. |
| `generation.py` | Genera el proyecto Airflow: DAG por secuencia, TaskGroup por job, DDL de staging, manifiesto de conexiones sin secretos, Variables por ambiente, reconciliación y prueba DagBag. |
| `validation.py` | Gates. |
| `adapter.py` | Orquesta el pipeline, el manifiesto con hashes y el ZIP. |

## Decisiones que importan

- **El DSX es entrada no confiable.** Solo se lee: nunca se ejecutan comandos, SQL ni rutinas. El DagBag importa los DAGs
  **generados** en otro intérprete (`MF_AIRFLOW_PYTHON`), con un entorno mínimo.
- **Credenciales:** solo como Airflow Connections. El SQL no menciona `PWD`, y `connection-manifest.json` trae referencias,
  nunca valores. Los parameter sets van a Variables por ambiente y los parámetros del job a `params` del DAG.
- **Sin SQL a medias:** un paso sin traducción genera una tarea que falla con el motivo (`runtime.manual_step`) en vez de
  ejecutar SQL incompleto.
- **«Valor de la fila anterior»** (variables de etapa que leen una variable definida después) se traduce a `LAG() OVER
  (ORDER BY …)`. El orden sale de un Sort previo o de la línea del archivo (`MF_SEQ`, agregado al cargarlo). Sin orden
  conocido, o con estado acumulado (contadores), queda MANUAL. Las variables de etapa se calculan en un SELECT interior, así
  que las restricciones (`WHERE`) nunca contienen funciones de ventana.
- **Merge:** el maestro es el primer pin de entrada. La salida principal es el maestro con la actualización (`LEFT JOIN` si
  `keepBadMasters`) y los rechazos son las actualizaciones sin maestro. Queda en REVIEW.
- **Dependencias de datos que contradicen el orden explícito de una secuencia:** se descartan y se reportan
  (`droppedEdges`); nunca se ignoran en silencio.
- **Estado global:** nunca es `SUCCEEDED` sin reconciliación ejecutada. Lo normal es `PARTIAL_SUCCESS` con
  `FUNCTIONALLY_UNVERIFIED`.

## Gates

| Gate | PASS cuando |
|---|---|
| `DSX_PARSE_GATE` | todo parseado (WARN con advertencias) |
| `DEPENDENCY_GRAPH_GATE` | sin ciclos ni jobs faltantes |
| `STAGE_MAPPING_GATE` | ninguna etapa UNSUPPORTED (WARN con revisión manual) |
| `SECRET_GATE` | sin valores secretos en la salida (WARN si el DSX traía credenciales literales) |
| `CONNECTION_MANIFEST_GATE` | ninguna contraseña en el manifiesto |
| `DAG_PARSE_GATE` | DagBag sin errores de importación (NOT_EXECUTABLE sin Airflow) |
| `SQL_VALIDATION_GATE` | estructura válida (paréntesis, comillas, sin `#PARAM#` sin resolver); estado `SQL_UNVERIFIED` hasta ejecutarlo en Oracle |
| `RECONCILIATION_GATE` | `NOT_EXECUTABLE` hasta comparar contra la salida de DataStage |
| `PACKAGE_GATE` | todo archivo generado está en el ZIP con el hash del manifiesto |

## Uso

```bash
python migrate.py datastage analyze export.zip                      # inventario, clasificación, oleadas, piloto
python migrate.py datastage migrate export.zip --output out \
       --airflow-python /opt/airflow-venv/bin/python                 # proyecto Airflow + gates + ZIP
```

En la plataforma:
1. Al registrar el proyecto, elegir **Tipo de migración: DataStage → Airflow**. El ZIP se valida como exportación DSX, no
   como Maven.
2. En Inventario, usar **«Analizar y migrar»** (job `datastage_migrate`, cola larga).
3. La API es `POST /projects/{id}/datastage-migrations`, `GET /projects/{id}/datastage-migrations` y
   `GET /datastage-migrations/{id}`.

El worker trae Airflow 2.10.5 en `/opt/airflow-venv`, fijado con los constraints oficiales, solo para el DagBag.

## Reconciliación (prueba real)

Que los DAGs importen no prueba que los datos sean los mismos. La prueba real corre el job en DataStage y el DAG en Airflow
con la misma entrada, en un ambiente de pruebas del cliente, y compara lo que escribe cada uno:

1. DataStage escribe en un esquema (legado) y Airflow en otro (migrado).
2. En el ambiente de Airflow: `python tests/reconciliation/run_reconciliation.py --conn <connection> --legacy <ESQUEMA_DS>
   --target <ESQUEMA_AF>` (o `--legacy-conn`/`--target-conn` si son dos bases). Usa Connections de Airflow: la plataforma
   nunca se conecta a las bases del cliente ni recibe credenciales.
3. El script deja `reconciliation-result.json`, que se sube en **DataStage → Reconciliación**
   (`POST /datastage-migrations/{id}/reconciliations`). La plataforma recalcula los veredictos desde los valores (no confía
   en los del archivo), guarda la última medición y el historial, y lo audita.

Por cada tabla Oracle que escribe un job compara: filas, claves distintas, duplicados, nulos por columna, suma/mínimo/máximo
de las numéricas, un checksum (`ORA_HASH` de la fila con separador) y una muestra de claves distintas en los dos sentidos.
Las columnas que se llenan con la hora de ejecución (`CurrentTimestamp`, `CurrentDate`…) se excluyen y se nombran.
`reconciliation.json` trae las consultas exactas y `<job>.sql`, las mismas para correrlas a mano.

Veredictos: tabla `EQUAL` / `DIFFERENT` / `ERROR`; job `VERIFIED` cuando todas sus tablas son iguales; los jobs que no
escriben una tabla Oracle o que no se midieron quedan `NOT_RECONCILED`.

### Prueba previa con Oracle y Airflow reales (CI)

`.github/workflows/datastage-e2e.yml` (sin datos del cliente; corre en GitHub porque necesita Docker): levanta Oracle Free 23
y Airflow 2.10.5, migra el fixture `shop_flow.dsx`, ejecuta el DAG `js_master` de punta a punta y lo reconcilia contra una
emulación fila por fila del job (`backend/tests/e2e/datastage_oracle.py`). Comprueba también que una fila alterada se detecta.
No reemplaza a DataStage: confirma que el SQL generado corre en Oracle, que el DAG ejecuta sus tareas y que la
reconciliación distingue igual de distinto. Encontró y quedaron corregidos: el checksum mal formado, el doble esquema en el
nombre de la tabla y las Variables usadas en expresiones que no se declaraban en `variables.json`.

## Corpus BIBES (regresión)

Son datos del cliente: nunca entran al repositorio. `MF_DATASTAGE_CORPUS=<carpeta>` activa la prueba de regresión.
Resultado con el corpus (8 DSX, 55 jobs, 10 secuencias, DataStage 11.7):
- 100 % parseado, inventariado y clasificado.
- Grafo sin ciclos: 9 oleadas y 6 aristas de datos descartadas, por nombres de DataSet parametrizados repetidos entre flujos.
- 10 DAGs que importan en DagBag sin errores.
- Sin secretos en la salida.
- 126 archivos SQL estructuralmente válidos.
- Resultado: 1 AUTO_MIGRATED, 37 SEMIAUTOMATIC y 17 MANUAL_REVIEW, con las acciones manuales explícitas.


El piloto sugerido (un job LOW, uno MEDIUM, uno HIGH y una secuencia maestra) sale en `migration-report.md` del corpus.

## Límites conocidos

- **Pendiente para Sprint 4:** el fallback con IA (doc 58). Hoy lo no traducible queda MANUAL, con el motivo.
- **Reconciliación:** ejecutarla requiere DataStage y Airflow sobre la misma entrada en el ambiente del cliente (ver arriba).
- **Programación:** el DSX no la trae. `schedule=None` y `config/schedules.yaml` para definirla.
- **Sin traducción, por diseño:** `@PARTITIONNUM` (particionamiento paralelo), contadores entre filas, ColumnGenerator y
  RowGenerator con valores sintéticos, y los bloques PL/SQL por fila (WriteMode 8).
- **Códigos de trigger:** `ConditionType` 0 se trata como «OK/sin condición» y 6 como expresión personalizada (los únicos
  presentes en el corpus). Otros códigos se reportan.
