# 05 — Data Model

[← Index](README.md)

PostgreSQL is the system of record. Redis is a cache that can be rebuilt from PostgreSQL and the event bus at any time. This document is authoritative for the names of tables, columns, keys, topics and features.

> **As built.** The authoritative schema is `infra/postgres/001_schema.sql` (41 tables) together with `002_security_and_community.sql` (four more tables and six columns, section 1.9). This document describes its design and was written first; where a column list here differs from that file, the file is right. Added during the build:
>
> - `users`: `pin_hash`, `phone_last4`, `active`, `failed_attempts`, `locked_until`, `seed_balance`, `is_demo`, `is_synthetic` (sign-in, lockout, demo reset).
> - `staff_users`: `active`, `failed_attempts`, `locked_until`, `last_login_at`.
> - `vpa_events`: one row per bad event against a payee (confirmed fraud, report, near-miss, flagged message), with its weight. The counters are rebuilt from it.
> - `vpa_reputation_seed`: a copy of reputation as seeded, which a demo reset restores.
> - `meta`: key and value; holds the time the database was seeded.
> - `transactions` and `risk_decisions`: `is_synthetic`; and the simulator's `truth_is_fraud`, `truth_family`, `campaign_id` on transactions.
> - `risk_decisions.detail`: model parts, rule effects, the payee as shown, the reasons in English, and the family wording variant.
> - `outbox` is defined and **not used**: the in-process event queue does not write to it.
>
> There are no migrations. `python manage.py simulate` drops and recreates everything.

## 1. PostgreSQL schema

Migrations live in `infra/postgres/migrations/` and are applied with Alembic. Types are PostgreSQL 16.

### 1.1 Tenancy and people

```sql
CREATE TABLE tenants (
  tenant_id      text PRIMARY KEY,             -- 'bank_a', 'bank_b'
  name           text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (                           -- payers and guardians
  user_id        uuid PRIMARY KEY,
  tenant_id      text NOT NULL REFERENCES tenants,
  display_name   text NOT NULL,
  phone_hash     text UNIQUE NOT NULL,         -- keyed hash; the number itself is never stored
  primary_vpa    text NOT NULL,
  segment        text NOT NULL,                -- student | salaried | elderly | small_business
  age_band       text NOT NULL,                -- 18-25 | 26-59 | 60+
  locale         text NOT NULL DEFAULT 'en',   -- en | hi
  voice_warnings boolean NOT NULL DEFAULT true,
  large_text     boolean NOT NULL DEFAULT false,
  balance        numeric(12,2),                -- simulated
  is_demo        boolean NOT NULL DEFAULT false,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE staff_users (                     -- dashboard users
  staff_id       uuid PRIMARY KEY,
  tenant_id      text NOT NULL REFERENCES tenants,
  email          text UNIQUE NOT NULL,
  password_hash  text NOT NULL,
  role           text NOT NULL,                -- analyst | admin | auditor
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE devices (
  device_id      uuid PRIMARY KEY,
  user_id        uuid NOT NULL REFERENCES users,
  fingerprint    text NOT NULL,
  first_seen     timestamptz NOT NULL,
  last_seen      timestamptz NOT NULL
);
```

### 1.2 Payees

