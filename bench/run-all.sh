#!/usr/bin/env bash
# One command for the whole benchmark: build, contract-check, load-test every backend, write the report.
#   ./bench/run-all.sh                 # all six languages, all scenarios (~2 h)
#   ./bench/run-all.sh go rust         # only these
#   SCENARIOS="light spike" ./bench/run-all.sh cpp
#   SCENARIOS="" ./bench/run-all.sh   # build + contract test only
# Close other heavy apps while it runs: everything shares one machine.
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1 # Git Bash on Windows: don't rewrite /scripts/... container paths

ALL_LANGS="go rust dotnet node python cpp"
LANGS=${*:-$ALL_LANGS}
SCENARIOS=${SCENARIOS-"light medium heavy spike ep_users ep_orders_include ep_post_orders"}
COOLDOWN=${COOLDOWN:-30}

log() { printf '\n\033[1;36m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }

stop_backends() { docker compose stop $ALL_LANGS >/dev/null 2>&1 || true; }

start_fresh() { # reset DB to the seeded template, then start one backend and wait for /health
  stop_backends
  ./bench/reset-db.sh
  docker compose up -d "$1" >/dev/null
  for _ in $(seq 1 60); do
    docker compose exec -T postgres wget -qO- "http://$1:8080/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "$1 did not become healthy"; docker compose logs --tail 30 "$1"; return 1
}

k6() { docker compose run --rm -q k6 run -q -e BASE="http://$1:8080" -e SCENARIO="$2" ${3:+-e OUT="$3"} /scripts/bench.js; }

log "Building images: $LANGS"
docker compose build $LANGS
docker compose build tools >/dev/null
docker compose up -d --wait postgres
mkdir -p results/contract

log "Recording machine info"
{
  printf '{\n  "date": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '  "cpu": "%s",\n' "$(docker run --rm alpine sh -c "grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs")"
  printf '  "docker": "%s",\n' "$(docker info --format '{{.ServerVersion}} on {{.OperatingSystem}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes RAM')"
  printf '  "host": "%s"\n}\n' "$(uname -srm)"
} > results/meta.json
cat results/meta.json

for lang in $LANGS; do
  log "$lang: contract test"
  start_fresh "$lang"
  if ! docker compose run --rm -q tools python bench/contract.py "http://$lang:8080" "results/contract/$lang.json" bench/contract-reference.json; then
    echo "!! $lang fails the API contract, skipping its benchmark"; continue
  fi

  for s in $SCENARIOS; do
    out="results/$lang/$s"
    mkdir -p "$out"
    log "$lang / $s"
    start_fresh "$lang"
    k6 "$lang" warmup >/dev/null
    ./bench/sampler.sh "$out" "$(docker compose ps -q "$lang")" "$(docker compose ps -q postgres)" &
    sampler=$!
    k6 "$lang" "$s" "/$out/summary.json" || echo "!! k6 exited non-zero for $lang/$s"
    kill "$sampler"; wait "$sampler" 2>/dev/null || true
    sleep "$COOLDOWN"
  done
done
stop_backends

log "Generating charts and README tables"
docker compose run --rm -q tools python report/generate.py
log "Done. See README.md and docs/charts/"
