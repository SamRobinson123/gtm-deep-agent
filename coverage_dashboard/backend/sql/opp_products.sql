-- ALL product families per opportunity (SKU-inclusive, multi-product), with the
-- per-family allocated NACV. PRODUCT PAGE ONLY — drives `_product_share_map`.
--
-- Source: [src].[trf_sku_nacv_npa_allocated] — the NPA-allocated version of
-- sku_nacv_fact (Sam, 2026-06-22). Upstream has ALREADY done the allocation in
-- the data itself: each no-product line's `Family` is reassigned to a real
-- product AND its `NACV_USD` is already split into one row per target family
-- (the fanned rows' NACV_USD sum back to the original line). `allocation_weight`
-- is just provenance metadata (each fanned row's share); it is NOT a multiplier.
-- So the correct per-family dollar is plain `SUM(NACV_USD)` — multiplying by
-- allocation_weight would re-apply the split a SECOND time and overstate booked
-- ~2.6% (verified opp-level vs sku_nacv_fact, 2026-06-22). With raw NACV_USD the
-- table ties sku_nacv_fact / Historic to the dollar.
--
-- Grouping by the canonical (allocated) Family gives each opp's product MIX;
-- `_product_share_map` turns it into shares used to split BOTH the snapshot's
-- whole-opp Cal_IACV (open pipe) and each opp's booked total across products, so
-- product rows still sum to the page-1 totals. A small residual the upstream
-- could not allocate stays null/No_Product_Assigned → canonical "Other" (we
-- deliberately leave it there rather than defaulting to a real product — Sam).
--
-- Same canonical CASE as snapshot.sql's opp_product CTE (plus Additional
-- Services / Flood, which appear in the allocated `Family`).
SELECT
    N.Opportunity_id AS Opportunity_ID,
    CASE
        WHEN N.Record_Type IN ('Service','Platnium Support') THEN 'Recurring Services'
        WHEN N.Family IN ('Additional Services') THEN 'Recurring Services'
        WHEN N.Family IN ('Tosca OSV','TTA','TEE','Tosca') THEN 'Tosca'
        WHEN N.Family IN ('Testim','Testim Salesforce','TTA for SFDC','TTA for SNOW','TTA SNOW','Tricentis Device Cloud','Mobile') THEN 'Testim'
        WHEN N.Family IN ('Tosca BI','Tosca DI') THEN 'Data Integrity'
        WHEN N.Family IN ('Vera') THEN 'Vera'
        WHEN N.Family IN ('qTest') THEN 'qTest'
        WHEN N.Family IN ('LiveCompare') THEN 'LiveCompare'
        WHEN N.Family IN ('NeoLoad','Flood') THEN 'NeoLoad'
        WHEN N.Family IN ('Tricentis Sealights') THEN 'Sealights'
        ELSE N.Family
    END AS Product,
    SUM(N.NACV_USD) AS nacv
FROM [src].[trf_sku_nacv_npa_allocated] AS N
WHERE Period = 'Period_1'
  AND N.NACV_USD != 0
  AND N.Record_Type IN ('Product','Service','Platinum Support')
GROUP BY
    N.Opportunity_id,
    CASE
        WHEN N.Record_Type IN ('Service','Platnium Support') THEN 'Recurring Services'
        WHEN N.Family IN ('Additional Services') THEN 'Recurring Services'
        WHEN N.Family IN ('Tosca OSV','TTA','TEE','Tosca') THEN 'Tosca'
        WHEN N.Family IN ('Testim','Testim Salesforce','TTA for SFDC','TTA for SNOW','TTA SNOW','Tricentis Device Cloud','Mobile') THEN 'Testim'
        WHEN N.Family IN ('Tosca BI','Tosca DI') THEN 'Data Integrity'
        WHEN N.Family IN ('Vera') THEN 'Vera'
        WHEN N.Family IN ('qTest') THEN 'qTest'
        WHEN N.Family IN ('LiveCompare') THEN 'LiveCompare'
        WHEN N.Family IN ('NeoLoad','Flood') THEN 'NeoLoad'
        WHEN N.Family IN ('Tricentis Sealights') THEN 'Sealights'
        ELSE N.Family
    END;
