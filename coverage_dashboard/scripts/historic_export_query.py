import pyodbc
import pandas as pd
import textwrap

# ── Connection parameters ──────────────────────────────────────────────────────
driver   = "ODBC Driver 18 for SQL Server"
server   = "syn-digital-zuse2-prod.sql.azuresynapse.net"   # Azure Synapse
database = "DedicatedSQLPool"

connection_string = f"""
Driver={{{driver}}};
Server={server};
Database={database};
Authentication=ActiveDirectoryInteractive;
Encrypt=yes;
TrustServerCertificate=no;
"""

# ── Run the query and grab the results into df_main ───────────────────────────
try:
    conn   = pyodbc.connect(connection_string, autocommit=True)
    cursor = conn.cursor()

    main_query_sql = textwrap.dedent("""
SELECT
Opp_Geo AS Geo,
CASE
    WHEN N.Booking_Team_Static = 'AMS Public Sector'
        THEN acc.Account_Owner_Bookings_Team__c
    ELSE N.Booking_Team_Static
END AS Territory,
acc.Account_Owner_Bookings_Team__c,
CASE
    WHEN N.Record_Type in ('Service','Platnium Support') THEN 'Recurring Services'
	WHEN N.Family in ('Tosca OSV', 'TTA','TEE','Tosca') THEN 'Tosca'
	WHEN N.Family in ('Testim','Testim Salesforce', 'TTA for SFDC', 'TTA for SNOW','TTA SNOW', 'Tricentis Device Cloud','Mobile') THEN 'Testim'
	WHEN N.Family in ('Tosca BI', 'Tosca DI') THEN 'Data Integrity'
	WHEN N.Family in ('Vera') THEN 'Vera'
	WHEN N.Family in ('qTest') THEN 'qTest'
	WHEN N.Family in ('LiveCompare') THEN 'LiveCompare'
	WHEN N.Family in ('NeoLoad') THEN 'NeoLoad'
	WHEN N.Family in ('Tricentis Sealights') THEN 'Sealights'
ELSE N.Family END AS [Product],
CASE
	WHEN N.Opportunity_Source_Logic = 'Lead Sourced' THEN 'Marketing Sourced' ELSE N.Opportunity_Source_Logic
END AS [Source],
N.Segment AS [Tier],
CASE 
  WHEN N.StageName IN ('Closed Deferred','Closed Lost') THEN 'Closed'
  WHEN N.StageName IN ('6 - Closed/Pending','Closed Won','Stage 5 - Closed Won') THEN 'Closed Won'
  WHEN N.StageName IN (
                     'Closed - Duplicate',
                     'Stage 6 - Closed - Admin',
                     'Stage 7 - Churned',
                     'Opportunity Rejected',
                     '0 - First Interaction'
                ) THEN 'Other'
                ELSE 'Open'
            END AS Stage,
CASE 
	WHEN N.Deal_Type = 'New Business' THEN 'New Customer' ELSE N.Deal_Type
END AS [Deal Type],
DATEADD(month,DATEDIFF(month,0,N.Stage_1_Start_Date_Corrected),0) AS [Create_Month],
DATEADD(month,DATEDIFF(month,0,N.Opp_Closed_Date), 0) AS [Opp Close Date],
N.NACV_USD - N.Uplift_USD AS [Product NACV],
N.Segment,
N.Opportunity_Source_Logic,
N.Opportunity_id AS [Opportunity ID],
N.Accountid AS [Account ID],
N.stage_1_start_date_corrected AS [Discovery Date],
N.Deal_Type AS [Type],
N.Booking_Team_Static,
CASE
	WHEN M.qualified_stage IS NULL THEN 'P2' ELSE M.qualified_stage
END AS [Qualified Stage],
Age_In_Days_Stage_1
from 
[src].[sku_nacv_fact] AS N
LEFT JOIN [rep].[trf_marketing_opps_dimension] AS M ON M.Opportunity_Id = N.Opportunity_id
LEFT JOIN [src].[trf_account_dimension] AS acc ON acc.Account_Id = N.Accountid
WHERE 
Period = 'Period_1'
AND
N.Opp_Closed_Date >= '2023-01-01'
AND N.NACV_USD != 0
And N.record_type in ('Product','Service','Platinum support')
AND N.Deal_Type IN ('New Business', 'Expansion', 'Upsell','Professional services')
AND N.Booking_Team_Static NOT IN ('Account Management', 'Global', 'QAS Account Management')
AND N.Booking_Team_Static IS NOT NULL 
AND N.StageName NOT IN ('Closed - Duplicate', 'Stage 6 - Closed - Admin', 'Stage 7 - Churned', 'Opportunity Rejected','Stage 0 - Renewal Outreach Not Started','0 - First Interaction')
    """)
    
    cursor.execute(main_query_sql)
    rows       = cursor.fetchall()
    columns    = [col[0] for col in cursor.description]
    df_main    = pd.DataFrame.from_records(rows, columns=columns)

    # Show a preview of df_main
    df_main.head()

    # Optionally save to CSV
    # df_main.to_csv("output.csv", index=False)

except pyodbc.Error as ex:
    print(f"Database error: {ex}")
except Exception as e:
    print(f"General error: {e}")
finally:
    try:
        cursor.close()
        conn.close()
    except:
        pass
