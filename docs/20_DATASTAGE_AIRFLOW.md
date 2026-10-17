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
- **Reconciliación:** se generan las consultas, pero ejecutarlas requiere DataStage y Airflow sobre la misma entrada.
- **Programación:** el DSX no la trae. `schedule=None` y `config/schedules.yaml` para definirla.
- **Sin traducción, por diseño:** `@PARTITIONNUM` (particionamiento paralelo), contadores entre filas, ColumnGenerator y
  RowGenerator con valores sintéticos, y los bloques PL/SQL por fila (WriteMode 8).
- **Códigos de trigger:** `ConditionType` 0 se trata como «OK/sin condición» y 6 como expresión personalizada (los únicos
  presentes en el corpus). Otros códigos se reportan.
