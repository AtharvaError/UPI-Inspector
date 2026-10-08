# 02 — Feature Specifications

[← Index](README.md)

Each feature has the same parts: **Purpose**, **Behaviour**, **Data and API** (names defined in [05](05-data-model.md) and [07](07-api-reference.md)), **Screens** (defined in [03](03-ux-and-design.md)) and **Done when** (the acceptance tests in [10](10-testing-and-demo.md)).

Features F-01 to F-15 come from the pitch deck. F-16 to F-28 were added afterwards, F-30 (sign-in and roles) when building began, and F-31 to F-35 (community and security features) after the first build. Formulas and thresholds live in [06](06-risk-engine-and-ml.md) and are only referenced here.

---

## Part A — Core features

### F-01 Risk assessment API with tiered response — Must

**Purpose.** One call that turns a payment attempt into a decision before the PIN screen.

**Behaviour.**
1. The app sends the payment, the device, the claimed payee name and the session signals.
2. The service authenticates the caller, validates the body, and returns the stored decision if this `txn_id` was seen before.
3. It enriches the request with features, scores it, maps the score to a tier and an action, attaches reasons and a scam family, and responds.
4. After responding it publishes the decision event and pushes it to the dashboard.

**Tier and action.**

| Score | Tier | Action | Notes |
|---|---|---|---|
| below 0.30 | `SAFE` | `ALLOW` | |
| 0.30 to below 0.60 | `WARNING` | `WARN` | |
| 0.60 to below 0.85 | `STEP_UP` | `STEP_UP` | |
| 0.85 to below 0.95 | `BLOCK` | `HOLD` | `DECLINE` if the tenant's `block_mode` is `decline`, or F-23 is not built |
| 0.95 and above, or a hard-decline rule | `BLOCK` | `DECLINE` | |

The thresholds 0.30, 0.60 and 0.85 are from the deck. The 0.95 split inside Block was added with the timed hold (F-23).

