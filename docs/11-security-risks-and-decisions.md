# 11 — Security, Risks and Decisions

[← Index](README.md)

## 1. Security in the prototype

The prototype handles only synthetic data. Because it is about payments, it was hardened on 7 October 2026 beyond what a demo needs: the aim is that nothing here would have to be thrown away for a pilot, and that every weakness left is written down. This section says what the build does, not what was planned. Each control has a test; the test is named in brackets ([10, section 2](10-testing-and-demo.md#2-acceptance-tests) for the AT numbers).

### 1.1 Signing in

| Control | What the build does |
|---|---|
| Who | Payers: phone number and a 6-digit app PIN, or register. Staff: email and password. Services (a PSP): an API key, compared in constant time. |
| PIN rules | Refused: one digit repeated, a run such as 123456 or 654321, a repeated or mirrored pattern, the commonest PINs, and any part of the person's own phone number. [unit, AT-37] |
| Password rules (staff) | At least 10 characters, letters with digits or symbols, not a common password, not built from the email address. [unit, AT-31] |
| Storage | Argon2id hashes. Hashing runs off the event loop, so a sign-in never delays a payment. |
| Lockout | Five wrong attempts in a row lock the account: 5 minutes the first time, then twice as long each time, up to a day. A correct PIN during a lock is refused too. The counter is updated in one SQL statement, so simultaneous guesses cannot each read the same count. [AT-31] |
| Throttling | 20 sign-in attempts a minute per address and 15 per account in five minutes, on top of the lockout; 10 registrations per address in ten minutes. |
| No account enumeration at sign-in | An unknown number and a wrong PIN get the same answer and take the same time (a dummy hash is verified). A deactivated account is revealed only to someone who has proved they know the PIN. Registration still says when a number is taken: see the gaps. |
| Passwordless demo sign-in | Only in demo mode, and only from where `DEMO_LOGIN` says. The default is `local`: the laptop itself. A request that arrived through a proxy or a tunnel is never local, whatever address it shows. `lan` extends it to private addresses, `any` to everyone, `off` removes it. [unit, AT-39] |

### 1.2 Sessions

| Control | What the build does |
|---|---|
| Tokens | Signed (HS256), with issuer, session id, the account's token generation and how the session was opened. App tokens last 30 minutes, dashboard tokens 60. Tokens signed with another key, expired, with a missing claim or with `"alg": "none"` are refused. [unit, AT-37] |
| Renewal | While someone is using the app or the dashboard, the token is renewed in the background (`POST /v1/auth/refresh`). Background polling does not count as use. An idle session therefore lapses by itself, and every session ends 12 hours after sign-in whatever happens. |
| Sign-out | Revokes the session on the server at once, for REST and for the live socket. "Sign out on every device" and a PIN or password change raise the account's token generation, which refuses every token issued before. An admin changing a role or resetting a password does the same to that account. Revocations are written to PostgreSQL and survive a restart. [AT-37] |
| App lock | A session opened with a PIN locks when the app is reopened, has been in the background for a minute, or has gone five minutes untouched. Unlocking needs the PIN, checked by the server and counted like a sign-in; five wrong PINs lock the account and end the session. On an enrolled phone a fingerprint also unlocks. Demo sessions, which never had a PIN, do not lock. |
| Dashboard | The session is kept per browser tab and is gone when the tab closes. After 15 minutes without a key press or a click the dashboard signs itself out. Staff can change their own password. |
| On the device | On a phone the token and the device key are in the system keystore (`expo-secure-store`); in a browser the token is in `sessionStorage`. |
| Sockets | Opened with the same token and held to the same checks. Closed when the session is signed out, the credentials change, the account is deactivated or the session reaches its end. Allowed origins are the same as for REST. |

### 1.3 Step-Up authentication

A Step-Up is a pause, a question and then proof that the payer is the account holder. All three are enforced by the server.

| Control | What the build does |
|---|---|
| One-time code | Six digits from a secure random source, valid five minutes, usable once. Stored as a keyed hash bound to its challenge, so reading the cache does not reveal a usable code. Compared in constant time. No code is sent before the pause has ended. At most three codes per challenge, 20 seconds apart, and 12 per person in ten minutes. [AT-30, AT-38] |
| Wrong attempts | Three wrong codes or proofs close the challenge for good. The payment has to be started again and assessed again. [AT-38] |
| Fingerprint or face | The app saying "the fingerprint matched" is **not** accepted. When a phone signs in with the PIN it makes a random 256-bit key, keeps it in the system keystore and registers it in that same request. A Step-Up confirmed by fingerprint must carry `HMAC-SHA256(device key, challenge id)`. The server recomputes it. A stolen token alone therefore cannot pass a Step-Up, and a proof cannot be replayed on another challenge. Enrolling a device later needs the PIN. [unit, AT-38; the app's HMAC is checked against Node's and the RFC 4231 vectors by `scripts/check-hmac.ts`] |
| What that does and does not prove | It proves the request comes from the enrolled phone. The fingerprint check itself is still made by the app: the key is not one that only a fingerprint can unlock. That needs a development build ([13, section 3](13-phone-test.md#3-what-cannot-be-tested-in-expo-go)). |

### 1.4 Payment integrity

| Control | What the build does |
|---|---|
| What is paid is what was checked | The mock PSP takes the amount and the payee from the decision, never from the pay request. |
| One payment per decision | A lock taken with `SET NX` makes a second, simultaneous pay request for the same decision fail. The balance is reduced and checked in one step, so two payments cannot both spend the same money. [AT-38] |
| A decision goes stale | It clears a payment for 10 minutes (`CLEARANCE_TTL_S`) counted from the decision or from the last approval it needed (Step-Up passed, guardian approved). After that the PSP is told to refuse and the app asks the payer to start again. [unit] |
| Only the PSP says money moved | The app may report that the payer cancelled or went ahead. It may not report a completed payment; before this fix a modified app could have marked its own held payment as paid and inflated a payee's history. [AT-38] |
| Ownership | A challenge, a decision, a hold, a pack, a block list and a guardian link can be touched only by their owner. The idempotency key is the payer and the `txn_id` together, so nobody can be handed another person's cached decision. [AT-38, security-check] |
| Bounded client signals | Session signals from the app (app switches, time on screen, edits) are range-checked. They still come from a device the payer controls: see the gaps. |

### 1.5 The front door

Every HTTP request passes `app/guard.py` first.

| Control | What the build does |
|---|---|
| Host check | The API answers only to this machine, private addresses, the host in `PUBLIC_BASE_URL` and `ALLOWED_HOSTS`. This stops a web page that points its own name at 127.0.0.1 (DNS rebinding) from talking to a laptop's API as if it were local. [unit, AT-39] |
| CORS | `CORS_ORIGINS=local` (the default): only pages served from this machine or a private address. A list of origins or `*` can be set; `*` stops the service from starting outside demo mode. [unit, AT-39] |
| Headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, a deny-all `Content-Security-Policy` on API responses, `Cache-Control: no-store`, and HSTS when the request came over HTTPS. The server software is not named. The dashboard sends its own set, with a policy that lets its pages talk only to themselves and the API. [unit, AT-39] |
| Size | A request body over 64 KB is refused, as is a chunked body that does not declare its length. |
| Rate limits | 1,200 requests a minute per address (service keys exempt), and per person: 120 assessments and 30 payments a minute, 240 payee look-ups a minute, 30 safety look-ups a minute and 300 a day, 10 fraud reports and 10 caller reports a day, 10 complaint packs an hour, 20 message checks a minute. A limiter that cannot reach the cache lets the request through. [AT-34] |
| HTTPS | `REQUIRE_HTTPS=true` refuses plain HTTP from anywhere but this machine. Whether a request came over HTTPS is taken from the server, which believes `X-Forwarded-Proto` only from a proxy it trusts; a client cannot claim it in a header. Off in the demo, where there is no certificate. [unit] |
| Documentation and metrics | `/docs` and the OpenAPI file exist only in demo mode. `/metrics` answers only to the local network or a service key. |
| Overload | Past 48 assessments in flight, a payment gets the safe default at once. |

### 1.6 Abuse of the community features

Features that let one person's report affect another person's payment are an obvious target.

| Risk | What stops it |
|---|---|
| Branding a number a scam out of spite | A report counts as verified only if the number called the reporter, or was in a message they checked that was flagged. Two verified reporters make a number "likely" (level 2); five make it "known" (level 3, where rule R-02 applies). Typed-in reports from people it never called count only towards the lowest level, and that takes three people. One report per person per number; ten a day. The app never holds the number, only "the caller of my last call". [unit, AT-33] |
| Running down a payee's reputation alone | One payer can lower one payee's reputation through abandoned payments at most once a day (before this fix, every abandoned attempt counted). A fraud report counts only from someone who paid or tried to pay that UPI ID, once per reporter, five a day; one open report per person per UPI ID. Reports alone cannot move a payee past Caution. |
| Using the look-up to learn who reported whom | Counts are given as ranges (none, 1 or 2, 3 to 9, 10 or more). A scam pattern is named only when at least three different people met it. Nothing about a reporter is returned. Look-ups are rate-limited against harvesting. [AT-34] |
| Being talked into unblocking | Taking a UPI ID off the never-pay list takes 24 hours, like removing a guardian. A block does not touch the payee's reputation. [AT-35, AT-40] |

### 1.7 Data and secrets

| Item | Handling |
|---|---|
| Secrets | In `.env`, generated by `python manage.py setup`, git-ignored. Placeholders or a token key under 32 characters stop the service outside demo mode. |
| Phone numbers | Never stored: a keyed hash and the last four digits. Caller numbers are keyed hashes too. |
| PINs and passwords | Argon2id. The digits typed on the mock UPI PIN pad are never sent: a placeholder of the same length is. |
| Device keys | Stored as they are, because the server needs them to check a proof. A leak of that table would let an attacker forge proofs, though still not without a valid token. A pilot would use a key pair, with only the public half on the server. |
| Tenant isolation | Enforced in the API layer on every staff endpoint, now including complaint packs. There is no row-level security in the database. |
| Input | Every body is validated against a schema with length limits. Rule expressions are parsed against a whitelist. Pasted messages are treated as data and never rendered as HTML. |

### 1.8 Audit and visibility

The append-only audit log now also records: every sign-in and failed sign-in with the address it came from, lockouts, sign-outs, PIN and password changes, device enrolments, wrong PINs on the app lock, caller reports and never-pay changes. A demo reset does not clear it. The dashboard's **Security** page (admin and auditor) shows the configuration checks, the last 24 hours of these events, locked accounts and revoked sessions.

`python manage.py security-check` prints the configuration, probes the front door, and then calls **every endpoint with the wrong credentials**: none, an app user on staff endpoints, an analyst and an auditor on admin endpoints, staff on app-user endpoints. On 7 October 2026: 99 endpoints refuse a caller with no credentials, 42 staff endpoints refuse an app user, 21 admin endpoints refuse an analyst and an auditor, 40 app-user endpoints refuse staff. A new endpoint added without an access check would fail this.

### 1.9 Start-up checks

`app/posture.py` holds one list of checks, used when the service starts, by the Security page and by `security-check`. In demo mode an unsafe setting is a warning in the log. Outside demo mode the service **refuses to start** with: placeholder or short secrets, a short service key, `CORS_ORIGINS=*`, `TRUST_CLIENT_BIOMETRIC=true`, or `REQUIRE_HTTPS=false`.

### Known gaps

Each would have to be closed before real data. None is hidden by the tests.

- **Plain HTTP.** On the laptop itself this is not exposed. Between a phone and the laptop, a PIN or a token crosses the network in the clear: use a hotspot you control. A pilot needs TLS everywhere; the switch (`REQUIRE_HTTPS`) and the HSTS header are in place, the certificate is not.
- **One-time codes are not out of band.** There is no SMS gateway among the free services, so the code is shown inside the app, and in demo mode also returned by the API. Whoever holds the session can read it. Outside a demo it must travel by SMS.
- **Registration does not prove the phone number is yours**, for the same reason, and it says when a number is already registered.
- **The fingerprint is checked by the app**, not by a key that only a fingerprint can unlock (1.3). Nothing detects a rooted phone or a modified copy of the app.
- **Session signals are trusted from the client** within their ranges. Rules may only add to a score on them, but the model has no such limit ([12, section 4.1](12-as-built.md#41-detection-held-out-last-15-days-19498-payments-742-scams)).
- **An attacker can lock someone out** by guessing their PIN five times. The lock grows with each repeat. A real app binds the account to a device so that a stranger cannot try at all.
- **Device keys are stored unencrypted** on the server (1.7).
- **Rate limits are per process and per address.** Behind a shared address (a college network) the registration and sign-in limits apply to everyone together; with more than one API process they need the shared Redis.
- **The dashboard's content policy allows inline scripts**, because the framework writes them; it limits where a page may send data but would not stop every injected script.
- No tenant row-level security in the database; isolation is in the API layer.
- Python dependencies have lower bounds, not exact pins. There is no repository, so no CI, no secret scanner and no dependency alerts.
- No web application firewall, no intrusion detection, no key rotation, and no penetration test by anyone but the author.

## 2. Privacy

| Principle | In the prototype |
|---|---|
| No real personal data | All people, phone numbers, UPI IDs and messages are fictional. Handles `@banka` and `@bankb` do not exist. |
| Phone numbers | Stored only as keyed hashes. |
| Calls | Metadata only: who was called, a risk level for the caller, start and end. Never audio or content. |
| Messages | Only what the user pastes. The inbox is never read. |
| Session signals | Counts and timings from our own app. No screen content, no keystrokes, no names of other apps. |
| Guardians | Opt-in by the payer; accepted by the guardian; visible to both; removable with a delay. |
| Consortium | A keyed hash of the UPI ID and aggregate counters. No payer data, no transaction rows. |
| Language models | Would receive only synthetic, tokenised decision data. None was called during the build. |
| Minimisation | Every feature in the catalogue is there because the model or a rule uses it. |

## 3. What a pilot would have to add

Not legal advice. A pilot needs review by the partner bank's compliance team.

- **Digital Personal Data Protection Act, 2023.** Notice, consent and purpose limitation for call metadata, message text, session signals and guardian relationships; rights to access and erasure.
- **RBI data localisation.** Payment system data stored in India: an Indian cloud region, and no free-tier services abroad.
- **RBI FREE-AI report.** Per-decision explanations, model versioning, human review of blocks, an audit trail, fairness monitoring. The prototype has a first version of each.
- **Telecom data.** A lawful source for caller risk (the DoT FRI through the bank), or signals the user consents to share from their own device.
- **Message text.** Keep only the hash, label and entities unless the user agrees to share the text.
- **Consortium.** A privacy review of the hashing scheme. A keyed hash is pseudonymous: a member holding the key can test a UPI ID it already knows.
- **Language models.** A paid tier or a self-hosted model with no training on submitted data; never a free tier.
- **Security.** TLS everywhere, one-time codes by SMS, a hardware-backed device key that needs the fingerprint for every use, app attestation, database row-level security, key management, penetration testing. Section 1 lists what is already in place and what is not.

## 4. Risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| Synthetic data flatters the model | Patterns we inject are patterns the model finds | Hard negatives; held-out campaigns and variants; saying so openly |
| False positives erode trust | Stopping a real rent payment is a serious failure | Calibration; false-positive budgets; R-09; the timed hold; review route; fairness report |
| Scope is larger than the team | 35 features, four people | All 35 have code behind them; what remains is verification, listed in [09, section 7](09-build-plan.md#7-what-is-left) |
| Free tiers change or run out | A demo dies on a limit | The prototype uses none: everything runs on the laptop |
| No public link | Judges cannot try it themselves | Team decision for now; planning notes for a hosted copy are in 08 |
| Latency budget missed | A slow check would be switched off by a PSP | Inside the budget at 100 a second in four runs, but the margin varied from 14 to 111 ms at p95, and about 100 a second is the ceiling of one process on the laptop. See [10, section 4](10-testing-and-demo.md#4-load-and-failure-tests). |
| Call signal is partial | Internet calls are invisible to telecom data | Calls are one signal of several; coaching signals and the message checker cover part of the gap |
| Guardian is misused | Control over a relative's money | Guardian can only approve or deny a risky payment; cannot initiate, release a decline or see history; opt-in; removable |
| Scammer coaches the victim to disable protection | Guardian removed mid-scam | 24-hour delay; guardian notified immediately |
| Scammer waits out the hold | Digital-arrest calls last hours | The hold never auto-releases; it re-checks with current signals |
| Reputation poisoning | False reports defame a legitimate UPI ID | A payer can report only their own payment; the Reported band needs confirmed fraud, so reports alone stop at Caution. Per-reporter limits are not built. |
| Message checker abused to flag rivals | A UPI ID named in fake "scam" pastes | Two distinct reporters before any effect; effect is one bad event, not a block; R-10 applies only to the person who flagged |
| Graph smears victims | People who paid a mule look connected to it | Features follow forwarded money and shared devices only; checked by AT-19 |
| The app names a scam that is not there | A payer is told their friend's account looks like a mule | A family is named only on its own evidence; otherwise the payment is just "unusual". Still named on 27% of flagged honest payments, each with some evidence behind it. |
| Language model invents facts | An analyst is misled | Strict prompt; numeric check; "AI-generated" label next to the facts; never used in decisions; template fallback |
| Uneven impact on vulnerable users | Elderly users may see more friction | Fairness report on legitimate payments; usability test with an older reader; spoken warning; guardian |
| Hindi copy is wrong or awkward | A warning that misleads | Native-speaker review before the demo. **Not done yet.** |
| Never run on a phone | The demo may be the first time | Run it on two phones before presenting; the browser build is the fallback |
| Demo depends on one laptop | Live failure | Two browser windows need no Wi-Fi; a recording as backup; `python manage.py demo-reset` |
| Events lost on a crash | The dashboard misses the last decisions | The payer's answer is unaffected; write to the outbox before publishing (not built) |
| Overclaiming in the pitch | Judges who know the domain will probe | Report synthetic metrics with the caveat; claim no real-world accuracy; fix the deck issues in section 6 |

## 5. Open questions

Answered by the team:

| # | Question | Answer |
|---|---|---|
| 1 | Which design supplies the visual elements? | The two reference designs (Madgicx, Scalenut), for both apps, with navigation on the left |
| 2 | What is the build window? | As the laptop allows |
| 3 | Hosted copy, or a laptop demo? | Laptop demo for now |
| 4 | Hold or Decline for scores from 0.85 to 0.95? | Hold, as proposed; `block_mode = decline` restores the deck's behaviour |
| 5 | How many demo phones? | Two |

Still open:

| # | Question | Why it matters | Default in the build |
|---|---|---|---|
| 6 | English and Hindi only, or Marathi too? | Copy and audio | English and Hindi |
| 7 | Who records the spoken warnings? | Audio quality | On-device text-to-speech |
| 8 | Who reviews the Hindi? | A wrong warning misleads | Nobody yet |
| 9 | Do we state any accuracy figure in the pitch? | Any number will be challenged | Synthetic metrics with the caveat only |
| 10 | Who owns each workstream? | The remaining work | Unassigned |

## 6. Decision log

Decisions this documentation makes beyond the pitch deck. Any can be overturned by the team.

| ID | Decision | Where |
|---|---|---|
| DEC-01 | The hot path is one service with four modules, not four services. | 04 §2.1 |
| DEC-02 | On any dependency failure the tier is capped at Warning. An outage never blocks a payment. | 04 §7 |
| DEC-03 | Step-Up is a pause and a pattern-specific question first, authentication second. | 02 F-09 |
| DEC-04 | Block is split: Hold from 0.85 to below 0.95, Decline at 0.95 or on a hard rule. | 02 F-01, 06 §5 |
| DEC-05 | A hold never releases money automatically. It asks again and re-assesses, and never holds twice. | 02 F-23 |
| DEC-06 | Decline always carries a route to a human review, and a 24-hour pair block. | 02 F-09 |
| DEC-07 | "No history" payees are shown neutrally. Newness alone never raises a tier. | 06 §6 |
| DEC-08 | Payee reputation is kept per observing bank. | 05 §1.2 |
| DEC-09 | Composite score: soft vote, anomaly score, rule score, stacker, isotonic calibration, floors and caps last. | 06 §4 |
| DEC-10 | Reputation formula: one half-life for both counters, smoothed rate, five bands. | 06 §6 |
| DEC-11 | Attribution by weighted cosine similarity in a 24-dimension signal space. Prototypes start hand-written and are replaced by learned ones. | 06 §11 |
| DEC-12 | A catalogue of 20 reason codes; reasons state observations, never accusations. | 06 §10 |
| DEC-13 | R-09 caps risk for long-standing payees. | 06 §2 |
| DEC-14 | The text model starts as TF-IDF with logistic regression; DistilBERT is optional. | 06 §9 |
| DEC-15 | The feature vector is stored on every decision and reused for training. | 05 §1.3 |
| DEC-16 | Contributions come from XGBoost's built-in output. The SHAP library is not used. | 04 §4.3 |
| DEC-17 | Nothing is written to PostgreSQL in the hot path. | 04 §4.3 |
| DEC-18 | An event-bus interface with in-process, Redis Streams and Kafka implementations. Only the in-process one was run. | 04 §2.3 |
| DEC-19 | The prototype runs natively on one laptop, with no containers. A hosted copy is not built. | 04 §3 |
| DEC-20 | Zero cost: open source locally; card-free free tiers when hosted. | 08 §1 |
| DEC-21 | If a hosted copy is built, Render hosts the API. Hugging Face Spaces was ruled out because running code there needs a paid plan. | 08 §3 |
| DEC-22 | Twilio is not used. One-time codes and calls are simulated in the app. | 08 §2 |
| DEC-23 | Built-in tokens are the default sign-in. Auth0 is optional. | 08 §2 |
| DEC-24 | The message checker takes pasted text only. The inbox is never read. | 02 F-16 |
| DEC-25 | Flagged indicators affect other users only after two distinct reporters. | 06 §8.1 |
| DEC-26 | Guardian removal and loosening take 24 hours. A guardian cannot release a Decline. | 02 F-17 |
| DEC-27 | Session signals can only bump a score. | 06 §2 |
| DEC-28 | Graph features follow forwarded money and shared devices, so victims are not implicated. | 06 §12 |
| DEC-29 | Consortium signals are a keyed hash and aggregates, and are a separate input that never changes the receiver's own counters. | 06 §13 |
| DEC-30 | A language model is used only for case summaries, after the decision, with a template fallback. | 02 F-27 |
| DEC-31 | Shadow mode returns Safe and Allow to the caller and stores the real decision. | 04 §5.5 |
| DEC-32 | Visual design is applied through a token contract; screens and copy come from this documentation. | 03 §1 |
| DEC-33 | Features are prioritised Must, Should, Could. | 01 §8 |
| DEC-34 | Numeric targets for recall, false positives, attribution, feedback and guardian latency. | 01 §10 |

Decisions made while building:

| ID | Decision | Where |
|---|---|---|
| DEC-35 | Sign-in with positions: admin, analyst and auditor on the dashboard; payers with phone and PIN in the app. | 02 F-30 |
| DEC-36 | Navigation on the left in both apps. | 03 §4.1, §6 |
| DEC-37 | Embedded PostgreSQL, an in-process cache and an in-process event queue, with the workers in the API process. | 04 §3.1 |
| DEC-38 | The model alone is capped at 0.94: it can hold a payment, never decline it. | 06 §3 |
| DEC-39 | Hard rules (denylist, a payee the payer flagged, first payment to an account with confirmed fraud) are exempt from the degraded cap. | 06 §5 |
| DEC-40 | The Reported band needs confirmed fraud. A high rate alone stops at Caution. | 06 §6 |
| DEC-41 | Reputation's burst rule and R-04 apply to personal accounts only, so a new shop's rush of customers is not treated as a mule. | 06 §2, §6 |
| DEC-42 | A scam family is named only when its own defining evidence was observed, and its text has variants so that it states only what was seen. | 06 §11 |
| DEC-43 | The attribution settings are chosen by cross-validation over whole campaigns, before the test window is looked at. | 06 §11 |
| DEC-44 | False-positive budgets are judged without payees that have confirmed fraud. | 06 §15 |
| DEC-45 | Training features are produced by replaying history through the serving feature code. | 06 §14.5 |
| DEC-46 | One payment is scored by flattened tree traversal, kept only if it reproduces scikit-learn's output. | 06 §3 |
| DEC-47 | At Step-Up the answer is sent alone first, so a risky answer stops the payment before authentication. | 04 §5.3 |
| DEC-48 | Past 48 assessments in flight, a payment gets the safe default immediately. | 04 §4.3 |
| DEC-49 | The mock PIN pad's digits never leave the device. | 03 P-09 |
| DEC-50 | Case summaries name no one: tokens go to the model, and "the payer" and "the payee" are shown. | 06 §19 |
| DEC-51 | Two browser windows stand in for two phones, each with its own session through `?slot=`. | 10 §5.1 |
| DEC-52 | Decisions are written to the database in batches; anything other than an ordinary allowed payment is written at once. | 04 §4.3 |
| DEC-53 | The graph is computed in a child process; other periodic work is sliced or moved off the request thread. | 04 §4.3 |
| DEC-54 | Three ideas are taken from caller-ID apps: a warning when a reported number calls, a look-up for a UPI ID or a number, and a personal never-pay list. No caller-ID service is used as a data source: none offers a free, public risk score. | 02 F-31 to F-33 |
| DEC-55 | A report about a caller counts fully only from someone that number called. Two such people flag it; five make it a known scam number. Typed-in reports count only towards the lowest level. | 11 §1.6 |
| DEC-56 | The look-up shows ranges, not counts, and names a scam pattern only when three or more people met it. | 02 F-32 |
| DEC-57 | A payer's own block changes the action, not the risk tier or the payee's reputation, and holds in shadow mode. Unblocking takes 24 hours. | 02 F-33 |
| DEC-58 | Passwordless demo sign-in is limited to the laptop itself by default; a phone signs in with a PIN. | 11 §1.1 |
| DEC-59 | Tokens are short-lived and renewed only while a person is active; sign-out and credential changes revoke on the server. | 11 §1.2 |
| DEC-60 | "The fingerprint matched" is not accepted from the app. A Step-Up by fingerprint needs a proof made with a key the phone enrolled using the PIN. | 11 §1.3 |
| DEC-61 | A decision clears a payment for ten minutes after its last approval, once, and only the PSP may report that money moved. | 11 §1.4 |
| DEC-62 | One payer can lower one payee's reputation through abandoned payments at most once a day. | 11 §1.6 |
| DEC-63 | Outside demo mode the service refuses to start with unsafe settings, rather than warning. | 11 §1.9 |
| DEC-64 | The workers run inside the API process. A separate worker process is not built; the claim that it could be run was removed. | 12 §6 |

## 7. Fixes to make in the pitch deck

Found while reading the slides. Worth correcting before the deck is submitted or presented.

1. **Slide 7, body text.** The last sentences of the fourth paragraph read as a note left by a drafting tool: the text says a third example code "isn't legible enough in the current image to transcribe accurately" and is "worth pulling from your original source file rather than guessing". Remove it.
2. **Slide 7, diagram.** The title is cropped ("TIME UPI AI FRAUD PREVENTION / TEM ARCHITECTURE & DESIGN"), the reputation box reads "PA REPUTATION ENGINE", and the example reason codes are garbled ("Deviice Changed", "Faclationaility reaoobord"). Redraw or re-export it.
3. **Slide 4, problem list.** "CEO-fraud via UPI collect requests" is out of date: P2P collect requests were discontinued on 1 October 2025. Say "payment links, QR codes and merchant collect requests".
4. **Slide 4, comparison table.** The row "Unsupervised anomaly" appears twice. "Legacy Rule" and "Engines" look like one column heading split across two columns. The ticks and crosses for NPCI FRM, MuleHunter.AI and M2P FRM need a source or should be softened. "Low false-positive rate" is ticked for the proposal before anything has been measured.
5. **Slide 6, API services.** "Twilio (call-correlation + OTP)" suggests Twilio supplies call records. It cannot. Say "simulated telecom signal" and name the DoT FRI as the production source.
6. **Slide 6, frontend.** Framer Motion does not run in React Native. List it against the web dashboard only.
7. **Slide 8.** The left edge of the feasibility diagram is cut off, so the green boxes have no visible parent node.
8. **Slide 5.** "Guided rehearsal (planned)" and "Scam Rehearsal Simulation (planned)" appear to be one feature under two names. Pick one.

If the added features are going into the deck, slide 5's feature list and slide 7's diagram should gain: the message checker, the guardian, coaching signals, name mismatch, the timed hold, shadow mode and the network graph.

## 8. References

Checked on 7 October 2026. Regulatory items were read from search results and publisher summaries, not from the primary documents in full; confirm against the originals before quoting them in the pitch. Free-tier items were read from the providers' own pages unless marked.

### Regulatory and context

- NPCI discontinuation of P2P collect requests from 1 October 2025: [Economic Times, 14 Aug 2025](https://economictimes.indiatimes.com/industry/banking/finance/banking/npci-to-stop-upi-p2p-collect-requests-from-october-1-to-combat-fraud/articleshow/123303105.cms)
- DoT Financial Fraud Risk Indicator: [PIB release, 21 May 2025](https://pib.gov.in/PressReleasePage.aspx?PRID=2130249)
- RBI advisory of 30 June 2025 on integrating the FRI: [PIB release, 2 Jul 2025](https://pib.gov.in/PressReleasePage.aspx?PRID=2141616&reg=3&lang=2)
- Digital Payments Intelligence Platform: [RBIH project page](https://rbihub.in/projects/digital-payments-intelligence-platform), [RBIH documentation](https://docs.rbihub.in/digital-payments-intelligence-platform)
- MuleHunter.AI: [RBIH project page](https://rbihub.in/projects/mulehunter)
- FREE-AI committee report, 13 August 2025: [RBI press release](https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=61018)

### Free tiers

- Render free web services: [Render docs](https://render.com/docs/free)
- Hugging Face Spaces hardware and plan requirement: [Spaces overview](https://huggingface.co/docs/hub/spaces-overview)
- Neon free plan: [Neon pricing](https://neon.com/pricing), [free plan limits](https://neon.com/faqs/free-plan-limits-and-quotas) (read from search summaries)
- Upstash Redis free tier: [Upstash pricing](https://upstash.com/pricing/redis) (read from search summaries)
- Gemini API free tier: [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- Groq free plan limits: [Groq rate limits](https://console.groq.com/docs/rate-limits)
- Auth0 free plan: [Auth0 pricing](https://auth0.com/pricing)
- Expo free plan: [Expo pricing](https://expo.dev/pricing)
- Resend free plan: [Resend pricing](https://resend.com/pricing)