```sql
CREATE TABLE vpas (                            -- the mock PSP's directory
  vpa_id          text PRIMARY KEY,            -- e.g. 'meera@banka'
  tenant_id       text NOT NULL REFERENCES tenants,
  registered_name text NOT NULL,               -- the name the bank holds
  kind            text NOT NULL,               -- person | merchant
  verified        boolean NOT NULL DEFAULT false,
  owner_user_id   uuid REFERENCES users,
  on_denylist     boolean NOT NULL DEFAULT false,
  first_seen      timestamptz NOT NULL
);

CREATE TABLE vpa_devices (                     -- which devices a UPI ID has been used from
  vpa_id         text NOT NULL REFERENCES vpas,
  fingerprint    text NOT NULL,
  PRIMARY KEY (vpa_id, fingerprint)
);

CREATE TABLE vpa_reputation (                  -- one row per observing bank and UPI ID
  tenant_id      text NOT NULL REFERENCES tenants,   -- the bank whose customers produced this view
  vpa_id         text NOT NULL REFERENCES vpas,
  recv_decayed   double precision NOT NULL DEFAULT 0,
  bad_decayed    double precision NOT NULL DEFAULT 0,
  lifetime_recv  integer NOT NULL DEFAULT 0,
  reports_30d    integer NOT NULL DEFAULT 0,
  confirmed_90d  integer NOT NULL DEFAULT 0,
  near_miss_14d  integer NOT NULL DEFAULT 0,
  fan_in_1h      integer NOT NULL DEFAULT 0,
  fan_in_24h     integer NOT NULL DEFAULT 0,
  first_time_payer_ratio_24h double precision NOT NULL DEFAULT 0,
  p_bad          double precision NOT NULL DEFAULT 0.01,
  maturity       double precision NOT NULL DEFAULT 0,
  trust_score    smallint NOT NULL DEFAULT 45, -- 0..100, for display
  band           text NOT NULL DEFAULT 'NO_HISTORY', -- TRUSTED | NEUTRAL | NO_HISTORY | CAUTION | REPORTED
  band_reason    text,                         -- one line for the chip
  counters_at    timestamptz NOT NULL,         -- when the decayed counters were last brought forward
  updated_at     timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, vpa_id)
);

CREATE TABLE vpa_reputation_history (          -- for the chart on the payee page
  tenant_id      text NOT NULL REFERENCES tenants,
  vpa_id         text NOT NULL REFERENCES vpas,
  at             timestamptz NOT NULL,
  trust_score    smallint NOT NULL,
  band           text NOT NULL,
  cause          text,                         -- e.g. 'near_miss', 'confirmed_fraud', 'flagged_message'
  PRIMARY KEY (tenant_id, vpa_id, at)
);

CREATE TABLE graph_features (
  vpa_id                      text PRIMARY KEY REFERENCES vpas,
  downstream_hop              smallint NOT NULL DEFAULT 9,   -- 1..3, or 9 for none
  shares_device_with_reported boolean NOT NULL DEFAULT false,
  passthrough_ratio           double precision NOT NULL DEFAULT 0,
  community_id                integer,
  community_risk_share        double precision NOT NULL DEFAULT 0,
  computed_at                 timestamptz NOT NULL
);

CREATE TABLE consortium_signals (
  vpa_token      text NOT NULL,                -- keyed hash of the UPI ID
  source_tenant  text NOT NULL REFERENCES tenants,
  bad_decayed    double precision NOT NULL,
  recv_decayed   double precision NOT NULL,
  band           text NOT NULL,
  confirmed      boolean NOT NULL DEFAULT false,
  updated_at     timestamptz NOT NULL,
  PRIMARY KEY (vpa_token, source_tenant)
);

CREATE TABLE flagged_indicators (              -- things named in messages users flagged
  tenant_id      text NOT NULL REFERENCES tenants,
  indicator_type text NOT NULL,                -- vpa | phone | url
  value          text NOT NULL,                -- UPI ID, phone hash, or normalised URL host
  reporter_count integer NOT NULL DEFAULT 0,   -- distinct users
  first_seen     timestamptz NOT NULL,
  last_seen      timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, indicator_type, value)
);

CREATE TABLE flagged_indicator_reporters (
  tenant_id      text NOT NULL,
  indicator_type text NOT NULL,
  value          text NOT NULL,
  user_id        uuid NOT NULL REFERENCES users,
  PRIMARY KEY (tenant_id, indicator_type, value, user_id)
);
```

**Reputation is kept per observing bank.** `vpas` is the shared directory: one row per UPI ID, whichever bank holds the account. `vpa_reputation` holds what one bank has learned about a UPI ID from its own customers' payments, reports and near-misses. Bank A's view of a payee and Bank B's view are separate rows. That separation is the "silo" the deck describes, and it is what makes cross-bank sharing (F-24) do something: a consortium signal carries one bank's view to the other.

Two things are shared across tenants in the prototype, as simplifications: the denylist flag on `vpas`, which stands in for a national registry, and the network graph, which is built from all simulated payments.

### 1.3 Payments, decisions and outcomes

```sql
CREATE TABLE transactions (
  txn_id             text PRIMARY KEY,
  tenant_id          text NOT NULL REFERENCES tenants,
  user_id            uuid NOT NULL REFERENCES users,
  payer_vpa          text NOT NULL,
  payee_vpa          text NOT NULL,
  claimed_name       text,                     -- the name the payer was given
  amount             numeric(12,2) NOT NULL,
  note               text,
  device_id          uuid,
  session            jsonb NOT NULL DEFAULT '{}',  -- vpa_entry_method, app_switch_count, ...
  parent_decision_id uuid,                     -- set when re-assessing after a hold
  -- simulator ground truth, null for live demo payments
  truth_is_fraud     boolean,
  truth_family       text,
  campaign_id        text,
  created_at         timestamptz NOT NULL
);
CREATE INDEX ON transactions (payee_vpa, created_at);
CREATE INDEX ON transactions (user_id, created_at);

CREATE TABLE risk_decisions (
  decision_id    uuid PRIMARY KEY,
  txn_id         text UNIQUE NOT NULL,         -- no FK: the decision can arrive before the transaction row
  tenant_id      text NOT NULL,
  score          double precision NOT NULL,
  tier           text NOT NULL,                -- SAFE | WARNING | STEP_UP | BLOCK   (what the engine decided)
  action         text NOT NULL,                -- ALLOW | WARN | STEP_UP | HOLD | DECLINE
  enforced       boolean NOT NULL DEFAULT true,-- false in shadow mode
  reason_codes   text[] NOT NULL DEFAULT '{}',
  reasons        jsonb NOT NULL DEFAULT '[]',  -- rendered sentences with evidence
  rules_fired    text[] NOT NULL DEFAULT '{}',
  contributions  jsonb NOT NULL DEFAULT '{}',  -- per signal group
  scam_family    text,
  family_sim     double precision,
  features       jsonb NOT NULL,               -- the exact feature vector used; training reads this
  model_version  text NOT NULL,
  rules_version  text NOT NULL,
  degraded       boolean NOT NULL DEFAULT false,
  latency_ms     integer NOT NULL,
  stage_ms       jsonb NOT NULL DEFAULT '{}',  -- latency per stage
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON risk_decisions (tenant_id, created_at DESC);
CREATE INDEX ON risk_decisions (tier, created_at DESC);

CREATE TABLE outcomes (                        -- the transaction ledger
  decision_id      uuid PRIMARY KEY REFERENCES risk_decisions,
  user_action      text NOT NULL,              -- see 02, F-10
  final_status     text,                       -- SUCCESS | FAILED | NOT_ATTEMPTED
  is_near_miss     boolean NOT NULL DEFAULT false,
  near_miss_weight double precision NOT NULL DEFAULT 0,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE decision_labels (                 -- analyst judgement; the training label
  decision_id    uuid PRIMARY KEY REFERENCES risk_decisions,
  label          text NOT NULL,                -- FRAUD | LEGITIMATE
  family         text,
  note           text,
  labelled_by    uuid REFERENCES staff_users,
  created_at     timestamptz NOT NULL DEFAULT now()
);
```

