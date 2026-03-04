/*
========================================
MACRO: Normalize Currency
========================================
Purpose:
  Reusable macro for currency normalization to a base currency (USD).
  Handles exchange rate application and decimal precision standardization
  across all financial calculations in the fintech operations platform.

Parameters:
  - amount_col: Column name containing amount in original currency
  - exchange_rate_col: Column name containing exchange rate to USD
  - precision: Decimal precision for result (default: 2)

Returns:
  Numeric expression normalizing amount to USD with specified precision

Business Logic:
  - Multiplies amount by exchange rate
  - Rounds to specified decimal places
  - Handles null rates by defaulting to 1.0 (USD)

Usage:
  select
    transaction_id,
    {{ normalize_currency('amount', 'exchange_rate', 2) }} as amount_usd
  from transactions
========================================
*/

{% macro normalize_currency(
  amount_col,
  exchange_rate_col,
  precision = 2
) %}

  round(
    coalesce({{ amount_col }}::decimal(15,2), 0.00) *
    coalesce({{ exchange_rate_col }}::decimal(10,6), 1.0),
    {{ precision }}
  )

{% endmacro %}
