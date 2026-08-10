# Product-Level Bookings & Pipeline — sku_nacv_fact

**When to load**: Live product-level bookings, open pipeline by product, any query against `[src].[sku_nacv_fact]`.  
**Requires first**: [`sql/conventions.md`](../sql/conventions.md)  
**Also load**: [`tables/territory-mapping.md`](territory-mapping.md) for geo grouping · [`tables/opp-daily-snapshot.md`](opp-daily-snapshot.md) for age features (Stage_Age, S1_Age)  
**Not for**: Current opp-level state → [`tables/opportunity.md`](opportunity.md) · Historical trend → [`tables/opp-daily-snapshot.md`](opp-daily-snapshot.md)

**Primary table**: `[src].[sku_nacv_fact]` alias `N`  
**Joins**: `[rep].[trf_marketing_opps_dimension]` alias `M` · `[src].[trf_account_dimension]` alias `acc`  
**Row grain**: One row per opportunity line item (product SKU per deal)  
**Use for**: Live product-level NACV, bookings actuals, open pipeline by product  
**Related**: [`sql/conventions.md`](../sql/conventions.md) · [`tables/opportunity.md`](opportunity.md) (opp-level joins) · [`tables/territory-mapping.md`](territory-mapping.md)

> This is the **live actuals calculator** — use this for current bookings and pipeline by product.  
> For historical trend and QoQ lookback use [`tables/opp-daily-snapshot.md`](opp-daily-snapshot.md) instead.

---

## Mandatory WHERE filters

These filters must be present on every query against this table. Omitting any one returns garbage rows.

```sql
WHERE N.Period                = 'Period_1'                   -- current period only
  AND N.NACV_USD             != 0                            -- exclude zero-value lines
  AND N.record_type          IN ('Product', 'Service', 'Platinum support')
  AND N.Deal_Type            IN ('New Business', 'Expansion', 'Upsell', 'Professional services')
  AND N.Booking_Team_Static  NOT IN ('Account Management', 'Global', 'QAS Account Management')
  AND N.Booking_Team_Static  IS NOT NULL
  AND N.StageName            NOT IN (
      'Closed - Duplicate',
      'Stage 6 - Closed - Admin',
      'Stage 7 - Churned',
      'Opportunity Rejected',
      'Stage 0 - Renewal Outreach Not Started',
      '0 - First Interaction'
  )
```

---

## Core columns — sku_nacv_fact (N)

| Column | Notes |
|--------|-------|
| `Opportunity_id` | FK → `[sfdc_trf].[opportunity_live].Id` |
| `Accountid` | FK → `[src].[trf_account_dimension].Account_Id` |
| `Booking_Team_Static` | Territory — see AMS Public Sector override below |
| `Opp_Geo` | Pre-computed geo — use as `Geo` in SELECT |
| `Family` | Raw product family — map using Product CASE below |
| `Record_Type` | `Product` · `Service` · `Platinum support` |
| `StageName` | Raw stage — map using Stage CASE below |
| `Deal_Type` | `New Business` · `Expansion` · `Upsell` · `Professional services` |
| `Opportunity_Source_Logic` | Source — map `Lead Sourced` → `Marketing Sourced` |
| `Segment` | Customer tier / segment |
| `NACV_USD` | NACV in USD |
| `Uplift_USD` | Uplift component of NACV |
| `Period` | Always filter `Period = 'Period_1'` for current data |
| `Stage_1_Start_Date_Corrected` | Discovery / create date — use as `Create_Month` |
| `Opp_Closed_Date` | Close date |
| `Age_In_Days_Stage_1` | Deal age from stage 1 entry |

---

## Business logic — apply in every query

### Product family mapping

Raw `Family` values map to reporting product groups. Always apply this CASE:

```sql
CASE
    WHEN N.Record_Type IN ('Service', 'Platinum Support')           THEN 'Recurring Services'
    WHEN N.Family IN ('Tosca OSV','TTA','TEE','Tosca')              THEN 'Tosca'
    WHEN N.Family IN ('Testim','Testim Salesforce','TTA for SFDC',
                      'TTA for SNOW','TTA SNOW',
                      'Tricentis Device Cloud','Mobile')            THEN 'Testim'
    WHEN N.Family IN ('Tosca BI','Tosca DI')                        THEN 'Data Integrity'
    WHEN N.Family IN ('Vera')                                       THEN 'Vera'
    WHEN N.Family IN ('qTest')                                      THEN 'qTest'
    WHEN N.Family IN ('LiveCompare')                                THEN 'LiveCompare'
    WHEN N.Family IN ('NeoLoad')                                    THEN 'NeoLoad'
    WHEN N.Family IN ('Tricentis Sealights')                        THEN 'Sealights'
    ELSE N.Family
END AS Product
```

