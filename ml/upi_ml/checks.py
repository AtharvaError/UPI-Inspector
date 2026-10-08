"""Checks that can be run at any time (docs/10, section 6):

    python manage.py preflight         everything a demo needs, in one go
    python manage.py security-check    how the service is configured, and what it refuses when asked
    python manage.py i18n-check        the Hindi and English strings line up; writes the sheet for a reviewer
    python manage.py phone-check       the payer app is ready for a real phone
    python manage.py llm-check         the case-summary providers
    python manage.py bus-check         the Redis event bus

Each prints what it checked and how it came out, and exits non-zero if something failed. None of them
changes demo data. What a check cannot establish from a laptop it says so, rather than passing silently.
"""
from __future__ import annotations

import asyncio
import csv
import json
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

from app.config import ROOT, get_settings

OK, WARN, FAIL, NOTE = "PASS", "WARN", "FAIL", "NOTE"
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
PLACEHOLDER = re.compile(r"\{[a-z_0-9]+\}")
ZERO = "00000000-0000-0000-0000-000000000000"


class Report:
    def __init__(self, title: str) -> None:
        self.rows: list[tuple[str, str, str]] = []
        print(f"\n{title}\n{'-' * len(title)}")

    def add(self, status: str, what: str, detail: str = "") -> bool:
        self.rows.append((status, what, detail))
        print(f"  {status:4s}  {what}" + (f"\n        {detail}" if detail else ""))
        return status != FAIL

    def check(self, cond: bool, what: str, detail: str = "", warn: bool = False) -> bool:
        return self.add(OK if cond else (WARN if warn else FAIL), what, "" if cond else detail)

    @property
    def failed(self) -> int:
        return sum(1 for s, _, _ in self.rows if s == FAIL)

    @property
    def warned(self) -> int:
        return sum(1 for s, _, _ in self.rows if s == WARN)

    def done(self) -> bool:
        n = len([r for r in self.rows if r[0] in (OK, FAIL, WARN)])
        print(f"  => {n - self.failed - self.warned} passed, {self.warned} to look at, {self.failed} failed")
        return self.failed == 0


def _api_up(base: str) -> dict | None:
    try:
        r = httpx.get(f"{base}/readyz", timeout=3)
        return r.json() if r.status_code in (200, 503) else None
    except Exception:
        return None


def lan_addresses() -> list[str]:
    """This machine's addresses on the local network (what a phone on the same Wi-Fi would connect to)."""
    found: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))            # no packet is sent; this only picks the outgoing interface
        found.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except Exception:
        pass
    return found


# ================================================================== security
PUBLIC = {("GET", "/"), ("GET", "/healthz"), ("GET", "/readyz"), ("GET", "/metrics"), ("GET", "/v1/auth/config")}
DEMO_PUBLIC = {("GET", "/v1/auth/demo-users"), ("GET", "/v1/auth/demo-staff")}
STAFF_ONLY = ("/v1/decisions", "/v1/near-misses", "/v1/reviews", "/v1/payees", "/v1/graph", "/v1/consortium", "/v1/policy",
              "/v1/metrics", "/v1/model-health", "/v1/audit", "/v1/admin", "/v1/security", "/v1/demo/scenario",
              "/v1/demo/call", "/v1/demo/message", "/v1/demo/replay", "/v1/demo/drift", "/v1/demo/chaos", "/v1/demo/reset",
              "/v1/demo/metrics-reset", "/v1/auth/change-password", "/v1/signals/call", "/v1/risk/clearance")
ADMIN_ONLY = ("/v1/admin", "/v1/demo/scenario", "/v1/demo/call", "/v1/demo/message", "/v1/demo/replay", "/v1/demo/drift",
              "/v1/demo/chaos", "/v1/demo/reset", "/v1/demo/metrics-reset", "/v1/signals/call", "/v1/risk/clearance")
ADMIN_ONLY_WRITES = ("/v1/policy/mode", "/v1/policy/thresholds", "/v1/policy/block-mode", "/v1/model-health/reload")
USER_ONLY = ("/v1/users/me", "/v1/guardians", "/v1/guardian/", "/v1/holds", "/v1/rehearsals", "/v1/stepup", "/v1/signals/message",
             "/v1/lookup", "/v1/callers", "/v1/reports", "/mock-psp/v1/pay", "/v1/auth/verify-pin", "/v1/auth/change-pin",
             "/v1/auth/device-key", "/v1/auth/devices", "/v1/demo/my-call")
