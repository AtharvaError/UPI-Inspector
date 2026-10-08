# 10 — Testing and Demo

[← Index](README.md)

This document describes the tests that exist in the repository and what they showed on the build laptop on 7 October 2026. Measured results are in [12, section 4](12-as-built.md#4-measured-results).

## 1. Test layers

| Layer | Covers | Command | Result |
|---|---|---|---|
| **Unit** | Each rule firing and not firing. Decay maths. Bands. Tier boundaries at exactly 0.30, 0.60, 0.85 and 0.95. Action selection. Reason rendering. Name matching. Entity extraction. Policy-simulation formulas. Family naming only on its own evidence. Fast single-payment scoring against scikit-learn. PIN and password rules, tokens (forged, expired, unsigned), where a request comes from, the request guard, start-up checks, payment clearance and its time limit, caller-report thresholds, the Redis bus, the case-summary providers against a stand-in server. | `python manage.py test` | 120 pass |
| **Golden scenarios** | 44 frozen situations with the tier, action and family they must produce: every rule, every family, the hard negatives, policy variants, degraded operation. They run through the same decision function the API calls. | `python manage.py golden` | 44 of 44 pass |
| **Model evaluation** | The metrics in [06, section 15](06-risk-engine-and-ml.md#15-training-and-evaluation), stored with the model version and shown on the Model health page. | `python manage.py train` | Gates pass; model promoted |
| **Acceptance (end to end)** | Each flow against a running API over HTTP: assess to outcome to reputation; message to flag to decline; guardian approve and deny; hold to re-assessment; roles; shadow mode; caller reports, look-up, never-pay list, trends; sessions, Step-Up limits, the front door. | `python manage.py e2e` | 33 of 33 pass |
| **Security sweep** | Every endpoint called with the wrong credentials, and the front-door probes. | `python manage.py security-check` | All pass (section 6) |
| **Load** | Simulated traffic at a fixed rate, 99% legitimate and 1% scam, reading the server's own latency histogram. | `python manage.py load --rps 100 --seconds 150` | Within budget at 100 a second in four runs; the margin varied a lot between runs |
| **Manual, in a browser** | Every payer-app screen and every dashboard page, driven by hand against the live API. | — | Done on the web build, including the new Safety check, app lock, caller banner and Security page |
| **Manual, on a phone** | Expo Go on Android: camera QR scan, fingerprint prompt, secure storage, spoken warning. | [13](13-phone-test.md) | **Not done.** The checklist is written and `phone-check` passes; no phone has run the app. |
| **Usability** | Five people who do not know the project read each tier screen. | — | **Not done** |

There is no separate contract-test layer. `python manage.py export-openapi` writes `contracts/openapi.yaml` from the running code (101 paths).

## 2. Acceptance tests

"Automated" means the test is in `ml/upi_ml/e2e.py` and passes. The suite resets the demo state before and after it runs.

| ID | Feature | Test | Pass condition | Status |
|---|---|---|---|---|
| AT-1 | F-01, F-09 | Repeat payment to a long-standing payee | `SAFE`; no reasons; mock PSP pays | Automated |
| AT-2 | F-03 | First-time, high-value honest payment, no call or message | `WARNING` at most | Automated |
| AT-3 | F-05, F-08 | Digital-arrest scenario | `BLOCK`; family `DIGITAL_ARREST`; a call reason; call linked on the dashboard timeline | Automated |
| AT-4 | F-08 | Fake-refund scenario | `STEP_UP` or `BLOCK`; family `FAKE_REFUND` | Automated |
| AT-5 | F-04, F-08 | Young UPI ID with a fan-in spike | Chip shows Caution before any amount; `STEP_UP` or `BLOCK`; family `MULE_TRANSFER`; reason `PAYEE_FAN_IN` | Automated |
| AT-6 | F-10, F-11 | Payer walks away from a flagged payment | Near-miss recorded; payee's trust score falls within 5 s; a second payer does not score lower | Automated. The scenario is now held, so the test cancels the hold. |
| AT-7 | F-01 | Cache unavailable, then model unavailable | Decisions returned, marked `degraded`, none above `WARNING` because of the outage | Automated, by fault injection (`/v1/demo/chaos`) |
| AT-8 | F-01 | Same `txn_id` twice; a malformed request | Identical response; HTTP 400 for the malformed one | Automated |
| AT-9 | F-01, F-15 | Load test | p95 ≤ 150 ms; p99 ≤ 250 ms | Run separately: within budget at 100 a second, p95 from 14 to 111 ms across four runs. See section 4. |
| AT-10 | F-07 | Flagged decisions | Every decision at Warning or above has at least one reason | Automated, over 200 stored decisions |
| AT-11 | F-16 | Paste a scam message naming a UPI ID, then pay it | `SCAM`; UPI ID extracted; `DECLINE` with `PAYEE_IN_SCAM_MESSAGE`; message linked; a second attempt is still declined | Automated |
| AT-12 | F-16 | Paste a genuine bank message | `FINE`; no flag | Automated, with AT-11 |
| AT-13 | F-17 | Protected payer blocked, guardian in the loop | Guardian's inbox shows the alert within 3 s | Automated, with AT-3 (0.26 s) |
| AT-14 | F-17 | Co-approval: guardian denies; then approves a second attempt | Deny: mock PSP refuses, `GUARDIAN_DENIED`. Approve: mock PSP pays once only. The payer sees only the status of the request. | Automated |
| AT-15 | F-17 | Payer removes the guardian; payer loosens the mode | `REMOVAL_PENDING` with a time; protection still applies; Undo restores it; a loosening is delayed | Automated |
| AT-16 | F-18 | Same payment typed and uncoached, then pasted with four app switches | Rule R-11 fires only on the second; the model's output and the session contribution both rise | Automated. In this scenario the final score sits on the same Warning floor both times; the test asserts on the model output (0.002 → 0.44). |
| AT-17 | F-19 | Shadow mode, digital-arrest scenario | Payer sees `SAFE`; stored decision is an unenforced `BLOCK`; no guardian request, no hold | Automated |
| AT-18 | F-19 | Policy lab | Same thresholds give the same numbers; lowering Step-Up catches no fewer scams; under 1 s; admin only; applied at once; audited | Automated (44 ms over 11,000 decisions) |
| AT-19 | F-20 | Mule chain | The first-layer mule is one step from a reported account; a victim who paid it has no graph flag | Automated |
| AT-20 | F-21 | Spoken warning in Hindi | Plays once at Step-Up, Hold and Decline; mute stops it; nothing at Warning | Manual. Triggered in the browser build; **not listened to on a phone.** |
| AT-21 | F-22 | Name claiming "SBI Refund Desk" on an individual's UPI ID | Chip shows the registered name and the mismatch | Automated, with AT-4 |
| AT-22 | F-23 | A held payment | Nothing is sent on the timer; confirming re-assesses; a second Block is a `DECLINE`; no second hold | Automated |
| AT-23 | F-24 | Fraud confirmed at Bank A; a Bank B payer pays the same UPI ID | Bank B flags it within 10 s; no UPI ID in the shared row | Automated (0.6 s) |
| AT-24 | F-25 | Complaint pack from a declined payment | PDF and JSON with the transaction, payee and timeline, under 5 s | Automated (0.1 s) |
| AT-25 | F-26 | Traffic with shifted amounts and hours | Drift alerts on the affected features | Automated |
| AT-26 | F-26 | Fairness report | Rates per group with a disparity ratio | Automated, with AT-25 |
| AT-27 | F-27 | Case summary | Appears within 30 s; names no one; never changes the decision; auditors cannot regenerate it | Automated, with the template provider. **No language-model provider was tested: no key was configured.** |
| AT-28 | F-28 | Rehearsal | Scripts listed, played and recorded; a Hindi user gets a Hindi script | Automated |
| AT-29 | G9 | Hosted copy | Every Must flow works on a hosted copy | **Not built.** The team chose a laptop demo. |
| AT-30 | F-09 | Step-Up | HTTP 425 before the pause ends; the safe answer alone does not release the payment; a wrong code fails; the risky answer declines and blocks the pair for 24 hours | Automated |
| AT-31 | F-30 | Roles | Admin, analyst and auditor can each do only what they should; five wrong passwords lock the account | Automated |
| AT-32 | F-30 | App users | Register; sign in with phone and PIN; lose access when deactivated | Automated |
| AT-33 | F-31 | A call from a known scam number; then two people report an unlisted number that called them | The call status shows level 3 and never the caller's identifier. One report does not flag a number, the same person twice counts once, a typed-in report from someone it never called does not count, two verified reports raise it to level 2, and its next call to a third person is flagged | Automated |
| AT-34 | F-32 | Look up a reported UPI ID, a days-old one, a verified shop, an unknown one and two phone numbers | Counts are ranges; no reporter is named; a wrong query is refused; staff and anonymous callers are refused; the 31st look-up in a minute gets `429` | Automated |
| AT-35 | F-33 | Put a shop on the never-pay list and pay it | `DECLINE` with `USER_BLOCKED` first; the risk tier is unchanged; the mock PSP refuses; another payer is unaffected; shadow mode still declines; the shop's reputation does not fall; unblocking is delayed and can be cancelled | Automated |
| AT-36 | F-34 | Trends | One to three patterns, each with a name, advice and a rounded count of at least three; in Hindi for a Hindi user | Automated |
| AT-37 | F-35 | Sessions | Easy PINs are refused. A refreshed token works. Sign-out ends that session and no other. A wrong PIN on the app lock is `403`, not a sign-out. A PIN change ends every other token and the old PIN. "Sign out everywhere" ends them all. A forged token and an `"alg": "none"` token are refused. All of it is in the audit log. | Automated |
| AT-38 | F-35 | Step-Up and payment | One person cannot verify, pay or report on another's payment, or be handed their decision. The app cannot report a completed payment. No code before the pause ends. A bare "fingerprint matched" is refused; a wrong device proof counts as an attempt; a correct one passes. A decision pays once. Three wrong codes lock the challenge and the PSP refuses. | Automated |
| AT-39 | F-35 | The front door | Security headers on every response; an unknown Host is refused and a private address is not; a foreign web page gets no CORS permission and the dashboard does; a 100 KB request is refused; demo sign-in and the demo account list are refused through a proxy; the Security page lists the checks and refuses an analyst | Automated |
| AT-40 | F-33 | The never-pay wait | The block is still in force just before its time and gone just after; the payee can then be paid | Automated (a 60-second wait in demo mode; skipped by `--fast`) |

**Order matters inside the suite.** A call from a flagged number affects the next ten minutes for that payer, so the guardian tests run before the scenarios that stage such calls.

## 3. Golden scenarios

All 44 are in one file, `ml/golden/scenarios.yaml`. Each builds its own state in a fresh in-process cache through the real feature store, computes features with the real code, and decides with the function the API calls.

```yaml
- id: G-F1
  title: Digital arrest - long call from a flagged number, pasted ID, most of the balance
  needs_model: true
  payee: { vpa: cyber.cell.verify@bankb, name: Imran Shaikh, age_days: 3, recv: 2 }
  call: { active: true, risk: 3, known: false, started_min_ago: 25 }
  request: { amount: 180000, entry: pasted, app_switches: 4, dwell: 140, claimed: Cyber Cell Verification }
  expect: { tier: BLOCK, action: HOLD, family: DIGITAL_ARREST }
```

| Group | Scenarios |
|---|---|
| Rules | A firing case for each rule and a near-miss case for most (`G-R01` to `G-R17`) |
| Families | One per family (`G-F1` to `G-F6`) |
| Hard negatives | Eight honest payments that look risky (`G-N1` to `G-N8`); expected `SAFE` or `WARNING` |
| Policy | `block_mode = decline`; re-assessment after a hold; stricter thresholds (`G-P1` to `G-P3`) |
| Degraded | Model unavailable and cache unavailable, each with and without a denylisted payee (`G-D1` to `G-D4`) |

`python manage.py golden` exits non-zero if any fails. Run it after every change to rules or models.

## 4. Load and failure tests

### Load

`python manage.py load --rps 100 --seconds 150` replays simulated payments through `POST /v1/risk/assess` at a fixed rate and prints the server's own latency by stage beside what the client saw. It writes real decisions, so run `python manage.py demo-reset` afterwards.

The load generator is deliberately plain: 32 keep-alive connections, opened before the clock starts, writing pre-encoded requests. It runs on the same laptop as the API, PostgreSQL and the workers.

Final build, 7 October 2026:

| Run | Offered | Answered | Decision time inside the server, p50 / p95 / p99 | Request as the client saw it, p50 / p95 / p99 | Abandoned at 300 ms |
|---|---|---|---|---|---|
| 1 | 100 a second for 150 s | 15,000 of 15,000 | 7.1 / 14.4 / 19.5 ms | 14.3 / 28.2 / 46.1 ms | 0 |
| 2 | 100 a second for 150 s | 15,000 of 15,000 | 7.0 / 15.4 / 32.1 ms | 14.3 / 29.9 / 112.6 ms | 0 |
| 3 | 100 a second for 150 s | 15,000 of 15,000 | 8.5 / 107.4 / 129.6 ms | 17.2 / 355.3 / 395.3 ms | 0 |
| 4 | 100 a second for 60 s, straight after start-up | 6,000 of 6,000 | 10.0 / 111.0 / 129.0 ms | not recorded | 0 |
| 5 | 50 a second for 150 s | 7,500 of 7,500 | 6.3 / 10.4 / 13.0 ms | 11.5 / 18.3 / 23.2 ms | 0 |
| 6 | 100 a second for 60 s, **after the security hardening** | 6,000 of 6,000 | 9.8 / 19.7 / 25.7 ms | 21.4 / 48.8 / 60.5 ms | 0 |

- **The hardening did not cost the budget.** Run 6 has the request guard, the rate limiter and the extra session checks in the path. It sits inside the range of the earlier runs; with one run it cannot be told apart from the laptop's own variation.
- **AT-9 passes at 100 a second in every run:** the budget is p95 ≤ 150 ms and p99 ≤ 250 ms, nothing was abandoned and nothing was degraded.
- **The margin is not steady.** Two runs sat at 14 to 15 ms; two sat near 110 ms, with requests queueing for up to 1.8 seconds on the client side. Same code, minutes apart. In the slower runs the model step, which is pure arithmetic, was also slower (0.8 ms against 0.7 ms at the median, 2.1 ms against 1.4 ms at p95). That points at the laptop rather than the code, but the cause was not established.
- **About 100 a second is this laptop's ceiling for one process.** The plan's 200 a second was not attempted on the final build, and its 5-minute duration was not run.
- **At 50 a second there is real headroom.**

What the load test found on the way, each of which is fixed:

| Symptom | Cause | Fix |
|---|---|---|
| 245 of 3,000 answered; decision took 137 ms | scikit-learn's per-call overhead for one row | Flat-array tree traversal ([06, section 3](06-risk-engine-and-ml.md#3-models)) |
| The logger fell thousands of events behind; 1.4% of checks abandoned | Two database round trips per payment, and waiting on a dashboard socket per payment | Decisions written in batches of up to 200; live-feed notices sent without waiting, and skipped for Safe payments when behind |
| About 1% of requests a second late | The graph job computing in a thread, taking the interpreter from the request thread | The graph is computed in a separate process |
| A one-second stall 45 seconds after start-up | The drift monitor parsing 20,000 stored feature records | The database extracts the 15 values it compares; the arithmetic runs off the request thread |
| A two-second "stall" at the start of every run | The load generator connecting to `localhost` over IPv6 first | Connections are opened before timing starts. Not a server fault. |
| Case summaries minutes late after a burst | A fixed pause after every summary, meant for free-tier rate limits | The pause applies only after a call to an outside model |

### Failure

The acceptance suite injects two faults through `/v1/demo/chaos` (AT-7): the cache unavailable and the model unavailable. In both, decisions are still returned, marked `degraded`, and nothing rises above a Warning because of the outage.

The other faults in the original plan (slow PostgreSQL, stopped bus, stopped worker, blocked language-model providers, dropped dashboard socket) were **not run**. The laptop build keeps the cache, the bus and the workers inside the API process, so stopping them separately is not possible in that mode.

## 5. Demo

### 5.1 Set-up on one laptop

| Window | Address | Signed in as |
|---|---|---|
| Dashboard | `http://localhost:3000` | Asha Kulkarni, admin of Bank A (one click on the sign-in page) |
| Phone 1 (payer) | `http://localhost:8081` | Meera Joshi, Hindi; switch user from Settings as the script needs |
| Phone 2 (guardian) | `http://localhost:8081/?slot=2` | Arjun Joshi, English |

Two browser windows on the same address share storage and would be the same user. `?slot=2` gives the second window its own session. On a wide window the app draws itself inside a phone-sized frame.

```
python manage.py api          # terminal 1
python manage.py dashboard    # terminal 2
python manage.py app --web    # terminal 3
python manage.py demo-reset   # immediately before presenting
```

Real phones work the same way with Expo Go on the same network: run `python manage.py app` and scan the code. A phone signs in with a phone number and PIN, because the one-tap demo accounts are offered only on the laptop itself (`DEMO_LOGIN=local`). That path was not tested on a device: [13](13-phone-test.md) is the test.

`python manage.py preflight` checks all of this in one go.

### 5.2 Script (about 7 minutes)

Each outcome below was checked against the running system on 7 October 2026.

| # | On the phone | On the dashboard | Say |
|---|---|---|---|
| 1 | Meera taps **Sharma Properties** in recent payees, enters ₹12,000. Straight to the PIN, then **Payment sent**. | A Safe row with its latency in milliseconds. | "Most payments look like this. The user never sees us." |
| 2 | In **Demo control**, pick Meera and stage **Digital arrest**. Her phone rings: accept. With the call bar showing, tap Pay, enter `cyber.cell.verify@bankb`, name `Cyber Cell Verification`: the chip shows the registered name **Imran Shaikh** and "the name you were given does not match". Enter ₹1,80,000. **Payment paused**, in Hindi, read aloud. Arjun's window shows the alert. | A Held row. Open it: the call and the payment on one timeline, the reasons shown to the payer, the case summary. Open **Network**: the payee forwards to a reported account. | "A flagged number has kept her on a call for 25 minutes, and the name she was given is not the account's name. It names the scam, says it aloud in her language and tells her son. Nothing is sent when the timer ends: she is asked again, and it is checked again." |
| 3 | After a minute the hold is ready. **Held payments** → **Yes, check and send**. The second check stops it: **Payment stopped**. Tap **Create complaint pack**. | A Declined row. | "A second Block is never held twice. And she leaves with the evidence for a 1930 complaint, not just a refusal." |
| 4 | Settings → Switch user → **Kavita**. Stage **Mule transfer** for Kavita. Tap Pay, enter `booking.advance@bankb`: the chip is amber before any amount. Enter ₹12,000. **Please pause**: the countdown, then "Do you personally know the person you are paying?" Answer **No**: stopped. | **Near-misses** shows it. The payee's trust score has dropped. | "An OTP would not have stopped this; she would have typed it in. A pause and one honest question did. And her answer now protects the next payer." |
| 5 | Switch user → **Rohan**. Stage **Fake refund**. A message arrives: tap **Check this message**. Verdict: looks like a scam, with the phrases highlighted. Under the result tap **Demo: tap the payment link in this message**, then **Pay ₹4,999**: **Payment stopped**, "This UPI ID appeared in a message you flagged as a scam". | Open the Declined row: the message and the payment on one timeline. | "The deck promised call and SMS. This is the SMS half: he told us what the scammer wants him to do, so we refuse it." |
| 6 | Switch user → **Sunita** (Bank B). | On the Declined row from step 5 press **Confirm fraud**. Switch the dashboard to **Bank B**. Sunita pays the same UPI ID: reason "reported by customers of another bank". | "Bank A's customers warned Bank B's customer. Only a keyed hash and a rating crossed. No customer data." |
| 7 | Switch user → **Meera**. Pay `rohan@banka` ₹25,000: a Warning that says only that the payment looks unusual. **Continue anyway**: "Waiting for Arjun Joshi to approve." Arjun approves in his window; Meera's phone moves to the PIN. | — | "For a large first payment, a person she trusts gets a say. He sees this one payment and nothing else." |
| 8 | — | **Policy lab**: switch to Shadow, move the Step-Up slider. Scams caught, payers interrupted and rupees protected all move. | "This is how a bank adopts it: watch first, pick the trade-off from evidence, then switch it on." |
| 9 | — | **Model health**, then **System health**. | "A decision takes a few milliseconds on this laptop against a 150 millisecond budget. The detection figures are on synthetic data we generated: they show the pipeline works, not real-world accuracy." |

What each staged scenario produces:

| Scenario | Payment without checking the message | After checking the message |
|---|---|---|
| Digital arrest | Block, held; family Digital arrest; guardian alerted | — |
| Fake refund | Block, held; family Fake refund | Declined: payee is in a flagged message |
| Mule transfer | Step-Up; family Mule transfer; chip Caution | — |
| Impersonation | Block, held; family Impersonation | Declined |
| KYC threat | Block, held; family KYC or bill threat | Declined |
| Investment scam | Block, held; family Investment or task scam | Declined |

### 5.3 Three-minute version

Steps 1, 2, 4 and 8.

### 5.4 Which features each step shows

| Step | Features |
|---|---|
| 1 | F-01, F-09, rule R-09 |
| 2 | F-05, F-08, F-18, F-21, F-22, F-23, F-17, F-20, F-27, F-29 |
| 3 | F-23, F-25 |
| 4 | F-04, F-09, F-10, F-11 |
| 5 | F-16, F-06, F-13 |
| 6 | F-24 |
| 7 | F-17 |
| 8 | F-19 |
| 9 | F-15, F-12, F-26 |

Not in the script, available if asked: rehearsal (F-28), sign-in and roles (F-30, on the **Users and roles** page), drift and fairness (F-26, on **Model health**).

**Two optional additions (about a minute each)**, both checked against the running system:

| # | On the phone | On the dashboard | Say |
|---|---|---|---|
| 2a | In step 2, before accepting the call, point at the red box on the incoming-call screen; after accepting, at the banner; tap **Report this caller**. | — | "Other people have already reported this number, so she is warned before she opens a payment. And her report warns the next person it calls." |
| 4a | After step 4, on the stopped screen tap **Never pay this UPI ID**. Then rail → **Safety check**: look up `91111 11103`, then the **Never pay** and **Trends** tabs. | **Security** page: the configuration checks and the last day's sign-in events. | "She can decide in a calm moment that an account must never be paid; taking it off the list takes a day. Because this is about payments, every control is listed here with its state, and a script calls every endpoint with the wrong credentials." |

### 5.5 Before the demo

- [ ] `python manage.py preflight` passes.
- [ ] `python manage.py golden` passes, so each step lands on its tier. Re-check after any retrain.
- [ ] `python manage.py demo-reset` run. It restores balances, reputation, guardians, thresholds, each demo user's language and PIN, and clears caller reports and never-pay lists.
- [ ] Policy mode is Enforce for Bank A and Bank B.
- [ ] Laptop volume up; voice warnings on in Settings. Check that the browser has a Hindi voice; if not, the warning is silent in Hindi and the text carries it.
- [ ] Both phone windows open and signed in. The dashboard shows "Live".
- [ ] A recording of a full run is on the laptop.

### 5.6 If something fails

| Failure | Do |
|---|---|
| A scenario lands on the wrong tier | `python manage.py demo-reset`, then stage it again. A call from a flagged number affects that payer's payments for ten minutes; a reset clears it. |
| The phone window shows "Cannot reach the server" | Check terminal 1. Restart with `python manage.py api`; the app reconnects. |
| A window is back at the sign-in screen saying the session ended | A session lapses after 30 minutes without use (60 on the dashboard). Sign in again: one tap on the laptop. |
| "Too many requests" | A rate limit. `python manage.py demo-reset` clears the counters. |
| The database will not start | Wait: after an unclean shutdown PostgreSQL recovers by itself, and the API retries for about 90 seconds. |
| No sound | Point at the on-screen text; say what would be heard. |
| Everything fails | Play the recording and narrate. |

## 6. Checks

Scripts that can be run at any time. Each prints what it checked and how it came out, exits non-zero on a failure, and says plainly what it could not establish. None changes demo data. Results below are from 7 October 2026.

| Command | What it does | Result |
|---|---|---|
| `python manage.py preflight` | Everything a demo needs: `.env` and secrets, the models, PostgreSQL and the demo accounts, the tables added since the first build, the API, whether the dashboard build is older than its source, the app's dependencies, unsafe settings, and the addresses to open | All pass. It caught a dashboard build that was older than its source. |
| `python manage.py security-check` | The configuration checks the service runs at start-up; then, against the running API: security headers, Host check, CORS, body size, forged tokens, demo sign-in through a proxy; then **every endpoint called with the wrong credentials** | 10 configuration checks in place, 3 demo settings noted. 11 front-door probes pass. 99 endpoints refuse a caller with no credentials; 42 staff endpoints refuse an app user; 21 admin endpoints refuse an analyst and an auditor; 40 app-user endpoints refuse staff; an auditor can change nothing; a signed-out token stops at once. |
| `python manage.py i18n-check` | English and Hindi have the same keys and the same `{placeholders}`, in the app, the server catalogue and the practice scripts; no Hindi string is left in English. Writes `docs/hindi-review.csv` for a reviewer. | 403 app keys, 104 server strings and one practice script line up. Two practice scripts have no Hindi version. **It cannot judge wording**: see below. |
| `python manage.py i18n-apply docs/hindi-review.csv` | Puts a reviewer's corrections from the sheet back into the app and the server catalogue, refusing any that change a placeholder | Tried on a copy of the sheet: two corrections applied, and one that dropped a placeholder refused |
| `python manage.py phone-check [--export]` | What can be checked about the phone app from a laptop: native modules installed, type-check, the device-proof arithmetic against the server's, the Android bundle builds, the API answers on the laptop's network address | All pass; the Android bundle is 9.7 MB and builds in about 30 seconds. **Nothing has run on a phone**: [13](13-phone-test.md) is the test to do. |
| `python manage.py llm-check [--live]` | Which case-summary providers are configured; whether an Ollama server is running; request format, reply parsing, the fact check and the fallback order against a stand-in server; with `--live`, one real call per configured provider | The stand-in tests pass. **No provider is configured, so no real model has been called.** |
| `python manage.py bus-check [--url redis://…]` | Runs the Redis Streams bus: 500 events to two consumer groups, each once and in order, all acknowledged | Passes on the in-process emulator. |
| `python manage.py redis-emulator` | A Redis look-alike on a TCP port, to try `REDIS_URL=redis://127.0.0.1:6390/0` without installing Redis | Used for the run below |

### The Redis option

`EVENT_BUS=redis` and a `redis://` cache had been written and never run. They have now been run as far as this laptop allows:

| Run | Result |
|---|---|
| Unit tests of the bus on the in-process emulator: delivery once per group and in order, a failing handler, recovery after the cache is emptied, no busy loop when idle | 3 of 3 pass |
| The whole acceptance suite with `EVENT_BUS=redis` on the in-process emulator | 33 of 33 pass; no errors in the log; 50 payments a second for 30 s held, decision p95 16 ms |
| The whole acceptance suite with `REDIS_URL=redis://127.0.0.1:6390/0` (the TCP emulator, through the real Redis client) and `EVENT_BUS=redis` | 33 of 33 pass; no errors in the log; cache rebuilt at start-up in 9.8 s; 30 payments a second held, decision p95 20 ms |

Running it found and fixed three faults: the consumer loop would spin at full speed against a server that does not block on an empty read; emptying the cache at start-up deleted the event streams and their consumer groups, after which every read failed for good; and `drain` and `backlog` were placeholders.

**What this does not show.** The emulator is not Redis. It says nothing about speed on a real server, about blocking reads, about a second API process sharing the cache, or about failover. The Kafka bus has still never been run: there is no broker on this laptop.

### The Hindi text

The Hindi in the app, in the reasons and in the scam-family messages was written by a non-native writer. On 7 October 107 new strings were added in the same style, and the automatic checks above pass; the earlier text was not read through again. **None of it has been reviewed by a native speaker.** `docs/hindi-review.csv` lists all 517 strings beside their English, with columns for "OK?" and "Correction"; `i18n-apply` puts the corrections back.

