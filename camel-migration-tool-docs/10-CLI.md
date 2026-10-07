# CLI

## Analizar solamente

```bash
python migrate.py analyze ./legacy-app
```

## Crear plan

```bash
python migrate.py plan ./legacy-app --target springboot
```

## Migrar

```bash
python migrate.py migrate ./legacy-app \
  --target springboot \
  --java 21 \
  --output ./migration-output
```

## Validar proyecto generado

```bash
python migrate.py validate ./migration-output/migrated-project
```

## Opciones

```text
--dry-run
--no-autofix
--max-fix-rounds 5
--fail-on-critical
--report-format md,json
--verbose
```

## Exit codes

- 0: success
- 1: analysis/migration error
- 2: build failed
- 3: critical manual action
- 4: invalid input
