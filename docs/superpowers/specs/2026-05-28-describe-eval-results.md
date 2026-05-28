# Auto-Describe Prompt Eval Results

LLM: `provider=ServiceProviders.SPEACHES, model=Qwen/Qwen3-14B-AWQ`

## Small FAQ (`faq_small.txt`)
- doc_type: `faq`
- intended_use: `['inbound', 'outbound']`
- length: 1296 chars

### Strategy: S1 (direct)

**Run 1** (53 words):

> This document contains frequently asked questions about the NorthStar Coffee Subscription, including subscription setup, shipment management, return policy, shipping details, gift options, and cancellation. The agent should consult it when answering customer inquiries about subscription features, order adjustments, or company policies. It is intended for use in both customer-facing conversations and internal reference.

**Run 2** (43 words):

> This document contains frequently asked questions about the NorthStar Coffee Subscription, including subscription setup, shipment management, return policy, shipping details, gift options, and contact information. The agent should consult it when answering customer inquiries about subscription terms, order changes, returns, or general policies.

**Run 3** (48 words):

> This document contains answers to common questions about the NorthStar Coffee Subscription, including subscription setup, pausing shipments, return policies, shipping details, gift options, address changes, fair-trade certification, cancellation, and contact support. The agent should consult it when addressing customer inquiries about subscription management, order policies, or service details.

### Strategy: S2 (structured→narrative)

**Run 1** (49 words):

> This document contains frequently asked questions about the NorthStar Coffee Subscription service, covering topics like subscription management, return and cancellation policies, shipping details, gift subscriptions, account settings, fair-trade certification, and customer support. The agent should consult it when a customer asks about subscription terms, account-related queries, or service policies.

**Run 2** (46 words):

> The document contains answers to common questions about the NorthStar Coffee Subscription service, including subscription management, shipping details, returns, gift options, and Fair Trade Certified products. The agent should consult it when addressing customer inquiries about pausing subscriptions, return policies, cancellation terms, or support contact information.

**Run 3** (52 words):

> This document contains answers to common questions about NorthStar Coffee Subscription services, including subscription details, roast options, shipping and return policies, pausing shipments, gift subscriptions, fair-trade certification, cancellation processes, and support contact information. The agent should consult it when addressing customer inquiries related to these topics to ensure accurate and consistent responses.

### Strategy: S3 (few-shot)

**Run 1** (47 words):

> The agent should consult this FAQ for inbound and outbound calls covering subscription setup, pausing/skipping shipments, return policy details, shipping eligibility, gift subscriptions, address changes, fair-trade certification, cancellation steps, and support contact. The agent should quote verbatim when explaining return windows, shipping costs, or how to cancel.

**Run 2** (57 words):

> The agent should consult this FAQ for details on subscription setup, pausing/skipping shipments, return eligibility (14-day unopened window for refunds, store credit for opened defective items), shipping restrictions (continental US only), gift subscriptions, address changes, fair-trade certification, cancellation, and contact info. Refer to the FAQ verbatim when answering questions about return timelines, shipping costs, or subscription modifications.

**Run 3** (60 words):

> The agent should consult this FAQ for questions about subscription setup, pausing/skipping shipments, return policy (including 14-day window and store credit for opened bags), shipping eligibility (continental US only), pricing ($5 flat rate under $40), gift subscriptions, address changes, fair-trade certification, cancellation, and contact details. The agent must quote specific answers verbatim, especially refund timelines, shipping restrictions, and RMA instructions.


## Mid-length policy (`policy_mid.txt`)
- doc_type: `policy`
- intended_use: `['inbound']`
- length: 2209 chars

### Strategy: S1 (direct)

**Run 1** (56 words):

> The document outlines Bluepeak Outdoor’s returns and refunds policy effective January 1, 2025, covering eligibility periods, refund methods, return shipping responsibilities, and exceptions for final sale items, wholesale orders, and gift cards. The agent should consult it when handling customer inquiries about return windows, defective products, international returns, or initiating a return via the provided process.

**Run 2** (54 words):

> The agent should consult this document when handling customer inquiries about returning or refunding purchases from Bluepeak Outdoor, including eligibility periods, refund methods, return shipping responsibilities, and exceptions for wholesale, international, or final-sale items. It outlines policies for defective products, gift cards, and how to initiate returns via the account portal or customer support.

