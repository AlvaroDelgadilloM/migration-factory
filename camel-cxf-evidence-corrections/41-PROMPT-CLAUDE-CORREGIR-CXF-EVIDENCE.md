# Prompt para Claude: corregir CXF y evidence model

Implementa estas correcciones en el migrador Java/Camel existente:

1. Eliminar `camel-cxf -> camel-cxf-soap` como mapping global.
2. Crear `CxfUsageAnalyzer`.
3. Separar `ProjectEvidence` y `DependencyEvidence`.
4. Agregar evidencia con `type`, `value`, `file`, `line`, `source`.
5. Separar `artifactConfidence`, `usageConfidence`, `migrationConfidence`.
6. Mantener `camel-blueprint` como `ARCHITECTURAL_MIGRATION`, pero reportar Spring Boot 3 / Quarkus 3 / Camel Main como runtimes objetivo.
7. Cambiar el formato del reporte.
8. Agregar tests de SOAP/REST/Blueprint/Spring XML/evidence isolation/ambiguous CXF.

Reglas CXF:
```text
SOAP + Spring XML -> camel-cxf-spring-soap
SOAP -> camel-cxf-soap
REST + Spring XML -> camel-cxf-spring-rest
REST -> camel-cxf-rest
```

Si Blueprint está presente, no marcar migración totalmente automática.

No romper:
- waves;
- baseline degraded mode;
- artifact availability gate;
- Camel version consistency gate;
- POM normalization;
- OpenRewrite execution.
