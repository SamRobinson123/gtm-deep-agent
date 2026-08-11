"""Synapse -> cached parquet.

Materialized from docs/analysis/gtm-dashboard.md (the `pipeline/pull.py` section).

Auth note: authentication does NOT come from the connection string. The ODBC driver
has no "use the CLI's cached login" mode, so we fetch a token from the user's
`az login` session ourselves and hand it to pyodbc. Consequences:
  - the agent holds no credential of its own; it queries as the signed-in user
  - a stale `az login` is the most likely failure, and it surfaces as an auth error
"""
from __future__ import annotations

import struct

import pandas as pd

from agent import sqlguard
from pipeline import config, queries

SQL_COPT_SS_ACCESS_TOKEN = 1256  # pyodbc connection attribute for a raw AAD access token


def get_conn():
    import pyodbc
    from azure.identity import AzureCliCredential

    if not config.SYNAPSE_CONN_STR:
        raise RuntimeError("SYNAPSE_CONN_STR is not set — check .env")
    token = AzureCliCredential().get_token("https://database.windows.net/.default").token
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    return pyodbc.connect(config.SYNAPSE_CONN_STR, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})


def cached_path(name: str):
    _, filename, _ = queries.get(name)
    return config.DATA / filename


def run_query(name: str, conn=None) -> pd.DataFrame:
    """Execute one registry query. `name` must be one of the four.

    sqlguard re-asserts read-only at call time — defence in depth against a bad edit
    to queries.py, not a check on anything the agent authored.
    """
    sql, _, _ = queries.get(name)
    sqlguard.assert_read_only(sql, name)
    own = conn is None
    conn = conn or get_conn()
    try:
        df = pd.read_sql(sql, conn)
    finally:
        if own:
            conn.close()
    # The snapshot table emits each row twice; the others are already unique.
    return df.drop_duplicates() if name in ("sku_nacv", "snapshot") else df


def pull_one(name: str, force: bool = False) -> dict:
    """Pull one query to parquet. Cache-first unless `force`.

    CLAUDE.md: never re-pull data that cached parquet can answer. That rule is
    mechanical here rather than a matter of remembering it.
    """
    path = cached_path(name)
    if path.exists() and not force:
        return {"query": name, "path": str(path), "cached": True, "rows": None}
    df = run_query(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return {"query": name, "path": str(path), "cached": False, "rows": len(df)}


def pull_all(force: bool = False) -> list[dict]:
    conn = get_conn()
    out = []
    try:
        for name in queries.QUERY_NAMES:
            path = cached_path(name)
            if path.exists() and not force:
                out.append({"query": name, "path": str(path), "cached": True, "rows": None})
                continue
            df = run_query(name, conn=conn)
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(path, index=False)
            out.append({"query": name, "path": str(path), "cached": False, "rows": len(df)})
    finally:
        conn.close()
    return out


if __name__ == "__main__":
    for r in pull_all():
        state = "cached" if r["cached"] else f"{r['rows']} rows"
        print(f"{r['query']:10} {state:>14}  {r['path']}")
