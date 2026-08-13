"""Ad-hoc analysis: what proportion of opportunities carry multiple products?

Counts distinct *canonical* product families per opportunity over the current
`Period = 'Period_1'` snapshot (same source/taxonomy as the product page), then
reports how many opps are multi-product and how much NACV sits in them.

Run:  uv run python -m scripts.analyze_multi_product_opps          # open pipe
      uv run python -m scripts.analyze_multi_product_opps booked   # Closed Won
"""

import sys

import pandas as pd

from backend.synapse import connect
from backend.snapshot import pull_opp_products, SQL_DIR
from backend.coverage_builder import _canon_product

# Same Closed Won stages as live_booked.sql / snapshot.sql.
_CLOSED_WON = "('6 - Closed/Pending','Closed Won','Stage 5 - Closed Won')"


def _pull_closed_won(conn) -> pd.DataFrame:
    """opp_products.sql restricted to Closed Won — the only added filter, so the
    booked numbers stay apples-to-apples with the open-pipe run. (NOTE: still
    looser than live_booked.sql, which also applies deal-type / booking-team
    filters, so totals here won't equal the official booked figure.)"""
    sql = (SQL_DIR / "opp_products.sql").read_text()
    inject = f"  AND N.StageName IN {_CLOSED_WON}\n  AND N.NACV_USD != 0"
    sql = sql.replace("  AND N.NACV_USD != 0", inject, 1)
    return pd.read_sql(sql, conn)


def main() -> None:
    booked = len(sys.argv) > 1 and sys.argv[1].lower() == "booked"
    with connect() as conn:
        opp_products = _pull_closed_won(conn) if booked else pull_opp_products(conn)

    # Canonicalize to the 9-family taxonomy (stragglers/null -> "Other"), then
    # collapse to distinct (opp, product) so duplicate canonical rows don't
    # inflate the per-opp product count.
    df = opp_products[["Opportunity_ID", "Product", "nacv"]].copy()
    df["product"] = _canon_product(df["Product"])
    df["pos"] = df["nacv"].clip(lower=0)  # positive-NACV basis (matches _product_share_map)

    distinct = df.groupby(["Opportunity_ID", "product"], as_index=False)["pos"].sum()

    per_opp = distinct.groupby("Opportunity_ID").agg(
        n_products=("product", "size"),
        opp_nacv=("pos", "sum"),
    )

    n_opps = len(per_opp)
    total_nacv = per_opp["opp_nacv"].sum()
    multi = per_opp[per_opp["n_products"] > 1]
    n_multi = len(multi)
    multi_nacv = multi["opp_nacv"].sum()

    print("=" * 64)
    print("MULTI-PRODUCT OPPORTUNITY ANALYSIS" + ("  [BOOKED / Closed Won]" if booked else "  [OPEN PIPE]"))
    print("Universe: current Period_1 sku_nacv_fact (non-zero NACV,")
    print("record types Product/Service/Platinum support); canonical families."
          + ("\nFiltered to Closed Won stages." if booked else ""))
    print("=" * 64)
    print()
    print(f"Total opportunities                 : {n_opps:,}")
    print(f"Opps with >1 product                : {n_multi:,}  "
          f"({n_multi / n_opps:.1%} of opps)")
    print(f"Total positive NACV                 : ${total_nacv / 1e6:,.1f}M")
    print(f"NACV in multi-product opps          : ${multi_nacv / 1e6:,.1f}M  "
          f"({multi_nacv / total_nacv:.1%} of NACV)")
    print(f"Mean products per opp               : {per_opp['n_products'].mean():.2f}")
    print(f"Median products per opp             : {per_opp['n_products'].median():.0f}")
    print()

    # Distribution by product count (bucket 4+ together).
    bucket = per_opp["n_products"].clip(upper=4)
    labels = {1: "1 product", 2: "2 products", 3: "3 products", 4: "4+ products"}
    dist = per_opp.groupby(bucket).agg(
        opps=("n_products", "size"),
        nacv=("opp_nacv", "sum"),
    )

    print(f"{'Products':<14}{'Opps':>10}{'% opps':>10}{'NACV ($M)':>14}{'% NACV':>10}")
    print("-" * 58)
    for k in sorted(dist.index):
        row = dist.loc[k]
        print(f"{labels[k]:<14}{int(row['opps']):>10,}"
              f"{row['opps'] / n_opps:>10.1%}"
              f"{row['nacv'] / 1e6:>14,.1f}"
              f"{row['nacv'] / total_nacv:>10.1%}")
    print("-" * 58)
    print(f"{'Total':<14}{n_opps:>10,}{1.0:>10.1%}"
          f"{total_nacv / 1e6:>14,.1f}{1.0:>10.1%}")
    print()

    # Sanity checks.
    assert int(dist["opps"].sum()) == n_opps, "bucket opp counts must sum to total"
    assert abs(dist["nacv"].sum() - total_nacv) < 1.0, "bucket NACV must sum to total"
    print("Sanity checks passed (buckets sum to totals).")


if __name__ == "__main__":
    main()
