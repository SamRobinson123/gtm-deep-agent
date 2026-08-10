# SQL Query Patterns

**When to load this file**: When you need a ready-to-run query template. Not required for writing new queries from scratch — use [`sql/conventions.md`](conventions.md) + the relevant table file instead.  
**Requires first**: [`sql/conventions.md`](conventions.md) — all rules defined there apply to every pattern here.  
**Also load**: [`tables/territory-mapping.md`](../tables/territory-mapping.md) for any pattern that includes geo/region columns.

Ready-to-run templates for common GTM queries. All patterns here already follow [`sql/conventions.md`](conventions.md) rules.

---

## Pattern 1 — Active pipeline

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

## Pattern 2 — Won deals, current fiscal year

Won status = `StageName = 'Closed Won'`. `CloseDate` scopes to current year only.

```sql
SELECT
    o.Id,
    o.Name,
    o.StageName,
    o.CreatedDate,
    o.CloseDate,
    o.Booking_Date__c,
    ISNULL(bts.BTS_Geo,    'Unassigned') AS Geo,
    ISNULL(bts.BTS_Region, 'Unassigned') AS Region,
    ISNULL(o.Total_ARR__c, 0)            AS Total_ARR,
    ISNULL(o.NACV__c, 0)                 AS NACV,
    o.Type,
    o.Legal_Entity__c,
    o.CurrencyIsoCode
FROM [sfdc_trf].[opportunity_live] o
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  o.Bookings_Team_static__c = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
WHERE o.IsDeleted  = 0
  AND o.StageName  = 'Closed Won'
  AND YEAR(o.CloseDate) = YEAR(GETDATE())
ORDER BY o.CloseDate DESC
```

---

## Pattern 3 — Win rate by territory and deal type

```sql
SELECT
    ISNULL(bts.BTS_Geo,    'Unassigned') AS Geo,
    ISNULL(bts.BTS_Region, 'Unassigned') AS Region,
    bts.BTS_ProductFamily                AS Product_Family,
    o.Type,
    COUNT(*)                                               AS Total_Opps,
    SUM(CASE WHEN o.StageName = 'Closed Won'  THEN 1 ELSE 0 END) AS Won,
    SUM(CASE WHEN o.StageName = 'Closed Lost' THEN 1 ELSE 0 END) AS Lost,
    CAST(SUM(CASE WHEN o.StageName = 'Closed Won' THEN 1 ELSE 0 END) AS FLOAT)
        / NULLIF(COUNT(*), 0)                              AS Win_Rate,
    SUM(ISNULL(o.Total_ARR__c, 0))                         AS Total_ARR,
    AVG(ISNULL(o.Total_ARR__c, 0))                         AS Avg_ARR
FROM [sfdc_trf].[opportunity_live] o
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  o.Bookings_Team_static__c = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
WHERE o.IsDeleted = 0
  AND o.IsClosed  = 1
GROUP BY
    bts.BTS_Geo,       bts.BTS_Geo_Sort,
    bts.BTS_Region,    bts.BTS_Region_Org_Sort,
    bts.BTS_ProductFamily,
    o.Type
ORDER BY bts.BTS_Geo_Sort, bts.BTS_Region_Org_Sort, Total_ARR DESC
```

---

## Pattern 4 — Loss reason analysis

Filter to `Closed Lost` only — `Opportunity Rejected` lacks structured loss reason data.

```sql
SELECT
    o.Loss_Reason__c,
    o.Loss_Subcategory__c,
    o.Primary_Competitor__c,
    ISNULL(bts.BTS_Geo,    'Unassigned') AS Geo,
    ISNULL(bts.BTS_Region, 'Unassigned') AS Region,
    COUNT(*)                             AS Lost_Count,
    SUM(ISNULL(o.Total_ARR__c, 0))       AS Lost_ARR
FROM [sfdc_trf].[opportunity_live] o
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  o.Bookings_Team_static__c = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
WHERE o.IsDeleted = 0
  AND o.StageName = 'Closed Lost'
GROUP BY o.Loss_Reason__c, o.Loss_Subcategory__c, o.Primary_Competitor__c,
         bts.BTS_Geo, bts.BTS_Region
ORDER BY Lost_Count DESC
```

