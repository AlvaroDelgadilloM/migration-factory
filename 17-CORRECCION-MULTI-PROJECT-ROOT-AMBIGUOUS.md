# Corrección: detección de múltiples proyectos Maven y ROOT_AMBIGUOUS

## Objetivo

Corregir el comportamiento del analizador cuando encuentra múltiples archivos `pom.xml` al mismo nivel y no existe un `pom.xml` agregador común.

Actualmente el sistema puede terminar con un error similar a:

```text
ROOT_AMBIGUOUS: Varios pom.xml al mismo nivel sin raíz común:
[
  'develop/commons/common-activemq/pom.xml',
  'develop/commons/common-catalogue/pom.xml',
  'develop/commons/common-datasource/pom.xml',
  'develop/commons/common-epay/pom.xml',
  'develop/commons/common-rate-exchange/pom.xml'
]
```

Este caso no debe considerarse automáticamente un error fatal. Puede representar un repositorio empresarial con múltiples proyectos Maven independientes.

## 1. Problema actual

El detector de proyecto asume implícitamente que debe existir una única raíz Maven.

```text
develop/
└── commons/
    ├── common-activemq/pom.xml
    ├── common-catalogue/pom.xml
    ├── common-datasource/pom.xml
    ├── common-epay/pom.xml
    └── common-rate-exchange/pom.xml
```

No existe:

```text
develop/commons/pom.xml
```

Por lo tanto, el sistema no puede resolver una única raíz y devuelve `ROOT_AMBIGUOUS`, aunque la estructura puede ser válida.

## 2. Tipos de repositorio soportados

El sistema debe clasificar automáticamente:

```text
SINGLE_PROJECT
MAVEN_MULTI_MODULE
MULTI_PROJECT_REPOSITORY
```

### SINGLE_PROJECT

```text
service-a/
├── pom.xml
└── src/
```

Resultado:

```text
repositoryType = SINGLE_PROJECT
root = service-a/pom.xml
```

### MAVEN_MULTI_MODULE

```text
platform/
├── pom.xml
├── module-a/pom.xml
└── module-b/pom.xml
```

Si el POM raíz contiene `<modules>`, clasificar como:

```text
repositoryType = MAVEN_MULTI_MODULE
root = platform/pom.xml
```

### MULTI_PROJECT_REPOSITORY

```text
develop/
└── commons/
    ├── common-activemq/pom.xml
    ├── common-catalogue/pom.xml
    ├── common-datasource/pom.xml
    ├── common-epay/pom.xml
    └── common-rate-exchange/pom.xml
```

Resultado:

```text
repositoryType = MULTI_PROJECT_REPOSITORY
workspaceRoot = develop/commons
```

La ejecución no debe abortar.

## 3. Nueva lógica de detección

```text
buscar todos los pom.xml
      ↓
¿solo uno?
      ├── sí → SINGLE_PROJECT
      └── no
           ↓
¿existe un POM ancestro agregador?
           ├── sí → MAVEN_MULTI_MODULE
           └── no
                ↓
¿hay varios POM hermanos/cercanos?
                ├── sí → MULTI_PROJECT_REPOSITORY
                └── no → ROOT_SELECTION_REQUIRED
```

Cambiar:

```text
ROOT_AMBIGUOUS → FAIL
```

por:

```text
ROOT_AMBIGUOUS → DISCOVER_PROJECTS
```

`ROOT_SELECTION_REQUIRED` solo debe usarse cuando existan varias raíces sin un workspace común razonable y el sistema realmente necesite una sola.

## 4. Descubrimiento de workspace

Calcular el ancestro común más cercano de todos los POM detectados.

Ejemplo:

```text
develop/commons/common-activemq/pom.xml
develop/commons/common-catalogue/pom.xml
develop/commons/common-datasource/pom.xml
```

Resultado:

```text
workspaceRoot = develop/commons
```

## 5. Modelo de datos recomendado

```json
{
  "repositoryType": "MULTI_PROJECT_REPOSITORY",
  "workspaceRoot": "develop/commons",
  "projects": [
    {
      "name": "common-activemq",
      "pom": "develop/commons/common-activemq/pom.xml",
      "packaging": "jar"
    },
    {
      "name": "common-catalogue",
      "pom": "develop/commons/common-catalogue/pom.xml",
      "packaging": "jar"
    }
  ]
}
```

## 6. Análisis independiente por proyecto

Cada proyecto debe pasar por:

```text
project
  ↓
preflight
  ↓
pom validation
  ↓
analysis
  ↓
migration plan
```

Ejemplo:

```text
common-activemq
  → Java 8
  → Spring legacy
  → javax.jms
  → ActiveMQ

common-datasource
  → Java 8
  → JDBC
  → JNDI
```

## 7. Grafo de dependencias internas

Detectar dependencias Maven entre proyectos del mismo workspace.

Ejemplo:

```xml
<dependency>
    <groupId>com.company.commons</groupId>
    <artifactId>common-datasource</artifactId>
</dependency>
```

Si `common-datasource` existe dentro del workspace, registrar:

```text
INTERNAL_DEPENDENCY
```

Ejemplo de grafo:

```text
common-datasource
       ↑
       │
common-epay

common-activemq
       ↑
       │
common-catalogue
```

## 8. Orden de migración

Hacer topological sort del grafo interno.

Ejemplo:

```text
1. common-datasource
2. common-activemq
3. common-rate-exchange
4. common-catalogue
5. common-epay
```

No migrar primero un módulo que depende de otro módulo interno todavía no migrado.

## 9. Manejo de ciclos

Si existe:

```text
A → B
B → C
C → A
```

Registrar:

```text
DEPENDENCY_CYCLE
```

Ejemplo:

```json
{
  "type": "DEPENDENCY_CYCLE",
  "projects": ["A", "B", "C"],
  "severity": "HIGH"
}
```

Bloquear únicamente el orden automático para esos proyectos.

## 10. Análisis transversal del workspace

Además del análisis individual, generar métricas globales:

```text
Workspace findings

Projects: 5
Java 8: 5/5
Camel 2: 4/5
javax.jms: 3/5
ActiveMQ legacy: 2/5
Spring XML: 3/5
```

Esto permite detectar reglas comunes de modernización.

## 11. Reglas compartidas

Ejemplo:

```text
COMMON_RULE-001

Detected:
javax.jms in 3 projects

Target:
jakarta.jms

Affected:
- common-activemq
- common-catalogue
- common-epay
```

## 12. Oleadas de migración

Generar waves según dependencias.

```text
Wave 1
- common-datasource
- common-activemq

Wave 2
- common-rate-exchange
- common-catalogue

Wave 3
- common-epay
```

## 13. CLI recomendado

Workspace completo:

```bash
migrate ./develop/commons --workspace
```

Descubrimiento automático:

```bash
migrate ./develop/commons --discover-projects
```

Proyecto específico:

```bash
migrate ./develop/commons/common-activemq
```

O:

```bash
migrate ./develop/commons \
  --workspace \
  --project common-activemq
```

## 14. Salida esperada

```text
workspace-analysis/
├── projects/
│   ├── common-activemq/
│   ├── common-catalogue/
│   ├── common-datasource/
│   ├── common-epay/
│   └── common-rate-exchange/
├── workspace-report.md
├── dependency-graph.json
├── migration-order.json
├── shared-findings.json
└── waves.json
```

## 15. Ejemplo de reporte

```text
Repository type:
MULTI_PROJECT_REPOSITORY

Workspace:
develop/commons

Projects detected:
5

Aggregator POM:
NOT FOUND

Internal dependencies:
4

Dependency cycles:
0

Migration waves:
3
```

## 16. Pseudocódigo

```python
def detect_repository_type(root):
    poms = find_all_poms(root)

    if len(poms) == 1:
        return SingleProject(poms[0])

    aggregator = find_aggregator_pom(poms)

    if aggregator:
        return MavenMultiModule(
            root=aggregator,
            modules=parse_modules(aggregator)
        )

    workspace = common_ancestor(poms)
    sibling_projects = discover_projects(workspace, poms)

    if len(sibling_projects) > 1:
        return MultiProjectRepository(
            root=workspace,
            projects=sibling_projects
        )

    return RootSelectionRequired(poms)
```

## 17. No crear un POM padre artificialmente

No generar automáticamente:

```text
develop/commons/pom.xml
```

solo para resolver la ambigüedad.

Primero se debe comprender la estructura real del repositorio.

## 18. Integración con el migrador

```text
REPOSITORY DISCOVERY
        ↓
CLASSIFY REPOSITORY
        ↓
DISCOVER PROJECTS
        ↓
BUILD INTERNAL DEPENDENCY GRAPH
        ↓
ANALYZE EACH PROJECT
        ↓
BUILD WORKSPACE FINDINGS
        ↓
CALCULATE MIGRATION ORDER
        ↓
MIGRATE BY WAVES
        ↓
BUILD / TEST
        ↓
WORKSPACE REPORT
```

## 19. Estados nuevos

Agregar:

```text
SINGLE_PROJECT
MAVEN_MULTI_MODULE
MULTI_PROJECT_REPOSITORY
ROOT_SELECTION_REQUIRED
DEPENDENCY_CYCLE
```

`ROOT_AMBIGUOUS` puede mantenerse como finding informativo:

```text
ROOT_AMBIGUOUS_RESOLVED_AS_WORKSPACE
```

## 20. Criterios de aceptación

### Caso 1

Entrada:

```text
project/pom.xml
```

Resultado:

```text
SINGLE_PROJECT
```

### Caso 2

Entrada:

```text
root/pom.xml
root/a/pom.xml
root/b/pom.xml
```

y el root declara módulos.

Resultado:

```text
MAVEN_MULTI_MODULE
```

### Caso 3

Entrada:

```text
commons/a/pom.xml
commons/b/pom.xml
commons/c/pom.xml
```

sin agregador.

Resultado:

```text
MULTI_PROJECT_REPOSITORY
```

La ejecución no debe abortar.

### Caso 4

Existen dependencias internas.

Resultado:

```text
dependency-graph.json
migration-order.json
```

### Caso 5

Existe ciclo.

Resultado:

```text
DEPENDENCY_CYCLE
```

y solo se bloquea la migración automática de los proyectos afectados.

## 21. Resultado esperado para el caso actual

Entrada:

```text
develop/commons/common-activemq/pom.xml
develop/commons/common-catalogue/pom.xml
develop/commons/common-datasource/pom.xml
develop/commons/common-epay/pom.xml
develop/commons/common-rate-exchange/pom.xml
```

Salida:

```text
Repository type:
MULTI_PROJECT_REPOSITORY

Workspace root:
develop/commons

Projects:
5

Action:
Analyze independently and build dependency graph
```

No producir:

```text
ROOT_AMBIGUOUS → FAIL
```

Sino:

```text
ROOT_AMBIGUOUS_RESOLVED_AS_WORKSPACE
```

y continuar.

## 22. Prioridad

```text
HIGH
```

Los monorepos y repositorios empresariales con varios proyectos Maven son comunes. Exigir un único POM raíz limitaría considerablemente el uso del migrador en repositorios reales.
