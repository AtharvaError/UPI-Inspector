# 07 — API Reference

[← Index](README.md)

The machine-readable contract is `contracts/openapi.yaml`, written from the running code by `python manage.py export-openapi` (101 paths). The same reference is served at `http://localhost:8000/docs`. Where this document and that file differ, the file is right. The two front ends call the API with small hand-written clients; they are not generated from the contract.

## 1. Conventions

| Topic | Rule |
|---|---|
| Base path | `/v1` on the `risk-api` service. Mock PSP endpoints are under `/mock-psp/v1`. |
| Format | JSON, UTF-8. Times are ISO 8601 with offset. Amounts are numbers in rupees with two decimals. |
| Tenant | Taken from the caller's token. Never sent in a body. |
| Locale | `locale` in the body, or the `Accept-Language` header; falls back to the user's stored locale, then `en`. |
| Idempotency | `POST /v1/risk/assess` is idempotent on the payer and `txn_id` together for 10 minutes. Filing the same fraud report or caller report twice returns the first. Other writes are not idempotent; the `Idempotency-Key` header in the first plan was not built. |
| Pagination | `?limit=` on list endpoints. Cursors were not built; lists return the newest rows up to the limit. |
| Rate limits | 1,200 requests a minute per address (service keys exempt), and per-person limits on sign-in, one-time codes, assessments, payments, look-ups, reports and packs ([11, section 1.5](11-security-risks-and-decisions.md#15-the-front-door)). All answer `429` with `retry_in_s`. |
| Size and host | A body over 64 KB gets `413`. A request to a host name the service is not meant to answer to gets `400 BAD_HOST`. |
| Headers | Every response carries the security headers and `Cache-Control: no-store`. |
| Documentation | `/docs` and `/openapi.json` exist only with `DEMO_MODE=true`. `contracts/openapi.yaml` is exported from the code (101 paths). |

### 1.1 Authentication

| Caller | How | Token contents |
|---|---|---|
| Payer app | `Authorization: Bearer <token>` from sign-in with phone and app PIN, from registering, or from demo sign-in | `sub` = user ID, `kind` = `user`, `tenant_id` |
| Dashboard | `Authorization: Bearer <token>` from staff sign-in. An admin may add `X-Tenant: bank_a` or `bank_b` to act on the other bank. | `sub` = staff ID, `kind` = `staff`, `tenant_id`, `role` (`admin`, `analyst`, `auditor`) |
| Simulator, mock PSP, call source | `X-API-Key: <key>` | Mapped to a service name and tenant |

Tokens are signed JWTs. An app token lasts 30 minutes and a dashboard token 60; both are renewed while the person is active, and a session ends 12 hours after sign-in ([11, section 1.2](11-security-risks-and-decisions.md#12-sessions)).

Every endpoint that issues a token returns the same **session grant**, with `user` or `staff` beside it:

```json
{ "token": "eyJ…", "expires_in_s": 1800, "session_ends_at": 1791417695, "auth_method": "pin", "user": { "user_id": "…", "name": "…", "vpa": "…" } }
```

`auth_method` is `pin`, `password` or `demo`.

| Endpoint | Body | Returns | Notes |
|---|---|---|---|
| `GET /v1/auth/config` | — | `{ demo_mode, demo_login, tenants, session }` | No token needed. `demo_login` says whether **this caller** is offered passwordless demo sign-in. |
| `POST /v1/auth/login` | `{ phone, pin, device? }` | Grant, `user` | App users. `pin` is six digits. `device` is `{ key_id, secret, label? }`: sent by a phone, it enrols that phone (section 3). `401` for a wrong number or PIN alike. |
| `POST /v1/auth/register` | `{ name, phone, pin, locale, tenant_id, age_band, device? }` | Grant, `user` | Creates the account, a UPI ID and a simulated balance of ₹50,000. `400 WEAK_PIN` with the reason for an easy PIN. |
| `POST /v1/auth/staff-login` | `{ email, password }` | Grant, `staff` | |
| `GET /v1/auth/me` | — | The caller's profile and `session: { expires_at, session_ends_at, auth_method }` | |
| `POST /v1/auth/refresh` | — | Grant | A new token for the same session. `401` once the session has reached its end. |
| `POST /v1/auth/logout` | — | `{ signed_out }` | Revokes this session at once, sockets included |
| `POST /v1/auth/logout-all` | — | `{ signed_out }` | Refuses every token the account holds, this one included |
| `POST /v1/auth/verify-pin` | `{ pin }` | `{ ok: true }` | The app lock. `403 WRONG_PIN` with `tries_left`. The fifth wrong PIN locks the account and ends the session (`401`). |
| `POST /v1/auth/change-pin` | `{ current_pin, new_pin }` | Grant, `user` | Signs every other device out. `403 WRONG_PIN`, `400 WEAK_PIN`. |
| `POST /v1/auth/change-password` | `{ current_password, new_password }` | Grant, `staff` | Staff. `403 WRONG_PASSWORD`, `400 WEAK_PASSWORD`. |
| `POST /v1/auth/device-key` | `{ pin, device }` | `{ enrolled, key_id }` | Enrol a phone after sign-in. Needs the PIN. |
| `GET /v1/auth/devices` | — | `{ devices: [{ key_id, label, created_at, last_used }] }` | |
| `DELETE /v1/auth/devices/{key_id}` | — | `{ removed }` | |
| `GET /v1/auth/demo-users` | — | List of demo users | Demo mode, and only from where `DEMO_LOGIN` allows; otherwise `404` |
| `POST /v1/auth/demo-login` | `{ user_id }` | Grant, `user` | As above |
| `GET /v1/auth/demo-staff` | — | List of seeded staff | As above |
| `POST /v1/auth/demo-staff-login` | `{ email }` | Grant, `staff` | As above |

Five wrong passwords or PINs in a row lock that account (`429` with `retry_in_s`): five minutes the first time, twice as long each time after, up to a day. A deactivated account gets `403`, and its existing tokens stop working. A token is refused with `401` if it is expired, its session was signed out, or the account's PIN, password or role changed after it was issued.

Administration, for admin staff only:

| Endpoint | Body | Effect |
|---|---|---|
| `GET /v1/admin/staff` | — | Staff of the admin's bank |
| `POST /v1/admin/staff` | `{ name, email, role, password }` | Add a staff member. `400 WEAK_PASSWORD` if the password is under 10 characters or easy to guess. |
| `PATCH /v1/admin/staff/{id}` | `{ role?, active?, name? }` | Change role or name; deactivate or reactivate. An admin cannot demote or deactivate themselves. |
| `POST /v1/admin/staff/{id}/reset-password` | `{ password }` | Set a new password and sign that person out everywhere |
| `GET /v1/admin/users` | `?q=` | App users of the bank |
| `PATCH /v1/admin/users/{id}` | `{ active }` | Deactivate or reactivate an app user |
| `GET /v1/security/posture` | — | Admin and auditor. `{ overall, checks: [{ key, label, status, detail }], events_24h, recent, counts }`: the configuration checks, security events and locked accounts shown on D-14 |

### 1.2 Errors

```json
{ "error": { "code": "VALIDATION_ERROR", "message": "amount must be greater than 0", "details": [{ "field": "amount" }] } }
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `VALIDATION_ERROR` | The body does not match the schema |
| 400 | `WEAK_PIN`, `WEAK_PASSWORD`, `BIOMETRIC_NOT_ENROLLED`, `BAD_HOST` | The message says what to change |
| 401 | `UNAUTHENTICATED` | Missing or invalid token or key |
| 403 | `FORBIDDEN` | Role or ownership does not allow it |
| 403 | `WRONG_PIN`, `WRONG_PASSWORD` | A wrong PIN or password given while signed in (app lock, change PIN). Not `401`, which would mean the session itself had ended. `details` carries `tries_left`. |
| 403 | `HTTPS_REQUIRED` | Plain HTTP with `REQUIRE_HTTPS=true` |
| 404 | `NOT_FOUND` | No such resource |
| 409 | `CONFLICT` | State does not allow it: hold not ready, guardian already active, challenge already closed |
| 425 | `TOO_EARLY` | Step-up cool-off has not finished |
| 413 | `TOO_LARGE` | The request body is over the limit |
| 429 | `RATE_LIMITED` | Too many requests, or the account is locked. `details` carries `retry_in_s`. |
| 503 | `UNAVAILABLE` | A dependency is down. Never returned by `/v1/risk/assess`, which degrades instead. |

## 2. Hot path

### 2.1 `POST /v1/risk/assess`

Score a payment before authorisation. Caller: payer app or PSP.

**Request**

```json
{
  "txn_id": "TXN-20261007-000123",
  "payer": {
    "user_id": "3f0c9a2e-5b1d-4c7e-9a41-0d2f6b8c1e77",
    "vpa": "meera@banka",
    "device_id": "b7e1d7c0-2a4f-4f0e-8d53-6c9a1e0f4b21"
  },
  "payee": {
    "vpa": "refund.desk7@bankb",
    "claimed_name": "SBI Refund Desk"
  },
  "amount": 49000.00,
  "currency": "INR",
  "note": "refund processing fee - urgent",
  "session": {
    "vpa_entry_method": "link",
    "app_switch_count": 3,
    "time_on_pay_screen_s": 94,
    "amount_edit_count": 1,
    "flow_started_at": "2026-10-07T11:40:36+05:30"
  },
  "parent_decision_id": null,
  "client_ts": "2026-10-07T11:42:10+05:30",
  "locale": "en"
}
```

| Field | Required | Notes |
|---|---|---|
| `txn_id` | yes | Unique per attempt. A re-assessment after a hold uses a new one. |
| `payer.user_id`, `payer.vpa`, `payer.device_id` | yes | |
| `payee.vpa` | yes | |
| `payee.claimed_name` | no | The name on the QR code or link, or typed by the payer |
| `amount` | yes | Greater than 0 |
| `note` | no | Up to 200 characters |
| `session` | no | Missing fields take the defaults in [05, section 4](05-data-model.md#4-feature-catalogue-v1) |
| `parent_decision_id` | no | Set when confirming a hold |

**Response, 200**

```json
{
  "decision_id": "0c8f4c8e-0d3e-4f6a-b1b7-7a3d9d0e2f10",
  "txn_id": "TXN-20261007-000123",
  "score": 0.97,
  "tier": "BLOCK",
  "action": "DECLINE",
  "enforced": true,
  "reasons": [
    { "code": "NAME_MISMATCH", "group": "name",
      "message": "The link says \"SBI Refund Desk\" but this UPI ID is registered to \"Rakesh Kumar\".",
      "evidence": { "claimed_name": "SBI Refund Desk", "registered_name": "Rakesh Kumar", "source": "link" } },
    { "code": "CALLER_FLAGGED", "group": "call",
      "message": "The number that called you 6 minutes ago is flagged for fraud.",
      "evidence": { "mins_since_call": 6, "caller_risk": 3 } },
    { "code": "PAYEE_FAN_IN", "group": "payee",
      "message": "37 different people have paid this UPI ID in the last hour.",
      "evidence": { "fan_in_1h": 37 } }
  ],
  "scam_family": {
    "code": "FAKE_REFUND",
    "name": "Fake refund",
    "similarity": 0.83,
    "headline": "You are about to PAY money, not receive it",
    "body": "No one needs your UPI PIN or a payment from you to send you a refund.",
    "audio_key": "FAKE_REFUND"
  },
  "payee": {
    "vpa": "refund.desk7@bankb",
    "registered_name": "Rakesh Kumar",
    "kind": "person",
    "band": "CAUTION",
    "trust_score": 12,
    "name_match_score": 0.08
  },
  "step_up": null,
  "hold": null,
  "guardian": { "required": false, "kind": "NOTIFY", "request_id": "9a1d0f0e-3b56-4c0e-8f4a-2d1c6e7b8a90", "guardian_name": "Arjun" },
  "links": { "calls": ["5d5c1f6a-8e0b-4b8a-9f43-1c2e7a6b9d02"], "messages": [] },
  "model_version": "ens-2026.10.07-1",
  "rules_version": "rules-0.4.0",
  "degraded": false,
  "latency_ms": 74,
  "user_block": null
}
```

| Field | Notes |
|---|---|
| `score` | For the integrating PSP. The payer app must never display it. |
| `tier` | `SAFE`, `WARNING`, `STEP_UP`, `BLOCK` |
| `action` | `ALLOW`, `WARN`, `STEP_UP`, `HOLD`, `DECLINE` |
| `enforced` | `false` in shadow mode; then `tier` is `SAFE`, `action` is `ALLOW` and `reasons` is empty, unless the payee is on the payer's never-pay list |
| `user_block` | `null`, or `{ blocked: true, unblock_at }` when the payee is on the payer's never-pay list (F-33). Then `action` is `DECLINE` and the first reason is `USER_BLOCKED`; `score` and `tier` are still what the models gave. |
| `reasons` | Up to three; empty for `SAFE` |
| `scam_family` | `null` for `SAFE` |
| `step_up` | Present when `action` is `STEP_UP` |
| `hold` | Present when `action` is `HOLD` |
| `guardian` | `null` when the payer has no active guardian. `required: true` means the payment needs approval. |

`step_up` object:

```json
{
  "challenge_id": "c2a7f0b4-6d1e-4a3b-9c58-0e7f1a2b3c4d",
  "cooloff_s": 30,
  "question": { "text": "Were you told this payment is needed to receive a refund?", "answers": ["yes", "no"] },
  "auth_methods": ["biometric", "otp"]
}
```

The risky answer is never sent to the client.

`hold` object:

```json
{ "hold_id": "e4b9c1d2-7f3a-4e5b-8a6c-1d2e3f4a5b6c", "release_at": "2026-10-07T12:12:10+05:30", "confirm_by": "2026-10-08T12:12:10+05:30" }
```

### 2.2 `POST /v1/risk/outcome`

Report what the payer did. Caller: payer app or PSP. Returns `202`.

```json
{ "decision_id": "0c8f4c8e-0d3e-4f6a-b1b7-7a3d9d0e2f10", "user_action": "ABORTED", "final_status": "NOT_ATTEMPTED" }
```

`user_action` values are listed in [02, F-10](02-feature-specs.md#f-10-outcome-logging-and-near-miss-feed--must). The server itself records `STEPUP_FAILED`, `STEPUP_LOCKED`, `GUARDIAN_DENIED`, `GUARDIAN_TIMEOUT`, `HOLD_CANCELLED`, `HOLD_EXPIRED` and `BLOCKED`; the client sends the rest.

An app user's token may not send `final_status: "SUCCESS"` (`403`): only the PSP reports that money moved.

### 2.3 `GET /v1/risk/clearance/{decision_id}`

Service-only. The mock PSP calls it before executing a payment.

```json
{ "cleared": true, "action": "STEP_UP", "why": "step-up passed; guardian approved" }
```

`cleared` is true when the action was `ALLOW`; or `WARN` (the payer chose to continue); or `STEP_UP` with a passed challenge; and, in each case, any required guardian approval was given.

Three things make it false whatever the action: the decision has already been paid; the payee is on the payer's never-pay list (`why: "payee blocked by the payer"`, in shadow mode too); or more than `CLEARANCE_TTL_S` (600 seconds) have passed since the decision or the last approval it needed (`why: "decision expired"`, `expired: true`). A cleared reply carries `valid_for_s`.

### 2.4 `GET /v1/payee/{vpa}/reputation`

The pre-payment chip. Query: `claimed_name` (optional).

```json
{
  "vpa": "refund.desk7@bankb",
  "exists": true,
  "registered_name": "Rakesh Kumar",
  "kind": "person",
  "verified": false,
  "band": "CAUTION",
  "trust_score": 12,
  "band_reason": "Many new people have paid this recently",
  "name_match": { "score": 0.08, "mismatch": true, "institutional_claim": true },
  "paid_before": 0, "is_self": false, "blocked": false
}
```

The last three are present for an app user: how often they have paid this UPI ID, whether it is their own, and whether it is on their never-pay list.

## 3. Step-up

| Endpoint | Body | Returns |
|---|---|---|
| `POST /v1/stepup/challenge` | `{ challenge_id, method: "otp" }` | `{ delivery: "simulated", expires_in_s: 300, sends_left, resend_in_s }`. Sends a one-time code. Not needed for `biometric`. `425` before the pause has ended; `429` for a fourth code or one asked for within 20 seconds of the last. In demo mode the reply also carries `demo_code`. |
| `POST /v1/stepup/verify` | See below | See below |

**Verify request**

```json
{
  "challenge_id": "c2a7f0b4-6d1e-4a3b-9c58-0e7f1a2b3c4d",
  "answer": "no",
  "auth": { "method": "otp", "otp": "482913" }
}
```

For fingerprint or face: `"auth": { "method": "biometric", "key_id": "dk-…", "proof": "<64 hex>" }`, where `proof` is `HMAC-SHA256(device secret, challenge_id)` made with the key the phone enrolled at sign-in. A bare `"biometric_ok": true` is refused with `400 BIOMETRIC_NOT_ENROLLED` unless `TRUST_CLIENT_BIOMETRIC=true`, which the service will not start with outside demo mode.

**Verify response**

```json
{ "result": "PASSED", "action": "ALLOW", "valid_for_s": 600, "guardian": { "required": true, "request_id": "9a1d…", "status": "PENDING" } }
```

| `result` | Meaning | What the server does |
|---|---|---|
| `AUTH_REQUIRED` | Cool-off complete, safe answer, and no `auth` was sent | Nothing. The app sends the answer alone first; if it gets this, it asks the payer to authenticate and sends the answer again with `auth`. The reply lists `auth_methods`: `otp`, and `biometric` only if the account has an enrolled phone. |
| `PASSED` | Cool-off complete, safe answer, authentication succeeded | Marks the challenge passed |
| `FAILED` | The risky answer was given | Converts the decision to `DECLINE`, sets the pair block, records `STEPUP_FAILED` |
| `BAD_CODE` | Wrong or expired code, or a wrong device proof | Counts the attempt; `tries_left` says how many remain |
| `LOCKED` | The third wrong attempt | Closes the challenge for good, records `STEPUP_LOCKED`. The payment must be started again. |
| — (HTTP 425) | Sent before the cool-off ended | Nothing |

## 4. Signals

### 4.1 `POST /v1/signals/call`

Service-only. Returns `202`.

```json
{
  "call_id": "5d5c1f6a-8e0b-4b8a-9f43-1c2e7a6b9d02",
  "event": "started",
  "callee_phone_hash": "h_91f0…",
  "caller_phone_hash": "h_27ab…",
  "caller_known": false,
  "at": "2026-10-07T11:36:02+05:30",
  "source": "simulated"
}
```

`event` is `started` or `ended`. Caller risk is looked up server-side.

### 4.2 `POST /v1/signals/message`

Check a pasted message. Caller: payer app.

**Request**

```json
{ "text": "Dear customer your SBI refund of Rs 4,999 is pending. Pay Rs 49 processing fee to refund.desk7@bankb within 2 hours. Call 9876543210", "source": "paste" }
```

**Response**

```json
{
  "check_id": "7b2e9c40-1a5d-4f3e-b6c7-8d9e0f1a2b3c",
  "verdict": "SCAM",
  "scam_prob": 0.94,
  "label": "refund_bait",
  "family": { "code": "FAKE_REFUND", "name": "Fake refund" },
  "red_flags": [
    { "start": 24, "end": 30, "kind": "refund_bait", "why": "You never need to pay to receive money." },
    { "start": 99, "end": 113, "kind": "urgency", "why": "Pressure to act fast is a common scam tactic." }
  ],
  "entities": { "vpas": ["refund.desk7@bankb"], "phones": ["h_27ab…"], "urls": [], "amounts": [4999, 49] },
  "advice": [
    "Do not reply, click links or call numbers in this message.",
    "Do not pay anyone it mentions.",
    "If you already paid, call 1930 now."
  ],
  "flag_set": true,
  "flag_expires_at": "2026-10-08T11:30:00+05:30"
}
```

`GET /v1/signals/messages` returns the caller's previous checks.

## 5. Payer features

### 5.1 Profile and history

| Endpoint | Purpose |
|---|---|
| `GET /v1/users/me` | Profile, settings, balance, guardian summary |
| `PATCH /v1/users/me` | `{ locale?, voice_warnings?, large_text? }` |
| `GET /v1/users/me/payees` | Saved payees with registered names and bands |
| `GET /v1/users/me/payments` | Payment history with tier and outcome |

### 5.2 Reports and complaint packs

| Endpoint | Body | Returns |
|---|---|---|
| `POST /v1/reports` | `{ kind: "FRAUD" \| "FALSE_POSITIVE", decision_id?, txn_id?, payee_vpa?, comment? }` | `{ report_id, status: "OPEN" }` |
| `POST /v1/complaint-packs` | `{ decision_id? \| txn_id? \| check_id?, description? }` | `{ pack_id, pdf_url, json_url }`. Also files a `FRAUD` report. |
| `GET /v1/complaint-packs/{id}.pdf` | — | The PDF |
| `GET /v1/complaint-packs/{id}.json` | — | The JSON pack |

JSON pack shape:

```json
{
  "pack_id": "…", "generated_at": "…",
  "complainant": { "name": "Meera Joshi", "upi_id": "meera@banka" },
  "transaction": { "txn_id": "…", "at": "…", "amount": 49000.00, "status": "DECLINED",
                   "payee_upi_id": "refund.desk7@bankb", "payee_registered_name": "Rakesh Kumar", "claimed_name": "SBI Refund Desk" },
  "assessment": { "scam_family": "Fake refund", "reasons": ["…", "…", "…"] },
  "timeline": [
    { "at": "…", "type": "message", "detail": "Message flagged as a scam (refund bait)" },
    { "at": "…", "type": "call", "detail": "Incoming call, 6 min, caller rated very high risk" },
    { "at": "…", "type": "payment", "detail": "Payment of ₹49,000 attempted and stopped" }
  ],
  "description": "…",
  "how_to_file": ["Call 1930", "File at cybercrime.gov.in"]
}
```

### 5.3 Guardians

| Endpoint | Body | Notes |
|---|---|---|
| `GET /v1/guardians` | — | `{ as_protected, as_guardian: [], invitations: [] }` |
| `POST /v1/guardians` | `{ guardian: { phone? \| vpa? }, mode, amount_threshold? }` | Creates a `PENDING` link. `409` if one is already active. |
| `POST /v1/guardians/{id}/accept` | — | By the invited guardian. Link becomes `ACTIVE`. |
| `POST /v1/guardians/{id}/decline` | — | By the invited guardian |
| `PATCH /v1/guardians/{id}` | `{ mode?, amount_threshold? }` | Tightening applies at once. Loosening is stored as `pending_change` and applies after 24 hours. |
| `DELETE /v1/guardians/{id}` | — | Sets `REMOVAL_PENDING` with `change_effective_at` 24 hours ahead. The guardian is notified. |
| `POST /v1/guardians/{id}/undo` | — | Cancels a pending removal or change |
| `GET /v1/guardian/requests` | `?status=` | The guardian's inbox |
| `GET /v1/guardian/requests/{id}` | — | Request detail. The protected payer may also call it for their own request, and receives only `status` and `expires_at`. |
| `POST /v1/guardian/requests/{id}/respond` | `{ decision: "approve" \| "deny" \| "dismiss" }` | `dismiss` is for `NOTIFY` requests. `409` if expired. |

Request detail, which is all a guardian may see:

```json
{
  "request_id": "9a1d0f0e-…", "kind": "APPROVAL", "status": "PENDING", "expires_at": "…",
  "protected_name": "Meera Joshi",
  "amount": 49000.00,
  "payee": { "vpa": "refund.desk7@bankb", "registered_name": "Rakesh Kumar" },
  "scam_family": { "name": "Fake refund", "headline": "You are about to PAY money, not receive it" },
  "reasons": ["…", "…"]
}
```

### 5.4 Holds

| Endpoint | Notes |
|---|---|
| `GET /v1/holds` | The caller's holds with status and times |
| `POST /v1/holds/{id}/cancel` | Allowed while `HELD` or `READY`. Records `HOLD_CANCELLED`. |
| `POST /v1/holds/{id}/confirm` | Allowed only when `READY`, otherwise `409`. Returns `{ parent_decision_id, payee, amount, note }`; the client then calls assess with a new `txn_id`. |

### 5.5 Rehearsals

| Endpoint | Notes |
|---|---|
| `GET /v1/rehearsals` | Scripts in the caller's locale, with completion status |
| `GET /v1/rehearsals/{code}` | One script |
| `POST /v1/rehearsals/{code}/runs` | `{ choices: [{ node, option }] }` → `{ safe_choices, total_choices }` |

### 5.6 Caller warning, look-up, never-pay list and trends

Features F-31 to F-34. All need an app user's token.

| Endpoint | Body or query | Returns |
|---|---|---|
| `GET /v1/users/me/call-status` | — | `{ active, recent, call_id, started_at, ended_at, caller: { risk, label, sources, reports, known }, can_report, reported_by_you }`. `recent` means the call ended in the last 30 minutes. With no call: `{ active: false, recent: false }`. |
| `POST /v1/callers/report` | `{ call_id?, phone?, decision_id?, category: "scam" \| "spam" }` | `{ reported, verified, caller }`. With no `call_id` and no `phone`, the caller of the person's latest call in the last 7 days. `404` if there is none. Ten a day. |
| `GET /v1/lookup` | `?q=` a UPI ID or a phone number | See below. 30 a minute, 300 a day. |
| `GET /v1/users/me/blocks` | — | `{ blocks: [{ vpa, name, reason, status, unblock_at, seconds_left }], unblock_delay_s, limit }`. `status` is `ACTIVE` or `UNBLOCK_PENDING`. |
| `POST /v1/users/me/blocks` | `{ vpa, reason?, report? }` | The list, plus `exists` (whether any account uses that UPI ID) and `report` if one was filed |
| `DELETE /v1/users/me/blocks/{vpa}` | — | The list. Starts the 24-hour wait; the block stays until then. |
| `POST /v1/users/me/blocks/{vpa}/keep` | — | The list. Cancels a pending unblock. |
| `GET /v1/trends` | — | `{ days: 7, trends: [{ code, name, advice, seen, trend }] }`. At most three. `seen` is rounded down; `trend` is `up`, `down`, `steady` or `new`. Staff may call it too. |

`caller.risk` is 0 to 3 and `label` is `NONE`, `SUSPECTED`, `LIKELY_SCAM` or `KNOWN_SCAM`. `sources` lists where the level comes from: `seed` (the list of known fraud numbers), `flagged_messages`, `caller_reports`. `reports` is a range.

**Look-up of a UPI ID**

```json
{
  "kind": "vpa", "vpa": "refund.desk7@bankb", "exists": true, "registered_name": "Rakesh Kumar", "payee_kind": "person",
  "verified": false, "band": "CAUTION", "band_reason": "Reported by other users", "trust_score": 38, "age_days": 4,
  "community": { "reports_30d": "1-2", "stopped_14d": "3-9", "confirmed_fraud": false, "other_bank": false,
                 "patterns": [{ "code": "FAKE_REFUND", "name": "Fake refund", "advice": "You never have to pay…" }] },
  "you": { "paid_before": 0, "blocked": false, "unblock_at": null, "is_self": false }
}
```

Counts are ranges: `"0"`, `"1-2"`, `"3-9"`, `"10+"`. A pattern is listed only when at least three different people were stopped in it. An unknown UPI ID returns `{ "kind": "vpa", "exists": false, … }`.

**Look-up of a phone number**

```json
{ "kind": "phone", "phone_last4": "1103", "risk": 3, "label": "KNOWN_SCAM", "sources": ["seed"], "reports": "0",
  "called_you": { "count_30d": 1, "last_at": "2026-10-07T12:33:07+00:00" }, "reported_by_you": false, "is_self": false }
```

## 6. Dashboard

All require a staff token. Role in brackets where it is not `analyst`.

### 6.1 Decisions and cases

| Endpoint | Notes |
|---|---|
| `GET /v1/decisions` | Filters: `tier`, `action`, `family`, `linked` (`call`, `message`), `enforced`, `from`, `to`, `q` (UPI ID) |
| `GET /v1/decisions/{id}` | Everything for the decision detail page: decision, transaction, session, payee, contributions, fired rules, timeline, outcome, guardian request, hold, label |
| `POST /v1/decisions/{id}/label` | `{ label: "FRAUD" \| "LEGITIMATE", family?, note? }`. Publishes to `fraud.reports`. Audited. |
| `GET /v1/decisions/{id}/summary` | The case summary, or `404` if not generated yet |
| `POST /v1/decisions/{id}/summary/regenerate` | `202` |
| `GET /v1/near-misses` | Filter by `payee`, `from`, `to`; `group_by=payee` |
| `GET /v1/reviews` | Open reports and review requests |
| `POST /v1/reviews/{report_id}/resolve` | `{ resolution: "UPHELD" \| "OVERTURNED" \| "CONFIRMED" \| "REJECTED", note? }`. `OVERTURNED` clears the pair block. |

### 6.2 Payees, network and consortium

| Endpoint | Notes |
|---|---|
| `GET /v1/payees/{vpa}` | Directory entry, this bank's reputation and its history, graph features, reports, consortium signals |
| `GET /v1/graph/neighbourhood` | `?vpa=&hops=1..3&days=30&min_amount=0` |
| `GET /v1/consortium/signals` | `?vpa=` or a list of recent signals received |

Neighbourhood response:

```json
{
  "centre": "cyber.cell.verify@bankb",
  "nodes": [
    { "id": "cyber.cell.verify@bankb", "registered_name": "Imran Shaikh", "band": "CAUTION", "kind": "person", "community_id": 14 },
    { "id": "layer2.acct@bankb", "registered_name": "Pooja Singh", "band": "REPORTED", "kind": "person", "community_id": 14 }
  ],
  "edges": [
    { "source": "cyber.cell.verify@bankb", "target": "layer2.acct@bankb", "type": "payment", "amount": 182000.00, "count": 6 },
    { "source": "cyber.cell.verify@bankb", "target": "layer2.acct@bankb", "type": "device" }
  ],
  "truncated": false
}
```

Responses are capped at 300 nodes; `truncated` says when the cap was hit.

### 6.3 Policy

| Endpoint | Role | Notes |
|---|---|---|
| `GET /v1/policy` | analyst | The tenant's policy |
| `PUT /v1/policy/mode` | admin | `{ mode: "enforce" \| "shadow" }`. Audited. |
| `PUT /v1/policy/thresholds` | admin | `{ t_warning, t_stepup, t_block }`. Must satisfy `0 < t_warning < t_stepup < t_block < t_decline`. Audited. |
| `POST /v1/policy/simulate` | analyst | See below |

**Simulate request**

```json
{ "from": "2026-10-01T00:00:00+05:30", "to": "2026-10-07T00:00:00+05:30",
  "t_warning": 0.30, "t_stepup": 0.55, "t_block": 0.85, "fraud_rate": 0.0005 }
```

**Simulate response**

```json
{
  "n_decisions": 41250, "n_fraud": 412,
  "proposed": {
    "scams_caught": 0.91, "scams_warned_only": 0.05, "scams_missed": 0.04,
    "legit_warning": 0.041, "legit_stepup": 0.011, "legit_block": 0.0008,
    "interruptions_per_1000": 53.2, "rupees_protected": 18450000.00, "precision_stepup_plus": 0.037
  },
  "current": { "…": "same shape, for the thresholds in force" },
  "curve": [ { "t_stepup": 0.05, "scams_caught": 0.99, "legit_interrupted": 0.31 }, { "…": "…" } ]
}
```

### 6.4 Model and system health

| Endpoint | Notes |
|---|---|
| `GET /v1/model-health/models` | Registry: versions, stage, metrics, gate results |
| `GET /v1/model-health/drift` | Latest report: PSI and status per feature and for the score; tier mix over time |
| `GET /v1/model-health/fairness` | Latest report: per dimension and group, with disparity ratios |
| `GET /v1/metrics/summary` | Latency percentiles by stage, requests per second, tier and action counts, degraded count, timeouts, requests shed under overload, database fallbacks, event backlog, versions in use |
| `POST /v1/model-health/reload` | [admin] Reload the active model and the rules now |
| `GET /v1/audit` | [auditor, admin] Audit log entries |

## 7. Demo control

Available only when `DEMO_MODE=true`, to admin staff tokens.

| Endpoint | Body | Effect |
|---|---|---|
| `POST /v1/demo/scenario` | `{ scenario, user_id }` | Stages a scenario: `digital_arrest`, `fake_refund`, `mule_transfer`, `impersonation`, `kyc_threat`, `investment_scam`. Returns what was staged and the payment the presenter should attempt. |
| `POST /v1/demo/call` | `{ user_id, action: "start" \| "end", caller_risk: 0..3, caller_known }` | Writes the call event and shows or ends the fake call on that user's app |
| `POST /v1/demo/message` | `{ user_id, template }` | Shows a simulated message on that user's app |
| `GET /v1/demo/scenarios` | — | The scenarios and the demo users, for the Demo control page |
| `POST /v1/demo/drift` | `{ n, amount_factor, hours }` | Sends honest traffic with much larger amounts at an unusual hour, then returns a drift report |
| `POST /v1/demo/replay` | `{ rate_per_s, duration_s }` | Replays simulator traffic through the live pipeline so the feed moves |
| `POST /v1/demo/chaos` | `{ cache_down?, model_down? }` | Simulates a dependency failure, for the failure tests |
| `POST /v1/demo/metrics-reset` | — | Clears the latency histogram, for the load test |
| `POST /v1/demo/reset` | — | Restores the seeded state: decisions, reputation, guardians, thresholds, balances, demo users' settings; removes accounts created since. Does not clear the audit log. |
| `GET /v1/demo/my-call` | — | [payer token] Whether a simulated call is in progress, so the app can restore the call bar |
| `POST /v1/demo/my-call/end` | — | [payer token] The payer hangs up the simulated call |

A simulated message that belongs to a scenario started from a link also carries `link: { vpa, name, amount, note }`, so the app can open the payment as a tapped link would.

## 8. Health

| Endpoint | Returns |
|---|---|
| `GET /healthz` | `200` if the process is up |
| `GET /readyz` | `{ ready, degraded, model_version, rules_version }` |
| `GET /metrics` | Prometheus text format. Answers only to the local network or a service key. |

## 9. Mock PSP

A stand-in for the bank's switch and the UPI directory. Not part of the product.

| Endpoint | Notes |
|---|---|
| `GET /mock-psp/v1/resolve/{vpa}` | `{ exists, registered_name, kind, verified }` |
| `POST /mock-psp/v1/pay` | `{ txn_id, decision_id, pin }`. Checks clearance, then pretends to pay the amount and the payee **recorded in the decision**. Returns `{ status: "SUCCESS" \| "REFUSED", reference }`; a refusal carries `reason` and, for a decision more than ten minutes old, `expired: true`. One payment per decision, even if asked twice at once. Any PIN digits are accepted. Updates the simulated balances. |

The mock PSP refuses to pay without clearance. That is the integration pattern a real PSP would follow.

## 10. Real-time events

Socket.IO on `risk-api`. The token is sent in the connection `auth` payload and is held to the same checks as a REST call. The server closes the socket when the session is signed out, the credentials change, or the session ends.

### Namespace `/analyst`

Room: the staff member's tenant.

| Event | Payload |
|---|---|
| `decision.created` | `{ decision_id, at, amount, payee_vpa, tier, action, score, top_reason, family, enforced, linked: { call, message } }` |
| `outcome.recorded` | `{ decision_id, user_action, is_near_miss }` |
| `reputation.updated` | `{ vpa, old_band, new_band, old_score, new_score, cause }` |
| `case.summary_ready` | `{ decision_id }` |
| `drift.alert` | `{ feature, psi, status }` |

### Namespace `/user`

Room: the user's ID.

| Event | Payload | Shown as |
|---|---|---|
| `guardian.request` | `{ request_id, kind, protected_name, amount, expires_at }` | Guardian inbox badge and alert |
| `guardian.response` | `{ request_id, status }` | Leaves the waiting screen |
| `hold.ready` | `{ hold_id, payee_name, amount }` | "Do you still want to send…" |
| `call.incoming` | `{ call_id, caller_label, caller_risk }` | Fake incoming-call screen (demo) |
| `call.flagged` | `{ call_id, risk }` | A reported number is calling: the red banner (F-31) |
| `block.lifted` | `{ vpa }` | A UPI ID has come off the never-pay list (F-33) |
| `call.ended` | `{ call_id }` | Removes the in-call bar (demo) |
| `sms.simulated` | `{ kind: "otp" \| "message", text }` | Simulated SMS banner (demo) |

If the socket is down, the apps poll: the dashboard `GET /v1/decisions`, a waiting payer `GET /v1/guardian/requests/{id}`, the holds screen `GET /v1/holds`, each every 5 seconds.