---

## Pattern 5 — Product ARR breakdown (open pipeline)

```sql
SELECT
    o.Id,
    o.Name,
    o.StageName,
    o.CloseDate,
    ISNULL(o.Tosca_ARR__c, 0)         AS Tosca_ARR,
    ISNULL(o.qTest_ARR__c, 0)         AS qTest_ARR,
    ISNULL(o.NeoLoad_ARR__c, 0)       AS NeoLoad_ARR,
    ISNULL(o.Testim_ARR__c, 0)        AS Testim_ARR,
    ISNULL(o.LiveCompare_ARR__c, 0)   AS LiveCompare_ARR,
    ISNULL(o.TTA_ARR__c, 0)           AS TTA_ARR,
    ISNULL(o.Agentic_ARR__c, 0)       AS Agentic_ARR,
    ISNULL(o.Total_ARR__c, 0)         AS Total_ARR,
    o.CurrencyIsoCode
FROM [sfdc_trf].[opportunity_live] o
WHERE o.IsDeleted = 0
  AND o.IsClosed  = 0
ORDER BY o.Total_ARR__c DESC
```

---

## Pattern 6 — Stage velocity and hygiene

```sql
SELECT
    o.Id,
    o.Name,
    o.StageName,
    o.CreatedDate,
    o.CloseDate,
    o.LastStageChangeDate,
    DATEDIFF(DAY, o.LastStageChangeDate, GETDATE()) AS Days_In_Stage,
    DATEDIFF(DAY, o.CreatedDate, GETDATE())         AS Opp_Age_Days,
    o.PushCount,
    o.Days_Since_Last_Activity__c,
    o.Next_Steps_Last_Updated__c,
    o.MEDDPICC_Score__c,
    o.Pipeline_Hygiene_Index__c,
    ISNULL(bts.BTS_Geo,    'Unassigned') AS Geo,
    ISNULL(bts.BTS_Region, 'Unassigned') AS Region,
    ISNULL(o.Total_ARR__c, 0)            AS Total_ARR,
    o.ForecastCategoryName
FROM [sfdc_trf].[opportunity_live] o
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  o.Bookings_Team_static__c = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
WHERE o.IsDeleted = 0
  AND o.IsClosed  = 0
ORDER BY Days_In_Stage DESC
```

---

## Pattern 7 — Pipeline created by month

```sql
SELECT
    YEAR(o.CreatedDate)                                          AS Created_Year,
    MONTH(o.CreatedDate)                                         AS Created_Month,
    ISNULL(bts.BTS_Geo, 'Unassigned')                           AS Geo,
    o.Type,
    COUNT(*)                                                     AS Opps_Created,
    SUM(ISNULL(o.Total_ARR__c, 0))                               AS Pipeline_ARR,
    SUM(CASE WHEN o.StageName = 'Closed Won'
             THEN ISNULL(o.Total_ARR__c, 0) END)                 AS Won_ARR,
    SUM(CASE WHEN o.StageName = 'Closed Won' THEN 1 ELSE 0 END)  AS Won_Count
FROM [sfdc_trf].[opportunity_live] o
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  o.Bookings_Team_static__c = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
WHERE o.IsDeleted = 0
GROUP BY YEAR(o.CreatedDate), MONTH(o.CreatedDate), bts.BTS_Geo, o.Type
ORDER BY Created_Year, Created_Month
```

---

## Pattern 8 — Closing this quarter

