# `Target_Monthly.csv` — target contract

**Location:** `data/Target_Monthly.csv` (11.4 MB, 63,171 rows x 36 columns)
**Consumed by:** `config.load_pipe_create_targets()` → [`../models/pipe-create.md`](../models/pipe-create.md)
**Read before:** any target, attainment, or ASP work.

Bottom-up monthly targets across every GTM dimension. This is the **only** source
of target values for the Pipe Create model. It is a flat additive fact table: one
row per dimension combination, one column per month, and a target for a given
grain is the **sum** of every row matching it.

> Everything below marked *verified* was checked directly against the file on
> 2026-08-10. Anything not covered says so — do not infer the rest.

---

## Load recipe — do not deviate

```python
df = pd.read_csv(DATA / 'Target_Monthly.csv', low_memory=False)
df.columns = df.columns.str.strip()                      # names carry stray spaces
for c in df.select_dtypes(include='str').columns:        # so do the values
    df[c] = df[c].str.strip()
```

Both strips are mandatory — see gotcha 1. Read the file **once** and pass the
frame down; `pipe_create.py` and `coverage.py` must not each re-read it.

Month columns are derived from the quarter start, never hardcoded
(root `CLAUDE.md` invariant 1):

```python
months = [f"M{d:%Y%m}" for d in pd.date_range(Q_START, Q_END, freq='MS')]
```

---

## Schema

**12 dimension columns** (all `str`) and **24 month columns**.

| Column | Distinct | Nulls | Notes |
|---|---:|---:|---|
| `Target_Type` | 6 | 0 | See values below. Always filter on this first. |
| `Segment` | 3 | 965 | |
| `Product_Family` | 7 | 6,955 | |
| `Opp_Entry` | 7 | 2,232 | |
| `Geo` | 4 | 0 | `AMS`, `APAC`, `EMEA`, `Pubsec` |
| `GeoTerritory` | 26 | 0 | Region grain. **Has case collisions — gotcha 2.** |
| `GeoSubTerritory_AccountOwnerBookingsTeam` | 38 | 0 | Territory grain. Leading space in the raw name, no trailing one. |
| `Application` | 1 | 62,469 | Effectively empty — 98.9% null. |
| `Product` | 15 | 71 | |
| `Marketing_Sub_Target` | 4 | 61,202 | Effectively empty — 96.9% null. |
| `Deal_Type` | 5 | 1,530 | |
| `Source` | 3 | 2,232 | |

**Month columns:** `M202501` … `M202612` — 24 consecutive months covering FY25
and FY26. All `float64`, **zero nulls, zero negatives** (verified), so
aggregation needs no `ISNULL` guard here. Not-yet-targeted combinations are
`0.0`, not null.

### `Target_Type` values

| Value | Rows | Meaning |
|---|---:|---|
| `Opportunities` | 30,942 | Opportunity **count** target. Unit unresolved — root `CLAUDE.md` invariant 10. |
| `Pipeline` | 29,997 | Pipe-create **dollar** target. Trustworthy. |
| `QLs` | 1,267 | Qualified leads. Not used by Pipe Create. |
| `Bookings` | 899 | Not used by Pipe Create. |
| `Pipeline AE Count` | 33 | Per-AE opp target = `Opportunities / HC`. See below. |
| `Pipeline AE` | 33 | Per-AE dollar target = `Pipeline / HC`. See below. |

### The per-AE rows

`Pipeline AE` and `Pipeline AE Count` are the per-AE views of `Pipeline` and
`Opportunities`, divided by territory headcount from `data/Headcount.xlsx`:

```
Pipeline AE        =  Pipeline      / HC        # the AE's dollar target
Pipeline AE Count  =  Opportunities / HC        # opps the AE must create
Opportunities      =  Σ_product ( Pipeline_product / ASP_product )
```

Verified 2026-08-10: headcount implied as `Pipeline / Pipeline AE` matches the
`Q3'26` column of `Headcount.xlsx` exactly for all 22 live territories, and
`Opportunities / Pipeline AE Count` yields the same divisor independently.

These rows are at **territory grain only** — `Product` is null on all 66 of them,
and they cover 33 territories. Full contract:
[`headcount.md`](headcount.md).

**There is no `ASP` row** (verified — root `CLAUDE.md` invariant 2). ASP is always
derived as `Pipeline / Opportunities` at matching grain, and every ASP or
opp-count figure carries the invariant-10 caveat inline.

---

## Gotchas

### 1. Stray whitespace in names *and* values — verified

Raw column names, exactly as they appear:

```
' Target_Type '   ' Segment '   ' Product_Family '   ' Opp_Entry '   ' Geo '
' GeoTerritory '  ' GeoSubTerritory_AccountOwnerBookingsTeam'   ' Application '
' Product '  ' Marketing_Sub_Target '  ' Deal_Type '  ' Source '
```

Note `' GeoSubTerritory_AccountOwnerBookingsTeam'` has a **leading space only** —
a `.str.strip()` handles it, but a hand-written `.replace(' ', '')` or a
trailing-only strip will not. Month columns (`M202501` …) are clean.

