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
#   bash $S poolsweep    # DEV-022: only if check 4 failed; picks the one pool adjustment
#   bash $S lock         # freeze the configuration (only after a passing calibration)
#   bash $S campaign     # RM2: 150 runs (~2.5 h), asks for confirmation; CAMPAIGN=C1 by default
#   bash $S resume       # continue an interrupted campaign
#   (campaign/resume copy the measurement data to _run_logs/<campaign>/; statistics are
#    done outside this repo, starting from runs.csv)
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
  setup|smoke|all|down|data|memcheck|calibrate|poolsweep|lock|campaign|resume) ;;
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
# tar does not delete files that were removed from the repo (for example analysis/,
# removed 27 Oct 2026), so clear the code folders of the WSL copy first. results/,
# secrets/, config/ and .git are kept.
for d in playground analysis tests tools workloads scripts docs docker; do
  rm -rf "${DST:?}/$d"
done
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

# Copy a campaign's measurement data to _run_logs/<id>/ so it can be opened from Windows:
# runs.csv (one row per run), plan.csv, attempts.csv, campaign.json and every run directory
# without the raw per-transaction pgbench logs (large; they stay on the WSL disk).
copy_data() {
  local cid="$1"
  mkdir -p "$LOGDIR/$cid"
  tar -C "results/$cid" --exclude='pgbench_log*' -cf - . | tar -C "$LOGDIR/$cid" -xf - \
    || status "PERINGATAN: gagal menyalin data $cid ke _run_logs"
}

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
  # DEV-020: the container exporter must report the containers by name (RSS and CPU per
  # container, RM2), and Prometheus must be scraping it.
  status "memeriksa exporter memori dan CPU per kontainer"
  ok=""
  for _ in $(seq 1 12); do
    # Ask Prometheus itself (not the exporter): the series must be scraped and stored.
    if docker exec pgb-prometheus wget -qO- \
        'http://localhost:9090/api/v1/query?query=container_memory_rss%7Bname%3D%22pgb-postgres%22%7D' \
        2>/dev/null | grep -q '"name":"pgb-postgres"'; then ok=1; break; fi
    sleep 5
  done
  [ -n "$ok" ] || fail "Prometheus belum menyimpan memori pgb-postgres. Kirim hasil: docker logs pgb-container-exporter --tail 20; docker exec pgb-prometheus wget -qO- http://localhost:9090/api/v1/targets | grep -o '\"health\":\"[a-z]*\"'"
  status "exporter kontainer OK: Prometheus menyimpan memori dan CPU per kontainer"
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
  copy_data "$cid"
  status "smoke selesai: $cid (data disalin ke _run_logs/$cid)"
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

if [ "$STAGE" = "poolsweep" ]; then
  run make poolsweep
  rep="$(ls -1d results/poolsweep/*/ 2>/dev/null | grep -v SMOKE | tail -n1)"
  if [ -n "$rep" ]; then
    mkdir -p "$LOGDIR/poolsweep"
    cp -r "$rep" "$LOGDIR/poolsweep/"
    status "laporan poolsweep disalin ke _run_logs/poolsweep/$(basename "$rep")"
  fi
fi

if [ "$STAGE" = "lock" ]; then
  # `make lock` commits config/locked and tags it; git needs an identity for that.
  git config user.email >/dev/null || fail "identitas git belum diatur. Jalankan: git config --global user.name \"Nama\"; git config --global user.email \"email@contoh.com\", lalu ulangi lock."
  run make lock
  cp -r config/locked "$LOGDIR/locked"
  status "konfigurasi terkunci; salinannya ada di _run_logs/locked"
fi

if [ "$STAGE" = "campaign" ] || [ "$STAGE" = "resume" ]; then
  [ -f config/locked/LOCK.json ] || fail "konfigurasi belum dikunci: jalankan 'calibrate' lalu 'lock'"
  run make "$STAGE" CAMPAIGN="$CAMPAIGN"
  make status CAMPAIGN="$CAMPAIGN" > "$LOGDIR/status-$CAMPAIGN.txt" 2>&1
  make evidence CAMPAIGN="$CAMPAIGN" > "$LOGDIR/evidence-$CAMPAIGN.txt" 2>&1
  copy_data "$CAMPAIGN"
  status "data kampanye disalin ke _run_logs/$CAMPAIGN (runs.csv = satu baris per run)"
fi

status "selesai"
