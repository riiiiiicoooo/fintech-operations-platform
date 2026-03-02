# Ledger Design: Double-Entry Accounting for Fintech

**Last Updated:** February 2025

---

## 1. Why the Ledger Is the Product

In a fintech platform, the ledger is not a backend implementation detail. It is the product. Every feature, every report, every compliance obligation, every support ticket resolution traces back to the ledger. If the ledger is wrong, nothing downstream can be right.

Before we built the internal ledger, our "source of truth" was Stripe's dashboard plus a PostgreSQL transactions table that stored a single row per transaction with a status field and an amount. This worked until it didn't:

- A user disputed a $200 transfer. Support looked at our table: status = "completed", amount = $200. They looked at Stripe: a $200 charge, a $2.50 platform fee deducted, a $197.50 payout. They looked at the bank: a $197.50 deposit. Three systems, three different numbers, no way to reconcile without manual math. Resolution took 2.5 hours.

- Month-end close required the finance ops manager to export Stripe's settlement reports, our transactions table, and bank statements into Excel, then manually match them row by row. Match rate: ~89%. The 11% that didn't match was a mix of timing differences, batch netting, fee deductions, and genuine errors, but there was no way to tell which was which without investigating each one.

- An employer disputed a funding amount. Our table said $50,000 funded. Stripe said $50,000 charged. But the employer had 200 employees, and the individual wallet credits totaled $49,750. Where was the $250? It was platform fees, spread across 200 transactions, but there was no single record that showed the full breakdown of how $50,000 in became $49,750 in wallets + $250 in fees.

The internal ledger solved all of these by enforcing a simple rule: every dollar that enters, moves within, or leaves the platform is recorded as a double-entry journal entry. The total debits always equal the total credits. If they don't, the write is rejected. This means the books are always balanced, reconciliation is derivable, and any question about where money went has a traceable answer.

---

## 2. Double-Entry Fundamentals

### 2.1 The Core Rule

Every financial event is recorded as a journal entry with two or more line items. The sum of all debit line items must equal the sum of all credit line items. No exceptions, no overrides, no "we'll fix it later" adjustments that bypass the rule.

```
Journal Entry: Employer funds employee wallet
┌─────────────────────────────────────────────────────┐
│ ID: JE-2024-03-15-00847                              │
│ Type: funding                                        │
│ Description: ACME Corp batch funding, 200 employees  │
│ Idempotency Key: fund_ACME_20240315_batch_042        │
│ Posted: 2024-03-15T14:23:07Z                         │
│                                                      │
│ Line Items:                                          │
│   Debit   employer_funding_holding    $500.00        │
│   Credit  user_wallet:user_8472      $497.50        │
│   Credit  platform_fee               $2.50          │
│                                                      │
│ Total Debits:  $500.00                               │
│ Total Credits: $500.00                               │
│ Balanced: YES                                        │
└─────────────────────────────────────────────────────┘
```

Why double-entry and not single-entry? Single-entry (recording just "user received $497.50") tells you what happened to one account but not where the money came from or where the $2.50 went. Double-entry forces you to account for every dollar across the entire system. If the books don't balance, something is wrong, and you know immediately instead of discovering it during month-end reconciliation.

### 2.2 Account Types

The chart of accounts follows standard accounting categories. Each account type has a "normal balance" direction, meaning the direction that increases the account.

| Account Type | Normal Balance | Increases When | Decreases When | Examples |
|---|---|---|---|---|
| **Asset** | Debit | Debited | Credited | User wallets, bank accounts, PSP receivables, funds in transit |
| **Liability** | Credit | Credited | Debited | Employer payables, user payables, tax withholding, chargeback reserves |
| **Revenue** | Credit | Credited | Debited (reversal) | Platform fees, subscription revenue, interchange revenue |
| **Expense** | Debit | Debited | Credited (reversal) | PSP processing fees, fraud losses, bank transfer fees |

The normal balance direction matters because it determines how you read account activity:

