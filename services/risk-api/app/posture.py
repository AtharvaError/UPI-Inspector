"""How this deployment is configured, seen from a security point of view (docs/11, section 1.9).

The same checks are used three ways: at start-up (warnings in the log, and a refusal to start outside demo
mode if a check is fatal), by the dashboard's Security page, and by `python manage.py security-check`.
"""
from __future__ import annotations

from .config import Settings

OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"


def checks(s: Settings) -> list[dict]:
    """One row per setting that matters. `fail` means: do not run outside a demo like this."""
    out: list[dict] = []

    def add(key: str, label: str, status: str, detail: str) -> None:
        out.append({"key": key, "label": label, "status": status, "detail": detail})

    placeholders = [k.upper() for k in ("jwt_secret", "phone_hash_key", "consortium_key") if getattr(s, k) == "change-me"]
    if placeholders:
        add("secrets", "Secrets", FAIL, f"Placeholder values in use: {', '.join(placeholders)}. Run python manage.py setup.")
    elif len(s.jwt_secret) < 32:
        add("secrets", "Secrets", FAIL, "JWT_SECRET is shorter than 32 characters.")
    else:
        add("secrets", "Secrets", OK, "Token, phone-hash and consortium keys are set and long enough.")

    keys = s.api_keys
    if not keys:
        add("service_keys", "Service keys", WARN, "No SERVICE_API_KEYS: a PSP cannot call the service.")
    elif any(len(k) < 24 for k in keys):
        add("service_keys", "Service keys", FAIL, "A service API key is shorter than 24 characters.")
    else:
        add("service_keys", "Service keys", OK, f"{len(keys)} service key(s), compared in constant time.")

    scope = s.demo_login.strip().lower()
    if not s.demo_mode:
        add("demo_mode", "Demo mode", OK, "Off. No demo control, no API documentation, no passwordless sign-in.")
    else:
        add("demo_mode", "Demo mode", INFO, "On: short timers, demo control, API documentation at /docs, "
                                            "one-time codes returned to the app.")
        if scope in ("", "off", "false", "0", "no"):
            add("demo_login", "Passwordless demo sign-in", OK, "Off. Everyone signs in with a PIN or password.")
        elif scope == "any":
            add("demo_login", "Passwordless demo sign-in", FAIL,
                "Open to anyone who can reach this port. Set DEMO_LOGIN=local.")
        elif scope == "lan":
            add("demo_login", "Passwordless demo sign-in", WARN,
                "Open to every device on the same network. Use only on a network you control.")
        else:
            add("demo_login", "Passwordless demo sign-in", OK, "This machine only. Phones sign in with a PIN.")

    cors = s.cors_origins.strip()
    if "*" in cors:
        add("cors", "Web origins (CORS)", FAIL, "Any web page may call the API from a visitor's browser.")
    elif cors.lower() == "local":
        add("cors", "Web origins (CORS)", OK, "Only pages served from this machine or a private address.")
    else:
        add("cors", "Web origins (CORS)", OK, f"Only: {cors}")

    if "*" in s.allowed_hosts:
        add("hosts", "Host check", WARN, "Off (ALLOWED_HOSTS=*). The API answers to any host name.")
    else:
        extra = f" and {s.allowed_hosts}" if s.allowed_hosts.strip() else ""
        add("hosts", "Host check", OK, f"Answers to this machine, private addresses{extra} only.")

    if s.require_https:
        add("https", "Transport", OK, "Plain HTTP is refused from everywhere but this machine.")
    else:
        add("https", "Transport", INFO if s.demo_mode else FAIL,
            "Plain HTTP is accepted. Fine on one laptop; on a shared network PINs and tokens could be read in transit.")

    add("sessions", "Sessions", OK,
        f"App sessions lapse after {s.user_token_minutes} min idle, dashboard sessions after {s.staff_token_minutes} min; "
        f"all end {s.session_max_hours} h after sign-in. Sign-out and PIN or password changes revoke at once.")
    add("lockout", "Sign-in lockout", OK, "5 wrong attempts lock an account for 5 minutes, doubling each time up to a day.")

    if s.rate_limit_per_ip > 0:
        add("rate_limit", "Rate limits", OK, f"{s.rate_limit_per_ip} requests a minute per address, plus limits on "
                                             "sign-in, codes, reports, look-ups and payments.")
    else:
        add("rate_limit", "Rate limits", WARN, "The per-address limit is off (RATE_LIMIT_PER_IP=0).")

    if s.trust_client_biometric:
        add("stepup", "Step-Up authentication", FAIL,
            "TRUST_CLIENT_BIOMETRIC=true: the app can pass a Step-Up just by saying the fingerprint matched.")
    else:
        add("stepup", "Step-Up authentication", OK,
            "One-time code, or a proof from a device enrolled with the PIN. Three wrong attempts close the challenge.")

    add("clearance", "Payment clearance", OK if s.clearance_ttl_s > 0 else WARN,
        f"A decision clears a payment for {s.clearance_ttl_s} s after its last approval; one payment per decision."
        if s.clearance_ttl_s > 0 else "Decisions never expire (CLEARANCE_TTL_S=0).")

    if s.redis_url.startswith("fakeredis"):
        add("cache", "Cache and event bus", INFO, f"In-process cache, '{s.event_bus}' bus: one API process, nothing on the network.")
    elif s.redis_url.startswith("rediss://"):
        add("cache", "Cache and event bus", OK, f"Redis over TLS, '{s.event_bus}' bus.")
    else:
        add("cache", "Cache and event bus", INFO if s.demo_mode else WARN,
            f"Redis without TLS, '{s.event_bus}' bus. Keep it on a private network.")
    return out


def posture_warnings(s: Settings) -> list[str]:
    """Log lines for start-up. Outside demo mode a failed check stops the service from starting."""
    rows = checks(s)
    fatal = [f"{r['label']}: {r['detail']}" for r in rows if r["status"] == FAIL]
    if fatal and not s.demo_mode:
        raise RuntimeError("Refusing to start outside demo mode. " + " | ".join(fatal))
    return [f"{r['label']}: {r['detail']}" for r in rows if r["status"] in (FAIL, WARN)]
