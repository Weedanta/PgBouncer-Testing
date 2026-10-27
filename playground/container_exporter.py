"""Per-container CPU and memory exporter (DEV-020), replacing cAdvisor on Docker Desktop.

On Docker Desktop with the WSL2 backend cAdvisor cannot register the Docker containers:
the container cgroups exist (/sys/fs/cgroup/docker/<id>) but cAdvisor reports only the
root cgroup, so every per-container series of Tabel 3.8 stayed empty (25 Oct 2026).

This exporter reads the same cgroup counters through the Docker Engine API
(`GET /containers/<id>/stats?stream=false&one-shot=true`, the data behind `docker stats`)
for the containers whose name starts with `pgb-`, and serves them under cAdvisor's
metric names and `name` label, so Prometheus queries, the runner and the analysis are
unchanged:

  container_memory_rss                 anonymous memory (cgroup v2 `anon`, v1 `rss`)
  container_memory_working_set_bytes   usage - inactive_file (what `docker stats` shows)
  container_memory_usage_bytes         cgroup memory usage
  container_cpu_usage_seconds_total    cumulative CPU time (counter)
  container_start_time_seconds         State.StartedAt (detects restarts)

Standard library only. Values are refreshed by a background loop every
`--interval` seconds and served from the last snapshot, so a scrape never waits on the
Docker API.
"""

from __future__ import annotations

import argparse
import http.client
import json
import socket
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PREFIX = "pgb-"


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 3.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def docker_get(sock_path: str, url: str) -> Any:
    conn = UnixHTTPConnection(sock_path)
    try:
        conn.request("GET", url)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(f"{url}: HTTP {resp.status}")
        return json.loads(body)
    finally:
        conn.close()


def parse_started_at(value: str) -> float | None:
    """Docker timestamps have nanoseconds and a Z suffix: 2026-10-25T16:27:02.344211645Z."""
    if not value or value.startswith("0001-"):
        return None
    v = value.rstrip("Z")
    if "." in v:
        head, frac = v.split(".", 1)
        v = f"{head}.{frac[:6]}"
    else:
        v = f"{v}.000000"
    return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=UTC).timestamp()


def metrics_from_stats(stats: dict[str, Any]) -> dict[str, float]:
    """Turn one Docker stats document into cAdvisor-named values."""
    out: dict[str, float] = {}
    mem = stats.get("memory_stats") or {}
    st = mem.get("stats") or {}
    usage = mem.get("usage")
    if usage is not None:
        out["container_memory_usage_bytes"] = float(usage)
        inactive = st.get("inactive_file", st.get("total_inactive_file", 0)) or 0
        out["container_memory_working_set_bytes"] = float(max(usage - inactive, 0))
    rss = st.get("anon", st.get("rss"))
    if rss is not None:
        out["container_memory_rss"] = float(rss)
    cpu = ((stats.get("cpu_stats") or {}).get("cpu_usage") or {}).get("total_usage")
    if cpu is not None:
        out["container_cpu_usage_seconds_total"] = float(cpu) / 1e9
    return out


HELP = {
    "container_memory_rss": ("gauge", "Anonymous memory of the container cgroup (bytes)."),
    "container_memory_working_set_bytes": ("gauge", "Memory usage minus inactive file cache."),
    "container_memory_usage_bytes": ("gauge", "Memory usage of the container cgroup (bytes)."),
    "container_cpu_usage_seconds_total": ("counter", "Cumulative CPU time (seconds)."),
    "container_start_time_seconds": ("gauge", "Start time of the container (unix seconds)."),
}


def render(snapshot: dict[str, dict[str, float]], ok: bool, last: float) -> str:
    lines: list[str] = []
    for metric, (kind, text) in HELP.items():
        lines.append(f"# HELP {metric} {text}")
        lines.append(f"# TYPE {metric} {kind}")
        for name in sorted(snapshot):
            if metric in snapshot[name]:
                lines.append(f'{metric}{{name="{name}"}} {snapshot[name][metric]!r}')
    lines.append("# TYPE container_exporter_up gauge")
    lines.append(f"container_exporter_up {1 if ok else 0}")
    lines.append("# TYPE container_exporter_last_success_seconds gauge")
    lines.append(f"container_exporter_last_success_seconds {last!r}")
    return "\n".join(lines) + "\n"


class Collector:
    def __init__(self, sock_path: str, interval: float) -> None:
        self.sock_path = sock_path
        self.interval = interval
        self.snapshot: dict[str, dict[str, float]] = {}
        self.ok = False
        self.last = 0.0
        self.started: dict[str, float | None] = {}
        self.lock = threading.Lock()

    def collect_once(self) -> None:
        conts = docker_get(self.sock_path, "/containers/json")
        snap: dict[str, dict[str, float]] = {}
        for c in conts:
            name = (c.get("Names") or ["/"])[0].lstrip("/")
            if not name.startswith(PREFIX):
                continue
            cid = c["Id"]
            stats = docker_get(
                self.sock_path, f"/containers/{cid}/stats?stream=false&one-shot=true"
            )
            vals = metrics_from_stats(stats)
            if cid not in self.started:
                info = docker_get(self.sock_path, f"/containers/{cid}/json")
                self.started[cid] = parse_started_at((info.get("State") or {}).get("StartedAt", ""))
            if self.started[cid] is not None:
                vals["container_start_time_seconds"] = float(self.started[cid])
            snap[name] = vals
        with self.lock:
            self.snapshot, self.ok, self.last = snap, True, time.time()

    def loop(self) -> None:
        while True:
            t0 = time.monotonic()
            try:
                self.collect_once()
            except Exception as exc:  # keep serving the last snapshot, report the failure
                with self.lock:
                    self.ok = False
                print(f"collect failed: {exc}", flush=True)
            time.sleep(max(0.0, self.interval - (time.monotonic() - t0)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9338)
    ap.add_argument("--socket", default="/var/run/docker.sock")
    ap.add_argument("--interval", type=float, default=2.5)
    args = ap.parse_args()
    col = Collector(args.socket, args.interval)
    threading.Thread(target=col.loop, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?")[0] not in ("/metrics", "/"):
                self.send_response(404)
                self.end_headers()
                return
            with col.lock:
                body = render(col.snapshot, col.ok, col.last).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a: Any) -> None:
            pass

    print(f"container exporter on :{args.port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
