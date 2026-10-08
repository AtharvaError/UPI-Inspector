# 08 — Tech Stack and Free Services

[← Index](README.md)

## 1. The zero-cost rules

1. **Local first.** The whole prototype runs on a laptop with open-source software. Nothing in it needs an account anywhere.
2. **Free tiers only for a hosted copy**, and only ones that need **no payment card**. No hosted copy was built.
3. **Never attach a payment method** to any account used for this project. A free tier with no card cannot bill; it can only stop.
4. **Every outside service has a fallback** that is local or built in. If a free tier changes, a feature degrades; the prototype does not break.
5. **Only synthetic data leaves the laptop.** Free tiers may use what they are sent.

**As built, the cost is ₹0 and no account was created.** The only outside service the code can call is a language-model provider for case summaries, and none was configured.

## 2. Stack as built

Versions are those installed on the build laptop on 7 October 2026.

| Layer | Choice | Version | Used for |
|---|---|---|---|
| Service | Python, FastAPI, Pydantic, Uvicorn with `httptools` | 3.12.4, 0.142, 2.13, 0.54 | The hot path, the product API, the mock PSP, the demo endpoints |
| Workers | Python asyncio, in the API process | — | Decision logging, reputation, guardian and hold timers, graph, drift, fairness, summaries |
| Database | PostgreSQL 16, from the `pgserver` package | 16.2 | System of record. No install, no container. |
| Database access | `asyncpg`, plain SQL | 0.32 | One schema file; no ORM and no migration tool |
| Cache | `fakeredis`, in process, through the `redis` asyncio client | 2.39 | Features, flags, policy, idempotency. The same code runs against a real Redis. |
| Event bus | In-process queue | — | Decision and outcome streams |
| ML | XGBoost, scikit-learn, NumPy | 3.4.1, 1.8.0, 2.5 | Scoring, anomaly detection, text model |
| Explanations | XGBoost's built-in contribution output | — | Reasons, family attribution |
| Name matching | RapidFuzz | 3.14 | Payee name mismatch |
| Graph | NetworkX | 3.7 | Mule network features, communities |
| PDF | fpdf2 | 2.8.9 | Complaint pack |
| Real-time | `python-socketio` over ASGI | 5.17 | Live dashboard, guardian alerts, hold notices, demo overlays |
| Sign-in | Signed tokens (`pyjwt`), Argon2 hashes (`argon2-cffi`) | 2.15, 25.1 | Staff with roles; app users with phone and PIN |
| Training and simulation | pandas, PyArrow | 3.0, — | Replay, training rows, evaluation |
| Tests | pytest, httpx | 9.1, 0.28 | Unit tests; the acceptance and load tests are plain Python |
| Dashboard | Next.js, React, TypeScript, Redux Toolkit, Recharts, react-force-graph-2d, socket.io-client, lucide-react | 16.4, 19.3, 7.0, 2.13, 3.10, 1.29, 4.8, 1.52 | Analyst and admin pages. Plain CSS with the design tokens as CSS variables. |
| Payer app | Expo, React Native, React, TypeScript, Redux Toolkit, socket.io-client | SDK 57, 0.86.3, 19.2.3, 5.9, 2.13, 4.8 | Demo PSP app, on the web and in Expo Go |
| Payer app, device features | expo-speech, expo-local-authentication, expo-camera, expo-sharing, expo-file-system, expo-font, AsyncStorage | SDK 57 | Spoken warning, biometric step, QR scan, sharing the pack, fonts, the saved session |
| Language model | Gemini → Groq → Ollama → template | — | Case summaries only. Only the template has been run. |
| Task runner | `manage.py` | — | Every task. Windows has no `make`. |

### Planned, not used

