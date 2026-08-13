# Segment Tier Logic — Current vs Live (comparison build)

**Date:** 2026-06-28
**Scope:** This describes a change applied **only to a separate comparison dashboard**
(`output/coverage_dashboard_NEW_SEGMENT.html`). The original / production dashboard
(`output/coverage_dashboard.html`), the production SQL (`snapshot.sql`,
`live_booked.sql`), and the render path are **unchanged** — they still use the old
frozen initial-tier segment page.

---

## 1. Background — the email thread

Source: Outlook thread **"Confirmation Required: Account Segment Field for Reporting
(Current vs Live)"**, 24–26 June 2026. Participants: Vikas Pandey (BI), Tiarnán Stacke
(Strategic Analytics), Lauren Lacivita, Maggie Phillips, Chandler Ogden; Sam Robinson cc'd.

### The question (Vikas Pandey, Jun 24)
Which Salesforce field should drive the **account Tier** in reporting?

- **Current Segment** = `Current_Segment__c`
- **Live Segment** = `X2019_Segment_expected__c`

> *"This question came up during a review of the c360AccountRankings report… There was a
> discussion indicating that the intent may have been to move reporting to the Current
> Segment (Current_Segment__c) field so that segment assignments remain fixed throughout
> the year rather than changing as account segments are updated."*

Vikas confirmed essentially **all** reporting currently uses the **Live Segment**
(`X2019_Segment_expected__c`): Customer360, Funnel Summary & Territory, GTM – Bookings &
Pipeline, PreQ Pipe WinRate, TAPT. **Only Renewals** already used Current Segment.

### The decision (Tiarnán Stacke, Jun 24–25)
> *"We should be using the **Current Segment** field for all reporting."*

> *"This will materially change numbers but looking at the bookings movements most of
> these accounts are due to **Current ARR rules placing them in higher tiers**."*

> *"Can you please implement the new Segment logic with a
> **COALESCE(Current_Segment__c, X2019_Segment_expected__c)** (or similar logic)."*

And, directly relevant to this project:
> *"FYI @Sam Robinson on the Coverage Curves for Segment; might need to take another
> look after the flag gets updated."*

### Data-hygiene caveat (Lauren Lacivita, Jun 25)
> *"The current segment needs to be **manually loaded periodically**, and can be done so
> for new accounts based on the live segment following… population of revenue by zoominfo.
> This should be part of general data hygiene."*

This is why a **COALESCE** (not just `Current_Segment__c`) is used: `Current_Segment__c`
is not always populated, so it falls back to the live field.

### Implementation by BI (Vikas Pandey, Jun 26)
> *"…updated all the views and stored procedures that use the X2019_Segment_expected__c
> field. We have kept the existing column/alias name the same, but the logic behind it has
> been changed to: COALESCE(a.Current_Segment__c, a.X2019_Segment_expected__c)… wherever
> you see the X2019_Segment_expected__c field… it will now show the Current Segment value."*

> **Note:** the rewrite was applied to downstream **views / stored procedures**. The base
> table `[sfdc_trf].[account_live]` is still **raw** — verified: in account_live the two
> fields still differ (~5% of accounts), so reading the raw fields gives the true
> Current vs Live values.

---

## 2. Field semantics (important — counterintuitive naming)

| Field | Label | Behaviour |
|---|---|---|
| `Current_Segment__c` | **Current Segment** | Meant to stay **FIXED** through the fiscal year; manually loaded periodically. Sometimes NULL. |
| `X2019_Segment_expected__c` | **Live Segment** | **DRIFTS** during the year as ARR / revenue updates (ZoomInfo + Current ARR rules). Always populated. |

The naming is the opposite of what it sounds like: the field named "Current" is the
*stable* one; the "X2019…expected" field is the *live, moving* one.

---

## 3. What changed in the coverage-curve logic

### Old logic (production — unchanged)
The segment page's account tier came from `[rpt_cx].[account_segment_quarterly]`, taking
each account's **earliest-quarter** value (its *initial* tier). That table was later
retired from Synapse, so production now serves a **frozen** copy of the last good segment
page (initial-tier, captured 2026-06-02).

