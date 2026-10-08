# UPI Inspector

Real-time, explainable UPI fraud prevention: a risk decision before the PIN screen, with the reason in plain words and the scam named. Prototype by team Semicolonist for DECODE SIH 2026. Everything here runs on one laptop at zero cost, on synthetic data.

**Documentation:** [docs/README.md](docs/README.md). What was built, how it was checked and what was measured: [docs/12-as-built.md](docs/12-as-built.md).

## Run it

Needs Python 3.12 and Node 20 or later. First time, about 10 minutes:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py bootstrap

cd apps\dashboard
npm install
npm run build
cd ..\mobile
npm install
cd ..\..
```

Every time, in three terminals:

```
python manage.py api
python manage.py dashboard
python manage.py app --web
```

| Open | For |
|---|---|
| http://localhost:3000 | Analyst dashboard |
| http://localhost:8081 | Payer app |
| http://localhost:8081/?slot=2 | A second payer app with its own session (the guardian) |
| http://localhost:8000/docs | API reference |

Sign in with one click from the demo accounts listed on each sign-in screen. That is offered only in a browser on this laptop; from a phone, sign in with a demo phone number (`90000 00001` to `90000 00005`) and the PIN. Passwords and the PIN for the seeded accounts are in `.env`, which `bootstrap` writes.

## Check it

```
python manage.py preflight        # everything a demo needs, in one go
python manage.py test             # 120 unit tests
python manage.py golden           # 44 golden scenarios
python manage.py e2e              # 33 acceptance tests, against a running API
python manage.py security-check   # settings, the front door, and every endpoint with the wrong credentials
python manage.py load             # load test; run  python manage.py demo-reset  afterwards
```

Also: `i18n-check` (English and Hindi line up; writes the sheet for a reviewer), `phone-check` (is the app ready for a real phone), `llm-check` (case-summary providers), `bus-check` (the Redis event bus).

## Demo

The script is in [docs/10-testing-and-demo.md](docs/10-testing-and-demo.md#5-demo). Run `python manage.py demo-reset` just before presenting.

## What to know before relying on it

- Detection figures come from data the team generated. They show the pipeline works, not real-world accuracy.
- The payer app has been run in a browser, not on a phone. The test to run on two phones is [docs/13-phone-test.md](docs/13-phone-test.md).
- The Hindi text is a draft. The sheet for a native speaker is [docs/hindi-review.csv](docs/hindi-review.csv).
- No real language model has written a case summary: no key is configured, so the built-in template is used.
- It handles pretend money, and it was hardened as if the money were real: every control and every remaining weakness is in [docs/11, section 1](docs/11-security-risks-and-decisions.md#1-security-in-the-prototype). Between a phone and the laptop the traffic is plain HTTP, so use your own hotspot.
- The full list is in [docs/12-as-built.md, section 6](docs/12-as-built.md#6-known-gaps).
