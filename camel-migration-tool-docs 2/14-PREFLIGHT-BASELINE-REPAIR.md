# Pre-Flight y Baseline Repair

## Objetivo
Antes de iniciar cualquier migración, validar que el proyecto fuente puede ser entendido y que su configuración de build es coherente. El migrador NO debe confundir errores existentes del proyecto con errores introducidos por la migración.

## Flujo obligatorio

```text
SOURCE PROJECT
    ↓
ENVIRONMENT CHECK
    ↓
POM / BUILD MODEL VALIDATION
    ↓
BASELINE REPAIR
    ↓
BASELINE BUILD
    ↓
ANALYSIS
    ↓
MIGRATION PLAN
    ↓
TARGET GENERATION
    ↓
MIGRATION
    ↓
TARGET BUILD / TEST
```

## 1. Environment Check

Validar al menos:

- Java instalado y versión efectiva
- Maven instalado y versión efectiva
- arquitectura del sistema (`amd64`, `arm64`, etc.)
- directorio temporal escribible
- directorio temporal ejecutable cuando Maven/Jansi lo requiera
- acceso al repositorio Maven configurado
- variables relevantes (`JAVA_HOME`, `MAVEN_OPTS`, proxies)

### Jansi y `/tmp noexec`

Un error como:

```text
Failed to load native library: jansi-*.so
/tmp/... is not executable
failed to map segment from shared object
```

debe clasificarse como `ENVIRONMENT`, no como error de migración.

El builder debe crear un tmp alternativo dentro del workspace, por ejemplo:

```text
<workspace>/.migration-tmp/jansi
```

y ejecutar Maven con:

```text
-Djansi.tmpdir=<workspace>/.migration-tmp/jansi
-Djansi.force=false
```

Ejemplo conceptual:

```python
env["MAVEN_OPTS"] = (
    f"-Djansi.tmpdir={jansi_tmp} "
    "-Djansi.force=false "
    + env.get("MAVEN_OPTS", "")
)
```

No se debe marcar la migración como fallida únicamente por un warning de Jansi si Maven puede continuar.

## 2. Validación del modelo Maven

Antes de ejecutar `compile`, parsear el `pom.xml` y validar:

- dependencias sin versión
- versiones resueltas por `dependencyManagement`
- BOMs importados
- propiedades inexistentes
- plugins sin versión cuando sea relevante
- parent resolvible
- módulos existentes
- packaging
- Java source/target/release

## 3. Reparación de dependencias Camel 2

Caso típico detectado:

```xml
<dependency>
    <groupId>org.apache.camel</groupId>
    <artifactId>camel-cdi</artifactId>
</dependency>
```

si no existe un BOM o `dependencyManagement`, Maven falla con:

```text
'dependencies.dependency.version' ... is missing
```

### Estrategia preferida

Si el proyecto usa una versión Camel identificable, por ejemplo `2.24.3`, agregar o reparar `dependencyManagement` con Camel BOM:

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

Esto puede resolver dependencias como:

- `org.apache.camel:camel-cdi`
- `org.apache.camel:camel-http4`
- `org.apache.camel:camel-cxf`

### Estrategia alternativa

Si no conviene modificar `dependencyManagement`, asignar explícitamente:

```xml
<version>${camel.version}</version>
```

a cada dependencia Camel afectada.

## 4. Regla de seguridad

El migrador debe diferenciar:

```text
LEGACY REPAIR
camel-http4:2.24.3
```

de:

```text
TARGET MIGRATION
camel-http4 -> camel-http
```

No reemplazar componentes Camel 2 por Camel 4 durante el baseline repair. El objetivo del baseline repair es conseguir una referencia válida del proyecto de origen.

## 5. Baseline Build

Después de reparar problemas estructurales de bajo riesgo, ejecutar:

```bash
mvn -B -e -Dstyle.color=never clean test
```

Si el proyecto no tiene tests, al menos:

```bash
mvn -B -e -Dstyle.color=never clean compile
```

Guardar:

- comando
- exit code
- stdout
- stderr
- duración
- errores parseados

## 6. Clasificación de errores

Todo error debe pertenecer a una categoría:

- `ENVIRONMENT`
- `BUILD_MODEL`
- `DEPENDENCY`
- `SOURCE`
- `MIGRATION`
- `TEST`
- `SECURITY`

Ejemplos:

```text
ENVIRONMENT
/tmp mounted noexec; Jansi cannot load native library
```

```text
BUILD_MODEL
Camel dependency has no version and no applicable BOM
```

```text
MIGRATION
camel-http4 no longer exists in the target Camel version
```

## 7. Estados de baseline

El proyecto debe quedar en uno de estos estados:

- `BASELINE_SUCCESS`: compila o pasa tests
- `BASELINE_REPAIRED`: requirió reparaciones automáticas y luego compila
- `BASELINE_FAILED_KNOWN`: falla por problema conocido/documentado del legacy
- `BASELINE_BLOCKED`: no es posible continuar de forma segura

Un proyecto puede continuar a migración desde `BASELINE_FAILED_KNOWN` solamente si el usuario lo permite o si las reglas definen que los fallos son ajenos al área a migrar.

## 8. Ejemplo de finding

```json
{
  "id": "POM-001",
  "category": "BUILD_MODEL",
  "severity": "HIGH",
  "confidence": 0.99,
  "artifact": "org.apache.camel:camel-cdi",
  "message": "Dependency has no version and is not covered by dependencyManagement",
  "detectedCamelVersion": "2.24.3",
  "autoFix": true,
  "fix": "Import org.apache.camel:camel-bom:2.24.3"
}
```

## 9. Criterio de éxito

El pre-flight termina cuando:

- el ambiente está usable
- el POM puede ser leído por Maven
- los errores de baseline están clasificados
- las reparaciones automáticas aplicadas están registradas
- existe evidencia del build de referencia

Solo después debe comenzar la migración a Java 21 + Spring Boot 3 + Camel 4.
