# Fintech Operations Platform - Great Expectations Data Quality

Critical data quality validation for settlement operations and reconciliation processes. Enforces strict compliance requirements and prevents invalid transactions from reaching settlement.

## Overview

Great Expectations framework validates:
- **Settlement Transactions**: Amount ranges, currency codes, time ordering, uniqueness
- **Reconciliation Health**: Match rates >= 95%, exception counts, processing SLA (< 1 hour)

## Files

- **great_expectations.yml**: Snowflake datasource configuration (10 connection pool)
- **expectations/settlement_validation_suite.json**: Transaction-level data quality rules
- **expectations/reconciliation_health_suite.json**: Process health and SLA monitoring
- **checkpoints/pre_settlement_check.yml**: Gate that blocks invalid settlement batches

## Critical: Pre-Settlement Validation Gate

The `pre_settlement_check` checkpoint is a **critical control** that must pass before ANY settlement batch can execute:

```
Settlement Batch Ready
  ↓
Run pre_settlement_check
  ├─ Settlement validation suite
  │  ├─ [CRITICAL] All transactions valid
  │  ├─ [CRITICAL] No duplicates
  │  ├─ [CRITICAL] All amounts in range
  │  └─ [CRITICAL] Status values valid
  │
  └─ Reconciliation health suite
     ├─ [WARNING] Match rate >= 95%
     └─ [WARNING] Processing time < 1 hour
  
  If CRITICAL fails → HALT SETTLEMENT, Page ops team
  If WARNING fails → Send email notification, proceed with caution
```

## Setup

### 1. Initialize Project

```bash
cd fintech-operations-platform/data_quality
great_expectations init
```

### 2. Configure Snowflake

```bash
export SNOWFLAKE_USER="settlement_user"
export SNOWFLAKE_PASSWORD="secure_password"
export SNOWFLAKE_ACCOUNT="xy12345.us-east-1"
export SNOWFLAKE_WAREHOUSE="SETTLEMENT_WH"
export SMTP_HOST="smtp.company.com"
export SMTP_USERNAME="ops@company.com"
export SMTP_PASSWORD="email_password"
```

### 3. Test Connection

```bash
great_expectations datasource list
great_expectations datasource test --datasource snowflake_datasource
```

## Expectation Suites

### Settlement Validation Suite

**Critical Gates** (blocks settlement if failed):

1. **transaction_id**: Not null, unique, format TXID-XXXXXX
   - Uniqueness essential: Duplicates = double-settlement (regulatory violation)

2. **amount**: Between $0.01 - $10M, not null
   - Prevents invalid values: $0 (error) or > $10M (fraud/limits)

3. **currency**: In [USD, EUR, GBP, CAD, JPY, CHF, AUD], not null
   - Multi-currency validation required for FX handling

4. **status**: In [pending, settled, failed, reversed, on_hold], not null
   - Invalid status = data corruption

5. **Time ordering**: settled_at > created_at
   - Inverted timestamps = data pipeline failure

6. **settlement_batch_id**: Composite key with transaction_id (uniqueness)
   - Prevents batch reprocessing

### Reconciliation Health Suite

**SLA Thresholds**:

- **match_rate**: >= 95% (CRITICAL)
  - Below 95% indicates source/target system sync issues
  - Page ops team if < 90%

- **exception_count**: <= 100 (HIGH)
  - Exceptions are unmatched transactions
  - High count = systemic data quality issue

- **processing_time**: <= 3600 seconds (HIGH)
  - 1-hour SLA for reconciliation completion
  - Timeout = infrastructure issue

## Running Validations

### Manual Pre-Settlement Check

```bash
# Run validation before settlement batch
great_expectations checkpoint run pre_settlement_check

# View results
great_expectations docs build
open uncommitted/data_docs/local_site/index.html
```

### Python Integration