- An asset account (like a user's wallet) goes UP when debited. "Debit the user wallet" means the user's balance increases. This is counterintuitive if you're used to thinking "debit = taking money away," but in accounting, a debit to an asset means the asset is growing.
- A revenue account goes UP when credited. "Credit platform_fee $2.50" means the platform earned $2.50.

### 2.3 The Chart of Accounts

Our platform uses the following account structure. Each user and employer has their own sub-accounts.

```
ASSETS (debit-normal)
├── employer_funding_holding          Funds received from employers, not yet allocated
├── user_wallet:{user_id}             Individual user balances (one per user)
├── psp_receivable:{psp_name}         Funds confirmed by PSP but not yet in our bank
├── bank_operating                    Operating bank account
├── bank_reserve                      Reserve account for holdbacks
└── settlement_in_transit             Funds being settled between parties

LIABILITIES (credit-normal)
├── employer_payable:{employer_id}    Funds owed back to employers (refunds, reversals)
├── user_payable:{user_id}            Funds owed to users (pending payouts)
├── chargeback_reserve                Reserve for expected chargebacks
├── tax_withholding                   Tax obligations held
└── suspense                          Unidentified funds awaiting classification

REVENUE (credit-normal)
├── platform_fee                      Per-transaction fees
├── subscription_revenue              Employer monthly/annual subscriptions
└── interchange_revenue               Card transaction interchange share

EXPENSES (debit-normal)
├── psp_processing_fees               Stripe/Adyen/Tabapay per-transaction costs
├── fraud_losses                      Chargebacks and fraud write-offs
├── bank_transfer_fees                ACH and wire fees
└── refund_expense                    Cost of refunds (when platform absorbs)
```

**Sub-accounts and the entity model:** Every user and employer has their own set of accounts. `user_wallet:user_8472` is a distinct account from `user_wallet:user_8473`. This means we can calculate any individual user's balance by summing only their account's entries, without scanning all entries in the system. It also means the audit trail for any specific user is isolated to their own accounts.

---

## 3. Journal Entry Patterns

Every transaction type in the platform maps to a specific journal entry pattern. These patterns are implemented as templates in the ledger service, not as ad-hoc code scattered across services.

### 3.1 Employer Funding

When an employer sends money to fund employee wallets.

```
Step 1: Employer initiates funding ($50,000 for 200 employees)

  Debit   bank_operating              $50,000.00   (cash received)
  Credit  employer_funding_holding    $50,000.00   (obligation to allocate)

Step 2: Allocate to individual wallets (200 entries, one per employee)

  For each employee:
  Debit   employer_funding_holding    $247.50      (reduce obligation)
  Credit  user_wallet:{user_id}       $246.25      (user receives)
  Credit  platform_fee                $1.25        (platform earns)

Step 3: Verify employer_funding_holding reaches $0.00 after all allocations
  If not zero, the allocation has a bug. Flag for investigation.
```

Why two steps instead of one? The employer sends a lump sum. The allocation to individual wallets happens separately (possibly minutes or hours later, depending on the payroll schedule). By recording the lump sum receipt first, we immediately know the money arrived. The allocation is a separate operation that decrements the holding account. If the holding account doesn't reach zero after allocation, we know something is wrong immediately.

### 3.2 User Transfer (Earned Wage Access)

When a user transfers money from their wallet to their bank account.

```
Step 1: Initiation (synchronous, before PSP call)

  Debit   user_wallet:{user_id}       $200.00      (reduce wallet balance)
  Credit  psp_receivable:stripe       $200.00      (money owed by PSP to settle)

  Hold created: $200.00 on user_wallet:{user_id}
  (prevents double-spend during async PSP processing)

Step 2: PSP confirms transfer (async, via webhook)

  Debit   psp_receivable:stripe       $200.00      (PSP has settled)
  Credit  settlement_in_transit       $200.00      (funds moving to user bank)

  Hold released: $200.00 on user_wallet:{user_id}

Step 3: Settlement confirmed (bank confirms receipt)

  Debit   settlement_in_transit       $200.00      (no longer in transit)
  Credit  bank_operating              $200.00      (left our bank account)
```

### 3.3 Platform Fee Collection

Fees are recorded as part of the originating transaction, not as a separate transaction. This ensures the full economic picture is captured in a single journal entry.

```
User initiates $200 transfer, platform charges 1.25% fee ($2.50)

  Debit   user_wallet:{user_id}       $200.00
  Credit  psp_receivable:stripe       $197.50      (net after fee)
  Credit  platform_fee                $2.50        (revenue recognized)
```

The fee is embedded in the transfer entry. If someone asks "where did the $2.50 go?" the journal entry answers it directly.

### 3.4 Refund

Refunds are never edits or deletions of the original entry. They are new journal entries that reverse the original flow.

```
Original: user transferred $200 (entry JE-00847)
Refund: full refund initiated

  Debit   psp_receivable:stripe       $200.00      (reverse the original credit)
  Credit  user_wallet:{user_id}       $200.00      (restore user balance)

  Reference: refund_of:JE-00847
```

The original entry JE-00847 still exists and is unchanged. The refund entry references it. An audit trail now shows: entry JE-00847 (transfer of $200), entry JE-00912 (refund of $200, referencing JE-00847). Both entries are immutable.

### 3.5 Chargeback

Chargebacks are messier than refunds because the money may have already been settled to the user.

```
Step 1: Chargeback received from card network

  Debit   fraud_losses                $200.00      (expense: we lost this money)
  Credit  psp_receivable:stripe       $200.00      (Stripe debits our account)

Step 2: If we recover from user (clawback from future earnings)

  Debit   user_wallet:{user_id}       $200.00      (reduce user balance)
  Credit  fraud_losses                $200.00      (reverse the expense)
```

Note that step 2 only happens if the user has a balance and the clawback is permitted by the user agreement. If the user has $0 and has left the platform, the fraud_losses entry stands and is written off.

### 3.6 Multi-Party Settlement (Complex Example)

A single earned wage access transaction involving employer funding, platform fee, PSP fee, and user payout.

```
Full lifecycle of a $500 earned wage access:

Entry 1: Employer funds employee wallet
  Debit   employer_funding_holding    $500.00
  Credit  user_wallet:{user_id}       $493.75
  Credit  platform_fee                $6.25        (1.25% platform fee)

Entry 2: User requests $300 transfer to bank
  Debit   user_wallet:{user_id}       $300.00
  Credit  psp_receivable:stripe       $300.00

Entry 3: PSP processing fee (recorded when webhook confirms)
  Debit   psp_processing_fees         $9.00        (2.9% + $0.30)
  Credit  psp_receivable:stripe       $9.00        (PSP nets their fee)

Entry 4: Settlement to user's bank
  Debit   psp_receivable:stripe       $291.00      ($300 - $9 PSP fee)
  Credit  bank_operating              $291.00

At this point:
  User wallet balance: $193.75 ($493.75 - $300.00)
  Platform fee earned: $6.25
  PSP fee paid: $9.00
  User received in bank: $291.00
  Employer funding holding: $0.00

  Total debits across all entries: $1,109.00
  Total credits across all entries: $1,109.00
  Balanced: YES
```

---

## 4. Holds and Authorizations

### 4.1 Why Holds Exist

A hold reduces a user's available balance without actually moving money in the ledger. This is necessary because of the time gap between when a user initiates a transaction and when the PSP confirms the money moved.

Without holds, a user with $500 could initiate two $400 transfers simultaneously. Both pass the balance check (both see $500 available), both get ledger entries, but only $500 exists. With holds, the first transfer places a $400 hold, reducing available balance to $100. The second transfer sees $100 available and is correctly rejected.

### 4.2 Hold Lifecycle

```
┌─────────────┐
│   ACTIVE    │
│             │
│ Created when│
│ transaction │
│ initiated   │
└──────┬──────┘
       │
  ┌────┴────┬────────────┐
  │         │            │
  ▼         ▼            ▼
┌──────┐ ┌──────┐  ┌─────────┐
│CAPTURED│ │VOIDED│  │ EXPIRED │
│       │ │      │  │         │
│ PSP   │ │ Txn  │  │ TTL hit │
│confirms│ │failed │  │ (7 days)│
│ -> hold│ │-> hold│  │ -> hold │
│ becomes│ │released│  │ auto-  │
│ settled│ │       │  │ released│
└───────┘ └───────┘  └─────────┘
```

**Available balance formula:**

```
available_balance = SUM(credits to account) - SUM(debits from account) - SUM(active holds)
```

The key distinction: `posted_balance` ignores holds (what the ledger says), `available_balance` accounts for holds (what the user can actually spend). The user-facing app always shows available_balance.

### 4.3 Hold Expiration

Holds expire after 7 days if neither captured nor voided. This prevents a stuck transaction from permanently reducing a user's available balance. The expiration job runs every hour, finds holds older than 7 days that are still active, marks them as expired, and creates an alert for investigation.

If a hold expires, something went wrong (we never received the PSP webhook, or the webhook processing failed). Expired holds are a critical alert, not a normal occurrence.

---

## 5. Idempotency

### 5.1 The Problem

Financial operations are inherently unreliable at the network level. API calls timeout, webhooks are delivered multiple times, users double-tap the "Send" button. Without idempotency, any of these can result in double-posting a transaction.

Example without idempotency:
1. User taps "Transfer $200"
2. API receives request, creates journal entry, calls Stripe
3. Stripe call times out (but actually succeeded on Stripe's side)
4. Client retries the request
5. API receives request again, creates another journal entry, calls Stripe again
6. User is now charged $400 instead of $200

### 5.2 Our Approach

Every financial operation requires a client-generated idempotency key. The key is stored permanently. If a request arrives with a key that already exists, the system returns the original result without executing anything.

```
Request 1: POST /transactions {idempotency_key: "usr_8472_txn_20240315_001", amount: 200}
  -> Key not found in Redis or DB
  -> Process transaction
  -> Store key + result in Redis (TTL: 7 days) and DB (permanent)
  -> Return: {transaction_id: "txn_abc123", status: "pending"}

Request 2: POST /transactions {idempotency_key: "usr_8472_txn_20240315_001", amount: 200}
  -> Key found in Redis
  -> Return cached result: {transaction_id: "txn_abc123", status: "pending"}
  -> No new journal entry, no PSP call, no side effects
```

**Key format convention:** `{entity_type}_{entity_id}_{operation}_{date}_{sequence}`

Example: `usr_8472_transfer_20240315_001`

This format makes keys human-readable during debugging and ensures uniqueness without coordination between clients.

### 5.3 Edge Cases

**Partial failure:** If the journal entry is written but the PSP call fails, the idempotency key is stored with the journal entry result. A retry will see the key, return the existing journal entry, and the system can retry the PSP call separately without creating a new ledger entry.

**Key reuse with different parameters:** If a request comes in with an existing key but a different amount, the system rejects it with a 409 Conflict. This catches bugs where the client reuses keys incorrectly.

**Webhook deduplication:** PSP webhooks also carry an event ID. We store processed webhook event IDs in Redis and skip duplicates. This is separate from transaction idempotency but uses the same pattern.

---

## 6. Reconciliation Theory

### 6.1 Why Three-Way Reconciliation

We reconcile three data sources because each one can be wrong in different ways:

| Source | What It Represents | How It Can Be Wrong |
|---|---|---|
| **Internal ledger** | What we believe happened | Bug in our code, race condition, failed webhook processing |
| **PSP records** | What the PSP believes happened | PSP system error, delayed settlement report, batch netting that obscures individual transactions |
| **Bank statement** | What the bank confirms happened | Timing delay (ACH takes 1-3 days), aggregated deposits, bank processing errors |

If all three agree, we have high confidence the transaction is correct. If two agree and one doesn't, we know which source to investigate. If all three disagree, something is seriously wrong and needs immediate attention.

### 6.2 Match Strategies

**Exact match (catches ~92%):** Match by PSP transaction ID + amount + date. This is the easiest and most reliable.

**Fuzzy match (catches ~5% more):** For transactions where the PSP transaction ID doesn't match (maybe we stored it differently, or the settlement report uses a different reference), match by amount + date (within +/- 1 day) + user account. The date tolerance accounts for timezone differences and settlement timing.

**Many-to-one match (catches ~2% more):** PSPs often settle in batches: 50 individual transactions are netted into a single bank transfer. We need to verify that the sum of the 50 transactions equals the bank transfer amount.

**Remaining ~1%:** These are genuine exceptions that require human investigation. Common causes include PSP-initiated adjustments, bank fees not in our records, and transactions that failed on our side but succeeded on the PSP side (or vice versa).

### 6.3 Auto-Resolution Patterns

Not every reconciliation break needs human investigation. Some patterns are predictable and can be auto-resolved with high confidence.

| Pattern | Description | Auto-Resolve Rule | Safety Check |
|---|---|---|---|
| **Timing** | PSP shows settlement on T, bank shows T+1 | If amount matches exactly, auto-resolve | Only if delta is exactly 1 business day |
| **Batch netting** | 50 PSP transactions = 1 bank transfer | If SUM(PSP txns in batch) matches bank amount, auto-resolve | Tolerance: $0.00 (exact match required) |
| **PSP fee deduction** | PSP nets their processing fee from settlement | If delta equals expected PSP fee (known rate), auto-resolve | Only if delta matches calculated fee within $0.01 |
| **FX rounding** | Sub-penny difference from currency conversion | If delta <= $0.01 per transaction, auto-resolve | Cap: max $0.50 total auto-resolved rounding per day |
| **Duplicate webhook** | Same transaction appears twice in PSP report | If PSP event_id is duplicate, auto-resolve by deduplication | Verify our ledger only has one entry |

**Dollar threshold:** We never auto-resolve a break where the unexplained delta exceeds $5.00. This is a hard limit, not configurable by individual users, because a $5.01 "rounding error" is probably not rounding.

### 6.4 The Suspense Account

When reconciliation identifies money that we can't immediately classify (e.g., an unexpected bank deposit with no matching PSP transaction), it goes into the suspense account.

```
Unidentified bank deposit of $1,247.50

  Debit   bank_operating              $1,247.50
  Credit  suspense                    $1,247.50

Investigation reveals: employer funding via wire (not through PSP)

  Debit   suspense                    $1,247.50    (clear suspense)
  Credit  employer_funding_holding    $1,247.50    (proper classification)
```

The suspense account should trend toward zero. A growing suspense balance means the reconciliation process has gaps. We track suspense balance as a key health metric with a target of < $500 at any time.

---

## 7. Immutability and Audit Trail

### 7.1 Why Immutable

Financial records must be immutable for two reasons: regulatory compliance and debugging.

**Regulatory:** Regulators expect to see the complete, unaltered history of every financial transaction. If we edit a journal entry, we've destroyed evidence. Even if the edit was a legitimate correction, we can't prove the original wasn't something worse. Immutability means every state the system has ever been in is preserved.

**Debugging:** When a support ticket comes in ("my transfer from March 3rd shows the wrong amount"), we need to reconstruct exactly what happened. If entries can be edited, we can't distinguish between "the system recorded it wrong" and "someone corrected it later." With immutable entries, the history is the truth.

### 7.2 Correction Pattern

Mistakes are corrected by posting reversing entries, not by editing or deleting.

```
Original entry (posted March 3, has an error: should have been $250, not $200)
  JE-001: Debit user_wallet $200, Credit psp_receivable $200

Correction (posted March 5, when the error is discovered)
  JE-042: Debit psp_receivable $200, Credit user_wallet $200
          (reverses JE-001 completely)
          Reference: reversal_of:JE-001
          Description: "Reversal: incorrect amount on original transfer"

  JE-043: Debit user_wallet $250, Credit psp_receivable $250
          (correct entry)
          Reference: correction_for:JE-001
          Description: "Correction: transfer should have been $250"
```

The audit trail now shows: original entry, reversal, and corrected entry, all with timestamps and references. Anyone reviewing the history can see exactly what happened and when.

### 7.3 Deletion Policy

There is no delete operation on journal entries. The application code does not expose a delete endpoint. The database user that the application connects with does not have DELETE permission on the journal_entries or journal_entry_lines tables. This is enforced at the database level, not just the application level.

```sql
-- Application database user has no DELETE on ledger tables
REVOKE DELETE ON journal_entries FROM app_user;
REVOKE DELETE ON journal_entry_lines FROM app_user;
REVOKE UPDATE ON journal_entries FROM app_user;
REVOKE UPDATE ON journal_entry_lines FROM app_user;

-- Only the DBA role (used for migrations, never for application queries) retains these permissions
-- And even the DBA role should never use them in production
```

---

## 8. Edge Cases and How We Handle Them

### 8.1 Partial Refund

A user requests a $50 refund on a $200 transfer.

```
Original: JE-001, Debit user_wallet $200, Credit psp_receivable $200
Partial refund: JE-052
  Debit   psp_receivable:stripe       $50.00
  Credit  user_wallet:{user_id}       $50.00
  Reference: partial_refund_of:JE-001

User wallet after: original balance - $200 + $50 = net -$150 from original
```

### 8.2 Insufficient Balance During Settlement

Settlement engine calculates user should receive $500, but at settlement time, the source account only has $480 (because of a concurrent transaction).

Resolution: settlement is retried with the next batch. The original settlement instruction is marked as `DEFERRED` with reason `insufficient_source_balance`. No partial settlement is attempted because partial settlements create reconciliation nightmares.

### 8.3 PSP Returns a Different Amount Than Requested

We request a $200 ACH transfer. The PSP confirms $199.97 (because the receiving bank charged a fee that was deducted).

```
Original hold: $200.00
PSP confirmed: $199.97

  Journal entry:
  Debit   psp_receivable:stripe       $199.97
  Credit  bank_operating              $199.97

  Fee variance entry:
  Debit   bank_transfer_fees          $0.03
  Credit  psp_receivable:stripe       $0.03

  Hold updated: captured at $200.00 (original amount)
```

The $0.03 difference is recorded as a bank transfer fee, not hidden or rounded away. This preserves the ability to reconcile to the penny.

### 8.4 Simultaneous Funding and Withdrawal

An employer is funding $50,000 at the same time a user is withdrawing $200 from their already-funded wallet.

These operations don't conflict because they touch different accounts. The funding hits `employer_funding_holding`, then individual `user_wallet` accounts. The withdrawal hits the specific `user_wallet:{user_id}` account. SERIALIZABLE isolation ensures that the withdrawal sees the correct balance for that specific user, regardless of what's happening with the employer funding.

### 8.5 Chargeback After Settlement

User received $200 in their bank account 5 days ago. Today, the original card transaction is charged back.

```
Step 1: Record chargeback (money leaves our Stripe account)
  Debit   fraud_losses                $200.00
  Credit  psp_receivable:stripe       $200.00

Step 2: Attempt recovery (debit from user's wallet if they have balance)
  IF user_wallet balance >= $200:
    Debit   user_wallet:{user_id}     $200.00
    Credit  fraud_losses              $200.00      (reverse the loss)
  ELSE:
    fraud_losses entry stands
    User flagged for collection or write-off

Step 3: If chargeback is reversed (we win the dispute)
  Debit   psp_receivable:stripe       $200.00      (Stripe returns the money)
  Credit  fraud_losses                $200.00      (reverse the loss)
  
  IF step 2 happened (user was debited):
    Debit   fraud_losses              $200.00      (re-record the loss temporarily)
    Credit  user_wallet:{user_id}     $200.00      (return money to user)
    
    Then:
    Debit   psp_receivable:stripe     $200.00      (offset)
    Credit  fraud_losses              $200.00      (net loss: $0)
```

This looks like a lot of entries. That's the point. Every state change is recorded. The alternative (editing the original entry) destroys the audit trail and makes it impossible to reconstruct what happened during a dispute investigation.

---

## 9. Lessons Learned

### 9.1 Things We Got Right

**Ledger before PSP call.** The cardinal rule: write the journal entry before calling the PSP. If the PSP call fails, we have a ledger entry we can void. If we call the PSP first and our ledger write fails, we've moved money with no record of it. The ledger write is the commit point.

**Holds from day one.** We implemented holds in Phase 1, not as a later optimization. Without holds, every concurrent transaction is a potential double-spend bug. Retrofitting holds into a live system with real money is terrifying.

**Immutability enforced at the database.** Not just "our code doesn't delete." The database user literally cannot delete ledger rows. This removes an entire class of operational mistakes.

### 9.2 Things We'd Do Differently

**Balance calculation from entries is slow at scale.** Calculating a user's balance by summing all their journal entry lines works at 10,000 entries. At 1,000,000 entries per user (a user who makes 3 transactions per day for a year has ~3,000 entries; an employer funding 200 employees daily has 73,000), it gets slow. The materialized view helps, but we should have designed for a balance snapshot + incremental update pattern from the start.

**The suspense account grew faster than expected.** Wire transfers from employers didn't always match expected amounts. Bank deposits sometimes arrived with different reference numbers than expected. We needed a more robust suspense classification workflow earlier. The original design assumed < 1% of transactions would hit suspense; reality was closer to 3% initially, requiring more manual investigation than planned.

**Sub-accounts for every user created a lot of accounts.** With 15,000 users, we have 15,000+ accounts in the chart of accounts. This is correct from an accounting perspective, but it made certain aggregate queries slow (e.g., "total funds held across all user wallets"). Partitioning the accounts table by entity_type and using aggregate materialized views was a Phase 2 fix that should have been Phase 1.
