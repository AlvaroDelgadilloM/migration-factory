#!/usr/bin/env bash
# Tag of each Odoo hop runner image: "<version> <version>-<OpenUpgrade commit>-<openupgradelib commit>-<image recipe hash>".
# The recipe hash (Dockerfile + runner) makes a change to either rebuild the images instead of reusing a published tag.
set -euo pipefail
cd "$(dirname "$0")/.."
l=$(jq -r .openupgradelibCommit rules/odoo/hops.json)
h=$(cat docker/odoo-hop.Dockerfile docker/odoo-hop/runner.py | sha256sum | cut -c1-8)
jq -r '.hops[] | "\(.version) \(.openupgrade)"' rules/odoo/hops.json | while read -r v c; do
  echo "$v $v-${c:0:8}-${l:0:8}-$h"
done