**Run 3** (60 words):

> The agent should consult this document when handling return requests, refund issues, or customer inquiries about Bluepeak Outdoor's returns and refunds policy, which applies to retail purchases made directly from the company. It outlines eligibility windows, refund methods, return shipping responsibilities, exceptions for final sale items, and procedures for defective products, international returns, and gift cards, effective January 1, 2025.

### Strategy: S2 (structured→narrative)

**Run 1** (66 words):

> This document outlines Bluepeak Outdoor’s Returns & Refunds Policy for retail customers, covering eligibility windows, refund methods, return shipping, exceptions for gift cards and final sale items, and procedures for defective products. The agent should consult it when addressing return eligibility, refund options, or special cases like international orders or wholesale purchases. Key details include how to initiate a return and policy variations for specific items.

**Run 2** (55 words):

> This document outlines Bluepeak Outdoor’s Returns & Refunds Policy, detailing eligibility criteria, refund processes, and procedures for initiating returns, including handling RMA numbers, international returns, gift cards, and distinctions between retail purchases and wholesale orders. The agent should consult it when addressing customer questions about return eligibility, refund timelines, or steps to begin a return.

**Run 3** (51 words):

> This document outlines Bluepeak Outdoor’s Returns & Refunds Policy effective January 1, 2025, covering eligibility rules, refund methods, and exceptions for retail customers. The agent should consult it when addressing return windows, resellability requirements, defective product warranties, or international return guidelines, as well as wholesale/gift card exceptions and RMA number procedures.

### Strategy: S3 (few-shot)

**Run 1** (79 words):

> The agent should consult this returns and refunds policy when a caller asks about return eligibility, refund methods, return shipping costs, defective product handling, final sale restrictions, or international return procedures. The policy details a 60-day return window for most items, refund timelines, prepaid return labels for errors, and steps to initiate a return via the account portal. The agent should quote specifics like the 60-day period, store credit for gifts, and RMA requirements verbatim when addressing customer inquiries.

**Run 2** (55 words):

> The agent should consult this returns and refunds policy when a customer asks about return eligibility, refund methods, or exceptions like final-sale items, wholesale orders, or international returns. It details a 60-day window for most items, conditions for hard goods, return shipping responsibilities, and steps to initiate a return, including contact info for further assistance.

**Run 3** (72 words):

> The agent should consult this returns and refunds policy when a caller asks about return eligibility, refund methods, or return shipping procedures. It covers 60-day return windows for most items (with conditions for apparel vs. hard goods), refund timelines, prepaid return labels for errors, defect handling, final-sale exclusions, and international return rules. The agent must reference specific details like the RMA process, store credit for gifts, and contact info for warranty claims.


## Long legal doc (`legal_long.txt`)
- doc_type: `contract`
- intended_use: `['outbound']`
- length: 12824 chars

### Strategy: S1 (direct)

**Run 1** (47 words):

> The document is an appellate court decision addressing the interpretation of indemnity and force majeure clauses in a commercial agreement between two parties. The agent should consult it when analyzing contract disputes involving indemnity obligations, force majeure exceptions, or the admissibility of expert evidence in commercial litigation.

**Run 2** (43 words):

> The document is an appellate court judgment addressing the interpretation of force majeure and indemnity clauses in a storage agreement between two companies. The agent should consult it when handling disputes over contract liability, indemnity scope, or force majeure exemptions in commercial agreements.

**Run 3** (70 words):

> The document is an appellate court judgment from New South Artemis regarding a contract dispute between Orinoco Holdings Limited and Westbridge Terminal Services Pty Ltd. It addresses the interpretation of indemnity and force majeure clauses in their storage agreement, determining that force majeure excludes indemnity liability and remitting quantum to the Commercial List. The agent should consult it when handling contract disputes involving indemnity, force majeure, or commercial agreement interpretations.

### Strategy: S2 (structured→narrative)

**Run 1** (79 words):

> The document details an appellate court ruling on an appeal involving indemnity and force majeure clauses in a storage agreement between Orinoco Holdings Limited and Westbridge Terminal Services Pty Ltd. It partially allows the appeal, remitting the quantum determination to a lower court, and references Clause 8.2, Clause 14, and the Court of Appeal of New South Artemis. The agent should consult this document when analyzing contractual indemnity obligations, force majeure disputes, or appellate court decisions on commercial agreements.

