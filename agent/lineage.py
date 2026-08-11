"""Run lineage — every model run is immutable and reviewable.

A run directory is written once and never modified. Corrections are new runs, so a
superseded number stays reviewable next to the one that replaced it.

The manifest answers "why did this run produce this number?" without rerunning it.
Input hashes are what make that real: two runs producing different numbers from an
identically-named input is exactly the situation this has to explain.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pipeline import config

RUNS_DIR = config.RUNS
INDEX = RUNS_DIR / "index.jsonl"
LATEST = RUNS_DIR / "latest.json"


def _utc_now():
    return datetime.now(timezone.utc)


def new_run_id(now=None) -> str:
    """<UTC timestamp>_<6 hex>. The random suffix makes same-second runs distinct."""
    now = now or _utc_now()
    return f"{now.strftime('%Y-%m-%dT%H%M%SZ')}_{secrets.token_hex(3)}"


def sha256_file(path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_record(path) -> dict:
    path = Path(path)
    exists = path.exists()
    return {
        "path": str(path.relative_to(config.ROOT)) if config.ROOT in path.parents else str(path),
        "exists": exists,
        "sha256": sha256_file(path) if exists else None,
        "bytes": path.stat().st_size if exists else None,
        "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if exists else None,
    }


def git_state() -> dict:
    """Which code version produced this. `dirty` true means the run is not
    reproducible — recorded honestly rather than refused."""
    def run(*args):
        try:
            return subprocess.run(
                ["git", *args], cwd=config.ROOT, capture_output=True, text=True, timeout=10
            ).stdout.strip()
        except Exception:
            return None

    commit = run("rev-parse", "HEAD")
    status = run("status", "--porcelain")
    return {
        "commit": commit or None,
        "dirty": bool(status) if status is not None else None,
    }


class Run:
    """An immutable run directory.

    Usage:
        with Run(quarter_start) as run:
            run.add_input(path); run.add_output(path, rows=...)
            run.headline(...); run.caveat(...); run.warn(...)
    """

    def __init__(self, quarter_start=None, run_id=None, runs_dir=None):
        self.quarter_start = quarter_start or config.QUARTER_START
        self.runs_dir = Path(runs_dir) if runs_dir else RUNS_DIR
        self.run_id = run_id or new_run_id()
        self.dir = self.runs_dir / self.run_id
        if self.dir.exists():
            raise FileExistsError(f"run directory already exists, refusing to overwrite: {self.dir}")
        self.dir.mkdir(parents=True)
        self.started_at = _utc_now()
        self._inputs, self._outputs = [], []
        self._headline, self._caveats, self._warnings = {}, [], []

    def add_input(self, path):
        self._inputs.append(file_record(path))
        return self

    def add_output(self, path, rows=None):
        rec = file_record(path)
        rec["rows"] = rows
        self._outputs.append(rec)
        return self

    def headline(self, **figures):
        self._headline.update(figures)
        return self

    def caveat(self, *slugs):
        for s in slugs:
            if s not in self._caveats:
                self._caveats.append(s)
        return self

    def warn(self, *slugs):
        for s in slugs:
            if s not in self._warnings:
                self._warnings.append(s)
        return self

    def build_manifest(self) -> dict:
        qs = self.quarter_start
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": _utc_now().isoformat(),
            "git": git_state(),
            "quarter": config.fq_label(qs),
            "quarter_start": qs,
            "quarter_end": config.q_end(qs),
            # Recorded as evidence invariant 1 was honored — derived, not hardcoded.
            "month_columns": config.month_columns(qs),
            "code": {
                name: sha256_file(config.ROOT / rel)
                for name, rel in (
                    ("config.py", "pipeline/config.py"),
                    ("queries.py", "pipeline/queries.py"),
                    ("pipe_create.py", "pipeline/pipe_create.py"),
                    ("targets.py", "agent/targets.py"),
                )
            },
            "inputs": self._inputs,
            "outputs": self._outputs,
            "headline": self._headline,
            "caveats": self._caveats,
            "warnings": self._warnings,
        }

    def finish(self) -> Path:
        manifest = self.build_manifest()
        (self.dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        self.runs_dir.mkdir(parents=True, exist_ok=True)
        with (self.runs_dir / "index.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "run_id": self.run_id,
                "finished_at": manifest["finished_at"],
                "quarter": manifest["quarter"],
                "headline": manifest["headline"],
                "git_dirty": manifest["git"]["dirty"],
            }) + "\n")

        # A pointer file, not a symlink: symlinks on Windows need elevation or
        # developer mode, and this must work without either.
        (self.runs_dir / "latest.json").write_text(
            json.dumps({"run_id": self.run_id, "path": str(self.dir)}, indent=2), encoding="utf-8"
        )
        return self.dir

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.finish()
        return False


def list_runs(runs_dir=None):
    """Every run, newest last. Reads the append-only index."""
    idx = (Path(runs_dir) if runs_dir else RUNS_DIR) / "index.jsonl"
    if not idx.exists():
        return []
    return [json.loads(line) for line in idx.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_manifest(run_id, runs_dir=None):
    p = (Path(runs_dir) if runs_dir else RUNS_DIR) / run_id / "manifest.json"
    if not p.exists():
        raise FileNotFoundError(f"no manifest for run {run_id}")
    return json.loads(p.read_text(encoding="utf-8"))
