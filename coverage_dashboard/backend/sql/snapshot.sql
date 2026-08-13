WITH opp_product AS (
    SELECT
        N.Opportunity_id AS Opportunity_ID,
        MIN(CASE
            WHEN N.Record_Type IN ('Service','Platnium Support') THEN 'Recurring Services'
            WHEN N.Family IN ('Tosca OSV','TTA','TEE','Tosca') THEN 'Tosca'
            WHEN N.Family IN ('Testim','Testim Salesforce','TTA for SFDC','TTA for SNOW','TTA SNOW','Tricentis Device Cloud','Mobile') THEN 'Testim'
            WHEN N.Family IN ('Tosca BI','Tosca DI') THEN 'Data Integrity'
            WHEN N.Family IN ('Vera') THEN 'Vera'
            WHEN N.Family IN ('qTest') THEN 'qTest'
            WHEN N.Family IN ('LiveCompare') THEN 'LiveCompare'
            WHEN N.Family IN ('NeoLoad') THEN 'NeoLoad'
            WHEN N.Family IN ('Tricentis Sealights') THEN 'Sealights'
            ELSE N.Family
        END) AS Product
    FROM [src].[sku_nacv_fact] AS N
    WHERE Period = 'Period_1'
      AND N.NACV_USD != 0
      AND N.record_type IN ('Product','Service','Platinum support')
    GROUP BY N.Opportunity_id
),
-- Account-level segment (Tier 1/2/3). The source holds one row per account per
-- quarter (QuarterStartDate), but we deliberately do NOT track the segment
-- change over time — we collapse to a single segment per account by taking its
-- EARLIEST quarter's value (the account's initial/original tier), so each opp
-- gets one stable tier via Account_Id.
account_segment AS (
    SELECT Id, QuarterStartSegment
    FROM (
        SELECT
            Id,
            QuarterStartSegment,
            ROW_NUMBER() OVER (PARTITION BY Id ORDER BY QuarterStartDate ASC) AS rn
        FROM [rpt_cx].[account_segment_quarterly]
    ) ranked
    WHERE rn = 1
)
SELECT
    s.Opp_Id AS Opportunity_ID,
    s.Cal_IACV,
    s.snapshot_date,
    s.CloseDate,
    s.Raw_Stage,
    s.Bookings_Team_static,
    s.Opp_Type AS Deal_Type,
    CASE
        WHEN s.Raw_Stage IN ('Closed Deferred','Closed Lost') THEN 'Closed'
        WHEN s.Raw_Stage IN ('6 - Closed/Pending','Closed Won','Stage 5 - Closed Won') THEN 'Closed Won'
        WHEN s.Raw_Stage IN ('Closed - Duplicate','Stage 6 - Closed - Admin','Stage 7 - Churned',
                             'Opportunity Rejected','0 - First Interaction') THEN 'Other'
        ELSE 'Open'
    END AS Stage,
    op.Product,
    seg.QuarterStartSegment AS Quarter_Start_Segment
FROM [rep].[trf_opp_daily_snapshot_new] AS s
LEFT JOIN opp_product AS op ON op.Opportunity_ID = s.Opp_Id
LEFT JOIN account_segment AS seg ON seg.Id = s.Account_Id
WHERE CAST(s.snapshot_date AS DATE) >= '{snap_start}'
  AND CAST(s.snapshot_date AS DATE) <= '{fy_end}'
  AND CAST(s.CloseDate AS DATE)   >= '{fy_start}'
  AND CAST(s.CloseDate AS DATE)   <= '{fy_end}'
  AND s.Cal_IACV != 0
  AND s.Bookings_Team_static NOT IN ('Account Management','Global','QAS Account Management')
  AND s.Bookings_Team_static IS NOT NULL
  AND s.Raw_Stage NOT IN ('Closed - Duplicate','Stage 6 - Closed - Admin','Stage 7 - Churned',
                          'Opportunity Rejected','Stage 0 - Renewal Outreach Not Started','0 - First Interaction');