Storing the exact feature vector on every decision keeps training and serving consistent. The retraining job reads the features that were actually used, so there is no second feature pipeline to drift.

### 1.4 Signals

```sql
CREATE TABLE call_events (
  call_id        uuid PRIMARY KEY,
  callee_hash    text NOT NULL,
  caller_hash    text NOT NULL,
  caller_risk    smallint NOT NULL,            -- 0 none, 1 medium, 2 high, 3 very high
  caller_known   boolean NOT NULL,
  started_at     timestamptz NOT NULL,
  ended_at       timestamptz,
  source         text NOT NULL                 -- simulated | demo | twilio | device
);
CREATE INDEX ON call_events (callee_hash, started_at DESC);

CREATE TABLE caller_risk_list (                -- seeded, plus numbers promoted from flagged messages
  caller_hash    text PRIMARY KEY,
  risk           smallint NOT NULL,
  source         text NOT NULL,                -- seed | flagged_messages
  updated_at     timestamptz NOT NULL
);

CREATE TABLE message_checks (
  check_id       uuid PRIMARY KEY,
  user_id        uuid NOT NULL REFERENCES users,
  text           text NOT NULL,                -- synthetic in the prototype; see 11 for the pilot rule
  text_hash      text NOT NULL,
  label          text NOT NULL,                -- benign | refund_bait | authority_threat | kyc_bill_threat | investment_task | impersonation_request | prize
  scam_prob      double precision NOT NULL,
  verdict        text NOT NULL,                -- SCAM | CAREFUL | FINE
  family         text,
  entities       jsonb NOT NULL DEFAULT '{}',  -- {vpas:[], phones:[], urls:[], amounts:[]}
  red_flags      jsonb NOT NULL DEFAULT '[]',  -- [{start, end, kind, why}]
  flag_set       boolean NOT NULL DEFAULT false,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE signal_links (                    -- "one connected risk event"
  decision_id    uuid NOT NULL REFERENCES risk_decisions,
  signal_type    text NOT NULL,                -- call | message
  signal_id      uuid NOT NULL,                -- call_id or check_id
  PRIMARY KEY (decision_id, signal_type, signal_id)
);

CREATE TABLE fraud_reports (
  report_id      uuid PRIMARY KEY,
  tenant_id      text NOT NULL REFERENCES tenants,
  kind           text NOT NULL,                -- FRAUD | FALSE_POSITIVE
  decision_id    uuid,
  txn_id         text,
  payee_vpa      text,
  reporter_id    uuid REFERENCES users,
  comment        text,
  status         text NOT NULL DEFAULT 'OPEN', -- OPEN | CONFIRMED | REJECTED | UPHELD | OVERTURNED
  family         text,
  resolved_by    uuid REFERENCES staff_users,
  resolved_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now()
);
```

### 1.5 Protective features

