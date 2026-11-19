#!/usr/bin/env bash
# Samples resource usage until killed.
#   stats.csv: ts,container,cpu_pct,mem_usage   (docker stats; cpu 100 = one full core)
#   db.csv:    ts,active,idle,total            (client connections to the bench DB)
# usage: sampler.sh <out_dir> <container>...
set -u
out=$1; shift
cd "$(dirname "$0")/.."
echo "ts,container,cpu_pct,mem_usage" > "$out/stats.csv"
echo "ts,active,idle,total" > "$out/db.csv"

# docker stats --no-stream blocks ~1-2 s per call, which sets the sampling period.
( while true; do
    ts=$(date +%s)
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' "$@" 2>/dev/null \
      | tr -d '%' | sed "s|^|$ts,|" >> "$out/stats.csv"
  done ) &
stats_pid=$!

( while true; do
    ts=$(date +%s)
    docker compose exec -T postgres psql -U bench -d postgres -Atc \
      "SELECT count(*) FILTER (WHERE state = 'active'), count(*) FILTER (WHERE state LIKE 'idle%'), count(*)
       FROM pg_stat_activity WHERE datname = 'bench' AND backend_type = 'client backend'" 2>/dev/null \
      | tr '|' ',' | sed "s|^|$ts,|" >> "$out/db.csv"
    sleep 1
  done ) &
db_pid=$!

trap 'kill $stats_pid $db_pid 2>/dev/null; exit 0' TERM INT
wait