REFUSED = (400, 401, 403, 404, 405, 409, 411, 413, 422, 425, 429)


def _routes() -> list[tuple[str, str]]:
    from app.main import create_app

    out = []
    for path, item in create_app().openapi()["paths"].items():
        for method in item:
            if method.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                out.append((method.upper(), path))
    return sorted(out)


def _fill(path: str) -> str:
    def sub(m: re.Match) -> str:
        name = m.group(1)
        return "nobody%40banka" if name == "vpa" else "x" if name in ("code", "key_id") else ZERO

    return re.sub(r"\{([a-z_]+)\}", sub, path)


def _sweep(c: httpx.Client, routes, headers: dict, only, allow: set) -> list[str]:
    """Call every route in `only` with these credentials. Nothing may come back as a success, and nothing may
    make the service fall over."""
    bad = []
    for method, path in routes:
        if not only(method, path) or (method, path) in allow:
            continue
        try:
            r = c.request(method, _fill(path), headers=headers, json={} if method in ("POST", "PUT", "PATCH") else None)
        except Exception as exc:
            bad.append(f"{method} {path}: {exc}")
            continue
        if r.status_code not in REFUSED:
            bad.append(f"{method} {path} -> {r.status_code}")
    return bad


def security_check(base: str = "http://localhost:8000") -> bool:
    from app.posture import checks

    s = get_settings()
    rep = Report("Configuration (the same checks the service runs when it starts)")
    for row in checks(s):
        rep.add({"ok": OK, "info": NOTE, "warn": WARN, "fail": WARN if s.demo_mode else FAIL}[row["status"]], row["label"], row["detail"])
    ok = rep.done()

    if not _api_up(base):
        print(f"\nThe API is not running at {base}, so the live checks were skipped. Start it with:  python manage.py api")
        return ok

    rep = Report(f"Front door ({base})")
    c = httpx.Client(base_url=base, timeout=15)
    r = c.get("/v1/auth/config")
    h = {k.lower(): v for k, v in r.headers.items()}
    cfg = r.json()
    rep.check(all(k in h for k in ("x-content-type-options", "x-frame-options", "referrer-policy", "content-security-policy")),
              "responses carry the security headers", f"got: {sorted(h)}")
    rep.check(h.get("cache-control") == "no-store", "responses are not cached", f"cache-control: {h.get('cache-control')}")
    rep.check("server" not in h, "responses do not name the server software", f"server: {h.get('server')}")
    rep.check(c.get("/readyz", headers={"Host": "evil.example"}).status_code == 400 or "*" in s.allowed_hosts,
              "an unknown Host name is refused (DNS rebinding)", "ALLOWED_HOSTS=* turns this off", warn="*" in s.allowed_hosts)
    evil = c.get("/v1/auth/config", headers={"Origin": "https://evil.example"})
    rep.check("access-control-allow-origin" not in {k.lower() for k in evil.headers}, "a foreign web page cannot read API responses (CORS)",
              f"CORS_ORIGINS={s.cors_origins}")
    big = c.post("/v1/auth/login", content=b'{"phone":"' + b"9" * (s.max_body_bytes + 10) + b'"}', headers={"Content-Type": "application/json"})
    rep.check(big.status_code == 413, "an oversized request is refused", f"got {big.status_code}")
    rep.check(c.get("/docs").status_code == (200 if s.demo_mode else 404), "API documentation is exposed only in demo mode")
    scope = s.demo_login.strip().lower()
    if s.demo_mode and scope == "local":
        via = c.get("/v1/auth/demo-users", headers={"X-Forwarded-For": "203.0.113.9"})
        rep.check(via.status_code == 404, "passwordless demo sign-in is refused through a proxy or tunnel", f"got {via.status_code}")
    elif s.demo_mode and scope in ("lan", "any"):
        rep.add(WARN, f"passwordless demo sign-in is open to {'the local network' if scope == 'lan' else 'anyone'} (DEMO_LOGIN={scope})")
    else:
        rep.check(c.get("/v1/auth/demo-users").status_code == 404, "passwordless demo sign-in is off")
    for label, token in (("no token", None), ("a made-up token", "abc.def.ghi"),
                         ('a token with "alg": "none"', "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJ4Iiwia2luZCI6InN0YWZmIiwicm9sZSI6ImFkbWluIn0.")):
        r = c.get("/v1/admin/staff", headers={"Authorization": f"Bearer {token}"} if token else {})
        rep.check(r.status_code == 401, f"{label} is refused", f"got {r.status_code}")
    ok = rep.done() and ok

    rep = Report("Who may call what (every endpoint, with the wrong credentials)")
    routes = _routes()
    allow = PUBLIC | (DEMO_PUBLIC if cfg.get("demo_login") else set()) | ({("GET", "/docs"), ("GET", "/openapi.json")} if s.demo_mode else set())
    bad = _sweep(c, routes, {}, lambda m, p: True, allow)
    rep.check(not bad, f"no credentials: {len(routes) - len(allow & set(routes))} endpoints refuse", "; ".join(bad[:8]))
    if cfg.get("demo_login"):
        tokens: dict[str, str] = {}
        users = c.get("/v1/auth/demo-users").json()["users"]
        tokens["user"] = c.post("/v1/auth/demo-login", json={"user_id": users[0]["user_id"]}).json()["token"]
        for st in c.get("/v1/auth/demo-staff").json()["staff"]:
            if st["role"] in ("analyst", "auditor") and st["role"] not in tokens:
                tokens[st["role"]] = c.post("/v1/auth/demo-staff-login", json={"email": st["email"]}).json()["token"]
        auth = lambda who: {"Authorization": f"Bearer {tokens[who]}"}
        staff_only = lambda m, p: p.startswith(STAFF_ONLY)
        bad = _sweep(c, routes, auth("user"), staff_only, set())
        rep.check(not bad, f"an app user: {sum(1 for m, p in routes if staff_only(m, p))} staff endpoints refuse", "; ".join(bad[:8]))
        admin_only = lambda m, p: p.startswith(ADMIN_ONLY) or (m != "GET" and p.startswith(ADMIN_ONLY_WRITES))
        for role in ("analyst", "auditor"):
            if role in tokens:
                bad = _sweep(c, routes, auth(role), admin_only, set())
                rep.check(not bad, f"an {role}: {sum(1 for m, p in routes if admin_only(m, p))} admin endpoints refuse", "; ".join(bad[:8]))
        user_only = lambda m, p: p.startswith(USER_ONLY)
        if "analyst" in tokens:
            bad = _sweep(c, routes, auth("analyst"), user_only, set())
            rep.check(not bad, f"a member of staff: {sum(1 for m, p in routes if user_only(m, p))} app-user endpoints refuse", "; ".join(bad[:8]))
        if "auditor" in tokens:
            writes = lambda m, p: m != "GET" and p.startswith(("/v1/decisions", "/v1/reviews", "/v1/policy", "/v1/model-health", "/v1/complaint-packs"))
            bad = _sweep(c, routes, auth("auditor"), writes, {("POST", "/v1/policy/simulate")})
            rep.check(not bad, "an auditor cannot change anything", "; ".join(bad[:8]))
        other = c.post("/v1/auth/demo-login", json={"user_id": users[1]["user_id"]}).json()["token"]
        mine = c.get("/v1/users/me/payments", headers=auth("user")).json()["payments"]
        if mine and mine[0].get("decision_id"):
            r = c.post("/v1/risk/outcome", headers={"Authorization": f"Bearer {other}"}, json={"decision_id": mine[0]["decision_id"], "user_action": "ABORTED"})
            rep.check(r.status_code in (403, 409), "one person cannot act on another's payment", f"got {r.status_code}")
        for t in list(tokens.values()) + [other]:                       # leave no sessions behind
            c.post("/v1/auth/logout", headers={"Authorization": f"Bearer {t}"})
        r = c.get("/v1/users/me", headers=auth("user"))
        rep.check(r.status_code == 401, "a signed-out token stops working at once", f"got {r.status_code}")
    else:
        rep.add(NOTE, "role checks skipped", "they sign in through the demo accounts, which this address may not use; "
                                             "python manage.py e2e covers them (AT-31, AT-37 to AT-39)")
    return rep.done() and ok


