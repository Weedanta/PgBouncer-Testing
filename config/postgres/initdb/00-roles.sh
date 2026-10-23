#!/bin/bash
# Runs once, when the data directory is initialized (docker-entrypoint-initdb.d).
# bench: owns the test databases, used by pgbench on BOTH paths.
# exporter: read-only monitoring role for postgres_exporter (pg_monitor).
set -euo pipefail
bench_pw="$(cat /run/secrets/bench_password)"
exporter_pw="$(cat /run/secrets/exporter_password)"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE bench LOGIN PASSWORD '${bench_pw}';
CREATE ROLE exporter LOGIN PASSWORD '${exporter_pw}';
GRANT pg_monitor TO exporter;
SQL
