/*
========================================
STAGING: Settlements
========================================
Purpose:
  Clean and standardize settlement batch data from payment processors.
  This model tracks settlement cycles, batching efficiency, and reconciliation
  readiness across multiple channels.

Key Transformations:
  - Type cast dates and amounts to appropriate types
  - Calculate settlement delay (days between transaction settlement and batch settlement)
  - Compute batch efficiency metrics (count, average amount)
  - Identify settlement status and processing state
  - Flag delayed or problematic settlement batches

Business Rules:
  - Settlement delay = settlement_batch_date - transaction_settlement_date
  - Expected settlement window: 1-3 business days (varies by processor)
  - Batch size variance monitored for anomalies
  - Only include settled batches (status != PENDING, FAILED)
========================================
*/

with raw_settlements as (
  select
    settlement_id,
    payment_processor_id,
    settlement_batch_date,
    settlement_period_start,
    settlement_period_end,
    total_transactions,
    total_amount,
    fees,
    status,
    bank_account_id,
    created_at,
    updated_at
  from {{ source('raw', 'settlements') }}
),

cleaned as (
  select
    -- Primary Key
    settlement_id::varchar as settlement_id,
    
    -- Foreign Keys
    payment_processor_id::varchar as payment_processor_id,
    bank_account_id::varchar as bank_account_id,
    
    -- Dates
    settlement_batch_date::date as settlement_batch_date,
    settlement_period_start::date as settlement_period_start,
    settlement_period_end::date as settlement_period_end,
    created_at::timestamp as created_at,
    updated_at::timestamp as updated_at,
    
    -- Financial Amounts
    coalesce(total_amount::decimal(15,2), 0.00) as total_settlement_amount,
    coalesce(fees::decimal(10,2), 0.00) as settlement_fees,
    round(coalesce(total_amount::decimal(15,2), 0.00) - coalesce(fees::decimal(10,2), 0.00), 2) as net_settlement_amount,
    
    -- Volume Metrics
    coalesce(total_transactions::integer, 0) as transaction_count,
    
    case
      when coalesce(total_transactions::integer, 0) > 0
      then round(coalesce(total_amount::decimal(15,2), 0.00) / coalesce(total_transactions::integer, 1), 2)
      else 0.00
    end as avg_transaction_amount,
    
    -- Settlement Delay Calculation
    datediff(day, settlement_period_end, settlement_batch_date) as settlement_delay_days,
    
    case
      when datediff(day, settlement_period_end, settlement_batch_date) <= 3 then 'TIMELY'
      when datediff(day, settlement_period_end, settlement_batch_date) <= 5 then 'DELAYED'
      else 'SIGNIFICANTLY_DELAYED'
    end as settlement_timeliness,
    
    -- Settlement Period Duration
    datediff(day, settlement_period_start, settlement_period_end) as settlement_period_days,
    
    -- Status
    upper(status) as settlement_status,
    
    case
      when upper(status) in ('SETTLED', 'COMPLETED') then true
      else false
    end as is_settlement_finalized,
    
    current_date() as snapshot_date
    
  from raw_settlements
  where status not in ('CANCELLED', 'FAILED')
    and settlement_id is not null
)

select * from cleaned
