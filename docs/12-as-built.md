# 12 — As Built

[← Index](README.md)

What exists in the repository on 7 October 2026, how to run it, what was measured, and where it differs from the plan in documents 04 to 11. Where this document and an earlier one disagree about how the prototype is built or run, this one is correct.

## 1. What was built

All 35 features have code behind them: the 30 of the first build and five added in a second pass the same day. The table says how each was checked.

| | Status |
|---|---|
| **Risk service** | Built: FastAPI service with 101 paths, rule engine, model ensemble, payee reputation, signals, explanations, guardian, hold, consortium, complaint packs, drift, fairness, case summaries, roles, caller reports, look-up, never-pay lists, trends, sessions and a request guard. |
| **Models** | Trained and promoted: `ens-2026.10.07-52648` (XGBoost, Random Forest, Isolation Forest, stacker, calibration) and a text classifier `text-2026.10.07-854`. |
| **Analyst dashboard** | Built: sign-in and 13 pages, navigation on the left. Checked by hand in a browser against the live API. |
| **Payer app** | Built: 22 screens, icon rail on the left, English and Hindi. Checked by hand on the web build. **Not run on a physical phone.** |
| **Tests** | 120 unit, 44 golden, 33 acceptance, all passing; a security sweep of every endpoint; a load test within budget at 100 payments a second. |
| **Hosted copy** | Not built. The team chose a laptop demo. |

### Added after the first build

On 7 October 2026, after the first build was complete, a second pass did three things.

**1. Five more features** (F-31 to F-35): a warning when a reported number calls and a way to report a caller; a look-up for a UPI ID or a phone number; a personal never-pay list; this week's scam trends with the official helplines; and session and device security. The first three take ideas from caller-ID apps and apply them to payments.

**2. A security review, because this is about payments.** It found real faults in the first build, each now fixed and tested:

| Found | Fixed by |
|---|---|
| A modified app could report its own held or declined payment as paid, and so inflate a payee's good history | Only the PSP may report that money moved |
| A Step-Up could be passed by the app simply saying the fingerprint matched | A proof made with a key the phone enrolled using the PIN |
| Wrong one-time codes were counted but never limited; the code was stored as a plain hash | Three wrong attempts close the challenge; three codes per challenge; a keyed hash bound to the challenge |
| The same decision could be paid twice by two requests at the same moment, and two payments could spend the same balance | A lock per decision; the balance is reduced and checked in one step |
| A decision cleared a payment for 24 hours | Ten minutes after its last approval |
| Anyone who knew a `txn_id` could be handed that payment's decision | The idempotency key includes the payer |
| One person could run a payee's reputation down by starting and abandoning flagged payments | At most once a day per payer and payee |
| Signing out only forgot the token on the device; tokens lasted 12 hours | Short tokens renewed while in use; sign-out and credential changes revoke on the server |
| Anyone who could reach the port could sign in as a seeded person without a password | Passwordless demo sign-in only on the laptop itself by default |
| Any web page could call the API from a visitor's browser; the API answered to any host name; `/metrics` and `/docs` were open to all | CORS limited to local pages; a Host check; metrics for the local network only; documentation only in demo mode |
| Staff of one bank could read another bank's complaint pack if they had its ID | Packs are checked against the staff member's bank |
| Nothing was rate-limited except message checks | Limits per address and per person on every sensitive endpoint |
| Sign-in work (Argon2) ran on the thread that answers payments | Moved off it |

