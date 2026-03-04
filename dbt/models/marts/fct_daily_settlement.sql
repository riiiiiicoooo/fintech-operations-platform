/*
========================================
FACT: Daily Settlement
========================================
Purpose:
  Aggregate settlement activity and efficiency metrics on a daily basis.
  This fact table supports operational monitoring of settlement cycles,
  payment processing performance, and transaction exception tracking.

Grain:
  One row per settlement batch per day

Key Metrics:
  - Transaction volume and count
  - Settlement amount and net proceeds
  - Average processing time
  - Settlement timeliness and delays
  - Exception rates and categories
  - Fee analysis
  - Match rate from reconciliation

Business Logic:
  - Groups transactions by settlement batch date
  - Calculates percentiles for processing time (p50, p95, p99)
  - Identifies problematic settlement periods
  - Tracks cumulative daily performance trends
========================================
*/

with settlement_base as (
  select
    s.settlement_batch_date as settlement_date,
    s.settlement_id,
    s.payment_processor_id,
    s.transaction_count,
    s.total_settlement_amount,
    s.settlement_fees,
    s.net_settlement_amount,
    s.settlement_delay_days,
    s.settlement_timeliness,
    s.is_settlement_finalized
  from {{ ref('stg_settlements') }} s
),

transaction_metrics as (
  select
    cast(t.created_at as date) as transaction_date,
    count(*) as transaction_count,
    count(case when t.transaction_outcome = 'SUCCESS' then 1 end) as successful_transactions,
    count(case when t.transaction_outcome = 'FAILED' then 1 end) as failed_transactions,
    sum(case when t.transaction_outcome = 'SUCCESS' then t.amount_usd else 0 end) as total_successful_amount,
    avg(case when t.transaction_outcome = 'SUCCESS' then t.processing_time_ms else null end) as avg_processing_time_ms,
    percentile_cont(0.50) within group (order by t.processing_time_ms) as p50_processing_time_ms,
    percentile_cont(0.95) within group (order by t.processing_time_ms) as p95_processing_time_ms,
    percentile_cont(0.99) within group (order by t.processing_time_ms) as p99_processing_time_ms,
    count(distinct t.payment_method) as payment_method_count,
    max(t.amount_usd) as max_transaction_amount
  from {{ ref('stg_transactions') }} t
  where t.transaction_outcome in ('SUCCESS', 'FAILED')
  group by cast(t.created_at as date)
),

reconciliation_metrics as (
  select
    cast(r.reconciliation_run_date as date) as reconciliation_date,
    avg(r.match_rate_pct) as avg_match_rate_pct,
    min(r.match_rate_pct) as min_match_rate_pct,
    count(case when r.reconciliation_health_status = 'HEALTHY' then 1 end) as healthy_reconciliation_batches,
    count(case when r.reconciliation_health_status in ('AT_RISK', 'CRITICAL') then 1 end) as problem_reconciliation_batches,
    sum(r.exception_count) as total_exceptions,
    sum(r.unmatched_amount) as total_unmatched_amount
  from {{ ref('stg_reconciliation') }} r
  group by cast(r.reconciliation_run_date as date)
),

combined_metrics as (
  select
    coalesce(tm.transaction_date, rm.reconciliation_date) as metric_date,
    coalesce(tm.transaction_count, 0) as daily_transaction_count,
    coalesce(tm.successful_transactions, 0) as daily_successful_transactions,
    coalesce(tm.failed_transactions, 0) as daily_failed_transactions,
    coalesce(tm.total_successful_amount, 0.00) as daily_successful_amount,
    
    case
      when coalesce(tm.transaction_count, 0) > 0
      then round((coalesce(tm.successful_transactions, 0)::float / coalesce(tm.transaction_count, 0)::float) * 100, 2)
      else 0.00
    end as daily_success_rate_pct,
    
    round(coalesce(tm.avg_processing_time_ms, 0), 1) as avg_processing_time_ms,
    round(coalesce(tm.p50_processing_time_ms, 0), 1) as p50_processing_time_ms,
    round(coalesce(tm.p95_processing_time_ms, 0), 1) as p95_processing_time_ms,
    round(coalesce(tm.p99_processing_time_ms, 0), 1) as p99_processing_time_ms,
    coalesce(tm.payment_method_count, 0) as payment_method_count,
    round(coalesce(tm.max_transaction_amount, 0.00), 2) as max_transaction_amount,
    
    -- Reconciliation Metrics
    round(coalesce(rm.avg_match_rate_pct, 0.00), 4) as daily_avg_match_rate_pct,
    round(coalesce(rm.min_match_rate_pct, 0.00), 4) as daily_min_match_rate_pct,
    coalesce(rm.healthy_reconciliation_batches, 0) as healthy_reconciliation_batches,
    coalesce(rm.problem_reconciliation_batches, 0) as problem_reconciliation_batches,
    coalesce(rm.total_exceptions, 0) as daily_total_exceptions,
    round(coalesce(rm.total_unmatched_amount, 0.00), 2) as daily_total_unmatched_amount,
    
    current_date() as processed_date
  from transaction_metrics tm
  full outer join reconciliation_metrics rm on tm.transaction_date = rm.reconciliation_date
)

select
  metric_date,
  daily_transaction_count,
  daily_successful_transactions,
  daily_failed_transactions,
  round(daily_successful_amount, 2) as daily_successful_amount,
  daily_success_rate_pct,
  avg_processing_time_ms,
  p50_processing_time_ms,
  p95_processing_time_ms,
  p99_processing_time_ms,
  payment_method_count,
  max_transaction_amount,
  daily_avg_match_rate_pct,
  daily_min_match_rate_pct,
  healthy_reconciliation_batches,
  problem_reconciliation_batches,
  daily_total_exceptions,
  daily_total_unmatched_amount,
  
  case
    when daily_success_rate_pct >= 99.5 and daily_avg_match_rate_pct >= 99.0 then 'EXCELLENT'
    when daily_success_rate_pct >= 98.0 and daily_avg_match_rate_pct >= 95.0 then 'GOOD'
    when daily_success_rate_pct >= 95.0 and daily_avg_match_rate_pct >= 90.0 then 'ACCEPTABLE'
    else 'PROBLEM'
  end as daily_operational_health,
  
  processed_date
  
from combined_metrics
order by metric_date desc
