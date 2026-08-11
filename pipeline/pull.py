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


DB_SCOPE = "https://database.windows.net/.default"

# Azure AD codes meaning "the user must re-authenticate interactively". MFA expiry
# (50078) is the common one and looks like a connection failure if not recognised.
_INTERACTIVE_CODES = ("AADSTS50078", "AADSTS50076", "AADSTS50079", "AADSTS700082",
                      "interaction_required", "invalid_grant", "Please run 'az login'")


def az_path():
    """Resolve the Azure CLI. On Windows `az` is az.CMD; subprocess cannot launch a
    .cmd by bare name, and the resulting FileNotFoundError reads as 'not installed'."""
    import shutil
    return shutil.which("az")


def needs_interactive_login(err: Exception) -> bool:
    msg = str(err)
    return any(c in msg for c in _INTERACTIVE_CODES)


def interactive_login(timeout=300, tenant=None):
    """Run `az login` for the DATABASE scope. Opens the browser for MFA.

    Scope matters: a general `az login` can leave MFA unsatisfied for the database
    audience, which then surfaces as AADSTS50078 at query time rather than as a
    login prompt.
    """
    import subprocess
    az = az_path()
    if not az:
        raise RuntimeError("Azure CLI is not installed or not on PATH.")
    cmd = [az, "login", "--scope", DB_SCOPE, "--only-show-errors"]
    if tenant:
        cmd[2:2] = ["--tenant", tenant]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"az login failed: {(r.stderr or '').strip()[:400]}")
    return True


class AzureAuthError(RuntimeError):
    """Could not obtain a Synapse token."""


def _token():
    """Fetch a database-scope access token by invoking the Azure CLI directly.

    Deliberately NOT azure-identity's AzureCliCredential: that discovers `az`
    through `cmd /c az ...`, which fails under Git Bash and other non-cmd shells
    with a generic "Failed to invoke the Azure CLI" — indistinguishable from a real
    auth problem. Resolving the path ourselves makes it work regardless of shell,
    and surfaces the true Azure AD error (e.g. AADSTS50078) instead of masking it.
    """
    import json as _json
    import subprocess

    az = az_path()
    if not az:
        raise AzureAuthError("Azure CLI is not installed or not on PATH.")
    r = subprocess.run(
        [az, "account", "get-access-token", "--scope", DB_SCOPE, "--output", "json"],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        raise AzureAuthError((r.stderr or r.stdout or "az returned no output").strip())
    try:
        return _json.loads(r.stdout)["accessToken"]
    except Exception as e:
        raise AzureAuthError(f"could not parse token response: {e}")


def get_conn(auto_login: bool = True, on_status=None):
    """Connect to Synapse, triggering interactive MFA if the session has expired.

    `auto_login` makes re-auth automatic: rather than failing with an opaque
    credential error, the browser opens for MFA and the connection is retried.
    `on_status` receives short progress strings so a UI can show what is happening.
    """
    import pyodbc

    if not config.SYNAPSE_CONN_STR:
        raise RuntimeError("SYNAPSE_CONN_STR is not set — check .env")

    def say(msg):
        if on_status:
            on_status(msg)

    try:
        token = _token()
    except Exception as e:
        if not (auto_login and needs_interactive_login(e)):
            raise
        say("Azure sign-in required — opening your browser for MFA…")
        interactive_login()
        say("Signed in. Retrying…")
        token = _token()

    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    return pyodbc.connect(config.SYNAPSE_CONN_STR, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})


def auth_status() -> dict:
    """Whether a Synapse token can be obtained right now, without side effects."""
    az = az_path()
    if not az:
        return {"ok": False, "state": "no_cli", "detail": "Azure CLI not installed or not on PATH."}
    try:
        _token()
        return {"ok": True, "state": "ready", "detail": "Signed in for the Synapse scope."}
    except Exception as e:
        if needs_interactive_login(e):
            return {"ok": False, "state": "mfa_required",
                    "detail": "Sign-in expired for the Synapse database scope. MFA needed."}
        return {"ok": False, "state": "error", "detail": str(e)[:300]}


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