# ================================================================== translations
def _ts_dicts() -> tuple[dict, dict]:
    src = (ROOT / "apps/mobile/src/i18n.ts").read_text(encoding="utf-8")
    line = re.compile(r'^\s*("(?:[^"\\]|\\.)*")\s*:\s*("(?:[^"\\]|\\.)*"),?\s*$')
    out = []
    for name in ("EN", "HI"):
        body = src[src.index(f"const {name}: Dict = {{"):]
        body = body[: body.index("\n};")]
        d = {}
        for ln in body.splitlines():
            m = line.match(ln)
            if m:
                d[json.loads(m.group(1))] = json.loads(m.group(2))
        out.append(d)
    return out[0], out[1]


def _flatten(d, prefix="") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        elif isinstance(v, list):
            for i, x in enumerate(v):
                out[f"{key}[{i}]"] = str(x)
        else:
            out[key] = str(v)
    return out


SAME_OK = {"app.name"}                       # strings that are meant to be identical in both languages
LATIN_OK = {"upi", "id", "pin", "otp", "sms", "qr", "kyc", "whatsapp", "name", "bank", "ok", "en", "inspector", "cybercrime",
            "gov", "in", "live", "offline", "psp", "cbi"}


def i18n_check(write: bool = True) -> bool:
    rep = Report("English and Hindi strings")
    en, hi = _ts_dicts()
    sets = [("app", en, hi)]
    cat_en = json.loads((ROOT / "services/risk-api/i18n/en.json").read_text(encoding="utf-8"))
    cat_hi = json.loads((ROOT / "services/risk-api/i18n/hi.json").read_text(encoding="utf-8"))
    sets.append(("server", _flatten(cat_en), _flatten(cat_hi)))
    scripts = json.loads((ROOT / "infra/seed/rehearsals.json").read_text(encoding="utf-8"))
    by = {(x["code"], x["locale"]): x for x in scripts}
    r_en, r_hi = {}, {}
    for (code, loc), x in by.items():
        if loc == "hi" and (code, "en") in by:
            r_hi.update(_flatten({code: {"title": x["title"], "script": x["script"]}}))
            e = by[(code, "en")]
            r_en.update(_flatten({code: {"title": e["title"], "script": e["script"]}}))
    sets.append(("practice script", r_en, r_hi))
    # values that are codes the program reads, not words a person reads
    codes = (".verdict", ".next", ".risky_answer", ".speaker", ".start", ".family", ".code")
    sets = [(w, {k: v for k, v in a.items() if not k.endswith(codes)}, {k: v for k, v in b.items() if not k.endswith(codes)})
            for w, a, b in sets]
    missing = sorted({c for c, l in by if l == "en"} - {c for c, l in by if l == "hi"})

    rows, total = [], 0
    for where, a, b in sets:
        total += len(a)
        only_en, only_hi = sorted(set(a) - set(b)), sorted(set(b) - set(a))
        rep.check(not only_en and not only_hi, f"{where}: the same {len(a)} keys in both languages",
                  f"missing in Hindi: {only_en[:6]}; extra in Hindi: {only_hi[:6]}")
        ph = [k for k in a if k in b and sorted(PLACEHOLDER.findall(a[k])) != sorted(PLACEHOLDER.findall(b[k]))]
        rep.check(not ph, f"{where}: every {{placeholder}} appears in both", f"differs in: {ph[:8]}")
        untranslated = [k for k in a if k in b and not DEVANAGARI.search(b[k]) and re.search(r"[A-Za-z]{3}", b[k])
                        and k not in SAME_OK and not set(re.findall(r"[a-z]+", b[k].lower())) <= LATIN_OK]
        rep.check(not untranslated, f"{where}: no Hindi string left in English", f"{untranslated[:8]}")
        for k in sorted(a):
            if k in b and (DEVANAGARI.search(b[k]) or b[k] != a[k]):
                latin = sorted({w for w in re.findall(r"[A-Za-z]{3,}", PLACEHOLDER.sub("", b[k])) if w.lower() not in LATIN_OK})
                note = []
                if latin:
                    note.append("Latin words: " + ", ".join(latin))
                if len(a[k]) <= 26 and len(b[k]) > max(30, 1.9 * len(a[k])):
                    note.append("much longer than the English; check it fits a button")
                rows.append((where, k, a[k], b[k], "", "", "; ".join(note)))
    rep.check(not missing, "every practice script has a Hindi version", f"English only: {', '.join(missing)}", warn=True)
    flagged = sum(1 for r in rows if r[6])
    rep.add(NOTE, f"{len(rows)} Hindi strings in all; {flagged} carry a note for the reviewer")
    rep.add(WARN, "the Hindi is a draft by a non-native writer and has not been reviewed by a native speaker",
            "these checks find missing and broken strings; they cannot judge wording, tone or grammar")
    if write:
        out = ROOT / "docs" / "hindi-review.csv"
        with out.open("w", encoding="utf-8-sig", newline="") as f:          # the BOM lets Excel open it as UTF-8
            w = csv.writer(f)
            w.writerow(["Where", "Key", "English", "Hindi (draft)", "OK? (y/n)", "Correction", "Note"])
            w.writerows(rows)
        rep.add(NOTE, f"review sheet written: {out.relative_to(ROOT)}", "open it in a spreadsheet, fill the last columns, "
                                                                        "then: python manage.py i18n-apply docs/hindi-review.csv")
    return rep.done()


