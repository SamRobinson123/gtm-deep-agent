-- Live SKU bookings pull from [src].[sku_nacv_fact] (PLAN §3.5 / §6.3).
--
-- Grain note: PLAN documents this table as "one row per (opportunity, SKU)".
-- The inner GROUP BY collapses each (opp, SKU) to one row; the outer GROUP BY
-- rolls SKUs up to ONE ROW PER (OPP, PRODUCT FAMILY) — SKU-LINE attribution
-- (Sam, 2026-06-07): each family gets exactly its own lines' dollars, matching
-- Historic.xlsx's product cut (Tosca FY25 Q1 = $7.73M, not the $3.7M the old
-- whole-opp MIN() attribution gave it). Families partition each opp's dollars,
-- so consumers that group by opp-level dimensions (geo / deal type / tier) sum
-- to identical totals as the old one-row-per-opp grain; only the product cut
-- changes. The dashboard's per-product booked therefore ties Historic.xlsx
-- product-by-product, not just in total.
--
-- NPA rows are KEPT (2026-05-29). `NPA…` Product_Ids are NOT junk placeholders:
-- they carry real negative adjustments (de-bookings / credits / discounts) that
-- the source-of-truth booked figure nets in. Reconciled opp-by-opp against the
-- user's Historic.xlsx Closed-Won export: dropping them over-stated FY26 Q1 by
-- ~$1.75M (e.g. opp 006Po00000ckrxtIAA carried a -657,167 NPA adjustment;
-- including it nets that opp from 737,297 → 80,130, matching Historic exactly).
-- The matching NPA `Uplift`-record-type add-back rows are still excluded — they
-- fail both the Record_Type filter (Record_Type='Uplift') and the team filter
-- (Booking_Team_Static='Account Management') — so the SUM nets correctly.
-- (Earlier "drop NPA" guidance was a corruption-era artifact: while every row
-- was duplicated ~32x the NPA negatives blew up too; with the source fixed they
-- are legitimate and must be summed in.)
--
-- HISTORY (2026-05-28 → 2026-05-29): the source briefly returned ~32
-- byte-identical copies of every (opp, SKU) with the NACV value itself
-- inflated ~32x, making `booked` read ~10x high. That upstream blow-up was
-- FIXED at source on/before 2026-05-29 (verified: opp 0068c000010exYyAAI now
-- has 1 row at NACV_USD = 525,424, formerly 16,813,566 = exactly 32x; 0
-- identical-copy dups remain anywhere in 2024-2026).
--
-- Inner collapse = SUM, not MAX. A handful of (opp, SKU) pairs legitimately
-- carry TWO rows — a booking and a later negative correction/reversal (e.g.
-- +248,080 with a -151,844 revision → net 96,236; or +4,163 with -4,163 → a
-- full unwind to 0). MAX kept only the larger row and discarded the negative
-- adjustment, over-stating booked. SUM nets them to the correct current value.
-- (MAX was the right defense ONLY while the 32x identical-copy corruption was
-- live; with that gone, SUM is correct and safe. If `booked` ever jumps high
-- again, re-check for identical-copy regression — SUM would re-inflate those.)
--
-- Quarter_Start_Segment: same earliest-quarter account tier as snapshot.sql —
-- each account keeps its INITIAL/original tier (deliberate: accounts graduate
-- tiers over time; Historic.xlsx shows the current tier, we show the starting
-- one). Joined here so the segment page's week-13 live booked override can
-- carry the same tier convention as its snapshot-sourced weeks 1–12.
WITH account_segment AS (
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
    deduped.Opportunity_ID,
    deduped.Booking_Team_Static,
    deduped.Deal_Type,
    deduped.Opp_Closed_Date,
    deduped.Product,
    SUM(deduped.Product_NACV) AS Product_NACV,
    MIN(seg.QuarterStartSegment) AS Quarter_Start_Segment
FROM (
    SELECT
        N.Opportunity_id          AS Opportunity_ID,
        N.AccountId,
        N.Booking_Team_Static,
        N.Deal_Type,
        N.Opp_Closed_Date,
        N.Product_Id,
        SUM(N.NACV_USD - N.Uplift_USD) AS Product_NACV,
        -- Per-SKU canonical family (each SKU has exactly one). The outer query
        -- groups by this, so each family carries its own lines' dollars —
        -- Historic.xlsx's product cut.
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
    WHERE N.Period = 'Period_1'
      AND N.Opp_Closed_Date >= '{start_date}'
      AND N.Opp_Closed_Date <= '{end_date}'
      AND N.NACV_USD != 0
      AND N.Record_Type IN ('Product','Service','Platinum support')
      AND N.Deal_Type IN ('New Business','Expansion','Upsell','Professional services')
      AND N.Booking_Team_Static NOT IN ('Account Management','Global','QAS Account Management')
      AND N.Booking_Team_Static IS NOT NULL
      AND N.StageName IN ('6 - Closed/Pending','Closed Won','Stage 5 - Closed Won')
    -- collapse each (opp, SKU) to one row, netting corrections within the SKU
    GROUP BY N.Opportunity_id, N.AccountId, N.Booking_Team_Static, N.Deal_Type, N.Opp_Closed_Date, N.Product_Id
) deduped
LEFT JOIN account_segment AS seg ON seg.Id = deduped.AccountId
GROUP BY deduped.Opportunity_ID, deduped.Booking_Team_Static, deduped.Deal_Type, deduped.Opp_Closed_Date, deduped.Product;