### Stage bucketing

Raw `StageName` maps to 4 reporting buckets:

```sql
CASE
    WHEN N.StageName IN ('Closed Deferred','Closed Lost')                         THEN 'Closed'
    WHEN N.StageName IN ('6 - Closed/Pending','Closed Won','Stage 5 - Closed Won') THEN 'Closed Won'
    WHEN N.StageName IN ('Closed - Duplicate','Stage 6 - Closed - Admin',
                         'Stage 7 - Churned','Opportunity Rejected',
                         '0 - First Interaction')                                 THEN 'Other'
    ELSE 'Open'
END AS Stage
```

| Bucket | Meaning |
|--------|---------|
| `Closed Won` | Booked revenue |
| `Closed` | Lost or deferred — not booked |
| `Open` | Active pipeline |
| `Other` | Admin/duplicate/churned — excluded by WHERE filter |

### Deal type mapping

```sql
CASE
    WHEN N.Deal_Type = 'New Business' THEN 'New Customer'
    ELSE N.Deal_Type
END AS Deal_Type
```

### Source mapping

```sql
CASE
    WHEN N.Opportunity_Source_Logic = 'Lead Sourced' THEN 'Marketing Sourced'
    ELSE N.Opportunity_Source_Logic
END AS Source
```

### Territory — AMS Public Sector override

`Booking_Team_Static` is correct for all territories except `AMS Public Sector`,
where the account owner's team should be used instead:

```sql
CASE
    WHEN N.Booking_Team_Static = 'AMS Public Sector'
        THEN acc.Account_Owner_Bookings_Team__c
    ELSE N.Booking_Team_Static
END AS Territory
```

This requires the account dimension join (see below).

### Product NACV calculation

```sql
N.NACV_USD - N.Uplift_USD AS [Product NACV]
```

Uplift is excluded from product NACV — it represents incremental value above renewal baseline, not new product revenue.

---

## Joins

### Marketing opps dimension — qualified stage

```sql
LEFT JOIN [rep].[trf_marketing_opps_dimension] AS M
    ON M.Opportunity_Id = N.Opportunity_id
```

Adds: `M.qualified_stage` — NULL-safe with fallback:

```sql
CASE
    WHEN M.qualified_stage IS NULL THEN 'P2'
    ELSE M.qualified_stage
END AS [Qualified Stage]
```

### Account dimension — territory override + account fields

```sql
LEFT JOIN [src].[trf_account_dimension] AS acc
    ON acc.Account_Id = N.Accountid
```

Adds: `acc.Account_Owner_Bookings_Team__c` — used for AMS Public Sector territory override.

---

## Date filters

`Opp_Closed_Date` scopes the results to a booking window. The standard filter is:

```sql
AND N.Opp_Closed_Date >= '2023-01-01'
```

Adjust the start date for the reporting period needed. For open pipeline, remove the date filter and filter by `Stage = 'Open'` instead.

---

## Standard full query

