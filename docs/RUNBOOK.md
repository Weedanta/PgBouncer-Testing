# Runbook

Operator guide. Sections are filled in as milestones land.

## 1. One-time setup (Windows + WSL2 development host)

1. Install an Ubuntu distro for WSL (Docker Desktop's own `docker-desktop` distro is not a shell):
   `wsl --install -d Ubuntu-24.04`
2. Docker Desktop → Settings → Resources → WSL integration → enable Ubuntu-24.04.
3. Clone the repo **inside the WSL filesystem** (for example `~/playground`), not under
   OneDrive or `/mnt/c`. PostgreSQL data and `results/` must live on the WSL ext4 disk:
   OneDrive would try to sync tens of GB of logs, and `/mnt/c` I/O is slow.
4. Install uv in Ubuntu (`curl -LsSf https://astral.sh/uv/install.sh | sh`), then `make sync`.
5. The WSL2 memory/CPU limits (`%UserProfile%\.wslconfig`) are set in M1 once the probe
   has measured what the stack needs.

## 2. Everyday commands

| Command | Purpose |
|---|---|
| `make validate` | Check `experiment.yaml` against the research design |
| `make lint test` | Static checks and unit tests |

The remaining targets (`probe` … `export-thesis`) arrive with milestones M1–M8.

## 3. Rules while a campaign runs

- No other workloads on the host. Plug the laptop in, use the high-performance power plan,
  and pause OneDrive and Windows Update.
- Never restart the `postgres` or `pgbouncer` containers; the runner logs any restart as an incident.
- Never edit files under `config/locked/`. The runner refuses to start if their hashes change.
