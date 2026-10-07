# Modelo de datos
Todas las tablas de negocio llevan UUID, created_at y tenant_id/organization_id si se habilita multiempresa. Autorización por pertenencia al proyecto, no solo por conocer UUID.

| Tabla | Campos principales |
|---|---|
| organization | id, name |
| project | id, organization_id, name, repository_id, default_branch |
| repository | id, provider, url, credential_ref |
| membership | project_id, subject_id, role |
| target_profile | id, runtime, java_version, camel_version, runtime_version, bom_coordinates, status, digest |
| rule_catalog | id, version, digest, source_uri |
| scan | id, project_id, commit_sha, catalog_id, status, started_at, finished_at |
| module | id, scan_id, path, packaging, coordinates, resolved_java |
| route | id, module_id, route_key, dsl, file, line, detection_confidence |
| endpoint | id, route_id, component, redacted_uri, dynamic |
| finding | id, scan_id, rule_id, file, line, severity, classification, status |
| finding_review | id, finding_id, reviewer, decision, reason, evidence_artifact_id |
| plan | id, scan_id, profile_id, version, status, digest |
| plan_step | id, plan_id, ordinal, recipe_id, parameters_json, depends_on |
| execution | id, plan_id, state, candidate_sha, lease_owner, lease_until |
| validation | id, execution_id, suite, status, exit_code, artifact_id |
| artifact | id, execution_id, kind, object_key, sha256, size_bytes |
| audit_event | id, actor, action, entity_type, entity_id, before_hash, after_hash, at |
| outbox | id, event_type, payload_json, published_at |

Índices: scan(project_id, created_at), finding(scan_id,status,severity), execution(state,lease_until), unique plan(scan_id,version), unique artifact(object_key). Catálogo/perfil inmutables cuando ya fueron usados. Tokens y passwords nunca en parámetros JSON; solo referencias a vault.

Retención configurable y borrado por organización con auditoría. Eliminar checkout tras generar artefactos; conservar SHA y hashes. Migraciones DB mediante Alembic.
