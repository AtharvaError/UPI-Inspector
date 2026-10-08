"""Unit tests for the security layer (F-35) and the community helpers (F-31 to F-33). No database, no network."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import time

import jwt
import pytest

from app import guard, posture, security
from app.config import Settings, get_settings
from app.routers.community import VPA_RE, bucket, risk_from_reports
from app.routers.stepup import device_proof
from app.security import ApiError, Principal


# ------------------------------------------------------------------ PINs and passwords
@pytest.mark.parametrize("pin", ["000000", "111111", "123456", "654321", "890123", "121212", "123123", "482284", "112233"])
def test_weak_pins_are_refused(pin):
    assert security.weak_pin(pin) is not None


@pytest.mark.parametrize("pin", ["482913", "739104", "905162", "318447"])
def test_ordinary_pins_are_accepted(pin):
    assert security.weak_pin(pin) is None


def test_a_pin_taken_from_the_phone_number_is_refused():
    assert security.weak_pin("765001", "9876500111") is not None
    assert security.weak_pin("482913", "9876500111") is None


@pytest.mark.parametrize("pw,email", [("short1!", ""), ("password123", ""), ("aaaaaaaaaaaa1", ""), ("onlyletterslong", ""),
                                       ("ritubose-2026", "ritubose@banka.example")])
def test_weak_passwords_are_refused(pw, email):
    assert security.weak_password(pw, email) is not None


def test_a_reasonable_password_is_accepted():
    assert security.weak_password("Tmp-4f9a1c07be", "temp.user@banka.example") is None


def test_lockouts_double_and_stop_at_a_day():
    secs = [security.lock_seconds(n) for n in range(12)]
    assert secs[:4] == [300, 600, 1200, 2400]
    assert secs == sorted(secs) and secs[-1] == 24 * 3600


def test_hash_and_verify_and_the_unknown_account_path():
    h = security.hash_secret("482913")
    assert security.verify_secret(h, "482913") and not security.verify_secret(h, "482914")
    assert security.verify_secret(None, "482913") is False          # verified against a dummy, never true
    assert security.same("abc", "abc") and not security.same("abc", "abd") and not security.same(None, "abc")


def test_keyed_values_depend_on_purpose_and_every_part():
    a = security.keyed("otp", "challenge-1", "123456")
    assert a == security.keyed("otp", "challenge-1", "123456") and len(a) == 64
    assert a != security.keyed("otp", "challenge-2", "123456")       # a code is bound to its challenge
    assert a != security.keyed("other", "challenge-1", "123456")
    assert security.keyed("otp", "ab", "c") != security.keyed("otp", "a", "bc")


# ------------------------------------------------------------------ tokens
def _principal(kind="user"):
    return Principal(kind=kind, sub="11111111-1111-1111-1111-111111111111", tenant_id="bank_a",
                     role="user" if kind == "user" else "analyst", name="Test")


def test_token_round_trip_carries_session_and_generation():
    t = security.make_token(_principal(), amr="pin", gen=3)
    p = security.read_token(t)
    assert (p.kind, p.gen, p.amr) == ("user", 3, "pin") and p.sid and p.exp > time.time()


def test_staff_and_user_tokens_have_their_own_lifetimes():
    s = get_settings()
    now = time.time()
    user = security.read_token(security.make_token(_principal("user")))
    staff = security.read_token(security.make_token(_principal("staff")))
    assert abs(user.exp - now - s.user_token_minutes * 60) < 5
    assert abs(staff.exp - now - s.staff_token_minutes * 60) < 5


def test_a_refreshed_token_keeps_its_session_and_cannot_outlive_it():
    s = get_settings()
    started = int(time.time()) - s.session_max_hours * 3600 + 90      # the session has 90 seconds left
    first = security.read_token(security.make_token(_principal(), sid="session-1", auth_time=started))
    assert first.sid == "session-1" and first.auth_time == started
    assert first.exp <= started + s.session_max_hours * 3600


def _refused(token: str) -> bool:
    try:
        security.read_token(token)
    except ApiError as e:
        return e.status_code == 401
    return False


def test_forged_tokens_are_refused():
    s = get_settings()
    good = security.make_token(_principal())
    head, body, sig = good.split(".")
    claims = jwt.decode(good, options={"verify_signature": False})
    assert _refused(".".join([head, body, sig[:-3] + ("AAA" if not sig.endswith("AAA") else "BBB")]))
    assert _refused(jwt.encode(claims, "some-other-secret-of-sufficient-length-000", algorithm="HS256"))
    assert _refused(jwt.encode({**claims, "exp": int(time.time()) - 10}, s.jwt_secret, algorithm="HS256"))
    assert _refused(jwt.encode({**claims, "iss": "someone-else"}, s.jwt_secret, algorithm="HS256"))
    assert _refused(jwt.encode({k: v for k, v in claims.items() if k != "sid"}, s.jwt_secret, algorithm="HS256"))
    assert _refused("eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0." + body + ".")          # "alg": "none"
    assert _refused("not-a-token")


# ------------------------------------------------------------------ where a request comes from
def test_address_classes():
    assert security.ip_class("127.0.0.1") == "loopback" and security.ip_class("::1") == "loopback"
    assert security.ip_class("::ffff:127.0.0.1") == "loopback"
    for ip in ("10.0.2.2", "192.168.1.20", "172.16.5.4", "fe80::1"):
        assert security.ip_class(ip) == "private", ip
    assert security.ip_class("8.8.8.8") == "public" and security.ip_class("203.0.113.9") == "public"
    assert security.ip_class("evil.example") == "unknown"


def test_a_forwarded_request_is_never_local():
    assert security.forwarded({"x-forwarded-for": "203.0.113.9"})
    assert security.forwarded({"forwarded": "for=1.2.3.4"}) and security.forwarded({"cf-connecting-ip": "1.2.3.4"})
    assert not security.forwarded({"host": "localhost:8000", "user-agent": "x"})


class _Req:
    def __init__(self, ip, headers=None):
        self.client = type("C", (), {"host": ip})()
        self.headers = headers or {}


def _with(**kw):
    get_settings.cache_clear()
    base = get_settings()
    return base.model_copy(update=kw)


def test_demo_sign_in_scope(monkeypatch):
    cases = [  # (demo_mode, demo_login, ip, forwarded, expected)
        (True, "local", "127.0.0.1", False, True), (True, "local", "192.168.1.9", False, False),
        (True, "local", "127.0.0.1", True, False), (True, "lan", "192.168.1.9", False, True),
        (True, "lan", "8.8.8.8", False, False), (True, "lan", "192.168.1.9", True, False),
        (True, "any", "8.8.8.8", True, True), (True, "off", "127.0.0.1", False, False),
        (False, "any", "127.0.0.1", False, False), (False, "local", "127.0.0.1", False, False),
    ]
    for demo, scope, ip, fwd, want in cases:
        monkeypatch.setattr(security, "get_settings", lambda d=demo, s=scope: _with(demo_mode=d, demo_login=s))
        req = _Req(ip, {"x-forwarded-for": "203.0.113.9"} if fwd else {})
        assert security.demo_login_allowed(req) is want, (demo, scope, ip, fwd)


# ------------------------------------------------------------------ the guard
def test_cors_local_allows_this_machine_and_private_addresses_only():
    for origin in ("http://localhost:3000", "http://127.0.0.1:8081", "http://192.168.1.20:8081", "http://10.0.2.2:8081",
                   "https://localhost", "http://app.localhost:3000"):
        assert guard.origin_allowed(origin, "local"), origin
    for origin in ("https://evil.example", "http://localhost.evil.example", "http://192.168.1.20.evil.example:80",
                   "http://172.32.0.1:3000", "null", "http://localhost:3000/path"):
        assert not guard.origin_allowed(origin, "local"), origin
    assert guard.origin_allowed(None, "local")                       # no Origin: not a web page (the phone app)


def test_cors_lists_and_wildcard():
    assert guard.origin_allowed("https://dash.bank.example", "https://dash.bank.example, https://app.bank.example")
    assert not guard.origin_allowed("http://localhost:3000", "https://dash.bank.example")
    assert guard.origin_allowed("https://anything.example", "*")
    assert guard.cors_setting("local") == ([], guard.LOCAL_ORIGIN)
    assert guard.cors_setting("https://a.example/") == (["https://a.example"], None)


def test_host_names_the_service_answers_to():
    g = guard.Guard(None, _with(allowed_hosts="api.bank.example", public_base_url="http://localhost:8000"))
    for host in ("localhost:8000", "127.0.0.1:8000", "[::1]:8000", "192.168.1.20:8000", "10.0.2.2:8000", "api.bank.example",
                 "demo.localhost:8000"):
        assert g.host_ok(host), host
    for host in ("evil.example", "localhost.evil.example:8000", "8.8.8.8:8000", ""):
        assert not g.host_ok(host), host
    assert guard.Guard(None, _with(allowed_hosts="*")).host_ok("evil.example")


def _call_guard(settings, *, method="GET", path="/v1/x", headers=None, scheme="http", client="127.0.0.1"):
    """Run one request through the guard with a stand-in application; return (status, headers, reached_app)."""
    reached = []

    async def app(scope, receive, send):
        reached.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b"{}"})

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    raw = [(b"host", b"localhost:8000")] if not headers or "host" not in headers else []
    raw += [(k.encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "method": method, "path": path, "headers": raw, "scheme": scheme, "client": (client, 5000)}
    asyncio.run(guard.Guard(app, settings)(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, bool(reached)


def test_guard_adds_headers_and_passes_good_requests():
    status, headers, reached = _call_guard(_with(rate_limit_per_ip=0))
    assert status == 200 and reached
    assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"
    assert headers["cache-control"] == "no-store" and "default-src 'none'" in headers["content-security-policy"]
    assert "strict-transport-security" not in headers                 # only promised over HTTPS


def test_guard_leaves_the_api_documentation_usable_and_sets_hsts_over_https():
    _, docs, _ = _call_guard(_with(rate_limit_per_ip=0), path="/docs")
    assert "content-security-policy" not in docs and docs["x-frame-options"] == "DENY"
    _, https, _ = _call_guard(_with(rate_limit_per_ip=0), scheme="https")
    assert "max-age=" in https["strict-transport-security"]


def test_guard_refuses_bad_hosts_big_bodies_and_plain_http_when_https_is_required():
    s = _with(rate_limit_per_ip=0)
    assert _call_guard(s, headers={"host": "evil.example"})[::2] == (400, False)
    assert _call_guard(s, method="POST", headers={"content-length": str(s.max_body_bytes + 1)})[::2] == (413, False)
    assert _call_guard(s, method="POST", headers={"content-length": "12"})[::2] == (200, True)
    assert _call_guard(s, method="POST", headers={"transfer-encoding": "chunked"})[::2] == (411, False)
    assert _call_guard(s, method="POST", headers={"content-length": "abc"})[::2] == (400, False)
    strict = _with(rate_limit_per_ip=0, require_https=True)
    assert _call_guard(strict, client="192.168.1.9", headers={"host": "192.168.1.20:8000"})[::2] == (403, False)
    assert _call_guard(strict, client="127.0.0.1")[::2] == (200, True)            # this machine may still use HTTP
    assert _call_guard(strict, client="192.168.1.9", headers={"host": "192.168.1.20:8000"}, scheme="https")[::2] == (200, True)
    # a client cannot talk its way past the check by claiming, in a header, to have come over HTTPS
    assert _call_guard(strict, client="192.168.1.9", headers={"host": "192.168.1.20:8000", "x-forwarded-proto": "https"})[::2] == (403, False)


# ------------------------------------------------------------------ posture
def test_posture_of_the_laptop_demo_has_no_failures():
    s = _with(demo_mode=True, demo_login="local", cors_origins="local", jwt_secret="x" * 48, phone_hash_key="y" * 32,
              consortium_key="z" * 32, service_api_keys="psp:" + "k" * 32, trust_client_biometric=False, allowed_hosts="")
    rows = posture.checks(s)
    assert not [r for r in rows if r["status"] == "fail"], rows
    assert {r["key"] for r in rows} >= {"secrets", "demo_login", "cors", "hosts", "https", "sessions", "rate_limit", "stepup"}
    assert posture.posture_warnings(s) == [] or all("fail" not in w for w in posture.posture_warnings(s))


def test_unsafe_settings_stop_the_service_outside_demo_mode():
    good = dict(demo_mode=False, cors_origins="https://dash.bank.example", jwt_secret="x" * 48, phone_hash_key="y" * 32,
                consortium_key="z" * 32, service_api_keys="psp:" + "k" * 32, require_https=True, trust_client_biometric=False)
    assert isinstance(posture.posture_warnings(_with(**good)), list)
    for bad in ({"cors_origins": "*"}, {"jwt_secret": "short"}, {"jwt_secret": "change-me"}, {"require_https": False},
                {"trust_client_biometric": True}, {"service_api_keys": "psp:tiny"}):
        with pytest.raises(RuntimeError):
            posture.posture_warnings(_with(**{**good, **bad}))


def test_the_same_unsafe_settings_only_warn_in_a_demo():
    s = _with(demo_mode=True, demo_login="any", cors_origins="*", trust_client_biometric=True, jwt_secret="x" * 48,
              phone_hash_key="y" * 32, consortium_key="z" * 32, service_api_keys="psp:" + "k" * 32)
    lines = posture.posture_warnings(s)
    assert len(lines) >= 3 and any("CORS" in line or "web page" in line for line in lines)


# ------------------------------------------------------------------ community helpers
def test_reports_alone_raise_a_number_slowly():
    assert risk_from_reports(0, 0) == 0 and risk_from_reports(1, 0) == 0 and risk_from_reports(1, 1) == 0
    assert risk_from_reports(0, 3) == 1 and risk_from_reports(1, 2) == 1
    assert risk_from_reports(0, 50) == 1                              # typed-in reports never go beyond the lowest level
    assert risk_from_reports(2, 0) == 2 and risk_from_reports(4, 9) == 2
    assert risk_from_reports(5, 0) == 3


def test_counts_are_shown_as_ranges():
    assert [bucket(n) for n in (0, 1, 2, 3, 9, 10, 500)] == ["0", "1-2", "1-2", "3-9", "3-9", "10+", "10+"]


def test_upi_id_shape():
    for ok in ("freshmart@banka", "cyber.cell.verify@bankb", "a1@upi", "first-last_9@okbank"):
        assert VPA_RE.match(ok), ok
    for bad in ("nobank@", "@banka", "two@at@signs", "space in@banka", "UPPER@BANKA", "x@b", "<script>@banka"):
        assert not VPA_RE.match(bad), bad


def test_device_proof_is_an_hmac_over_the_challenge():
    secret = "0b" * 32
    want = hmac.new(bytes.fromhex(secret), b"challenge-1", hashlib.sha256).hexdigest()
    assert device_proof(secret, "challenge-1") == want
    assert device_proof(secret, "challenge-2") != want               # a proof cannot be replayed on another challenge
    assert device_proof("0c" * 32, "challenge-1") != want


# ------------------------------------------------------------------ payment clearance
def _clearance(monkeypatch, snap: dict, chal: str | None = None, greq: str | None = None, ttl: int = 600):
    """Ask whether the PSP may execute the payment described by this working copy of a decision."""
    import fakeredis

    from app.routers import risk

    r = fakeredis.FakeAsyncRedis(decode_responses=True)

    async def run():
        if chal:
            await r.set("chal:c1", '{"status": "%s"}' % chal)
        if greq:
            await r.set("greq:g1", '{"status": "%s"}' % greq)

        async def snapshot(_id):
            return snap

        monkeypatch.setattr(risk, "get_snapshot", snapshot)
        monkeypatch.setattr(risk.rt, "store", type("S", (), {"r": r})())
        monkeypatch.setattr(risk.rt, "settings", _with(clearance_ttl_s=ttl))
        return await risk.clearance("d1")

    return asyncio.run(run())


def _snap(**kw) -> dict:
    return {"action": "ALLOW", "enforced": True, "paid": False, "created_at": time.time(), "challenge_id": "c1",
            "guardian_request_id": "g1", "guardian_required": False, **kw}


def test_a_fresh_allowed_payment_is_cleared_and_a_stale_one_is_not(monkeypatch):
    assert _clearance(monkeypatch, _snap())["cleared"] is True
    old = _clearance(monkeypatch, _snap(created_at=time.time() - 601))
    assert old["cleared"] is False and old["expired"] is True
    assert _clearance(monkeypatch, _snap(created_at=time.time() - 601), ttl=0)["cleared"] is True     # 0 turns the limit off


def test_a_step_up_clears_only_once_passed_and_only_for_a_while(monkeypatch):
    assert _clearance(monkeypatch, _snap(action="STEP_UP"), chal="OPEN")["cleared"] is False
    assert _clearance(monkeypatch, _snap(action="STEP_UP"), chal="LOCKED")["cleared"] is False
    made = time.time() - 900                                   # the decision is 15 minutes old ...
    passed = _snap(action="STEP_UP", created_at=made, stepup_passed_at=time.time() - 30)
    assert _clearance(monkeypatch, passed, chal="PASSED")["cleared"] is True     # ... but it was passed 30 seconds ago
    late = _snap(action="STEP_UP", created_at=made, stepup_passed_at=time.time() - 700)
    assert _clearance(monkeypatch, late, chal="PASSED")["expired"] is True


def test_guardian_approval_is_required_and_restarts_the_clock(monkeypatch):
    need = dict(guardian_required=True, created_at=time.time() - 900)
    assert _clearance(monkeypatch, _snap(**need), greq="PENDING")["cleared"] is False
    assert _clearance(monkeypatch, _snap(**need), greq="DENIED")["cleared"] is False
    assert _clearance(monkeypatch, _snap(**need, guardian_approved_at=time.time() - 20), greq="APPROVED")["cleared"] is True
    assert _clearance(monkeypatch, _snap(**need, guardian_approved_at=time.time() - 700), greq="APPROVED")["expired"] is True


def test_stopped_paid_and_blocked_payments_are_never_cleared(monkeypatch):
    for action in ("HOLD", "DECLINE"):
        assert _clearance(monkeypatch, _snap(action=action))["cleared"] is False
    assert _clearance(monkeypatch, _snap(paid=True))["cleared"] is False
    # shadow mode clears what it would have stopped, but not what the payer blocked, and not a stale decision
    assert _clearance(monkeypatch, _snap(action="DECLINE", enforced=False))["cleared"] is True
    assert _clearance(monkeypatch, _snap(action="DECLINE", enforced=False, user_block=True))["cleared"] is False
    assert _clearance(monkeypatch, _snap(enforced=False, created_at=time.time() - 601))["cleared"] is False
