# Corrección del pipeline: POM reparado en candidate y estado real del job

## Objetivo

Corregir dos defectos detectados en el flujo actual de migración:

1. El `candidate/pom.xml` sigue llegando inválido a las recipes y al build final.
2. El job termina como `succeeded` aunque existan recipes fallidas, `secret-scan` fallido, compilación fallida y tests omitidos.

El pipeline debe garantizar que un proyecto candidato solo entre a fase de migración si su modelo Maven es válido y debe reflejar el estado real del proceso al finalizar.

---

# 1. Problema observado

Log representativo:

```text
[recipe:jakarta-jms] Paso jakarta-jms falló y se revirtió
[secret-scan] candidate/secret-scan: FAIL
[candidate-compile] candidate/compile: FAIL
[candidate-compile] candidate/test: SKIPPED
[succeeded] Trabajo succeeded
```

Además Maven reporta:

```text
'dependencies.dependency.version' for org.apache.camel:camel-cdi:jar is missing
'dependencies.dependency.version' for org.apache.camel:camel-http4:jar is missing
'dependencies.dependency.version' for org.apache.camel:camel-cxf:jar is missing
```

Esto confirma que:

- el POM no fue reparado antes de ejecutar recipes;
- o sí se reparó, pero el candidate se recreó desde el source original y perdió la corrección;
- o la reparación se aplicó a otra ruta distinta de `candidate/pom.xml`;
- el orquestador no está agregando correctamente los estados parciales;
- el estado final del job no se deriva de los resultados reales de las fases.

---

# 2. Flujo incorrecto actual

```text
source
  ↓
crear candidate
  ↓
recipe: jakarta-jms
  ↓
Maven intenta cargar POM inválido
  ↓
recipe FAIL
  ↓
secret-scan
  ↓
candidate-build
  ↓
POM inválido
  ↓
compile FAIL
  ↓
test SKIPPED
  ↓
job = succeeded   ❌
```

---

# 3. Flujo correcto

```text
source
  ↓
copy → baseline
  ↓
preflight
  ↓
validate Maven model
  ↓
POM repair
  ↓
revalidate Maven model
  ↓
baseline compile
  ↓
copy baseline repaired → candidate
  ↓
validate candidate POM
  ↓
recipes
  ↓
secret remediation / scan
  ↓
candidate compile
  ↓
candidate test
  ↓
status aggregation
  ↓
SUCCEEDED / PARTIAL_SUCCESS / FAILED / BLOCKED
```

---

# 4. Regla crítica: nunca ejecutar recipes con un POM inválido

Antes de cualquier recipe debe ejecutarse una validación obligatoria.

Pseudo código:

```python
def prepare_candidate(source_dir, baseline_dir, candidate_dir):
    copy_project(source_dir, baseline_dir)

    result = validate_maven_model(baseline_dir)

    if not result.ok:
        repair = repair_pom(baseline_dir)

        if not repair.ok:
            return fail("BASELINE_POM_UNREPAIRABLE")

    result = validate_maven_model(baseline_dir)

    if not result.ok:
        return fail("BASELINE_MAVEN_MODEL_INVALID")

    compile_result = compile_project(baseline_dir)

    if not compile_result.ok:
        return fail("BASELINE_COMPILE_FAILED")

    copy_project(baseline_dir, candidate_dir)

    candidate_model = validate_maven_model(candidate_dir)

    if not candidate_model.ok:
        return fail("CANDIDATE_MAVEN_MODEL_INVALID")

    return success()
```

Solo después:

```python
run_recipe(candidate_dir, "jakarta-jms")
```

---

# 5. Reparación automática específica para Camel 2

## Detección

Detectar dependencias `org.apache.camel:*` sin versión cuando no existe un `dependencyManagement` válido.

Ejemplo:

```xml
<dependency>
    <groupId>org.apache.camel</groupId>
    <artifactId>camel-cdi</artifactId>
</dependency>
```

```xml
<dependency>
    <groupId>org.apache.camel</groupId>
    <artifactId>camel-http4</artifactId>
</dependency>
```

```xml
<dependency>
    <groupId>org.apache.camel</groupId>
    <artifactId>camel-cxf</artifactId>
</dependency>
```

## Resolución

Orden recomendado:

```text
1. Buscar property camel.version
2. Buscar parent que defina Camel
3. Buscar camel-bom existente
4. Inferir versión desde otras dependencias Camel
5. Inferir desde metadata/configuración del proyecto
6. Si se resuelve con alta confianza → insertar camel-bom
7. Si no se puede resolver → bloquear migración
```

