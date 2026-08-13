"""Standalone analysis: how a CURRENT-tier segment definition shifts the tiers.

The retired tier source (`rpt_cx.account_segment_quarterly`) gave each account
its earliest-quarter / INITIAL tier. A proposed replacement reads tiers from
`[sfdc_trf].[account_live]` as `COALESCE(Current_Segment__c, X2019_Segment_expected__c)`
— a point-in-time CURRENT tier. This script quantifies, entirely within
account_live, how moving to that definition reshapes the tier population.

It compares three definitions per account:
  - X2019  = X2019_Segment_expected__c  (fixed ~original baseline)
  - Current = Current_Segment__c        (today's tier)
  - COALESCE = Current, falling back to X2019 where Current is NULL (the proposal)

Outputs: NULL coverage per column, tier distribution under each definition, the
X2019->COALESCE transition matrix (who moves where), and a net up/down/flat
summary. This is read-only and does NOT touch the dashboard pipeline.

Source of account_live, in order of preference:
  1. --excel PATH    read an exported workbook/CSV (e.g. "Segment Check.xlsx")
  2. live Synapse    [sfdc_trf].[account_live] via backend.synapse.connect()

Usage:
    uv run python scripts/segment_tier_shift_analysis.py                  # live pull
    uv run python scripts/segment_tier_shift_analysis.py --excel "Segment Check.xlsx"
    uv run python scripts/segment_tier_shift_analysis.py --csv-out movers.csv
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
COLS = ["Id", "Current_Segment__c", "X2019_Segment_expected__c"]
QUERY = (
    "SELECT Id, Current_Segment__c, X2019_Segment_expected__c "
    "FROM [sfdc_trf].[account_live]"
)


def _canon(s: pd.Series) -> pd.Series:
    """'tier 1' / ' Tier 1 ' -> 'Tier 1'; blanks -> NA."""
    out = s.astype("string").str.strip().str.title()
    return out.mask(out.isin(["", "None", "Nan"]))


def load_account_live(excel: str | None) -> pd.DataFrame:
    if excel:
        path = Path(excel)
        if not path.is_absolute():
            path = REPO / path
        if path.suffix.lower() == ".csv":
            df = pd.read_csv(path, usecols=COLS)
        else:
            df = pd.read_excel(path, usecols=COLS)
        print(f"Source: {path.name} ({len(df):,} rows)")
        if len(df) == 10000:
            print("  WARNING: exactly 10,000 rows — likely a capped SFDC report, "
                  "not the full account universe.")
        return df
    # live pull
    sys.path.insert(0, str(REPO))
    from backend.synapse import connect  # noqa: E402
    with connect() as conn:
        df = pd.read_sql(QUERY, conn)
    print(f"Source: [sfdc_trf].[account_live] live pull ({len(df):,} rows)")
    return df


def _dist(s: pd.Series) -> pd.DataFrame:
    n = s.notna().sum()
    vc = s.value_counts(dropna=False)
    return pd.DataFrame({"count": vc, "pct": (vc / len(s) * 100).round(1)})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", help="path to an account_live export (xlsx/csv)")
    ap.add_argument("--csv-out", help="write per-account tier moves to this CSV")
    args = ap.parse_args()

    df = load_account_live(args.excel)
    df["X2019"] = _canon(df["X2019_Segment_expected__c"])
    df["Current"] = _canon(df["Current_Segment__c"])
    df["COALESCE"] = df["Current"].fillna(df["X2019"])

    n = len(df)
    print("\n=== 1. NULL coverage ===")
    for col in ("X2019", "Current", "COALESCE"):
        nulls = df[col].isna().sum()
        print(f"  {col:9} NULL: {nulls:>6,} ({nulls/n*100:4.1f}%)")
    rescued = (df["Current"].isna() & df["X2019"].notna()).sum()
    print(f"  COALESCE rescues {rescued:,} accounts where Current is NULL.")

    print("\n=== 2. Tier distribution by definition ===")
    dist = pd.concat(
        {c: _dist(df[c])["count"] for c in ("X2019", "Current", "COALESCE")}, axis=1
    ).fillna(0).astype(int)
    share = (dist / dist.sum() * 100).round(1)
    show = dist.astype(str) + "  (" + share.astype(str) + "%)"
    print(show.to_string())

    print("\n=== 3. Transition: X2019 (rows) -> COALESCE (cols) ===")
    tx = pd.crosstab(df["X2019"].fillna("<NA>"), df["COALESCE"].fillna("<NA>"),
                     margins=True, margins_name="Total")
    print(tx.to_string())

    print("\n=== 4. Net movement (X2019 -> COALESCE) ===")
    both = df.dropna(subset=["X2019", "COALESCE"])
    tier_rank = {"Tier 1": 1, "Tier 2": 2, "Tier 3": 3}
    o = both["X2019"].map(tier_rank)
    c = both["COALESCE"].map(tier_rank)
    moved = both["X2019"] != both["COALESCE"]
    up = (c < o).sum()    # lower rank number = higher tier = graduated up
    down = (c > o).sum()
    flat = (~moved).sum()
    print(f"  accounts compared: {len(both):,}")
    print(f"  unchanged: {flat:,} ({flat/len(both)*100:.1f}%)")
    print(f"  moved:     {moved.sum():,} ({moved.sum()/len(both)*100:.1f}%)  "
          f"[up {up:,} / down {down:,}]")

    if args.csv_out:
        out = df.loc[df["X2019"] != df["COALESCE"],
                     ["Id", "X2019", "Current", "COALESCE"]]
        out_path = Path(args.csv_out)
        out.to_csv(out_path, index=False)
        print(f"\nWrote {len(out):,} movers to {out_path}")


if __name__ == "__main__":
    main()
