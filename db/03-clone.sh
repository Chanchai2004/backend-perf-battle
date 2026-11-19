#!/bin/sh
# Runs once at first Postgres start: bench = fresh copy of the seeded template.
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE bench TEMPLATE bench_template"
