#!/usr/bin/env bash
# Runner for the campaign host: Windows 11 + Docker Desktop (WSL2 backend), proposal v5.
# Run it inside Ubuntu (WSL), one stage at a time, in this order:
#
#   S=/mnt/c/Users/<you>/OneDrive/Documents/Skripsi/Playground/scripts/wsl_run.sh
#   bash $S setup        # copy repo to the WSL disk, uv env, probe, secrets, build, start stack
#   bash $S smoke        # smoke dataset + smoke campaign + check-metrics + evidence (~40 min)
#   bash $S data         # real dataset: scale 100 + R5 seed + template
#   bash $S memcheck     # DEV-019: memory limits at the highest c, both paths (~5 min)
#   bash $S calibrate    # RM1: four checks (~40 min), report copied to _run_logs/
#   bash $S lock         # freeze the configuration (only after a passing calibration)
#   bash $S campaign     # RM2: 150 runs (~2.5 h), asks for confirmation; CAMPAIGN=C1 by default
#   bash $S resume       # continue an interrupted campaign
#   bash $S analyze      # tables, figures, REPORT.md; copied to _run_logs/<campaign>/
#   bash $S down         # stop the stack (data stays)
#   (bash $S all = setup + smoke)
#
# - Only `setup` copies the repo from OneDrive to the WSL disk (~/pgbouncer-playground),
#   because PostgreSQL data and results must not live on OneDrive or /mnt/c (Microsoft's
#   WSL guidance; RUNBOOK section 1). After `lock`, setup refuses to copy again, so the
#   locked configuration and its git commit in the WSL copy are never overwritten.
# - Writes a log and a STATUS file to <Skripsi>/_run_logs/ so progress can be followed
#   from Windows, and copies reports there.
set -uo pipefail
STAGE="${1:-all}"
case "$STAGE" in
  setup|smoke|all|down|data|memcheck|calibrate|lock|campaign|resume|analyze) ;;
  *) echo "stage tidak dikenal: $STAGE (lihat komentar di awal skrip)" >&2; exit 2;;
esac
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
# DEV-019: WSL is capped at 10 GB so Windows keeps about 5 GB of the 15.3 GB laptop.
if [ "$mem_mb" -gt 11500 ]; then
  status "PERINGATAN: WSL memakai ${mem_mb} MB; Windows bisa kekurangan memori dan melakukan paging. Disarankan memory=10GB di .wslconfig (DEV-019)."
fi
if [ "$mem_mb" -lt 9000 ]; then
  win_user="$(cmd.exe /c 'echo %USERNAME%' 2>/dev/null | tr -d '\r')"
  cfg="/mnt/c/Users/${win_user}/.wslconfig"
  echo
  echo "Memori WSL terlalu kecil untuk pembagian sumber daya Playground (perlu sekitar 10 GB)."
  if [ -n "$win_user" ] && [ ! -e "$cfg" ]; then
    read -r -p "Buat $cfg dengan memory=10GB, processors=16, swap=0? [y/N] " ans
    if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
      printf '[wsl2]\nmemory=10GB\nprocessors=16\nswap=0\n' > "$cfg"
      status "dibuat $cfg"
    fi
  elif [ -e "$cfg" ]; then
    echo "Isi $cfg saat ini:"; cat "$cfg"
  fi
  fail "Atur memori WSL (lihat di atas), lalu di PowerShell: wsl --shutdown, buka Ubuntu lagi, dan jalankan skrip ini lagi."
fi

CAMPAIGN="${CAMPAIGN:-C1}"

# --- copy the repo to the WSL disk (setup only) -----------------------------------
copy_repo() {
if [ -f "$DST/config/locked/LOCK.json" ]; then
  fail "konfigurasi sudah dikunci di $DST; salinan tidak ditimpa. Ubahan di OneDrive tidak dipakai lagi."
fi
status "menyalin repo dari OneDrive ke $DST"
mkdir -p "$DST"
tar -C "$SRC" \
  --exclude=./.venv --exclude=./results --exclude=./secrets --exclude=./config/host.env \
  --exclude=./config/active --exclude=./config/locked --exclude=./config/calibration_overrides.yaml \
  --exclude='*/__pycache__' --exclude=./.pytest_cache --exclude=./.ruff_cache \
  -cf - . | tar -C "$DST" -xf - || fail "gagal menyalin repo"
mkdir -p "$DST/results" "$DST/secrets" "$DST/config/locked"
}

if [ "$STAGE" = "setup" ] || [ "$STAGE" = "all" ]; then
  copy_repo
fi
[ -d "$DST" ] || fail "$DST belum ada: jalankan dulu 'setup'"
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
  status "smoke campaign dimulai (sekitar 40 menit)"
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

if [ "$STAGE" = "data" ]; then
  run make init-data
  cp results/datasets/campaign-*.json "$LOGDIR/" 2>/dev/null
fi

if [ "$STAGE" = "memcheck" ]; then
  run make memcheck
  rep="$(ls -1d results/memcheck/*/ 2>/dev/null | grep -v SMOKE | tail -n1)"
  if [ -n "$rep" ]; then
    mkdir -p "$LOGDIR/memcheck"
    cp -r "$rep" "$LOGDIR/memcheck/"
    status "laporan uji memori disalin ke _run_logs/memcheck/$(basename "$rep")"
    grep -q "Kesimpulan: LOLOS" "$rep/REPORT.md" || fail "uji memori belum lolos: baca _run_logs/memcheck/$(basename "$rep")/REPORT.md"
  fi
fi

if [ "$STAGE" = "calibrate" ]; then
  run make calibrate
  rep=""
  for d in results/calibration/*/; do
    case "$d" in *SMOKE*) ;; *) [ -d "$d" ] && rep="$d";; esac
  done
  if [ -n "$rep" ]; then
    mkdir -p "$LOGDIR/calibration"
    cp -r "$rep" "$LOGDIR/calibration/"
    status "laporan kalibrasi disalin ke _run_logs/calibration/$(basename "$rep")"
  fi
fi

if [ "$STAGE" = "lock" ]; then
  run make lock
  cp -r config/locked "$LOGDIR/locked"
  status "konfigurasi terkunci; salinannya ada di _run_logs/locked"
fi

if [ "$STAGE" = "campaign" ] || [ "$STAGE" = "resume" ]; then
  [ -f config/locked/LOCK.json ] || fail "konfigurasi belum dikunci: jalankan 'calibrate' lalu 'lock'"
  run make "$STAGE" CAMPAIGN="$CAMPAIGN"
  make status CAMPAIGN="$CAMPAIGN" > "$LOGDIR/status-$CAMPAIGN.txt" 2>&1
fi

if [ "$STAGE" = "analyze" ]; then
  run make analyze CAMPAIGN="$CAMPAIGN"
  run make export-thesis CAMPAIGN="$CAMPAIGN"
  mkdir -p "$LOGDIR/$CAMPAIGN"
  cp -r "results/$CAMPAIGN/analysis" "results/$CAMPAIGN/thesis" "$LOGDIR/$CAMPAIGN/" 2>/dev/null
  cp "results/$CAMPAIGN/runs.csv" "results/$CAMPAIGN/attempts.csv" "$LOGDIR/$CAMPAIGN/" 2>/dev/null
  status "analisis disalin ke _run_logs/$CAMPAIGN"
fi

status "selesai"
