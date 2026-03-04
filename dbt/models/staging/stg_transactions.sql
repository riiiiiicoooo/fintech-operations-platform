/*
========================================
STAGING: Transactions
========================================
Purpose:
  Clean and standardize raw payment transaction data from multiple payment
  processors and gateways. This model normalizes transaction types, amounts,
  and timing metrics to support settlement and reconciliation analysis.

Key Transformations:
  - Type cast all amounts to decimal(15,2) and normalize to USD
  - Standardize transaction types across multiple source systems
  - Calculate processing time in milliseconds
  - Handle multiple currency conversions to base currency
  - Classify transactions by processor and payment method
  - Filter test and cancelled transactions

Business Rules:
  - All amounts converted to USD using exchange rates from time of transaction
  - Transaction types: PAYMENT, REFUND, CHARGEBACK, SETTLEMENT, ADJUSTMENT
  - Processing time = settled_at - created_at (in milliseconds)
  - Only include completed or failed transactions for analysis
  - Test transactions (test_mode = true) excluded
========================================
*/

with raw_transactions as (
  select
    transaction_id,
    merchant_id,
    payment_processor_id,
    transaction_type,
    payment_method,
    currency_code,
    amount,
    exchange_rate,
    transaction_status,
    created_at,
    authorized_at,
    settled_at,
    test_mode,
    notes,
    updated_at
  from {{ source('raw', 'transactions') }}
),

cleaned as (
  select
    -- Primary Key
    transaction_id::varchar as transaction_id,
    
    -- Foreign Keys
    merchant_id::varchar as merchant_id,
    payment_processor_id::varchar as payment_processor_id,
    
    -- Dates
    created_at::timestamp as created_at,
    authorized_at::timestamp as authorized_at,
    settled_at::timestamp as settled_at,
    updated_at::timestamp as updated_at,
    
    -- Time-based Metrics
    datediff(millisecond, created_at, settled_at) as processing_time_ms,
    datediff(second, created_at, authorized_at) as authorization_time_seconds,
    
    -- Amount Normalization
    coalesce(amount::decimal(15,2), 0.00) as original_amount,
    coalesce(currency_code::varchar, 'USD') as currency_code,
    coalesce(exchange_rate::decimal(10,6), 1.0) as exchange_rate,
    round(coalesce(amount::decimal(15,2), 0.00) * coalesce(exchange_rate::decimal(10,6), 1.0), 2) as amount_usd,
    
    -- Transaction Classification
    case
      when upper(transaction_type) = 'PAYMENT' then 'PAYMENT'
      when upper(transaction_type) = 'REFUND' then 'REFUND'
      when upper(transaction_type) = 'CHARGEBACK' then 'CHARGEBACK'
      when upper(transaction_type) = 'SETTLEMENT' then 'SETTLEMENT'
      when upper(transaction_type) = 'ADJUSTMENT' then 'ADJUSTMENT'
      else 'UNKNOWN'
    end as transaction_type_normalized,
    
    payment_method::varchar as payment_method,
    
    -- Status
    upper(transaction_status) as transaction_status,
    
    case
      when upper(transaction_status) in ('COMPLETED', 'SETTLED') then 'SUCCESS'
      when upper(transaction_status) in ('FAILED', 'REJECTED') then 'FAILED'
      when upper(transaction_status) in ('PENDING', 'PROCESSING') then 'PENDING'
      else 'UNKNOWN'
    end as transaction_outcome,
    
    -- Quality Flags
    coalesce(test_mode, false) as is_test_transaction,
    notes::varchar as notes,
    
    case
      when transaction_id is null then 'INVALID'
      when amount < 0 and upper(transaction_type) not in ('REFUND', 'CHARGEBACK') then 'SUSPICIOUS'
      when amount = 0 then 'ZERO_VALUE'
      else 'VALID'
    end as transaction_quality_flag,
    
    current_date() as snapshot_date
    
  from raw_transactions
  where test_mode = false
    and transaction_id is not null
    and upper(transaction_status) not in ('CANCELLED', 'DELETED')
)

select * from cleaned
