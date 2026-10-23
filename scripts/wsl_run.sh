#!/usr/bin/env bash
# One-shot runner for Windows + Docker Desktop (WSL2). Run it inside Ubuntu (WSL):
#
#   bash /mnt/c/Users/<you>/OneDrive/Documents/Skripsi/Playground/scripts/wsl_run.sh [setup|smoke|all|down]
#
# - copies the repo from OneDrive to the WSL disk (~/pgbouncer-playground), because
#   PostgreSQL data and results must not live on OneDrive or /mnt/c (RUNBOOK section 1);
# - setup: uv env, probe, secrets, build images, start all containers (docker compose);
# - smoke: smoke dataset + smoke campaign + check-metrics under load + evidence;
# - writes a log and a STATUS file to <Skripsi>/_run_logs/ so progress can be followed
#   from Windows, and copies the smoke analysis there.
set -uo pipefail
STAGE="${1:-all}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKRIPSI="$(dirname "$SRC")"
DST="$HOME/pgbouncer-playground"
LOGDIR="$SKRIPSI/_run_logs"
mkdir -p "$LOGDIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOGDIR/run-$STAMP.log"
STATUS="$LOGDIR/STATUS.txt"
exec > >(tee -a "$LOG") 2>&1

status() { echo "$(date '+%F %T') $*" | tee -a "$STATUS"; }
fail() { status "GAGAL: $*"; exit 1; }

status "mulai stage=$STAGE log=$LOG"

# --- prerequisites ---------------------------------------------------------------
command -v docker >/dev/null || fail "docker tidak ditemukan. Docker Desktop > Settings > Resources > WSL integration: aktifkan distro ini."
docker info >/dev/null 2>&1 || fail "Docker Desktop belum berjalan atau WSL integration belum aktif."
docker compose version >/dev/null 2>&1 || fail "docker compose v2 tidak tersedia."
if ! command -v make >/dev/null; then
  status "memasang make (butuh password sudo)"
  sudo apt-get update -qq && sudo apt-get install -y -qq make || fail "gagal memasang make"
fi
if ! command -v uv >/dev/null && [ ! -x "$HOME/.local/bin/uv" ]; then
  status "memasang uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh || fail "gagal memasang uv"
fi
export PATH="$HOME/.local/bin:$PATH"

mem_mb=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 ))
status "memori WSL: ${mem_mb} MB, CPU: $(nproc)"
if [ "$mem_mb" -lt 11000 ]; then
  win_user="$(cmd.exe /c 'echo %USERNAME%' 2>/dev/null | tr -d '\r')"
  cfg="/mnt/c/Users/${win_user}/.wslconfig"
  echo
  echo "Memori WSL terlalu kecil untuk pembagian sumber daya Playground (perlu sekitar 13 GB)."
  if [ -n "$win_user" ] && [ ! -e "$cfg" ]; then
    read -r -p "Buat $cfg dengan memory=13GB, processors=16, swap=0? [y/N] " ans
    if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
      printf '[wsl2]\nmemory=13GB\nprocessors=16\nswap=0\n' > "$cfg"
      status "dibuat $cfg"
    fi
  elif [ -e "$cfg" ]; then
    echo "Isi $cfg saat ini:"; cat "$cfg"
  fi
  fail "Atur memori WSL (lihat di atas), lalu di PowerShell: wsl --shutdown, buka Ubuntu lagi, dan jalankan skrip ini lagi."
fi

# --- copy the repo to the WSL disk ------------------------------------------------
status "menyalin repo dari OneDrive ke $DST"
mkdir -p "$DST"
tar -C "$SRC" \
  --exclude=./.venv --exclude=./results --exclude=./secrets --exclude=./config/host.env \
  --exclude=./config/active --exclude=./config/locked --exclude=./config/calibration_overrides.yaml \
  --exclude='*/__pycache__' --exclude=./.pytest_cache --exclude=./.ruff_cache \
  -cf - . | tar -C "$DST" -xf - || fail "gagal menyalin repo"
mkdir -p "$DST/results" "$DST/secrets" "$DST/config/locked"
cd "$DST" || fail "tidak bisa masuk ke $DST"

run() { status "> $*"; "$@" || fail "$*"; }

if [ "$STAGE" = "down" ]; then
  run make down
  exit 0
fi

if [ "$STAGE" = "setup" ] || [ "$STAGE" = "all" ]; then
  run make sync
  run make validate
  run make probe
  cp docs/HOST.md "$LOGDIR/HOST.md"
  [ -f secrets/postgres_password ] || run make secrets
  run make build
  run make up
  make ps | tee "$LOGDIR/compose-ps.txt"
  run make pin
  cp config/images.lock.json "$LOGDIR/images.lock.json"
  status "stack berjalan: Grafana http://127.0.0.1:3000 (admin / $(cat secrets/grafana_admin_password)), Prometheus http://127.0.0.1:9090"
fi

if [ "$STAGE" = "smoke" ] || [ "$STAGE" = "all" ]; then
  run make init-data-smoke
  status "smoke campaign dimulai (sekitar 25 menit)"
  make smoke > "$LOGDIR/smoke-$STAMP.log" 2>&1 &
  smoke_pid=$!
  sleep 150
  status "check-metrics saat beban berjalan"
  docker compose --env-file config/versions.env --env-file config/host.env \
    --env-file secrets/secrets.env exec -T client playground check-metrics --database bench_smoke \
    > "$LOGDIR/check-metrics.txt" 2>&1 || status "check-metrics: ada seri yang hilang (lihat check-metrics.txt)"
  wait "$smoke_pid" || fail "smoke gagal (lihat smoke-$STAMP.log)"
  cid="$(ls -1d results/SMOKE-* | tail -n1 | xargs basename)"
  make evidence CAMPAIGN="$cid" > "$LOGDIR/evidence-$cid.txt" 2>&1
  mkdir -p "$LOGDIR/$cid"
  cp -r "results/$cid/analysis" "$LOGDIR/$cid/" 2>/dev/null
  cp "results/$cid/runs.csv" "results/$cid/attempts.csv" "$LOGDIR/$cid/" 2>/dev/null
  status "smoke selesai: $cid (analisis disalin ke _run_logs/$cid)"
fi
status "selesai"