| Planned | Why it was dropped |
|---|---|
| Docker, Docker Compose | Docker was not running on the build laptop. The embedded PostgreSQL and in-process cache need nothing installed. |
| Redis server, Redpanda (Kafka) | Replaced by in-process equivalents. Clients for both are written and untested. |
| SQLAlchemy, Alembic | One schema file applied by the bootstrap is enough for a prototype that is rebuilt from scratch. |
| SHAP library | XGBoost's own contribution output gives the same values for tree models. |
| DistilBERT, Transformers, ONNX Runtime | The lightweight text model was built; the heavy one was a stretch item. |
| MLflow, Airflow, n8n, Prometheus, Grafana | The model registry is a table; the System health page shows the metrics; `/metrics` serves them as text. |
| Tailwind | Plain CSS with token variables is enough for 13 pages. |
| Framer Motion | CSS transitions. The `motion` package is still listed in the dashboard's dependencies and is unused. |
| Reanimated, expo-router, i18next | React Native's `Animated`, a small stack navigator and a dictionary. Fewer moving parts in Expo Go. |
| Locust, Schemathesis | The load test is 100 lines of Python. No contract tests were written. |
| Auth0 | Built-in tokens. |
| GitHub Actions, Vercel, Render, Neon, Upstash | No repository was pushed and no hosted copy was built. |

Notes on choices that differ from the pitch deck:

- **Twilio** is not used. It offers trial credit, not a free tier, and it cannot see a user's carrier calls. One-time codes are shown in the app as a simulated SMS, and the "scam call" is a simulated call screen.
- **Kafka** is not used in the build. The bus sits behind one interface, so a Kafka client can replace the in-process queue.
- **AWS and GCP** are not used. Both need a payment card.
- **The UPI PSP sandbox** is replaced by the mock PSP in this repository.

## 3. Free services for a hosted copy (not built)

Kept as planning notes for when the team wants a public link. The limits were checked on 7 October 2026; re-check before relying on any of them.

| Need | Service | Free allowance | Card | What the limit means for us |
|---|---|---|---|---|
| API hosting | **Render**, free web service | 512 MB memory, a fraction of a CPU. Spins down after 15 minutes without traffic; about a minute to wake. 750 instance-hours a month. Ephemeral disk. No background workers. | No | One service: API and workers in one process, which is how the build already runs. Keep memory under 400 MB; the API process used about 450 MB on the build laptop, so it would need trimming. |
| PostgreSQL | **Neon**, free plan | 100 compute-hours per project a month, scales to zero when idle, 0.5 GB storage | No | The seeded database is about 310 MB on disk; fits, with little room. |
| Redis | **Upstash**, free plan | 256 MB, 500,000 commands a month | No | About 25 commands per assessment: roughly 20,000 assessments a month. No load tests against it. |
| Web hosting | **Vercel**, Hobby plan | Free for personal, non-commercial projects | No | Two projects: dashboard, payer web build. |
| Phone app | **Expo Go** | Free | No | Runs the app without a build. |
| Language model | **Gemini API** free tier, then **Groq** free plan | Rate-limited; content may be used to improve the provider's products | No | Synthetic data only. |
| Public URL for a laptop | **Cloudflare quick tunnel** | Free, no account | No | Lets a phone off the Wi-Fi reach the laptop. |

### What to avoid, and why

| Do not use | Reason |
|---|---|
| Hugging Face Spaces for the API | A Space that runs code needs a paid plan. Only static Spaces are free. |
| Render's free PostgreSQL | It expires 30 days after creation. |
| Managed Kafka | No free tier that can be relied on. |
| Twilio | Trial credit runs out, and trial accounts can only reach verified numbers. |
| AWS, GCP, Azure | Need a card. Free credits expire. |
| Any "free trial" | A trial is a deadline. |

## 4. Software to install

| Software | Purpose |
|---|---|
| Python 3.12 | The service, the models, the tests |
| Node.js 20 or later | The dashboard and the payer app. The build used Node 24. |
| Expo Go, on a phone | Only to run the payer app on a real phone |

Nothing else. PostgreSQL arrives as a Python package.

**On Windows with Smart App Control:** scikit-learn 1.9.1 would not load (a compiled file was blocked). `requirements.txt` pins scikit-learn below 1.9.