**Run 2** (74 words):

> The document details an appeal between Orinoco Holdings Limited and Westbridge Terminal Services Pty Ltd regarding the interpretation of indemnity (Clause 14) and force majeure (Clause 8.2) clauses in their 2018 storage agreement. The Court of Appeal of New South Artemis partially allowed the appeal, remitting quantum for re-determination (case [2025] NSACA 117). The agent should consult this document when advising on contractual indemnity obligations, force majeure disputes, or appellate rulings in commercial agreements.

**Run 3** (78 words):

> This document details an appeal case involving the interpretation of force majeure (Clause 8.2) and indemnity (Clause 14) clauses in a storage agreement between Orinoco Holdings Limited and Westbridge Terminal Services Pty Ltd, leading to a partial reversal of damages. The agent should consult it when handling contract disputes related to force majeure or indemnity in long-term storage agreements, particularly in cases referencing the Court of Appeal of New South Artemis ([2025] NSACA 117) and Dr. Anneliese Hennings.

### Strategy: S3 (few-shot)

**Run 1** (62 words):

> The agent should consult this contract document when addressing disputes over indemnity claims under clause 14, particularly regarding force majeure exemptions in clause 8.2, notice compliance in clause 17.3, or the admissibility of expert evidence on industry practice. It outlines the appellate court’s ruling that the indemnity does not apply to force majeure events and remits quantum determination to the Commercial List.

**Run 2** (70 words):

> The agent should consult this contract judgment when handling disputes over indemnity claims, force majeure events, or notice requirements under a long-term throughput agreement. It clarifies that clause 14’s indemnity excludes force majeure-caused failures and rejects third-party mitigation costs, while emphasizing strict compliance with notice provisions. The agent must reference the court’s construction of clauses 8.2 and 14 when advising on liability, damages, or procedural compliance in similar commercial contracts.

**Run 3** (63 words):

> The agent should consult this legal contract when addressing indemnity claims related to force majeure events in storage agreements, specifically clauses 8.2 and 14. It clarifies that indemnity does not apply to failures caused by force majeure, excludes third-party mitigation costs, and emphasizes strict compliance with notice requirements. Use it to explain indemnity limits, force majeure exclusions, and procedural obligations in outbound calls.


## Recommendation

**Winner:** S2 (structured → narrative, two-call)

**Confirmed by user:** 2026-05-28.

**Mean scores (faithfulness / usefulness / length / consistency, /5):**
- S1 (direct): 5 / 3 / 5 / 4 → overall **4.25**
- S2 (structured→narrative): 5 / 4 / 4 / 5 → overall **4.5**
- S3 (few-shot): 4 / 5 / 3 / 4 → overall **4.0**

**One-sentence reasoning per strategy:**
- S1 — Faithful and disciplined on length, but the descriptions read generically ("consult when handling customer inquiries about X") and miss the entity-level specifics (clause numbers, case citation) that make a knowledge-base description useful for downstream grounding.
- S2 — Same faithfulness as S1 but the structured profile step forces the model to surface entities, clauses, and case identifiers; cross-run consistency is the highest of the three; cost is ~2× tokens and ~2× latency (~8–13 s observed against the local Qwen3-14B-AWQ stack), which is acceptable for a click-and-wait UI affordance.
- S3 — Most action-oriented prose ("the agent should quote verbatim …"), but introduced a small unsupported claim on one FAQ run ("RMA instructions" — the FAQ has no RMA section) and the length variance pushed one sample to 79 words; for a description that downstream LLMs ground on, hallucination-resistance outweighs punchiness.

**Latency observations** (`docker exec ... describe_prompt_eval`, Qwen3-14B-AWQ on the local Speaches stack):
- S1: ~3–5 s per output
- S2: ~7–13 s per output (two sequential calls)
- S3: ~3–5 s per output

**Action:** Pin S2 by changing `build_describe_prompt` to return the step-1 profile prompt and exposing a new `build_describe_prompt_step2(profile_json)` helper. The Task 7 service module makes two sequential LLM calls.
