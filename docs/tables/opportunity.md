# Opportunity Table — opportunity_live

**When to load**: Current opp-level pipeline state, win/loss analysis, MEDDPICC, partner fields, people fields.  
**Requires first**: [`sql/conventions.md`](../sql/conventions.md)  
**Also load**: [`tables/territory-mapping.md`](territory-mapping.md) for geo grouping  
**Not for**: Product-level bookings → [`tables/sku-nacv-fact.md`](sku-nacv-fact.md) · Historical trend → [`tables/opp-daily-snapshot.md`](opp-daily-snapshot.md)

**Source table**: `[sfdc_trf].[opportunity_live]`  
**Primary key**: `Id` (SFDC 18-char opportunity ID)  
**Row grain**: One row per opportunity  
**Related**: [`sql/conventions.md`](../sql/conventions.md) (rules) · [`tables/territory-mapping.md`](territory-mapping.md) (geo join) · [`sql/patterns.md`](../sql/patterns.md) (query templates)

---

## Core identity

| Column | Notes |
|--------|-------|
| `Id` | Primary key — 18-char SFDC ID |
| `Name` | Opportunity name |
| `AccountId` | FK → `[sfdc_trf].[account_live]` |
| `OwnerId` | FK → `[sfdc_trf].[user_live]` (AE owner) |
| `RecordTypeId` | New Business vs Renewal vs Expansion |
| `ContactId` | Primary contact FK |
| `CreatedDate` | When opp entered the pipeline |
| `Legacy_ID__c` | Pre-migration ID (`OPP-XXXXXX` format) |
| `snaplogic_extract_date` | ETL field — do not use in business queries |
| `snap_source_hash` | ETL field — do not use in business queries |

---

## Stage and status

Won/lost is determined by `StageName` — see [`sql/conventions.md`](../sql/conventions.md) for full stage logic.

| Column | Notes |
|--------|-------|
| `StageName` | Source of truth for outcome — see stage values below |
| `IsClosed` | Derived from `StageName`: 0 = open, 1 = any closed stage |
| `IsWon` | Derived from `StageName`: 1 = `Closed Won` only |
| `ForecastCategoryName` | Pipeline · Upside · Commit · Omitted · Closed |
| `Manager_Forecast_Category__c` | Manager override |
| `LastStageChangeDate` | Last stage transition |
| `PushCount` | Times close date was pushed — key hygiene signal |
| `Days_Since_Last_Activity__c` | Activity recency |
| `Next_Steps_Last_Updated__c` | Last next steps update |

**Stage values**
```
Open:   1-Discovery
        2-Qualification/Analysis
        3-Technical/Executive Evaluation
        3b-Technical Evaluation
        4-Negotiation/Procurement

Closed: Closed Won          (IsWon = 1)
        Closed Lost         (IsWon = 0)
        Opportunity Rejected (IsWon = 0, ForecastCategory = Omitted)
```

**Stage entry timestamps**

| Static | Dynamic (recalculates on stage change) |
|--------|----------------------------------------|
| `X1_Discovery_Date__c` | `X1_Discovery_Date_Dynamic__c` |
| `X2_Qualification_Analysis_Date__c` | `X2_Qualification_Analysis_Date_Dynamic__c` |
| `X3_Technical_Executive_Evaluation_Date__c` | `X3_Technical_Executive_Eval_Date_Dynamic__c` |
| `X3b_Technical_Evaluation_Date__c` | `X3b_Technical_Evaluation_Date_Dynamic__c` |
| `X4_Negotiation_and_Business_Procurement__c` | `X4_Negotiation_Procurement_Date_Dynamic__c` |

---

## Financial fields

See [`sql/conventions.md`](../sql/conventions.md) for the full hierarchy rule. Short version: use `Total_ARR__c` and `NACV__c`, never `Amount`.

### Primary metrics

| Column | Notes |
|--------|-------|
| `Total_ARR__c` | Total ARR — primary pipeline metric |
| `NACV__c` | Net Annual Contract Value — primary booking metric |
| `ACV__c` | Annual Contract Value |
| `TCV__c` | Total Contract Value |
| `License_ARR__c` · `Services_ARR__c` · `Support_ARR__c` | ARR by component |
| `ATR__c` · `ATR_License__c` · `ATR_Services__c` · `ATR_Support__c` | Available to Renew |
| `CurrencyIsoCode` | USD / EUR / GBP — normalize before cross-geo aggregation |

### NACV year breakdown
`ARRow_NACV_Y1__c` · `ARRow_NACV_Y2__c` · `ARRow_NACV_Y3__c` · `NACV_for_Tracking__c`

### Per-product columns

Each product has: `{Product}_ARR__c` · `{Product}_ATR__c` · `{Product}_Uplift__c` · `{Product}_NACV__c` · `{Product}_expansion_upsell__c`

| Products |
|----------|
| `Tosca` · `Tosca_BI` · `Tosca_OSV` · `qTest` · `NeoLoad` · `Testim` · `Testim_Salesforce` |
| `LiveCompare` · `Mobile` · `TTA` · `TTA_for_SF` · `TDC` · `VERA` · `TEE` · `SeaLights` · `Agentic` · `CapIO` |

---

## Key date fields