## 5. Accounts

None are needed. For AI case summaries from a real model, create one key:

- [ ] Google AI Studio (Gemini), or Groq

No payment method on any account.

## 6. Environment variables

`.env.example` lists these with comments. `python manage.py setup` copies it to `.env` and fills in fresh random secrets. `.env` is never committed. Without a `.env` the code falls back to placeholder secrets; the service warns about them in demo mode and refuses to start outside it.

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `local` | Label only |
| `DEMO_MODE` | `true` | Demo endpoints, API documentation, short timers, one-time codes returned to the app |
| `DEMO_LOGIN` | `local` | Who may use the passwordless demo sign-in: `off`, `local` (this machine), `lan` (private addresses too), `any` |
| `DATABASE_URL` | `pgserver` | `pgserver` runs the embedded PostgreSQL. Or any `postgresql://…` address. |
| `PG_DATA_DIR` | `.data/pg` | Where the embedded PostgreSQL keeps its files |
| `DATABASE_NAME` | `upi` | Database name |
| `REDIS_URL` | `fakeredis://` | In-process cache. Or `redis://…`. |
| `EVENT_BUS` | `memory` | `memory`; `redis` (run on an emulator only); `kafka` (never run) |
| `KAFKA_BROKERS` | — | When `EVENT_BUS=kafka` |
| `WORKERS_IN_PROCESS` | `true` | Workers inside the API process. The only supported value: a separate worker process is not built. |
| `JWT_SECRET` | generated | Signs tokens. At least 32 characters outside demo mode. |
| `USER_TOKEN_MINUTES`, `STAFF_TOKEN_MINUTES` | `30`, `60` | How long a session lasts without use |
| `SESSION_MAX_HOURS` | `12` | The longest a session can last |
| `CLEARANCE_TTL_S` | `600` | How long a decision clears a payment after its last approval |
| `TRUST_CLIENT_BIOMETRIC` | `false` | `true` accepts "fingerprint matched" from the app with no device proof. Never outside a demo. |
| `RATE_LIMIT_PER_IP` | `1200` | Requests a minute from one address; `0` turns it off |
| `MAX_BODY_BYTES` | `65536` | Largest request body |
| `ALLOWED_HOSTS` | empty | Extra host names the API answers to; `*` turns the check off |
| `REQUIRE_HTTPS` | `false` | `true` refuses plain HTTP from anywhere but this machine |
| `PHONE_HASH_KEY` | generated | Keyed hash for phone numbers |
| `CONSORTIUM_KEY` | generated | Keyed hash for consortium tokens |
| `SERVICE_API_KEYS` | generated | `name:key` pairs for the simulator and the mock PSP |
| `SEED_STAFF_PASSWORD` | generated | Password of the seeded staff accounts |
| `SEED_USER_PIN` | generated | App PIN of the seeded demo users |
| `MODEL_DIR` | `ml/artifacts` | Where model artefacts are read from |
| `TEXT_MODEL` | `tfidf` | Text classifier |
| `HOT_PATH_TIMEOUT_MS` | `300` | A decision that takes longer is abandoned |
| `MAX_INFLIGHT` | `48` | Assessments allowed in flight; beyond this a payment gets the safe default at once |
| `PG_FALLBACK_TIMEOUT_MS` | `40` | Cache-miss fallback |
| `GRAPH_JOB_INTERVAL_S` | `120` | Graph job period |
| `MONITOR_INTERVAL_S` | `900` | Drift and fairness period |
| `HOLD_SECONDS_DEMO` | `60` | Hold length in demo mode |
| `GUARDIAN_TIMEOUT_S_DEMO` | `120` | Guardian timeout in demo mode |
| `COOLOFF_SECONDS_DEMO` | `10` | Cool-off in demo mode |
| `UNBLOCK_SECONDS_DEMO` | `60` | The 24-hour wait before a never-pay block lifts, in demo mode |
| `LLM_PROVIDERS` | `gemini,groq,ollama,template` | Order to try. A provider with no key is skipped. |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | empty | Optional |
| `GROQ_API_KEY`, `GROQ_MODEL` | empty | Optional |
| `OLLAMA_URL`, `OLLAMA_MODEL` | empty | Optional |
| `CORS_ORIGINS` | `local` | Which web pages may call the API: `local` (this machine and private addresses), a list of origins, or `*` |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Used in links to complaint packs |