def i18n_apply(path: str) -> bool:
    """Put a reviewer's corrections from the sheet back into the app and the server catalogue."""
    rep = Report(f"Applying corrections from {path}")
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("Correction") or "").strip()]
    if not rows:
        rep.add(NOTE, "no corrections in the sheet")
        return rep.done()
    ts_path = ROOT / "apps/mobile/src/i18n.ts"
    ts = ts_path.read_text(encoding="utf-8")
    start = ts.index("const HI: Dict = {")
    head, body = ts[:start], ts[start:]
    cat_path = ROOT / "services/risk-api/i18n/hi.json"
    cat = json.loads(cat_path.read_text(encoding="utf-8"))
    done = skipped = 0
    for r in rows:
        key, new, old = r["Key"], r["Correction"].strip(), r["Hindi (draft)"]
        if sorted(PLACEHOLDER.findall(new)) != sorted(PLACEHOLDER.findall(r["English"])):
            rep.add(FAIL, f"{key}: the correction changes the placeholders", new)
            continue
        if r["Where"] == "app":
            was = f"  {json.dumps(key)}: {json.dumps(old, ensure_ascii=False)},"
            if was in body:
                body = body.replace(was, f"  {json.dumps(key)}: {json.dumps(new, ensure_ascii=False)},", 1)
                done += 1
            else:
                skipped += 1
        elif r["Where"] == "server":
            node, parts = cat, re.split(r"\.(?![^\[]*\])", key)
            try:
                for part in parts[:-1]:
                    node = node[part]
                last = parts[-1]
                m = re.match(r"(.+)\[(\d+)\]$", last)
                if m:
                    node[m.group(1)][int(m.group(2))] = new
                else:
                    node[last] = new
                done += 1
            except (KeyError, IndexError, TypeError):
                skipped += 1
        else:
            skipped += 1
    ts_path.write_text(head + body, encoding="utf-8")
    cat_path.write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rep.add(OK if done else NOTE, f"{done} strings updated", f"{skipped} could not be matched (the text changed since the sheet was written, "
                                                           "or it is a practice script: edit infra/seed/rehearsals.json by hand)" if skipped else "")
    return rep.done()


