# UPI Inspector — Prototype Documentation

| | |
|---|---|
| **Product** | UPI Inspector: AI-driven, real-time UPI fraud prevention |
| **Team** | Semicolonist (Atharva Andhare, Sanket Kharche, Tanay Patil, Sharwari Dhandale) |
| **Event** | DECODE SIH 2026, OSCode by communityX |
| **Version** | 2.2 (7 October 2026) |
| **Status** | Built. The service, the models, the dashboard and the payer app run on a laptop and pass their tests. What was and was not verified is in [12](12-as-built.md). |

## What this is

The complete specification for the UPI Inspector **prototype**: what it does, how every feature behaves, how it is built, what it runs on, and how it is tested and demonstrated. Documents 01 to 11 were written before building and then corrected to match the build; [12](12-as-built.md) records exactly what exists, how to run it and what was measured.

## Run it

Python 3.12 and Node 20 or later. First time, about 10 minutes:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py bootstrap
cd apps\dashboard && npm install && npm run build
cd ..\mobile && npm install
```

Every time, in three terminals:

```
python manage.py api          # http://localhost:8000
python manage.py dashboard    # http://localhost:3000
python manage.py app --web    # http://localhost:8081  and  http://localhost:8081/?slot=2 for a second phone
```

Sign in with one click from the demo accounts listed on each sign-in screen (offered on the laptop itself; a phone signs in with a PIN). `python manage.py preflight` checks that everything a demo needs is in place. Details, including passwords and running on a real phone, are in [12, section 3](12-as-built.md#3-running-it).

Three constraints shape everything here:

1. **Zero cost.** Every service is open source and self-hosted, or on a free tier that needs no payment card. See [08](08-tech-stack-and-free-services.md).
2. **No real data.** Transactions, calls, messages and bank directories are synthetic or simulated.
3. **Visual design comes from the two reference designs the team supplied.** Colours, type, spacing and component styling follow them, with navigation on the left in both apps. Screens, content, copy and flows come from this documentation. See [03](03-ux-and-design.md).

## Document map

| # | Document | Answers |
|---|---|---|
| 01 | [Product overview](01-product-overview.md) | What problem, for whom, what is in the prototype, how success is measured |
| 02 | [Feature specifications](02-feature-specs.md) | Exactly how each of the 35 features behaves and when it counts as done |
| 03 | [UX and design](03-ux-and-design.md) | Every screen in the payer app and the dashboard, all user-facing copy, how the supplied design is applied |
| 04 | [Architecture](04-architecture.md) | Components, request flows, how it runs, latency budget, failure behaviour |
| 05 | [Data model](05-data-model.md) | PostgreSQL schema, Redis keys, event topics, feature catalogue, seed data |
| 06 | [Risk engine and ML](06-risk-engine-and-ml.md) | Rules, models, score formula, reputation, signals, explanations, simulator, training, monitoring |
| 07 | [API reference](07-api-reference.md) | Every endpoint and real-time event, with request and response shapes |
| 08 | [Tech stack and free services](08-tech-stack-and-free-services.md) | What each tool is for, free-tier limits, accounts to create, environment variables |
| 09 | [Build plan](09-build-plan.md) | Repository layout, setup, phases, task lists, cut lines |
| 10 | [Testing and demo](10-testing-and-demo.md) | Test layers and their results, acceptance tests, load test, demo script, the check scripts |
| 11 | [Security, risks and decisions](11-security-risks-and-decisions.md) | Every security control and its test, what is still weak, privacy, risks, open questions, decision log, deck fixes, references |
| 12 | [As built](12-as-built.md) | What exists, how to run it, measured results, differences from the plan, known gaps |
| 13 | [Testing on a real phone](13-phone-test.md) | The 37-step test to run on two Android phones, which has not been run yet |
| — | [hindi-review.csv](hindi-review.csv) | Every Hindi string beside its English, for a native speaker to correct |
| — | [archive/PRD-v1.0.md](archive/PRD-v1.0.md) | The first PRD, superseded by this set |

## The prototype at a glance

- A bank or UPI app calls UPI Inspector **just before the PIN screen**. It returns one of four tiers: **Safe**, **Warning**, **Step-Up** or **Block**.
- The decision combines the payment, the payee's reputation, the payer's habits, and what happened around the payment: a suspicious call, a scam message, signs of being coached, a mismatched payee name, the payee's place in a mule network.
- Every flag carries a **plain-language reason** and the **name of the scam pattern**, in English or Hindi, optionally spoken aloud.
- Protective actions go beyond a warning: a pause with a targeted question, a trusted family member who is alerted or must co-approve, and a timed hold.
- Analysts get a live dashboard: linked call-and-payment timelines, a mule network graph, a policy lab for tuning thresholds in shadow mode, and model health.
- Outcomes feed back: an abandoned payment lowers the payee's reputation within seconds and protects the next payer.
- People protect each other and themselves: a warning when a number other people have reported is calling, a look-up for a UPI ID or a number, and a personal never-pay list that takes a day to undo.
- Because it is about payments: short sessions that sign out for real, an app lock, a Step-Up that a copied token cannot pass, and a decision that clears one payment for ten minutes.

**35 features:** 21 Must, 9 Should, 5 Could. The first 30 are listed in [01, section 8](01-product-overview.md#8-prototype-scope-the-feature-catalogue); F-31 to F-35 were added after the first build and are specified in [02, Part C](02-feature-specs.md#part-c--community-and-security-features). All 35 have code behind them; how each was checked is in [12, section 1](12-as-built.md#1-what-was-built).

**How it runs:**

| Mode | Runs on | Status |
|---|---|---|
| **Laptop native** | One laptop, nothing installed beyond Python and Node: embedded PostgreSQL, an in-process cache and event queue, the API and its workers in one process | Built and tested. This is the prototype. |
| **Containers** | Docker Compose with Redis and Redpanda | Planned in 04, not built. The Redis option passes the acceptance suite on a Redis emulator, not on a real server; Kafka and an external PostgreSQL were never run. |
| **Hosted copy** | Free tiers of Vercel, Render, Neon and Upstash | Not built. The team chose a laptop demo. |

**Measured, on synthetic data the team generated:** 99.9% of simulated scams reach Step-Up or Block with 0.14% of honest payments interrupted (leaving out payees with confirmed fraud); at 100 payments a second a decision takes between 14 and 111 ms at the 95th percentile, depending on the run, against a 150 ms budget (20 ms in the run made after the security hardening). These show the pipeline works. They are not real-world accuracy. See [12, section 4](12-as-built.md#4-measured-results).

## Conventions

| Prefix | Meaning | Defined in |
|---|---|---|
| `F-xx` | Feature | 01, 02 |
| `US-xx` | User story | 01 |
| `R-xx` | Risk rule | 06 |
| `P-xx`, `D-xx` | Payer-app screen, dashboard page | 03 |
| `AT-xx` | Acceptance test | 10 |
| `DEC-xx` | Decision made in this documentation | 11 |

- **Priority.** *Must* is needed for the core demo. *Should* makes it finals-ready. *Could* is stretch.
- **Simulated** means the prototype fakes an input that would come from a real system in a pilot. Every simulated input is listed in [01, section 9](01-product-overview.md#9-what-is-real-and-what-is-simulated).
- **If two documents disagree:** 05 and 07 are authoritative for names (tables, fields, endpoints); 02 is authoritative for behaviour; 06 is authoritative for formulas and thresholds.
- Numeric targets other than the 150 ms latency budget and the tier thresholds (0.30, 0.60, 0.85) are proposals from this documentation, not commitments made in the pitch deck.
- **If a document from 04 to 11 disagrees with 12 about how the prototype is built or run, 12 is correct.**

## Decisions the team made

| # | Question | Decision |
|---|---|---|
| 1 | The design to take visual elements from | Two reference designs: Madgicx (near-black violet, icon rail on the left, pill buttons, segmented tabs) and Scalenut (dark navy, blue buttons, cyan links). Applied to both the payer app and the dashboard. |
| 2 | Where navigation sits | On the left, in both apps: a sidebar on the dashboard, an icon rail in the payer app. |
| 3 | Sign-in | Required in both apps, with positions: admin, analyst and auditor on the dashboard; payers in the app (F-30). |
| 4 | Build window | As the laptop allows. |
| 5 | Demo devices | Two phones for now; a laptop demo is enough. Two browser windows stand in for the phones. |
| 6 | Hosting | None for now. |
| 7 | What a Block does | A score from 0.85 to below 0.95 is a timed hold that re-checks; 0.95 and above, or a hard rule, is a decline. |
| 8 | Languages | English and Hindi. |
| 9 | Voice for the spoken warnings | On-device text-to-speech. |
| 10 | Ideas from caller-ID apps | Three taken: the caller warning with reports, the look-up, the never-pay list. No caller-ID service is used as a data source. |
| 11 | Security | Hardened beyond what a demo needs, because the product is about payments. Passwordless demo sign-in only on the laptop itself. |

Still open, and prepared for: a native speaker's review of the Hindi text (`hindi-review.csv`), a run on real phones ([13](13-phone-test.md)), and a first call to a real language model (`python manage.py llm-check --live`, once a free key is in `.env`).

## Glossary

| Term | Meaning |
|---|---|
| **VPA / UPI ID** | Virtual Payment Address, such as `name@bank`. Used interchangeably. |
| **PSP** | Payment Service Provider: the app or bank that initiates a UPI payment. |
| **Payer / payee** | The person sending money / the UPI ID receiving it. |
| **Hot path** | The synchronous work done between the app asking for a decision and getting one. Budget: 150 ms. |
| **Async path** | Everything that happens after the response: logging, reputation updates, graph and monitoring jobs. |
| **Tier** | Safe, Warning, Step-Up or Block. |
| **Action** | What the app must do: `ALLOW`, `WARN`, `STEP_UP`, `HOLD` or `DECLINE`. |
| **Step-Up** | A modal that makes the payer pause, answer one question and authenticate again. |
| **Hold** | A Block that schedules the payment for later and re-checks it then. |
| **Near-miss** | A payment the payer abandoned after being warned. A weak sign of a scam. |
| **Mule** | An account used to receive and forward scam proceeds. |
| **Guardian** | A trusted contact nominated by the payer. |
| **Shadow mode** | Scoring every payment without intervening, to measure what would have happened. |
| **Scam family** | A named scam pattern such as digital arrest or fake refund. |
| **Reason code** | A machine identifier for one plain-language reason, such as `NEW_PAYEE`. |
| **SHAP** | A method for attributing a model's output to its input features. |
| **PSI** | Population Stability Index, a measure of how far a distribution has drifted. |
| **FRI** | Financial Fraud Risk Indicator, the Department of Telecommunications' risk rating for mobile numbers. |
| **DPIP** | Digital Payments Intelligence Platform, RBI's shared fraud-intelligence platform. |

## Change log

| Version | Date | Change |
|---|---|---|
| 2.1 | 7 Oct 2026 | The prototype was built. Added sign-in and roles (F-30), navigation on the left, and the two reference designs. Documents 04 to 11 corrected to match the build; added 12, the as-built record with measured results. |
| 2.0 | 7 Oct 2026 | Restructured into a documentation set scoped to the prototype. Added 12 features (F-16 to F-27) and brought the planned scam rehearsal (F-28) into scope. Added the zero-cost constraint and re-based the stack on free services. Added the design token contract. |
| 1.0 | 7 Oct 2026 | Single-file PRD covering prototype and pilot. Archived. |
