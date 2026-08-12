"""PreToolUse guards — v2.

The v1 hook was a cage: reads confined to project subdirs, Bash limited to an
`az` prefix allowlist. v2 deletes both, because generic capability is the point,
and replaces them with a much narrower rule: there are things the agent may
never MODIFY, and credentials it may never read.

What survives from v1: `docs/superpowers/` stays read-denied (it is decision
history, not context) and credential filenames stay denied.

What replaces the allowlist: approval mode is the runtime control for Bash and
for repo edits; `.claude/settings.json` decides what runs without a prompt. The
hook is the last word only on things no approval should be able to grant.
"""
from __future__ import annotations

import pytest

from agent import hooks


def denied(result):
    """A hook result that is a denial, with its reason."""
    if not result:
        return None
    out = result.get("hookSpecificOutput", {})
    return out.get("permissionDecisionReason") if out.get("permissionDecision") == "deny" else None


# --- 5. the context corpus is human-curated -----------------------------------

@pytest.mark.parametrize("path", [
    "docs/models/pipe-create.md",
    "docs/README.md",
    "docs/analysis/slip.md",
    "CLAUDE.md",
])
def test_the_agent_cannot_edit_its_own_constitution(path):
    """An agent that can rewrite the docs it reasons from can drift silently and
    leave no trace of having done so. Changes go through workspace/proposals/
    for a human to apply."""
    reason = denied(hooks.check_write(path))
    assert reason, f"{path} must not be writable"
    assert "proposal" in reason.lower(), (
        "the denial has to say what to do instead, or the agent just retries")


def test_the_agent_cannot_widen_its_own_permissions():
    """Self-widening permissions is the classic failure. settings.json decides
    what runs without an approval prompt, so it is not the agent's to edit."""
    assert denied(hooks.check_write(".claude/settings.json"))


@pytest.mark.parametrize("path", [".env", ".env.local", ".env.production"])
def test_credentials_are_never_writable(path):
    assert denied(hooks.check_write(path))


def test_inputs_stay_read_only():
    assert denied(hooks.check_write("data/Target_Monthly.csv"))
    assert denied(hooks.check_write("data/snapshot.parquet"))


# --- 6. everything else is editable, with approval ----------------------------

@pytest.mark.parametrize("path", [
    "workspace/scratch/explore.py",
    "workspace/notes/journal.md",
    "workspace/proposals/claude-md-v2.patch",
    "workspace/exports/report.xlsx",
])
def test_the_workspace_is_the_agents_own_space(path):
    assert hooks.check_write(path) is None


@pytest.mark.parametrize("path", [
    "pipeline/config.py",
    "agent/waterfall.py",
    "tests/test_waterfall.py",
])
def test_the_agent_may_edit_its_own_code(path):
    """The 'build' half of think-and-build. The hook does not gate this —
    approval does, at runtime. Denying it here would make the agent unable to
    fix itself even when the human wants it to."""
    assert hooks.check_write(path) is None


# --- 7. run immutability ------------------------------------------------------

def test_writes_into_an_existing_run_are_denied(tmp_path, monkeypatch):
    """Lineage is immutable. A run that can be edited after the fact cannot be
    used to defend a number."""
    runs = tmp_path / "runs"
    (runs / "2026-08-11T000000Z_abc123").mkdir(parents=True)
    monkeypatch.setattr(hooks.config, "RUNS", runs)

    assert denied(hooks.check_write(str(runs / "2026-08-11T000000Z_abc123" / "manifest.json")))
    assert denied(hooks.check_write(str(runs / "2026-08-11T000000Z_abc123" / "new_file.csv")))


def test_a_run_that_does_not_exist_yet_is_not_blocked(tmp_path, monkeypatch):
    """lineage.Run creates its directory through Python file I/O, which never
    reaches this hook — but a path under a run id that does not exist yet must
    not be pre-emptively denied either, or the guard would depend on ordering."""
    runs = tmp_path / "runs"
    runs.mkdir(parents=True)
    monkeypatch.setattr(hooks.config, "RUNS", runs)
    assert hooks.check_write(str(runs / "2026-08-11T999999Z_new" / "out.csv")) is None


def test_lineage_can_still_create_and_populate_a_run(tmp_path):
    """The guard must not have broken the thing it protects. This exercises the
    real Run object, which is how every reported figure gets recorded."""
    from agent import lineage
    with lineage.Run(quarter_start="2026-07-01", runs_dir=tmp_path) as run:
        out = run.dir / "result.csv"
        out.write_text("a,b\n1,2\n", encoding="utf-8")
        run.add_output(out, rows=1)
    assert (tmp_path / run.run_id / "manifest.json").exists()