```sql
SELECT
    o.Id,
    o.Name,
    o.StageName,
    o.CloseDate,
    o.ForecastCategoryName,
    o.Manager_Forecast_Category__c,
    ISNULL(bts.BTS_Geo,    'Unassigned') AS Geo,
    ISNULL(bts.BTS_Region, 'Unassigned') AS Region,
    ISNULL(o.Total_ARR__c, 0)            AS Total_ARR,
    ISNULL(o.NACV__c, 0)                 AS NACV,
    o.PushCount,
    o.MEDDPICC_Score__c
FROM [sfdc_trf].[opportunity_live] o
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  o.Bookings_Team_static__c = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
WHERE o.IsDeleted = 0
  AND o.IsClosed  = 0
  AND o.CloseDate >= DATEADD(QUARTER, DATEDIFF(QUARTER, 0, GETDATE()), 0)
  AND o.CloseDate <  DATEADD(QUARTER, DATEDIFF(QUARTER, 0, GETDATE()) + 1, 0)
ORDER BY o.CloseDate, o.Total_ARR__c DESC
```

---

## Pattern 9 — MEDDPICC completeness

```sql
SELECT
    o.Id,
    o.Name,
    o.StageName,
    o.CloseDate,
    ISNULL(o.Total_ARR__c, 0) AS Total_ARR,
    o.MEDDPICC_Score__c,
    (
        CASE WHEN o.M_Metrics_Details__c           IS NOT NULL AND o.M_Metrics_Details__c           <> '' THEN 1 ELSE 0 END +
        CASE WHEN o.E_Economic_Buyer_Details__c    IS NOT NULL AND o.E_Economic_Buyer_Details__c    <> '' THEN 1 ELSE 0 END +
        CASE WHEN o.DP_Decision_Process_Details__c IS NOT NULL AND o.DP_Decision_Process_Details__c <> '' THEN 1 ELSE 0 END +
        CASE WHEN o.IP_Identified_Pain_Details__c  IS NOT NULL AND o.IP_Identified_Pain_Details__c  <> '' THEN 1 ELSE 0 END +
        CASE WHEN o.DC_Technical_Win__c = 'Yes'                                                          THEN 1 ELSE 0 END +
        CASE WHEN o.Authority__c        IS NOT NULL                                                       THEN 1 ELSE 0 END +
        CASE WHEN o.Need__c             IS NOT NULL                                                       THEN 1 ELSE 0 END
    ) AS MEDDPICC_Fields_Complete
FROM [sfdc_trf].[opportunity_live] o
WHERE o.IsDeleted = 0
  AND o.IsClosed  = 0
ORDER BY o.MEDDPICC_Score__c ASC
```

---

## Pattern 10 — Partner pipeline

```sql
SELECT
    o.Primary_Partner__c,
    o.Partner_Deal_Type__c,
    ISNULL(bts.BTS_Geo, 'Unassigned')                             AS Geo,
    COUNT(*)                                                      AS Opp_Count,
    SUM(CASE WHEN o.StageName = 'Closed Won' THEN 1 ELSE 0 END)  AS Won_Count,
    CAST(SUM(CASE WHEN o.StageName = 'Closed Won' THEN 1 ELSE 0 END) AS FLOAT)
        / NULLIF(COUNT(CASE WHEN o.IsClosed = 1 THEN 1 END), 0)  AS Win_Rate,
    SUM(CASE WHEN o.IsClosed = 0 THEN ISNULL(o.Total_ARR__c, 0) END) AS Open_ARR,
    SUM(CASE WHEN o.StageName = 'Closed Won'
             THEN ISNULL(o.Total_ARR__c, 0) END)                 AS Won_ARR
FROM [sfdc_trf].[opportunity_live] o
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  o.Bookings_Team_static__c = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
WHERE o.IsDeleted = 0
  AND (o.LeadSource LIKE '%Partner%' OR o.Opportunity_Source__c LIKE '%Partner%')
GROUP BY o.Primary_Partner__c, o.Partner_Deal_Type__c, bts.BTS_Geo
ORDER BY Open_ARR DESC
```
