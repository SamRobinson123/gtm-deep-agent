# Opportunity Field History — opportunity_field_history_live

**When to load**: You need to know when a note field (NextStep, SA_Next_Steps__c, Loss_Notes__c,
etc.) was last changed, who changed it, or what it said before — e.g. judging whether a note is
fresh evidence or stale. Not for opp-level current state → [`tables/opportunity.md`](opportunity.md).

**Source table**: `[sfdc_trf].[opportunity_field_history_live]`
**Primary key**: none — natural key is `(OpportunityId, Field, CreatedDate)`
**Row grain**: one row per field-change event
**Related**: [`tables/opportunity.md`](opportunity.md) (the fields this tracks)

---

## Columns

| Column | Semantic | Notes |
|--------|----------|-------|
| `OpportunityId` | FK → `opportunity_live.Id` | 18-char SFDC ID, same join key the rest of the pipeline uses |
| `Field` | API name of the field that changed | e.g. `SA_Next_Steps__c`, `NextStep` — **`NextStep` has no `__c`, the 8 custom fields do** |
| `OldValue` | Field value before the change | Free text, null if the field was previously empty |
| `NewValue` | Field value after the change | Free text |
| `CreatedDate` | When the change was made | The real timestamp — sort/filter on this |
| `CreatedById` | SFDC User ID of who made the change | **Not resolved to a name.** No `user_live`-equivalent table exists in this schema (confirmed: `sfdc_trf.user_live` does not exist) — treat as an opaque ID, never fabricate a name for it |
| `snaplogic_extract_date`, `snap_source_hash`, `dif_load_date` | ETL infrastructure | Never use in business queries |

## Caveat: overlapping history sources

Some fields (`SA_Next_Steps__c` observed) already contain a manually-typed running log *inside the
field's own text* (e.g. `18/11/25 - SK ...` lines prefixed on every edit). This means this table's
`CreatedDate` can overlap or duplicate the dates already embedded in `next_steps_history`'s parsed
log (`_parse_history` in `pipeline/gtm_shared_candidates.py`). Treat them as two views of similar
information, not double-count them as separate evidence.

## Field-name mapping (opp_notes key ↔ SFDC Field value)

| `opp_notes` key | SFDC `Field` value |
|---|---|
| `next_step` | `NextStep` |
| `sa_next_steps` | `SA_Next_Steps__c` |
| `manager_notes` | `Manager_Notes__c` |
| `ae_notes` | `Account_Executive_notes__c` |
| `competitive_environment_notes` | `Competitive_Environment_Notes__c` |
| `description` | `Description` |
| `loss_notes` | `Loss_Notes__c` |
| `technical_win_loss_comments` | `Technical_Win_Loss_Comments__c` |

`Next_Steps_History__c` is deliberately not pulled: it's an append-only log already parsed
(with dates) into `opp_notes.next_steps_log`, so its old/new full-blob lineage would only
duplicate that at ~2x token cost per revision.