Para la demo actual:

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

Después ejecutar:

```bash
mvn validate
```

Si pasa:

```bash
mvn compile
```

---

# 6. Separar baseline repair de migration transform

No transformar Camel 2 a Camel 4 durante la reparación del baseline.

## Baseline repair

Objetivo:

```text
Hacer que el proyecto legacy sea válido y evaluable.
```

Ejemplo:

```text
camel-http4
+
Camel 2.24.3
```

Debe permanecer así durante baseline.

## Migration transform

Después del baseline válido:

```text
camel-http4
    ↓
camel-http
```

Y se aplican las transformaciones de Camel 4/Spring Boot/Quarkus.

---

# 7. Verificación obligatoria de candidate

Antes de ejecutar recipes:

```bash
mvn validate
```

El pipeline debe registrar:

```text
candidate/maven-model: PASS
```

Si falla:

```text
candidate/maven-model: FAIL
```

Y detener recipes obligatorias.

No se debe continuar con:

```text
jakarta-jms
javax-to-jakarta
camel2-to-4
jboss-to-spring
```

si Maven no puede construir el modelo del proyecto.

---

# 8. Corrección del motor de estados

El estado final nunca debe asignarse de manera fija como `succeeded` después de generar el reporte.

Debe calcularse a partir de los resultados de todas las fases.

## Estados permitidos

```text
SUCCEEDED
PARTIAL_SUCCESS
FAILED
BLOCKED
```

---

# 9. Reglas de agregación

## SUCCEEDED

Solo cuando:

```text
candidate Maven model = PASS
all mandatory recipes = PASS
candidate compile = PASS
required tests = PASS
no blocking security findings
```

## PARTIAL_SUCCESS

Cuando:

```text
candidate compile = PASS
core migration = PASS
pero existen warnings o tareas manuales no bloqueantes
```

Ejemplos:

```text
SOAP endpoint requires manual validation
SFTP credentials externalized but not configured
optional recipe skipped
```

## FAILED

Cuando ocurra cualquiera de estos casos:

```text
mandatory recipe = FAIL
candidate Maven model = FAIL
candidate compile = FAIL
required tests = FAIL
```

## BLOCKED

Cuando no es posible continuar de forma segura:

```text
Camel version cannot be inferred
POM cannot be repaired
credentials are required but unavailable
unsupported build system
required migration metadata missing
```

---

# 10. Ejemplo de agregador de estado

```python
def calculate_job_status(result):
    if result.blocking_error:
        return "BLOCKED"

    if not result.candidate_maven_model_ok:
        return "FAILED"

    if result.mandatory_recipe_failures > 0:
        return "FAILED"

    if not result.candidate_compile_ok:
        return "FAILED"

    if result.required_tests_failed:
        return "FAILED"

    if result.manual_actions > 0 or result.non_blocking_warnings > 0:
        return "PARTIAL_SUCCESS"

    return "SUCCEEDED"
```

---

# 11. Los tests omitidos no cuentan como éxito

Actualmente aparece:

```text
candidate/test: SKIPPED
```

Si los tests se omiten porque compile falló:

```text
compile = FAIL

test = SKIPPED_DUE_TO_COMPILE_FAILURE

job = FAILED
```

No interpretar `SKIPPED` como `PASS`.

Estados sugeridos para fases:

```text
PASS
FAIL
SKIPPED
BLOCKED
WARN
ERROR
```

Con reason code obligatorio:

```json
{
  "phase": "candidate-test",
  "status": "SKIPPED",
  "reason": "CANDIDATE_COMPILE_FAILED"
}
```

---

# 12. Corrección de secret-scan

No usar un único `FAIL` para todos los escenarios.

Distinguir:

```text
PASS
FINDINGS
ERROR
BLOCKED
```

## FINDINGS

La herramienta funcionó y encontró secretos.

Ejemplo:

```text
candidate/secret-scan: FINDINGS
```

Se debe intentar remediación:

```properties
ftp.password=Admin123
```

pasa a:

```properties
ftp.password=${FTP_PASSWORD}
```

Luego volver a escanear.

## ERROR

La herramienta de secret scan no pudo ejecutarse.

Ejemplo:

```text
candidate/secret-scan: ERROR
reason: SCANNER_EXECUTION_FAILED
```

---

# 13. Pipeline actualizado

