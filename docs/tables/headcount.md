# `Headcount.xlsx` — AE capacity contract

**Location:** `data/Headcount.xlsx`, single sheet `WW HC 12.29` (24 rows x 11 cols)
**Used by:** the per-AE target derivation — see [`target-monthly.md`](target-monthly.md)
**Grain:** Geo / Region / Territory x fiscal quarter

The number of AEs carrying quota in each territory. It is the **divisor** that
turns a territory-level Pipe Create target into an individual AE's target.

> Source explanation provided by the Strategic Analytics lead on 2026-08-10 and
> verified against `data/Target_Monthly.csv` the same day — see Verification.

---

## The capacity model

```
Pipeline AE        =  Pipe Create $ target (territory)  /  HC
ASP  (per product) =  average sales price per product, averaged per quarter
Opportunities      =  Σ_product ( Pipe Create $_product / ASP_product )
Pipeline AE Count  =  Opportunities (territory)  /  HC
```

In words: divide the territory's pipe-create dollar target by headcount to get
**pipeline per AE** — that is the AE's dollar target. Divide the dollar target by
ASP to get the **number of opportunities** that must be created, then divide by
headcount for the per-AE opp target.

Note the direction: **create dollars ÷ ASP = count**. Dollars divided by
dollars-per-opp yields opps.

This is why `Target_Monthly.csv` carries `Pipeline AE` and `Pipeline AE Count`
rows (33 territories each): they are the per-AE views of `Pipeline` and
`Opportunities`.

---

## Schema

| Column | Type | Notes |
|---|---|---|
| `Geo` | str | `AMS`, `EMEA`, `APAC`, `Pubsec` — matches `Target_Monthly.csv` |
| `Region` | str | **Does not always match `GeoTerritory`** — see gotcha 2 |
| `Territory` | str | Joins to `GeoSubTerritory_AccountOwnerBookingsTeam` — see gotcha 2 |
| `Q1'25` … `Q4'26` | int/float | AE count per fiscal quarter. 8 quarters, FY25–FY26 |

**Half-counts are real.** `Q1'26` and `Q2'26` contain values like `4.5`, `8.5`,
`10.5` — mid-quarter hires or partial allocations. Do not round or cast to int.
`Q3'26` and `Q4'26` are whole numbers throughout.

---

## Verification — 2026-08-10

Headcount implied purely from `Target_Monthly.csv` as
`Pipeline / Pipeline AE` was compared with the `Q3'26` column of this file.
**All 22 live territories matched exactly**, and `Opportunities /
Pipeline AE Count` gave the same divisor independently. Sample:

| Territory | Implied from targets | `Q3'26` here |
|---|---:|---:|
| AMS Core East Canada | 5.0 | 5 |
| AMS Core East Northeast | 9.0 | 9 |
| EMEA Core Germany | 11.0 | 11 |
| EMEA Core UKI | 11.0 | 11 |
| APAC Asia | 12.0 | 12 |
| AMS Public Sector - FED | 4.0 | 4 |

**Use this as a consistency check.** If implied headcount ever comes out
non-integer for a `Q3'26`/`Q4'26` territory, or disagrees with this file, then
one of the two files was updated without the other. Surface it — do not pick a
winner.

---

## Gotchas

### 1. `Sealights` headcount went to zero in FY26

`AMS Sealights` runs 7 → 7 → 6 → 5 across FY25 and then **0 for all of FY26**.
Any per-AE figure for a Sealights territory divides by zero. Return `None`, not
`inf` and not `0` — the same treatment missing teams get.

Note this is *headcount*, not target: Sealights still carries product-level
pipeline targets (Q3 FY26 ASP $165,255, the highest of any product).

### 2. Territory names do not join cleanly to `Target_Monthly.csv`

A naive join drops or mismatches rows. Known discrepancies:

| `Headcount.xlsx` | `Target_Monthly.csv` | Note |
|---|---|---|
| `New Logo` (Region and Territory, Geo `AMS`) | `AMS Corporate` | **`AMS Corporate` is the correct name** — confirmed 2026-08-10. `New Logo` is the stale label in this file; same team, HC 5 in Q3'26. Always report as `AMS Corporate` |
| `AMS Core East LATAM` | `AMS Core LATAM` | `East` dropped |
| `EMEA Core Benelux` | `EMEA Core BeNeLux` | Case differs — see `target-monthly.md` gotcha 2 |
| `LATAM`, `Sealights`, `Germany`, `New Logo` | — | `Region` here is sometimes a short name, not the full `GeoTerritory` |

**Canonical naming: `Target_Monthly.csv` wins.** Where the two files disagree on
a team's name, the target file carries the current label and `Headcount.xlsx`
carries the stale one (confirmed for `AMS Corporate` / `New Logo`). Report the
`Target_Monthly.csv` name; treat this file's `Territory` column as a join key
only, never as a display label.

**There is no mapping table in this file.** The legacy workbook has a `Mapping`
sheet (21 x 3) which may serve this purpose, but it is **unaudited** — see
[`../reference/legacy-pipe-create-xlsm.md`](../reference/legacy-pipe-create-xlsm.md).
Until that is confirmed, join on `Territory` and **assert no unmatched rows**
rather than letting a silent left-join produce nulls.

### 3. 24 rows, 33 AE-row territories

`Target_Monthly.csv` carries `Pipeline AE` rows for 33 territories; this file has
24. The extras are territories with zero targets (`AMS Devops`, `EMEA Growth`,
`APAC DevOps`, and other renamed or retired teams). A territory absent here has
**no headcount**, which is not the same as zero headcount.

### 4. The sheet name is a date stamp

`WW HC 12.29` — worldwide headcount as of Dec 29. The file itself is dated
2026-06-27. Read the sheet by index or by matching a `WW HC` prefix; do not
hardcode `12.29`, which will change when the file is refreshed.

---

## Bearing on invariant 10

Root `CLAUDE.md` invariant 10 records that the `Opportunities` target unit is
unresolved. The capacity model above **partially resolves the mechanism**:

Because ASP is computed **per product** and the opp target is
`Σ_product (Pipeline_product / ASP_product)`, the target counts
**opportunity-product-lines, not distinct opportunities.** An opportunity
carrying three products contributes three to the target and one to a distinct-opp
actual count. Verified: Q3 FY26 per-product ASPs range $33,158 (Recurring
Services) to $165,255 (Sealights), and the per-product quotients sum to exactly
the 2,844 all-Geo `Opportunities` target.

**This does not fully close invariant 10.** The mechanism is confirmed, but a
5.5x gap would require roughly 5.5 product lines per opportunity, which is high
enough that another factor is likely also at play. **The invariant stands and its
caveat still travels with every opp-count and ASP figure** until the residual is
explained.
