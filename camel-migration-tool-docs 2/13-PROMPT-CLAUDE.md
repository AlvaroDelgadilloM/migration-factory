# Prompt maestro para implementar el Camel Migration Tool

Construye una aplicación Python llamada `camel-migration-tool` cuyo objetivo sea analizar aplicaciones Java legacy desplegadas en JBoss/WildFly con Apache Camel 2.x y generar una nueva aplicación migrada a Java 21 + Spring Boot 3 + Apache Camel 4.

## Reglas obligatorias

- Nunca modificar el proyecto fuente.
- Ejecutar un PRE-FLIGHT antes de migrar.
- Separar errores de ambiente, baseline y target.
- Reparar problemas Maven de bajo riesgo antes de generar el target.
- Crear siempre un directorio de salida nuevo.
- Separar análisis, reglas, transformación, generación y validación.
- Usar clases pequeñas y principios SOLID.
- El conocimiento de migración debe vivir preferentemente en reglas YAML.
- No realizar reemplazos globales inseguros de texto.
- Utilizar AST/parsers cuando sea posible.
- Toda transformación debe registrar evidencia, archivo, regla y confidence.
- Los cambios de confidence bajo deben marcarse como manuales.

## Primera versión
Soportar:

- Maven
- Java 8/11
- Camel 2.x
- JBoss/WildFly
- CDI/javax
- REST
- SOAP/CXF
- JMS/JNDI
- JDBC/SQL
- FTP/SFTP
- Quartz
- SMTP

Destino:

- Java 21
- Spring Boot 3
- Camel 4
- Maven
- executable JAR

## CLI requerida

```bash
python migrate.py analyze <project>
python migrate.py plan <project> --target springboot
python migrate.py migrate <project> --target springboot --output <path>
python migrate.py validate <migrated-project>
```

## Output

```text
output/
├── migrated-project/
├── reports/
│   ├── analysis.json
│   ├── baseline-report.md
│   ├── migration-plan.md
│   ├── migration-report.md
│   └── manual-actions.md
└── logs/
```

## Pre-Flight y baseline
Antes de migrar:

1. validar Java/Maven y entorno
2. detectar `/tmp noexec` y configurar `jansi.tmpdir` dentro del workspace
3. validar el `pom.xml`
4. detectar dependencias sin versión
5. si Camel 2.x es identificable, reparar con Camel BOM o `${camel.version}` cuando el confidence sea alto
6. ejecutar un baseline build
7. registrar los errores que ya existían en el legacy

Nunca transformar `camel-http4` a `camel-http` durante el baseline repair; esa transformación pertenece a la migración al target.

## Validación
Después de generar el proyecto ejecutar Maven. Analizar errores conocidos y realizar autocorrección con máximo 5 rondas.

## Testing
Crear unit tests para analyzer, rules y transformers. Crear al menos smoke tests del proyecto generado.

## Entrega
Generar el proyecto completo, README, requirements, tests, reglas iniciales y una demo de uso contra un proyecto Camel 2/JBoss.

No simules resultados de compilación. Si una transformación no puede resolverse de forma segura, genera una acción manual detallada.
