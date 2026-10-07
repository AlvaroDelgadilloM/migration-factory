# Plugin Architecture

## Objetivo

Permitir soportar nuevos stacks sin modificar el core.

## Plugins

### Analyzer Plugin
Ejemplos:
- JBossAnalyzer
- WebLogicAnalyzer
- TomcatAnalyzer

### Migration Plugin
- Camel2To4
- Java8To21
- JavaxToJakarta

### Target Generator
- SpringBootTarget
- QuarkusTarget

### Build Adapter
- Maven
- Gradle

## Contrato

Cada plugin debe declarar:
- id
- version
- capabilities
- supportedSources
- supportedTargets
- dependencies