# --- 8. credentials, on both surfaces -----------------------------------------

@pytest.mark.parametrize("path", [".env", "id_rsa", "credentials", ".env.local"])
def test_credential_files_are_never_readable(path):
    assert denied(hooks.check_read(path))


@pytest.mark.parametrize("command", [
    "cat .env",
    "type .env",
    "python -c \"print(open('.env').read())\"",
    "cp ~/.ssh/id_rsa /tmp/x",
    "grep SYNAPSE .env.local",
])
def test_bash_cannot_reach_credentials_either(command):
    """v1 blocked this with a prefix allowlist. v2's Bash is general, so the
    denial matches on the credential NAME instead. Crude and defeatable by an
    agent that wants to defeat it — the real control is that the connection
    string is not in the environment (test_env_isolation). This converts an
    accident into a visible denial."""
    assert denied(hooks.check_bash(command))


@pytest.mark.parametrize("command", [
    "python workspace/scratch/explore.py",
    "python -m pytest tests/ -q",
    "python -m pipeline.targets_cli --quarter 'Q3 FY26'",
    "git status",
    "ls workspace/runs",
    "az account show",
])
def test_general_bash_is_allowed(command):
    """The v1 allowlist is deleted. This is the point of v2: a thinking agent
    writes and runs its own scripts."""
    assert hooks.check_bash(command) is None


def test_shell_chaining_is_no_longer_blocked_for_its_own_sake():
    """v1 denied `&&` and `|` because they defeated prefix matching. There is no
    prefix matching now, so a pipeline is just a shell command."""
    assert hooks.check_bash("ls workspace | head -5") is None
    assert hooks.check_bash("cd workspace/scratch && python x.py") is None


def test_chaining_still_cannot_smuggle_a_credential_read():
    """...but the credential rule applies to the whole command string, so
    chaining does not get around it."""
    assert denied(hooks.check_bash("az account show && cat .env"))


# --- what survives from v1 ----------------------------------------------------

def test_design_specs_stay_read_denied():
    """docs/superpowers/ records decisions including rejected ones. Citing it as
    fact about the data is exactly the failure docs/README.md warns about — so
    this denial outlives the read confinement that used to surround it."""
    reason = denied(hooks.check_read("docs/superpowers/specs/2026-08-11-gtm-deep-agent-v2-design.md"))
    assert reason and "superpowers" in reason.lower()


def test_archive_is_read_denied_with_docs_as_the_alternative():
    """archive/ holds context retired from docs/ (dashboard era, 2026-08-12).
    Location is status: the banner text alone stops nothing — this does. The
    reason must point the agent back at docs/ instead."""
    for p in ("archive/README.md", "archive/docs/analysis/gtm-dashboard.md"):
        reason = denied(hooks.check_read(p))
        assert reason and "docs/" in reason


def test_archive_is_write_denied():
    """History is not edited — an archive that can be revised afterwards is
    not an archive."""
    assert denied(hooks.check_write("archive/docs/tables/call-signals.md"))
    assert denied(hooks.check_write("archive/new-file.md"))


def test_reads_outside_the_project_are_now_allowed():
    """Read confinement is deleted. The agent may look at anything except
    credentials — it needs to read its own environment to work in it."""
    assert hooks.check_read("C:/Windows/System32/drivers/etc/hosts") is None


def test_traversal_still_resolves_before_the_credential_check():
    """Confinement is gone, but the credential rule must not be defeated by a
    path that only looks harmless."""
    assert denied(hooks.check_read("docs/../.env"))
    assert denied(hooks.check_write("workspace/../CLAUDE.md"))


# --- the router ---------------------------------------------------------------

async def _hook(tool, args):
    return await hooks.pre_tool_use({"tool_name": tool, "tool_input": args}, "id", None)


@pytest.mark.anyio
async def test_router_applies_the_right_guard_per_tool():
    assert denied(await _hook("Write", {"file_path": "CLAUDE.md"}))
    assert denied(await _hook("Edit", {"file_path": "docs/README.md"}))
    assert denied(await _hook("Read", {"file_path": ".env"}))
    assert denied(await _hook("Bash", {"command": "cat .env"}))
    assert not denied(await _hook("Write", {"file_path": "workspace/scratch/x.py"}))
    assert not denied(await _hook("Bash", {"command": "python -m pytest"}))


@pytest.fixture
def anyio_backend():
    return "asyncio"
