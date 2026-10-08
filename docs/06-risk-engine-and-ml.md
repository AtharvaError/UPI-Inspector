# 06 — Risk Engine and ML

[← Index](README.md)

This document is authoritative for formulas, thresholds and weights. It describes the engine as built: rule set `rules-0.6.0` and model `ens-2026.10.07-52648`. The tier thresholds 0.30, 0.60 and 0.85 come from the pitch deck; every other number was a starting value that was then tuned, and the tuned value is what is written here. Measured results are in [12, section 4](12-as-built.md#4-measured-results).

## 1. The pipeline

```
request ─► features (54) ─► rules ──────────────┐
                        ├─► XGBoost ─┐           ├─► composite ─► calibration ─► floors and caps ─► score
                        ├─► Random Forest ─┤     │
                        └─► Isolation Forest ────┘
score ─► tier ─► action
      └► if the tier is not Safe: contributions ─► reasons (≤ 3) ─► scam family ─► signal links
```

Features are defined in [05, section 4](05-data-model.md#4-feature-catalogue-v1).

## 2. Rule engine

Rules are deterministic, versioned in `services/risk-api/rules/rules.yaml`, and reloaded without a restart when the file changes. A rule sets a **floor** (the score cannot go below), a **cap** (cannot go above) or a **bump** (added to the rule score `s_rule`). Three rules are **hard**: they stop the payment whatever the model says, and they still apply when the system is degraded.

| ID | Condition | Effect | Reason given |
|---|---|---|---|
| R-01 | Payee is on the denylist | Floor 0.95, hard | `PAYEE_REPORTED` |
| R-10 | The payee's UPI ID appears in a message this payer flagged as a scam in the last 7 days | Floor 0.95, hard | `PAYEE_IN_SCAM_MESSAGE` |
| R-16 | The payee has confirmed fraud in the last 90 days at this bank, and this payer has never paid it | Floor 0.95, hard | `PAYEE_REPORTED` |
| R-02 | Caller risk is the highest level, the call is active or ended under 10 minutes ago, and the payee is new | Floor 0.85 | `CALLER_FLAGGED` |
| R-03 | New payee, amount over 5× the payer's 30-day p95, and a call with an unknown number is active | Floor 0.60 | `CALL_ACTIVE_UNKNOWN` |
| R-05 | Device first seen under 24 hours ago, new payee, amount over ₹10,000 | Floor 0.60 | `DEVICE_CHANGED` |
| R-15b | Another bank has reported the payee and confirmed the fraud | Floor 0.60 | `REPORTED_AT_OTHER_BANK` |
| R-04b | A personal UPI ID under 7 days old, 20 or more payers in the last hour, at least 80% of the day's payers first-time, a new payee for this payer, and ₹2,000 or more | Floor 0.60 | `PAYEE_FAN_IN` |
| R-04 | A personal UPI ID under 7 days old, 10 or more payers in the last hour, at least 80% of the day's payers first-time | Bump 0.25, floor 0.30 | `PAYEE_FAN_IN` |
| R-17 | New payee, amount over 3× the payer's p95, and ₹5,000 or more | Floor 0.30 | `AMOUNT_UNUSUAL` |
| R-12c | The name the payer was given is an institution's, the account is an individual's, and the payee is new | Floor 0.30 | `NAME_MISMATCH` |
| R-13 | A message flag is active and the payee is new | Bump 0.20, floor 0.30 | `MESSAGE_FLAGGED` |
| R-14 | Payee is one forwarding step from a reported account, or shares a device with one, or forwards 80% or more of receipts within 30 minutes while having 5 or more payers in 24 hours | Bump 0.20, floor 0.30 | `PAYEE_NEAR_REPORTED` |
| R-12b | The claimed name is an institution's and the account is an individual's | Bump 0.30 | `NAME_MISMATCH` |
| R-12a | QR or link payment with a name-match score under 0.5, when R-12b does not apply | Bump 0.20 | `NAME_MISMATCH` |
| R-15a | Another bank has reported the payee | Bump 0.25 | `REPORTED_AT_OTHER_BANK` |
| R-06 | Three or more payments to new payees within 10 minutes | Bump 0.20 | `RAPID_NEW_PAYEES` |
| R-07 | Note text has a scam probability of 0.8 or more | Bump 0.15 | `NOTE_SCAM_LANGUAGE` |
| R-08 | The payment would use 80% or more of the balance | Bump 0.15 | `BALANCE_DRAIN` |
| R-11 | UPI ID was pasted or came from a link, the payee is new, the app was switched 2 or more times, and the amount is above the payer's p95 | Bump 0.15 | `APP_SWITCHING` |
| R-09 | Payee paid at least 3 times over at least 30 days, with no reports and no confirmed fraud | Cap 0.29, unless R-01, R-10 or R-16 fires | — |

R-09 protects the "no friction for safe payments" promise: paying your landlord again should never produce a warning.

Session signals (R-11) come from the client and can only bump; they never set a floor.

What changed from the first version of this table, and why:

- **R-16 is new.** A first payment to an account already confirmed as fraud is declined outright. Before it existed, such a payment could be held and then released.
- **R-17 and R-12c are new, and R-04, R-13 and R-14 gained a floor of 0.30.** The calibrated model score moves in steps and often gave these cases nothing at all. A floor guarantees at least a Warning for the plainest signs.
- **R-04 applies to personal accounts only**, and R-04b adds a Step-Up for the largest bursts. A new shop with a genuine rush of customers is not a mule.
- **R-04b never fires in the simulated history.** The simulated world has under a thousand people, so no account collects twenty strangers in an hour. It fires in the staged demo scenario and in a golden scenario. Its false-positive rate is unmeasured.
- **R-12 and R-15 were each split in two** so that each part has its own identifier in the audit trail.

Format of one rule:

```yaml
- id: R-03
  when: is_new_payee == 1 and amount_over_p95 > 5 and call_active == 1 and caller_unknown == 1
  effect: { floor: 0.60 }
  reason: CALL_ACTIVE_UNKNOWN
```

The `when` expression is parsed once into a syntax tree and checked against a whitelist: comparisons, `and`, `or`, `not`, `in`, numbers, strings and feature names. Nothing else can run.

## 3. Models

| Layer | Model | Purpose | Status |
|---|---|---|---|
| Supervised | XGBoost | Main fraud probability. Also the model that is explained. | Built |
| Supervised | Random Forest | Second opinion, soft-voted with XGBoost for stability. | Built |
| Unsupervised | Isolation Forest | Flags payments unlike anything seen, for patterns not in the training labels. | Built |
| Text | TF-IDF + logistic regression | Scam-language probability for notes and messages. Feeds the supervised models. | Built |
| Text | DistilBERT, multilingual | A stronger text model. | Not built |

Settings as trained:

| Model | Settings |
|---|---|
| XGBoost | depth 5, learning rate 0.05, row and column subsample 0.8, minimum child weight 2, positive-class weight from the class ratio (at most 25), up to 600 trees with early stopping on validation PR-AUC. The current model stopped at 274 trees. |
| Random Forest | 120 trees, depth 10, at least 3 samples per leaf, balanced subsample class weights |
| Isolation Forest | 100 trees, trained on 40,000 legitimate payments, on 15 behaviour features: amount, velocity, balance share, relationship, device, session and hour |

Per the deck, interpretability is preferred over the last fraction of accuracy. Trees stay shallow and the feature set stays small enough to explain.

**Serving.** Models are loaded at start-up from the directory named in `ml/artifacts/ACTIVE`, and reloaded when that file changes. Categorical features are one-hot encoded with a fixed mapping checked against the model's own column list.

**Scoring one payment.** Calling scikit-learn for a single row costs several milliseconds in input checks and job dispatch, about 11 ms for the two forests together and several times that under load. So at load time the Random Forest and the Isolation Forest are each flattened into arrays (children, feature, threshold and leaf value per node), and one payment is scored by walking all trees at once with a few array operations. The flat version is kept only if it reproduces scikit-learn's own output on a set of probe rows to within 1e-9; otherwise the service falls back to scikit-learn. XGBoost is called through `inplace_predict`. Together this takes about 0.5 ms per payment. On 5,000 real rows the single-payment score matched the batch score to within 5e-15.

**Contributions only when needed.** Per-feature contributions come from XGBoost's own contribution output (TreeSHAP), about 0.9 ms. They are computed only when the tier is not Safe.

**The model alone cannot decline.** The calibrated model score is capped at 0.94. A statistical model can pause a payment; only a rule floor, which rests on evidence a person can check, can lift a score to 0.95 and decline it.

## 4. Composite score

```
s_ml    = 0.7 * p_xgboost + 0.3 * p_random_forest
s_anom  = isolation-forest score, as its quantile among legitimate training payments (0 to 1)
s_rule  = min(1, sum of rule bumps)

raw     = sigmoid(b0 + b1 * logit(s_ml) + b2 * s_anom + b3 * s_rule)    # stacker
score   = min(0.94, isotonic_calibration(raw))                           # the model alone cannot decline
score   = min(cap, max(floor, score))                                    # rule floors and caps applied last
```

The stacker and the calibration are fitted on the validation window, which is split in two. A stacker fitted on one half scores the other, so the calibration map is learned from scores the stacker did not train on. A single small fold gave a step function with wide flat stretches.

Current stacker: `b0 = −1.38`, `b1 = 1.86`, `b2 = −0.80`, `b3 = −0.01`. The rule score adds almost nothing once the supervised models have spoken; rules act through floors and caps instead.

The calibrated score still moves in steps (0, 0.25, 0.5, 0.91, 0.96 …), because isotonic regression is a step function and the simulated classes separate cleanly.

If no model is available:

```
score = min(0.94, 0.05 + s_rule)       then floors and caps
```

If a floor and a cap conflict, the floor wins.

## 5. Tier mapping, calibration and action

**Calibration.** The thresholds only mean something if 0.85 really corresponds to a very high fraud probability. Calibrate with isotonic regression on a held-out fold, then check that the share of legitimate payments in each tier is within the budgets in [01, section 10](01-product-overview.md#10-success-metrics). If it is not, recalibrate. Do not move the default thresholds to make the numbers fit.

**Tier.** With the tenant's thresholds `t_warning`, `t_stepup`, `t_block`:

| Condition | Tier |
|---|---|
| score < `t_warning` | `SAFE` |
| `t_warning` ≤ score < `t_stepup` | `WARNING` |
| `t_stepup` ≤ score < `t_block` | `STEP_UP` |
| score ≥ `t_block` | `BLOCK` |

**Action.** Decided in this order:

1. If the decision is **degraded** (cache or model unavailable) and no hard rule fired, cap the tier at `WARNING`.
2. `SAFE` → `ALLOW`. `WARNING` → `WARN`. `STEP_UP` → `STEP_UP`.
3. `BLOCK` → `DECLINE` if any of these holds: score ≥ `t_decline` (0.95); a hard rule fired (R-01, R-10 or R-16); the tenant's `block_mode` is `decline`; the request has a `parent_decision_id` (it already had its hold). Otherwise → `HOLD`.
4. If a **pair block** exists for this payer and payee (a Decline in the last 24 hours), the tier is `BLOCK` and the action `DECLINE`, with the reason `PAIR_BLOCKED` first and the family of the earlier decision.
5. If the tenant's mode is `shadow`, store the decision with `enforced = false` and respond `SAFE` / `ALLOW`.

A `DECLINE` sets the pair block, as does a risky answer at Step-Up. An analyst overturning it in the review queue clears it.

All of this is one function, `core/decide.py`, called by the live service, the golden scenarios and the offline scorer, so they cannot disagree about what a set of features means.

**Guardian requirement.** After the action is known, and only when enforcing: if the payer has an active guardian,

- mode `notify`: create a `NOTIFY` request when the tier is `STEP_UP` or `BLOCK`;
- mode `co_approve`: additionally create an `APPROVAL` request when the action is `STEP_UP`, or when the action is `ALLOW` or `WARN`, the payee is new and the amount is at or above the payer's threshold. In the second case the app shows the waiting screen before the PIN.

## 6. Payee reputation

Kept per observing bank ([05, section 1.2](05-data-model.md#12-payees)).

**Decayed counters.** Each is brought forward and incremented on every event:

```
C(t) = C(t_prev) * 2^(-(t - t_prev) / half_life) + x
```

| Counter | Increment | Half-life |
|---|---|---|
| `recv_decayed` | 1 per inbound payment that completed | 30 days |
| `bad_decayed` | 5 per confirmed fraud; 1 per credible report; 1 when the UPI ID is flagged in messages by 2 distinct users; the near-miss weight (0.3 or 0.6) per near-miss | 30 days |

Both counters share one half-life on purpose. If inbound payments decayed faster than bad events, the bad-event rate of a UPI ID that went quiet would climb with no new evidence.

**Derived values.**

```
p_bad       = (0.5 + bad_decayed) / (50 + recv_decayed)         # smoothed rate, prior mean 1%
maturity    = 1 - exp(-recv_decayed / 20)                       # how much history backs the estimate
trust_score = round(100 * (1 - min(1, p_bad / 0.10)) * (0.5 + 0.5 * maturity))
```

**Bands**, evaluated in this order:

1. `REPORTED` if there is a confirmed fraud in the last 90 days. A high rate alone is never enough.
2. `CAUTION` if `p_bad` is 2% or more, or the UPI ID is in a **burst**: a personal, unverified account under 7 days old with 10 or more payers in the last hour, at least 80% of them first-time. A burst also limits the trust score to 30.
3. `NO_HISTORY` if the UPI ID is unknown, was first seen under 30 days ago, or has received fewer than 3 payments in total.
4. `TRUSTED` if it has next to no bad events, `maturity` ≥ 0.8, and is at least 90 days old. In practice this is established merchants.
5. `NEUTRAL` otherwise. Most personal UPI IDs sit here.

A brand-new UPI ID scores about 45 and shows "No history", not a warning. Whether a payee is familiar *to this payer* is a separate signal, handled by the relationship features and R-09.

**Sliding windows.** `fan_in_1h`, `fan_in_24h` and `first_time_payer_ratio_24h` are exact windows, not decayed, because the mule pattern is a burst.

**Band reason.** The one line shown on a Caution chip is chosen in this order: reports, fan-in (a burst, or 5 or more payers in the last hour), near-misses, then unusual activity. A Reported chip says the UPI ID has been reported for fraud.

**Protection against abuse.** `REPORTED` needs confirmed fraud: an analyst's confirmation, or the simulator's ground truth in seeded history. Reports and near-misses alone hold the band at `CAUTION`. A payer may report only their own payment. The per-reporter limits in the first plan (one report per UPI ID, five a day) were **not built**.

## 7. Call correlation

The flag is keyed by the hashed number of the person called.

- **Window.** While the call is active and for 30 minutes after it ends.
- **Features.** `call_active`, `mins_since_call_end`, `call_duration_s`, `caller_unknown`, `caller_risk` (0 to 3), `unknown_calls_24h`.
- **Caller risk.** Looked up in `caller_risk_list` when the call event arrives: the seeded list, plus numbers promoted from flagged messages (section 8.1).
- **Linking.** The orchestrator writes a `signal_links` row of type `call` when R-02 or R-03 fired, or when a call dimension is among the top three contributors.

Digital-arrest scams keep the victim on the line for a long time, so `call_active` and `call_duration_s` are features in their own right.

Known limit: calls on internet-calling apps are invisible to telecom data. Coaching signals (section 8.2) cover part of that gap.

## 8. Message, session and name signals

### 8.1 Message flags (F-16)

**Classification.** The text model (section 9) returns a label and `scam_prob`.

**Entity extraction**, by pattern:

| Entity | Pattern, in words |
|---|---|
| UPI ID | Letters, digits, dot, hyphen or underscore, then `@`, then a letters-only handle of 2 to 64 characters. Email addresses are excluded by rejecting handles that contain a dot. |
| Phone | An Indian mobile number: optional `+91` or `0`, then ten digits starting 6 to 9 |
| Link | Any `http(s)://` URL, and bare domains from a list of URL shorteners |
| Amount | `₹`, `Rs` or `INR` followed by digits with optional commas |

**Flag.** When `scam_prob` ≥ 0.7: set `msg_flag:{user_id}` for 24 hours, add the extracted UPI IDs to `msg_vpas:{user_id}` for 7 days, and upsert each entity in `flagged_indicators`.

**Promotion.** When a UPI ID reaches 2 distinct reporters in a tenant, add 1 to its `bad_decayed` in that tenant's view. When a phone number reaches 2 distinct reporters, add it to `caller_risk_list` at medium risk; at 5, high.

**Features.** `msg_flag_active`, `msg_scam_prob`, `mins_since_msg_flag`, `payee_in_flagged_msg`.

**Linking.** A `signal_links` row of type `message` when R-10 or R-13 fired, or a message dimension is among the top three contributors.

### 8.2 Session signals (F-18)

Values are taken from the request and clamped: `app_switch_count` to 0–20, `time_on_pay_screen_s` to 0–3600, `amount_edit_count` to 0–20. An unknown `vpa_entry_method` is treated as `typed`.

A long call, a pasted UPI ID and repeated app switching together are the signature of someone following instructions. The model learns the combination; R-11 guarantees a minimum response to the clearest form of it.

### 8.3 Name match (F-22)

1. **Normalise** both names: lower-case, strip punctuation, remove honorifics and company suffixes (`mr`, `mrs`, `shri`, `smt`, `m/s`, `pvt`, `ltd`, `llp`), collapse spaces.
2. **Score** with a token-set similarity ratio, scaled to 0–1. Word order and extra middle names do not reduce it much.
3. `name_match_score` = 1 when no claimed name was supplied.
4. `claimed_name_institutional` = 1 if the normalised claimed name contains a word from the institutional lexicon: bank and brand names, `refund`, `customer care`, `support`, `helpdesk`, `police`, `cyber cell`, `cbi`, `customs`, `court`, `income tax`, `kyc`, `electricity`, `bijli`, `rbi`, `npci`, `courier`.

## 9. Text classification

Used for payment notes (F-06) and pasted messages (F-16).

**Classes.** `benign`, `refund_bait`, `authority_threat`, `kyc_bill_threat`, `investment_task`, `impersonation_request`, `prize`.

**Scam probability.** `1 − P(benign)`.

**Class to scam family.**

| Class | Family |
|---|---|
| `refund_bait` | `FAKE_REFUND` |
| `authority_threat` | `DIGITAL_ARREST` |
| `kyc_bill_threat` | `KYC_BILL_THREAT` |
| `investment_task` | `INVESTMENT_TASK` |
| `impersonation_request` | `IMPERSONATION` |
| `prize` | `UNCLASSIFIED` |

**Lightweight model (Must).** TF-IDF over character n-grams (2 to 5) and word n-grams (1 to 2), logistic regression with class weights. Under a millisecond per text, and tolerant of Hinglish, transliteration and misspellings.

**DistilBERT (Could).** `distilbert-base-multilingual-cased`, fine-tuned on the same data, exported to ONNX with 8-bit quantisation, input capped at 64 tokens. If it cannot hold 25 ms p95 on a laptop CPU, it runs off the hot path and its result is cached by text hash, with the lightweight model as the synchronous answer.

**Training data.** Generated from templates per class with slot filling (names, amounts, banks, deadlines, links) and surface variation (English, Hinglish, Hindi in Devanagari, abbreviations, missing spaces, emoji). About 2,000 texts per class. Benign texts include the hard cases: genuine bank OTP messages, delivery notices, real bill reminders, a friend asking to split a bill.

**Test set.** 40 hand-written messages (20 scam, 20 benign) that share no template with the training data. The message-checker metric in [01, section 10](01-product-overview.md#10-success-metrics) is measured on this set.

**Red-flag lexicon**, used for highlighting only:

| Kind | Examples | Explanation shown |
|---|---|---|
| Urgency | "immediately", "within 24 hours", "turant", "last warning" | "Pressure to act fast is a common scam tactic." |
| Authority | "police", "CBI", "customs", "court", "arrest warrant" | "Real authorities do not contact you this way." |
| Threat | "account will be blocked", "connection will be cut", "legal action" | "Threats are used to stop you thinking." |
| Refund bait | "refund", "cashback", "claim your money" | "You never need to pay to receive money." |
| Secrets | "share OTP", "enter UPI PIN", "verify PIN" | "No one legitimate asks for your PIN or code." |
| Link | Shortened or look-alike links | "Shortened links hide where they really go." |
| Secrecy | "do not tell anyone", "stay on the call" | "Being told to keep it secret is a warning sign." |

## 10. Explainability

1. When the tier is not Safe, take the per-feature contributions from XGBoost.
2. Sum them into the eleven **display groups** below.
3. Build the reason list: reasons attached to fired rules first, then reasons for the display groups with the largest positive contribution, skipping duplicates, until there are three.
4. Render each from its template in the payer's locale, filling in evidence values.

Display groups: payee, network, call, message, amount, relationship, device, session, name, note, time.

### Reason codes

| Code | Group | English template |
|---|---|---|
| `NEW_PAYEE` | relationship | You have never paid this UPI ID before. |
| `PAYEE_NEW_VPA` | payee | This UPI ID was created {vpa_age} ago. |
| `PAYEE_FAN_IN` | payee | {fan_in_1h} different people have paid this UPI ID in the last hour. |
| `PAYEE_REPORTED` | payee | This UPI ID has been reported for fraud. |
| `PAYEE_NEAR_REPORTED` | network | Money sent to this UPI ID is quickly passed on to an account reported for fraud. |
| `PAYEE_PASSTHROUGH` | network | This UPI ID forwards almost everything it receives within minutes. |
| `REPORTED_AT_OTHER_BANK` | payee | This UPI ID has been reported by customers of another bank. |
| `CALL_ACTIVE_UNKNOWN` | call | You are on a call with a number that is not in your contacts. |
| `CALLER_FLAGGED` | call | The number that called you {mins_since_call} ago is flagged for fraud. |
| `MESSAGE_FLAGGED` | message | You recently received a message that looks like a scam. |
| `PAYEE_IN_SCAM_MESSAGE` | message | This UPI ID appeared in a message you flagged as a scam. |
| `AMOUNT_UNUSUAL` | amount | This is {amount_ratio} times larger than your usual payment. |
| `BALANCE_DRAIN` | amount | This payment would use most of your balance. |
| `DEVICE_CHANGED` | device | You are paying from a device first used {device_age} ago. |
| `RAPID_NEW_PAYEES` | relationship | This is your {n} payment to a new UPI ID in 10 minutes. |
| `VPA_PASTED` | session | You pasted this UPI ID instead of choosing a saved contact. |
| `APP_SWITCHING` | session | You switched to another app {app_switch_count} times while making this payment. |
| `NAME_MISMATCH` | name | The {source} says "{claimed_name}" but this UPI ID is registered to "{registered_name}". |
| `NOTE_SCAM_LANGUAGE` | note | The payment message uses wording common in {family_name} scams. |
| `ODD_HOUR` | time | You do not usually make payments at this time. |

Rules for reason text:

- State what was observed. Never accuse the payee of a crime; `PAYEE_REPORTED` is used only for the `REPORTED` band.
- A reason is used only when its own evidence is true. `PAYEE_FAN_IN` needs `fan_in_1h` ≥ 5, however large the payee group's contribution.
- If no reason qualifies for a non-Safe decision, use `NEW_PAYEE` when true, otherwise `AMOUNT_UNUSUAL`, so that coverage is always 100%.

## 11. Scam taxonomy and attribution

Each family is a **prototype vector** in a 24-dimension signal space.

| # | Dimension | Built from |
|---|---|---|
| 1 | `call_active` | `call_active` |
| 2 | `call_recent` | call ended under 30 minutes ago |
| 3 | `caller_risk` | `caller_risk` / 3 |
| 4 | `caller_unknown` | `caller_unknown` |
| 5 | `msg_flag` | `msg_flag_active` × `msg_scam_prob` |
| 6 | `payee_in_msg` | `payee_in_flagged_msg` |
| 7 | `new_payee` | `is_new_payee` |
| 8 | `payee_young` | UPI ID under 30 days old |
| 9 | `payee_fan_in` | fan-in features |
| 10 | `payee_reports` | reports, confirmed fraud, `p_bad` |
| 11 | `payee_network` | graph features |
| 12 | `consortium` | `consortium_reported` |
| 13 | `amount_anomaly` | `amount_z`, `amount_over_p95` |
| 14 | `balance_drain` | `balance_fraction` |
| 15 | `velocity` | counts in the last hour and 10 minutes |
| 16 | `new_device` | `device_is_new` |
| 17 | `coaching` | pasted or linked entry, app switching |
| 18 | `name_mismatch` | 1 − `name_match_score`, institutional claim |
| 19 | `note_refund` | text class probability |
| 20 | `note_authority` | text class probability |
| 21 | `note_kyc` | text class probability |
| 22 | `note_invest` | text class probability |
| 23 | `note_impersonation` | text class probability |
| 24 | `odd_hour` | hour outside the payer's usual hours |

The note dimensions use the larger of the payment note's and the flagged message's class probabilities. The name the payer was given counts too: a claimed name containing a word from a short list per story ("refund", "cyber", "kyc", "trading", "suppliers" and the like) sets that story's dimension to at least 0.8.

Two indicators are graded, not 0 or 1. `call_active` is 0.4 at the start of a call and rises to 1.0 at twenty minutes, because being kept on a call is the mark of a digital arrest and a short call is not. `payee_network` is 1.0 for a forwarding link or shared device, 0.7 for heavy pass-through, 0.4 for membership of a risky community.

**The payment's vector.** For each dimension: the positive model contribution of its features, scaled to 0–1 across the vector, blended with the indicator. The indicator's weight is chosen at training time.

**Prototypes.** They start as 1.0 for defining dimensions, 0.5 for supporting ones, 0 elsewhere:

| Family | Defining (1.0) | Supporting (0.5) |
|---|---|---|
| `DIGITAL_ARREST` | `call_active`, `caller_risk`, `caller_unknown`, `new_payee`, `amount_anomaly`, `balance_drain` | `note_authority`, `coaching`, `payee_young` |
| `FAKE_REFUND` | `call_recent`, `note_refund`, `name_mismatch`, `new_payee` | `caller_unknown`, `msg_flag`, `coaching`, `payee_young` |
| `MULE_TRANSFER` | `payee_young`, `payee_fan_in`, `payee_reports`, `payee_network` | `consortium`, `new_payee` |
| `IMPERSONATION` | `new_payee`, `amount_anomaly`, `note_impersonation` | `name_mismatch`, `msg_flag`, `coaching` |
| `KYC_BILL_THREAT` | `msg_flag`, `note_kyc`, `new_payee` | `caller_risk`, `call_recent`, `name_mismatch` |
| `INVESTMENT_TASK` | `note_invest`, `velocity` | `msg_flag`, `new_payee`, `amount_anomaly`, `payee_fan_in` |

Training replaces each with the mean vector of that family's scam payments in the training and validation windows, once there are at least 30. The served prototypes are these learned ones, stored with the model.

**Attribution** has three parts.

1. **Similarity.** Weighted cosine similarity against each prototype. The five story dimensions (`note_*`) carry a weight; the rest carry 1. Nearly every scam is a first payment of an unusual amount to a young account, so those dimensions agree with every family and say nothing about which. What the note, the message and the claimed name say is what tells families apart.
2. **Threshold.** A family is a candidate only at similarity 0.6 or more.
3. **Evidence.** Going down the candidates from the closest, the first whose own defining evidence is present is attached. If none qualifies, the family is `UNCLASSIFIED` and the payer is told only that the payment looks unusual.

| Family | Evidence required |
|---|---|
| `DIGITAL_ARREST` | At least five minutes into an active call with an unknown or flagged number; or any call in the window from a number at risk level 2 or 3; or an authority story |
| `FAKE_REFUND` | A refund story |
| `MULE_TRANSFER` | Evidence about the payee: 5 or more payers in the hour with most of them first-time; a report, confirmed fraud or a bad-event rate of 2% or more; a forwarding link, a shared device or heavy pass-through; or a consortium report. Membership of a risky community alone is not enough. |
| `IMPERSONATION` | An impersonation story |
| `KYC_BILL_THREAT` | A KYC or bill story |
| `INVESTMENT_TASK` | An investment or task story |

A "story" is that dimension at 0.5 or more: from the note, a flagged message, or the claimed name.

The evidence step was added after the built app told a payer that an ordinary first payment to a friend "looks like a pass-through account". Similarity alone had matched the mule prototype on `new_payee` and `amount_anomaly`. Naming a family says something specific, so it needs something specific behind it.

**Two settings are chosen before the test window is looked at:** the indicator's weight in the blend (0.5, 0.7, 0.85 or 1.0) and the story weight (1, 1.5, 2 or 3). They are picked by five-fold cross-validation over the training and validation scams, grouped by campaign so that a campaign never helps to attribute itself. The current model uses 0.85 and 2.

**If nothing is named but a message flag is active,** the family of the flagged message is used.

**Wording that matches the evidence.** The usual mule-transfer text says many new people paid the account in the last hour. When that is not what was seen (the family was named on a report or a forwarding link), a variant is used: "This UPI ID looks like a pass-through account." The usual impersonation text says the payer has never paid this UPI ID; when they have, a variant drops that sentence. The variant is stored with the decision so that the Step-Up, the hold list and a later pair block all use the same wording.

## 12. Network graph features

Computed by the graph job every 120 seconds over the last 30 days of completed payments.

**Graph.** Nodes are UPI IDs. A directed **payment edge** runs from payer to payee. An undirected **device edge** joins two UPI IDs used from the same device fingerprint.

A payment edge is a **forwarding edge** when the sender received money in the hour before it and the amount sent is between 0.5 and 1.2 times what was received. That is the mark of money being passed on.

| Feature | Definition |
|---|---|
| `downstream_hop` | Length of the shortest path of **forwarding edges** from this UPI ID to one with confirmed fraud. 1 to 3; 9 if none within 3. |
| `shares_device_with_reported` | A device edge to a UPI ID with confirmed fraud. |
| `passthrough_ratio` | Over the last 24 hours: amount sent out within 30 minutes of an inbound payment, divided by total amount received. |
| `community_risk_share` | Communities are found on the undirected graph by modularity (Louvain). The feature is the share of the community's UPI IDs in the `CAUTION` or `REPORTED` band. Communities under 3 nodes score 0. |

**Why forwarding edges.** A victim who paid a mule is one step from it too. The first version followed every outgoing payment and marked victims, and anyone they went on to pay, as part of the chain. Following only forwarded money, and shared devices, points at the mule chain and not at its victims. AT-19 checks both halves.

Simplification: the prototype builds one graph from all simulated payments. A real bank sees outgoing flows only for its own account holders.

## 13. Consortium signals

**Token.** `vpa_token = HMAC-SHA256(consortium_key, lower-cased UPI ID)`. All members hold the key.

**Publish.** When a tenant's view of a UPI ID has `bad_decayed` ≥ 2 and either a confirmed fraud or the `CAUTION` band or worse, upsert a row in `consortium_signals` with the token, the decayed counters, the band and whether fraud is confirmed. Triggered by `reputation.updated`.

**Consume.** For every other tenant, write `consortium:{tenant_id}:{vpa_token}`. Enrichment computes the token for the payee and reads the key.

**Features.** `consortium_reported` (0/1), `consortium_p_bad` (the source's smoothed rate).

A signal never changes the receiving bank's own counters. It is a separate input, so one bank's mistake cannot silently become another's.

## 14. Synthetic simulator

There is no public UPI fraud dataset with linked call and message signals. The simulator is a first-class deliverable: every model, demo and load test depends on it.

### 14.1 Entities

| Entity | Attributes |
|---|---|
| Users | Tenant, segment, age band, locale, device(s), balance, usual hours, amount distribution |
| Merchants | Verified or not, age, steady inbound volume |
| Peers | Personal UPI IDs paid by a small circle |
| Mule accounts | Young, bursty fan-in, fast forwarding to a next-layer account, devices shared within a ring |
| Scam campaigns | Family, caller numbers and their risk, message templates, claimed names, mule set, target segments |

### 14.2 Legitimate behaviour

Payments arrive per user following a daily rhythm. Amounts are log-normal per segment. About 85% of payments go to payees the user has paid before. Session signals look ordinary: payees picked from contacts or scanned, zero or one app switch, 10 to 40 seconds on the pay screen.

### 14.3 Scam injection

For each victim, a campaign emits, in order:

1. Optionally a scam message (delivered; a share of victims check it in the app).
2. One or more call events, with the campaign's caller risk.
3. A payment to a mule, after a family-specific delay: during the call for digital arrest; minutes later for refund scams; days of small payments then a large one for investment scams.
4. Session signals consistent with being coached: pasted or linked UPI ID, several app switches, a long time on the pay screen.
5. A claimed name that matches the campaign's story.

A share of victims abort, which produces near-misses. A share report afterwards.

### 14.4 Hard negatives and weakened scams

These decide whether the false-positive numbers mean anything. The first simulator had none worth the name: one tree separated the classes perfectly, on the age of the payee's account. Each item below was added because a version of the model was leaning on one easy signal.

Honest payments that look risky:

- A first-time, high-value payment: rent deposit, tuition, medical bill.
- A payment made while on a call with a known contact, and one made on a long call with an unknown number.
- A payment made after pasting a UPI ID a friend sent in chat.
- A new merchant with a genuine rush of first-time customers (five launch during the period).
- New users and new shops throughout, so a young account is not in itself a sign.
- A payment from a new phone.
- A bank or brand service point run by an individual, so an institutional name sits on a personal account, and is paid regularly.
- People who collect money from friends and pay it on within minutes.
- New individual sellers taking advances, with notes such as "advance for booking".
- A family member asking for urgent help from a new number.
- A payer who checked a real bank message (benign verdict, no flag).

Scams that look ordinary:

- **Weak variants** of each campaign, with the family's own signals removed: no call, no note, a typed UPI ID.
- **Unseen variants**, held out of training entirely.
- **Rented mules:** about 1% of ordinary users whose long-standing accounts are used for a while.

### 14.5 Outputs and scale

As run for the current model (`python manage.py simulate --users 800 --days 90 --campaigns 66`):

| | |
|---|---|
| Users | 951: 800 ordinary people, 5 demo users, and the mule and demo-payee accounts. About 60% are at Bank A. |
| UPI IDs | 1,208, including 250 merchants |
| Scam campaigns | 66 across six families |
| Events over 90 days | 113,379: payments, calls, messages, reports |
| Payments | 99,222, of which 2,486 are scam payments (2.5%) |
| Training rows after replay | 97,217 |
| Formats | Parquet for training; PostgreSQL for the service |

The fraud share is far above reality on purpose, so that 90 days of a small world holds enough scams to learn from. Precision is therefore reported at a realistic rate (section 15).

Every synthetic payment carries `truth_is_fraud`, `truth_family` and `campaign_id`. These are used for evaluation and the policy lab, and are never given to the model as features.

**Training features equal serving features.** The simulated events are replayed in time order through the real feature store on a simulated clock, and the feature vector is taken at the moment each payment is assessed. The model is trained on exactly what the service would have computed.

## 15. Training and evaluation

- **Split by time.** Train on payments more than 30 days before the end of the data (59,711 rows), validate on days 30 to 15 (18,008), test on the last 15 (19,498, with 742 scams). A random split would leak campaigns and inflate every number.
- **Unseen variants.** A campaign that starts inside the test window is, about one time in three, built as a variant that training never saw: for a digital arrest the call leaves no trace and there is no note or claimed name; for a mule transfer the account is weeks old and there is no burst. In the current test window the unseen campaigns were fake refund, impersonation, KYC threat and mule transfer (135 scam payments); no unseen digital-arrest or investment campaign fell in it. Those four kept enough of their story to be caught and named, so the unseen-variant figures say little about a truly new scam.
- **Labels** are the simulator's ground truth. Near-misses and analyst labels are stored but the current trainer does not use them.
- **False-positive budgets are judged without payees that have confirmed fraud.** An honest person's first payment to such an account is meant to be stopped.
- **Precision is reported at a realistic fraud rate**, since precision depends on it:
  `precision = (TPR × π) / (TPR × π + FPR × (1 − π))`, with π = 0.05%. At that rate about 8% of payments at Step-Up or above would be scams.
- **Ranking quality is measured on the stacked model output**, not the final score, which has ties by design (rule floors and the 0.94 cap).
- **Reported for every model version:** PR-AUC; recall at Warning, Step-Up and Block; share of legitimate payments per tier, with and without confirmed-fraud payees; recall on unseen and weak variants; recall by family; attribution accuracy, the share named, precision when named, and how often a family is named on an honest payment; single-payment scoring time; the most important features.
- **Golden scenarios.** 44 frozen scenarios ([10, section 3](10-testing-and-demo.md#3-golden-scenarios)).

The current results are in [12, section 4](12-as-built.md#4-measured-results). State plainly when presenting: these metrics come from synthetic data that we generated. They prove the pipeline works end to end. They are not a claim about real-world performance.

## 16. Retraining

`python manage.py train`:

1. Read the training rows (the stored feature vectors from the replay).
2. Train the three models; fit the stacker and the calibration; learn the prototypes and the two attribution settings.
3. Evaluate on the test window, and score the same window with the active model for comparison.
4. Save the artefacts under `ml/artifacts/<version>/` and register the version with its metrics in `model_registry`.
5. **Gates**, all required: PR-AUC not more than 0.005 below the active model's; the three false-positive budgets met (Step-Up or Block ≤ 1%, Block ≤ 0.1%, Warning ≤ 5%); single-payment scoring p95 at most 40 ms.
6. Promote if every gate passes: write the version to `ml/artifacts/ACTIVE`. The running service notices within a few seconds and loads it.

The golden scenarios are not a gate inside the trainer. Run `python manage.py golden` after training, and `python manage.py backfill` to re-score the last 15 days for the dashboard.

The trainer learns from the simulator's rows. **Retraining from live decisions with analyst labels and near-misses, as the first plan described, was not built.**

## 17. Drift and fairness monitoring

### 17.1 Drift (F-26)

For the score and the 15 most important numeric features, compare the last 24 hours of decisions with the training baseline using ten quantile bins fixed at training time:

```
PSI = Σ over bins of (actual_share − expected_share) × ln(actual_share / expected_share)
```

| PSI | Status |
|---|---|
| below 0.1 | stable |
| 0.1 to 0.2 | watch |
| above 0.2 | alert, and a `drift.alert` event |

Also tracked hourly: the share of decisions per tier, and the degraded-decision rate. A sudden change in tier mix usually means a data or model problem, not a wave of fraud.

### 17.2 Fairness

For each of three dimensions (age band, segment, locale) and each group in it, over **legitimate** payments only:

- share at Warning, at Step-Up, at Block;
- false-positive rate at Step-Up or above.

**Disparity ratio** = highest group rate ÷ lowest group rate, per dimension. Above 1.5 is flagged. Groups with fewer than 200 legitimate payments in the window are shown but not used for the ratio.

In the prototype, "legitimate" comes from the simulator's ground truth. In a pilot it would come from the absence of a confirmed complaint, which is a weaker label.

## 18. Policy simulation

Input: a time window and three candidate thresholds. Data: stored decisions in the window with a known label (simulator ground truth, or an analyst label).

For candidate thresholds `t1 < t2 < t3`:

| Output | Formula |
|---|---|
| Scams caught | share of fraud with score ≥ `t2` |
| Scams warned only | share of fraud with `t1` ≤ score < `t2` |
| Scams missed | share of fraud with score < `t1` |
| Legitimate at Warning, Step-Up, Block | share of legitimate payments in each band |
| Interruptions per 1,000 payments | 1000 × share of all payments with score ≥ `t1`, re-weighted to the chosen real-world fraud rate |
| Rupees protected | sum of amounts of fraud with score ≥ `t2` |
| Precision at Step-Up or above | the formula in section 15, at the chosen fraud rate |

The trade-off curve is produced by sweeping `t2` from 0.05 to 0.95.

Rule floors and caps are already inside each stored score, so the simulation re-buckets scores; it does not re-run rules. Constraint enforced on apply: `0 < t1 < t2 < t3 < t_decline ≤ 1`.

## 19. Case summaries

Generated after the decision by a background worker, for Step-Up and Block only. Never an input to any decision.

**Input.** A JSON object built from the decision: amount, tier, action, score, reasons in English, fired rules, display-group contributions, scam family and similarity, payee band and counters, graph features, session signals, name match, linked calls and messages with times and ratings, outcome if known. The payer, payee and phone numbers are replaced with tokens such as `PAYER_1` and `PAYEE_1`.

**Prompt.**

```
You are assisting a bank fraud analyst. Using ONLY the facts in the JSON below, write:
1. "summary": 3 to 4 plain sentences describing what happened and why the payment was flagged.
2. "key_evidence": up to 4 short bullet strings, each a fact from the JSON.
3. "suggested_action": one sentence, chosen from: confirm as fraud, mark as false positive,
   contact the customer, monitor the payee, no action needed.
Do not add facts, numbers, names or causes that are not in the JSON. Do not speculate about identity or intent.
Reply with a JSON object with exactly those three keys.

<decision JSON>
```

**Checks before saving.** The reply parses as JSON with the three keys. Every number in the reply appears in the input. No token outside the input's set appears. A reply that fails is discarded and the next provider is tried.

**Before it is shown,** `PAYER_1` and `PAYEE_1` are replaced with "the payer" and "the payee". The analyst sees who they are on the same page; the summary itself names no one.

**Providers, in order.** Gemini, Groq, a local Ollama model, then a fixed template (`LLM_PROVIDERS`). A provider with no key is skipped. The template builds the summary from the reasons and the family name and always succeeds. **Only the template has been run:** no key was configured during the build, so the three model providers are untested. Provider choice and limits are in [08](08-tech-stack-and-free-services.md).

Only synthetic data is sent. Free tiers may use submitted content to improve their products, which rules them out for real customer data.
