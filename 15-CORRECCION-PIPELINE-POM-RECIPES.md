# Corrección del pipeline: POM Repair antes de Recipes

## Objetivo

Corregir el flujo de migración para evitar que las recetas de transformación (`jakarta-jms`, `javax-to-jakarta`, `camel2-to-4`, etc.) se ejecuten sobre un proyecto Maven cuyo `pom.xml` todavía es inválido.

El problema observado es que Maven no puede construir el modelo del proyecto porque existen dependencias Apache Camel sin versión:

```text
org.apache.camel:camel-cdi
org.apache.camel:camel-http4
org.apache.camel:camel-cxf
```

El error aparece antes de que las recipes puedan ejecutarse correctamente:

```text
ProjectBuildingException
'dependencies.dependency.version' ... is missing
```

Por lo tanto, este fallo debe clasificarse como un problema de **baseline/build model** y no como un error propio de la receta `jakarta-jms`.

---

# 1. Problema actual

El flujo actual se comporta de forma equivalente a:

```text
source
  ↓
crear candidate
  ↓
recipe: jakarta-jms
  ↓
Maven intenta construir MavenProject
  ↓
POM inválido
  ↓
recipe FAIL
  ↓
rollback
  ↓
candidate-build
  ↓
mismo POM inválido
  ↓
FAIL
```

Esto provoca que:

- las recipes fallen antes de poder analizar el código;
- los cambios se reviertan;
- el candidate conserve el POM inválido;
- el build final vuelva a fallar por exactamente la misma causa;
- el sistema pueda clasificar incorrectamente el error como una incompatibilidad Jakarta/Camel.

---

# 2. Pipeline corregido

El flujo debe modificarse a:

```text
SOURCE
  ↓
ENVIRONMENT CHECK
  ↓
MAVEN MODEL CHECK
  ↓
BASELINE POM REPAIR
  ↓
BASELINE VALIDATION
  ↓
ANALYSIS
  ↓
CREATE CANDIDATE FROM REPAIRED BASELINE
  ↓
APPLY RECIPES
  ↓
SECRET REMEDIATION
  ↓
CANDIDATE BUILD
  ↓
AUTOFIX
  ↓
TEST
  ↓
REPORT
```

Las recipes no deben ejecutarse hasta que exista un Maven model válido.

---

# 3. Separación de áreas de trabajo

Se recomienda mantener tres árboles independientes.

```text
work/
├── source/
├── baseline/
└── candidate/
```

## source

Copia inmutable del proyecto original.

Nunca debe modificarse.

```text
source/
└── pom.xml original
```

## baseline

Copia destinada exclusivamente a reparar problemas preexistentes necesarios para poder analizar y compilar el legacy.

Ejemplos:

- dependencias sin versión;
- propiedades Maven faltantes;
- BOM faltante;
- plugins Maven incompletos;
- configuración de entorno necesaria para ejecutar Maven.

```text
source
  ↓ COPY
baseline
  ↓ REPAIR
baseline reparado
```

## candidate

Debe crearse desde el baseline reparado, no desde `source`.

```text
baseline reparado
  ↓ COPY
candidate
```

Después se aplican las migraciones reales:

```text
candidate
  ↓
Java 8 → Java 21
javax → jakarta / Spring
Camel 2 → Camel 4
JBoss → Spring Boot
```

Esto evita perder los fixes del baseline.

---

# 4. Maven Model Check

Antes de ejecutar una recipe, validar que Maven puede construir el modelo del proyecto.

Ejecutar primero:

```bash
mvn -B -e validate
```

También se puede usar:

```bash
mvn -B help:effective-pom
```

## Resultado esperado

### Si pasa

```text
MAVEN_MODEL: VALID
```

Continuar al análisis.

### Si falla

```text
MAVEN_MODEL: INVALID
```

No ejecutar recipes.

Enviar el error al `PomRepairEngine`.

---

# 5. Reparación automática de dependencias Camel sin versión

Caso detectado:

```xml
<dependency>
    <groupId>org.apache.camel</groupId>
    <artifactId>camel-cdi</artifactId>
</dependency>

<dependency>
    <groupId>org.apache.camel</groupId>
    <artifactId>camel-http4</artifactId>
</dependency>

<dependency>
    <groupId>org.apache.camel</groupId>
    <artifactId>camel-cxf</artifactId>
</dependency>
```

Si el sistema identifica que el proyecto usa Camel 2.24.3, debe reparar el legacy sin migrarlo todavía.

## Estrategia recomendada: Camel BOM

Agregar:

```xml
<properties>
    <camel.version>2.24.3</camel.version>
</properties>

<dependencyManagement>
    <dependencies>
        <dependency>
            <groupId>org.apache.camel</groupId>
            <artifactId>camel-bom</artifactId>
            <version>${camel.version}</version>
            <type>pom</type>
            <scope>import</scope>
        </dependency>
    </dependencies>
</dependencyManagement>
```

