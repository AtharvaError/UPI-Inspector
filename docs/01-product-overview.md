# 01 — Product Overview

[← Index](README.md)

## 1. Summary

UPI Inspector is a risk-decisioning layer that a bank or UPI app calls just before a payment is authorised. It scores the payment, the payee and the payer's situation together and returns one of four responses: **Safe**, **Warning**, **Step-Up** or **Block**. Every response above Safe carries a plain-language reason and the name of the scam pattern the payment resembles.

It works alongside existing bank and PSP infrastructure and does not move money itself.

The fraud it targets is social engineering: fake refunds, "digital arrest", impersonation and mule transfers. In these scams the real account holder makes the payment willingly, so checks that only confirm the payer's identity pass. The product's bet is that the manipulation leaves evidence around the payment, and that combining that evidence at the moment of payment lets us intervene before the money leaves. The evidence the prototype uses:

- a call from a risky or unknown number shortly before, or during, the payment,
- a scam message the payer received,
- signs of being coached while paying: a pasted UPI ID, switching between apps,
- a payee name that does not match what the payer was told,
- a brand-new payee collecting from many strangers, or sitting in a mule network,
- an amount far outside the payer's habits.

## 2. The problem

From the pitch deck:

- **Reactive only.** Today's systems act after the loss. There is no real-time hold at the point of payment.
- **No correlation.** Nothing links the scam call or SMS to the transaction that follows it.
- **Scores without reasons.** Risk systems emit a confidence score with no explanation a payer or a regulator can read.
- **Silos.** Banks, telecom operators and platforms each see one part of the event.

## 3. Who is affected

| Group | How they are hit |
|---|---|
| Citizens and families | Panic-driven transfers during a scam call |
| Elderly users | Targeted disproportionately by digital-arrest scams |
| Small businesses | Boss or vendor impersonation that ends in a UPI payment |
| Law enforcement | No tool to trace a scam call to the transaction it caused |

## 4. Why now

- **Every P2P payment is now payer-initiated.** NPCI discontinued P2P collect requests from 1 October 2025. A scammer can no longer pull money with a request; they have to persuade the payer to push it.
- **A regulator-backed phone-risk signal exists.** The Department of Telecommunications launched the Financial Fraud Risk Indicator (FRI) in May 2025, which classifies mobile numbers into three risk levels. An RBI advisory of 30 June 2025 directed banks to integrate it. This is the production source for the "risky caller" signal the prototype simulates.
- **Shared fraud intelligence is being built.** RBI's Digital Payments Intelligence Platform (DPIP) is a shared fraud-intelligence platform and fraud-account repository, and MuleHunter.AI scores accounts for mule behaviour. UPI Inspector is positioned as complementary: those work at the registry and account level; UPI Inspector acts on a single payment at the moment it is made.
- **Explainability is expected.** RBI's FREE-AI committee report (13 August 2025) sets out principles for AI in finance that include explainability and human oversight.