Object *values* carry the same whitespace. Stripping names but not values
silently creates blank-key rows and zeroes out real teams.

### 2. Case collisions split groupby keys — verified, undocumented elsewhere

`GeoTerritory` contains near-duplicate values differing only in case. They are
**distinct groupby keys**, so aggregating on the raw column orphans real money:

| Collision | Rows | 24-month target |
|---|---:|---:|
| `AMS DevOps` | 163 | $11,709,684 |
| `AMS Devops` | 6 | **$3,916,026 orphaned** |
| `EMEA DevOps` | 163 | $9,603,988 |
| `EMEA Devops` | 6 | **$4,322,631 orphaned** |
| `EMEA SeaLights` | 166 | $5,784,378 |
| `EMEA Sealights` | 1 | **$492,809 orphaned** |

`AMS Sealights` also exists and has no `AMS SeaLights` counterpart, so it is not
a collision — do not "fix" it by casing.

**This is not yet handled by `pipe_create.py`.** Whether to normalize case or
preserve the split is an open decision — see below. Until it is resolved, any
`GeoTerritory`-grain output must be checked against these three pairs.

### 2b. Product names do not match `sku_nacv_fact` — verified

The target file uses short codes where `SKU_SQL`'s `PRODUCT_CASE` emits full
names. Joining on `Product` silently drops **$53,271,424 — 26.4% of the Q3 FY26
target**:

| Here | `PRODUCT_CASE` emits | Q3 FY26 Pipeline |
|---|---|---|
| `LC` | `LiveCompare` | $17,906,662 |
| `DI` | `Data Integrity` | $12,730,530 |
| `Neoload` | `NeoLoad` | $22,634,232 |

`Tosca`, `qTest`, `Sealights`, `Recurring Services` match as-is. An explicit
mapping is required — do not case-fold blindly, since `Sealights` matches while
`SeaLights` also exists in the file and a naive fold creates a new collision.

See [`../analysis/pipe-create-waterfall.md`](../analysis/pipe-create-waterfall.md)
Step 2b for where splits come from.

### 3. `SEA` does not mean South-East Asia

Substring-matching `SEA` against team names hits `SeaLights` / `Sealights` — a
product line, in all three Geos. Root `CLAUDE.md` invariant 9 concerns
*APAC Asia SEA*, which is a different thing entirely.

### 4. Missing teams are absent rows, not zeros — verified

`APAC` sub-territories present: `APAC ANZ`, `APAC Asia`, `APAC DevOps`,
`APAC Japan`, `APAC SeaLights`. There is **no `APAC Asia AGE` and no
`APAC Asia SEA` row** — confirming invariant 9. The parent `APAC Asia` carries
the target.

A team with no row has **no target**, which is different from a zero target.
Never zero-fill it into a real-looking 0% attainment; flag it.

---

## Verified reference figures

Q3 FY26 = `M202607` + `M202608` + `M202609`, summed across all Geos:

| Measure | Value |
|---|---|
| `Pipeline` target | **$201,789,918** |
| `Opportunities` target | **2,844** — unit unresolved, invariant 10 |
| Derived ASP | **$70,965** — carries the invariant-10 caveat |

The Pipeline figure reproduces the total quoted in the root `CLAUDE.md` output
conventions, which is what validates this load recipe end to end. **Use it as the
regression check** after any change to target loading: if this total moves and
the file did not change, the loader is wrong.

---

## Open questions — not covered by the corpus

1. ~~`Pipeline AE` / `Pipeline AE Count` undocumented~~ — **resolved 2026-08-10.**
   See the per-AE rows section above and [`headcount.md`](headcount.md).
2. **Case-collision policy** — normalize `GeoTerritory` case, or treat the
   variants as genuinely distinct teams? Unresolved. Affects $8.7M across 24
   months. Note `AMS Devops` and `EMEA Devops` (the lowercase variants) also
   appear among the per-AE rows with zero targets.
3. **Territory join keys** — `Headcount.xlsx` names do not join cleanly here
   (`New Logo` ≡ `AMS Corporate`, `AMS Core East LATAM` ≡ `AMS Core LATAM`,
   `Benelux` vs `BeNeLux`). No mapping table is confirmed. See
   [`headcount.md`](headcount.md) gotcha 2.
4. **Invariant 10 — narrowed, not closed.** The `Opportunities` target is built
   as `Σ_product (Pipeline_product / ASP_product)` with ASP computed **per
   product** ($33,158–$165,255 in Q3 FY26), so it counts
   **opportunity-product-lines, not distinct opportunities**. That mechanism is
   verified. It does not fully account for the ~5.5x gap, which would need ~5.5
   product lines per opp. **The invariant stands and its caveat still travels
   with every opp-count and ASP figure.** Residual likely resolvable from
   [`../reference/legacy-pipe-create-xlsm.md`](../reference/legacy-pipe-create-xlsm.md).