```sql
-- account_segment CTE (old) — initial / earliest-quarter tier
account_segment AS (
    SELECT Id, QuarterStartSegment
    FROM (
        SELECT Id, QuarterStartSegment,
               ROW_NUMBER() OVER (PARTITION BY Id ORDER BY QuarterStartDate ASC) AS rn
        FROM [rpt_cx].[account_segment_quarterly]
    ) ranked
    WHERE rn = 1
)
```

### New logic (comparison dashboard only)
Tier comes from `[sfdc_trf].[account_live]` using the company-standard COALESCE — the
**current (fixed) segment**, falling back to the live segment where Current is NULL. A
`CASE` guard maps any stray non-tier value (e.g. a legacy `"2. Enterprise Account"`) to
NULL → Unassigned.

```sql
-- account_segment CTE (new) — current-segment COALESCE
account_segment AS (
    SELECT Id,
        CASE WHEN COALESCE(Current_Segment__c, X2019_Segment_expected__c)
                  IN ('Tier 1', 'Tier 2', 'Tier 3')
             THEN COALESCE(Current_Segment__c, X2019_Segment_expected__c)
        END AS QuarterStartSegment
    FROM [sfdc_trf].[account_live]
)
```

Everything downstream is unchanged — the output column keeps the name
`QuarterStartSegment`, so the same `build_segment_coverage` / payload code runs. Only the
**tier each account is assigned to** changes.

**Conceptual shift:** old = *initial* tier (fixed at account's first quarter); new =
*current* tier (fixed within the year via the manually-loaded Current Segment).

---

## 4. How the comparison dashboard was built

Script: **`scripts/build_new_segment_dashboard.py`** → `output/coverage_dashboard_NEW_SEGMENT.html`

- Pulls a fresh snapshot + live bookings with the CTE above (string-swapped on a *copy* of
  the SQL — the real SQL files are untouched).
- Rebuilds **only** the segment coverage + recommendations; every other page is served from
  the existing cached parquets, so the non-segment pages match the current dashboard.
- **Capped to an as-of date** (default `2026-06-02`, the frozen original's capture date):
  snapshot filtered to `snapshot_date <= as-of` and live bookings to opps closed by then.
  This removes the data-freshness difference so the comparison isolates the **tier logic**.
- Bypasses the frozen-payload override for this one render via an in-process patch
  (touches no file).

Re-run:
```
uv run python scripts/build_new_segment_dashboard.py                 # default --as-of 2026-06-02
uv run python scripts/build_new_segment_dashboard.py --no-cap        # today's data instead
```

Supporting analysis (read-only, full population): **`scripts/segment_tier_shift_analysis.py`**
compares X2019 vs Current vs COALESCE in `account_live`.

---

## 5. What the change does to the numbers

Comparing the capped new build vs the frozen original (data vintage now matched):

- **Totals tie.** All-segments open pipe ties the frozen original to the dollar for all 8
  FY24/FY25 quarters (and within ~$0.06M for FY26 Q1/Q2). The opp population is identical;
  only the tier labels move. So any per-tier difference is purely the re-bucketing.
- **Tier 1 roughly doubles in dollars.** Current-tier puts ~2× the open pipe into Tier 1
  every quarter (e.g. FY26 Q1 Tier 1 open: **$46.25M** new vs **$24.44M** frozen; FY24 Q1:
  $17.37M vs $9.33M). This is the ARR effect Tiarnán described — Current ARR rules push
  big-dollar accounts up into Tier 1.
- **Account counts move modestly.** Across the full `account_live` (88,684 accounts), the
  COALESCE rescues 783 NULLs and **5.0%** of accounts change tier (2,094 up / 2,337 down).
  Tier 1 actually loses a little by *count* (≈ −1.5pp) — but the accounts that stay/enter
  Tier 1 carry far more pipe, hence the dollar increase.

---

## 6. Open items (not owned by this project)

- `Current_Segment__c` relies on **manual periodic loading**; the thread raised whether an
  SFDC trigger should auto-populate it on account creation / after ARR data lands. Until
  then, the COALESCE fallback to the live field is doing real work.
- Whether/when to adopt the current-tier logic in the **production** coverage-curve segment
  page is still Sam's decision — this build exists to support that comparison.