Las dependencias pueden permanecer sin versión explícita.

## Alternativa

Agregar directamente:

```xml
<version>2.24.3</version>
```

a cada dependencia.

El BOM es preferible para proyectos con múltiples módulos Camel.

---

# 6. Regla de autofix

Definir una regla equivalente a:

```yaml
id: MAVEN-CAMEL-MISSING-VERSION
category: BUILD_MODEL
severity: ERROR
match:
  groupId: org.apache.camel
  versionMissing: true
action:
  type: ADD_OR_IMPORT_CAMEL_BOM
confidence: HIGH
autoFix: true
```

Salida esperada:

```text
[MAVEN-CAMEL-MISSING-VERSION]

Detected dependencies without version:
- org.apache.camel:camel-cdi
- org.apache.camel:camel-http4
- org.apache.camel:camel-cxf

Resolved Camel version:
2.24.3

Action:
Imported org.apache.camel:camel-bom:2.24.3

Confidence:
HIGH

AUTO_FIX:
YES
```

---

# 7. No migrar componentes durante Baseline Repair

El baseline repair únicamente debe hacer que el legacy sea válido y analizable.

Por ejemplo:

```text
camel-http4
```

se conserva durante baseline como:

```text
camel-http4:2.24.3
```

No se debe convertir todavía a:

```text
camel-http
```

La transformación:

```text
camel-http4 → camel-http
```

pertenece a la fase de migración Camel 2 → Camel 4.

## Regla fundamental

```text
BASELINE REPAIR != TARGET MIGRATION
```

---

# 8. Precondición para ejecutar Recipes

Todas las recipes deben tener esta precondición:

```python
if not baseline.maven_model_valid:
    raise MigrationBlocked(
        "Recipes cannot run because baseline Maven model is invalid"
    )
```

Flujo recomendado:

```python
model_result = maven_validator.validate_model(baseline_dir)

if not model_result.success:
    repair_result = pom_repair.repair(baseline_dir, model_result)

    if not repair_result.success:
        stop_pipeline("BASELINE_POM_UNREPAIRABLE")

    model_result = maven_validator.validate_model(baseline_dir)

    if not model_result.success:
        stop_pipeline("BASELINE_POM_STILL_INVALID")

candidate = create_candidate_from(baseline_dir)

run_recipes(candidate)
```

---

# 9. Baseline Build

Después de validar el Maven model, ejecutar el build legacy.

Orden recomendado:

```text
mvn validate
    ↓
mvn compile
    ↓
opcional: mvn test
```

Esto permite distinguir:

```text
POM inválido
```

versus:

```text
Código legacy no compila
```

El segundo caso no debe confundirse con un error de migración.

---

# 10. Clasificación de errores

Usar categorías independientes.

```text
ENVIRONMENT
BUILD_MODEL
DEPENDENCY
SOURCE
MIGRATION
TEST
SECURITY
TOOLING
```

## Ejemplo

El error:

```text
'dependencies.dependency.version' ... is missing
```

se clasifica como:

```text
category: BUILD_MODEL
stage: BASELINE
blocking: true
autoFixable: true
```

El error:

```text
package org.apache.camel.component.http4 does not exist
```

cuando se ejecuta sobre Camel 4 se clasifica como:

```text
category: MIGRATION
stage: CANDIDATE
blocking: true
autoFixable: true
```

No deben mezclarse.

---

# 11. Estado de una Recipe

No usar únicamente `PASS/FAIL`.

Estados recomendados:

```text
APPLIED
SKIPPED
ROLLED_BACK
BLOCKED_BY_BASELINE
FAILED
```

Ejemplo para el caso actual:

```text
recipe: jakarta-jms
status: BLOCKED_BY_BASELINE
reason: MAVEN_MODEL_INVALID
```

No debería registrarse como:

```text
jakarta-jms FAILED
```

porque la recipe nunca tuvo una oportunidad válida de ejecutarse.

---

# 12. Manejo de secret-scan

El secret scanner debe distinguir entre:

```text
FINDINGS
```

y:

```text
TOOL_ERROR
```

Estados recomendados:

```text
PASS
WARN
BLOCKED
ERROR
```

## Secret encontrado

Ejemplo:

```properties
ftp.password=Admin123
```

Resultado:

```text
secret-scan: WARN
finding: HARDCODED_CREDENTIAL
```

La migración puede intentar externalizarlo:

```properties
ftp.password=${FTP_PASSWORD}
```

Después vuelve a ejecutar el scan.

## Error de herramienta

Si el scanner no puede ejecutarse:

```text
secret-scan: ERROR
reason: scanner execution failure
```

No debe reportarse igual que un secreto encontrado.

