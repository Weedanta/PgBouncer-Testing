"""Session-feature linter (evidence for the transaction-mode criterion, Tabel 2.2).

Every workload script must avoid the PostgreSQL features that transaction pooling does
not support. This module scans pgbench scripts and reports, per script, which Tabel 2.2
feature (if any) is used. It is a selection criterion, not a measured variable
(Batasan 6).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# (rule id, Tabel 2.2 feature, statement-level regex). Patterns run on SQL with comments
# and string literals removed, one statement at a time, case-insensitive.
STATEMENT_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("set_session", "SET / RESET", re.compile(r"^\s*SET\b(?!\s+LOCAL\b)", re.I)),
    ("reset", "SET / RESET", re.compile(r"^\s*RESET\b", re.I)),
    ("listen", "LISTEN", re.compile(r"^\s*(UN)?LISTEN\b", re.I)),
    ("sql_prepare", "PREPARE / DEALLOCATE (level-SQL)", re.compile(r"^\s*PREPARE\b", re.I)),
    ("sql_execute", "PREPARE / DEALLOCATE (level-SQL)", re.compile(r"^\s*EXECUTE\b", re.I)),
    ("sql_deallocate", "PREPARE / DEALLOCATE (level-SQL)", re.compile(r"^\s*DEALLOCATE\b", re.I)),
    (
        "cursor_with_hold",
        "Cursor WITH HOLD",
        re.compile(r"^\s*DECLARE\b.*\bWITH\s+HOLD\b", re.I | re.S),
    ),
    ("load", "Statement LOAD", re.compile(r"^\s*LOAD\b", re.I)),
]
ANYWHERE_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "session_advisory_lock",
        "Advisory lock level-sesi",
        re.compile(
            r"\bpg_(try_)?advisory_lock(_shared)?\s*\(|\bpg_advisory_unlock(_shared|_all)?\s*\(",
            re.I,
        ),
    ),
    (
        "set_config_session",
        "SET / RESET",
        re.compile(r"\bset_config\s*\([^)]*,\s*false\s*\)", re.I),
    ),
]
TEMP_TABLE = re.compile(r"^\s*CREATE\s+(GLOBAL\s+|LOCAL\s+)?TEMP(ORARY)?\s+TABLE\b", re.I)
ON_COMMIT_DROP = re.compile(r"\bON\s+COMMIT\s+DROP\b", re.I)


@dataclass
class Finding:
    rule: str
    feature: str
    statement: str


@dataclass
class ScriptResult:
    name: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def strip_sql(text: str) -> str:
    """Remove comments, string literals and pgbench meta-commands, keep SQL structure."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        elif ch == "'":
            j = i + 1
            while j < n:
                if text[j] == "'" and j + 1 < n and text[j + 1] == "'":
                    j += 2
                elif text[j] == "'":
                    break
                else:
                    j += 1
            out.append("''")
            i = j + 1
        elif ch == "$":
            m = re.match(r"\$[A-Za-z_]*\$", text[i:])
            if m:
                tag = m.group(0)
                j = text.find(tag, i + len(tag))
                out.append("''")
                i = n if j < 0 else j + len(tag)
            else:
                out.append(ch)
                i += 1
        else:
            out.append(ch)
            i += 1
    lines = []
    for line in "".join(out).splitlines():
        stripped = line.strip()
        if stripped.startswith("\\"):
            continue  # pgbench meta-command (\set, \sleep, ...)
        lines.append(re.sub(r"\\\w+.*$", ";", line))  # trailing \gset / \aset ends a command
    return "\n".join(lines)


def statements(text: str) -> list[str]:
    return [s.strip() for s in strip_sql(text).split(";") if s.strip()]


def lint_text(name: str, text: str) -> ScriptResult:
    result = ScriptResult(name)
    for stmt in statements(text):
        short = " ".join(stmt.split())[:120]
        for rule, feature, pattern in STATEMENT_RULES:
            if pattern.search(stmt):
                result.findings.append(Finding(rule, feature, short))
        for rule, feature, pattern in ANYWHERE_RULES:
            if pattern.search(stmt):
                result.findings.append(Finding(rule, feature, short))
        if TEMP_TABLE.search(stmt) and not ON_COMMIT_DROP.search(stmt):
            result.findings.append(
                Finding("temp_table_preserve", "Temporary table PRESERVE ROWS / DELETE ROWS", short)
            )
    return result


def builtin_script(name: str, pgbench: str = "pgbench") -> str | None:
    """Text of a pgbench built-in script (select-only, tpcb-like), if pgbench is present."""
    if not shutil.which(pgbench):
        return None
    proc = subprocess.run(
        [pgbench, f"--show-script={name}"], capture_output=True, text=True, check=False
    )
    text = proc.stdout or proc.stderr
    # pgbench prints a header line like '-- select-only: <builtin: select only>'
    return text if proc.returncode == 0 else None


def lint_experiment(cfg: dict, repo_root: Path) -> list[ScriptResult]:
    results: list[ScriptResult] = []
    seen: set[str] = set()
    for pname in cfg["design"]["profiles"]:
        prof = cfg["profiles"][pname]
        if "builtin" in prof:
            key = f"builtin:{prof['builtin']}"
            if key in seen:
                continue
            seen.add(key)
            text = builtin_script(prof["builtin"])
            if text is None:
                res = ScriptResult(key)
                res.findings.append(Finding("unchecked", "pgbench not available", ""))
                results.append(res)
            else:
                results.append(lint_text(key, text))
        for script in prof.get("scripts") or {}:
            if script in seen:
                continue
            seen.add(script)
            results.append(lint_text(script, (repo_root / script).read_text(encoding="utf-8")))
    return results


def report_markdown(results: list[ScriptResult]) -> str:
    lines = [
        "# Pemeriksaan kompatibilitas mode transaction (Tabel 2.2)",
        "",
        "Every workload script was scanned for the PostgreSQL features that PgBouncer",
        "transaction mode does not support. `OK` means none was found.",
        "",
        "| Script | Verdict | Finding |",
        "|---|---|---|",
    ]
    for r in results:
        if r.ok:
            lines.append(f"| `{r.name}` | OK | - |")
        for f in r.findings:
            verdict = "UNCHECKED" if f.rule == "unchecked" else "FAIL"
            lines.append(f"| `{r.name}` | {verdict} | {f.feature}: `{f.statement}` |")
    lines.append("")
    passed = all(r.ok for r in results)
    lines.append(f"**Result: {'all scripts pass' if passed else 'at least one script fails'}.**")
    return "\n".join(lines) + "\n"