A script now calls every endpoint with the wrong credentials (`python manage.py security-check`), so an endpoint added without an access check would be caught. What is still weak is listed in [11, section 1](11-security-risks-and-decisions.md#known-gaps).

**3. The five things the first build left open.** Each was taken as far as this laptop allows; the rest is stated in section 6.

| Left open | Now |
|---|---|
| Nothing has run on a phone | Still true. The Android bundle builds, the app type-checks, the API answers on the laptop's network address, and the device-proof arithmetic matches the server's. A 37-step test is written: [13](13-phone-test.md). |
| The Hindi is a draft | Still true. Automatic checks for missing keys and broken placeholders pass; a review sheet with all 517 strings is in `docs/hindi-review.csv`, and a command puts a reviewer's corrections back. |
| No language model was tried for case summaries | Still true: no key is configured and no Ollama server is installed. The three providers' request format, reply parsing, fact check and fallback order now pass against a stand-in server. |
| The Redis and Kafka options were never run | The Redis option runs: the whole acceptance suite passes on a Redis emulator, both in process and over TCP through the real client, after three faults were fixed. Not on a real Redis server. Kafka is still untested and is now labelled so. A separate worker process, which an earlier note said could be run, does not exist; the note is corrected. |
| Demo mode allows passwordless sign-in | Limited to the laptop itself by default (`DEMO_LOGIN=local`), and never through a proxy or a tunnel. |

### Feature by feature

"Acceptance" means an automated test in `ml/upi_ml/e2e.py` passes against the running API. "Browser" means the screen was also operated by hand.

| ID | Feature | Checked by |
|---|---|---|
| F-01 | Risk assessment API | Acceptance (AT-1, 7, 8), load test, browser |
| F-02 | Rule engine | 21 rules; unit tests per rule; golden scenarios |
| F-03 | Model ensemble | Training gates; golden scenarios |
| F-04 | Payee reputation and chip | Acceptance (AT-5, 6), browser |
| F-05 | Call correlation | Acceptance (AT-3), browser. Calls are simulated. |
| F-06 | Note and message text classification | Acceptance (AT-11); 85% scam recall on the hand-written set, below the 90% target |
| F-07 | Reason codes | Acceptance (AT-10), unit tests |
| F-08 | Scam-family attribution | Acceptance (AT-3, 4, 5, 11); see section 4 for accuracy |
| F-09 | Tiered payer experience | Acceptance (AT-30), browser: Safe, Warning, Step-Up, Hold, Decline |
| F-10 | Outcomes and near-misses | Acceptance (AT-6) |
| F-11 | Feedback loop to reputation | Acceptance (AT-6): trust score falls 0.3 s after a walked-away payment |
| F-12 | Retraining and model registry | `manage.py train` trains, evaluates against gates, registers and promotes. Four versions are in the registry. |
| F-13 | Analyst dashboard | Browser: every page |
| F-14 | Simulator and demo control | Six staged scenarios, each landing on its intended family (10, section 5.2) |
| F-15 | Observability | Stage latency, counters, `/metrics`; System health page |
| F-16 | Message checker | Acceptance (AT-11, 12), browser |
| F-17 | Guardian | Acceptance (AT-13, 14, 15), browser on two windows |
| F-18 | Coaching and session signals | Acceptance (AT-16) |
| F-19 | Shadow mode and policy lab | Acceptance (AT-17, 18), browser |
| F-20 | Mule network graph | Acceptance (AT-19), browser |
| F-21 | Spoken warning | Code path exercised in the browser. **Not listened to on a phone.** |
| F-22 | Payee name mismatch | Acceptance (AT-4), browser |
| F-23 | Timed hold | Acceptance (AT-22), browser |
| F-24 | Consortium signal | Acceptance (AT-23). Two simulated banks in one database. |
| F-25 | Complaint pack | Acceptance (AT-24), browser. The PDF itself was not opened and read. |
| F-26 | Drift and fairness | Acceptance (AT-25) |
| F-27 | AI case summaries | Acceptance (AT-27) with the built-in template. The three providers pass unit tests against a stand-in server. **No real language model has been called.** |
| F-28 | Rehearsals | Acceptance (AT-28), browser |
| F-29 | English and Hindi | Browser: both languages. `i18n-check`: same keys and placeholders in both. The Hindi is a draft and has not been reviewed by a native speaker. |
| F-30 | Sign-in and roles | Acceptance (AT-31, 32), browser; every endpoint called with the wrong credentials (`security-check`) |
| F-31 | Caller warning and reports | Acceptance (AT-33), unit, browser: incoming-call warning, banner, report |
| F-32 | Look-up | Acceptance (AT-34), browser: a UPI ID and a phone number |
| F-33 | Never-pay list | Acceptance (AT-35, 40), browser: add, payment declined, unblock countdown |
| F-34 | Trends and help | Acceptance (AT-36), browser. The helpline links were not followed. |
| F-35 | Session and device security | Acceptance (AT-37, 38, 39), 47 unit tests, `security-check`, browser: weak PIN refused, register, app lock with a wrong and a right PIN, reload reopens locked, change PIN, sign out on every device, Security page. The fingerprint path was not run on a phone. |

## 2. How it runs

One mode was built and tested: **laptop native**. No Docker, no cloud account, no paid service.

| Part | In the plan | As built |
|---|---|---|
| PostgreSQL | Container | PostgreSQL 16 from the `pgserver` Python package, data under `.data/pg`, started on demand |
| Cache | Redis container | `fakeredis` inside the API process (`REDIS_URL=fakeredis://`) |
| Event bus | Redpanda (Kafka) | An in-process queue (`EVENT_BUS=memory`) |
| Workers | Separate process | Inside the API process (`WORKERS_IN_PROCESS=true`) |
| API | Container | `uvicorn` running `app.main:asgi`, one process |
| Dashboard | Container | `next start` on port 3000 |
| Payer app | Expo Go | Expo dev server; web build on port 8081, or Expo Go on a phone |
| Task runner | `make` | `manage.py` (Windows has no `make`) |

The code also has a Redis-streams bus, a Kafka bus, and accepts any PostgreSQL or Redis address. **None of those was run.** Treat them as untested.

Consequences of this mode:

- One process holds the cache. Restarting the API empties it, and the API rebuilds it from PostgreSQL at start (about 5 seconds for 950 users and 39,000 recent payments).
- Only one API process can run. There is no horizontal scaling in this mode.
- The component-failure tests in the plan (stop Redis, stop the bus, stop the worker) cannot be run as written. Two faults are injected in software instead.

## 3. Running it

### 3.1 First time

Needs Python 3.12 and Node 20 or later. About 10 minutes, most of it simulating history.

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py bootstrap
```

`bootstrap` writes `.env` with fresh random secrets, trains the text classifier, simulates 90 days of payments for about 950 people, replays them through the real feature code, loads PostgreSQL, trains and registers the model, and scores the last 15 days into the dashboard as unenforced decisions.

Then the two front ends, once each:

```
cd apps\dashboard
npm install
npm run build
cd ..\mobile
npm install
```

### 3.2 Every time

Three terminals:

```
python manage.py api
python manage.py dashboard
python manage.py app --web
```

| Open | For |
|---|---|
| `http://localhost:3000` | Dashboard |
| `http://localhost:8081` | Payer app, first phone |
| `http://localhost:8081/?slot=2` | Payer app, second phone (its own session) |
| `http://localhost:8000/docs` | API reference generated from the code |

### 3.3 Signing in

- **Demo mode is on by default.** The dashboard sign-in page and the app's Demo accounts tab list the seeded people; one click signs in, with no password. **This is offered only in a browser on the laptop itself** (`DEMO_LOGIN=local`). From a phone, or through a tunnel, people sign in with a PIN or password. `DEMO_LOGIN=lan` offers it to devices on the same network as well; use that only on a network you control.
- With a password: staff emails are `admin@banka.demo`, `analyst@banka.demo`, `auditor@banka.demo`, `admin@bankb.demo`, `analyst@bankb.demo`. The password is `SEED_STAFF_PASSWORD` in `.env`.
- App users sign in with a phone number and a 6-digit app PIN. The seeded demo users have phones `9000000001` to `9000000005`; the PIN is `SEED_USER_PIN` in `.env`. Registering a new user in the app works and gives a simulated balance of ₹50,000.
- A session opened with a PIN locks when the app is reopened or left; the PIN opens it. Sessions lapse after 30 minutes without use (60 on the dashboard) and end 12 hours after sign-in.
- Set `DEMO_MODE=false` in `.env` to remove the one-click sign-in, the demo control page, the API documentation and the short timers. The service then refuses to start unless `REQUIRE_HTTPS=true` and the other settings in [11, section 1.9](11-security-risks-and-decisions.md#19-start-up-checks) are safe.

### 3.4 Tasks

| Command | Does |
|---|---|
| `python manage.py bootstrap` | Everything for a first run |
| `python manage.py api` | API and workers on port 8000 |
| `python manage.py dashboard` | Dashboard on port 3000 (`--dev` for hot reload) |
| `python manage.py app` | Expo dev server; add `--web` to open the browser build |
| `python manage.py test` | Unit tests |
| `python manage.py golden` | Golden scenarios |
| `python manage.py e2e` | Acceptance tests against a running API (`--only AT-3,AT-11`, `--fast`) |
| `python manage.py load --rps 100 --seconds 60` | Load test |
| `python manage.py demo-reset` | Restore the seeded demo state on a running API |
| `python manage.py preflight` | Everything a demo needs, checked in one go |
| `python manage.py security-check` | Configuration, the front door, and every endpoint called with the wrong credentials |
| `python manage.py i18n-check` / `i18n-apply <sheet>` | English and Hindi line up; write the review sheet; put corrections back |
| `python manage.py phone-check [--export]` | Is the payer app ready for a real phone |
| `python manage.py llm-check [--live]` | The case-summary providers |
| `python manage.py bus-check` / `redis-emulator` | Run the Redis event bus; a Redis look-alike on a TCP port |
| `python manage.py simulate` / `train` / `backfill` | The three stages of the bootstrap, separately |
| `python manage.py text` | Retrain the text classifier |
| `python manage.py export-openapi` | Write `contracts/openapi.yaml` |
| `python manage.py db` / `db-stop` | Start or stop the embedded PostgreSQL |

### 3.5 On a phone

Install Expo Go, put the phone and the laptop on the same network (your own hotspot, not venue Wi-Fi: the traffic is plain HTTP), run `python manage.py app` and scan the code. The app finds the API at the laptop's address on port 8000. Windows may ask to allow Python and Node through the firewall. Sign in with a demo phone number and the PIN in `.env`.

**This path has not been tested on a device.** `python manage.py phone-check --export` passes: the Android bundle builds and the API answers on the laptop's network address. The camera QR scan, the fingerprint prompt, the keystore and the spoken warning have only been exercised in a desktop browser, where they fall back to sample codes, a one-time code and browser storage. [13](13-phone-test.md) is the test to run on two phones.

### 3.6 Notes for this laptop

- **scikit-learn is pinned below 1.9.** Windows Smart App Control blocked a compiled file in 1.9.1. Version 1.8.0 loads.
- **`npm` is not on the path here.** The installs were done with `node D:\node_modules\npm\bin\npm-cli.js` and shims in `.cache\bin`. `manage.py dashboard` and `manage.py app` call Node directly and need no `npm`.
- **After an unclean shutdown** PostgreSQL recovers by itself on the next start. The code waits up to about 90 seconds for it.

## 4. Measured results

Everything below is on **synthetic data the team generated**. It shows the pipeline works end to end. It is not a claim about real-world accuracy.

### 4.1 Detection (held-out last 15 days: 19,498 payments, 742 scams)

| Measure | Target in 01 | Result | Met |
|---|---|---|---|
| Scams reaching Step-Up or Block | ≥ 85% | 99.87% | Yes |
| Scams from campaign variants never seen in training | ≥ 70% | 99.26% (135 scams) | Yes |
| Honest payments at Step-Up or Block | ≤ 1% | 0.60%; 0.14% leaving out payees with confirmed fraud | Yes |
| Honest payments at Block | ≤ 0.1% | 0.19%; 0.07% leaving out payees with confirmed fraud | Only with that exclusion |
| Honest payments shown a Warning | ≤ 5% | 1.00% | Yes |
| Every non-Safe decision carries a reason | 100% | 100% of 200 checked (AT-10), and every golden scenario | Yes |
| Scam-family attribution, top-1 | ≥ 80% | 74.8% overall; 87.4% on standard variants (section 4.2) | Not overall |
| Scam payments linked to their call or message | ≥ 95% | Not measured as a rate. Linking is checked in AT-3 and AT-11. | — |
| Reputation reflects a near-miss | within 5 s | 0.27 s | Yes |
| Guardian sees the request | within 3 s | 0.26 s | Yes |
| Message checker | ≥ 90% scam recall, ≤ 5% genuine flagged | 85% recall, 0% flagged (section 4.3) | Recall: no |
| Payments blocked because a dependency was down | 0 | 0 in AT-7 and golden G-D1 to G-D4 | Yes |
| Decision time | p95 ≤ 150 ms, p99 ≤ 250 ms at 200 a second | At 100 a second: p95 from 14 to 111 ms and p99 from 20 to 130 ms across four runs (section 4.4) | At 100 a second, with a margin that varied |
| Cost | ₹0 | ₹0: nothing hosted, no account created | Yes |

Also measured: scams reaching Block 98.38%; PR-AUC of the model output 0.997, of rules alone 0.844.

Read these with care:

- **They are near the ceiling because the simulator is easier than reality.** The same code that writes the scams also defines the features. Hard negatives and stripped-down scam variants were added until the model stopped being perfect, but a real bank's data would be far harder.
- **"Never seen in training" is weaker than it sounds.** The 135 such scams in the test window came from four families (fake refund, impersonation, KYC threat, mule transfer) whose unseen variants kept much of their story. No unseen digital-arrest or investment campaign fell in the window.
- **0.45% of honest payments go to a payee with confirmed fraud** and are meant to be stopped. That is why the false-positive budget is judged without them.
- **The 20-payer burst rule (R-04b) never fires in the simulated history.** The simulated world has under a thousand people, so no account collects twenty strangers in an hour. Its false-positive rate is not measured.
- **The model leans on signals the app reports about itself.** In AT-16 the model's output for the same payment moved from 0.002 to 0.44 when the UPI ID was pasted, the app was left four times and the payer lingered. Simulated victims behave that way and simulated honest payers do not, so the model learned it. Rules may only add to a score on such signals; the model has no such limit, and a real deployment would need one. In a controlled check on four payments, time on the pay screen alone did not change any tier.

### 4.2 Naming the scam

| Measure | Result |
|---|---|
| Flagged scams where a family was named | 94.2% |
| Named family is the campaign's family | 79.4% |
| Correct family, all flagged scams | 74.8% |
| … standard variants | 87.4% |
| … variants never seen in training (four families only; see 4.1) | 98.5% |
| … variants with their family signals removed | 21.3% |
| Flagged honest payments where a family was named | 27.1% (81 of 299), about 43 per 10,000 honest payments |

A family is named only when its own evidence is present ([06, section 11](06-risk-engine-and-ml.md#11-scam-taxonomy-and-attribution)). The 81 honest payments that had a family named were: on a long call with an unknown number (23), a note or name that reads like a scam (30), a payee linked to a reported account or with confirmed fraud (28).

The first target in 01 was 80% attribution accuracy. It is met on standard variants and not overall.

### 4.3 Text classifier

On a hand-written set of 20 scam and 20 genuine messages: scam recall 85%, class accuracy 90%, genuine messages flagged 0%. The target was 90% recall. The set was looked at once while broadening the training templates, so it is not fully independent.

### 4.4 Speed

Simulated payments, 99% honest and 1% scam, through the public endpoint; the API, PostgreSQL, the workers and the load generator all on one laptop (13th-generation Core i5, 15.6 GB).

| Run | Offered | Answered | Decision time inside the server, p50 / p95 / p99 | Request as the client saw it, p50 / p95 / p99 | Abandoned at 300 ms |
|---|---|---|---|---|---|
| 1 | 100 a second for 150 s | 15,000 of 15,000 | 7.1 / 14.4 / 19.5 ms | 14.3 / 28.2 / 46.1 ms | 0 |
| 2 | 100 a second for 150 s | 15,000 of 15,000 | 7.0 / 15.4 / 32.1 ms | 14.3 / 29.9 / 112.6 ms | 0 |
| 3 | 100 a second for 150 s | 15,000 of 15,000 | 8.5 / 107.4 / 129.6 ms | 17.2 / 355.3 / 395.3 ms | 0 |
| 4 | 100 a second for 60 s, straight after start-up | 6,000 of 6,000 | 10.0 / 111.0 / 129.0 ms | not recorded | 0 |
| 5 | 50 a second for 150 s | 7,500 of 7,500 | 6.3 / 10.4 / 13.0 ms | 11.5 / 18.3 / 23.2 ms | 0 |
| 6 | 100 a second for 60 s, after the security hardening | 6,000 of 6,000 | 9.8 / 19.7 / 25.7 ms | 21.4 / 48.8 / 60.5 ms | 0 |

The budget is 150 ms at p95 and 250 ms at p99. Every run is inside it; nothing was abandoned, degraded or lost.

**The margin varied from run to run on the same code**, from 14 ms to 111 ms at p95, and in the slower runs requests queued for up to 1.8 seconds before being answered. The model step, which is pure arithmetic, was slower in those runs too. That points at the laptop rather than the code, but the cause was not established. Read this as: about 100 payments a second is the ceiling of one process on this laptop, and 50 a second is comfortable.

By stage, from run 1:

| Stage (server, p95) | Time | Budget |
|---|---|---|
| Read state | 12.7 ms | 10 ms |
| Features | 0.7 ms | 26 ms |
| Rules | 0.04 ms | 2 ms |
| Models | 1.5 ms | 100 ms |
| Reasons and family, flagged payments only | 1.6 ms at p99 | 12 ms |
| **Decision, total** | **14.4 ms** | **150 ms** |

Reading state is over its own stage budget. It is one pipelined read of an in-process cache, and under load the wait is mostly other requests taking their turn on the same thread.

Getting here took several fixes that the load test exposed; they are listed in [10, section 4](10-testing-and-demo.md#4-load-and-failure-tests).

### 4.5 Other timings

| | Measured |
|---|---|
| Guardian alerted after a blocked payment | 0.26 s |
| Trust score falls after a walked-away payment | 0.27 s |
| Bank B protected after Bank A confirms fraud | 0.6 s |
| Complaint pack built | 0.1 s |
| Policy simulation over 11,000 decisions | 44 to 175 ms |
| Demo reset | 5 to 10 s |
| Bootstrap | about 10 min |

## 5. Differences from the plan

| Area | Plan | As built | Why |
|---|---|---|---|
| Runtime | Docker Compose, Redpanda | Laptop native (section 2) | Docker was not running on the build laptop; this needs nothing installed beyond Python and Node |
| Hosted copy | Vercel, Render, Neon, Upstash | Not built | Team decision: a laptop demo is enough for now |
| Rules | R-01 to R-15 | 21 rules, set `rules-0.6.0`: added R-04b, R-12c, R-16, R-17; R-12 and R-15 split in two | Found while tuning false positives ([06, section 2](06-risk-engine-and-ml.md#2-rule-engine)) |
| Model alone | Could reach any score | Capped at 0.94: it can hold a payment, never decline it | A hard stop should rest on evidence a person can check |
| Explanations | SHAP library | XGBoost's own per-feature contributions | Same values for tree models, no extra dependency in the service |
| Single-payment scoring | scikit-learn calls | Flat-array tree traversal, checked against scikit-learn at load | scikit-learn took about 11 ms per payment and far longer under load |
| Family naming | Closest prototype | Closest prototype **whose own evidence is present**, with wording that states only what was seen | A first payment of a large amount was being called a mule account |
| Step-Up question | Sent with the authentication | Sent first, alone | So a risky answer stops the payment before anyone is asked to authenticate |
| Degraded operation | Capped at Warning | Capped at Warning, except hard rules | A denylisted payee should be stopped even when the model is down |
| Overload | — | Past 48 checks in flight, a payment gets the safe default at once | Queueing makes every answer late |
| Graph "one step from a reported account" | Any payment edge | Only forwarding edges (out within an hour of money in, 0.5 to 1.2 times the amount) | Otherwise a mule's victims were marked as mules |
| Payer app navigation | expo-router, bottom tabs | A small stack navigator and a left icon rail | The team asked for navigation on the left; fewer dependencies |
| Payer app motion | Reanimated | React Native's built-in `Animated` | No native module needed in Expo Go |
| Dashboard motion | Framer Motion | CSS transitions | Enough for the feed and page changes |
| Spoken warning | Recorded clips, text-to-speech as fallback | Text-to-speech only | No recordings were made |
| Load test | 200 per second for 5 minutes | 100 per second for 150 seconds | About 100 a second is the ceiling of one process on this laptop; see section 4.4 |
| Decision logging | One write per payment | Batches of up to 200, flushed whenever the logger catches up and before any other kind of event is handled | Per-payment round trips were the slowest link at 100 a second |
| Graph job | In the worker | In a separate process started by the API, with a thread as fallback | Computing in a thread took the interpreter away from payments for about a second |
| Drift monitor | Reads stored feature records | PostgreSQL extracts the 15 values compared | Parsing 20,000 records stalled payments for about a second |
| Contract tests | A separate layer | Not written; the OpenAPI file is exported from the code | Time |

## 6. Known gaps

**Not verified**

- **Nothing has run on a physical phone.** Camera QR scan, the fingerprint prompt, the system keystore, the screen-capture block and the spoken warning have never run on a device. `phone-check` passes and [13](13-phone-test.md) is the test to do. The device-key enrolment and the fingerprint Step-Up are checked on the server side and in arithmetic only.
- **The Hindi text has not been reviewed by a native speaker**, including the 107 strings added in the second pass. `docs/hindi-review.csv` is the sheet to fill.
- **No language-model provider has been called.** The provider code is tested against a stand-in server. `python manage.py llm-check --live` makes a real call once a key or an Ollama URL is in `.env`.
- **A real Redis server** has not been used: the Redis option was run on an emulator. **Kafka** and an **external PostgreSQL** have not been run at all.
- The complaint-pack PDF was generated and its size checked, not read.
- Whether the browser or the phone has a Hindi voice for the spoken warning.
- The app lock's timers (a minute in the background, five minutes untouched) were not waited for; the lock was triggered by hand and by reloading.
- The dashboard's 15-minute idle sign-out and both apps' background token renewal were not waited for; the renewal endpoint is tested (AT-37).
- The dashboard's change-password dialog was not operated by hand; the endpoint behind it is tested (unit and `security-check`). The app's change-PIN form and "Sign out on every device" were.

**Known weaknesses**

- Text classifier recall is 85% against a 90% target.
- Detection figures are near the ceiling on easy data (section 4.1).
- The model has no limit on how far client-reported session signals move its output (section 4.1).
- Reasons shown to a guardian are worded for the payer ("the number you are on a call with").
- The complaint pack's timeline is in English whatever the user's language.
- About 100 payments a second is the ceiling on this laptop, and at that rate the decision time varied between runs from 14 to 111 ms at p95 for reasons not established. One process, one thread for requests and logging.
- A demo reset removes accounts registered through the app.
- Events queued in the process are lost if it stops; the `outbox` table that would prevent this is unused ([04, section 2.3](04-architecture.md#23-event-bus-abstraction)).
- Security: plain HTTP between phone and laptop, one-time codes shown in the app rather than sent by SMS, a fingerprint check made by the app rather than by the key, and the rest of [11, section 1](11-security-risks-and-decisions.md#known-gaps).
- Caller reports and the never-pay list depend on calls the app is told about. In the prototype those come from the demo control; a real app would need the phone's call log, which Expo Go cannot read.
- The look-up's reputation is one bank's view. What the other bank knows about a UPI ID appears only as "reported by customers of another bank", and only once that bank's signal has been shared (F-24).
- Two practice scripts have no Hindi version.
- Two unused items remain: the `motion` package in the dashboard, and `prometheus-client` in the requirements (the `/metrics` text is written by hand).

**Not built**

- The hosted copy (AT-29).
- A separate worker process. The workers run inside the API process; splitting them needs an entry point and a real-time layer that publishes through Redis.
- Contract tests.
- Recorded audio clips.
- A usability round with outside readers.
- Retraining from live decisions, analyst labels and near-misses: the trainer learns from simulated rows only.
- A dashboard view of caller reports for analysts, and a way to lower a number's level once raised.
- The heavier text model (DistilBERT), and the planned Docker, Kafka, Prometheus and Grafana set-up.

## 7. Repository

```
manage.py                    task runner
requirements.txt             Python dependencies
.env.example                 every setting, with comments
infra/postgres/001_schema.sql  the first build's 41 tables (505 lines)
infra/postgres/002_security_and_community.sql  four more tables and six columns; idempotent, applied at every start
infra/seed/                  rehearsal scripts; the message test set
packages/design-tokens/      tokens.json and the script that writes them into both apps
contracts/openapi.yaml       exported from the code
services/risk-api/
  app/main.py                application, start-up, error shape
  app/orchestrator.py        the hot path
  app/core/                  features, rules, scoring, decide, explain, reputation, state, graph, text
  app/guard.py               host check, body size, rate limit, security headers
  app/security.py, sessions.py, posture.py   tokens and rate limits; sign-out and revocation; start-up checks
  app/routers/               auth, risk, stepup, signals, payer, community, reports, analyst, ops, demo
  app/workers/               decision logging, reputation, guardian and hold timers, jobs, summaries
  app/services/packs.py      complaint pack
  rules/rules.yaml           the rule set
  i18n/en.json, hi.json      reasons, families, advice
  tests/                     test_core.py, test_security.py, test_bus_and_llm.py: 120 unit tests
ml/upi_ml/
  simulator.py  replay.py  train.py  text_train.py  offline.py
  pipeline.py  seed_db.py    simulate, train, register, backfill, load PostgreSQL
  golden.py  e2e.py  loadtest.py  analyze.py
  checks.py                  preflight, security-check, i18n-check, phone-check, llm-check, bus-check
ml/golden/scenarios.yaml     44 golden scenarios
ml/artifacts/                trained models (not committed)
apps/dashboard/              Next.js 16, React 19; 13 pages under src/app/(app)
apps/mobile/                 Expo SDK 57, React Native 0.86; App.tsx, src/screens, src/components
  src/secure.ts, hmac.ts, session.ts   keystore and device key; HMAC for the device proof; sign-out
  scripts/check-hmac.ts      the app's HMAC against Node's and the RFC vectors
docs/hindi-review.csv        every Hindi string beside its English, for a reviewer
```

About 13,800 lines of Python, 2,500 of dashboard code and 6,300 of app code.
