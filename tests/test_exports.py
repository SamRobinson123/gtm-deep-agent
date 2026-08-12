"""Exports — the write boundary, the no-overwrite rule, and the formatting contract.

The boundary tests matter most. This is the agent's only ability to write files,
and a filename can reach it from a prompt, so "cannot escape workspace/exports/"
has to be a property of the code rather than an intention.
"""
from __future__ import annotations

import pandas as pd
import pytest

from agent import exports


# --- the write boundary -------------------------------------------------------

@pytest.mark.parametrize("hostile", [
    "../../etc/passwd",
    "..\\..\\Windows\\System32\\evil",
    "/absolute/path/thing",
    "C:\\Dev V2\\gtm-deep-agent\\data\\Target_Monthly",
    "data/Target_Monthly",
    "....//....//escape",
    "con",                       # a reserved Windows device name, still just a stem
    "  ../  ",
])
def test_a_hostile_name_cannot_escape_the_exports_directory(hostile, tmp_path, monkeypatch):
    monkeypatch.setattr(exports, "EXPORTS", tmp_path)
    p = exports.export_path(hostile, ".xlsx")
    assert p.parent.resolve() == tmp_path.resolve()
    assert ".." not in p.parts


def test_an_empty_or_all_punctuation_name_still_produces_a_file(tmp_path, monkeypatch):
    """`safe_stem` strips everything from "..." — it must not yield a bare suffix
    like ".xlsx", which is a hidden file rather than a deliverable."""
    monkeypatch.setattr(exports, "EXPORTS", tmp_path)
    for name in ("", "...", "///", None):
        p = exports.export_path(name, ".xlsx")
        assert p.stem and p.stem != ""
        assert p.name != ".xlsx"


def test_confine_rejects_a_path_outside_exports(tmp_path, monkeypatch):
    """The last line of defence, tested directly — safe_stem should make this
    unreachable, which is exactly why it needs its own test."""
    monkeypatch.setattr(exports, "EXPORTS", tmp_path)
    with pytest.raises(ValueError, match="refusing to write outside"):
        exports._confine(tmp_path.parent / "escaped.xlsx")


# --- never overwrite ----------------------------------------------------------

def test_a_collision_gets_a_date_then_a_counter(tmp_path, monkeypatch):
    """CLAUDE.md: never overwrite an existing export. Someone may already have
    opened or sent the previous one."""
    monkeypatch.setattr(exports, "EXPORTS", tmp_path)

    first = exports.export_path("report", ".xlsx")
    first.write_text("original")

    second = exports.export_path("report", ".xlsx")
    assert second != first
    second.write_text("second")

    third = exports.export_path("report", ".xlsx")
    assert third not in (first, second)

    assert first.read_text() == "original"      # untouched


# --- formatting contract ------------------------------------------------------

def test_dollar_and_rate_columns_get_the_right_number_format():
    assert exports._fmt_for("pipe_create_target") == "#,##0"
    assert exports._fmt_for("sales_cycle_tail_from_earlier_quarters") == "#,##0"
    assert exports._fmt_for("expected_from_existing_pipe") == "#,##0"
    assert exports._fmt_for("closed_won") == "#,##0"
    assert exports._fmt_for("pre_q_win_rate") == "0.0%"
    assert exports._fmt_for("in_quarter_win_rate") == "0.0%"
    assert exports._fmt_for("q0_weight") == "0.0%"
    assert exports._fmt_for("Territory") is None
    assert exports._fmt_for("binding") is None


def test_slip_rate_is_a_rate_not_a_dollar_amount():
    """`slip` is in the money pattern and `slip_rate` must not be caught by it."""
    assert exports._fmt_for("slip_rate") == "0.0%"
    assert exports._fmt_for("slip_inflow") == "#,##0"


def test_write_sheet_freezes_the_header_and_survives_missing_values(tmp_path, monkeypatch):
    """A NaN in a text column used to crash the auto-width calculation: pandas 3's
    astype(str) keeps NaN as MISSING rather than the text "nan", so iterating the
    column handed back a float and len() raised."""
    monkeypatch.setattr(exports, "EXPORTS", tmp_path)
    df = pd.DataFrame({
        "Territory": ["AMS Corporate", None],
        "pipe_create_target": [1234567.89, float("nan")],
        "pre_q_win_rate": [0.1472, None],
        "outlier_reasons": [None, "Yield below threshold"],
    })
    path = exports.export_path("fixture", ".xlsx")
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        ws = exports.write_sheet(w, df, "Q3 FY26")
        assert ws.freeze_panes == "A2"

    from openpyxl import load_workbook
    wb = load_workbook(path)
    sheet = wb["Q3 FY26"]
    assert sheet["B2"].number_format == "#,##0"
    assert sheet["C2"].number_format == "0.0%"
    assert sheet["A1"].font.bold


def test_a_long_sheet_name_is_truncated_to_excels_limit(tmp_path, monkeypatch):
    """openpyxl raises on >31 chars, which would fail the whole export."""
    monkeypatch.setattr(exports, "EXPORTS", tmp_path)
    path = exports.export_path("longname", ".xlsx")
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        ws = exports.write_sheet(w, pd.DataFrame({"a": [1]}), "x" * 60)
    assert len(ws.title) <= 31