---

# 13. Environment Check

Antes de Maven, verificar:

- versión de Java;
- versión de Maven;
- permisos del workspace;
- espacio disponible;
- posibilidad de ejecutar librerías temporales;
- configuración de Jansi.

Para entornos con `/tmp` montado `noexec`, usar un directorio ejecutable.

Ejemplo:

```bash
mkdir -p /work/tmp/jansi
export MAVEN_OPTS="-Djansi.tmpdir=/work/tmp/jansi -Djansi.force=false"
```

El `MavenBuilder` debe configurar esto automáticamente cuando detecte el problema.

---

# 14. Candidate Build

Solo debe ejecutarse una vez aplicadas las recipes.

Orden:

```text
candidate
  ↓
mvn validate
  ↓
mvn compile
  ↓
mvn test
```

Si falla `validate`, clasificar como `BUILD_MODEL`.

Si falla `compile`, clasificar el error en:

- dependencia;
- import;
- API eliminada;
- cambio Jakarta;
- cambio Camel;
- configuración Spring.

Después enviar los errores auto-corregibles al `AutofixEngine`.

---

# 15. Pipeline final recomendado

```text
1. SOURCE COPY

2. ENVIRONMENT CHECK
   ├── Java
   ├── Maven
   ├── filesystem
   ├── /tmp noexec
   └── Jansi

3. CREATE BASELINE

4. MAVEN MODEL CHECK
   └── mvn validate

5. BASELINE POM REPAIR
   ├── missing versions
   ├── missing BOM
   ├── unresolved properties
   └── invalid plugins

6. BASELINE MODEL REVALIDATION

7. BASELINE COMPILE

8. ANALYSIS
   ├── Java
   ├── Camel
   ├── JBoss
   ├── CDI
   ├── JMS
   ├── REST
   ├── SOAP
   ├── SQL
   └── integrations

9. CREATE CANDIDATE FROM BASELINE

10. APPLY RECIPES
    ├── java8-to-21
    ├── javax-to-jakarta
    ├── jakarta-jms
    ├── camel2-to-4
    └── jboss-to-spring

11. SECRET REMEDIATION

12. CANDIDATE VALIDATE

13. CANDIDATE COMPILE

14. AUTOFIX

15. CANDIDATE TEST

16. REPORT
```

---

# 16. Reporte esperado

Ejemplo:

```text
Migration Run
=============

Baseline
--------
Maven model: INVALID

Detected:
3 Camel dependencies without version

Applied repair:
org.apache.camel:camel-bom:2.24.3

Maven model after repair:
VALID

Baseline compile:
SUCCESS

Recipes
-------
jakarta-jms: APPLIED
camel2-to-4: APPLIED
jboss-to-spring: APPLIED

Security
--------
Secrets detected: 2
Secrets externalized: 2
Rescan: PASS

Candidate
---------
mvn validate: SUCCESS
mvn compile: SUCCESS
mvn test: SUCCESS

Result
------
Migration status: SUCCESS
```

---

# 17. Criterios de aceptación

La corrección se considera terminada cuando:

1. Ninguna recipe se ejecuta sobre un Maven model inválido.
2. Las dependencias Camel sin versión se reparan antes de crear el candidate.
3. El candidate se crea desde el baseline reparado.
4. Un error preexistente del legacy no se registra como error de migración.
5. `jakarta-jms` usa `BLOCKED_BY_BASELINE` cuando el modelo Maven no es válido.
6. `secret-scan` distingue findings de errores de ejecución.
7. El reporte muestra claramente qué cambios pertenecen al baseline repair y cuáles a la migración.
8. El proyecto original nunca se modifica.

---

# 18. Prioridad de implementación

## P0

- `MavenModelValidator`
- `PomRepairEngine`
- separación `source/baseline/candidate`
- crear candidate desde baseline reparado
- bloquear recipes cuando Maven model es inválido

## P1

- clasificación avanzada de errores
- manejo automático de Jansi
- estados avanzados de recipes
- secret remediation

## P2

- autofix iterativo de compilación
- scoring de confianza
- reporte comparativo baseline/candidate

---

# Conclusión

El error observado no corresponde todavía a una incompatibilidad real de `jakarta-jms`.

El problema raíz es que el pipeline permite ejecutar recipes antes de garantizar que el proyecto legacy tenga un Maven model válido.

La corrección principal es introducir formalmente una fase de:

```text
MAVEN MODEL VALIDATION
       ↓
BASELINE POM REPAIR
       ↓
BASELINE VALIDATION
```

antes de crear y transformar el candidate.

Esto permite diferenciar correctamente:

```text
problemas heredados del proyecto
```

versus:

```text
problemas introducidos por la migración
```

y hace que el proceso de migración sea determinista, auditable y mucho más seguro.
