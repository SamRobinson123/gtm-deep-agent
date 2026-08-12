"""Refresh cached parquet from Synapse — the one approvable command for it.

    python -m pipeline.pull_cli                      # status: age of every cache
    python -m pipeline.pull_cli sku_nacv snapshot    # pull these (cache-first)
    python -m pipeline.pull_cli --force sku_nacv snapshot
    python -m pipeline.pull_cli --force --all

Exists so that "recalculate as of today" is one visible, approvable command
rather than a scratch script: the freshness rule in agent/options.py routes
here. Cache-first by default (CLAUDE.md: never re-pull what cache can answer);
`--force` is the deliberate staleness override. Needs VPN; triggers the MFA
flow via pull.get_conn() when the token is stale.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pandas as pd

from pipeline import pull, queries


def _max_date(path):
    """Max snapshot_date in a parquet, reading only that column."""
    try:
        s = pd.read_parquet(path, columns=["snapshot_date"])["snapshot_date"]
        return str(pd.to_datetime(s).max().date())
    except Exception:
        return None


def status_lines() -> list[str]:
    lines = []
    for name in queries.QUERY_NAMES:
        path = pull.cached_path(name)
        if not path.exists():
            lines.append(f"{name:14} MISSING")
            continue
        age_h = (datetime.now(timezone.utc)
                 - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)).total_seconds() / 3600
        mx = _max_date(path)
        lines.append(f"{name:14} pulled {age_h:5.1f}h ago"
                     + (f"  max snapshot_date {mx}" if mx else ""))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("names", nargs="*", help=f"queries to pull: {', '.join(queries.QUERY_NAMES)}")
    ap.add_argument("--force", action="store_true", help="re-pull even when cached")
    ap.add_argument("--all", action="store_true", dest="pull_all", help="every registry query")
    a = ap.parse_args(argv)

    if not a.names and not a.pull_all:
        print("Cache status (no pull requested — name queries or pass --all):")
        for line in status_lines():
            print("  " + line)
        return 0

    names = list(queries.QUERY_NAMES) if a.pull_all else a.names
    bad = [n for n in names if n not in queries.QUERY_NAMES]
    if bad:
        print(f"Unknown quer{'y' if len(bad) == 1 else 'ies'}: {', '.join(bad)}. "
              f"Known: {', '.join(queries.QUERY_NAMES)}")
        return 1

    try:
        conn = pull.get_conn()
    except Exception as e:
        print(f"Connection failed: {type(e).__name__}: {e}\n"
              f"Check VPN, then az_login_status / azure_login for the MFA flow.")
        return 1
    try:
        for name in names:
            path = pull.cached_path(name)
            if path.exists() and not a.force:
                print(f"{name:14} cached — pass --force to refresh  {path}")
                continue
            df = pull.run_query(name, conn=conn)
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(path, index=False)
            print(f"{name:14} {len(df):>9,} rows  {path}")
    finally:
        conn.close()

    print("\nAfter refresh:")
    for line in status_lines():
        print("  " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