# ================================================================== phone readiness
NATIVE = ["expo-camera", "expo-local-authentication", "expo-speech", "expo-secure-store", "expo-crypto", "expo-screen-capture",
          "expo-file-system", "expo-sharing"]


def _node(args: list[str], cwd: Path, timeout: int = 600) -> tuple[int, str]:
    try:
        p = subprocess.run(["node", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout + p.stderr)[-1500:]
    except FileNotFoundError:
        return 127, "node is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def phone_check(base: str = "http://localhost:8000", export: bool = False) -> bool:
    s = get_settings()
    app = ROOT / "apps" / "mobile"
    rep = Report("Payer app on a real phone: what can be checked from this laptop")
    pkg = json.loads((app / "package.json").read_text(encoding="utf-8"))
    deps = pkg.get("dependencies", {})
    rep.add(NOTE, f"Expo SDK {deps.get('expo', '?')}, React Native {deps.get('react-native', '?')}")
    missing = [m for m in NATIVE if m not in deps or not (app / "node_modules" / m).exists()]
    rep.check(not missing, f"the {len(NATIVE)} native modules the app uses are installed", f"missing: {missing}. Run npm install in apps/mobile")
    code, out = _node(["node_modules/typescript/bin/tsc", "--noEmit"], app, 300)
    rep.check(code == 0, "the app type-checks", out[-600:])
    code, out = _node(["scripts/check-hmac.ts"], app, 60)
    rep.check(code == 0, "the device-proof arithmetic matches the server's (HMAC-SHA256)", out[-300:])
    if export:
        dest = ROOT / ".cache" / "android-export"
        t0 = time.time()
        code, out = _node(["node_modules/expo/bin/cli", "export", "--platform", "android", "--output-dir", str(dest)], app, 900)
        size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e6 if dest.exists() else 0
        rep.check(code == 0 and size > 0.5, f"the Android bundle builds ({size:.1f} MB in {time.time() - t0:.0f}s)", out[-800:])
    else:
        rep.add(NOTE, "Android bundle not built this time", "add --export to build it (about two minutes); it proves the "
                                                            "native-module imports resolve for Android")
    ips = lan_addresses()
    rep.check(bool(ips), "this laptop has an address on a local network", "connect to Wi-Fi or a phone hotspot", warn=True)
    up = _api_up(base)
    rep.check(bool(up), f"the API is running at {base}", "start it with: python manage.py api", warn=True)
    for ip in ips[:2]:
        url = f"http://{ip}:{base.rsplit(':', 1)[-1].split('/')[0]}"
        reach = _api_up(url)
        rep.check(bool(reach), f"the API answers on {url} (the address a phone would use)",
                  "it must listen on 0.0.0.0 (manage.py api does) and Windows Firewall must allow Python on private networks", warn=True)
    scope = s.demo_login.strip().lower()
    rep.add(NOTE, f"DEMO_LOGIN={scope}: " + ("a phone signs in with a phone number and PIN (the seeded PIN is SEED_USER_PIN in .env); "
                                             "the tap-to-sign-in demo accounts appear only on this laptop" if scope == "local"
                                             else "a phone on the same network sees the tap-to-sign-in demo accounts" if scope == "lan"
                                             else "see .env.example"))
    rep.add(WARN, "between phone and laptop the traffic is plain HTTP",
            "use your own phone's hotspot, not venue Wi-Fi: on a shared network a PIN or token could be read in transit")
    rep.add(WARN, "nothing here has run on a physical phone",
            "camera scan, fingerprint prompt, secure storage, screen-capture block and the spoken warning can only be "
            "confirmed on a device: follow docs/13-phone-test.md and write down what happens")
    return rep.done()


# ================================================================== case-summary providers
def llm_check(live: bool = False) -> bool:
    s = get_settings()
    rep = Report("Case-summary providers")
    order = [p.strip() for p in s.llm_providers.split(",") if p.strip()]
    rep.add(NOTE, f"order: {' > '.join(order)}")
    have = {"gemini": bool(s.gemini_api_key), "groq": bool(s.groq_api_key), "ollama": bool(s.ollama_url)}
    for name in ("gemini", "groq", "ollama"):
        rep.add(NOTE, f"{name}: {'configured' if have[name] else 'not configured'}",
                "" if have[name] else {"gemini": "set GEMINI_API_KEY in .env (free key from Google AI Studio)",
                                       "groq": "set GROQ_API_KEY in .env (free key from console.groq.com)",
                                       "ollama": "install Ollama, pull a model, set OLLAMA_URL=http://localhost:11434"}[name])
    try:
        r = httpx.get((s.ollama_url or "http://localhost:11434").rstrip("/") + "/api/tags", timeout=2)
        models = [m["name"] for m in r.json().get("models", [])]
        rep.add(NOTE, f"an Ollama server is running here with {len(models)} model(s)", ", ".join(models[:6]) or "pull one: ollama pull llama3.2")
    except Exception:
        rep.add(NOTE, "no Ollama server is running on this machine")
    code = subprocess.call([sys.executable, "-m", "pytest", "-q", "-k", "gemini or groq or ollama or provider or replies",
                            str(ROOT / "services/risk-api/tests/test_bus_and_llm.py")],
                           cwd=str(ROOT), env={**__import__("os").environ, "PYTHONPATH": f"{ROOT / 'services/risk-api'}{__import__('os').pathsep}{ROOT / 'ml'}"},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rep.check(code == 0, "request format, reply parsing, fact check and fallback order (against a stand-in server)")
    if not any(have.values()):
        rep.add(WARN, "no real provider has been called", "summaries come from the built-in template until a key or an Ollama URL is set")
        return rep.done()
    if not live:
        rep.add(NOTE, "a provider is configured; add --live to make one real call per provider")
        return rep.done()

    async def run() -> None:
        from app import db
        from app.runtime import rt
        from app.workers import summariser

        rt.settings = s
        await db.init_pool()
        try:
            did = await db.fetchval("SELECT decision_id FROM risk_decisions WHERE tier IN ('STEP_UP','BLOCK') ORDER BY created_at DESC LIMIT 1")
            if not did:
                rep.add(WARN, "no flagged decision in the database to summarise")
                return
            data = await summariser.build_input(str(did))
            prompt = summariser.PROMPT + json.dumps(data, indent=1)
            for name in order:
                fn = summariser.PROVIDERS.get(name)
                if fn is None or not have.get(name):
                    continue
                t0 = time.time()
                try:
                    got = await fn(prompt)
                    reply = summariser._parse(got[0]) if got else None
                    good = bool(reply) and summariser._check(reply, data)
                    rep.check(good, f"{name} ({got[1] if got else '?'}) answered in {time.time() - t0:.1f}s and its reply passed the fact check",
                              "the reply was not valid JSON, or mentioned a number or party that is not in the input")
                    if good:
                        print("        " + summariser._readable(str(reply["summary"]))[:300])
                except Exception as exc:
                    rep.add(FAIL, f"{name} failed after {time.time() - t0:.1f}s", str(exc)[:200])
        finally:
            await db.close_pool()

    asyncio.run(run())
    return rep.done()


# ================================================================== event bus
def bus_check(url: str = "", events: int = 500) -> bool:
    """Run the Redis Streams bus for real. `url` is a redis:// address; empty means the in-process emulator."""
    from app.bus import RedisStreamBus
    from app.cache import make_cache

    target = url or get_settings().redis_url
    kind = "the in-process emulator (fakeredis)" if target.startswith("fakeredis") else target
    rep = Report(f"Redis event bus on {kind}")

    async def run() -> None:
        r = make_cache(target)
        try:
            await r.ping()
        except Exception as exc:
            rep.add(FAIL, "cannot reach Redis", str(exc)[:200])
            return
        prefix = f"buscheck.{uuid.uuid4().hex[:6]}"
        bus = RedisStreamBus(r)
        seen: dict[str, list] = {"a": [], "b": []}

        async def a(ev):
            seen["a"].append(ev["n"])

        async def b(ev):
            seen["b"].append(ev["n"])

        bus.subscribe(f"{prefix}.t", "a", a)
        bus.subscribe(f"{prefix}.t", "b", b)
        await bus.start()
        t0 = time.perf_counter()
        for i in range(events):
            await bus.publish(f"{prefix}.t", {"n": i})
        t_pub = time.perf_counter() - t0
        await bus.drain(30)
        t_all = time.perf_counter() - t0
        rep.check(seen["a"] == list(range(events)) and seen["b"] == list(range(events)),
                  f"{events} events reached two consumer groups, once each and in order",
                  f"group a got {len(seen['a'])}, group b got {len(seen['b'])}")
        left = await bus.pending()
        rep.check(left == 0, "every event was acknowledged", f"{left} still pending")
        rep.add(NOTE, f"published at {events / t_pub:,.0f}/s; all handled {t_all:.2f}s after the first publish")
        await bus.stop()
        try:
            await r.delete(f"stream:{prefix}.t")
            await r.aclose()
        except Exception:
            pass

    asyncio.run(run())
    if target.startswith("fakeredis") or "emulator" in kind:
        rep.add(WARN, "this was an emulator, not a Redis server", "it shows the code path works; it says nothing about speed, "
                                                                  "blocking reads or failover on a real server")
    rep.add(WARN, "the Kafka bus has never been run", "no broker is available on this laptop; treat EVENT_BUS=kafka as unfinished")
    return rep.done()


def redis_emulator(port: int = 6390) -> None:
    """A Redis look-alike on a TCP port, for trying REDIS_URL=redis://127.0.0.1:<port>/0 without installing Redis."""
    from fakeredis import TcpFakeServer

    srv = TcpFakeServer(("127.0.0.1", port), server_type="redis")
    print(f"Redis emulator (fakeredis) listening on 127.0.0.1:{port}. It keeps nothing after it stops. Ctrl+C to stop.\n"
          f"Try:  set REDIS_URL=redis://127.0.0.1:{port}/0 and EVENT_BUS=redis in .env, then  python manage.py api")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


# ================================================================== everything a demo needs
def preflight(base: str = "http://localhost:8000") -> bool:
    s = get_settings()
    rep = Report("Before a demo")
    rep.check((ROOT / ".env").exists(), ".env exists", "run: python manage.py setup")
    placeholders = [k for k in ("jwt_secret", "phone_hash_key", "consortium_key") if getattr(s, k) == "change-me"]
    rep.check(not placeholders, "secrets are set", f"placeholders: {placeholders}; run: python manage.py setup")
    active = (s.model_path / "ACTIVE")
    rep.check(active.exists() and (s.model_path / active.read_text().strip()).exists() if active.exists() else False,
              "a scoring model is trained and active", "run: python manage.py train")
    rep.check((s.model_path / "text.joblib").exists(), "the message classifier is trained", "run: python manage.py text")

    async def database() -> None:
        from app import db

        try:
            await db.init_pool()
            users = await db.fetchval("SELECT count(*) FROM users WHERE is_demo")
            rep.check(users >= 4, f"PostgreSQL is up with the demo accounts ({users})", "run: python manage.py bootstrap")
            new = await db.fetchval("SELECT to_regclass('public.user_blocks') IS NOT NULL AND to_regclass('public.caller_reports') IS NOT NULL")
            rep.check(bool(new), "the tables added since the first build are present", "start the API once: it adds them", warn=True)
            await db.close_pool()
        except Exception as exc:
            rep.add(FAIL, "PostgreSQL could not be reached", str(exc)[:200])

    asyncio.run(database())
    up = _api_up(base)
    if up:
        rep.check(up["ready"] and not up["degraded"], f"the API is ready (model {up['model_version']}, {up['rules_version']})", f"{up}")
    else:
        rep.add(WARN, f"the API is not running at {base}", "python manage.py api")
    dash = ROOT / "apps/dashboard"
    build = dash / ".next" / "BUILD_ID"
    if build.exists():
        newest = max((f.stat().st_mtime for f in (dash / "src").rglob("*") if f.is_file()), default=0)
        rep.check(build.stat().st_mtime >= newest, "the dashboard build is newer than its source", "run: npm run build in apps/dashboard", warn=True)
    else:
        rep.add(FAIL, "the dashboard has not been built", "run: npm install and npm run build in apps/dashboard")
    rep.check((ROOT / "apps/mobile/node_modules/expo").exists(), "the payer app's dependencies are installed", "run: npm install in apps/mobile")
    for name, port in (("dashboard", 3000), ("payer app", 8081)):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            rep.add(OK, f"the {name} is being served on port {port}")
        except OSError:
            rep.add(NOTE, f"the {name} is not running on port {port}", f"python manage.py {'dashboard' if port == 3000 else 'app --web'}")
    ok = rep.done()
    from app.posture import checks

    rep = Report("Security settings")
    for row in checks(s):
        if row["status"] in ("warn", "fail"):
            rep.add(WARN if s.demo_mode else FAIL, row["label"], row["detail"])
    if not rep.rows:
        rep.add(OK, "nothing to change for a demo on this laptop")
    ok = rep.done() and ok
    ips = lan_addresses()
    print("\nAddresses\n---------")
    print(f"  dashboard   http://localhost:3000\n  payer app   http://localhost:8081   (second person: http://localhost:8081/?slot=2)\n  API         {base}")
    if ips:
        print(f"  on a phone  Expo Go, same network as this laptop ({ips[0]}); see: python manage.py phone-check")
    print("\n  Run the other checks when something changes:  security-check, i18n-check, phone-check, llm-check, bus-check, test, golden, e2e")
    return ok
