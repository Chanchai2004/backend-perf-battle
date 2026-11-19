#!/usr/bin/env bash
# Replace the bench DB with a fresh copy of the seeded template (~1-2 s).
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T postgres psql -q -U bench -d postgres \
  -c "DROP DATABASE IF EXISTS bench WITH (FORCE)" \
  -c "CREATE DATABASE bench TEMPLATE bench_template"
docker compose exec -T postgres psql -q -U bench -d bench -c "VACUUM ANALYZE" >/dev/null
