"""Execute the verifier subagent's recompute script and report the delta.

    python -m pipeline.verify_cli <run_id>

WHY THIS EXISTS. The verifier subagent re-derives a run's headline in its own
context window — it never sees the maker's reasoning, which is what makes its
answer worth having. But SDK 0.2.134 does not grant Bash to subagents (three
acceptance runs; permissionMode tried with approval and reverted as useless),
so the verifier can WRITE its recompute script and cannot RUN it.

This relay is the split at the only seam that preserves independence: the
verifier derives the logic; the main agent — which has Bash — executes it here
and reports the comparison. Independence lives in WHO DERIVES THE LOGIC, not in
who types `python`.

CONTRACT with the verifier (mirrored in agent/options.py::VERIFIER):
  - the script lives at  workspace/scratch/verify_<run_id>.py
  - it prints one line per figure it recomputes:  RECOMPUTED <key>=<number>
    where <key> matches a key in the manifest's headline
  - anything else it prints is carried into the report as commentary

This module never invents a verdict. No script -> CANNOT VERIFY. A crash ->
CANNOT VERIFY with the script's own error. Exit 0 with no RECOMPUTED lines ->
CANNOT VERIFY, because silence is not success.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

from pipeline import config

SCRATCH = config.ROOT / "workspace" / "scratch"

# Relative tolerance for AGREE. Float dust from a re-derivation is not a
# disagreement; a tenth of a percent on a nine-figure target is ~$200K and IS.
REL_TOL = 1e-4
TIMEOUT_S = 300

_RECOMPUTED = re.compile(r"^RECOMPUTED\s+(\S+?)\s*=\s*(-?[\d_,.]+)\s*$", re.M)


def _claimed(run_id: str) -> dict:
    p = config.RUNS / run_id / "manifest.json"
    if not p.exists():
        raise FileNotFoundError(f"no manifest for run {run_id!r} under {config.RUNS}")
    head = json.loads(p.read_text(encoding="utf-8")).get("headline", {})
    return {k: v for k, v in head.items() if isinstance(v, (int, float))}


def execute(run_id: str) -> str:
    """Run the verifier's script for `run_id` and format the comparison."""
    claimed = _claimed(run_id)
    script = SCRATCH / f"verify_{run_id}.py"

    if not script.exists():
        return (f"VERDICT: CANNOT VERIFY\n"
                f"reason: the verifier has not run — no script at {script}.\n"
                f"Launch the verifier subagent on run {run_id} first; it writes "
                f"its recompute script there, then this relay executes it.")

    # The child inherits this process's environment — which, per step 1, does
    # NOT contain SYNAPSE_CONN_STR. The verifier checks what the maker used
    # from cached files; it has no warehouse access by construction.
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, timeout=TIMEOUT_S,
        cwd=str(config.ROOT),
    )

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
        return (f"VERDICT: CANNOT VERIFY\n"
                f"reason: the verifier's script failed (exit {proc.returncode}).\n"
                + "\n".join(f"  {l}" for l in tail))

    recomputed = {k: float(v.replace(",", "").replace("_", ""))
                  for k, v in _RECOMPUTED.findall(proc.stdout or "")}
    if not recomputed:
        return (f"VERDICT: CANNOT VERIFY\n"
                f"reason: the script exited cleanly but printed no figures. "
                f"Silence is not success — it must print one "
                f"'RECOMPUTED <key>=<number>' line per figure, with keys matching "
                f"the manifest headline ({', '.join(claimed) or 'none numeric'}).")

    lines, worst = [], 0.0
    for key, got in sorted(recomputed.items()):
        if key not in claimed:
            lines.append(f"  {key}: recomputed {got:,.2f} but the manifest has no "
                         f"such headline figure")
            continue
        want = float(claimed[key])
        delta = got - want
        rel = abs(delta) / abs(want) if want else (0.0 if delta == 0 else float("inf"))
        worst = max(worst, rel)
        lines.append(f"  {key}: claimed {want:,.2f}  recomputed {got:,.2f}  "
                     f"delta {delta:+,.2f} ({rel:.4%})")
    unchecked = sorted(set(claimed) - set(recomputed))

    verdict = "AGREE" if worst <= REL_TOL else "DISAGREE"
    out = [f"VERDICT: {verdict}  (worst relative delta {worst:.4%}, "
           f"tolerance {REL_TOL:.2%})", *lines]
    if unchecked:
        out.append(f"  not recomputed: {', '.join(unchecked)} — the verdict covers "
                   f"only the figures above")
    commentary = [l for l in (proc.stdout or "").splitlines()
                  if l.strip() and not _RECOMPUTED.match(l)]
    if commentary:
        out += ["", "verifier commentary:"] + [f"  {l}" for l in commentary[:12]]
    out += ["", f"script: {script}", f"run: {run_id}"]
    return "\n".join(out)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_id")
    a = ap.parse_args(argv)
    try:
        print(execute(a.run_id))
    except Exception as e:
        print(f"Failed: {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
