# Prompt maestro para Claude — mejora del migrador

Quiero que mejores el proyecto Camel Migration Tool existente siguiendo estos documentos como especificación obligatoria.

## Objetivo

Convertir la herramienta actual en una plataforma robusta de análisis y modernización Java legacy.

## Prioridades

1. No modificar nunca el source original.
2. Implementar snapshots `source`, `baseline` y `candidate`.
3. Validar y reparar Maven antes de ejecutar recipes.
4. Separar errores de environment, baseline, migration, security y test.
5. Nunca marcar `SUCCEEDED` si el candidate no compila.
6. Implementar Rule Engine declarativo.
7. Añadir confidence scoring.
8. Añadir rollback por recipe.
9. Ejecutar candidate en sandbox.
10. Generar reportes trazables.

## Primera meta funcional

Soportar completamente:

```text
Java 8 + JBoss/WildFly + Camel 2 + Maven
        ->
Java 21 + Spring Boot 3 + Camel 4 + Maven
```

## Entregables

- código funcional
- tests
- fixtures
- documentación
- CLI
- API interna
- reportes

## Regla de implementación

No hagas reemplazos de texto simples cuando exista una alternativa AST o estructural.

Cada transformación debe registrar:
- regla
- archivo
- before
- after
- confidence
- rollback

Cada bug encontrado debe convertirse en una prueba de regresión.
