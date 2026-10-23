#!/usr/bin/env bash
# Record the digest of every image the stack uses -> config/images.lock.json.
# Run after `make build` (images must be present locally). `make lock` calls this.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source config/versions.env; set +a
out=config/images.lock.json
{
  echo "{"
  first=1
  for var in POSTGRES_IMAGE POSTGRES_EXPORTER_IMAGE PGBOUNCER_EXPORTER_IMAGE NODE_EXPORTER_IMAGE \
             CADVISOR_IMAGE PROMETHEUS_IMAGE GRAFANA_IMAGE UV_IMAGE; do
    img="${!var}"
    digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "$img" 2>/dev/null || echo "not pulled")"
    [ $first -eq 1 ] || echo ","
    first=0
    printf '  "%s": {"tag": "%s", "digest": "%s"}' "$var" "$img" "$digest"
  done
  for img in pgbouncer-playground/pgbouncer:local pgbouncer-playground/client:local; do
    id="$(docker image inspect --format '{{.Id}}' "$img" 2>/dev/null || echo "not built")"
    printf ',\n  "%s": {"image_id": "%s"}' "$img" "$id"
  done
  pgb="$(docker run --rm --entrypoint cat pgbouncer-playground/pgbouncer:local /pgbouncer-version.txt 2>/dev/null | head -1 || true)"
  printf ',\n  "pgbouncer_build": "%s"\n}\n' "$pgb"
} > "$out"
cat "$out"