**Guarantees.**
- A response within a hard timeout of 300 ms: a decision, or a safe default marked `degraded`.
- Idempotent on `txn_id`.
- A malformed request gets a 4xx. It never gets a default "Safe".
- A dependency failure never produces a Block. See [04, section 7](04-architecture.md#7-failure-behaviour).

**Data and API.** `POST /v1/risk/assess`. Writes `transactions`, `risk_decisions`. Publishes `txn.assessed`.

**Done when.** AT-1, AT-7, AT-8, AT-9, AT-10.

---

### F-02 Deterministic rule engine — Must

**Purpose.** Catch known-bad situations with certainty and protect known-good ones, independently of the model.

**Behaviour.** Rules are read from a versioned YAML file and evaluated on the feature vector. A rule can set a **floor** (the score cannot go below it), a **cap** (cannot go above it) or a **bump** (added to the rule score). Fired rules are returned and can become reason codes. The file reloads without a restart. The rule list (21 rules, set `rules-0.6.0`) is in [06, section 2](06-risk-engine-and-ml.md#2-rule-engine). Three of them are hard: they stop a payment whatever the model says, and still apply when the system is degraded.

**Data and API.** `services/risk-api/rules/rules.yaml`. `risk_decisions.rules_fired`, `risk_decisions.rules_version`.

**Done when.** Each rule has a unit test for firing and not firing; AT-1 and AT-3 pass on rules alone.

---

### F-03 ML scoring ensemble — Must (XGBoost), Should (the rest)

**Purpose.** Score the combinations of signals that rules cannot enumerate.

**Behaviour.** XGBoost gives the main fraud probability. Random Forest gives a second opinion. Isolation Forest flags payments unlike anything seen. A small stacker combines them with the rule score, and isotonic calibration makes the final number behave like a probability. Rule floors and caps are applied last. Models are loaded by version and can be swapped without a restart.

Until the Should parts exist, the composite uses XGBoost and the rule score with fixed weights. Formulas are in [06, sections 3 to 5](06-risk-engine-and-ml.md#3-models).

**Data and API.** `model_registry`, Redis key `model:active`. `risk_decisions.model_version`.

**Done when.** The metrics in [01, section 10](01-product-overview.md#10-success-metrics) are measured and recorded with the model version.

---

### F-04 Payee reputation engine and pre-payment chip — Must

**Purpose.** Tell the payer how much history backs a payee, before they type an amount.

**Behaviour.**
- A background worker keeps time-decayed counters per UPI ID: payments received, bad events (confirmed fraud, credible reports, near-misses), and sliding-window fan-in.
- From these it derives a bad-event rate, a 0–100 trust score and a band: **Trusted**, **Neutral**, **No history**, **Caution** or **Reported**.
- The app requests the reputation as soon as a UPI ID is entered or scanned and shows a chip with the band, a one-line reason for Caution and Reported, and the **registered name** of the payee.
- "No history" is neutral. A new shop is not a scammer and the chip must not imply it.
- Reports are protected from abuse: they count only from users who paid that UPI ID, are rate-limited per reporter, and "Reported" needs an analyst's confirmation or a confirmed fraud.

**Data and API.** `GET /v1/payee/{vpa}/reputation`. Tables `vpas`, `vpa_reputation`. Redis `vpa_dir:{vpa_id}`, `vpa_rep:{tenant_id}:{vpa_id}`. Reputation is kept per observing bank; see [05, section 1.2](05-data-model.md#12-payees).

**Screens.** P-03, P-04, D-05.

**Done when.** AT-5, AT-6.

---

### F-05 Call correlation — Must

**Purpose.** Connect a scam call to the payment it causes.

**Behaviour.**
- The call correlator accepts call events: who was called (hashed), the caller's risk level, whether the caller is in the payer's contacts, start and end times.
- It writes a flag for the person called, which lives while the call is active and for 30 minutes after it ends.
- When that person starts a payment, enrichment reads the flag. Call features go to the rules and the model.
- When a call rule fires or a call feature is among the top contributors, the decision is **linked** to the call event. The dashboard draws the link as a timeline.
- In the prototype, calls come from the simulator and from the demo control panel, which also shows a fake incoming-call screen on the demo phone.
- No audio or call content is ever received.

Caller risk has four levels: none, medium, high, very high. They mirror the three FRI levels so that connecting the real source later is a mapping change.

**Data and API.** `POST /v1/signals/call`, `POST /v1/demo/call`. Tables `call_events`, `signal_links`. Redis `call_flag:{phone_hash}`.

**Screens.** P-18, D-03.

**Done when.** AT-3.

---

### F-06 Note-field NLP — Must (lightweight), Could (DistilBERT)

**Purpose.** Recognise scam wording in the text attached to a payment.

**Behaviour.** The note on a QR code, payment link or merchant collect request, or typed by the payer, is classified as benign, refund bait, authority threat, KYC or bill threat, investment or task offer, impersonation request, or prize. The scam probability becomes a model feature and can fire rule R-07. The same model powers the message checker (F-16).

The Must version is TF-IDF with logistic regression, and is built. The Could version, a multilingual DistilBERT, was not built. See [06, section 9](06-risk-engine-and-ml.md#9-text-classification).

**Done when.** The classifier meets the message-checker metric in [01, section 10](01-product-overview.md#10-success-metrics) and stays within 25 ms p95.

---

### F-07 Explainability: reason codes — Must

**Purpose.** Every flag comes with reasons a person can read.

**Behaviour.**
- For every decision at Warning or above, produce up to three reasons.
- Fired rules come first, then the model's top positive contributions grouped by signal.
- Each reason is a full sentence with the evidence in it ("This is 8 times larger than your usual payment"), in the payer's language.
- Reasons state observable facts. They never accuse the payee of a crime unless fraud is confirmed.
- The payer never sees a score or a code.

The catalogue of 20 reason codes is in [06, section 10](06-risk-engine-and-ml.md#10-explainability).

**Data and API.** `reason_codes`. `risk_decisions.reason_codes`. The `reasons` array in the assess response.

**Done when.** AT-10.

---

### F-08 Scam-family attribution and pattern-specific messages — Must

**Purpose.** Name the scam, and say what the scammer is probably saying.

**Behaviour.** Each flagged payment is compared with a prototype for each scam family. The closest family above a similarity threshold is attached, **provided the evidence that defines that family was actually observed** (the call, the story in the note or message, or evidence about the payee); otherwise the payment is "unclassified suspicious" with generic copy. Each family owns a headline, a body and one step-up question. Where the usual wording would state something that was not seen, a variant without that claim is used. The warning is read aloud by text-to-speech; there are no recorded clips.

Six families: digital arrest, fake refund, mule transfer, impersonation, KYC or bill threat, investment or task scam. Method in [06, section 11](06-risk-engine-and-ml.md#11-scam-taxonomy-and-attribution); copy in [03, section 7](03-ux-and-design.md#7-copy-catalogue).

**Data and API.** `scam_families`. `risk_decisions.scam_family`, `family_sim`.

**Done when.** AT-3, AT-4, AT-5; attribution accuracy ≥ 80%. As built: 87% on standard scam variants, 75% over all flagged scams ([12, section 4.2](12-as-built.md#42-naming-the-scam)).

---

### F-09 Tiered payer experience — Must

**Purpose.** Friction in proportion to risk.

**Behaviour by tier.**

| Tier | Payer sees | Payer does |
|---|---|---|
| Safe | Nothing. Straight to the PIN screen. | Nothing |
| Warning | An inline banner above the Pay button with the top reason and the family headline. | Continue or Cancel |
| Step-Up | A modal that interrupts the flow, in four steps. | All four steps, or Cancel |
| Block | A full-screen stop: Hold (F-23) or Decline. | Nothing to do in this flow |

**Step-Up, in order.**
1. **Pattern message.** The family headline and body, up to three reasons, and the spoken warning (F-21).
2. **Cool-off.** A visible countdown, 30 s by default. The Continue button is disabled until it ends. The server enforces it too.
3. **One question**, specific to the family, with a known risky answer. Giving the risky answer escalates the payment to Decline.
4. **Authentication.** Device biometric, or a one-time code.

The order matters. In these scams the real user is paying, so authentication alone stops nothing; the pause and the question are what break the scammer's script.

**Decline screen.** Reasons, family message, a button to call 1930, a button to generate a complaint pack (F-25), and a "This is not a scam" link that files a review request. The same payer-to-payee pair stays declined for 24 hours.

**Data and API.** `POST /v1/stepup/challenge`, `POST /v1/stepup/verify`, `POST /v1/risk/outcome`, `POST /v1/reports`. Table `stepup_challenges`.

**Screens.** P-05 to P-10.

**Done when.** AT-1 to AT-5.

---

### F-10 Outcome logging and near-miss feed — Must

**Purpose.** Record what the payer did, and treat backing out as a signal.

**Behaviour.** The app reports the payer's action and the final payment status. A decision at Warning or above that the payer abandoned is a **near-miss**. Near-misses appear in a dashboard feed and count as weak evidence against the payee.

| `user_action` | Meaning | Near-miss weight |
|---|---|---|
| `PROCEEDED` | Continued past Safe or Warning | — |
| `ABORTED` | Cancelled at a Warning | 0.3 |
| `STEPUP_PASSED` | Completed Step-Up and paid | — |
| `STEPUP_ABANDONED` | Cancelled during Step-Up | 0.3 |
| `STEPUP_FAILED` | Gave the risky answer | 0.6 |
| `BLOCKED` | Declined | — |
| `HOLD_CANCELLED` | Cancelled a held payment | 0.3 |
| `HOLD_CONFIRMED` | Confirmed after the hold; a new assessment follows | — |
| `HOLD_EXPIRED` | Never came back to a ready hold | — |
| `GUARDIAN_DENIED` | The guardian refused | 0.6 |
| `GUARDIAN_TIMEOUT` | The guardian did not answer in time | — |
| `SHADOW_ALLOWED` | Shadow mode; nothing was enforced | — |

**Data and API.** `POST /v1/risk/outcome`, `GET /v1/near-misses`. Table `outcomes`. Publishes `txn.outcome`.

**Screens.** D-04.

**Done when.** AT-6.

---

### F-11 Feedback loop: live reputation updates — Must

**Purpose.** One person's hesitation protects the next person.

**Behaviour.** Outcome and report events update the payee's counters within seconds. The cache is refreshed, the dashboard is notified, and the next assessment of that UPI ID uses the new values. Analyst labels (confirmed fraud, false positive) do the same with more weight, and are the labels used for retraining.

**Data and API.** `POST /v1/decisions/{id}/label`, `POST /v1/reports`. Topics `txn.outcome`, `fraud.reports`, `reputation.updated`.

**Done when.** AT-6.

---

### F-12 Retraining pipeline and model registry — Should

**Purpose.** Keep up with changing scam patterns without touching the hot path.

**Behaviour.** A job extracts decisions with outcomes and labels, trains candidates on the feature vectors stored at decision time, evaluates them against the active model on a frozen test set, registers them, and promotes one only if every gate passes. Promotion flips a version pointer and the scoring module loads the new model. The job also recomputes scam-family prototypes. It is a script. Details in [06, section 16](06-risk-engine-and-ml.md#16-retraining). **As built, the trainer learns from the simulator's rows; extracting live decisions with analyst labels and near-misses is not built.**

**Data and API.** `model_registry`, the `ml/artifacts/ACTIVE` pointer file. `python manage.py train`.

**Done when.** A retrain run produces a registered candidate with metrics, and promotion changes `model_version` on new decisions without a restart.

---

### F-13 Analyst dashboard — Must

**Purpose.** Show analysts what is happening and why, and collect their judgement.

| Page | Contents |
|---|---|
| D-02 Live feed | Decisions as they happen: time, amount, payee, tier, action, top reason, family. Filter by tier, family, tenant. |
| D-03 Decision detail | Reasons, feature contributions as a waterfall, fired rules, payee reputation, session signals, name match, linked timeline of calls, messages and the payment, AI summary (F-27), case actions. |
| D-04 Near-miss feed | Payments abandoned after a warning. |
| D-05 Payee detail | Reputation history, counters, graph neighbourhood, consortium signal, reports. |
| D-10 Review queue | False-positive review requests and fraud reports awaiting a decision. |

Case actions are **Confirm fraud** (with a family), **Mark false positive** and **Add note**. Each writes a label and an audit entry.

**Data and API.** `GET /v1/decisions`, `GET /v1/decisions/{id}`, `POST /v1/decisions/{id}/label`, `GET /v1/payees/{vpa}`, `GET /v1/reviews`. Socket.IO namespace `/analyst`.

**Done when.** AT-3 shows the linked timeline; a label changes the payee's reputation.

---

### F-14 Synthetic simulator and demo control — Must

**Purpose.** Produce all the data the prototype needs, and stage scenarios on demand.

**Behaviour.**
- **Simulator.** Generates users, devices, merchants, peers, mule accounts and scam campaigns across two tenants; a history of legitimate payments; and scam scenarios with their calls, messages, session signals and claimed payee names. It also generates hard negatives. Output is a replay stream and training files. Specification in [06, section 14](06-risk-engine-and-ml.md#14-synthetic-simulator).
- **Demo control** (D-11). Buttons that set up a named scenario for a chosen demo user: trigger a fake call, deliver a scam message, prepare a payee. A reset button restores the starting state.

**Data and API.** `POST /v1/demo/scenario`, `POST /v1/demo/call`, `POST /v1/demo/reset`. `python manage.py simulate`, `python manage.py demo-reset`.

**Done when.** Every scenario in the demo script can be staged from D-11 and reset in under 10 s.

---

### F-15 Observability — Should

**Purpose.** Know whether the service is fast and healthy.

**Behaviour.** The service exports latency per stage, tier and action counts, degraded-decision count, cache hit rate, fallback rate and the active model version. `GET /metrics` serves them in Prometheus text format, and the dashboard's System health page (D-09) shows the same numbers from an API endpoint. Prometheus and Grafana themselves were not set up.

**Data and API.** `GET /metrics`, `GET /v1/metrics/summary`.

**Done when.** AT-9 reads its percentiles from these metrics.

---

## Part B — Added features

### F-16 Message checker — Must

**Purpose.** Deliver the "SMS" half of the deck's call-and-SMS promise. Let a payer check a suspicious message, and use the result to protect their next payment.

**Behaviour.**
1. From the home screen the payer opens **Check a message** and pastes the text of an SMS or chat message.
2. The service classifies it with the text model and extracts **entities**: UPI IDs, phone numbers, links and amounts.
3. The payer sees a verdict (**Looks like a scam**, **Be careful** or **Looks fine**), the scam family, the red-flag phrases highlighted with a short explanation of each, and advice.
4. If the scam probability is 0.7 or more:
   - a **message flag** is set for that payer for 24 hours and raises the score of their payments;
   - extracted UPI IDs and phone numbers are recorded as **flagged indicators**.
5. If the payer later tries to pay a UPI ID that appeared in a message they flagged, rule R-10 declines the payment with the reason "This UPI ID appeared in a message you flagged as a scam."
6. When two or more different users flag the same indicator, it starts to affect everyone: a flagged UPI ID gains a bad event in its reputation, and a flagged phone number is rated a medium-risk caller.
7. A decision influenced by a message flag is linked to the message, and both appear on the dashboard timeline.

**Verdict bands.** Scam probability ≥ 0.7: looks like a scam. 0.4 to below 0.7: be careful. Below 0.4: looks fine.

**Red-flag highlighting.** Scoring uses the model. Highlighting uses a curated lexicon of patterns per class (urgency, authority, threats, refund bait, requests for a PIN or code, shortened links), because it gives stable, explainable highlights.

**Out of scope.** Reading the SMS inbox automatically; screenshots and OCR; share-to-app from other apps.

**Data and API.** `POST /v1/signals/message`. Tables `message_checks`, `flagged_indicators`, `signal_links`. Redis `msg_flag:{user_id}`. Topic `msg.checked`. Features `msg_flag_active`, `msg_scam_prob`, `mins_since_msg_flag`, `payee_in_flagged_msg`. Rules R-10, R-13.

**Screens.** P-12, D-03.

**Done when.** AT-11, AT-12.

---

### F-17 Trusted-contact guardian — Must

**Purpose.** Protect payers who may not act on a warning themselves, by involving someone they trust.

**Behaviour.**

*Setting up.*
1. A payer nominates another user as their guardian by phone number or UPI ID and picks a mode.
2. The guardian receives an invitation and accepts it. Until then the link is `PENDING`.
3. A payer has at most one active guardian in the prototype. A user can be guardian to several people.

*Modes.*

| Mode | What happens |
|---|---|
| **Notify** | The guardian is alerted whenever the payer's payment reaches Step-Up or Block. The payer's flow is unchanged. |
| **Co-approve** | In addition, a payment needs the guardian's approval when it is at Step-Up, or when it is to a new payee and above an amount the payer set. |

*A co-approval.*
1. The assess response says a guardian approval is required and carries a request ID.
2. The payer sees a waiting screen. They still complete the Step-Up steps.
3. The guardian's phone shows the request: amount, the payee's registered name and UPI ID, the reasons, the scam family. Buttons: **Approve**, **Deny**, **Call them**.
4. **Approve** and Step-Up passed: the payment continues. **Deny**: it is cancelled and recorded as `GUARDIAN_DENIED`. No answer in 10 minutes: it is cancelled as `GUARDIAN_TIMEOUT`.

*Limits that keep it safe.*
- A guardian can never release a Decline, shorten a hold, or start a payment.
- A guardian sees only the risky payment in front of them: no balance, no history.
- **Removing a guardian takes 24 hours** and the guardian is told at once. A scammer coaching a victim cannot switch the protection off in the moment.
- Changing from Co-approve to Notify has the same 24-hour delay.

*Delivery.* Alerts arrive in-app over a live socket, in the Guardian inbox. Push notifications are optional and need a development build of the app.

**Data and API.** `POST /v1/guardians`, `POST /v1/guardians/{id}/accept`, `DELETE /v1/guardians/{id}`, `GET /v1/guardian/requests`, `POST /v1/guardian/requests/{id}/respond`. Tables `guardians`, `guardian_requests`. Topic `guardian.events`. Socket.IO namespace `/user`, events `guardian.request`, `guardian.response`.

**Screens.** P-13, P-14, P-20.

**Done when.** AT-13, AT-14, AT-15.

---

### F-18 Coaching signals from the session — Must

**Purpose.** Detect that someone is being walked through a payment, using only what the app can observe, including when the scam call is on an internet-calling app that telecom data cannot see.

**Behaviour.** The app records, for each payment flow, and sends with the assess request:

| Signal | How the app measures it |
|---|---|
| `vpa_entry_method` | `typed`, `pasted`, `contact`, `qr` or `link` (a sixth value, `merchant_collect`, is set by the PSP for merchant collect requests) |
| `app_switch_count` | Times the app went to the background and came back during the flow |
| `time_on_pay_screen_s` | Seconds between opening the pay screen and tapping Pay |
| `amount_edit_count` | Times the amount field was cleared and re-entered |
| `flow_started_at` | When the flow began |

These become model features. Rule R-11 fires on the classic combination: a pasted or linked UPI ID, a new payee, two or more app switches, and an unusually large amount. Reasons can say "You pasted this UPI ID instead of choosing a saved contact" and "You switched to another app 3 times while making this payment."

**Notes.**
- The signals come from the client and could be falsified by a modified app. They are treated as supporting evidence and never set a floor on their own.
- No screen content, keystrokes or other apps' names are collected.

**Data and API.** The `session` object in the assess request; stored in `transactions.session`. Features in [05, section 4](05-data-model.md#4-feature-catalogue-v1). Rule R-11.

**Screens.** P-03, P-05 (collection only), D-03 (display).

**Done when.** AT-16.

---

### F-19 Shadow mode and policy lab — Must

**Purpose.** Let a bank see what the system would do before it does anything, and choose thresholds from evidence.

**Behaviour.**

*Shadow mode.* Each tenant has a mode, `enforce` or `shadow`. In shadow mode every payment is scored and stored exactly as normal, but the response tells the app to allow it and shows the payer nothing. The stored decision is marked `enforced = false`.

*Policy lab* (D-07). For a chosen time window the page shows, from stored decisions and their labels:

- three sliders for the Warning, Step-Up and Block thresholds, starting at the current values;
- live-updating results: share of scams caught at Step-Up or Block, share of legitimate payments in each tier, interruptions per 1,000 payments, rupees of fraud that would have been stopped, and precision at a chosen real-world fraud rate;
- a curve of scams caught against payers interrupted, with the current setting marked;
- the same figures for the thresholds in force, for comparison.

An admin can **Apply** the slider values. That updates the tenant's policy, takes effect on the next request, and writes an audit entry. An admin can also switch the mode.

**Rules.**
- Rule floors and caps are already part of each stored score, so moving a threshold re-buckets final scores; it does not re-run rules.
- The deck's thresholds remain the defaults.
- Maths in [06, section 18](06-risk-engine-and-ml.md#18-policy-simulation).

**Data and API.** `GET /v1/policy`, `PUT /v1/policy/mode`, `PUT /v1/policy/thresholds`, `POST /v1/policy/simulate`. Table `policy_config`. Redis `policy:{tenant_id}`.

**Screens.** D-07.

**Done when.** AT-17, AT-18.

---

### F-20 Mule network graph — Should

**Purpose.** See, and score, a payee by where its money goes and what it shares with known bad accounts.

**Behaviour.**
- A job rebuilds a graph every 60 seconds from the last 30 days: UPI IDs are nodes; payments and shared devices are edges.
- For each payee it computes:
  - **downstream distance** to a Reported UPI ID, following the direction money flows out;
  - whether it **shares a device** with a Reported UPI ID;
  - **pass-through ratio**: how much of what it receives it forwards within 30 minutes;
  - the share of risky accounts in its **community**.
- These feed the model and rule R-14, and can produce the reasons "Money sent to this UPI ID is quickly passed on to an account reported for fraud" and "This UPI ID forwards almost everything it receives within minutes."
- The dashboard's Network page (D-06) draws a payee's neighbourhood: nodes coloured by reputation band, edges weighted by amount, a click to expand or open a payee.

**Why direction matters.** A victim who paid a mule is one step away from it too. Following only outgoing money, and shared devices, points at the mule chain and not at its victims.

**Data and API.** `GET /v1/graph/neighbourhood`. Table `graph_features`, `vpa_devices`. Redis `vpa_graph:{vpa_id}`. Rule R-14.

**Screens.** D-06, D-05.

**Done when.** AT-19.

---

### F-21 Spoken warning — Should

**Purpose.** Reach payers who will not read a screen while a scammer is talking to them.

**Behaviour.**
- At Step-Up and Block, the app plays a short audio warning in the payer's language: the family headline and body.
- A mute button is always visible. A setting turns voice warnings off; it is on by default.
- Audio plays once per decision and stops when the payer leaves the screen.
- Source of the audio: recorded clips bundled with the app, one per family per language. If a clip is missing, on-device text-to-speech reads the same text.
- Warnings are never spoken at the Warning tier, to keep them rare enough to matter.

**Data and API.** `users.voice_warnings`. The `audio_key` in the assess response's `scam_family`.

**Screens.** P-06, P-07, P-08, P-17.

**Done when.** AT-20.

---

### F-22 Payee name mismatch — Must

**Purpose.** Catch the moment a payer is told they are paying "SBI Refund Desk" while the UPI ID belongs to an individual.

**Behaviour.**
- Every payment carries a **claimed name**: the name on the QR code or link, or the label the payer typed.
- The service looks up the **registered name** and account kind (person or merchant) for the UPI ID.
- It computes a name-match score from 0 to 1 after normalising both names.
- Two conditions matter:
  - a low match (below 0.5) on a QR or link payment;
  - a claimed name containing an institutional word (bank, refund, police, customs, KYC, electricity, customer care, a major brand) while the registered account is an individual.
- Rule R-12 raises the score. The reason reads: "The link says 'SBI Refund Desk' but this UPI ID is registered to 'Rakesh Kumar'."
- The reputation chip always shows the registered name, so the payer can compare for themselves even when nothing fires.

**Data and API.** `payee.claimed_name` in the assess request and as a query parameter on the reputation endpoint. `vpas.registered_name`, `vpas.kind`. Features `name_match_score`, `claimed_name_institutional`, `payee_is_individual`. Rule R-12.

**Screens.** P-03, P-04, D-03.

**Done when.** AT-21.

---

### F-23 Timed hold — Should

**Purpose.** A Block that a legitimate payer can recover from, and that gives a scam time to fall apart.

**Behaviour.**
1. A Block with a score from 0.85 to below 0.95, and no hard-decline rule, returns the action `HOLD`.
2. The payer sees the Hold screen: the reasons, the family message, when they can try again (30 minutes by default; 60 seconds in demo mode), and a **Cancel payment** button.
3. A guardian, if there is one, is alerted.
4. The payer can cancel at any time. Cancelling is a near-miss.
5. When the hold period ends, the payer is told the payment is ready and is asked **"Do you still want to send this?"** Nothing is sent automatically.
6. If they confirm, the payment is **assessed again** with the signals as they are now.
   - If the result is Safe, Warning or Step-Up, that tier's flow applies.
   - If the result is still Block, the payment is declined. A payment is never held twice.
7. If they do not respond within 24 hours, the hold expires.

**Why it re-checks and does not auto-release.** A digital-arrest call can last hours. Releasing money on a timer would let the scammer simply wait.

**Data and API.** `GET /v1/holds`, `POST /v1/holds/{id}/cancel`, `POST /v1/holds/{id}/confirm`. Table `held_payments`. `policy_config.block_mode`, `hold_seconds`. Topic `hold.events`. Socket.IO event `hold.ready`.

**Screens.** P-07, P-11.

**Done when.** AT-22.

---

### F-24 Cross-bank reputation sharing — Could

**Purpose.** Show how the "silos" gap closes: a report at one bank protects customers of another.

**Behaviour.**
- The prototype has two tenants, Bank A and Bank B, in one deployment. Each keeps its own reputation view of every UPI ID, built only from its own customers.
- When a UPI ID in one tenant accumulates enough bad events (two or more, and either a confirmed fraud or the Caution band), that tenant publishes a **consortium signal**: a keyed hash of the UPI ID, its decayed counters and its band. Nothing about payers or individual transactions is shared.
- The other tenant matches the hash against the payee in its own assessments. A match feeds the model and rule R-15 and produces the reason "This UPI ID has been reported by customers of another bank."
- The dashboard's payee page shows where a signal came from.

**Limits to state openly.** A keyed hash hides UPI IDs from outsiders, but any member holding the key can test a UPI ID it already knows. This is pseudonymisation, not anonymity, and a real consortium would need a privacy review.

**Data and API.** `GET /v1/consortium/signals`. Table `consortium_signals`. Redis `consortium:{tenant_id}:{vpa_token}`. Features `consortium_reported`, `consortium_p_bad`. Rule R-15.

**Screens.** D-05.

**Done when.** AT-23.

---

### F-25 One-tap complaint pack — Should

**Purpose.** Cut the time between realising and reporting, which decides whether money can be frozen.

**Behaviour.**
- Available from the Decline and Hold screens, from a completed payment in history ("Report this payment"), and from the dashboard's decision detail.
- One tap generates a **PDF and a JSON file** containing: the transaction ID, date and time, amount, payer and payee UPI IDs, the payee's registered name, the scam family, the reasons, and a timeline of linked calls and messages with their times and risk ratings.
- The screen then offers: **Call 1930**, **Open the cybercrime portal**, **Share the PDF**.
- Generating a pack also files a fraud report internally, which feeds the payee's reputation.
- The prototype submits nothing to any outside system. The payer files the complaint themselves.

The pack's field list should be checked against the current portal form before a pilot.

**Data and API.** `POST /v1/complaint-packs`, `GET /v1/complaint-packs/{id}.pdf`, `GET /v1/complaint-packs/{id}.json`. Table `complaint_packs`.

**Screens.** P-15, D-03.

**Done when.** AT-24.

---

### F-26 Drift and fairness monitoring — Could

**Purpose.** Notice when the model is seeing a world it was not trained on, and whether it treats groups of users unevenly.

**Behaviour.**
- A job compares recent traffic with the training baseline and computes a stability index for the score and the main features. Values over 0.2 raise an alert on the dashboard.
- It also tracks the tier mix and the degraded-decision rate over time.
- For fairness it reports, by age band, user segment and language, the share of **legitimate** payments at each tier and the false-positive rate at Step-Up or above, plus the ratio between the best- and worst-treated group. A ratio above 1.5 is flagged.
- Results appear on Model health (D-08).

Elderly users really are targeted more, so more flags for them is not in itself unfair. The measure that matters is how often *legitimate* payments are interrupted, which is what is reported.

**Data and API.** `GET /v1/model-health/drift`, `GET /v1/model-health/fairness`. Tables `drift_reports`, `fairness_reports`. Socket.IO event `drift.alert`. Maths in [06, section 17](06-risk-engine-and-ml.md#17-drift-and-fairness-monitoring).

**Screens.** D-08.

**Done when.** AT-25, AT-26.

---

### F-27 AI-written case summaries — Could

**Purpose.** Save an analyst the minute it takes to read a decision's evidence.

**Behaviour.**
- For each decision at Step-Up or above, a background worker writes a three-to-four sentence summary, a list of key evidence and a suggested next action.
- It is generated **after** the decision and **never influences it**.
- The model is given only the decision's own structured data, with identifiers replaced by tokens, and is told to cite nothing else. A check rejects a summary that contains a number not present in the input.
- The dashboard shows it labelled "AI-generated", next to the underlying facts, with a Regenerate button.
- Providers are tried in order: a free-tier hosted model, a second free-tier provider, a local model, and finally a fixed template that needs no model. The feature always produces something.

**Data and API.** `GET /v1/decisions/{id}/summary`, `POST /v1/decisions/{id}/summary/regenerate`. Table `case_summaries`. Socket.IO event `case.summary_ready`. Prompt in [06, section 19](06-risk-engine-and-ml.md#19-case-summaries).

**Screens.** D-03.

**Done when.** AT-27.

---

### F-28 Scam rehearsal simulation — Could

**Purpose.** Let people meet a scam script in a safe setting, so the real one is familiar.

**Behaviour.**
- A **Practice** section lists short scripted simulations: digital arrest, fake refund, impersonation.
- Each is a 60 to 90 second branching conversation. The "scammer" speaks; the user picks a reply from two or three options; each choice gets immediate feedback on why it was safe or risky.
- At the end the user sees how many safe choices they made and the three signs to remember.
- A Step-Up or Decline screen offers "See how this scam works", which opens the matching script after the payment flow has ended.
- Scripts are data. They use no language model.
- A rehearsal never blocks, delays or influences a payment.

**Data and API.** `GET /v1/rehearsals`, `GET /v1/rehearsals/{code}`, `POST /v1/rehearsals/{code}/runs`. Tables `rehearsal_scripts`, `rehearsal_runs`.

**Screens.** P-16.

**Done when.** AT-28.

---

### F-29 Localisation: English and Hindi — Must

**Purpose.** A warning in the wrong language is not a warning.

**Behaviour.**
- All payer-facing text comes from a catalogue keyed by locale: screen labels, reason sentences, family messages, questions, message-checker advice, rehearsal scripts.
- The payer chooses a language in settings; it is stored on the user and sent with each request.
- The API returns reasons and family messages already rendered in the requested locale.
- Numbers and currency follow Indian formatting (₹49,000; 1,00,000).
- The dashboard is English only.
- Adding a language is adding catalogue entries and audio clips. No code changes.

**Data and API.** `users.locale`, `locale` in requests. `reason_codes.templates`, `scam_families.messages`. App string files.

**Screens.** All payer screens; P-17 for the setting.

**Done when.** Every payer-facing string on screens P-02 to P-17 appears in both languages; AT-20 runs in Hindi.

---

### F-30 Authentication and role-based access — Must

**Purpose.** Everyone signs in, and what they can see and do depends on their position.

**Accounts.**

| Kind | Signs in with | Roles |
|---|---|---|
| Staff (dashboard) | Email and password | `admin`, `analyst`, `auditor` |
| App user (payer app) | Phone number and 6-digit app PIN | `user`. Being a guardian is a relationship (F-17), not a role. |

**Behaviour.**
- Passwords and PINs are stored only as Argon2 hashes.
- Signing in returns a short-lived signed token that carries the account ID, kind, bank, role and session. Every endpoint checks it. Session rules are in F-35.
- Five failed attempts lock an account: five minutes the first time, longer each time after (F-35).
- A payer can register: name, phone number, PIN, language and bank. Registration creates the account and a UPI ID in the mock PSP directory.
- An admin manages accounts on D-13: add staff, change a role, deactivate or reactivate, reset a password; deactivate or reactivate an app user. A deactivated account cannot sign in and its tokens stop working.
- An admin cannot deactivate or demote their own account, so a bank can never lock itself out.
- Every account change is written to the audit log.
- In demo mode only, both apps offer one-click sign-in to the seeded accounts, and by default only on the laptop that runs the demo (F-35).

**What each position can do.**

| Capability | Admin | Analyst | Auditor | App user |
|---|---|---|---|---|
| Live feed, decisions, near-misses, payees, network, model and system health | Yes | Yes | Read-only | — |
| Label decisions, resolve reviews, create complaint packs | Yes | Yes | — | Own payments only |
| Run a policy simulation | Yes | Yes | Yes | — |
| Change thresholds, switch enforce and shadow | Yes | — | — | — |
| Manage staff, roles and app users | Yes | — | — | — |
| Read the audit log and the Security page | Yes | — | Yes | — |
| Demo control | Yes | — | — | — |
| Pay, check messages, manage holds, report, rehearse, manage own guardian, look up, report a caller, keep a never-pay list | — | — | — | Yes |
| Approve or deny a protected person's payment | — | — | — | Yes, as that person's guardian |

**Data and API.** `POST /v1/auth/login`, `POST /v1/auth/register`, `POST /v1/auth/staff-login`, `GET /v1/auth/me`, `GET /v1/admin/staff`, `POST /v1/admin/staff`, `PATCH /v1/admin/staff/{id}`, `POST /v1/admin/staff/{id}/reset-password`, `GET /v1/admin/users`, `PATCH /v1/admin/users/{id}`. Tables `users`, `staff_users`, `audit_log`.

**Screens.** P-00, P-01, D-01, D-13.

**Done when.** AT-31, AT-32.

---

## Part C — Community and security features

Added on 7 October 2026. F-31 to F-34 take three ideas from caller-ID apps (a warning when a reported number calls, a look-up, a personal block list) and apply them to payments. F-35 hardens sign-in, sessions and payment for a product that handles money. No caller-ID service is used as a data source: none offers a free, public risk score ([11](11-security-risks-and-decisions.md), DEC-54).

### F-31 Flagged-caller warning and caller reports — Should

**Purpose.** Tell someone, before they open the payment screen, that the number on the line has been reported by other people; and let them say so themselves.

**Behaviour.**
- When a call starts from a number at risk level 2 or 3 that is not in the callee's contacts, the callee's app is told at once. The incoming-call screen (P-18) carries a red "Reported as a scam number" box. While the call is active, an amber in-call bar and a red banner sit above every ordinary screen. The banner is hidden on the screens that stop or finish a payment, which carry their own warning.
- When the app opens, it asks whether a call is in progress or ended in the last 30 minutes, and what is known about the caller.
- **Reporting.** "Report this caller" on the banner, and "Was the caller trying to scam you?" on the Hold and Decline screens after a payment made during or just after a call was stopped. The app sends "the caller of my last call"; it never holds the number. The look-up (F-32) can report a typed number.
- **What a report does.** One report per person per number. A report is *verified* if the number called the reporter in the last 30 days, or was in a message they checked that was flagged. Two verified reporters raise a number to level 2; five to level 3, where rule R-02 applies. Unverified reports count only towards level 1, and that takes three people in all. Reports never lower a level. Ten reports a day per person.
- A raised level takes effect on that number's next call, for everyone at every bank's app.

**Data and API.** `GET /v1/users/me/call-status`, `POST /v1/callers/report`. Tables `caller_reports`, `caller_risk_list`. Real-time event `call.flagged`.

**Screens.** P-18, the banner on every screen, P-07, P-08, P-22.

**Done when.** AT-33.

---

### F-32 Look up a UPI ID or a number — Should

**Purpose.** Check who you are dealing with before paying or calling back, outside any payment.

**Behaviour.**
- One field takes a UPI ID or a phone number.
- **UPI ID.** Registered name, personal or business, verified or not, reputation band and its reason, how long ago the account was opened, fraud reports in the last 30 days, payments other people cancelled after a warning in the last 14 days, whether the bank has confirmed fraud, whether another bank's customers reported it, how often the person looking has paid it, and whether it is on their never-pay list. If three or more different people were stopped while paying it in the same situation, that scam pattern is named with its advice.
- **Phone number.** A level in words (no reports; a few people; several people; known scam number), where the information comes from, how many people reported it, and how often it called the person looking.
- **Counts are ranges**: none, 1 or 2, 3 to 9, 10 or more. Nothing about any reporter is returned.
- "No reports" is shown with a caution that it is not proof of safety.
- From a result: pay the UPI ID, put it on the never-pay list, or report the number.
- 30 look-ups a minute and 300 a day per person.

**Data and API.** `GET /v1/lookup?q=`.

**Screens.** P-22.

**Done when.** AT-34.

---

### F-33 Never-pay list — Should

**Purpose.** Let a person decide, in a calm moment, that a UPI ID must never be paid, and have that hold when someone is pressing them.

**Behaviour.**
- Add a UPI ID from the look-up, from the Hold or Decline screen, or by typing it. Optionally report it as fraud at the same time. Up to 200 entries.
- A payment to a listed UPI ID is declined before the PIN with the reason "You put this UPI ID on your never-pay list". The payee screen and the amount screen already say so.
- **It changes the action, not the judgement.** The risk score and tier stay as the models gave them, and the payee's reputation is not affected: one person's list is not evidence about a payee. It is the payer's own instruction, so it is honoured in shadow mode too.
- **Unblocking takes 24 hours** (one minute in demo mode), for the same reason removing a guardian does. The entry shows a countdown and "Keep blocked", which cancels the request. The app is told when a block lifts.
- The list is private: nobody else, including a guardian, sees it.

**Data and API.** `GET /v1/users/me/blocks`, `POST /v1/users/me/blocks`, `DELETE /v1/users/me/blocks/{vpa}`, `POST /v1/users/me/blocks/{vpa}/keep`. Table `user_blocks`. Cache key `user_block:{user_id}`. Reason code `USER_BLOCKED`. Real-time event `block.lifted`.

**Screens.** P-22, P-03, P-05, P-08.

**Done when.** AT-35, AT-40.

---

### F-34 Scam trends and official help — Could

**Purpose.** Show what is going around this week, and where to turn.

**Behaviour.**
- The three scam patterns seen most at the person's bank in the last seven days, each with a name, general advice, a rounded count ("80+") and whether it is rising, falling, steady or new compared with the week before. A pattern is listed only if at least three different people met it.
- Home shows the top one. Each links to the matching practice script (F-28).
- A short list of official help: 1930, cybercrime.gov.in, Sanchar Saathi (Chakshu), and a reminder to use only the bank number printed on the card.
- In the person's language.

**Data and API.** `GET /v1/trends`.

**Screens.** P-02, P-22.

**Done when.** AT-36.

---

### F-35 Session and device security — Must

**Purpose.** Make sure that a copied token, an unlocked phone left on a table, or a modified app is not enough to move money or to see someone's payments. [11, section 1](11-security-risks-and-decisions.md#1-security-in-the-prototype) has the full list of controls; this is what the user and the operator see.

**Behaviour.**
- **PIN and password rules.** Easy PINs are refused at registration and on change. Staff passwords need 10 characters.
- **Lockout** grows: 5 minutes, then double each time, up to a day.
- **Sessions are short.** App sessions lapse after 30 minutes without use, dashboard sessions after 60; both end 12 hours after sign-in. While in use they renew silently.
- **Sign-out is real.** Signing out, "Sign out on every device", and changing a PIN or password stop the affected tokens at once.
- **App lock.** A PIN session locks on reopening, after a minute in the background or five minutes untouched. PIN or, on an enrolled phone, fingerprint.
- **Device enrolment.** Signing in with the PIN on a phone enrols that phone. Only an enrolled phone can confirm a Step-Up by fingerprint; anything else uses a one-time code.
- **Step-Up limits.** Three wrong codes close the challenge. Three codes per challenge.
- **A decision is good for ten minutes** after its last approval, and for one payment.
- **Passwordless demo sign-in** is offered only on the laptop that runs the demo.
- **Security page (D-14)** for admins and auditors, and `python manage.py security-check`.

**Data and API.** `POST /v1/auth/refresh`, `POST /v1/auth/logout`, `POST /v1/auth/logout-all`, `POST /v1/auth/verify-pin`, `POST /v1/auth/change-pin`, `POST /v1/auth/change-password`, `POST /v1/auth/device-key`, `GET /v1/auth/devices`, `DELETE /v1/auth/devices/{key_id}`, `GET /v1/security/posture`. Tables `revoked_sessions`, `device_keys`; columns `token_gen`, `lockouts` on `users` and `staff_users`.

**Screens.** P-00, P-17, P-21, D-01, D-14.

**Done when.** AT-37, AT-38, AT-39.

---

## Feature dependencies

Build order follows from these. A feature needs everything to its right.

| Feature | Needs |
|---|---|
| F-07 Reason codes | F-02, F-03 |
| F-08 Attribution | F-07 |
| F-09 Tiered experience | F-01, F-07, F-08 |
| F-11 Feedback loop | F-04, F-10 |
| F-16 Message checker | F-06 |
| F-17 Guardian | F-09 |
| F-19 Policy lab | F-10, F-14 (labels) |
| F-20 Network graph | F-04, F-14 |
| F-21 Spoken warning | F-08, F-29 |
| F-23 Timed hold | F-09 |
| F-24 Consortium | F-04, two tenants in F-14 |
| F-25 Complaint pack | F-05, F-16 (for the timeline) |
| F-26 Drift and fairness | F-03, F-14 |
| F-27 Case summaries | F-07, F-13 |
| F-28 Rehearsal | F-08 (family codes) |
| F-31 Caller warning and reports | F-05 |
| F-32 Look-up | F-04, F-31 |
| F-33 Never-pay list | F-01, F-09 |
| F-34 Trends and help | F-08, F-10 |
| F-35 Session and device security | F-30, F-09 |
