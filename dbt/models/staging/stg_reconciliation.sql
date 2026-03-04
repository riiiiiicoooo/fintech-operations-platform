/*
========================================
STAGING: Reconciliation
========================================
Purpose:
  Clean and standardize reconciliation run data from the automated
  reconciliation engine. This model tracks match rates, exceptions,
  and unmatched items across settlement sources.

Key Transformations:
  - Type cast amounts and counts to appropriate precision
  - Calculate match rate as percentage
  - Count exception categories for root cause analysis
  - Calculate unmatched amount variance
  - Classify reconciliation health status

Business Rules:
  - Match rate = matched_transactions / total_expected_transactions
  - Healthy reconciliation: match rate >= 99%
  - Unmatched amount = abs(expected_total - matched_total)
  - Exception categories tracked separately
  - Reconciliation runs completed daily/weekly/monthly per schedule
========================================
*/

with raw_reconciliation as (
  select
    reconciliation_id,
    settlement_id,
    reconciliation_run_date,
    total_expected_transactions,
    matched_transactions,
    expected_total_amount,
    matched_total_amount,
    unmatched_count,
    unmatched_amount,
    exception_count,
    exception_categories,
    resolution_notes,
    status,
    created_at,
    updated_at
  from {{ source('raw', 'reconciliation') }}
),

cleaned as (
  select
    -- Primary Key
    reconciliation_id::varchar as reconciliation_id,
    
    -- Foreign Keys
    settlement_id::varchar as settlement_id,
    
    -- Dates
    reconciliation_run_date::timestamp as reconciliation_run_date,
    created_at::timestamp as created_at,
    updated_at::timestamp as updated_at,
    
    -- Match Metrics
    coalesce(total_expected_transactions::integer, 0) as total_expected_transactions,
    coalesce(matched_transactions::integer, 0) as matched_transactions,
    coalesce(unmatched_count::integer, 0) as unmatched_count,
    
    case
      when coalesce(total_expected_transactions::integer, 0) > 0
      then round((coalesce(matched_transactions::integer, 0)::float / 
                  coalesce(total_expected_transactions::integer, 0)::float) * 100, 4)
      else 0.0000
    end as match_rate_pct,
    
    -- Amount Metrics
    coalesce(expected_total_amount::decimal(15,2), 0.00) as expected_total_amount,
    coalesce(matched_total_amount::decimal(15,2), 0.00) as matched_total_amount,
    coalesce(unmatched_amount::decimal(15,2), 0.00) as unmatched_amount,
    
    case
      when coalesce(expected_total_amount::decimal(15,2), 0.00) > 0
      then round((coalesce(unmatched_amount::decimal(15,2), 0.00) / 
                  coalesce(expected_total_amount::decimal(15,2), 0.00)) * 100, 4)
      else 0.0000
    end as unmatched_amount_variance_pct,
    
    -- Exception Metrics
    coalesce(exception_count::integer, 0) as exception_count,
    exception_categories::varchar as exception_categories,
    
    -- Reconciliation Health Status
    case
      when coalesce(matched_transactions::integer, 0)::float / 
           nullif(coalesce(total_expected_transactions::integer, 1)::float, 0) >= 0.99
      then 'HEALTHY'
      when coalesce(matched_transactions::integer, 0)::float / 
           nullif(coalesce(total_expected_transactions::integer, 1)::float, 0) >= 0.95
      then 'ACCEPTABLE'
      when coalesce(matched_transactions::integer, 0)::float / 
           nullif(coalesce(total_expected_transactions::integer, 1)::float, 0) >= 0.90
      then 'AT_RISK'
      else 'CRITICAL'
    end as reconciliation_health_status,
    
    -- Status
    upper(status) as reconciliation_status,
    
    case
      when upper(status) in ('COMPLETED', 'RESOLVED') then true
      when upper(status) in ('PENDING', 'IN_PROGRESS') then false
      else null
    end as is_reconciliation_complete,
    
    resolution_notes::varchar as resolution_notes,
    
    current_date() as snapshot_date
    
  from raw_reconciliation
  where reconciliation_id is not null
)

select * from cleaned