Front ends: `NEXT_PUBLIC_API_URL` for the dashboard (`apps/dashboard/.env.local`), and `EXPO_PUBLIC_API_URL` for the payer app. Without it the app uses the address it was loaded from, on port 8000.

Model identifiers are configuration, not code. Check which Gemini Flash model currently has a free tier before setting `GEMINI_MODEL`.

## 7. Libraries

### Python (`requirements.txt`)

| Library | Purpose |
|---|---|
| `fastapi`, `uvicorn`, `httptools`, `pydantic`, `pydantic-settings` | Web service and settings |
| `asyncpg`, `pgserver` | PostgreSQL access; the embedded server |
| `redis`, `fakeredis` | Cache client; the in-process cache |
| `python-socketio` | Real-time channels |
| `pyjwt`, `argon2-cffi` | Tokens; password and PIN hashing |
| `numpy`, `xgboost`, `scikit-learn`, `joblib` | Models |
| `rapidfuzz` | Name matching |
| `networkx` | Graph job |
| `fpdf2` | Complaint pack PDF |
| `httpx` | Calls to language-model providers; the test clients |
| `pyyaml` | Rules file, golden scenarios |
| `pandas`, `pyarrow` | Simulation, training, backfill |
| `pytest`, `pytest-asyncio` | Tests |
| `prometheus-client` | Listed, unused: the `/metrics` text is written by hand |

One requirements file serves the service and the training code. A hosted image would split them: the service does not need pandas to serve.

### Payer app (`apps/mobile/package.json`)

`expo`, `react-native`, `react`, `react-dom`, `react-native-web`, `@expo/metro-runtime`, `@reduxjs/toolkit`, `react-redux`, `socket.io-client`, `react-native-safe-area-context`, `@react-native-async-storage/async-storage`, `@expo/vector-icons`, `@expo-google-fonts/dm-sans`, `@expo-google-fonts/noto-sans-devanagari`, `expo-font`, `expo-speech`, `expo-local-authentication`, `expo-camera`, `expo-sharing`, `expo-file-system`, `expo-linking`, `expo-constants`, `expo-status-bar`, and, added with the security work, `expo-secure-store` (the system keystore), `expo-crypto` (random bytes and SHA-256 for the device proof) and `expo-screen-capture` (keeps PIN and code screens out of screenshots outside demo mode).

Everything here runs in Expo Go. Push notifications, real call-state detection and a key that only a fingerprint can unlock would need a development build and are not in the prototype.

### Dashboard (`apps/dashboard/package.json`)

`next`, `react`, `react-dom`, `@reduxjs/toolkit`, `react-redux`, `recharts`, `react-force-graph-2d`, `socket.io-client`, `lucide-react`, `typescript`. Versions are pinned to what was installed.

### Shared

`packages/design-tokens`: one `tokens.json`, and `build.mjs`, which writes `apps/dashboard/src/theme/tokens.css` and `apps/mobile/theme/tokens.ts`. See [03, section 1](03-ux-and-design.md#1-design-source).

## 8. Language-model use

One feature uses a language model: AI-written case summaries (F-27). It is optional, asynchronous and never part of a payment decision.

| Order | Provider | When it is used | Tested |
|---|---|---|---|
| 1 | Gemini API | A key is set and the provider answers | No |
| 2 | Groq | The first is rate-limited or down | No |
| 3 | Ollama, local | A URL is set | No |
| 4 | Template | Always available; no model | Yes |

The input names the parties `PAYER_1` and `PAYEE_1`, a reply is rejected if it contains a number or a token that is not in the input, and summaries are generated only for enforced Step-Up and Block decisions.
