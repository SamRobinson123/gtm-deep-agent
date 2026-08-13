"""Freeze the segment payload from a known-good dashboard HTML.

The per-account tier source (`rpt_cx.account_segment_quarterly`) was removed from
Synapse, so the live segment build collapses every opp to "Unassigned" (see
memory: account-segment-table-gone). Until a replacement tier source exists, the
dashboard serves the segment section from a frozen snapshot captured here.

Usage:
    uv run python scripts/freeze_segment_payload.py [path/to/coverage_dashboard.html]

Extracts payload["segment"] from the embedded <script id="coverage-data"> blob
and writes it to data/inputs/segment_payload_frozen.json, which
coverage_render.build_payload prefers over the live (broken) build.
"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SRC = REPO / "coverage_dashboard.html"
OUT = REPO / "data" / "inputs" / "segment_payload_frozen.json"

_DATA_RE = re.compile(
    r'<script id="coverage-data" type="application/json">(.*?)</script>', re.S
)


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    html = src.read_text(encoding="utf-8")
    m = _DATA_RE.search(html)
    if not m:
        raise SystemExit(f"No <script id='coverage-data'> blob found in {src}")
    payload = json.loads(m.group(1))
    seg = payload.get("segment")
    if not seg or not seg.get("quarters"):
        raise SystemExit(f"{src} has no populated segment payload")
    OUT.write_text(json.dumps(seg, ensure_ascii=False), encoding="utf-8")
    n_q = len(seg.get("quarters", {}))
    print(f"Wrote {OUT} ({n_q} quarters, segments={[s['canonical'] for s in seg['segments']]})")


if __name__ == "__main__":
    main()
