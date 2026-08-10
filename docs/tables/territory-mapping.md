# Territory Mapping — Map_Booking_Team_Static_live

**When to load**: Any task that groups by Geo, Region, Territory, Product Family, FLM, or VP. Required by every query that joins `Map_Booking_Team_Static_live`.  
**Requires first**: [`sql/conventions.md`](../sql/conventions.md)  
**Used by**: Every table file in this context — this is a shared dependency.  
**Never**: Derive geo from `Bookings_Team_static__c` with a CASE statement. Always join this table with `ActiveTeam = 'Active'`.

**Source table**: `[sharepoint].[Map_Booking_Team_Static_live]`  
**Join key**: `o.Bookings_Team_static__c = bts.Bookings_Team_Static`  
**Active filter**: `WHERE bts.ActiveTeam = 'Active'` — always required  
**Related**: [`sql/conventions.md`](../sql/conventions.md) (join rules) · [`tables/opportunity.md`](opportunity.md) (join source) · [`sql/patterns.md`](../sql/patterns.md) (full query examples)

Never derive Geo, Region, Territory, or Product Family from `Bookings_Team_static__c` using a CASE statement.
Join to this table instead — it owns all of that hierarchy.

---

## Column Reference

| Column | Notes |
|--------|-------|
| `Id` | Surrogate key |
| `Bookings_Team_Static` | Join key — matches `o.Bookings_Team_static__c` on opportunity |
| `BTS_Lowercase` | Lowercase version — useful for case-insensitive matching |
| `ActiveTeam` | `'Active'` or `'Inactive'` — **always filter `ActiveTeam = 'Active'`** |
| `dif_load_date` | ETL load date — do not use in business queries |

### Geography hierarchy

| Column | Example values | Notes |
|--------|---------------|-------|
| `BTS_Geo` | `AMS` · `EMEA` · `APJ` | Top-level geo. Uses `APJ` here — but for dashboard geo buckets use `BTS_RegionFamily` (prefixed `APAC`), not this column. |
| `BTS_Geo_Clari` | `AMS` · `DevOps` · `EMEA` | Geo as used in Clari forecasting |
| `BTS_Geo_Sort` | integer | Sort order for geo |
| `BTS_GeoFamily` | `AMS` · `AMS LATAM` · `DevOps` | Geo + product family grouping |
| `BTS_GeoFamily_Sort` | integer | Sort order |
| `BTS_Partner_Geo` | `AMS` · `LATAM` · `EMEA` · `APJ` | Partner-facing geo grouping |
| `BTS_Partner_Geo_Sort` | integer | Sort order |

### Region hierarchy

| Column | Example values | Notes |
|--------|---------------|-------|
| `BTS_Region` | `AMS East` · `AMS West` · `AMS South` · `AMS LATAM` · `AMS Corporate` · `AMS DevOps` | Sub-geo region |
| `BTS_Region_Org` | Same as BTS_Region in most cases | Org chart region |
| `BTS_Region_Clari` | Clari-specific region label | |
| `BTS_RegionFamily` | `AMS East` · `AMS West` · `EMEA ...` · `APAC ...` · `Public Sector ...` · `DevOps` | Region + product family. **This is the geo-bucketing source** for the dashboard — values are prefixed `AMS` / `EMEA` / `APAC` / `Public Sector` (note: `APAC`, not `APJ`). |
| `BTS_Region_Org_Sort` | integer | Sort order |

### Territory

| Column | Example values | Notes |
|--------|---------------|-------|
| `BTS_Territory` | `AMS Core East Canada` · `AMS Core West Pacific` | Finest-grain territory |
| `BTS_Territory_Clari` | Clari-specific territory label | |

### Product family

| Column | Example values | Notes |
|--------|---------------|-------|
| `BTS_ProductFamily` | `Core` · `DevOps` | Product family for this team |
| `BTS_GeoProductFamily` | `AMS Core` · `AMS DevOps` · `EMEA Core` | Geo + product family combined |
| `BTS_GeoFamily_Sort` | integer | |
| `BTS_Product_Family_Sort` | integer | |
| `BTS_Geo_Product_Family_Sort` | integer | |

### Leadership

| Column | Notes |
|--------|-------|
| `BTS_FLM` | First-line manager name |
| `BTS_VP` | VP name |
| `AI_Strategist` | Assigned AI strategist (e.g. Matt Serpone) |

### Segment & other

