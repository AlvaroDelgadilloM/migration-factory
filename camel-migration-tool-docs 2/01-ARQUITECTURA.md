# Arquitectura del Camel Migration Tool

## Componentes

### 1. CLI
Responsable de recibir parámetros y ejecutar el pipeline.

### 2. Pre-Flight Validator
Valida el entorno y el build del proyecto legacy antes de cualquier migración:

- Java/Maven disponibles
- arquitectura y workspace
- directorio temporal usable
- manejo de Jansi cuando `/tmp` está montado con `noexec`
- modelo Maven válido
- baseline build
- clasificación de errores de origen

### 3. Project Scanner
Recorre el repositorio y clasifica archivos relevantes:

- pom.xml
- *.java
- *.xml
- *.properties
- *.yml / *.yaml
- web.xml
- jboss-web.xml
- beans.xml
- persistence.xml

### 4. Technology Detector
Detecta:

- versión de Java
- versión de Camel
- servidor de aplicaciones
- tipo de empaquetado
- framework CDI/Spring
- componentes Camel
- integraciones externas

### 5. Integration Inventory
Genera inventario de:

- rutas Camel
- REST
- SOAP
- JMS
- JNDI
- SQL
- SFTP/FTP
- Quartz
- SMTP
- endpoints externos
- secretos/configuración sensible

### 6. Migration Rule Engine
Evalúa reglas declarativas YAML/JSON y determina:

- cambio automático
- cambio sugerido
- cambio manual
- incompatibilidad crítica

### 7. Target Project Generator
Genera un proyecto Spring Boot limpio con:

- pom.xml nuevo
- Application.java
- application.yml
- estructura packages
- configuración base
- Dockerfile opcional

### 8. Code Transformer
Transforma rutas, imports, componentes y configuración legacy.

### 9. Validator
Ejecuta:

```bash
mvn clean test
mvn verify
```

### 10. Auto-fix Engine
Interpreta errores conocidos y aplica reglas de corrección de bajo riesgo.

### 11. Report Generator
Produce resultados auditables y trazables.

## Estructura sugerida

```text
camel-migration-tool/
├── migrate.py
├── requirements.txt
├── preflight/
│   ├── environment_checker.py
│   ├── pom_validator.py
│   ├── baseline_repair.py
│   └── baseline_builder.py
├── analyzer/
│   ├── project_scanner.py
│   ├── maven_analyzer.py
│   ├── java_analyzer.py
│   ├── camel_analyzer.py
│   ├── jboss_analyzer.py
│   └── integration_analyzer.py
├── model/
│   ├── project_model.py
│   ├── route_model.py
│   ├── dependency_model.py
│   └── finding_model.py
├── migration/
│   ├── migration_engine.py
│   ├── dependency_migrator.py
│   ├── java_transformer.py
│   ├── camel_transformer.py
│   ├── config_transformer.py
│   └── secret_externalizer.py
├── generators/
│   ├── springboot_generator.py
│   └── test_generator.py
├── validators/
│   ├── maven_builder.py
│   ├── test_runner.py
│   ├── compile_error_parser.py
│   └── error_classifier.py
├── rules/
│   ├── camel2-to-camel4.yml
│   ├── jboss-to-springboot.yml
│   ├── javax-to-spring.yml
│   └── security.yml
└── reports/
    └── report_generator.py
```
