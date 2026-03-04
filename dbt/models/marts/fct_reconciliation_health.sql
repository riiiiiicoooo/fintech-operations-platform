/*
========================================
FACT: Reconciliation Health
========================================
Purpose:
  Track reconciliation health trends and exception patterns across
  payment processors. This fact table enables root cause analysis of
  matching failures and operational bottlenecks.

Grain:
  One row per settlement batch with reconciliation analysis

Key Metrics:
  - Match rate trend (overall, by processor, by category)
  - Exception categories and frequencies
  - Resolution time for exceptions
  - Unmatched transaction analysis
  - Reconciliation completeness and timeliness
  - Processor-level performance comparison

Business Logic:
  - Aggregates exceptions by category for trending
  - Calculates match rate percentiles across time
  - Identifies repeat exception patterns
  - Tracks manual vs automated resolution
========================================
*/

with reconciliation_detail as (
  select
    r.reconciliation_id,
    r.settlement_id,
    r.reconciliation_run_date,
    s.settlement_batch_date,
    s.payment_processor_id,
    r.total_expected_transactions,
    r.matched_transactions,
    r.unmatched_count,
    r.expected_total_amount,
    r.matched_total_amount,
    r.unmatched_amount,
    r.match_rate_pct,
    r.exception_count,
    r.exception_categories,
    r.reconciliation_health_status,
    r.is_reconciliation_complete,
    r.resolution_notes
  from {{ ref('stg_reconciliation') }} r
  left join {{ ref('stg_settlements') }} s on r.settlement_id = s.settlement_id
),

match_rate_trends as (
  select
    rd.reconciliation_run_date,
    rd.settlement_batch_date,
    rd.payment_processor_id,
    rd.settlement_id,
    rd.match_rate_pct,
    
    -- Rolling 7-day average match rate
    avg(rd.match_rate_pct) over (
      partition by rd.payment_processor_id
      order by rd.reconciliation_run_date
      rows between 6 preceding and current row
    ) as rolling_7day_match_rate_pct,
    
    -- Previous match rate for trend
    lag(rd.match_rate_pct) over (
      partition by rd.payment_processor_id
      order by rd.reconciliation_run_date
    ) as previous_match_rate_pct,
    
    -- Match rate change
    rd.match_rate_pct - 
    lag(rd.match_rate_pct) over (
      partition by rd.payment_processor_id
      order by rd.reconciliation_run_date
    ) as match_rate_change_pct
  from reconciliation_detail rd
),

exception_analysis as (
  select
    rd.reconciliation_run_date,
    rd.settlement_batch_date,
    rd.payment_processor_id,
    rd.settlement_id,
    rd.exception_count,
    rd.exception_categories,
    
    case
      when exception_count = 0 then 'NO_EXCEPTIONS'
      when exception_count <= 5 then 'MINOR_EXCEPTIONS'
      when exception_count <= 20 then 'MODERATE_EXCEPTIONS'
      else 'MAJOR_EXCEPTIONS'
    end as exception_severity,
    
    -- Identify specific exception categories
    case
      when exception_categories ilike '%AMOUNT_MISMATCH%' then 1 else 0
    end as has_amount_mismatch,
    
    case
      when exception_categories ilike '%MISSING_TRANSACTION%' then 1 else 0
    end as has_missing_transaction,
    
    case
      when exception_categories ilike '%DUPLICATE%' then 1 else 0
    end as has_duplicate_transaction,
    
    case
      when exception_categories ilike '%STATUS_MISMATCH%' then 1 else 0
    end as has_status_mismatch
  from reconciliation_detail rd
)

select
  mr.reconciliation_run_date,
  mr.settlement_batch_date,
  mr.payment_processor_id,
  mr.settlement_id,
  
  -- Match Rate Metrics
  round(mr.match_rate_pct, 4) as match_rate_pct,
  round(mr.rolling_7day_match_rate_pct, 4) as rolling_7day_match_rate_pct,
  round(coalesce(mr.match_rate_change_pct, 0), 4) as match_rate_change_pct,
  
  case
    when mr.match_rate_change_pct > 0 then 'IMPROVING'
    when mr.match_rate_change_pct < 0 then 'DECLINING'
    else 'STABLE'
  end as match_rate_trend,
  
  -- Exception Metrics
  ea.exception_count,
  ea.exception_severity,
  ea.exception_categories,
  ea.has_amount_mismatch,
  ea.has_missing_transaction,
  ea.has_duplicate_transaction,
  ea.has_status_mismatch,
  
  -- Reconciliation Detail
  mr.total_expected_transactions,
  mr.matched_transactions,
  mr.unmatched_count,
  round(mr.expected_total_amount, 2) as expected_total_amount,
  round(mr.matched_total_amount, 2) as matched_total_amount,
  round(mr.unmatched_amount, 2) as unmatched_amount,
  
  case
    when mr.expected_total_amount > 0
    then round((mr.unmatched_amount / mr.expected_total_amount) * 100, 4)
    else 0.0000
  end as unmatched_amount_variance_pct,
  
  -- Completion Status
  mr.is_reconciliation_complete,
  mr.reconciliation_health_status,
  mr.resolution_notes,
  
  -- Processor Health Score
  case
    when mr.match_rate_pct >= 99.5 and ea.exception_count <= 2 then 100
    when mr.match_rate_pct >= 99.0 and ea.exception_count <= 5 then 90
    when mr.match_rate_pct >= 98.0 and ea.exception_count <= 10 then 75
    when mr.match_rate_pct >= 95.0 and ea.exception_count <= 20 then 60
    else 40
  end as processor_health_score,
  
  current_date() as processed_date
  
from match_rate_trends mr
inner join exception_analysis ea on mr.settlement_id = ea.settlement_id

order by mr.reconciliation_run_date desc