| Column | Notes |
|--------|-------|
| `BTS_Segment` | Customer segment (e.g. `Testim`) — NULL for most teams |
| `BTS_GeoSegment` | Geo + segment (e.g. `AMS Testim`) |
| `BT_security_group` | Security group for row-level security (e.g. `AMS_DEVOPS_1`) |
| `BTS_Is_Curr` | `1` = current team definition |
| `BTS_Booking_Team_Static_Sort` | Sort order for the full team name |

---

## Standard join pattern

```sql
FROM [sfdc_trf].[opportunity_live] o
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  o.Bookings_Team_static__c = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
```

Use `LEFT JOIN` so opportunities with unmapped or inactive teams are not silently dropped.
Unmapped opps will have NULL for all `bts.*` columns — handle in reporting as `'Unassigned'`.

---

## Which column to use for which reporting need

| Reporting need | Column to use |
|----------------|--------------|
| Top-level geo rollup | `bts.BTS_Geo` |
| Clari / forecasting views | `bts.BTS_Geo_Clari` · `bts.BTS_Region_Clari` |
| Regional breakdown | `bts.BTS_Region` |
| Territory-level detail | `bts.BTS_Territory` |
| Core vs DevOps split | `bts.BTS_ProductFamily` |
| Geo + product family | `bts.BTS_GeoProductFamily` |
| Partner reporting | `bts.BTS_Partner_Geo` |
| FLM rollup | `bts.BTS_FLM` |
| VP rollup | `bts.BTS_VP` |
| Sorted display (dashboards) | `bts.BTS_Geo_Sort` · `bts.BTS_Region_Org_Sort` · `bts.BTS_Booking_Team_Static_Sort` |

---

## Full pipeline query with territory join

```sql
SELECT
    o.Id,
    o.Name,
    o.StageName,
    o.CreatedDate,
    o.CloseDate,
    o.Bookings_Team_static__c,
    ISNULL(bts.BTS_Geo,              'Unassigned') AS Geo,
    ISNULL(bts.BTS_Region,           'Unassigned') AS Region,
    ISNULL(bts.BTS_Territory,        'Unassigned') AS Territory,
    ISNULL(bts.BTS_ProductFamily,    'Unassigned') AS Product_Family,
    ISNULL(bts.BTS_GeoProductFamily, 'Unassigned') AS Geo_Product_Family,
    bts.BTS_FLM                                    AS FLM,
    bts.BTS_VP                                     AS VP,
    ISNULL(o.Total_ARR__c, 0)                      AS Total_ARR,
    ISNULL(o.NACV__c, 0)                           AS NACV,
    o.ForecastCategoryName,
    o.Type,
    o.CurrencyIsoCode
FROM [sfdc_trf].[opportunity_live] o
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  o.Bookings_Team_static__c = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
WHERE o.IsDeleted = 0
  AND o.IsClosed  = 0
ORDER BY o.Total_ARR__c DESC
```

---

## Territory rollup — ARR by geo and region

```sql
SELECT
    ISNULL(bts.BTS_Geo,    'Unassigned') AS Geo,
    ISNULL(bts.BTS_Region, 'Unassigned') AS Region,
    bts.BTS_ProductFamily                AS Product_Family,
    o.Type,
    COUNT(*)                             AS Opp_Count,
    SUM(ISNULL(o.Total_ARR__c, 0))       AS Total_ARR,
    SUM(ISNULL(o.NACV__c, 0))            AS Total_NACV,
    SUM(CASE WHEN o.IsWon = 1 THEN ISNULL(o.Total_ARR__c, 0) ELSE 0 END) AS Won_ARR
FROM [sfdc_trf].[opportunity_live] o
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  o.Bookings_Team_static__c = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
WHERE o.IsDeleted = 0
GROUP BY
    bts.BTS_Geo,
    bts.BTS_Region,
    bts.BTS_ProductFamily,
    o.Type
ORDER BY
    bts.BTS_Geo_Sort,
    bts.BTS_Region_Org_Sort,
    Total_ARR DESC
```

---

## Notes on inactive teams

Teams are marked `ActiveTeam = 'Inactive'` when territories are reorganized.
Historical opportunities may still carry old `Bookings_Team_static__c` values that
map to inactive rows. For historical trend queries, you may need to remove the
`ActiveTeam = 'Active'` filter and accept that some older records resolve to
deprecated team definitions. Document this choice in any report that does so.
---

## Handoff

This file has no further dependencies — it is a leaf node in the reference tree.  
Every other query file that needs geo joins back to this one.
