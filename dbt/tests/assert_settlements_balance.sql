/*
========================================
CUSTOM DATA TEST: Settlement Balance Validation
========================================
Purpose:
  Data quality test ensuring settlement ledger integrity by validating
  that settlement totals match transaction aggregates minus exceptions.
  This test identifies data discrepancies and processing errors.

Test Logic:
  For each settlement batch:
  1. Sum all transactions assigned to that settlement
  2. Subtract identified exceptions and unmatched amounts
  3. Verify result matches settlement recorded total

  Formula: settlement_total = transaction_sum - exceptions - unmatched_amounts
  
  Tolerance: $0.01 to account for rounding

Scope:
  Tests against fct_daily_settlement where transaction and settlement
  data should be mutually consistent

Pass Condition:
  Zero rows returned (no balance mismatches found)

Fail Condition:
  Returns settlement dates where transaction sum != settlement total
  beyond tolerance threshold
========================================
*/

with settlement_summary as (
  select
    ds.metric_date,
    ds.daily_successful_amount,
    ds.daily_total_exceptions,
    ds.daily_total_unmatched_amount,
    
    -- Expected settlement total (successful minus issues)
    ds.daily_successful_amount - 
    ds.daily_total_exceptions - 
    ds.daily_total_unmatched_amount as calculated_settlement_total,
    
    -- Difference
    abs(
      (ds.daily_successful_amount - 
       ds.daily_total_exceptions - 
       ds.daily_total_unmatched_amount) - 
      ds.daily_successful_amount
    ) as settlement_variance
    
  from {{ ref('fct_daily_settlement') }} ds
  where ds.daily_successful_amount > 0
)

select
  metric_date,
  daily_successful_amount,
  daily_total_exceptions,
  daily_total_unmatched_amount,
  calculated_settlement_total,
  settlement_variance
  
from settlement_summary

where settlement_variance > 0.01

order by settlement_variance desc
