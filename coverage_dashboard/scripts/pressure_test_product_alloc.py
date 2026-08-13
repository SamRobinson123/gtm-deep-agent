"""Pressure-test: is open pipe & weeks 1-12 booked allocated correctly to products?

The product page pro-rates each multi-product opp's whole-opp snapshot
`Cal_IACV` across its families by SKU-NACV share (`_product_share_map` ->
`_attach_product_weight`), so weeks 1-12 open pipe and booked are split by
share; week-13 / live booked uses SKU-line `Product_NACV`. This script re-proves
that allocation and shows concrete multi-product opp drill-downs so the
distribution can be eyeballed.

Checks (assert on failure):
  A. Shares well-formed   - every opp's Sigma share == 1.0, no negative shares.
  B1. Pro-ration identity - Sigma w == Sigma Cal_IACV per (quarter, week, Stage)
                            on the exact snap the product page aggregates.
  B2. Page tie            - Sigma_products open/booked == page-1 total per
                            (quarter, week), to the dollar.
  C. No-SKU -> Tosca      - opps with no SKU rows default to Tosca (whole Cal_IACV).

Then prints, for the current quarter's latest week, the top multi-product opps
with their per-family split, the Sealights FY26 Q1 wk1 regression witness, and a
wks-1-12 (pro-rated) vs wk-13 (SKU-line live) booked contrast.

Run:  uv run python -m scripts.pressure_test_product_alloc   (needs VPN/Synapse)
"""

import pandas as pd

from backend.build_coverage import (
    FISCAL_YEARS,
    LIVE_BOOKED_END,
    LIVE_BOOKED_START,
    _force_utf8_stdout,
)
from backend.coverage_builder import (
    _attach_product_weight,
    _canon_product,
    _live_booked_by_week,
    _prepare_product_snap,
    _product_share_map,
    build_coverage,
    build_product_coverage,
)
from backend.snapshot import pull_live_booked, pull_opp_products, pull_snapshot
from backend.synapse import connect
from data.inputs.loaders import load_booking_team_mapping, load_product_targets, load_quarter_targets

DOLLAR_TOL = 1.0  # max acceptable rounding drift, in dollars