```sql
CREATE TABLE stepup_challenges (
  challenge_id   uuid PRIMARY KEY,
  decision_id    uuid NOT NULL REFERENCES risk_decisions,
  question_code  text NOT NULL,                -- the scam family code
  risky_answer   text NOT NULL,                -- yes | no
  cooloff_until  timestamptz NOT NULL,
  auth_method    text,                         -- biometric | otp
  status         text NOT NULL DEFAULT 'OPEN', -- OPEN | PASSED | FAILED | ABANDONED
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE guardians (
  guardian_link_id     uuid PRIMARY KEY,
  user_id              uuid NOT NULL REFERENCES users,   -- the protected payer
  guardian_user_id     uuid NOT NULL REFERENCES users,
  mode                 text NOT NULL,                    -- notify | co_approve
  amount_threshold     numeric(12,2),                    -- co_approve: new payee above this needs approval
  status               text NOT NULL,                    -- PENDING | ACTIVE | REMOVAL_PENDING | REMOVED | DECLINED
  pending_change       jsonb,                            -- e.g. {"mode": "notify"}
  change_effective_at  timestamptz,                      -- 24 h after a removal or loosening
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_active_guardian ON guardians (user_id)
  WHERE status IN ('ACTIVE', 'REMOVAL_PENDING');

CREATE TABLE guardian_requests (
  request_id        uuid PRIMARY KEY,
  guardian_link_id  uuid NOT NULL REFERENCES guardians,
  decision_id       uuid NOT NULL,
  kind              text NOT NULL,              -- NOTIFY | APPROVAL
  status            text NOT NULL,              -- PENDING | APPROVED | DENIED | EXPIRED | DISMISSED
  summary           jsonb NOT NULL,             -- amount, payee, reasons, family: all the guardian may see
  expires_at        timestamptz,
  responded_at      timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE held_payments (
  hold_id        uuid PRIMARY KEY,
  decision_id    uuid NOT NULL,
  txn_id         text NOT NULL,
  user_id        uuid NOT NULL REFERENCES users,
  payee_vpa      text NOT NULL,
  amount         numeric(12,2) NOT NULL,
  release_at     timestamptz NOT NULL,
  confirm_by     timestamptz NOT NULL,          -- release_at + 24 h
  status         text NOT NULL,                 -- HELD | READY | CANCELLED | CONFIRMED | EXPIRED
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pair_blocks (                      -- payer-to-payee cooling period after a Decline
  user_id        uuid NOT NULL REFERENCES users,
  payee_vpa      text NOT NULL,
  until          timestamptz NOT NULL,
  PRIMARY KEY (user_id, payee_vpa)
);

CREATE TABLE complaint_packs (
  pack_id        uuid PRIMARY KEY,
  decision_id    uuid,
  txn_id         text,
  check_id       uuid,                          -- when created from a message check alone
  requested_by   text NOT NULL,                 -- user:{id} or staff:{id}
  payload        jsonb NOT NULL,                -- the JSON pack
  pdf_path       text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE rehearsal_scripts (
  code           text NOT NULL,                 -- e.g. 'digital_arrest_1'
  locale         text NOT NULL,
  family         text NOT NULL,
  title          text NOT NULL,
  script         jsonb NOT NULL,                -- nodes: see 02, F-28 and section 6 below
  PRIMARY KEY (code, locale)
);

CREATE TABLE rehearsal_runs (
  run_id         uuid PRIMARY KEY,
  user_id        uuid NOT NULL REFERENCES users,
  script_code    text NOT NULL,
  choices        jsonb NOT NULL,
  safe_choices   smallint NOT NULL,
  total_choices  smallint NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);
```

### 1.6 Configuration and catalogues

```sql
CREATE TABLE policy_config (
  tenant_id          text PRIMARY KEY REFERENCES tenants,
  mode               text NOT NULL DEFAULT 'enforce',   -- enforce | shadow
  t_warning          double precision NOT NULL DEFAULT 0.30,
  t_stepup           double precision NOT NULL DEFAULT 0.60,
  t_block            double precision NOT NULL DEFAULT 0.85,
  t_decline          double precision NOT NULL DEFAULT 0.95,
  block_mode         text NOT NULL DEFAULT 'hold',      -- hold | decline
  hold_seconds       integer NOT NULL DEFAULT 1800,
  cooloff_seconds    integer NOT NULL DEFAULT 30,
  guardian_timeout_s integer NOT NULL DEFAULT 600,
  pair_block_hours   integer NOT NULL DEFAULT 24,
  updated_by         uuid REFERENCES staff_users,
  updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE scam_families (
  code           text PRIMARY KEY,              -- DIGITAL_ARREST, FAKE_REFUND, ...
  name           text NOT NULL,
  prototype      double precision[] NOT NULL,   -- vector in signal space, see 06 section 11
  messages       jsonb NOT NULL,                -- per locale: headline, body, question, risky_answer
  audio_key      text NOT NULL,
  active         boolean NOT NULL DEFAULT true
);

CREATE TABLE reason_codes (
  code           text PRIMARY KEY,
  signal_group   text NOT NULL,
  priority       smallint NOT NULL,             -- tie-break when choosing three
  templates      jsonb NOT NULL                 -- per locale, with {placeholders}
);

CREATE TABLE model_registry (
  version        text PRIMARY KEY,              -- e.g. 'ens-2026.10.07-1'
  kind           text NOT NULL,                 -- ensemble | text
  artifact_path  text NOT NULL,
  metrics        jsonb NOT NULL,
  gates          jsonb NOT NULL DEFAULT '{}',   -- pass or fail per gate
  stage          text NOT NULL,                 -- candidate | active | archived
  created_at     timestamptz NOT NULL DEFAULT now()
);
```

### 1.7 Monitoring and summaries

