#!/usr/bin/env bash
# Host tuning for stable measurements (Subbab 3.4: no other load, fixed conditions).
# Without --apply it only prints the current values. With --apply it uses sudo.
# On WSL2 most of these are controlled by Windows: see docs/RUNBOOK.md section 1.
set -uo pipefail
apply=0; [ "${1:-}" = "--apply" ] && apply=1

show() { printf '%-32s %s\n' "$1" "$2"; }
gov=/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
thp=/sys/kernel/mm/transparent_hugepage/enabled
echo "== before"
show "cpu governor" "$(cat $gov 2>/dev/null || echo 'n/a (WSL2/VM: set the Windows power plan)')"
show "transparent hugepages" "$(cat $thp 2>/dev/null || echo n/a)"
show "vm.swappiness" "$(cat /proc/sys/vm/swappiness)"
show "open files (ulimit -n)" "$(ulimit -n)"
show "docker" "$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo 'not reachable')"

if [ $apply -eq 1 ]; then
  if [ -w "$gov" ] || sudo test -w "$gov" 2>/dev/null; then
    for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
      echo performance | sudo tee "$f" >/dev/null
    done
  fi
  [ -e "$thp" ] && echo madvise | sudo tee "$thp" >/dev/null
  sudo sysctl -q vm.swappiness=1
  echo "== after"
  show "cpu governor" "$(cat $gov 2>/dev/null || echo n/a)"
  show "transparent hugepages" "$(cat $thp 2>/dev/null || echo n/a)"
  show "vm.swappiness" "$(cat /proc/sys/vm/swappiness)"
  echo "Record these values in docs/HOST.md (rerun: make probe)."
fi
