# Integración OpenRewrite
## Proceso
1. Verificar SHA, checkout limpio y perfil aprobado.
2. Capturar baseline build/tests en sandbox, incluyendo fallas previas.
3. Resolver POM efectivo y guardar digest; no exponer settings con credenciales.
4. Elegir recetas por transición exacta; fijar versión de plugin y artifact de recetas.
5. Ejecutar dryRun, registrar diff y revisar precondiciones.
6. Aplicar en copia aislada; guardar hash de cada receta y parámetros.
7. Compilar/probar y producir evidencia de equivalencia.

La herramienta oficial Camel Upgrade Recipes ayuda con determinados upgrades. Su cobertura no equivale a migración integral de JBoss/Camel 2. Revisar guías 2→3, 3→4 y upgrades intermedios relevantes.

## Runner a implementar
`run_recipe(workdir, recipe_artifact, recipe_names, plugin_version, timeout)` acepta únicamente coordenadas validadas. Usar `subprocess` con lista de argumentos y `shell=False`. Guardar exit code, duración y artefactos. Cancelación mata grupo de procesos. No usar LATEST/RELEASE en runs reproducibles.

Plantilla conceptual (sustituir variables por versiones verificadas):
```bash
mvn org.openrewrite.maven:rewrite-maven-plugin:VERSION_FIJADA:dryRun   -Drewrite.recipeArtifactCoordinates=COORDENADAS_FIJADAS   -Drewrite.activeRecipes=RECETAS_VERIFICADAS
```
No se incluye un runner ejecutable para evitar aparentar una compatibilidad sin seleccionar versiones reales del cliente.

## Target Spring
BOM Camel Spring Boot y Spring Boot compatibles; starters solo para componentes usados; entrada Application y configuración de beans/DS externa. Servidor HTTP embebido solo si la aplicación lo necesita.

## Target Quarkus
Usar BOM plataforma Quarkus/Camel Quarkus compatible; extensiones para cada componente; revisar CDI, reflexión y loaders XML. Primera validación en JVM. Native image es una fase adicional, nunca un gate asumido de migración.

## Conversiones propias
XML DSL → Java DSL solo para subconjunto con equivalencia probada. EIP desconocido detiene conversión del bloque y crea hallazgo; no omitir nodos. No transformar endpoints/JTA por regex. Rollback descarta el checkout candidato y preserva origen y artefactos.


## Estado
Runner implementado en `backend/mf/worker/mavenops.py` y `tasks.py` con versiones fijadas por perfil (ver `docs/15_IMPLEMENTACION.md`). La frase anterior «No se incluye un runner ejecutable» ya no aplica. Validado con el piloto sintético para Spring y Quarkus.