```sql
CREATE TABLE drift_reports (
  report_id      uuid NOT NULL,
  model_version  text NOT NULL,
  window_start   timestamptz NOT NULL,
  window_end     timestamptz NOT NULL,
  feature        text NOT NULL,                 -- a feature name, or '__score__'
  psi            double precision NOT NULL,
  status         text NOT NULL,                 -- stable | watch | alert
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (report_id, feature)
);

CREATE TABLE fairness_reports (
  report_id          uuid NOT NULL,
  model_version      text NOT NULL,
  window_start       timestamptz NOT NULL,
  window_end         timestamptz NOT NULL,
  dimension          text NOT NULL,             -- age_band | segment | locale
  segment            text NOT NULL,
  n_legit            integer NOT NULL,
  share_warning      double precision NOT NULL,
  share_stepup       double precision NOT NULL,
  share_block        double precision NOT NULL,
  fpr_stepup_plus    double precision NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (report_id, dimension, segment)
);

CREATE TABLE case_summaries (
  decision_id      uuid PRIMARY KEY REFERENCES risk_decisions,
  summary          text NOT NULL,
  key_evidence     jsonb NOT NULL DEFAULT '[]',
  suggested_action text,
  provider         text NOT NULL,               -- gemini | groq | ollama | template
  model            text,
  generated_at     timestamptz NOT NULL DEFAULT now()
);
```

### 1.8 Plumbing

```sql
CREATE TABLE audit_log (                        -- append-only
  id             bigserial PRIMARY KEY,
  tenant_id      text,
  actor          text NOT NULL,                 -- staff:{id} | user:{id} | system
  action         text NOT NULL,                 -- e.g. 'policy.thresholds.update'
  subject        text NOT NULL,
  detail         jsonb,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE outbox (                           -- events waiting for the bus
  id             bigserial PRIMARY KEY,
  topic          text NOT NULL,
  payload        jsonb NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  published_at   timestamptz
);
```

### 1.9 Added after the first build

`infra/postgres/002_security_and_community.sql`. Every statement is idempotent (`IF NOT EXISTS`), so the file is applied on top of 001 by the simulator **and again at every API start**: an existing database gains the new tables and columns without being rebuilt. This is the only migration mechanism; anything that is not expressible this way still needs a rebuild.

```sql
-- sessions (F-35)
ALTER TABLE users       ADD COLUMN IF NOT EXISTS token_gen           integer NOT NULL DEFAULT 0;   -- raised to refuse every earlier token
ALTER TABLE users       ADD COLUMN IF NOT EXISTS lockouts            integer NOT NULL DEFAULT 0;   -- locks in a row; each lasts twice as long
ALTER TABLE users       ADD COLUMN IF NOT EXISTS pin_changed_at      timestamptz;
ALTER TABLE staff_users ADD COLUMN IF NOT EXISTS token_gen           integer NOT NULL DEFAULT 0;
ALTER TABLE staff_users ADD COLUMN IF NOT EXISTS lockouts            integer NOT NULL DEFAULT 0;
ALTER TABLE staff_users ADD COLUMN IF NOT EXISTS password_changed_at timestamptz;

CREATE TABLE IF NOT EXISTS revoked_sessions (   -- a signed-out session, kept until it could no longer be renewed
  sid         text PRIMARY KEY,
  kind        text NOT NULL,                    -- user | staff
  sub         text NOT NULL,
  reason      text NOT NULL DEFAULT 'logout',   -- logout | locked
  expires_at  timestamptz NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS device_keys (        -- a secret held by one installed copy of the app (11, section 1.3)
  user_id     uuid NOT NULL REFERENCES users ON DELETE CASCADE,
  key_id      text NOT NULL,
  secret      text NOT NULL,                    -- 64 hex characters; the server needs it to check a proof
  label       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  last_used   timestamptz,
  PRIMARY KEY (user_id, key_id)                 -- at most five per user; the oldest is dropped
);

-- caller reports (F-31)
CREATE TABLE IF NOT EXISTS caller_reports (
  report_id    uuid PRIMARY KEY,
  tenant_id    text NOT NULL,
  caller_hash  text NOT NULL,
  reporter_id  uuid NOT NULL,
  call_id      uuid,
  decision_id  uuid,
  category     text NOT NULL DEFAULT 'scam' CHECK (category IN ('scam','spam','safe')),
  verified     boolean NOT NULL DEFAULT false,  -- the number called the reporter, or was in a message they flagged
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (caller_hash, reporter_id)             -- one report per person per number
);

-- never-pay list (F-33)
CREATE TABLE IF NOT EXISTS user_blocks (
  user_id      uuid NOT NULL REFERENCES users ON DELETE CASCADE,
  payee_vpa    text NOT NULL,
  payee_name   text,
  reason       text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  unblock_at   timestamptz,                     -- set when the payer asks to unblock; the block lifts then
  PRIMARY KEY (user_id, payee_vpa)
);
```

