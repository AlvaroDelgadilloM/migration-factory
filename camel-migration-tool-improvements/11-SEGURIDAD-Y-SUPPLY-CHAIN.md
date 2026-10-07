# Seguridad y supply chain

## Riesgos

- ejecución de Maven plugins maliciosos
- scripts de build
- dependencias comprometidas
- secretos en repositorios
- URLs internas

## Controles

- sandbox
- allowlist de repositorios Maven
- no ejecutar scripts arbitrarios sin política
- SBOM
- dependency vulnerability scan
- secret scan
- hash de artefactos

## Secret scan

Distinguir:
- FINDINGS
- TOOL_ERROR
- BLOCKED

Los secretos detectados deben externalizarse cuando sea seguro.
