# Camel Migration Tool

## Objetivo
Construir una herramienta capaz de analizar aplicaciones legacy Java/JBoss/Apache Camel 2 y generar una nueva versión migrada a Java 21 + Apache Camel 4 + Spring Boot 3, sin modificar el proyecto fuente.

## Flujo principal

```text
Proyecto Legacy
    ↓
Scanner
    ↓
Detector de tecnologías
    ↓
Inventario técnico
    ↓
Motor de reglas
    ↓
Plan de migración
    ↓
Generador de proyecto destino
    ↓
Transformadores de código/configuración
    ↓
Compilación y pruebas
    ↓
Autocorrección conocida
    ↓
Reporte final
    ↓
Proyecto migrado
```

## MVP
Primera versión soportada:

- Java 8/11
- Maven
- JBoss/WildFly
- WAR
- Apache Camel 2.x
- CDI / javax
- JMS
- REST
- SOAP/CXF
- SQL/JDBC/JNDI
- SFTP/FTP
- Quartz
- Properties/XML

Destino inicial:

- Java 21
- Spring Boot 3
- Apache Camel 4
- Maven
- JAR ejecutable

## Principio fundamental
El proyecto original nunca se modifica. Todo cambio se genera en un directorio de salida nuevo.

## Ejemplo

```bash
python migrate.py \
  --source ./legacy-app \
  --target springboot \
  --output ./migration-output
```

Resultado:

```text
migration-output/
├── migrated-project/
├── reports/
│   ├── analysis.json
│   ├── migration-plan.md
│   ├── migration-report.md
│   └── manual-actions.md
└── logs/
```


## Actualización: Pre-Flight
Se agregó `14-PREFLIGHT-BASELINE-REPAIR.md` para cubrir validación del ambiente, reparación del POM legacy, Camel BOM, baseline build y manejo de Jansi cuando `/tmp` está montado con `noexec`.