`caller_risk_list.source` gains the value `caller_reports`. `audit_log.action` gains `auth.*`, `caller.report` and `block.*` ([11, section 1.8](11-security-risks-and-decisions.md#18-audit-and-visibility)). `stepup_challenges.status` gains `LOCKED`, and `outcomes.user_action` gains `STEPUP_LOCKED`. The working copy of a decision (`dec:{decision_id}`) gains `stepup_passed_at`, `guardian_approved_at` and `user_block`.

That makes 45 tables.

## 2. Redis keys

| Key | Type | Contents | Expires |
|---|---|---|---|
| `vpa_dir:{vpa_id}` | hash | Directory entry: `registered_name`, `kind`, `verified`, `on_denylist`, `first_seen` | 24 h |
| `vpa_rep:{tenant_id}:{vpa_id}` | hash | That bank's view: reputation counters, `p_bad`, `maturity`, `trust_score`, `band`, `band_reason` | 24 h, refreshed on write |
| `vpa_graph:{vpa_id}` | hash | `downstream_hop`, `shares_device_with_reported`, `passthrough_ratio`, `community_risk_share` | 1 h |
| `consortium:{tenant_id}:{vpa_token}` | hash | Signals received by this bank from others: `reported`, `p_bad`, `source_tenant`, `confirmed` | 24 h |
| `call_flag:{phone_hash}` | hash | `call_id`, `state`, `caller_risk`, `caller_known`, `started_at`, `ended_at` | 30 min after the call ends |
| `calls_24h:{phone_hash}` | sorted set | Times of calls from unknown numbers | 24 h |
| `msg_flag:{user_id}` | hash | `check_id`, `scam_prob`, `family`, `set_at`, `vpas` (comma-separated) | 24 h |
| `msg_vpas:{user_id}` | set | UPI IDs named in messages this user flagged | 7 days |
| `user_prof:{user_id}` | hash | 30-day mean, standard deviation and p95 of amount; counts; `account_first_seen`; `phone_hash`; `locale`; `balance` | 24 h |
| `known_payee:{user_id}` | hash | payee → `count|first_paid_at` | 30 days |
| `vel:{user_id}` | sorted set | Recent payment times, with a new-payee marker | 24 h |
| `dev:{user_id}` | hash | device → `first_seen` | 90 days |
| `guardian:{user_id}` | hash | Active guardian link: `guardian_link_id`, `mode`, `amount_threshold` | 1 h |
| `pair_block:{user_id}:{vpa_id}` | string | Present while the cooling period lasts | The cooling period |
| `decision:{user_id}:{txn_id}` | string | Serialised decision, for idempotency. Keyed by payer too, so nobody is handed another person's decision. | 10 min |
| `policy:{tenant_id}` | hash | The tenant's `policy_config` row | 5 min, deleted on update |
| `model:active` | — | Not used in the build. The active model is named in the file `ml/artifacts/ACTIVE`. | — |
| `otp:{challenge_id}` | string | Keyed hash of the one-time code, bound to its challenge | 5 min |
| `stream:{topic}` | stream | Events, when `EVENT_BUS=redis` | Trimmed to 10,000 entries |

Keys added during the build:

| Key | Type | Holds | Expiry |
|---|---|---|---|
| `decision:{user_id}:{txn_id}` | string | The response already given to this payer for this `txn_id` | 10 min |
| `dec:{decision_id}` | string | The working copy of a decision: action, challenge, guardian request, hold, whether paid | 24 h |
| `chal:{challenge_id}`, `otp:{challenge_id}` | string | Step-up challenge state; hash of the one-time code | 24 h; 5 min |
| `greq:{request_id}` | string | Guardian request state | 24 h |
| `pair_block:{user_id}:{vpa_id}` | string | A decline that stands for this payer and payee | until it lapses |
| `guardian:{user_id}` | hash | The payer's guardian, mode and threshold | none |
| `vpa_in:…`, `vpa_in1:…` | sorted set | Payers, and first-time payers, of a UPI ID by time | trimmed |
| `vpa_ev:{tenant}:{vpa}:{kind}` | sorted set | Confirmed fraud, reports and near-misses by time | trimmed |
| `calls_24h:{phone_hash}`, `caller_risk:{phone_hash}` | sorted set, string | Calls from unknown numbers; a caller's risk level | 24 h; none |
| `msg_vpas:{user_id}` | sorted set | UPI IDs named in messages the user flagged | 7 days |
| `demo_ovr:{user_id}` | hash | Session values set by a staged demo scenario | 30 min |
| `deactivated:{kind}:{id}` | string | A deactivated account, checked on every request | none |
| `rl:{bucket}:{who}` | counter | Requests in the current window, per address or per person, for each rate limit | the window |
| `user_block:{user_id}` | hash | The payer's never-pay list: payee → 0, or the time the block lifts (F-33) | none |
| `revoked_sid:{session}` | string | A signed-out session (F-35) | until the session would have ended |
| `tgen:{kind}:{id}` | string | The account's current token generation, when above 0 | none |
| `paying:{decision_id}` | string | A payment in progress, so the same decision cannot be paid twice at once | 30 s |
| `nm:{tenant}:{vpa}:{user_id}` | string | This payer has already lowered this payee's reputation today | 24 h |
| `trends:{tenant}:{locale}` | string | The week's trends, as served (F-34) | 5 min |

The cache is in the API process and starts empty. Start-up rebuilds everything above that is durable from PostgreSQL, including never-pay lists, signed-out sessions and token generations. With `EVENT_BUS=redis` the rebuild leaves the `stream:*` keys alone, since the event streams live in the same Redis.

## 3. Event topics

Names are the same on every event bus implementation ([04, section 2.3](04-architecture.md#23-event-bus-abstraction)). Every event carries `event_id`, `occurred_at` and `tenant_id`.

| Topic | Published by | Consumed by | Payload, main fields |
|---|---|---|---|
| `txn.assessed` | Orchestrator | Outcome logger, reputation engine, guardian scheduler, case summariser, dashboard bridge | The request, the full decision, the feature vector |
| `txn.outcome` | Gateway | Outcome logger, reputation engine | `decision_id`, `user_action`, `final_status` |
| `call.events` | Call correlator | Outcome logger | The call event |
| `msg.checked` | Message checker | Outcome logger, reputation engine | `check_id`, `user_id`, verdict, entities |
| `fraud.reports` | Product API | Reputation engine | The report, or the analyst's label |
| `reputation.updated` | Reputation engine | Dashboard bridge, consortium publisher | `vpa_id`, old and new band and score, cause |
| `guardian.events` | Orchestrator, product API | Guardian scheduler | Request created, responded, link changed |
| `hold.events` | Orchestrator, product API | Hold scheduler | Hold created, cancelled, confirmed |
| `sim.transactions` | Simulator | Replay driver | A synthetic payment and its context |

## 4. Feature catalogue (v1)

The feature schema is published as `contracts/features.v1.json`. "Default" is the value used when the source is unavailable; defaults are neutral, never alarming.

| Group | Feature | Type | Source | Default |
|---|---|---|---|---|
| Transaction | `amount` | number | Request | required |
| | `log_amount` | number | Computed | — |
| | `hour_of_day` | 0–23 | Computed, India time | — |
| | `day_of_week` | 0–6 | Computed | — |
| | `has_note` | 0/1 | Request | 0 |
| Payer behaviour | `amount_z` | number | `user_prof` | 0 |
| | `amount_over_p95` | ratio | `user_prof` | 1 |
| | `txn_count_1h` | count | `vel` | 0 |
| | `txn_count_24h` | count | `vel` | 0 |
| | `new_payees_24h` | count | `vel` | 0 |
| | `new_payees_10m` | count | `vel` | 0 |
| | `account_age_days` | days | `user_prof` | 365 |
| | `balance_fraction` | 0–1 | `user_prof` | 0 |
| Relationship | `is_new_payee` | 0/1 | `known_payee` | 1 |
| | `prior_txn_count` | count | `known_payee` | 0 |
| | `days_since_first_paid` | days | `known_payee` | 0 |
| Payee reputation | `vpa_age_days` | days | `vpa_dir` | 180 |
| | `p_bad` | 0–1 | `vpa_rep` | 0.01 |
| | `maturity` | 0–1 | `vpa_rep` | 0 |
| | `fan_in_1h` | count | `vpa_rep` | 0 |
| | `fan_in_24h` | count | `vpa_rep` | 0 |
| | `first_time_payer_ratio_24h` | 0–1 | `vpa_rep` | 0.5 |
| | `reports_30d` | count | `vpa_rep` | 0 |
| | `confirmed_90d` | count | `vpa_rep` | 0 |
| | `near_miss_14d` | count | `vpa_rep` | 0 |
| | `is_verified_merchant` | 0/1 | `vpa_dir` | 0 |
| | `payee_is_individual` | 0/1 | `vpa_dir` | 1 |
| | `payee_on_denylist` | 0/1 | `vpa_dir` | 0 |
| Network (F-20) | `downstream_hop` | 1–3, or 9 | `vpa_graph` | 9 |
| | `shares_device_with_reported` | 0/1 | `vpa_graph` | 0 |
| | `passthrough_ratio` | 0–1 | `vpa_graph` | 0 |
| | `community_risk_share` | 0–1 | `vpa_graph` | 0 |
| Consortium (F-24) | `consortium_reported` | 0/1 | `consortium` | 0 |
| | `consortium_p_bad` | 0–1 | `consortium` | 0 |
| Device | `device_is_new` | 0/1 | `dev` | 0 |
| | `device_age_days` | days | `dev` | 365 |
| Session (F-18) | `vpa_entry_method` | category | Request | `typed` |
| | `app_switch_count` | count | Request | 0 |
| | `time_on_pay_screen_s` | seconds | Request | 30 |
| | `amount_edit_count` | count | Request | 0 |
| Call (F-05) | `call_active` | 0/1 | `call_flag` | 0 |
| | `mins_since_call_end` | minutes | `call_flag` | 999 |
| | `call_duration_s` | seconds | `call_flag` | 0 |
| | `caller_unknown` | 0/1 | `call_flag` | 0 |
| | `caller_risk` | 0–3 | `call_flag` | 0 |
| | `unknown_calls_24h` | count | `calls_24h` | 0 |
| Message (F-16) | `msg_flag_active` | 0/1 | `msg_flag` | 0 |
| | `msg_scam_prob` | 0–1 | `msg_flag` | 0 |
| | `mins_since_msg_flag` | minutes | `msg_flag` | 9999 |
| | `payee_in_flagged_msg` | 0/1 | `msg_vpas` | 0 |
| Name (F-22) | `name_match_score` | 0–1 | Computed | 1 |
| | `claimed_name_institutional` | 0/1 | Computed | 0 |
| Note (F-06) | `note_scam_prob` | 0–1 | Text model | 0 |
| | `note_label` | category | Text model | `benign` |

54 features. `vpa_entry_method` takes `typed`, `pasted`, `contact`, `qr`, `link` or `merchant_collect`.

## 5. Seed data

Loaded by `python manage.py simulate`, which `bootstrap` runs. Everything is fictional. UPI handles use `@banka` and `@bankb`, which are not real handles.

### 5.1 Tenants and staff

| Tenant | Staff (email, role) |
|---|---|
| `bank_a` — Bank A | `analyst@banka.demo` (analyst), `admin@banka.demo` (admin), `auditor@banka.demo` (auditor) |
| `bank_b` — Bank B | `analyst@bankb.demo` (analyst), `admin@bankb.demo` (admin) |

Passwords are set from `.env` at seed time and are never committed.

### 5.2 Demo users

| Name | Tenant | Segment, age band | Language | Role in the demo |
|---|---|---|---|---|
| Meera Joshi | Bank A | elderly, 60+ | Hindi | The protected payer. Guardian: Arjun, co-approve. |
| Arjun Joshi | Bank A | salaried, 26–59 | English | Meera's guardian. |
| Kavita Rao | Bank A | small business, 26–59 | English | Impersonation and name-mismatch scenarios. |
| Rohan Das | Bank A | student, 18–25 | English | Second payer for the "system learns" step. |
| Sunita Verma | Bank B | salaried, 26–59 | Hindi | Consortium scenario: protected by a Bank A report. |

Each has a 90-day history of ordinary payments from the simulator, a known device, and saved payees including a landlord and a grocery shop.

### 5.3 Demo payees

| UPI ID | Registered name | Kind | Starting state |
|---|---|---|---|
| `sharma.rent@banka` | Sharma Properties | person | Long relationship with Meera. Rule R-09 applies. |
| `freshmart@banka` | FreshMart Groceries | merchant, verified | Trusted |
| `newcafe@banka` | Third Wave Cafe | merchant | No history. A legitimate new shop. |
| `refund.desk7@bankb` | Rakesh Kumar | person | Young, fan-in spike. Used with the claimed name "SBI Refund Desk". |
| `cyber.cell.verify@bankb` | Imran Shaikh | person | Young. Digital-arrest scenario. Forwards to a reported account. |
| `quick.returns@banka` | Deepak Nair | person | Investment-scam scenario. |
| `layer2.acct@bankb` | Pooja Singh | person | Reported. Receives forwarded funds; anchors the graph. |
| `vendor.payments@banka` | Anil Traders | person | New. Impersonation scenario, claimed name "Mehta Suppliers Pvt Ltd". |

### 5.4 Catalogues

- 7 scam families (six named plus `UNCLASSIFIED`) with prototypes, messages in English and Hindi, and audio keys.
- 20 reason codes with templates.
- A caller-risk list of 50 seeded numbers across the three risk levels.
- A denylist of 10 UPI IDs.
- 3 rehearsal scripts in English.
- 40 example messages for the message checker's test set: 20 scam, 20 benign, mixed English and Hinglish.
- One `policy_config` row per tenant with the defaults.

## 6. Rehearsal script format

```json
{
  "start": "n1",
  "nodes": {
    "n1": {
      "speaker": "scammer",
      "text": "This is Inspector Verma from the Cyber Cell. A parcel in your name contained illegal items.",
      "options": [
        { "label": "Oh no. What should I do?", "next": "n2", "verdict": "risky",
          "feedback": "Engaging keeps you on the call. Real police do not phone to announce an arrest." },
        { "label": "I will call my local police station myself.", "next": "end_safe", "verdict": "safe",
          "feedback": "Right. Hang up and call a number you looked up yourself." }
      ]
    }
  },
  "signs": [
    "They say you are in trouble and must stay on the call.",
    "They ask you to move money to a 'safe' or 'verification' account.",
    "They tell you not to talk to family or the bank."
  ]
}
```

## 7. Retention in the prototype

| Data | Kept |
|---|---|
| Transactions, decisions, outcomes | Until `python manage.py demo-reset`, which restores the seeded state |
| Message check text | Same. In a pilot, only the hash, labels and entities are kept unless the user agrees to share the text. |
| Call events | Same. Metadata only, hashed numbers. |
| Audit log | Never reset by `demo-reset` |
| Caller reports, never-pay lists | Cleared by `demo-reset`, which also puts the seeded caller levels, PINs and passwords back |
| Device keys, signed-out sessions | Kept across `demo-reset`: a phone stays enrolled and a signed-out token stays refused |
| Redis | Per the expiry column in section 2 |