```sql
SELECT
    N.Opp_Geo                                    AS Geo,
    CASE
        WHEN N.Booking_Team_Static = 'AMS Public Sector'
            THEN acc.Account_Owner_Bookings_Team__c
        ELSE N.Booking_Team_Static
    END                                          AS Territory,
    acc.Account_Owner_Bookings_Team__c,
    CASE
        WHEN N.Record_Type IN ('Service','Platinum Support')             THEN 'Recurring Services'
        WHEN N.Family IN ('Tosca OSV','TTA','TEE','Tosca')               THEN 'Tosca'
        WHEN N.Family IN ('Testim','Testim Salesforce','TTA for SFDC',
                          'TTA for SNOW','TTA SNOW',
                          'Tricentis Device Cloud','Mobile')             THEN 'Testim'
        WHEN N.Family IN ('Tosca BI','Tosca DI')                         THEN 'Data Integrity'
        WHEN N.Family IN ('Vera')                                        THEN 'Vera'
        WHEN N.Family IN ('qTest')                                       THEN 'qTest'
        WHEN N.Family IN ('LiveCompare')                                 THEN 'LiveCompare'
        WHEN N.Family IN ('NeoLoad')                                     THEN 'NeoLoad'
        WHEN N.Family IN ('Tricentis Sealights')                         THEN 'Sealights'
        ELSE N.Family
    END                                          AS Product,
    CASE
        WHEN N.Opportunity_Source_Logic = 'Lead Sourced' THEN 'Marketing Sourced'
        ELSE N.Opportunity_Source_Logic
    END                                          AS Source,
    N.Segment                                    AS Tier,
    CASE
        WHEN N.StageName IN ('Closed Deferred','Closed Lost')                          THEN 'Closed'
        WHEN N.StageName IN ('6 - Closed/Pending','Closed Won','Stage 5 - Closed Won') THEN 'Closed Won'
        WHEN N.StageName IN ('Closed - Duplicate','Stage 6 - Closed - Admin',
                             'Stage 7 - Churned','Opportunity Rejected',
                             '0 - First Interaction')                                  THEN 'Other'
        ELSE 'Open'
    END                                          AS Stage,
    CASE
        WHEN N.Deal_Type = 'New Business' THEN 'New Customer'
        ELSE N.Deal_Type
    END                                          AS Deal_Type,
    N.Stage_1_Start_Date_Corrected               AS Create_Month,
    N.Opp_Closed_Date                            AS [Opp Close Date],
    N.NACV_USD - N.Uplift_USD                    AS [Product NACV],
    N.Segment,
    N.Opportunity_Source_Logic,
    N.Opportunity_id                             AS Opportunity_Id,
    N.Accountid                                  AS [Account ID],
    N.Stage_1_Start_Date_Corrected               AS [Discovery Date],
    N.Deal_Type                                  AS [Type],
    N.Booking_Team_Static,
    CASE
        WHEN M.qualified_stage IS NULL THEN 'P2'
        ELSE M.qualified_stage
    END                                          AS [Qualified Stage],
    N.Age_In_Days_Stage_1
FROM [src].[sku_nacv_fact] AS N
LEFT JOIN [rep].[trf_marketing_opps_dimension] AS M
    ON  M.Opportunity_Id = N.Opportunity_id
LEFT JOIN [src].[trf_account_dimension] AS acc
    ON  acc.Account_Id = N.Accountid
WHERE N.Period               = 'Period_1'
  AND N.Opp_Closed_Date     >= '2023-01-01'
  AND N.NACV_USD            != 0
  AND N.Record_Type         IN ('Product','Service','Platinum support')
  AND N.Deal_Type           IN ('New Business','Expansion','Upsell','Professional services')
  AND N.Booking_Team_Static NOT IN ('Account Management','Global','QAS Account Management')
  AND N.Booking_Team_Static IS NOT NULL
  AND N.StageName           NOT IN (
      'Closed - Duplicate','Stage 6 - Closed - Admin','Stage 7 - Churned',
      'Opportunity Rejected','Stage 0 - Renewal Outreach Not Started','0 - First Interaction'
  )
```

---

## Common aggregations

### Bookings by product and geo

```sql
SELECT
    N.Opp_Geo AS Geo,
    CASE
        WHEN N.Record_Type IN ('Service','Platinum Support')             THEN 'Recurring Services'
        WHEN N.Family IN ('Tosca OSV','TTA','TEE','Tosca')               THEN 'Tosca'
        WHEN N.Family IN ('Testim','Testim Salesforce','TTA for SFDC',
                          'TTA for SNOW','TTA SNOW',
                          'Tricentis Device Cloud','Mobile')             THEN 'Testim'
        WHEN N.Family IN ('Tosca BI','Tosca DI')                         THEN 'Data Integrity'
        WHEN N.Family IN ('Vera')                                        THEN 'Vera'
        WHEN N.Family IN ('qTest')                                       THEN 'qTest'
        WHEN N.Family IN ('LiveCompare')                                 THEN 'LiveCompare'
        WHEN N.Family IN ('NeoLoad')                                     THEN 'NeoLoad'
        WHEN N.Family IN ('Tricentis Sealights')                         THEN 'Sealights'
        ELSE N.Family
    END                                              AS Product,
    SUM(N.NACV_USD - N.Uplift_USD)                   AS Product_NACV,
    COUNT(DISTINCT N.Opportunity_id)                 AS Opp_Count
FROM [src].[sku_nacv_fact] AS N
WHERE N.Period               = 'Period_1'
  AND N.StageName           IN ('Closed Won','6 - Closed/Pending','Stage 5 - Closed Won')
  AND N.NACV_USD            != 0
  AND N.Record_Type         IN ('Product','Service','Platinum support')
  AND N.Deal_Type           IN ('New Business','Expansion','Upsell','Professional services')
  AND N.Booking_Team_Static NOT IN ('Account Management','Global','QAS Account Management')
  AND N.Booking_Team_Static IS NOT NULL
  AND YEAR(N.Opp_Closed_Date) = YEAR(GETDATE())
GROUP BY N.Opp_Geo, N.Family, N.Record_Type
ORDER BY Product_NACV DESC
```

