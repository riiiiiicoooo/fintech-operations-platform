# Compliance Framework: KYC/AML and Regulatory Automation

**Last Updated:** February 2025

---

## 1. Why Compliance Is a Product Problem

Most fintech teams treat compliance as a checkbox: hire a compliance officer, buy a vendor, file reports when asked. This works until it doesn't. The moment a regulator sends an examination letter, or a banking partner asks for your BSA/AML program documentation, or Visa puts you in their chargeback monitoring program, "checkbox compliance" falls apart.

We learned this the hard way. Our compliance program before the platform rebuild was:

- **KYC:** Manual email chain with identity verification vendor. Average verification time: 24-48 hours. Drop-off rate during onboarding: 34% (users abandoned because they couldn't use the product immediately).
- **Transaction monitoring:** Weekly CSV export from the database, manually reviewed by the compliance officer. She scanned for large transactions and unusual patterns by eye. No automated rules, no standardized thresholds, no documentation of what was reviewed or why.
- **SAR filing:** Entirely manual. The compliance officer would identify suspicious activity, write up the narrative in a Word document, and file through FinCEN's BSA E-Filing system. Average time from detection to filing: 18 days. FinCEN requires filing within 30 calendar days of detection, so we were technically compliant, but with no margin.
- **Audit trail:** Application logs captured some transaction events but not all. There were gaps: some webhook events weren't logged, some manual overrides weren't captured, and the logs were in the same database as the application (no separation of concerns, no tamper resistance).

The rebuilt compliance framework treats compliance as a product with its own requirements, metrics, and user experience. The compliance officer (Priya) is a user with workflows, dashboards, and automation. The goal: make compliance sustainable at scale without linear headcount growth.

---

## 2. KYC Verification Tiers

### 2.1 Progressive Verification Model

We use progressive KYC, meaning users start with minimal verification and unlock higher limits as they verify more. This balances two competing goals: onboarding speed (don't make users submit a passport photo to send $50) and regulatory obligation (don't let unverified users move $10,000).

```
User signs up
      |
      v
+---------------------+
|  BASIC TIER          |
|                      |
|  Required:           |
|  - Email verified    |
|  - Phone verified    |
|  - Employer match    |
|    (employer confirms |
|    this person is an |
|    employee)         |
|                      |
|  Limits:             |
|  - $250/txn          |
|  - $500/day          |
|  - $2,000/month      |
|                      |
|  Time: < 30 seconds  |
|  (automated)         |
+----------+-----------+
           |
           | User hits limit or proactively upgrades
           v
+---------------------+
|  STANDARD TIER       |
|                      |
|  Required:           |
|  - Everything in     |
|    Basic +           |
|  - Government ID     |
|    (driver's license |
|    or passport)      |
|  - Database check    |
|    (SSN trace,       |
|    address history,  |
|    watchlist screen)  |
|                      |
|  Limits:             |
|  - $1,000/txn        |
|  - $2,500/day        |
|  - $10,000/month     |
|                      |
|  Time: < 3 minutes   |
|  (automated, 95%+)   |
|  Manual review: < 5% |
+----------+-----------+
           |
           | User hits limit or high-risk trigger
           v
+---------------------+
|  ENHANCED TIER       |
|                      |
|  Required:           |
|  - Everything in     |
|    Standard +        |
|  - Document proof    |
|    (utility bill or  |
|    bank statement    |
|    for address)      |
|  - Manual review by  |
|    compliance team   |
|                      |
|  Limits:             |
|  - $5,000/txn        |
|  - $10,000/day       |
|  - $25,000/month     |
|                      |
|  Time: < 24 hours    |
|  (requires human     |
|  review)             |
+---------------------+
```

### 2.2 Verification Workflow

The KYC verification is orchestrated through Alloy, which acts as a decision engine combining multiple data sources.

```
User submits verification request
      |
      v
+------------------------+
|  Alloy Orchestration    |
|                         |
|  Step 1: Identity       |
|  - Name + DOB + SSN    |
|    against credit       |
|    bureau records       |
|  - Result: match /      |
|    partial / no match   |
|                         |
|  Step 2: Document       |
|  (Standard tier+)       |
|  - ID photo uploaded    |
|  - OCR extracts data    |
|  - Liveness check       |
|    (selfie matches ID)  |
|  - Document authenticity|
|    (tamper detection)    |
|                         |
|  Step 3: Watchlist      |
|  - OFAC SDN list        |
|  - PEP (politically     |
|    exposed persons)     |
|  - Adverse media        |
|  - Global sanctions     |
|                         |
|  Step 4: Risk scoring   |
|  - Aggregate signals    |
|  - Output: approve /    |
|    manual review /      |
|    reject               |
+----------+--------------+
           |
     +-----+------+--------+
     |            |         |
     v            v         v
 APPROVED    MANUAL      REJECTED
             REVIEW
     |            |         |
     v            v         v
 Tier        Queue for   Notify user
 upgraded    compliance   with reason
 immediately team review  (generic, not
              |           specific to
              v           avoid gaming)
         Analyst reviews
         evidence, makes
         decision within
         24 hours
```

### 2.3 Re-Verification

KYC is not a one-time event. Verification expires and must be renewed.

| Trigger | Action |
|---|---|
| 12 months since last verification | Re-run database checks. If still passing, auto-renew. If flags, require document re-upload. |
| Material change (name change, new SSN) | Re-verify from scratch at current tier level. |
| Compliance alert on user | Freeze account, require Enhanced verification regardless of current tier. |
| Regulatory change (new requirements) | Batch re-verify affected users against new rules. |

---

## 3. Transaction Monitoring

### 3.1 Rule Categories

Transaction monitoring runs asynchronously after transactions are posted. Unlike fraud detection (which runs synchronously before the transaction), monitoring looks for patterns across multiple transactions over time.

```
+----------------------------------------------------------+
|              MONITORING RULE CATEGORIES                    |
|                                                           |
|  AGGREGATION RULES (cumulative thresholds)                |
|  - Total outbound > $5,000 in 30 rolling days            |
|  - Total inbound > $10,000 in 30 rolling days            |
|  - Total activity > $15,000 in 30 rolling days           |
|  These are based on the BSA currency transaction          |
|  reporting threshold ($10K) with buffers                  |
|                                                           |
|  STRUCTURING RULES (deliberate avoidance)                 |
|  - 3+ transactions between $2,000-$2,999 in 7 days       |
|    (just below the $3,000 funds transfer rule)            |
|  - 3+ transactions within 10% of a round threshold       |
|    in 14 days                                             |
|  - Declining transaction amounts in sequence              |
|    ($2,900, $2,800, $2,700) suggesting testing limits     |
|                                                           |
|  RAPID MOVEMENT RULES (pass-through behavior)             |
|  - Funds deposited and withdrawn within 24 hours          |
|    (> 80% of deposit amount)                              |
|  - New account receives large deposit, immediately        |
|    transfers out (account age < 30 days)                  |
|  - Multiple small deposits followed by one large          |
|    withdrawal within 48 hours                             |
|                                                           |
|  GEOGRAPHIC RULES                                         |
|  - Transactions from FATF high-risk jurisdictions         |
|  - IP geolocation mismatch with registered address        |
|    (by > 500 miles, excluding known travel patterns)      |
|  - Multiple users transacting from same IP address        |
|                                                           |
|  BEHAVIORAL RULES                                         |
|  - Transaction volume suddenly 3x above 90-day average    |
|  - First transaction is near the tier limit               |
|  - Immediate tier upgrade request after account creation  |
|  - Multiple failed transactions followed by successful    |
|    one at lower amount (probing limits)                   |
+----------------------------------------------------------+
```

### 3.2 Alert Lifecycle

```
Rule triggers
      |
      v
+-------------------+
|  ALERT CREATED    |
|                   |
|  Auto-populated:  |
|  - User profile   |
|  - Transaction    |
|    history        |
|  - Rule that      |
|    triggered      |
|  - Risk context   |
|  - Similar past   |
|    alerts for     |
|    this user      |
+--------+----------+
         |
         v
+-------------------+
|  ASSIGNED         |
|                   |
|  Assigned to      |
|  compliance       |
|  analyst based    |
|  on: rule type,   |
|  alert volume,    |
|  analyst workload |
+--------+----------+
         |
         v
+-------------------+
|  INVESTIGATING    |
|                   |
|  Analyst reviews: |
|  - Full txn       |
|    history        |
|  - KYC status     |
|  - Previous       |
|    alerts         |
|  - Source of      |
|    funds          |
|  - Employer       |
|    context        |
+--------+----------+
         |
    +----+----+
    |         |
    v         v
+--------+ +-----------+
|DISMISSED| |SAR        |
|         | |RECOMMENDED|
|Reason   | |           |
|documented| Narrative  |
|          | drafted    |
|Analyst   | Evidence   |
|signs off | compiled   |
+----------+ +----+-----+
                  |
                  v
           +------------+
           |SAR FILED   |
           |            |
           |Filed via   |
           |FinCEN BSA  |
           |E-Filing    |
           |            |
           |Confirmation|
           |number      |
           |stored      |
           +------------+
```

### 3.3 Alert SLAs

| Alert Priority | Rule Type | Review SLA | Escalation |
|---|---|---|---|
| **Critical** | OFAC match, structuring pattern | 4 hours | Auto-escalate to BSA Officer if unreviewed |
| **High** | Aggregation threshold, rapid movement | 24 hours | Auto-escalate after 24 hours |
| **Medium** | Behavioral anomaly, geographic flag | 48 hours | Auto-escalate after 72 hours |
| **Low** | Minor velocity change, single rule trigger | 5 business days | Batch review weekly |

---

## 4. SAR Workflow

### 4.1 When to File

A Suspicious Activity Report is required when the compliance team identifies activity that they know, suspect, or have reason to suspect:

- Involves funds from illegal activity
- Is designed to evade reporting requirements (structuring)
- Has no business or apparent lawful purpose
- Involves use of the platform to facilitate criminal activity

The threshold for SAR filing is subjective and based on the totality of circumstances. Not every monitoring alert results in a SAR. Most don't. But every alert must be documented with a clear decision (file or dismiss) and the reasoning behind that decision.

### 4.2 Filing Process

```
SAR recommended by analyst
      |
      v
+---------------------------+
|  BSA Officer Review        |
|                            |
|  Reviews:                  |
|  - Analyst investigation   |
|    notes                   |
|  - Supporting evidence     |
|  - Transaction details     |
|  - User KYC information    |
|  - Previous SARs on this   |
|    user (if any)           |
|                            |
|  Decision:                 |
|  - Approve filing          |
|  - Return for more         |
|    investigation           |
|  - Dismiss (with           |
|    documented reasoning)   |
+-----------+----------------+
            |
            v (approved)
+---------------------------+
|  Narrative Preparation     |
|                            |
|  FinCEN requires a written |
|  narrative describing:     |
|  - Who (subject info)      |
|  - What (suspicious        |
|    activity described)     |
|  - When (date range)       |
|  - Where (geographic)      |
|  - Why (why it's           |
|    suspicious)             |
|  - How (method/pattern)    |
|                            |
|  Our system pre-populates  |
|  the who/what/when from    |
|  transaction data. Analyst |
|  writes the why/how.       |
+-----------+----------------+
            |
            v
+---------------------------+
|  Filing                    |
|                            |
|  Submitted through FinCEN  |
|  BSA E-Filing System       |
|                            |
|  Deadline: 30 calendar     |
|  days from detection       |
|                            |
|  Confirmation number       |
|  stored in compliance      |
|  event log                 |
|                            |
|  Copy retained for 5 years |
|  (regulatory requirement)  |
+-----------+----------------+
            |
            v
+---------------------------+
|  Post-Filing Actions       |
|                            |
|  - Continue monitoring     |
|    (SARs don't mean        |
|    account closure)        |
|  - 90-day continuing       |
|    activity review         |
|  - File continuation SAR   |
|    if activity persists    |
|  - Account closure if      |
|    warranted (separate     |
|    decision, not automatic)|
+---------------------------+
```

### 4.3 SAR Tipping Off

Federal law prohibits informing the subject of a SAR that a report has been filed. This has product implications:

- If a user asks "why was my account frozen?" and the reason is a SAR investigation, support cannot mention the SAR
- The support team has a standard script: "Your account is under review. We are unable to provide specific details at this time."
- Support agents do not have access to SAR case details in their tools. Only the compliance team sees SAR-related data
- Internal systems use a generic "compliance_review" status, not "sar_investigation"

---

## 5. Sanctions Screening (OFAC)

### 5.1 How It Works

Every outbound money movement is screened against the OFAC Specially Designated Nationals (SDN) list before execution. This is not optional and has no override mechanism.

```
Outbound transfer initiated
      |
      v
+---------------------------+
|  Local OFAC Cache Check    |
|  (< 10ms)                 |
|                            |
|  SDN list cached locally   |
|  Refreshed daily at        |
|  6:00 AM ET               |
|                            |
|  Match algorithm:          |
|  - Exact name match        |
|  - Fuzzy match (Jaro-      |
|    Winkler similarity      |
|    > 0.85)                 |
|  - Alias matching          |
|  - DOB cross-reference     |
|    (if available)          |
+-----------+----------------+
            |
       +----+----+
       |         |
       v         v
   NO MATCH    POTENTIAL
               MATCH
       |         |
       v         v
   Continue    BLOCK
   with        transaction
   transaction immediately
               |
               v
          +------------------+
          |  Alert compliance |
          |  team             |
          |                   |
          |  Analyst reviews: |
          |  - Is this a true |
          |    match or false |
          |    positive?      |
          |  - Common names   |
          |    produce many   |
          |    false positives|
          |                   |
          |  True match:      |
          |  - Block account  |
          |  - File report    |
          |  - Do NOT notify  |
          |    user of reason |
          |                   |
          |  False positive:  |
          |  - Document the   |
          |    review         |
          |  - Release        |
          |    transaction    |
          |  - Add to         |
          |    false-positive |
          |    allowlist for  |
          |    this user      |
          +------------------+
```

### 5.2 OFAC List Management

| Detail | Value |
|---|---|
| Source | US Treasury OFAC SDN List (XML format) |
| Update frequency | Daily download at 6:00 AM ET |
| List size | ~12,000 entries (individuals and entities) |
| Storage | Redis sorted set for fast lookup |
| Matching | Name (fuzzy), DOB, country, aliases |
| False positive rate | ~0.3% of transactions (mostly common name matches) |
| SLA for match review | 4 hours (Critical priority) |

---

## 6. PCI DSS Scope Minimization

We minimize PCI scope by never handling raw card numbers. All card data flows directly from the user's device to the PSP's hosted form (Stripe Elements or Adyen Drop-in). Our servers only receive tokenized references.

| Data Element | Where It Lives | Our Access |
|---|---|---|
| Card number (PAN) | Stripe/Adyen vault | Never touches our systems |
| CVV | Stripe/Adyen (transient) | Never touches our systems |
| Cardholder name | Stripe/Adyen | Accessible via API if needed |
| Payment token | Our database | Stored, used for recurring charges |
| Last 4 digits | Our database | Stored for user display ("Visa ending 4242") |
| Expiration date | Stripe/Adyen | Accessible via API, not stored locally |

**PCI scope result:** SAQ-A (self-assessment questionnaire only, no on-site audit). This is the lightest PCI compliance level, appropriate for merchants that fully outsource card handling to a PCI-compliant processor.

---

## 7. State Money Transmitter Licensing

Operating a platform that moves money for users requires compliance with state-level money transmitter laws. The regulatory landscape is complex: 49 states plus DC, Puerto Rico, and US Virgin Islands each have their own requirements (Montana is the only state without a money transmitter law).

### 7.1 Our Approach

Rather than obtaining money transmitter licenses in every state (a multi-year, multi-million dollar process), we operate under our banking partner's license. The banking partner holds the licenses and acts as the principal. We operate as their agent under an agent-of-the-payee or bank-partnership model.

This has product implications:

- Our banking partner must approve changes to fund flows or new payment methods
- Settlement timing is partially controlled by the banking partner's policies
- Some states require specific disclosures to users (e.g., California requires a notice about the right to a refund)
- The banking partner conducts annual audits of our compliance program

### 7.2 State-Specific Rules

| Requirement | States | Product Impact |
|---|---|---|
| Specific disclosure language | CA, NY, TX, IL | Dynamic disclosure text shown at transaction initiation based on user state |
| Refund rights notice | CA | Refund policy notice required before every transaction for CA users |
| Transaction receipt requirements | NY (BitLicense for crypto), TX | Email receipt within 24 hours of completed transaction |
| Complaint filing instructions | Most states | Link to state regulator in user settings |
| Maximum hold period | Varies | Hold expiration configured per state (default: 7 days) |

---

## 8. Audit Trail Architecture

### 8.1 What Gets Logged

Every action with financial or compliance significance is logged to the compliance_events table. This is separate from application logs (which are for debugging) and the transaction_events table (which is for user-facing status tracking).

| Event Category | Events Logged |
|---|---|
| **KYC** | Verification initiated, vendor response received, tier upgraded, tier downgraded, re-verification triggered, verification expired |
| **Transaction monitoring** | Rule evaluated, alert created, alert assigned, alert investigated, alert dismissed (with reason), SAR recommended |
| **SAR** | SAR drafted, SAR reviewed by BSA Officer, SAR filed (confirmation number), continuation SAR filed, SAR-related account action taken |
| **Sanctions** | OFAC screening initiated, no match, potential match found, match reviewed, true positive confirmed, false positive cleared |
| **Account actions** | Account frozen (reason), account unfrozen (reason), account closed (reason), limits changed (old value, new value, reason) |
| **Access** | Compliance dashboard accessed, SAR case viewed, alert reviewed, monitoring rule changed |

### 8.2 Storage and Retention

```
+------------------------------------------+
|  COMPLIANCE EVENT LOG                     |
|                                           |
|  Storage: Separate PostgreSQL schema      |
|  ("compliance") with restricted access    |
|                                           |
|  Access: compliance_reader role only      |
|  (compliance team + engineering lead      |
|  for maintenance)                         |
|                                           |
|  Application database user (app_user)     |
|  can INSERT but cannot UPDATE, DELETE,    |
|  or TRUNCATE                              |
|                                           |
|  Retention:                               |
|  - SAR-related records: 5 years from      |
|    filing date (FinCEN requirement)        |
|  - KYC records: 5 years from account      |
|    closure (BSA requirement)              |
|  - All other compliance events: 7 years   |
|    (our policy, exceeds minimums)         |
|                                           |
|  Tamper resistance:                       |
|  - No UPDATE or DELETE permissions        |
|  - Hash chain: each event includes        |
|    SHA-256 hash of previous event         |
|  - Daily integrity check validates        |
|    hash chain is unbroken                 |
+------------------------------------------+
```

### 8.3 Examination Readiness

When a regulator (FinCEN, state examiner, or banking partner auditor) requests information, the compliance team needs to produce specific reports quickly. These are pre-built, not generated ad-hoc.

| Report | Contents | Generation |
|---|---|---|
| **BSA/AML Program Summary** | Program description, policies, procedures, training records, independent testing results | Static document, updated quarterly |
| **SAR Filing Log** | All SARs filed in date range, with confirmation numbers, subject info, narrative summaries | Query compliance_events where event_type = 'sar_filed' |
| **Transaction Activity Report** | All transactions for a specific user or date range, with full event history | Query transactions + transaction_events + compliance_events |
| **KYC Status Report** | Verification status for all users, with tier, verification date, expiration, any flags | Query kyc_verifications + compliance_events |
| **Alert Disposition Report** | All monitoring alerts in date range, with resolution (filed/dismissed) and analyst notes | Query compliance_events where event_type LIKE 'alert_%' |
| **OFAC Screening Log** | All sanctions screenings, with match/no-match results and review documentation for matches | Query compliance_events where event_type LIKE 'sanctions_%' |

---

## 9. How Compliance Shaped Product Decisions

Compliance requirements directly influenced several core product decisions. These aren't afterthoughts bolted on; they changed how the platform works.

| Product Decision | Compliance Driver | Impact |
|---|---|---|
| Progressive KYC tiers (not full verification upfront) | Balancing onboarding conversion with BSA obligations | Basic tier allows immediate use at low limits; reduces 34% drop-off to < 8% |
| Ledger immutability | Audit trail must be tamper-resistant and complete | No edits or deletes on financial records; corrections via reversing entries only |
| Separate compliance event store | Compliance data must be accessible to compliance team but restricted from support/engineering | Dedicated schema with separate access controls |
| SAR-unaware support tools | SAR tipping-off prohibition (31 USC 5318(g)(2)) | Support agents see "compliance_review" status, never SAR details |
| OFAC check before every outbound transfer | No money moves to sanctioned entities, no exceptions | Adds < 10ms latency per transaction (cached list), but blocks the transfer if matched |
| Hold expiration (7 days) | Money can't be held indefinitely without settlement | Prevents stuck balances from creating unresolvable reconciliation issues |
| Bank partnership model | State money transmitter licensing requirements | Platform operates under partner's licenses; some product changes require partner approval |
| State-specific disclosures | CA, NY, TX, IL require specific language | Dynamic disclosure component that renders state-appropriate text |

---

## 10. Metrics

### 10.1 Compliance Health Dashboard

These are the metrics Priya reviews daily.

| Metric | Target | Current | Alert If |
|---|---|---|---|
| KYC verification pass rate (automated) | > 90% | 93% | < 85% |
| KYC average verification time (Basic) | < 30 seconds | 12 seconds | > 60 seconds |
| KYC average verification time (Standard) | < 3 minutes | 2.1 minutes | > 5 minutes |
| Open monitoring alerts | < 20 | 14 | > 30 |
| Alert review SLA compliance | > 95% | 97% | < 90% |
| Average alert-to-decision time | < 24 hours | 18 hours | > 36 hours |
| SAR filing SLA (< 30 days from detection) | 100% | 100% | Any miss (critical) |
| OFAC false positive rate | < 1% | 0.3% | > 2% |
| Suspense account balance | < $500 | $210 | > $1,000 |
| Compliance event log integrity | 100% (hash chain valid) | 100% | Any break (critical) |

### 10.2 Trends to Watch

| Trend | What It Means | Action |
|---|---|---|
| Rising alert volume without rising SAR rate | Rules may be too sensitive, creating analyst fatigue | Tune thresholds, retire low-signal rules |
| Declining KYC pass rate | Possible fraud ring using synthetic identities, or vendor quality issue | Investigate rejected applications for patterns |
| Growing suspense balance | Reconciliation gaps, unclassified funds | Prioritize suspense resolution in finance ops |
| Increasing OFAC false positives | User base growing into demographics with common name matches | Refine matching algorithm, expand allowlist |
| SAR continuation rate increasing | Existing suspicious users continuing to use platform | Evaluate account closure policy |
