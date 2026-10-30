#!/usr/bin/env bash
# Business flows of the ported example modules on a plain official Odoo 19 (not the platform's runners): database with demo data,
# the example's external SQL, the modules installed with their own tests, then the flows with `odoo shell`. Fails if any fails.
#   run.sh <ported addons dir> <modules csv> <flow script> [sql files…]     (needs a PostgreSQL reachable as $PGHOST, user odoo)
set -uo pipefail
addons=$(cd "$1" && pwd); mods=$2; flow=$3; shift 3
here=$(cd "$(dirname "$0")" && pwd); db="flows_$(date +%s)"
PGPASSWORD=$PGPASS psql -h "$PGHOST" -U odoo -d postgres -q -c "create database $db" || exit 1
for f in "$@"; do grep -viE '^\s*(create|drop)\s+database' "$f" | grep -v '^\s*\\' | PGPASSWORD=$PGPASS psql -h "$PGHOST" -U odoo -d "$db" -q 2>&1 | grep -v NOTICE | head -5; done
odoo() { docker run -i --rm --network host -v "$addons":/mnt/extra-addons:ro -v "$here":/flows:ro \
           -e HOST="$PGHOST" -e USER=odoo -e PASSWORD="$PGPASS" odoo:19.0 "$@"; }
odoo odoo -d "$db" -i "$mods" --with-demo --test-enable --test-tags "$(echo "$mods" | sed 's/[^,]*/\/&/g')" --stop-after-init 2>&1 \
  | grep -E " CRITICAL |failed, .* error\(s\) of" | tee /tmp/flows-install.log
grep -qE " CRITICAL |[1-9][0-9]* failed|[1-9][0-9]* error\(s\)" /tmp/flows-install.log && exit 1
{ cat "$here/../../../mf/odoo/flow_helpers.py"; echo "MODULES = '$mods'.split(',')"; cat "$here/$flow"; echo; echo "done()"; } | odoo odoo shell -d "$db" --no-http 2>&1 | grep '^MF_FLOW ' | sed 's/^MF_FLOW //' > /tmp/flows-result.json
python3 - <<'PY'
import json, sys
r = json.load(open('/tmp/flows-result.json'))
for x in r:
    print(('OK   ' if x['ok'] else 'FALLA'), x['check'], '·', x.get('got') or x.get('error'))
print(f"{sum(x['ok'] for x in r)}/{len(r)} flujos bien")
sys.exit(0 if r and all(x['ok'] for x in r) else 1)
PY