```text
PHASE 1 - source
    ↓
PHASE 2 - environment-check
    ↓
PHASE 3 - baseline-copy
    ↓
PHASE 4 - maven-model-validation
    ↓
PHASE 5 - baseline-pom-repair
    ↓
PHASE 6 - baseline-maven-revalidation
    ↓
PHASE 7 - baseline-compile
    ↓
PHASE 8 - candidate-create-from-repaired-baseline
    ↓
PHASE 9 - candidate-maven-model-validation
    ↓
PHASE 10 - recipes
    ↓
PHASE 11 - secret-remediation
    ↓
PHASE 12 - secret-scan
    ↓
PHASE 13 - candidate-compile
    ↓
PHASE 14 - candidate-test
    ↓
PHASE 15 - report
    ↓
PHASE 16 - calculate-final-status
```

---

# 14. Orden de recipes recomendado

Solo después de candidate Maven model = PASS.

```text
1. java-version
2. javax-to-jakarta base
3. jakarta-jms
4. camel dependencies
5. camel routes
6. jboss-specific config
7. Spring Boot / Quarkus adaptation
8. configuration externalization
9. cleanup
```

Cada recipe debe declarar:

```yaml
id: jakarta-jms
mandatory: true
requires:
  - valid_maven_model
  - java_source_available
rollback_on_failure: true
```

---

# 15. Reporte final correcto para el caso observado

El log actual debería producir algo parecido a:

```text
Migration status: FAILED

Primary reason:
CANDIDATE_MAVEN_MODEL_INVALID

Detected issues:
- org.apache.camel:camel-cdi has no version
- org.apache.camel:camel-http4 has no version
- org.apache.camel:camel-cxf has no version

Recipe status:
- jakarta-jms: NOT_EXECUTABLE / FAILED_DUE_TO_INVALID_MAVEN_MODEL

Security:
- secret scan: FINDINGS or ERROR

Build:
- candidate compile: FAILED
- candidate test: SKIPPED_DUE_TO_COMPILE_FAILURE

Final status:
FAILED
```

Nunca:

```text
Trabajo succeeded
```

---

# 16. Validaciones automáticas del pipeline

Agregar tests del propio migrador.

## Test 1

Dado un POM con dependencias Camel sin versión:

```text
THEN baseline repair agrega BOM
AND mvn validate pasa
```

## Test 2

Dado un candidate Maven model inválido:

```text
THEN ninguna recipe obligatoria se ejecuta
AND status = FAILED
```

## Test 3

Dado compile FAIL:

```text
THEN tests = SKIPPED_DUE_TO_COMPILE_FAILURE
AND final status = FAILED
```

## Test 4

Dado mandatory recipe FAIL:

```text
THEN candidate puede conservar rollback
BUT final status = FAILED
```

## Test 5

Dado secret findings pero build exitoso:

```text
THEN status depende de security policy
AND nunca confundir findings con scanner error
```

---

# 17. Criterios de aceptación

La corrección se considera terminada cuando:

- [ ] `candidate` siempre se genera desde el baseline reparado.
- [ ] `candidate/pom.xml` se valida antes de recipes.
- [ ] no se ejecutan recipes Maven-dependent con modelo inválido.
- [ ] Camel dependencies sin versión pueden repararse automáticamente cuando existe evidencia suficiente.
- [ ] `camel-http4` no se transforma durante baseline repair.
- [ ] `candidate compile = FAIL` produce job `FAILED`.
- [ ] mandatory recipe `FAIL` produce job `FAILED`.
- [ ] `test SKIPPED` por compile fallido no se considera éxito.
- [ ] secret scanner diferencia `FINDINGS` de `ERROR`.
- [ ] el reporte final contiene primary reason y reason codes.
- [ ] `succeeded` solo se emite si se cumplen todos los gates obligatorios.

---

# 18. Resultado esperado después de implementar esta corrección

Para el proyecto demo:

```text
baseline POM repair
    ↓
Camel 2.24.3 BOM inserted
    ↓
mvn validate PASS
    ↓
mvn compile PASS o error real de código legacy
    ↓
candidate created
    ↓
candidate Maven model PASS
    ↓
recipes execute
    ↓
Camel/Jakarta migration
    ↓
candidate compile
    ↓
tests
    ↓
real final status
```

El objetivo es que el sistema deje de reportar falsos positivos de migración exitosa y que las recipes trabajen siempre sobre un proyecto Maven válido.
