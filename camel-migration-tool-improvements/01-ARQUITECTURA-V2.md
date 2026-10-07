# Arquitectura V2

## Problema actual

El proyecto mezcla análisis, reparación, recipes, compilación y reportes dentro del mismo flujo. Esto genera acoplamiento y hace difícil distinguir errores de entorno, errores preexistentes y errores de migración.

## Arquitectura propuesta

```text
Git / ZIP / File System
        |
        v
   Source Ingestor
        |
        v
 Snapshot Manager
        |
        +--> source
        +--> baseline
        +--> candidate
        |
        v
 Analysis Engine
        |
        v
 Migration Planner
        |
        v
 Rule Engine
        |
        v
 Candidate Generator
        |
        v
 Build & Test Engine
        |
        v
 Autofix Engine
        |
        v
 Report Engine
```

## Módulos

### Source Ingestor
Responsable de clonar o importar proyectos. Nunca modifica el origen.

### Snapshot Manager
Crea snapshots inmutables:
- source
- baseline
- candidate
- candidate-N para reintentos

### Analysis Engine
Analiza Maven, Java, Camel, JBoss, Spring, JMS, REST, SOAP, SQL, SFTP, Quartz, secretos y configuración.

### Migration Planner
Convierte hallazgos en acciones ordenadas y dependientes.

### Rule Engine
Aplica reglas declarativas y transformaciones AST.

### Build & Test Engine
Ejecuta validación Maven, compile, test y quality gates.

### Autofix Engine
Clasifica errores conocidos y aplica correcciones iterativas.

### Report Engine
Genera JSON y Markdown con trazabilidad completa.

## Principios

- El proyecto fuente es inmutable.
- Cada cambio debe ser auditable.
- Toda transformación debe ser reversible.
- Ninguna recipe se ejecuta si el modelo Maven es inválido.
- Los estados del job deben reflejar el estado real del candidate.
