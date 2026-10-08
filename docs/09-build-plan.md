# 09 — Build Plan

[← Index](README.md)

**Status: built.** The prototype was built in one pass on 7 October 2026, and extended the same day with five more features and a security review ([12, section 1](12-as-built.md#added-after-the-first-build)). This document now records the repository as it is, how to set it up, what the build taught, and what is left to do. The original six-phase, 26-day plan is summarised in section 6 for reference.

## 1. Repository layout

```
DECODE/
├── manage.py                      # task runner: every command in section 4
├── requirements.txt               # Python dependencies, service and training together
├── .env.example                   # every setting, with comments
├── docs/                          # this documentation
├── contracts/
│   └── openapi.yaml               # exported from the running code (101 paths)
├── packages/
│   └── design-tokens/
│       ├── tokens.json            # values taken from the two reference designs
│       └── build.mjs              # writes tokens.css (dashboard) and tokens.ts (app)
├── infra/
│   ├── postgres/001_schema.sql    # the first build's schema
│   ├── postgres/002_security_and_community.sql   # later additions; idempotent, applied at every start
│   └── seed/                      # rehearsals.json, message_testset.json
├── services/
│   └── risk-api/
│       ├── app/
│       │   ├── main.py            # application, start-up, error shape
│       │   ├── orchestrator.py    # the hot path
│       │   ├── core/              # features, rules, scoring, decide, explain, reputation,
│       │   │                      # state (feature store), warm, graphfeat, textcheck, textmodel, present
│       │   ├── guard.py, security.py, sessions.py, posture.py   # request guard, tokens and limits, revocation, start-up checks
│       │   ├── routers/           # auth, risk, stepup, signals, payer, community, reports, analyst, ops, demo
│       │   ├── workers/           # pipeline (logging, reputation, consortium), protect (guardian,
│       │   │                      # holds), jobs (graph, drift, fairness), summariser
│       │   ├── services/packs.py  # complaint pack
│       │   └── config, db, cache, bus, security, i18n, realtime, runtime, common
│       ├── rules/rules.yaml       # the rule set
│       ├── i18n/                  # en.json, hi.json: reasons, families, advice
│       └── tests/                 # test_core, test_security, test_bus_and_llm
├── ml/
│   ├── upi_ml/
│   │   ├── simulator.py           # world, campaigns, lookalikes, demo users and payees
│   │   ├── replay.py              # runs history through the real feature store
│   │   ├── train.py               # models, stacker, calibration, prototypes, metrics, gates
│   │   ├── text_train.py          # message and note classifier
│   │   ├── offline.py             # frame scoring with the service's own decision code
│   │   ├── pipeline.py, seed_db.py  # simulate, train and register, backfill, load PostgreSQL
│   │   ├── golden.py, e2e.py, loadtest.py, analyze.py
│   │   └── checks.py              # preflight, security-check, i18n-check, phone-check, llm-check, bus-check
│   ├── golden/scenarios.yaml      # 44 golden scenarios
│   └── artifacts/                 # trained models and the ACTIVE pointer (not committed)
└── apps/
    ├── dashboard/                 # Next.js: src/app/login, src/app/(app)/<page>, src/components, src/lib
    └── mobile/                    # Expo: App.tsx, src/screens, src/components, src/{api,flow,store,nav,socket,i18n,speech}.ts
```

Differences from the planned layout: the mock PSP and the workers are inside `risk-api`; there is no `docker-compose.yml`, `Makefile`, migrations folder, Prometheus, Grafana or Airflow configuration, and no CI workflow.

## 2. Prerequisites

| On the laptop | Version | For |
|---|---|---|
| Python | 3.12 | The service, the simulator, training, the tests |
| Node.js | 20 or later | Dashboard and payer app |

On a phone, optionally: Expo Go, on the same Wi-Fi as the laptop.

Memory, as observed on the build laptop (15.6 GB): the API process about 450 MB, PostgreSQL about 200 MB, the simulation about 480 MB. Training and the two front-end servers were not measured. Disk: about 340 MB for the database and models, plus the two `node_modules` folders.

## 3. First-time setup

```
python -m venv .venv
.venv\Scripts\activate                 (on macOS or Linux: source .venv/bin/activate)
pip install -r requirements.txt
python manage.py bootstrap             about 10 minutes

cd apps/dashboard
npm install
npm run build
cd ../mobile
npm install
```

Then, in three terminals:

```
python manage.py api                   http://localhost:8000
python manage.py dashboard             http://localhost:3000
python manage.py app --web             http://localhost:8081
```

`bootstrap` runs, in order: `setup` (write `.env`), `text` (train the text classifier), `simulate` (generate 90 days, replay, load PostgreSQL), `train` (train, evaluate, register, promote), `backfill` (score the last 15 days into the dashboard as unenforced decisions).

## 4. Tasks

| Command | Does |
|---|---|
| `python manage.py setup` | Write `.env` with fresh random secrets (`--force` to regenerate; that invalidates stored hashes) |
| `python manage.py bootstrap` | Everything needed for a first run |
| `python manage.py simulate [--users 800 --days 90 --campaigns 66 --seed 7]` | Generate history, replay it through the feature code, rebuild and load PostgreSQL. **Erases the database.** |
| `python manage.py train [--no-promote]` | Train, calibrate, evaluate, register; promote if the gates pass |
| `python manage.py backfill` | Score the last 15 days of history into decisions |
| `python manage.py text` | Train the text classifier |
| `python manage.py api [--port 8000]` | Run the API and its workers |
| `python manage.py dashboard [--dev]` | Run the dashboard; builds it first if needed |
| `python manage.py app [--web]` | Run the Expo dev server |
| `python manage.py test` | Unit tests |
| `python manage.py golden` | Golden scenarios |
| `python manage.py e2e [--only AT-3,AT-11] [--fast]` | Acceptance tests against a running API |
| `python manage.py load [--rps 100 --seconds 60]` | Load test |
| `python manage.py demo-reset` | Restore the seeded state on a running API |
| `python manage.py export-openapi` | Write `contracts/openapi.yaml` |
| `python manage.py db` / `db-stop` | Start or stop the embedded PostgreSQL |
| `python manage.py preflight` | Everything a demo needs, checked in one go |
| `python manage.py security-check` | Configuration, the front door, and every endpoint with the wrong credentials |
| `python manage.py i18n-check` | English and Hindi line up; writes `docs/hindi-review.csv` |
| `python manage.py i18n-apply <sheet>` | Put a reviewer's corrections back into the code |
| `python manage.py phone-check [--export]` | Is the payer app ready for a real phone |
| `python manage.py llm-check [--live]` | The case-summary providers |
| `python manage.py bus-check [--url]` | Run the Redis event bus |
| `python manage.py redis-emulator [--port 6390]` | A Redis look-alike on a TCP port |

To rebuild the design tokens after editing `tokens.json`: `node packages/design-tokens/build.mjs`.

## 5. Workstreams

For the work that remains, the same four areas as planned:

| Workstream | Owns |
|---|---|
| **A. Platform** | `risk-api`, PostgreSQL, the cache and bus, the API contract, the mock PSP |
| **B. ML and data** | Simulator, features, models, calibration, reasons, attribution, text model, graph features, monitoring maths |
| **C. Payer app** | The Expo app, copy, Hindi, audio, testing on phones |
| **D. Dashboard and ops** | The dashboard, real-time, demo tooling, any hosting |

## 6. How the build went

### The original plan

Six phases over 26 working days for four people: foundations; a rules-only walking skeleton; the models and reputation; the protective features; depth (ensemble, graph, holds, complaint pack); stretch items (consortium, drift, summaries, rehearsals); then hardening and a hosted copy. Cut lines after Phases 3, 4 and 5 for Must, Should and everything.

### What happened

Everything through the stretch phase was built in one pass, without the hosted copy, and in a different order: the decision core and the simulator first, then the service around them, then the two front ends, then testing against the running system. The last step changed the most.

| Found by | What it found | What changed |
|---|---|---|
| First training run | One tree separated the classes perfectly, on the age of the payee's account | The simulator gained new users and shops, rented mules, look-alike honest payments and weakened scams ([06, section 14.4](06-risk-engine-and-ml.md#144-hard-negatives-and-weakened-scams)) |
| False-positive analysis | Honest payments stopped by the rule for payees another bank had reported | Fewer rented mules; the model capped at 0.94; R-16 added; budgets judged without confirmed-fraud payees |
| Golden scenarios with the model | A burst of strangers paying a three-day-old account scored only a Warning; an investment scam was named a KYC threat | R-04b added; story dimensions weighted in the family comparison, the weight chosen by cross-validation over campaigns |
| First acceptance run | Four failures, all from one scenario's staged call leaking into the next test | Tests reordered; reset restores more |
| First load test | 245 of 3,000 requests answered; the decision took 137 ms | Single-payment scoring rewritten ([06, section 3](06-risk-engine-and-ml.md#3-models)) |
| Longer load tests | The logger fell thousands of events behind; periodic jobs stalled payments for a second at a time | Decisions written in batches; the graph computed in its own process; the drift monitor reads less ([10, section 4](10-testing-and-demo.md#4-load-and-failure-tests)) |
| Load test followed by the acceptance suite | A case summary took minutes to appear after a burst of flagged payments | The summary worker pauses only after calling an outside model |
| Using the app by hand | A first payment to a friend was described as "looks like a pass-through account" | A family is named only on its own evidence; wording variants ([06, section 11](06-risk-engine-and-ml.md#11-scam-taxonomy-and-attribution)) |
| Using the app by hand | Two browser windows were the same user | `?slot=` gives a window its own session |
| Using the dashboard by hand | Case summaries said "PAYER_1"; a label read "would allowed"; a total overflowed its card | Fixed |
| A security review of the finished build | Thirteen faults, among them: the app could report its own stopped payment as paid; a Step-Up passed on the app's word that the fingerprint matched; a decision could be paid twice | Each fixed and tested ([12, section 1](12-as-built.md#added-after-the-first-build)) |
| Running the Redis option for the first time | The consumer loop would spin; a cache rebuild deleted the event streams for good; two functions were placeholders | Fixed; the acceptance suite passes in that mode on an emulator |
| The new pre-demo check | A dashboard build older than its source | Rebuilt |

The lesson for whoever continues: the automated tests passed before several of these were found. Running the real thing by hand found what the tests did not ask, and reading the code as an attacker would found what using it did not.

## 7. What is left

In rough order of value. Items 1 to 3 need people or accounts this build did not have.

| # | Work | Why |
|---|---|---|
| 1 | Run the payer app on two Android phones: [13](13-phone-test.md), about 30 minutes | Nothing has run on a phone. `phone-check` passes; the camera, the fingerprint prompt, the keystore and the spoken warning are untested on a device. |
| 2 | Give `docs/hindi-review.csv` to a native speaker, then `python manage.py i18n-apply` | 517 strings, none reviewed |
| 3 | Put a free Gemini or Groq key in `.env`, or install Ollama, then `python manage.py llm-check --live` | No real language model has been called; the provider code passes against a stand-in |
| 4 | Rehearse the demo script in [10, section 5](10-testing-and-demo.md#5-demo) three times and record one run | The script was checked step by step, not end to end in one sitting |
| 5 | Raise text-classifier recall from 85% to 90% | Below target; add templates for the cases it misses, and write a fresh test set that nobody has looked at |
| 6 | Limit how far client-reported session signals can move the model's output | See [12, section 4.1](12-as-built.md#41-detection-held-out-last-15-days-19498-payments-742-scams) |
| 7 | Make the simulator harder, and include large bursts | Detection figures are near the ceiling; R-04b is unmeasured |
| 8 | Reword reasons for the guardian's view; translate the complaint pack's timeline; write Hindi versions of two practice scripts | Each reads oddly or is missing today |
| 9 | Write events to the `outbox` table before publishing | The in-process queue loses events if the process stops |
| 10 | TLS, and one-time codes by SMS | The two security gaps that most limit a pilot ([11, section 1](11-security-risks-and-decisions.md#known-gaps)) |
| 11 | A development build with a hardware-backed key and the phone's call log | So that the fingerprint itself unlocks the device key, and real calls are seen |
| 12 | Run against a real Redis; build the separate worker process | The Redis option passes on an emulator only; more than one API process is needed for more than about 100 payments a second |
| 13 | A hosted copy, if a public link is wanted | Not built |
| 14 | Find out why the decision time varies between load runs on the same code | p95 was 14 to 20 ms in three runs and about 110 ms in two others |

## 8. Definition of done

A feature is done when:

1. Its behaviour matches [02](02-feature-specs.md).
2. Its acceptance tests in [10](10-testing-and-demo.md) pass.
3. Its screens use design tokens only, in both languages where payer-facing.
4. Any new rule has a unit test and a golden scenario.
5. Any new feature in the feature vector has a default and is produced by the simulator.
6. Someone has used it by hand in the running app.

## 9. Working agreements

- **One feature definition.** The service and the training code import the same feature module. Nobody re-implements a feature.
- **One decision function.** The service, the golden scenarios and the offline scorer all call `core/decide.py`.
- **Rules and thresholds are data.** They change in `rules.yaml` or `policy_config`, never in code.
- **After any change to rules or models:** `python manage.py golden`, then `python manage.py e2e`.
- **No real personal data**, anywhere, including screenshots and test messages.
- **Demo data is sacred.** Changes to seed users and payees are announced, because the demo script depends on them.
- **Say what was not tested.** [12, section 6](12-as-built.md#6-known-gaps) is kept current.

## 10. The design

Done as follows:

1. Colours, type, spacing, radii, shadows and motion were taken from the two reference designs into `packages/design-tokens/tokens.json`, using the names in [03, section 2](03-ux-and-design.md#2-design-token-contract).
2. The four tier colours were chosen to sit on the dark background with enough contrast, and are recorded in 03.
3. `build.mjs` writes the tokens into both apps. Neither app contains a colour or a radius of its own.
4. Navigation is on the left in both: a grouped sidebar on the dashboard, an icon rail in the payer app, hidden inside the payment flow and on the tier screens.

Not done: a contrast check of every text and background pair with a tool, and a review of every screen on a phone.