Sources are listed in [11, section 8](11-security-risks-and-decisions.md#8-references).

## 5. Goals and non-goals

### Goals

| # | Goal |
|---|---|
| G1 | Decide on every payment inside the payment flow, within a 150 ms synchronous budget. |
| G2 | Score transaction, payee trust and payer behaviour together in one decision. |
| G3 | Correlate call and message signals with the payment that follows and record them as one connected risk event. |
| G4 | Attach a plain-language reason and a named scam pattern to every flag. |
| G5 | Respond in proportion to risk, so that safe payments see no friction. |
| G6 | Learn from outcomes: confirmed fraud and near-misses update payee reputation and retrain the models. |
| G7 | Give vulnerable payers protection that does not rely on them reading a warning: a trusted contact, a spoken alert, a pause. |
| G8 | Let a bank adopt it without risk: run in shadow mode, see the trade-off, then switch on enforcement. |
| G9 | Run the whole prototype at zero cost. |

### Non-goals for the prototype

- Replacing a bank's or PSP's existing fraud system.
- Moving money or holding real funds. A mock PSP pretends to execute payments.
- Live NPCI, bank or telecom data.
- Recording or analysing call audio.
- Reading the user's SMS inbox or call log automatically. The payer chooses what to share.
- Submitting complaints to any government portal. The complaint pack is generated for the user to file.
- Using a language model to make or influence a payment decision.
- Production security hardening, high availability or regulatory certification.

## 6. Users

| Persona | Needs |
|---|---|
| **Payer** | Pay quickly when things are normal. Be stopped, with a reason they understand under stress, when they are being scammed. |
| **Elderly payer** | Large text, one clear instruction, their own language, a spoken warning, a family member in the loop. |
| **Guardian** | Know immediately when a relative is about to make a risky payment, and be able to stop or approve it from their own phone. |
| **Small-business owner or accountant** | Protection when a payment request claims to come from the boss or a vendor. |
| **Fraud analyst** | A live feed of decisions, the evidence behind each one, the network a payee belongs to, and a way to confirm fraud or mark a false positive. |
| **Risk manager at a bank** | Proof of what the system would have done before turning it on, and control over the thresholds. |
| **Integration engineer** | One API call with a stable contract, a hard latency ceiling, and predictable behaviour when the service is down. |
| **Investigator** | An evidence pack linking a scam call or message to the payment it caused. |

## 7. User stories

| ID | Story | Feature |
|---|---|---|
| US-1 | As a payer, I see a trust indicator and the registered name for a payee as soon as I enter their UPI ID. | F-04, F-22 |
| US-2 | As a payer making a normal payment, I see no extra screens. | F-01, F-09 |
| US-3 | As a payer making a risky payment, I see why it is risky in one or two sentences, in my language. | F-07, F-29 |
| US-4 | As a payer being coached on a call, I am told which scam this looks like and what the scammer is likely saying. | F-05, F-08 |
| US-5 | As a payer at Step-Up, I am made to pause and answer one question before I can continue. | F-09 |
| US-6 | As a payer who was blocked wrongly, I can ask for a human review. | F-09, F-13 |
| US-7 | As a payer who received a suspicious message, I can paste it in and be told whether it is a scam. | F-16 |
| US-8 | As a payer, I am protected automatically if I later try to pay the UPI ID named in a message I flagged. | F-16 |
| US-9 | As an elderly payer, I can nominate a family member who is alerted, or must approve, when I make a risky payment. | F-17 |
| US-10 | As a guardian, I get the alert on my phone with the reason, and can approve, deny or call. | F-17 |
| US-11 | As a payer, I hear the warning read aloud in my language. | F-21 |
| US-12 | As a payer whose payment was held, I can cancel it at any time and am asked again before it goes. | F-23 |
| US-13 | As a payer who has been scammed, I can generate a complaint pack with all the evidence in one tap. | F-25 |
| US-14 | As a payer, I can rehearse a simulated scam so I recognise the real one. | F-28 |
| US-15 | As an analyst, I see decisions arrive live with tier, score, reasons and scam family. | F-13 |
| US-16 | As an analyst, I can open a decision and see the call, the message and the payment on one timeline. | F-13 |
| US-17 | As an analyst, I can confirm fraud or mark a false positive, and that label feeds reputation and retraining. | F-11, F-13 |
| US-18 | As an analyst, I can see the network of accounts a payee sends money to and shares devices with. | F-20 |
| US-19 | As an analyst, I get a short written summary of a case and a suggested next step. | F-27 |
| US-20 | As a risk manager, I can run in shadow mode and see how many scams would be caught and how many payers interrupted at any threshold. | F-19 |
| US-21 | As a risk manager, I can see whether the model is drifting and whether it treats user groups unevenly. | F-26 |
| US-22 | As a risk manager, I benefit when another bank's customers report a payee. | F-24 |
| US-23 | As an integration engineer, I get a decision or a safe default within a fixed timeout, every time. | F-01 |
| US-24 | As an admin, I can add staff, set their role and deactivate accounts, and each role sees only what it should. | F-30 |
| US-25 | As a payer, I sign in with my phone number and a PIN, or register a new account. | F-30 |

## 8. Prototype scope: the feature catalogue

"Origin" shows whether a feature was in the pitch deck or added afterwards. Full specifications are in [02](02-feature-specs.md).

| ID | Feature | Priority | Origin |
|---|---|---|---|
| F-01 | Risk assessment API with tiered response | Must | Deck |
| F-02 | Deterministic rule engine | Must | Deck |
| F-03 | ML scoring ensemble | Must (XGBoost), Should (Random Forest, Isolation Forest, stacker) | Deck |
| F-04 | Payee reputation engine and pre-payment chip | Must | Deck |
| F-05 | Call correlation | Must | Deck |
| F-06 | Note-field NLP | Must (lightweight model), Could (DistilBERT) | Deck |
| F-07 | Explainability: reason codes | Must | Deck |
| F-08 | Scam-family attribution and pattern-specific messages | Must | Deck |
| F-09 | Tiered payer experience: Safe, Warning, Step-Up, Block | Must | Deck |
| F-10 | Outcome logging and near-miss feed | Must | Deck |
| F-11 | Feedback loop: live reputation updates | Must | Deck |
| F-12 | Retraining pipeline and model registry | Should | Deck |
| F-13 | Analyst dashboard (live feed, decision detail, timeline, case actions, payee lookup) | Must | Deck |
| F-14 | Synthetic simulator and demo control | Must | Deck |
| F-15 | Observability | Should | Deck |
| F-16 | Message checker | Must | Added |
| F-17 | Trusted-contact guardian | Must | Added |
| F-18 | Coaching signals from the session | Must | Added |
| F-19 | Shadow mode and policy lab | Must | Added |
| F-20 | Mule network graph | Should | Added |
| F-21 | Spoken warning | Should | Added |
| F-22 | Payee name mismatch | Must | Added |
| F-23 | Timed hold | Should | Added |
| F-24 | Cross-bank reputation sharing (simulated consortium) | Could | Added |
| F-25 | One-tap complaint pack | Should | Added |
| F-26 | Drift and fairness monitoring | Could | Added |
| F-27 | AI-written case summaries | Could | Added |
| F-28 | Scam rehearsal simulation (scripted) | Could | Deck, marked "planned" |
| F-29 | Localisation: English and Hindi | Must | Deck |
| F-30 | Authentication and role-based access (admin, analyst, auditor, app user) | Must | Added |
| F-31 | Flagged-caller warning and caller reports | Should | Added after the first build |
| F-32 | Look up a UPI ID or a phone number | Should | Added after the first build |
| F-33 | Personal never-pay list | Should | Added after the first build |
| F-34 | Scam trends and official help | Could | Added after the first build |
| F-35 | Session and device security | Must | Added after the first build |

**Counts:** 21 Must, 9 Should, 5 Could: 35 features. F-03 and F-06 are counted once, as Must. F-31 to F-33 take ideas from caller-ID apps; no caller-ID service is used as a data source.

## 9. What is real and what is simulated

| Input or system | In the prototype | In a pilot |
|---|---|---|
| Transactions | Synthetic, from the simulator | Bank or PSP transaction stream |
| Payment execution | Mock PSP service | The PSP's own switch |
| UPI ID directory and registered names | Seeded table in the mock PSP | NPCI name resolution through the PSP |
| Call signals | Simulated telecom API, and a fake incoming-call screen in the demo app | DoT FRI through the bank; consented on-device call state |
| Caller risk ratings | Seeded list, plus numbers extracted from messages users flag | DoT FRI |
| Scam messages | Pasted by the user; seeded examples | Shared by the user; operator spam feeds |
| Session signals | Real, collected by the demo app | Real, collected by the PSP app or an SDK |
| Payee denylist | Seeded table | DPIP registry, bank lists |
| Other banks' reports | A second simulated tenant in the same deployment | A consortium service between banks |
| Fraud labels | Injected by the simulator, plus analyst clicks | Confirmed complaints |
| Account balance | Simulated | Available inside the bank or PSP |
| OTP delivery | Shown in-app as a simulated SMS | SMS through the bank's gateway |
| Guardian alerts | In-app, over a live socket | Push notification and SMS |
| Device biometric | Real, on the demo phone | Real |
| Spoken warning | Real, on the demo phone | Real |

## 10. Success metrics

All detection figures are measured on a held-out synthetic test set ([06, section 15](06-risk-engine-and-ml.md#15-training-and-evaluation)). They show that the pipeline works, not how it would perform on real traffic. Say so when presenting them. **What was measured against each target is in [12, section 4.1](12-as-built.md#41-detection-held-out-last-15-days-19498-payments-742-scams).**

| Metric | Target | Measured where |
|---|---|---|
| Hot-path latency | p95 ≤ 150 ms, p99 ≤ 250 ms at 200 requests/s | One laptop. Measured at 100 requests/s; see [12](12-as-built.md#44-speed). |
| Scam recall at Step-Up or Block | ≥ 85% | Held-out scam scenarios |
| Recall on unseen scam variants | ≥ 70% | Variants excluded from training |
| Legitimate payments sent to Step-Up or Block | ≤ 1% | Held-out legitimate payments |
| Legitimate payments sent to Block | ≤ 0.1% | Same |
| Legitimate payments shown a Warning | ≤ 5% | Same |
| Explanation coverage | 100% of non-Safe decisions carry at least one reason | Automated check |
| Scam-family attribution accuracy | ≥ 80% top-1 | Labelled scam scenarios |
| Signal-to-payment linkage | ≥ 95% of scam payments preceded by a flagged call or message are linked | Scenario replay |
| Feedback latency | Payee reputation reflects a report or near-miss within 5 s | Timer on the async path |
| Guardian alert latency | Guardian sees a request within 3 s | Timer |
| Message checker | ≥ 90% recall on scam messages, ≤ 5% of benign messages flagged | Held-out message set |
| Degraded-mode safety | 0 payments blocked because a dependency was down | Chaos test |
| Cost | ₹0. No account has a payment method attached. | Checklist in [08](08-tech-stack-and-free-services.md) |

## 11. Beyond the prototype

Not built now, recorded so the prototype does not close them off.

- **Real signals.** DoT FRI through a partner bank; consented on-device call state and screen-share detection; DPIP and MuleHunter.AI as inputs.
- **Real consortium.** Cross-bank reputation sharing between separate organisations, with a privacy review of the hashing scheme.
- **Multi-tenancy.** Row-level isolation, per-bank rule packs and thresholds.
- **Deployment.** An Indian cloud region, managed PostgreSQL, Redis and Kafka, autoscaling.
- **SDK.** The payer-side logic packaged for PSP apps.

**Business model, from the deck.** Customers are banks, UPI PSPs and fintechs. Revenue is B2B licensing, per-transaction pricing and enterprise support. The path is a single-bank pilot, then ecosystem integration, then production.