### Open pipeline by product

```sql
SELECT
    N.Opp_Geo AS Geo,
    CASE
        WHEN N.Record_Type IN ('Service','Platinum Support')             THEN 'Recurring Services'
        WHEN N.Family IN ('Tosca OSV','TTA','TEE','Tosca')               THEN 'Tosca'
        WHEN N.Family IN ('Testim','Testim Salesforce','TTA for SFDC',
                          'TTA for SNOW','TTA SNOW',
                          'Tricentis Device Cloud','Mobile')             THEN 'Testim'
        WHEN N.Family IN ('Tosca BI','Tosca DI')                         THEN 'Data Integrity'
        WHEN N.Family IN ('Vera')                                        THEN 'Vera'
        WHEN N.Family IN ('qTest')                                       THEN 'qTest'
        WHEN N.Family IN ('LiveCompare')                                 THEN 'LiveCompare'
        WHEN N.Family IN ('NeoLoad')                                     THEN 'NeoLoad'
        WHEN N.Family IN ('Tricentis Sealights')                         THEN 'Sealights'
        ELSE N.Family
    END                                              AS Product,
    N.Opp_Closed_Date                               AS CloseDate,
    SUM(N.NACV_USD - N.Uplift_USD)                   AS Product_NACV,
    COUNT(DISTINCT N.Opportunity_id)                 AS Opp_Count
FROM [src].[sku_nacv_fact] AS N
WHERE N.Period               = 'Period_1'
  AND N.NACV_USD            != 0
  AND N.Record_Type         IN ('Product','Service','Platinum support')
  AND N.Deal_Type           IN ('New Business','Expansion','Upsell','Professional services')
  AND N.Booking_Team_Static NOT IN ('Account Management','Global','QAS Account Management')
  AND N.Booking_Team_Static IS NOT NULL
  AND N.StageName           NOT IN (
      'Closed - Duplicate','Stage 6 - Closed - Admin','Stage 7 - Churned',
      'Opportunity Rejected','Stage 0 - Renewal Outreach Not Started','0 - First Interaction',
      'Closed Deferred','Closed Lost','6 - Closed/Pending','Closed Won','Stage 5 - Closed Won'
  )
GROUP BY N.Opp_Geo, N.Family, N.Record_Type, N.Opp_Closed_Date
ORDER BY Product_NACV DESC
```

---

## Key differences from opportunity_live

| | `opportunity_live` | `sku_nacv_fact` |
|--|-------------------|-----------------|
| Row grain | One per opp | One per product SKU per opp |
| Financial metric | `Total_ARR__c` | `NACV_USD - Uplift_USD` |
| Product detail | Per-product ARR columns | `Family` → mapped Product |
| Territory col | `Bookings_Team_static__c` | `Booking_Team_Static` (no `__c`) + PS override |
| Stage field | `StageName` (clean) | `StageName` (legacy values — apply bucket CASE) |
| Geo | Via territory join | `Opp_Geo` pre-computed |
| Schema | `sfdc_trf` | `src` |
| Mandatory filters | `IsDeleted = 0` | `Period = 'Period_1'`, `NACV_USD != 0`, record type, deal type, excluded territories, excluded stages |
---

## Handoff

- Need geo columns (Geo, Region, Territory) → load [`tables/territory-mapping.md`](territory-mapping.md)
- Need Stage_Age or S1_Age features → load [`tables/opp-daily-snapshot.md`](opp-daily-snapshot.md)
- Building the win probability model → load [`models/win-probability-design.md`](../models/win-probability-design.md)
- Building the full pipeline → load [`analysis/gtm-dashboard.md`](../analysis/gtm-dashboard.md)