| Column | Use for |
|--------|---------|
| `CloseDate` | When deal closed or is expected to close |
| `CreatedDate` | When opp entered the pipeline |
| `LastStageChangeDate` | Stage velocity calculations |
| `LastActivityDate` | Activity recency |
| `Booking_Date__c` | Finance booking date |
| `NACV_Booking_date__c` | NACV booking date |
| `Subscription_Start_Date__c` · `Order_Effective_Date__c` | Contract start |
| `Renewal_Date__c` · `Forecasting_Renewal_Date__c` | Renewal tracking |
| `POV_Start_Date__c` · `POV_End_Date__c` | Proof of value window |
| `Pilot_Start_Date__c` · `Pilot_End_Date__c` | Pilot window |

---

## Segmentation

| Column | Notes |
|--------|-------|
| `Type` | New Business · Expansion · Renewal |
| `Expansion_Type__c` | Cross-sell · Upsell (when Type = Expansion) |
| `Bookings_Team_static__c` | Join key → `[sharepoint].[Map_Booking_Team_Static_live]` for all geo/region/territory |
| `LeadSource` / `Opportunity_Source__c` | How opp originated |
| `GTM_Source__c` / `IB_OB_Source__c` | Inbound / Outbound |
| `Legal_Entity__c` | Tricentis USA Corp. · Tricentis GmbH · etc. |

---

## Pipeline health and MEDDPICC

| Column | Notes |
|--------|-------|
| `MEDDPICC_Score__c` | Composite MEDDPICC score |
| `Pipeline_Hygiene_Index__c` | Composite hygiene score |
| `Opp_Age_Score__c` · `Stage_Duration_Score__c` · `PushCount_Score__c` · `Next_Steps_Last_Updated_Score__c` | Sub-scores |
| `M_Metrics_Details__c` | Metrics (free text) |
| `E_Economic_Buyer_Details__c` | Economic buyer notes |
| `DP_Decision_Process_Details__c` · `DP_Mutual_Joint_Execution_Plan__c` | Decision process |
| `IP_Identified_Pain_Details__c` · `IP_Compelling_Event_Urgency__c` | Identified pain |
| `DC_Technical_Win__c` · `Technical_Win_SA__c` | Technical win flags |
| `Authority__c` · `Need__c` · `Mutual_Business_Case__c` | Remaining pillars |
| `Budget_Confirmed__c` · `Discovery_Completed__c` · `ROI_Analysis_Completed__c` | Milestone flags |

---

## Win/loss and competition

| Column | Notes |
|--------|-------|
| `Loss_Reason__c` · `Loss_Subcategory__c` · `Loss_Notes__c` | Loss data — only populated on `Closed Lost` |
| `Primary_Competitor__c` · `Competitor__c` | Competitor fields |
| `Reason_for_Downsell__c` | If ARR decreased |
| `Reason_Pushed_Out__c` · `PushCount` | Close date push history |
| `Renewal_Opportunity_Risk__c` · `Risk_One_Liner__c` | Risk signals |
| `Debrief_Feedback__c` · `What_were_the_major_drivers_for_their_de__c` | Post-close debrief |

---

## Partner fields

`Primary_Partner__c` · `Partner_Deal_Type__c` · `Partner_Involvment__c` · `Partner_Commission_Type__c`  
`Sourcing_Partner__c` · `Influence_Partner_1_GSI_SI_Reseller_T2__c` · `Service_Delivery_Partner__c`  
`Referral_Fee__c` · `Distributor__c` · `Primary_Partner_Manager__c`

---

## People and team

`OwnerId` (AE) · `Sales_Engineer__c` · `Customer_Success_Manager__c` · `Engagement_Manager__c`  
`Champion_Contact__c` · `Executive_Sponsor__c` · `Renewals_Manager__c`  
`DevOps_Opportunity_Owner__c` · `Growth_Opportunity_Owner__c` · `Customer_Growth_Solutions_Architect__c`

---

## Relationships to other tables

| Table | Alias | Join | What it adds |
|-------|-------|------|-------------|
| `[sharepoint].[Map_Booking_Team_Static_live]` | `bts` | `o.Bookings_Team_static__c = bts.Bookings_Team_Static AND bts.ActiveTeam = 'Active'` | Geo, Region, Territory, Product Family, FLM, VP — see [`tables/territory-mapping.md`](territory-mapping.md) |
| `[sfdc_trf].[account_live]` | `a` | `o.AccountId = a.Id` | Industry, size, segment — *context file coming* |
| `[sfdc_trf].[user_live]` | `u` | `o.OwnerId = u.Id` | AE details, manager, role — *context file coming* |
| `[sfdc_trf].[contact_live]` | `c` | `o.ContactId = c.Id` | Primary contact — *context file coming* |
| `[sfdc_trf].[opportunitylineitem_live]` | `oli` | `oli.OpportunityId = o.Id` | Products, pricing, line items — *context file coming* |
| `[sfdc_trf].[opportunityhistory_live]` | `oh` | `oh.OpportunityId = o.Id` | Stage change history — *context file coming* |
| `[sfdc_trf].[task_live]` | `t` | `t.WhatId = o.Id` | Activities — *context file coming* |
| `call_transcripts.csv` | `ct` | `ct.opp_id = o.Id` | Call signals |
| `scored_opps.parquet` | `s` | `s.Opportunity_Id = o.Id` | ML win probability |
---

## Handoff

- Writing a query against this table → already have what you need + [`sql/conventions.md`](../sql/conventions.md)
- Need geo/region/territory columns → load [`tables/territory-mapping.md`](territory-mapping.md)
- Need product-level ARR breakdown → load [`tables/sku-nacv-fact.md`](sku-nacv-fact.md) instead
- Need historical pipeline state → load [`tables/opp-daily-snapshot.md`](opp-daily-snapshot.md) instead