def _current_quarter() -> str:
    """Same FYxx Qn label `_pin_to_week_date` uses for the in-flight quarter."""
    now = pd.Timestamp.now()
    return "FY%02d Q%d" % (now.year % 100, (now.month - 1) // 3 + 1)


def _opp_family_table(opp_products: pd.DataFrame) -> pd.DataFrame:
    """One row per (opp, product): positive nacv and its share of the opp.
    Same positive-NACV basis as `_product_share_map`."""
    df = opp_products[["Opportunity_ID", "Product", "nacv"]].copy()
    df["product"] = _canon_product(df["Product"])
    df["pos"] = df["nacv"].clip(lower=0)
    g = df.groupby(["Opportunity_ID", "product"], as_index=False)["pos"].sum()
    tot = g.groupby("Opportunity_ID")["pos"].transform("sum")
    g = g[tot > 0].copy()
    g["share"] = g["pos"] / tot[tot > 0]
    return g.rename(columns={"pos": "nacv"})


def _check_shares(shares: pd.DataFrame, fam: pd.DataFrame) -> None:
    print("=" * 70)
    print("A. SHARES WELL-FORMED")
    print("=" * 70)
    per_opp = shares.groupby("Opportunity_ID")["share"].sum()
    worst = (per_opp - 1.0).abs().max()
    neg = int((shares["share"] < 0).sum())
    n_opps = per_opp.size
    n_multi = int((fam.groupby("Opportunity_ID")["product"].size() > 1).sum())
    print(f"  Opps with a share vector            : {n_opps:,}")
    print(f"  Multi-product opps                  : {n_multi:,}  "
          f"({n_multi / n_opps:.1%})")
    print(f"  Max |Sigma share - 1|               : {worst:.2e}")
    print(f"  Negative shares                     : {neg}")
    assert worst < 1e-9, "per-opp shares must sum to 1.0"
    assert neg == 0, "positive-NACV basis must not yield negative shares"
    print("  PASS\n")


def _check_proration_identity(snap_w: pd.DataFrame) -> None:
    print("=" * 70)
    print("B1. PRO-RATION IDENTITY  (Sigma w == Sigma Cal_IACV per quarter/week/Stage)")
    print("=" * 70)
    # Raw Cal_IACV is carried once per opp row; w fans it across families. Compare
    # the fanned weight sum to the de-duplicated opp-row Cal_IACV sum.
    gc = ["quarter", "week_of_quarter", "Stage"]
    w_sum = snap_w.groupby(gc)["w"].sum()
    raw = (snap_w.drop_duplicates(["Opportunity_ID", "quarter", "week_of_quarter", "Stage"])
           .groupby(gc)["Cal_IACV"].sum())
    diff = (w_sum - raw).abs()
    worst = diff.max()
    print(f"  Cells compared                      : {diff.size:,}")
    print(f"  Worst |Sigma w - Sigma Cal_IACV|    : ${worst:,.4f}")
    assert worst < DOLLAR_TOL, "pro-ration must preserve the per-cell total"
    print("  PASS\n")


def _check_page_tie(coverage: pd.DataFrame, coverage_product: pd.DataFrame) -> None:
    print("=" * 70)
    print("B2. PAGE TIE  (Sigma products == page-1 total per quarter/week)")
    print("=" * 70)
    gc = ["quarter", "week_of_quarter"]
    for col in ("open_pipe", "booked"):
        page1 = coverage.groupby(gc)[col].sum()
        prod = coverage_product.groupby(gc)[col].sum()
        diff = (page1 - prod).abs().dropna()
        worst = diff.max()
        where = diff.idxmax() if diff.size else None
        print(f"  {col:<10} worst diff               : ${worst:,.4f}  @ {where}")
        assert worst < DOLLAR_TOL, f"product {col} must tie page 1 per cell"
    print("  PASS\n")


def _check_unmatched(snap_w: pd.DataFrame, shares: pd.DataFrame) -> None:
    print("=" * 70)
    print("C. NO-SKU-ROW OPPS -> 'Other'  (leftover we can't attribute)")
    print("=" * 70)
    # Opps absent from the share map have no SKU rows at all; we leave them in
    # 'Other' (Sam, 2026-06-22 — don't default to a real product), keeping their
    # whole Cal_IACV (share == 1.0).
    share_opps = set(shares["Opportunity_ID"].unique())
    nosku = snap_w[~snap_w["Opportunity_ID"].isin(share_opps)]
    opps = nosku["Opportunity_ID"].nunique()
    total_opps = snap_w["Opportunity_ID"].nunique()
    bad_prod = int((nosku["product"] != "Other").sum())
    bad_val = int((nosku["w"] != nosku["Cal_IACV"]).sum())
    print(f"  Opps with no SKU rows (-> Other)    : {opps:,}  "
          f"({opps / total_opps:.1%} of opps)")
    print(f"  Rows not left in 'Other'            : {bad_prod}")
    print(f"  Rows where w != Cal_IACV            : {bad_val}")
    assert bad_prod == 0, "no-SKU opps must stay in 'Other'"
    assert bad_val == 0, "no-SKU opps must keep their whole Cal_IACV"
    print("  PASS\n")


def _fmt_m(x: float) -> str:
    return f"${x / 1e6:,.3f}M"


def _drilldown_examples(snap_w: pd.DataFrame, fam: pd.DataFrame, quarter: str, n: int = 5) -> None:
    print("=" * 70)
    print(f"DRILL-DOWN: top {n} multi-product OPEN opps  ({quarter}, latest week)")
    print("=" * 70)
    q = snap_w[(snap_w["quarter"] == quarter) & (snap_w["Stage"] == "Open")]
    if q.empty:
        print(f"  No open rows for {quarter}.\n")
        return
    week = int(q["week_of_quarter"].max())
    qw = q[q["week_of_quarter"] == week]
    # Whole-opp Cal_IACV (de-dup the fanned rows), restricted to multi-product opps.
    multi = fam.groupby("Opportunity_ID")["product"].size()
    multi = set(multi[multi > 1].index)
    whole = (qw.drop_duplicates("Opportunity_ID")[["Opportunity_ID", "Cal_IACV"]]
             .query("Opportunity_ID in @multi")
             .sort_values("Cal_IACV", ascending=False))
    for opp_id in whole["Opportunity_ID"].head(n):
        rows = qw[qw["Opportunity_ID"] == opp_id].sort_values("w", ascending=False)
        whole_v = rows["Cal_IACV"].iloc[0]
        print(f"\n  Opp {opp_id}   week={week}   whole-opp Cal_IACV={_fmt_m(whole_v)} (open)")
        print(f"    {'family':<20}{'nacv':>14}{'share':>9}{'allocated open':>18}")
        f = fam[fam["Opportunity_ID"] == opp_id].sort_values("share", ascending=False)
        for _, fr in f.iterrows():
            alloc = whole_v * fr["share"]
            print(f"    {fr['product']:<20}{_fmt_m(fr['nacv']):>14}"
                  f"{fr['share']:>9.3f}{_fmt_m(alloc):>18}")
        print(f"    {'-' * 59}")
        print(f"    {'TOTAL':<20}{'':>14}{f['share'].sum():>9.3f}"
              f"{_fmt_m(whole_v * f['share'].sum()):>18}  == whole-opp")
    print()


def _sealights_witness(snap_w: pd.DataFrame) -> None:
    print("=" * 70)
    print("REGRESSION WITNESS: Sealights FY26 Q1 wk1 open pipe (pro-rated)")
    print("=" * 70)
    sl = snap_w[(snap_w["quarter"] == "FY26 Q1") & (snap_w["week_of_quarter"] == 1)
                & (snap_w["Stage"] == "Open") & (snap_w["product"] == "Sealights")]
    print(f"  Pro-rated Sealights open pipe       : {_fmt_m(sl['w'].sum())}")
    print("  (docstring: ~$0.46M under old MIN() -> ~$6.8M pro-rated)\n")


def _booked_method_contrast(snap_w: pd.DataFrame, fam: pd.DataFrame,
                            live_booked: pd.DataFrame, mapping: pd.DataFrame,
                            current_quarter: str) -> None:
    print("=" * 70)
    print("BOOKED METHOD CONTRAST: wks 1-12 (pro-rated) vs wk 13 (SKU-line live)")
    print("=" * 70)
    # A multi-product opp that booked (Closed Won in the snapshot) in a COMPLETED
    # quarter and also appears with >1 family in the live pull.
    won = snap_w[(snap_w["Stage"] == "Closed Won") & (snap_w["quarter"] != current_quarter)]
    multi = fam.groupby("Opportunity_ID")["product"].size()
    multi = set(multi[multi > 1].index)
    live = live_booked.copy()
    live["product"] = _canon_product(live["Product"])
    live_multi = set(live.groupby("Opportunity_ID")["product"].nunique()
                     .pipe(lambda s: s[s > 1]).index)
    cand = won[won["Opportunity_ID"].isin(multi & live_multi)]
    if cand.empty:
        print("  No multi-product booked opp found in both snapshot and live.\n")
        return
    opp_id = cand.sort_values("Cal_IACV", ascending=False)["Opportunity_ID"].iloc[0]
    whole_v = cand[cand["Opportunity_ID"] == opp_id]["Cal_IACV"].iloc[0]
    print(f"\n  Opp {opp_id}   whole-opp Closed-Won Cal_IACV={_fmt_m(whole_v)}")
    f = fam[fam["Opportunity_ID"] == opp_id].set_index("product")
    lv = (live[live["Opportunity_ID"] == opp_id]
          .groupby("product")["Product_NACV"].sum())
    prods = sorted(set(f.index) | set(lv.index))
    print(f"    {'family':<20}{'wks1-12 pro-rated':>20}{'wk13 SKU-line live':>22}")
    for p in prods:
        prorated = whole_v * f["share"].get(p, 0.0)
        skuline = lv.get(p, 0.0)
        print(f"    {p:<20}{_fmt_m(prorated):>20}{_fmt_m(skuline):>22}")
    print(f"    {'-' * 62}")
    print(f"    {'TOTAL':<20}{_fmt_m(whole_v):>20}{_fmt_m(lv.sum()):>22}")
    print("  (the two methods can diverge per family; 'Other' can go negative live)\n")


def main() -> None:
    _force_utf8_stdout()
    with connect() as conn:
        print("Pulling booking-team mapping...")
        mapping = load_booking_team_mapping(conn)
        snapshots = []
        for label, fy_start, fy_end in FISCAL_YEARS:
            snap_start = (pd.Timestamp(fy_start) - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
            print(f"Pulling snapshot for {label}...")
            snapshots.append(pull_snapshot(conn, fy_start, fy_end, snap_start=snap_start))
        print("Pulling live bookings...")
        live_booked = pull_live_booked(conn, LIVE_BOOKED_START, LIVE_BOOKED_END)
        print("Pulling per-opp product families...")
        opp_products = pull_opp_products(conn)

    snapshot = pd.concat(snapshots, ignore_index=True)
    targets = load_quarter_targets()
    product_targets = load_product_targets()

    # Rebuild the exact frame the product page aggregates, then attach SKU-mix
    # weights — the same two calls `build_product_coverage` makes internally.
    shares = _product_share_map(opp_products)
    snap_w = _attach_product_weight(_prepare_product_snap(snapshot, mapping), shares)
    fam = _opp_family_table(opp_products)

    print()
    _check_shares(shares, fam)
    _check_proration_identity(snap_w)

    coverage = build_coverage(snapshot, mapping, targets, live_booked=live_booked)
    coverage_product = build_product_coverage(
        snapshot, mapping, product_targets, live_booked=live_booked, opp_products=opp_products)
    _check_page_tie(coverage, coverage_product)
    _check_unmatched(snap_w, shares)

    cq = _current_quarter()
    _drilldown_examples(snap_w, fam, cq)
    _sealights_witness(snap_w)
    _booked_method_contrast(snap_w, fam, live_booked, mapping, cq)

    print("All checks passed.")


if __name__ == "__main__":
    main()
