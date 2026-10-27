#!/usr/bin/env bash
# Record the digest of every image the stack uses -> config/images.lock.json.
# Run after `make build` (images must be present locally). `make lock` calls this.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source config/versions.env; set +a
out=config/images.lock.json
tmp="$out.tmp"
# A missing image must stop the pin (and therefore `make lock`): an empty or "not
# pulled" digest would leave the lock without the version it is meant to record.
# (`docker image inspect` prints an empty line before failing, which previously ended
# up inside the JSON string.) CADVISOR_IMAGE is not pinned: DEV-020 replaced cAdvisor
# with the container exporter, which runs from the client image recorded below.
inspect() {
  local fmt="$1" img="$2" val
  val="$(docker image inspect --format "$fmt" "$img" 2>/dev/null | head -n1)" || val=""
  if [ -z "$val" ]; then
    echo "pin: image $img is not available locally (run make build)" >&2
    exit 1
  fi
  printf '%s' "$val"
}
{
  echo "{"
  first=1
  for var in POSTGRES_IMAGE POSTGRES_EXPORTER_IMAGE PGBOUNCER_EXPORTER_IMAGE NODE_EXPORTER_IMAGE \
             PROMETHEUS_IMAGE GRAFANA_IMAGE UV_IMAGE; do
    img="${!var}"
    digest="$(inspect '{{index .RepoDigests 0}}' "$img")"
    [ $first -eq 1 ] || echo ","
    first=0
    printf '  "%s": {"tag": "%s", "digest": "%s"}' "$var" "$img" "$digest"
  done
  for img in pgbouncer-playground/pgbouncer:local pgbouncer-playground/client:local; do
    id="$(inspect '{{.Id}}' "$img")"
    printf ',\n  "%s": {"image_id": "%s"}' "$img" "$id"
  done
  pgb="$(docker run --rm --entrypoint cat pgbouncer-playground/pgbouncer:local /pgbouncer-version.txt 2>/dev/null | head -1 || true)"
  if [ -z "$pgb" ]; then
    echo "pin: could not read the PgBouncer version from pgbouncer-playground/pgbouncer:local" >&2
    exit 1
  fi
  printf ',\n  "pgbouncer_build": "%s"\n}\n' "$pgb"
} > "$tmp"
python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$tmp" \
  || { echo "pin: $tmp is not valid JSON" >&2; exit 1; }
mv "$tmp" "$out"
cat "$out"