```python
from great_expectations import load_context

def validate_before_settlement(batch_id: str) -> bool:
    """Gate that must pass before settlement execution."""
    context = load_context(context_root_dir="./data_quality")
    
    # Run checkpoint with batch context
    checkpoint_result = context.run_checkpoint(
        checkpoint_name="pre_settlement_check",
        run_name=f"settlement_{batch_id}_{datetime.now().isoformat()}",
    )
    
    # Check critical gates
    if not checkpoint_result["success"]:
        # Critical failure - halt settlement
        logger.critical(f"Settlement validation FAILED for batch {batch_id}")
        send_pagerduty_alert(
            title="Settlement validation failed",
            batch_id=batch_id,
            results=checkpoint_result,
        )
        return False
    
    # Check reconciliation warnings
    for validation in checkpoint_result["validation_results"]:
        if "reconciliation" in validation.expectation_suite_name:
            if not validation.success:
                logger.warning(f"Reconciliation check failed for {batch_id}")
                send_email_alert(
                    to=["fintech-ops@company.com"],
                    subject="Settlement proceeding with reconciliation warnings",
                    body=str(validation),
                )
    
    return True

# In settlement pipeline:
if validate_before_settlement(batch_id):
    execute_settlement_batch(batch_id)
else:
    raise SettlementValidationError(f"Batch {batch_id} failed validation")
```

### Automated Daily Reconciliation Validation

```bash
# Run reconciliation health check nightly
# Detects issues before next settlement
great_expectations checkpoint run --checkpoint_name pre_settlement_check \
  --validation_name reconciliation_health_check
```

## Monitoring & Compliance

### Critical Alerts

- **Settlement validation failure**: PagerDuty + Email to fintech-ops, compliance, CFO
- **Reconciliation < 95% match**: Email notification, proceed with caution
- **Processing time SLA violation**: Alert to ops team

### Compliance Logging

All validation results logged to `compliance_logs.validation_history`:
- Timestamp of validation
- Batch ID and transaction counts
- All validation results and failures
- User who initiated settlement
- System making the settlement decision

### Audit Trail

Every settlement execution must have:
1. Pre-settlement validation result (PASS/FAIL)
2. Validation details preserved in compliance log
3. Approval chain if warnings present

## Regulatory Context

**Requirements Met**:
- **SOX**: Data integrity controls before financial settlement
- **PCI-DSS**: Payment data validation before processing
- **Operational Resilience**: Detects data quality degradation
- **Audit Trail**: Comprehensive validation history

## Troubleshooting

### Match Rate Below 95%

1. Check reconciliation details:
   ```sql
   SELECT * FROM reconciliation_results 
   WHERE match_rate < 0.95 
   ORDER BY completed_at DESC 
   LIMIT 10;
   ```

2. Investigate mismatches:
   - Query source system transactions
   - Compare with target system transactions
   - Check FX conversions (if multi-currency)

3. Manual reconciliation:
   ```bash
   # Trigger manual recon before settling
   python -m fintech_ops.reconciliation --manual --source_system internal_ledger
   ```

### Transaction Amount Out of Range

```sql
-- Check invalid amounts
SELECT transaction_id, amount, currency 
FROM settlement_transactions 
WHERE amount <= 0 OR amount > 10000000;
```

If found, investigate source system data quality.

### Processing Time Exceeding SLA

1. Check reconciliation duration:
   ```sql
   SELECT *, 
     DATEDIFF(SECOND, started_at, completed_at) as duration_sec
   FROM reconciliation_results 
   ORDER BY completed_at DESC LIMIT 1;
   ```

2. Optimize if performance issue:
   - Scale reconciliation infrastructure
   - Optimize SQL queries
   - Increase batch processing parallelization

## References

- [Great Expectations Financial Services Guide](https://docs.greatexpectations.io/)
- [Expectation Gallery](https://greatexpectations.io/expectations/)
- [Settlement Process Best Practices](https://www.dtcc.com/settlement-services)
