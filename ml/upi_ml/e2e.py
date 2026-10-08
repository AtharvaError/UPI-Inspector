"""Acceptance tests against a running API (docs/10, section 2):  python manage.py e2e

They drive the real endpoints the two apps use, as the seeded demo users and staff, and reset the
demo state before and after. `--fast` skips the tests that wait on the hold timer.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import traceback
import uuid

import httpx

from app.config import get_settings

ORDER = ["SAFE", "WARNING", "STEP_UP", "BLOCK"]


class Fail(AssertionError):
    pass


def ok(cond: bool, msg: str) -> None:
    if not cond:
        raise Fail(msg)


class Ctx:
    def __init__(self, base: str) -> None:
        s = get_settings()
        self.base = base.rstrip("/")
        self.key = next(iter(s.api_keys), "")
        self.c = httpx.Client(base_url=self.base, timeout=30)
        self.users: dict[str, dict] = {}
        self.staff: dict[str, str] = {}
        self.demo = s.demo_mode
        self.settings = s

    def call(self, method: str, path: str, token: str | None = None, body=None, key: bool = False, tenant: str | None = None,
             expect: int | tuple = 200):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if key:
            headers["X-API-Key"] = self.key
        if tenant:
            headers["X-Tenant"] = tenant
        r = self.c.request(method, path, headers=headers, json=body)
        exp = (expect,) if isinstance(expect, int) else expect
        if r.status_code not in exp:
            raise Fail(f"{method} {path} -> {r.status_code} {r.text[:240]} (expected {exp})")
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r

    def login_all(self) -> None:
        for u in self.call("GET", "/v1/auth/demo-users")["users"]:
            t = self.call("POST", "/v1/auth/demo-login", body={"user_id": u["user_id"]})
            key = u["name"].split()[0].lower()
            me = self.call("GET", "/v1/users/me", t["token"])
            self.users[key] = {**u, "token": t["token"], "device": me["known_device_id"]}
        for s in self.call("GET", "/v1/auth/demo-staff")["staff"]:
            t = self.call("POST", "/v1/auth/demo-staff-login", body={"email": s["email"]})
            self.staff[s["email"]] = t["token"]

    @property
    def admin(self) -> str:
        return self.staff["admin@banka.demo"]

    def assess(self, who: str, payee: str, amount: float, *, entry: str = "typed", switches: int = 0, dwell: float = 20,
               note: str | None = None, claimed: str | None = None, txn: str | None = None, parent: str | None = None,
               device: str | None = None) -> dict:
        u = self.users[who]
        return self.call("POST", "/v1/risk/assess", u["token"], body={
            "txn_id": txn or f"E2E-{uuid.uuid4().hex[:14]}",
            "payer": {"user_id": u["user_id"], "vpa": u["vpa"], "device_id": device or u["device"]},
            "payee": {"vpa": payee, "claimed_name": claimed}, "amount": amount, "note": note,
            "session": {"vpa_entry_method": entry, "app_switch_count": switches, "time_on_pay_screen_s": dwell},
            "parent_decision_id": parent})

    def pay(self, who: str, d: dict) -> dict:
        return self.call("POST", "/mock-psp/v1/pay", self.users[who]["token"],
                         body={"txn_id": d["txn_id"], "decision_id": d["decision_id"], "pin": "1234"})

    def stage(self, scenario: str, who: str) -> dict:
        return self.call("POST", "/v1/demo/scenario", self.admin, body={"scenario": scenario, "user_id": self.users[who]["user_id"]})

    def wait(self, fn, seconds: float = 5.0, every: float = 0.25):
        end = time.time() + seconds
        last = None
        while time.time() < end:
            last = fn()
            if last:
                return last
            time.sleep(every)
        return last

    def detail(self, decision_id: str, tenant: str = "bank_a") -> dict:
        return self.wait(lambda: self._detail(decision_id, tenant), 5)

    def _detail(self, decision_id: str, tenant: str):
        r = self.c.get(f"/v1/decisions/{decision_id}", headers={"Authorization": f"Bearer {self.admin}", "X-Tenant": tenant})
        return r.json() if r.status_code == 200 else None

    def reset(self) -> None:
        self.call("POST", "/v1/demo/reset", key=True)


def rank(t: str) -> int:
    return ORDER.index(t)


def codes(d: dict) -> list[str]:
    return [r["code"] for r in d.get("reasons", [])]


# ------------------------------------------------------------------------------- tests
def at1(x: Ctx):
    """AT-1 Repeat payment to a long-standing payee is Safe and pays straight through."""
    d = x.assess("meera", "sharma.rent@banka", 12000, entry="contact", note="rent")
    ok(d["tier"] == "SAFE" and d["action"] == "ALLOW", f"expected SAFE/ALLOW, got {d['tier']}/{d['action']}")
    ok(d["reasons"] == [] and d["scam_family"] is None, "a Safe decision must not carry reasons")
    ok(x.pay("meera", d)["status"] == "SUCCESS", "payment should succeed")


def at2(x: Ctx):
    """AT-2 First-time, high-value honest payment with no call or message: a Warning at most."""
    d = x.assess("kavita", "freshmart@banka", 2500, entry="qr", claimed="FreshMart Groceries")
    ok(rank(d["tier"]) <= rank("WARNING"), f"merchant payment was {d['tier']}")
    d = x.assess("rohan", "kavita@banka", 15000, entry="typed", dwell=45)
    ok(rank(d["tier"]) <= rank("WARNING"), f"first-time large payment was {d['tier']}, expected Warning at most")


def at3(x: Ctx):
    """AT-3 / AT-13 Digital arrest: Block, family named, call linked, guardian alerted within 3 s."""
    st = x.stage("digital_arrest", "meera")
    sp = st["suggested_payment"]
    d = x.assess("meera", sp["payee_vpa"], sp["amount"], entry="pasted", switches=4, dwell=140, claimed=sp["claimed_name"])
    ok(d["tier"] == "BLOCK", f"expected BLOCK, got {d['tier']} ({d['score']})")
    ok(d["scam_family"]["code"] == "DIGITAL_ARREST", f"family {d['scam_family']['code']}")
    ok(set(codes(d)) & {"CALLER_FLAGGED", "CALL_ACTIVE_UNKNOWN"}, f"no call reason in {codes(d)}")
    ok(len(d["links"]["calls"]) == 1, "the call is not linked to the decision")
    ok(d["guardian"] and d["guardian"]["kind"] == "NOTIFY", "guardian was not alerted")
    arjun = x.users["arjun"]["token"]
    t0 = time.time()
    got = x.wait(lambda: [r for r in x.call("GET", "/v1/guardian/requests", arjun)["requests"]
                          if r["request_id"] == d["guardian"]["request_id"]], 3)
    ok(bool(got), "guardian request did not arrive within 3 s")
    x.results_note = f"guardian alert in {time.time() - t0:.2f}s; action {d['action']}"
    det = x.detail(d["decision_id"])
    ok(det is not None, "decision not visible on the dashboard")
    ok(any(e["type"] == "call" and e["linked"] for e in det["timeline"]), "timeline does not show the linked call")
    x.keep["da"] = d
    x.call("POST", "/v1/demo/call", x.admin, body={"user_id": x.users["meera"]["user_id"], "action": "end"})


def at4(x: Ctx):
    """AT-4 / AT-21 Fake refund: Step-Up or Block, family named, name mismatch shown before and after."""
    st = x.stage("fake_refund", "rohan")
    sp = st["suggested_payment"]
    chip = x.call("GET", f"/v1/payee/{sp['payee_vpa']}/reputation?claimed_name=SBI%20Refund%20Desk", x.users["rohan"]["token"])
    ok(chip["registered_name"] == "Rakesh Kumar", "chip does not show the registered name")
    ok(chip["name_match"]["mismatch"] and chip["name_match"]["institutional_claim"], "chip does not flag the name mismatch")
    d = x.assess("rohan", sp["payee_vpa"], sp["amount"], entry="link", switches=2, dwell=80, claimed=sp["claimed_name"], note=sp["note"])
    ok(rank(d["tier"]) >= rank("STEP_UP"), f"expected Step-Up or Block, got {d['tier']}")
    ok(d["scam_family"]["code"] == "FAKE_REFUND", f"family {d['scam_family']['code']}")
    x.keep["refund"] = d


def at5(x: Ctx):
    """AT-5 Young UPI ID with a fan-in spike: chip shows Caution before any amount; payment is flagged."""
    st = x.stage("mule_transfer", "kavita")
    sp = st["suggested_payment"]
    chip = x.call("GET", f"/v1/payee/{sp['payee_vpa']}/reputation", x.users["kavita"]["token"])
    ok(chip["band"] == "CAUTION", f"chip band {chip['band']}, expected CAUTION")
    d = x.assess("kavita", sp["payee_vpa"], sp["amount"], entry="pasted", switches=1, dwell=45, note=sp["note"])
    ok(rank(d["tier"]) >= rank("STEP_UP"), f"expected Step-Up or Block, got {d['tier']}")
    ok(d["scam_family"]["code"] == "MULE_TRANSFER", f"family {d['scam_family']['code']}")
    ok("PAYEE_FAN_IN" in codes(d), f"no fan-in reason in {codes(d)}")
    x.keep["mule"] = d


def at6(x: Ctx):
    """AT-6 An abandoned payment is a near-miss and lowers the payee's reputation within 5 s."""
    vpa = "quick.returns@banka"
    tok = x.users["rohan"]["token"]
    before = x.call("GET", f"/v1/payee/{vpa}/reputation", tok)
    x.stage("investment_scam", "rohan")
    d = x.assess("rohan", vpa, 25000, entry="pasted", switches=2, dwell=70, note="task deposit plan A", claimed="Alpha Trading Desk")
    ok(rank(d["tier"]) >= rank("WARNING"), f"expected a flagged decision, got {d['tier']}")
    if d["action"] == "DECLINE":
        raise Fail("scenario produced DECLINE; there is nothing for the payer to walk away from")
    t0 = time.time()
    if d["action"] == "HOLD":            # the payer cancels the paused payment
        hid = d["hold"]["hold_id"]
        ok(bool(x.wait(lambda: any(h["hold_id"] == hid for h in x.call("GET", "/v1/holds", tok)["holds"]), 3)), "hold not stored")
        t0 = time.time()
        x.call("POST", f"/v1/holds/{hid}/cancel", tok)
    else:
        action = "ABORTED" if d["action"] == "WARN" else "STEPUP_ABANDONED"
        x.call("POST", "/v1/risk/outcome", tok, body={"decision_id": d["decision_id"], "user_action": action}, expect=202)
    after = x.wait(lambda: (lambda r: r if r["trust_score"] < before["trust_score"] else None)(
        x.call("GET", f"/v1/payee/{vpa}/reputation", tok)), 5)
    ok(bool(after), f"trust score did not fall within 5 s (still {before['trust_score']})")
    took = time.time() - t0
    nm = x.wait(lambda: [n for n in x.call("GET", "/v1/near-misses", x.admin)["near_misses"] if n["decision_id"] == d["decision_id"]], 5)
    ok(bool(nm), "near-miss not in the feed")
    d2 = x.assess("kavita", vpa, 25000, entry="pasted", switches=2, dwell=70, note="task deposit plan A", claimed="Alpha Trading Desk")
    ok(d2["score"] >= d["score"] - 1e-6, f"second payer scored {d2['score']} < {d['score']}")
    x.results_note = f"{d['action']} walked away from; trust {before['trust_score']} -> {after['trust_score']} in {took:.2f}s"


def at7(x: Ctx):
    """AT-7 Cache failure: decisions still returned, marked degraded, nothing blocked by the outage."""
    x.call("POST", "/v1/demo/chaos", x.admin, body={"cache_down": True})
    try:
        a = x.assess("kavita", "freshmart@banka", 800, entry="qr")
        ok(a["degraded"] and rank(a["tier"]) <= rank("WARNING"), f"degraded={a['degraded']} tier={a['tier']}")
        b = x.assess("kavita", "cyber.cell.verify@bankb", 180000, entry="pasted", switches=4)
        ok(b["degraded"] and rank(b["tier"]) <= rank("WARNING"), f"an outage escalated to {b['tier']}")
    finally:
        x.call("POST", "/v1/demo/chaos", x.admin, body={"cache_down": False})
    x.call("POST", "/v1/demo/chaos", x.admin, body={"model_down": True})
    try:
        c = x.assess("kavita", "cyber.cell.verify@bankb", 180000, entry="pasted", switches=4)
        ok(c["degraded"] and rank(c["tier"]) <= rank("WARNING"), f"model outage escalated to {c['tier']}")
    finally:
        x.call("POST", "/v1/demo/chaos", x.admin, body={"model_down": False})


def at8(x: Ctx):
    """AT-8 The same txn_id twice returns the identical decision."""
    txn = f"E2E-IDEM-{uuid.uuid4().hex[:8]}"
    a = x.assess("arjun", "freshmart@banka", 450, entry="qr", txn=txn)
    b = x.assess("arjun", "freshmart@banka", 450, entry="qr", txn=txn)
    ok(a == b, "responses differ for the same txn_id")
    bad = x.c.post("/v1/risk/assess", headers={"Authorization": f"Bearer {x.users['arjun']['token']}"}, json={"txn_id": "x"})
    ok(bad.status_code == 400 and bad.json()["error"]["code"] == "VALIDATION_ERROR", "a malformed request must get a 400, not a default")


def at11(x: Ctx):
    """AT-11 / AT-12 Message checker: scam flagged and its UPI ID then declined; a genuine message is fine."""
    tok = x.users["sunita"]["token"]
    fine = x.call("POST", "/v1/signals/message", tok, body={
        "text": "Dear Customer, Rs.1,250.00 has been debited from account 4821 to VPA freshmart@banka on 05-10-26. If not you, call 18001234."})
    ok(fine["verdict"] == "FINE" and not fine["flag_set"], f"genuine bank message judged {fine['verdict']}")
    scam = x.call("POST", "/v1/signals/message", tok, body={
        "text": "Dear customer your SBI account will be blocked today due to pending KYC. Pay Rs 2,499 KYC update charge to "
                "kyc.update.sbi@bankb immediately or call 9111111102."})
    ok(scam["verdict"] == "SCAM" and scam["flag_set"], f"scam message judged {scam['verdict']}")
    ok("kyc.update.sbi@bankb" in scam["entities"]["vpas"], "UPI ID not extracted")
    ok(len(scam["red_flags"]) >= 1 and scam["family"]["code"] == "KYC_BILL_THREAT", "no red flags or wrong family")
    d = x.assess("sunita", "kyc.update.sbi@bankb", 2499, entry="link", claimed="SBI KYC Update", note="kyc update charge")
    ok(d["action"] == "DECLINE", f"expected DECLINE, got {d['action']}")
    ok("PAYEE_IN_SCAM_MESSAGE" in codes(d), f"reasons {codes(d)}")
    ok(len(d["links"]["messages"]) == 1, "message not linked")
    again = x.assess("sunita", "kyc.update.sbi@bankb", 100, entry="typed")
    ok(again["action"] == "DECLINE", "the payer-to-payee stop did not hold on a second attempt")


def at14(x: Ctx):
    """AT-14 Guardian co-approval: deny cancels the payment; approve lets it through."""
    meera, arjun = x.users["meera"]["token"], x.users["arjun"]["token"]
    d = x.assess("meera", "rohan@banka", 25000, entry="typed", dwell=40)
    ok(d["action"] in ("ALLOW", "WARN"), f"expected an allowed or warned payment, got {d['action']}")
    ok(d["guardian"] and d["guardian"]["required"] and d["guardian"]["kind"] == "APPROVAL", "guardian approval not required")
    ok(x.pay("meera", d)["status"] == "REFUSED", "the PSP paid without guardian approval")
    rid = d["guardian"]["request_id"]
    x.wait(lambda: any(r["request_id"] == rid for r in x.call("GET", "/v1/guardian/requests", arjun)["requests"]), 3)
    full = x.call("GET", f"/v1/guardian/requests/{rid}", arjun)
    ok("balance" not in str(full) and full["summary"]["amount"] == 25000, "guardian sees the wrong things")
    mine = x.call("GET", f"/v1/guardian/requests/{rid}", meera)
    ok(set(mine) == {"request_id", "status", "expires_at"}, "the payer sees more than the status of their own request")
    x.call("POST", f"/v1/guardian/requests/{rid}/respond", arjun, body={"decision": "deny"})
    ok(x.pay("meera", d)["status"] == "REFUSED", "the PSP paid after the guardian refused")
    det = x.wait(lambda: (lambda r: r if r and r["outcome"] else None)(x._detail(d["decision_id"], "bank_a")), 5)
    ok(det and det["outcome"]["user_action"] == "GUARDIAN_DENIED", "GUARDIAN_DENIED not recorded")
    d2 = x.assess("meera", "rohan@banka", 25000, entry="typed", dwell=40)
    ok(d2["guardian"] and d2["guardian"]["required"], "second attempt did not need approval")
    rid2 = d2["guardian"]["request_id"]
    x.wait(lambda: any(r["request_id"] == rid2 for r in x.call("GET", "/v1/guardian/requests", arjun)["requests"]), 3)
    x.call("POST", f"/v1/guardian/requests/{rid2}/respond", arjun, body={"decision": "approve"})
    ok(x.pay("meera", d2)["status"] == "SUCCESS", "payment refused after guardian approval")
    ok(x.pay("meera", d2)["status"] == "REFUSED", "the same payment was paid twice")


def at15(x: Ctx):
    """AT-15 Removing a guardian takes 24 hours, the guardian is told, and Undo restores it."""
    meera = x.users["meera"]["token"]
    g = x.call("GET", "/v1/guardians", meera)["as_protected"]
    ok(g and g["status"] == "ACTIVE", "no active guardian to start with")
    after = x.call("DELETE", f"/v1/guardians/{g['guardian_link_id']}", meera)["as_protected"]
    ok(after["status"] == "REMOVAL_PENDING" and after["change_effective_at"], "removal was immediate")
    d = x.assess("meera", "kavita@banka", 30000, entry="typed")
    ok(d["guardian"] and d["guardian"]["required"], "protection lapsed during the 24-hour delay")
    back = x.call("POST", f"/v1/guardians/{g['guardian_link_id']}/undo", meera)["as_protected"]
    ok(back["status"] == "ACTIVE", "undo did not restore the guardian")
    loosen = x.call("PATCH", f"/v1/guardians/{g['guardian_link_id']}", meera, body={"mode": "notify"})["as_protected"]
    ok(loosen["mode"] == "co_approve" and loosen["pending_change"], "loosening applied at once")
    x.call("POST", f"/v1/guardians/{g['guardian_link_id']}/undo", meera)


def at16(x: Ctx):
    """AT-16 Coaching signals: pasting the UPI ID and switching apps raises the model's score and fires R-11."""
    a = x.assess("arjun", "vendor.payments@banka", 20000, entry="typed", switches=0, dwell=15)
    b = x.assess("arjun", "vendor.payments@banka", 20000, entry="pasted", switches=4, dwell=120)
    ok(b["score"] >= a["score"], f"coached payment scored {b['score']} < {a['score']}")
    da, db = x.detail(a["decision_id"]), x.detail(b["decision_id"])
    ok(da and db, "decisions not visible on the dashboard")
    ok("R-11" in db["decision"]["rules_fired"], f"R-11 did not fire: {db['decision']['rules_fired']}")
    ok("R-11" not in da["decision"]["rules_fired"], "R-11 fired on the uncoached payment")
    # The final score can sit on the same rule floor for both; what must move is the model's own output
    # and the share of it that comes from the session signals.
    ra, rb = da["score_parts"]["raw"], db["score_parts"]["raw"]
    sa, sb = da["decision"]["contributions"]["session"], db["decision"]["contributions"]["session"]
    ok(rb > ra, f"model output did not rise: {ra} -> {rb}")
    ok(sb > sa, f"session contribution did not rise: {sa} -> {sb}")
    x.results_note = (f"model output {ra:.4f} -> {rb:.4f}; session contribution {sa:+.2f} -> {sb:+.2f}; "
                      f"final score {a['score']:.2f} ({a['tier']}) -> {b['score']:.2f} ({b['tier']})")


def at17(x: Ctx):
    """AT-17 Shadow mode: payer sees nothing; the real decision is stored unenforced; no guardian, no hold."""
    x.call("PUT", "/v1/policy/mode", x.admin, body={"mode": "shadow"})
    try:
        st = x.stage("digital_arrest", "meera")
        sp = st["suggested_payment"]
        before = len(x.call("GET", "/v1/guardian/requests", x.users["arjun"]["token"])["requests"])
        d = x.assess("meera", sp["payee_vpa"], sp["amount"], entry="pasted", switches=4, dwell=140)
        ok(d["tier"] == "SAFE" and d["action"] == "ALLOW" and d["enforced"] is False, f"shadow response was {d['tier']}/{d['action']}")
        ok(d["reasons"] == [] and d["hold"] is None and d["guardian"] is None and d["step_up"] is None, "shadow response leaks details")
        det = x.detail(d["decision_id"])
        ok(det and det["decision"]["tier"] == "BLOCK" and det["decision"]["enforced"] is False, "real decision not stored as unenforced Block")
        time.sleep(0.6)
        ok(len(x.call("GET", "/v1/guardian/requests", x.users["arjun"]["token"])["requests"]) == before, "a guardian request was created in shadow mode")
        ok(not any(h["decision_id"] == d["decision_id"] for h in x.call("GET", "/v1/holds", x.users["meera"]["token"])["holds"]), "a hold was created in shadow mode")
    finally:
        x.call("PUT", "/v1/policy/mode", x.admin, body={"mode": "enforce"})
        x.call("POST", "/v1/demo/call", x.admin, body={"user_id": x.users["meera"]["user_id"], "action": "end"})


def at18(x: Ctx):
    """AT-18 Policy lab: simulation is consistent; applying thresholds is admin-only, immediate and audited."""
    base = {"t_warning": 0.30, "t_stepup": 0.60, "t_block": 0.85}
    t0 = time.time()
    sim = x.call("POST", "/v1/policy/simulate", x.admin, body={**base, "hours": 360, "fraud_rate": 0.0005})
    took = time.time() - t0
    ok(sim["n_decisions"] > 1000 and sim["n_fraud"] > 10, f"too little labelled data: {sim['n_decisions']}/{sim['n_fraud']}")
    for k in ("scams_caught", "legit_stepup", "interruptions_per_1000"):
        ok(abs(sim["proposed"][k] - sim["current"][k]) < 1e-9, f"same thresholds, different {k}")
    lower = x.call("POST", "/v1/policy/simulate", x.admin, body={**base, "t_stepup": 0.35, "hours": 360})
    ok(lower["proposed"]["scams_caught"] >= sim["proposed"]["scams_caught"], "lowering Step-Up caught fewer scams")
    ok(lower["proposed"]["legit_stepup"] + lower["proposed"]["legit_block"] >= sim["proposed"]["legit_stepup"] + sim["proposed"]["legit_block"],
       "lowering Step-Up interrupted fewer payers")
    ok(took < 1.0, f"simulation took {took:.2f}s")
    analyst = x.staff["analyst@banka.demo"]
    x.call("PUT", "/v1/policy/thresholds", analyst, body={**base, "t_stepup": 0.5}, expect=403)
    x.call("PUT", "/v1/policy/thresholds", x.admin, body={**base, "t_stepup": 0.9}, expect=400)
    x.call("PUT", "/v1/policy/thresholds", x.admin, body={**base, "t_warning": 0.25})
    try:
        d = x.assess("arjun", "freshmart@banka", 30, entry="qr")
        ok(d["tier"] == "SAFE", f"ordinary payment not Safe: {d['tier']} {codes(d)}")
        ok(abs(x.call("GET", "/v1/policy", x.admin)["t_warning"] - 0.25) < 1e-9, "threshold not applied")
        audit = x.call("GET", "/v1/audit?action=policy.thresholds", x.admin)["entries"]
        ok(audit and audit[0]["detail"]["to"]["t_warning"] == 0.25, "no audit entry for the threshold change")
    finally:
        x.call("PUT", "/v1/policy/thresholds", x.admin, body=base)
    x.results_note = f"simulate over {sim['n_decisions']:,} decisions in {took * 1000:.0f} ms"


def at19(x: Ctx):
    """AT-19 Network: the mule chain is visible; the first-layer mule is one step from a reported account."""
    x.call("POST", "/v1/model-health/reload", x.admin)
    g = x.call("GET", "/v1/graph/neighbourhood?vpa=cyber.cell.verify%40bankb&hops=2", x.admin)
    nodes = {n["id"]: n for n in g["nodes"]}
    ok("layer2.acct@bankb" in nodes and nodes["layer2.acct@bankb"]["band"] == "REPORTED", "reported next-layer account not in the graph")
    ok(any(e["source"] == "cyber.cell.verify@bankb" and e["target"] == "layer2.acct@bankb" and e["type"] == "payment" for e in g["edges"]),
       "forwarding payment edge missing")
    ok(any(e["type"] == "device" for e in g["edges"]), "shared-device edge missing")
    p = x.call("GET", "/v1/payees/cyber.cell.verify%40bankb", x.admin)
    ok(p["graph"] and p["graph"]["downstream_hop"] == 1, f"downstream_hop {p['graph'] and p['graph']['downstream_hop']}")
    victims = [e["source"] for e in g["edges"] if e["target"] == "cyber.cell.verify@bankb" and e["type"] == "payment"]
    ok(victims, "no payers of the mule in the graph")
    v = x.call("GET", f"/v1/payees/{victims[0].replace('@', '%40')}", x.admin)
    ok(not v["graph"] or v["graph"]["downstream_hop"] >= 9, "a victim who paid the mule carries a graph flag")


def at22(x: Ctx):
    """AT-22 Timed hold: nothing is sent on the timer; confirming re-assesses; a second Block is a Decline."""
    d = x.keep.get("da")
    ok(d is not None and d["action"] == "HOLD", f"the digital-arrest decision was {d and d['action']}, expected HOLD")
    meera = x.users["meera"]["token"]
    hold_id = d["hold"]["hold_id"]
    h = x.wait(lambda: [y for y in x.call("GET", "/v1/holds", meera)["holds"] if y["hold_id"] == hold_id], 5)
    ok(bool(h) and h[0]["status"] == "HELD", "hold not stored as HELD")
    x.call("POST", f"/v1/holds/{hold_id}/confirm", meera, expect=409)
    ok(x.pay("meera", d)["status"] == "REFUSED", "a held payment was paid")
    if not x.slow:
        x.results_note = "skipped the wait for the hold timer (--fast)"
        return
    ready = x.wait(lambda: [y for y in x.call("GET", "/v1/holds", meera)["holds"] if y["hold_id"] == hold_id and y["status"] == "READY"], 90, 2)
    ok(bool(ready), "hold never became READY")
    paid = [p for p in x.call("GET", "/v1/users/me/payments", meera)["payments"] if p["txn_id"] == d["txn_id"]]
    ok(paid and paid[0]["status"] != "SUCCESS", "money was sent when the timer ended")
    c = x.call("POST", f"/v1/holds/{hold_id}/confirm", meera)
    x.stage("digital_arrest", "meera")                # the scammer is still on the line
    d2 = x.assess("meera", c["payee"]["vpa"], c["amount"], entry="pasted", switches=4, dwell=140, parent=c["parent_decision_id"])
    ok(d2["tier"] == "BLOCK" and d2["action"] == "DECLINE" and d2["hold"] is None, f"re-assessment gave {d2['tier']}/{d2['action']}")
    x.call("POST", "/v1/demo/call", x.admin, body={"user_id": x.users["meera"]["user_id"], "action": "end"})
    st = x.stage("mule_transfer", "rohan")
    sp = st["suggested_payment"]
    d3 = x.assess("rohan", sp["payee_vpa"], sp["amount"], entry="pasted", switches=1, note=sp["note"])
    if d3["action"] == "HOLD":
        x.call("POST", f"/v1/holds/{d3['hold']['hold_id']}/cancel", x.users["rohan"]["token"], expect=(200, 404))


def at23(x: Ctx):
    """AT-23 Consortium: confirmed fraud at Bank A protects a Bank B payer within 10 s; no UPI ID crosses."""
    mule = x.keep.get("mule")
    ok(mule is not None, "needs the mule decision from AT-5")
    vpa = mule["payee"]["vpa"]
    before = x.assess("sunita", vpa, 5000, entry="typed")
    ok("REPORTED_AT_OTHER_BANK" not in codes(before), "Bank B already had the signal")
    x.call("POST", f"/v1/decisions/{mule['decision_id']}/label", x.admin, body={"label": "FRAUD", "family": "MULE_TRANSFER"})
    t0 = time.time()
    got = x.wait(lambda: (lambda r: r if "REPORTED_AT_OTHER_BANK" in codes(r) else None)(x.assess("sunita", vpa, 5000, entry="typed")), 10, 0.5)
    ok(bool(got), "Bank B payer was not warned within 10 s")
    sig = x.call("GET", f"/v1/consortium/signals?vpa={vpa.replace('@', '%40')}", x.staff["admin@bankb.demo"])["received"]
    ok(sig and sig[0]["source_tenant"] == "bank_a" and sig[0]["confirmed"], "signal not recorded at Bank B")
    ok(vpa not in str(sig) and sig[0]["vpa_token"].startswith("t_"), "the shared row carries the UPI ID in clear")
    ok(not ({"user_id", "payer_vpa", "txn_id", "amount"} & set(sig[0])), "payer or transaction data was shared")
    x.results_note = f"Bank B warned {time.time() - t0:.1f}s after Bank A confirmed"


def at24(x: Ctx):
    """AT-24 Complaint pack: PDF and JSON with the transaction, payee and timeline, in under 5 s."""
    d = x.keep.get("da")
    ok(d is not None, "needs the digital-arrest decision from AT-3")
    meera = x.users["meera"]["token"]
    t0 = time.time()
    pack = x.call("POST", "/v1/complaint-packs", meera, body={"decision_id": d["decision_id"], "description": "A caller said he was from the cyber cell."})
    pdf = x.c.get(pack["pdf_url"], headers={"Authorization": f"Bearer {meera}"})
    took = time.time() - t0
    ok(pdf.status_code == 200 and pdf.content[:4] == b"%PDF" and len(pdf.content) > 1500, "PDF missing or empty")
    j = x.call("GET", pack["json_url"], meera)
    ok(j["transaction"]["payee_upi_id"] == d["payee"]["vpa"] and j["transaction"]["amount"] == 180000, "pack has the wrong transaction")
    ok(any(e["type"] == "call" for e in j["timeline"]) and any(e["type"] == "payment" for e in j["timeline"]), "timeline lacks the call or the payment")
    ok(j["assessment"]["reasons"], "pack has no reasons")
    ok(took < 5, f"pack took {took:.1f}s")
    other = x.c.get(pack["pdf_url"], headers={"Authorization": f"Bearer {x.users['rohan']['token']}"})
    ok(other.status_code == 403, "another user could open the pack")
    rv = x.wait(lambda: [r for r in x.call("GET", "/v1/reviews", x.admin)["reviews"] if r["decision_id"] == d["decision_id"]], 3)
    ok(bool(rv), "creating a pack did not file a fraud report")
    x.results_note = f"{len(pdf.content) / 1024:.0f} KB PDF in {took:.2f}s"


def at25(x: Ctx):
    """AT-25 / AT-26 Drift alerts on shifted traffic; fairness report by group."""
    r = x.call("POST", "/v1/demo/drift", x.admin, body={"n": 300})
    feats = r["drift"].get("features", [])
    alerts = [f["feature"] for f in feats if f["status"] == "alert"]
    ok(bool(alerts), f"no drift alert after shifted traffic: {[(f['feature'], f['psi']) for f in feats[:5]]}")
    fair = x.call("GET", "/v1/model-health/fairness?refresh=true", x.admin)
    dims = {d["dimension"] for d in fair.get("dimensions", [])}
    ok(dims == {"age_band", "segment", "locale"}, f"fairness dimensions {dims}")
    ok(all("disparity_ratio" in d and d["segments"] for d in fair["dimensions"]), "fairness report incomplete")
    x.results_note = f"alerts on {', '.join(alerts[:4])}"


def at27(x: Ctx):
    """AT-27 Case summary appears within 30 s, cites only facts from the decision, and never changes it."""
    d = x.keep.get("refund") or x.keep.get("da")
    ok(d is not None, "needs a Step-Up or Block decision")
    s = x.wait(lambda: (lambda r: r.json() if r.status_code == 200 else None)(
        x.c.get(f"/v1/decisions/{d['decision_id']}/summary", headers={"Authorization": f"Bearer {x.admin}"})), 30, 1)
    ok(bool(s) and len(s["summary"]) > 40, "no summary within 30 s")
    det = x.detail(d["decision_id"])
    ok(det["decision"]["tier"] == d["tier"] and abs(det["decision"]["score"] - d["score"]) < 1e-6, "the summary changed the decision")
    text = s["summary"] + " ".join(s.get("key_evidence") or [])
    ok(not any(u["name"] in text or u["name"].split()[0] in text for u in x.users.values()), "the summary names a payer")
    ok("PAYER_1" not in text and "PAYEE_1" not in text, "the summary shows placeholder tokens to the analyst")
    x.call("POST", f"/v1/decisions/{d['decision_id']}/summary/regenerate", x.staff["auditor@banka.demo"], expect=403)
    x.results_note = f"provider: {s['provider']}"


def at28(x: Ctx):
    """AT-28 Rehearsal: scripts listed, played and recorded; nothing to do with payments."""
    tok = x.users["rohan"]["token"]
    scripts = x.call("GET", "/v1/rehearsals", tok)["scripts"]
    ok(len(scripts) >= 3, f"only {len(scripts)} scripts")
    s = x.call("GET", f"/v1/rehearsals/{scripts[0]['code']}", tok)
    node = s["script"]["nodes"][s["script"]["start"]]
    safe = next(i for i, o in enumerate(node["options"]) if o["verdict"] == "safe")
    r = x.call("POST", f"/v1/rehearsals/{scripts[0]['code']}/runs", tok, body={"choices": [{"node": s["script"]["start"], "option": safe}]})
    ok(r == {"safe_choices": 1, "total_choices": 1}, f"run result {r}")
    hi = x.call("GET", "/v1/rehearsals", x.users["meera"]["token"])["scripts"]
    ok(any(sc["locale"] == "hi" for sc in hi), "Hindi user got no Hindi script")


def at30(x: Ctx):
    """AT-30 Step-Up: the server enforces the pause; the risky answer stops the payment; the safe path pays."""
    x.call("POST", "/v1/demo/call", x.admin, body={"user_id": x.users["kavita"]["user_id"], "action": "start", "caller_risk": 0})
    try:
        d = x.assess("kavita", "newcafe@banka", 700000 * 0 + 150000, entry="typed", dwell=30)
    finally:
        pass
    ok(d["action"] == "STEP_UP", f"expected STEP_UP, got {d['tier']}/{d['action']} ({d['score']})")
    su = d["step_up"]
    ok(su and su["question"]["text"] and "risky_answer" not in str(su), "step-up payload wrong or leaks the risky answer")
    tok = x.users["kavita"]["token"]
    early = x.c.post("/v1/stepup/verify", headers={"Authorization": f"Bearer {tok}"},
                     json={"challenge_id": su["challenge_id"], "answer": "no", "auth": {"method": "biometric", "biometric_ok": True}})
    ok(early.status_code == 425, f"verify before the pause ended gave {early.status_code}")
    ok(x.pay("kavita", d)["status"] == "REFUSED", "the PSP paid before step-up was passed")
    time.sleep(su["cooloff_s"] + 0.3)
    first = x.call("POST", "/v1/stepup/verify", tok, body={"challenge_id": su["challenge_id"], "answer": "no"})
    ok(first["result"] == "AUTH_REQUIRED", f"the safe answer alone gave {first['result']}, expected AUTH_REQUIRED")
    ok(x.pay("kavita", d)["status"] == "REFUSED", "the PSP paid on an answer without authentication")
    otp = x.call("POST", "/v1/stepup/challenge", tok, body={"challenge_id": su["challenge_id"], "method": "otp"})
    bad = x.call("POST", "/v1/stepup/verify", tok, body={"challenge_id": su["challenge_id"], "answer": "no", "auth": {"method": "otp", "otp": "000000"}})
    ok(bad["result"] == "BAD_CODE", f"wrong code gave {bad['result']}")
    good = x.call("POST", "/v1/stepup/verify", tok, body={"challenge_id": su["challenge_id"], "answer": "no", "auth": {"method": "otp", "otp": otp["demo_code"]}})
    ok(good["result"] == "PASSED", f"correct code gave {good['result']}")
    ok(x.pay("kavita", d)["status"] == "SUCCESS", "payment refused after step-up passed")
    # the risky answer, on a second payer
    x.call("POST", "/v1/demo/call", x.admin, body={"user_id": x.users["rohan"]["user_id"], "action": "start", "caller_risk": 0})
    d2 = x.assess("rohan", "newcafe@banka", 30000, entry="typed", dwell=30)
    ok(d2["action"] == "STEP_UP", f"expected STEP_UP for the second payer, got {d2['action']}")
    time.sleep(d2["step_up"]["cooloff_s"] + 0.3)
    rtok = x.users["rohan"]["token"]
    # the app sends the answer on its own first, so a risky answer stops the payment before any authentication
    fail = x.call("POST", "/v1/stepup/verify", rtok, body={"challenge_id": d2["step_up"]["challenge_id"], "answer": "yes"})
    ok(fail["result"] == "FAILED" and fail["action"] == "DECLINE", f"risky answer gave {fail}")
    ok(x.pay("rohan", d2)["status"] == "REFUSED", "the PSP paid after the payer admitted being coached")
    d3 = x.assess("rohan", "newcafe@banka", 500, entry="typed")
    ok(d3["action"] == "DECLINE" and "PAIR_BLOCKED" in codes(d3), "the 24-hour stop for this payer and payee did not apply")
    for who in ("kavita", "rohan"):
        x.call("POST", "/v1/demo/call", x.admin, body={"user_id": x.users[who]["user_id"], "action": "end"})


def at31(x: Ctx):
    """AT-31 Roles: each position can do only what it should; lockout after five wrong passwords."""
    analyst, auditor = x.staff["analyst@banka.demo"], x.staff["auditor@banka.demo"]
    user = x.users["rohan"]["token"]
    x.call("GET", "/v1/decisions?limit=1", user, expect=403)
    x.call("GET", "/v1/decisions?limit=1", None, expect=401)
    x.call("GET", "/v1/admin/staff", analyst, expect=403)
    x.call("GET", "/v1/audit", analyst, expect=403)
    x.call("GET", "/v1/audit", auditor)
    x.call("PUT", "/v1/policy/mode", auditor, body={"mode": "shadow"}, expect=403)
    x.call("GET", "/v1/demo/scenarios", analyst, expect=403)
    x.call("GET", "/v1/users/me", analyst, expect=403)
    x.call("GET", "/v1/decisions?limit=1", analyst, tenant="bank_b", expect=403)
    x.call("GET", "/v1/decisions?limit=1", x.admin, tenant="bank_b")
    dec = x.call("GET", "/v1/decisions?limit=1", analyst)["decisions"][0]
    x.call("POST", f"/v1/decisions/{dec['decision_id']}/label", auditor, body={"label": "LEGITIMATE"}, expect=403)
    email = f"temp.{uuid.uuid4().hex[:6]}@banka.example"
    pw = "Tmp-" + uuid.uuid4().hex[:10]
    new = x.call("POST", "/v1/admin/staff", x.admin, body={"name": "Temp Analyst", "email": email, "role": "analyst", "password": pw})
    tok = x.call("POST", "/v1/auth/staff-login", body={"email": email, "password": pw})["token"]
    x.call("GET", "/v1/decisions?limit=1", tok)
    me = x.call("GET", "/v1/auth/me", x.admin)["staff"]["staff_id"]
    x.call("PATCH", f"/v1/admin/staff/{me}", x.admin, body={"active": False}, expect=409)
    x.call("PATCH", f"/v1/admin/staff/{me}", x.admin, body={"role": "analyst"}, expect=409)
    x.call("PATCH", f"/v1/admin/staff/{new['staff_id']}", x.admin, body={"active": False})
    x.call("GET", "/v1/decisions?limit=1", tok, expect=401)
    x.call("POST", "/v1/auth/staff-login", body={"email": email, "password": pw}, expect=403)
    x.call("PATCH", f"/v1/admin/staff/{new['staff_id']}", x.admin, body={"active": True})
    for _ in range(5):
        x.call("POST", "/v1/auth/staff-login", body={"email": email, "password": "wrong-password"}, expect=(401, 429))
    x.call("POST", "/v1/auth/staff-login", body={"email": email, "password": pw}, expect=429)
    x.call("POST", f"/v1/admin/staff/{new['staff_id']}/reset-password", x.admin, body={"password": pw + "2"})
    x.call("POST", "/v1/auth/staff-login", body={"email": email, "password": pw + "2"})
    x.call("PATCH", f"/v1/admin/staff/{new['staff_id']}", x.admin, body={"active": False})
    audit = x.call("GET", "/v1/audit?action=staff", x.admin)["entries"]
    ok(any(e["action"] == "staff.create" for e in audit) and any(e["action"] == "staff.update" for e in audit), "account changes not audited")


def at32(x: Ctx):
    """AT-32 App users: register, sign in with phone and PIN, and lose access when deactivated."""
    phone = f"98{int(time.time()) % 10 ** 8:08d}"
    reg = x.call("POST", "/v1/auth/register", body={"name": "Test Payer", "phone": phone, "pin": "482913", "locale": "hi", "tenant_id": "bank_a"})
    ok(reg["user"]["vpa"].endswith("@banka") and reg["user"]["locale"] == "hi", "registration returned the wrong profile")
    x.call("POST", "/v1/auth/register", body={"name": "Test Payer", "phone": phone, "pin": "482913"}, expect=409)
    x.call("POST", "/v1/auth/login", body={"phone": phone, "pin": "000000"}, expect=401)
    tok = x.call("POST", "/v1/auth/login", body={"phone": phone, "pin": "482913"})["token"]
    me = x.call("GET", "/v1/users/me", tok)
    ok(me["balance"] == 50000 and me["vpa"] == reg["user"]["vpa"], "new account profile wrong")
    r = x.call("GET", f"/mock-psp/v1/resolve/{reg['user']['vpa'].replace('@', '%40')}", tok)
    ok(r["exists"] and r["registered_name"] == "Test Payer", "new UPI ID not in the directory")
    d = x.call("POST", "/v1/risk/assess", tok, body={"txn_id": f"E2E-{uuid.uuid4().hex[:12]}",
               "payer": {"user_id": reg["user"]["user_id"], "vpa": reg["user"]["vpa"], "device_id": str(uuid.uuid4())},
               "payee": {"vpa": "freshmart@banka"}, "amount": 120, "session": {"vpa_entry_method": "qr"}})
    ok(rank(d["tier"]) <= rank("WARNING"), f"a new user's small shop payment was {d['tier']}")
    x.call("POST", "/v1/risk/assess", tok, body={"txn_id": f"E2E-{uuid.uuid4().hex[:12]}",
           "payer": {"user_id": x.users["rohan"]["user_id"], "vpa": "rohan@banka"}, "payee": {"vpa": "freshmart@banka"}, "amount": 10}, expect=403)
    x.call("PATCH", f"/v1/admin/users/{reg['user']['user_id']}", x.admin, body={"active": False})
    x.call("GET", "/v1/users/me", tok, expect=401)
    x.call("POST", "/v1/auth/login", body={"phone": phone, "pin": "482913"}, expect=403)


def at10(x: Ctx):
    """AT-10 Every decision at Warning or above carries at least one reason."""
    rows = x.call("GET", "/v1/decisions?tier=WARNING,STEP_UP,BLOCK&limit=200", x.admin)["decisions"]
    ok(len(rows) >= 10, f"only {len(rows)} flagged decisions to check")
    missing = [r["decision_id"] for r in rows if not r["top_reason"]]
    ok(not missing, f"{len(missing)} flagged decisions have no reason")
    x.results_note = f"{len(rows)} flagged decisions checked"


# ------------------------------------------------------------- community features (F-31 to F-34)
def _call(x: Ctx, who: str, action: str, risk: int = 0) -> dict:
    return x.call("POST", "/v1/demo/call", x.admin, body={"user_id": x.users[who]["user_id"], "action": action, "caller_risk": risk})


def at33(x: Ctx):
    """AT-33 Caller warning: a reported number is flagged in the app; two people's reports flag a new one."""
    kav, roh, arj = (x.users[k]["token"] for k in ("kavita", "rohan", "arjun"))
    ok(x.call("GET", "/v1/users/me/call-status", kav)["active"] is False, "a call was reported with none in progress")
    _call(x, "kavita", "start", 3)
    st = x.call("GET", "/v1/users/me/call-status", kav)
    ok(st["active"] and st["caller"]["risk"] == 3 and st["caller"]["label"] == "KNOWN_SCAM", f"flagged caller not shown: {st}")
    ok(st["can_report"] and not st["reported_by_you"], "the call cannot be reported")
    ok("caller_hash" not in str(st) and "h_" not in str(st["caller"]), "the caller's identifier leaked to the app")
    _call(x, "kavita", "end")
    # a number nobody has listed: one report is not enough, two different people's reports are
    look = lambda tok: x.call("GET", "/v1/lookup?q=9111111100", tok)
    ok(look(arj)["risk"] == 0, "the unlisted number already had a risk level")
    _call(x, "rohan", "start", 0)
    r1 = x.call("POST", "/v1/callers/report", roh, body={"category": "scam"})
    ok(r1["reported"] and r1["verified"] and r1["caller"]["risk"] == 0, f"one report should not flag a number: {r1}")
    again = x.call("POST", "/v1/callers/report", roh, body={"category": "scam"})
    ok(again["caller"]["risk"] == 0, "the same person reporting twice was counted twice")
    _call(x, "rohan", "end")
    typed = x.call("POST", "/v1/callers/report", arj, body={"phone": "9111111100"})
    ok(typed["verified"] is False and typed["caller"]["risk"] == 0, f"a typed-in report from someone never called counted: {typed}")
    _call(x, "kavita", "start", 0)
    r2 = x.call("POST", "/v1/callers/report", kav, body={"category": "scam"})
    ok(r2["caller"]["risk"] == 2 and r2["caller"]["label"] == "LIKELY_SCAM", f"two verified reports gave {r2['caller']}")
    _call(x, "kavita", "end")
    seen = look(arj)
    ok(seen["risk"] == 2 and "caller_reports" in seen["sources"] and seen["reports"] in ("1-2", "3-9"), f"lookup shows {seen}")
    _call(x, "arjun", "start", 0)                      # the number now calls a third person
    st = x.call("GET", "/v1/users/me/call-status", arj)
    ok(st["caller"]["risk"] == 2, f"the newly reported number was not flagged on its next call: {st['caller']}")
    d = x.assess("arjun", "newcafe@banka", 30000, entry="typed", dwell=30)
    ok("CALLER_FLAGGED" in codes(d) or rank(d["tier"]) >= rank("STEP_UP"), f"the reported caller did not count: {d['tier']} {codes(d)}")
    _call(x, "arjun", "end")
    x.call("POST", "/v1/callers/report", arj, body={"phone": "12345"}, expect=400)
    x.call("POST", "/v1/callers/report", arj, body={"category": "nonsense"}, expect=400)


def at34(x: Ctx):
    """AT-34 Look-up: a UPI ID or number is described in ranges, never with who reported it; rate-limited."""
    tok = x.users["arjun"]["token"]
    listed = x.call("GET", "/v1/payees?band=REPORTED&limit=1", x.admin)["payees"]
    ok(len(listed) == 1, "no reported UPI ID in the seeded data to look up")
    bad = x.call("GET", f"/v1/lookup?q={listed[0]['vpa'].replace('@', '%40')}", tok)
    ok(bad["exists"] and bad["band"] == "REPORTED" and bad["community"]["confirmed_fraud"], f"a reported UPI ID looked up as {bad.get('band')}")
    com = bad["community"]
    young = x.call("GET", "/v1/lookup?q=cyber.cell.verify%40bankb", tok)
    # earlier tests may already have reported it, so its band is No history or Caution; its age does not change
    ok(young["exists"] and young["band"] in ("NO_HISTORY", "CAUTION") and young["age_days"] < 30, f"a days-old UPI ID looked up as {young}")
    ok(all(isinstance(com[k], str) for k in ("reports_30d", "stopped_14d")), f"counts are not ranges: {com}")
    ok(isinstance(bad["age_days"], int) and bad["registered_name"], "missing basic facts")
    ok("user_id" not in str(bad) and "reporter" not in str(bad), "the look-up names people")
    good = x.call("GET", "/v1/lookup?q=freshmart%40banka", tok)
    ok(good["exists"] and good["verified"] and good["band"] in ("TRUSTED", "NEUTRAL"), f"a verified shop looked up as {good}")
    ok(good["you"]["paid_before"] >= 0 and good["you"]["blocked"] is False, "the personal part is wrong")
    none = x.call("GET", "/v1/lookup?q=nobody.here%40banka", tok)
    ok(none["exists"] is False, "an unknown UPI ID was said to exist")
    num = x.call("GET", "/v1/lookup?q=%2B91%2091111%2011103", tok)
    ok(num["kind"] == "phone" and num["risk"] == 3 and num["phone_last4"] == "1103", f"listed number: {num}")
    clean_num = x.call("GET", "/v1/lookup?q=9000012345", tok)
    ok(clean_num["risk"] == 0 and clean_num["reports"] == "0", f"an unknown number: {clean_num}")
    x.call("GET", "/v1/lookup?q=hello", tok, expect=400)
    x.call("GET", "/v1/lookup?q=bad%40", tok, expect=400)
    x.call("GET", "/v1/lookup?q=freshmart%40banka", None, expect=401)
    x.call("GET", "/v1/lookup?q=freshmart%40banka", x.admin, expect=403)
    stok = x.users["sunita"]["token"]                  # someone else, so the other tests keep their allowance
    last = 200
    for _ in range(40):
        last = x.c.get("/v1/lookup?q=freshmart%40banka", headers={"Authorization": f"Bearer {stok}"}).status_code
        if last == 429:
            break
    ok(last == 429, "40 look-ups in a row were all answered")


def at35(x: Ctx):
    """AT-35 Never-pay list: a blocked UPI ID is declined before the PIN, even in shadow mode; unblocking waits."""
    tok = x.users["arjun"]["token"]
    vpa = "freshmart@banka"
    before = x.assess("arjun", vpa, 300, entry="qr")
    ok(before["action"] == "ALLOW", f"the shop was {before['action']} before being blocked")
    trust = x.call("GET", f"/v1/payee/{vpa.replace('@', '%40')}/reputation", tok)["trust_score"]
    x.call("POST", "/v1/users/me/blocks", tok, body={"vpa": "arjun@banka"}, expect=400)
    x.call("POST", "/v1/users/me/blocks", tok, body={"vpa": "not a upi id"}, expect=400)
    res = x.call("POST", "/v1/users/me/blocks", tok, body={"vpa": vpa, "reason": "wrong shop"})
    ok(any(b["vpa"] == vpa and b["status"] == "ACTIVE" for b in res["blocks"]), "the block was not listed")
    d = x.assess("arjun", vpa, 300, entry="qr")
    ok(d["action"] == "DECLINE" and codes(d)[0] == "USER_BLOCKED" and d["user_block"]["blocked"], f"blocked payee got {d['action']} {codes(d)}")
    ok(d["tier"] == before["tier"], "blocking changed the risk tier; it should only change the action")
    ok(x.pay("arjun", d)["status"] == "REFUSED", "the PSP paid a payee the payer had blocked")
    other = x.assess("kavita", vpa, 300, entry="qr")
    ok(other["action"] == "ALLOW", "one person's block stopped someone else")
    x.call("PUT", "/v1/policy/mode", x.admin, body={"mode": "shadow"})
    try:
        sh = x.assess("arjun", vpa, 300, entry="qr")
        ok(sh["action"] == "DECLINE" and codes(sh) == ["USER_BLOCKED"], f"shadow mode ignored the payer's own block: {sh['action']} {codes(sh)}")
        ok(x.pay("arjun", sh)["status"] == "REFUSED", "the PSP paid a blocked payee in shadow mode")
    finally:
        x.call("PUT", "/v1/policy/mode", x.admin, body={"mode": "enforce"})
    x.call("POST", "/v1/risk/outcome", tok, body={"decision_id": d["decision_id"], "user_action": "ABORTED"}, expect=202)
    time.sleep(0.6)
    after = x.call("GET", f"/v1/payee/{vpa.replace('@', '%40')}/reputation", tok)["trust_score"]
    ok(after >= trust, f"blocking and trying again lowered the shop's reputation ({trust} -> {after})")
    rep = x.call("POST", "/v1/users/me/blocks", tok, body={"vpa": "quick.returns@banka", "report": True})
    ok(any(b["vpa"] == "quick.returns@banka" for b in rep["blocks"]) and rep["report"], "block with a report did not file one")
    pend = x.call("DELETE", f"/v1/users/me/blocks/{vpa.replace('@', '%40')}", tok)
    b = next(i for i in pend["blocks"] if i["vpa"] == vpa)
    ok(b["status"] == "UNBLOCK_PENDING" and 0 < b["seconds_left"] <= pend["unblock_delay_s"], f"unblocking was immediate: {b}")
    ok(x.assess("arjun", vpa, 300, entry="qr")["action"] == "DECLINE", "the block lifted before its waiting time")
    kept = x.call("POST", f"/v1/users/me/blocks/{vpa.replace('@', '%40')}/keep", tok)
    ok(next(i for i in kept["blocks"] if i["vpa"] == vpa)["status"] == "ACTIVE", "keeping the block did not cancel the unblock")
    pend = x.call("DELETE", f"/v1/users/me/blocks/{vpa.replace('@', '%40')}", tok)
    x.keep["unblock"] = (vpa, time.time() + next(i for i in pend["blocks"] if i["vpa"] == vpa)["seconds_left"])
    x.call("DELETE", "/v1/users/me/blocks/never.blocked%40banka", tok, expect=404)


def at36(x: Ctx):
    """AT-36 Trends: this week's commonest scam patterns, by name and advice, with rounded counts."""
    t = x.call("GET", "/v1/trends", x.users["meera"]["token"])
    ok(t["days"] == 7 and 1 <= len(t["trends"]) <= 3, f"expected 1 to 3 trends, got {len(t['trends'])}")
    for item in t["trends"]:
        ok(item["name"] and item["advice"] and item["trend"] in ("new", "up", "down", "steady"), f"incomplete trend {item}")
        ok(item["seen"] >= 3, "a pattern met by too few people was shown")
    ok(any(ord(ch) > 0x900 for ch in t["trends"][0]["name"]), "Meera's trends are not in Hindi")
    x.call("GET", "/v1/trends", x.admin)
    x.call("GET", "/v1/trends", None, expect=401)
    x.results_note = ", ".join(f"{i['code']} {i['seen']}+ ({i['trend']})" for i in x.call("GET", "/v1/trends", x.users["arjun"]["token"])["trends"])


# ------------------------------------------------------------- security (F-35)
def at37(x: Ctx):
    """AT-37 Sessions: refresh, sign-out, sign-out-everywhere and a PIN change each end exactly the right tokens."""
    phone = f"97{(int(time.time()) + 7) % 10 ** 8:08d}"
    me = lambda t, expect=200: x.call("GET", "/v1/users/me", t, expect=expect)
    x.call("POST", "/v1/auth/register", body={"name": "Weak Pin", "phone": f"96{int(time.time()) % 10 ** 8:08d}", "pin": "123456"}, expect=400)
    x.call("POST", "/v1/auth/register", body={"name": "Weak Pin", "phone": phone, "pin": phone[-6:]}, expect=400)
    reg = x.call("POST", "/v1/auth/register", body={"name": "Session Test", "phone": phone, "pin": "482913"})
    t1 = reg["token"]
    ok(0 < reg["expires_in_s"] <= x.settings.user_token_minutes * 60 and reg["auth_method"] == "pin", f"session facts wrong: {reg['expires_in_s']}")
    t2 = x.call("POST", "/v1/auth/login", body={"phone": phone, "pin": "482913"})["token"]
    t1b = x.call("POST", "/v1/auth/refresh", t1)["token"]
    ok(t1b != t1 and me(t1b)["vpa"] == reg["user"]["vpa"], "the refreshed token does not work")
    x.call("POST", "/v1/auth/logout", t1b)
    me(t1, 401), me(t1b, 401)
    ok(me(t2)["vpa"] == reg["user"]["vpa"], "signing out one session ended another")
    wrong = x.c.post("/v1/auth/verify-pin", headers={"Authorization": f"Bearer {t2}"}, json={"pin": "000000"})
    ok(wrong.status_code == 403 and wrong.json()["error"]["code"] == "WRONG_PIN", f"a wrong unlock PIN gave {wrong.status_code}")
    ok(x.call("POST", "/v1/auth/verify-pin", t2, body={"pin": "482913"})["ok"], "the right unlock PIN was refused")
    x.call("POST", "/v1/auth/change-pin", t2, body={"current_pin": "000000", "new_pin": "739104"}, expect=403)
    x.call("POST", "/v1/auth/change-pin", t2, body={"current_pin": "482913", "new_pin": "111111"}, expect=400)
    t3 = x.call("POST", "/v1/auth/change-pin", t2, body={"current_pin": "482913", "new_pin": "739104"})["token"]
    me(t2, 401)
    ok(me(t3)["vpa"] == reg["user"]["vpa"], "the token issued with the new PIN does not work")
    x.call("POST", "/v1/auth/login", body={"phone": phone, "pin": "482913"}, expect=401)
    t4 = x.call("POST", "/v1/auth/login", body={"phone": phone, "pin": "739104"})["token"]
    x.call("POST", "/v1/auth/logout-all", t4)
    me(t3, 401), me(t4, 401)
    # a token that was never issued by this service
    parts = t3.split(".")
    forged = ".".join([parts[0], parts[1], parts[2][:-4] + ("AAAA" if not parts[2].endswith("AAAA") else "BBBB")])
    me(forged, 401)
    me("eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0." + parts[1] + ".", 401)       # "alg": "none"
    audit = x.call("GET", "/v1/audit?action=auth.", x.admin)["entries"]
    acts = {e["action"] for e in audit}
    ok({"auth.logout", "auth.pin_changed", "auth.logout_all"} <= acts, f"session events missing from the audit log: {sorted(acts)}")


def _proof(secret: str, challenge_id: str) -> str:
    return hmac.new(bytes.fromhex(secret), challenge_id.encode(), hashlib.sha256).hexdigest()


def at38(x: Ctx):
    """AT-38 Step-Up and payment: no bare biometric, three wrong codes lock, device proof, pay once, only your own."""
    arj, sun = x.users["arjun"]["token"], x.users["sunita"]["token"]
    pin = x.settings.seed_user_pin
    _call(x, "arjun", "start", 0), _call(x, "sunita", "start", 0)
    d1 = x.assess("arjun", "kavita@banka", 40000, entry="typed", dwell=30)
    d2 = x.assess("sunita", "rohan@banka", 30000, entry="typed", dwell=30)
    ok(d1["action"] == "STEP_UP" and d2["action"] == "STEP_UP", f"expected two step-ups, got {d1['action']} and {d2['action']}")
    c1, c2 = d1["step_up"]["challenge_id"], d2["step_up"]["challenge_id"]
    # one person cannot touch another's payment
    x.call("POST", "/v1/stepup/verify", sun, body={"challenge_id": c1, "answer": "no"}, expect=403)
    x.call("POST", "/mock-psp/v1/pay", sun, body={"txn_id": d1["txn_id"], "decision_id": d1["decision_id"], "pin": "1234"}, expect=403)
    x.call("POST", "/v1/risk/outcome", sun, body={"decision_id": d1["decision_id"], "user_action": "ABORTED"}, expect=403)
    x.call("POST", "/v1/risk/outcome", arj, body={"decision_id": d1["decision_id"], "user_action": "PROCEEDED", "final_status": "SUCCESS"}, expect=403)
    replay = x.assess("sunita", "rohan@banka", 30000, txn=d1["txn_id"])
    ok(replay["decision_id"] != d1["decision_id"], "one payer was handed another's decision for the same txn_id")
    x.call("POST", "/v1/stepup/challenge", arj, body={"challenge_id": c1}, expect=425)          # no code before the pause ends
    time.sleep(max(d1["step_up"]["cooloff_s"], d2["step_up"]["cooloff_s"]) + 0.4)
    first = x.call("POST", "/v1/stepup/verify", arj, body={"challenge_id": c1, "answer": "no"})
    ok(first["result"] == "AUTH_REQUIRED" and first["auth_methods"] == ["otp"], f"methods offered before enrolment: {first}")
    x.call("POST", "/v1/stepup/verify", arj, body={"challenge_id": c1, "answer": "no", "auth": {"method": "biometric", "biometric_ok": True}}, expect=400)
    # a device enrolled with the PIN can prove itself; nothing else can
    secret, key_id = secrets.token_hex(32), f"e2e-{uuid.uuid4().hex[:10]}"
    x.call("POST", "/v1/auth/device-key", arj, body={"pin": "000001", "device": {"key_id": key_id, "secret": secret}}, expect=403)
    x.call("POST", "/v1/auth/device-key", arj, body={"pin": pin, "device": {"key_id": key_id, "secret": secret, "label": "test phone"}})
    ok(x.call("POST", "/v1/stepup/verify", arj, body={"challenge_id": c1, "answer": "no"})["auth_methods"] == ["otp", "biometric"], "biometric not offered after enrolment")
    badp = x.call("POST", "/v1/stepup/verify", arj, body={"challenge_id": c1, "answer": "no", "auth": {"method": "biometric", "key_id": key_id, "proof": "0" * 64}})
    ok(badp["result"] == "BAD_CODE" and badp["tries_left"] == 2, f"a wrong device proof gave {badp}")
    good = x.call("POST", "/v1/stepup/verify", arj, body={"challenge_id": c1, "answer": "no", "auth": {"method": "biometric", "key_id": key_id, "proof": _proof(secret, c1)}})
    ok(good["result"] == "PASSED", f"a correct device proof gave {good}")
    a, b = x.pay("arjun", d1), x.pay("arjun", d1)
    ok(a["status"] == "SUCCESS" and b["status"] == "REFUSED", f"a decision paid twice: {a['status']}, {b['status']}")
    x.call("DELETE", f"/v1/auth/devices/{key_id}", arj)
    # three wrong codes close the challenge for good
    sent = x.call("POST", "/v1/stepup/challenge", sun, body={"challenge_id": c2})
    x.call("POST", "/v1/stepup/challenge", sun, body={"challenge_id": c2}, expect=429)           # no second code at once
    wrong = "000000" if sent["demo_code"] != "000000" else "111111"
    res = [x.call("POST", "/v1/stepup/verify", sun, body={"challenge_id": c2, "answer": "no", "auth": {"method": "otp", "otp": wrong}})["result"] for _ in range(3)]
    ok(res == ["BAD_CODE", "BAD_CODE", "LOCKED"], f"three wrong codes gave {res}")
    x.call("POST", "/v1/stepup/verify", sun, body={"challenge_id": c2, "answer": "no", "auth": {"method": "otp", "otp": sent["demo_code"]}}, expect=409)
    ok(x.pay("sunita", d2)["status"] == "REFUSED", "the PSP paid after the step-up was locked")
    _call(x, "arjun", "end"), _call(x, "sunita", "end")


def at39(x: Ctx):
    """AT-39 Front door: host check, CORS, headers, body size, and no demo sign-in through a proxy."""
    r = x.c.get("/v1/auth/config")
    h = {k.lower(): v for k, v in r.headers.items()}
    for name in ("x-content-type-options", "x-frame-options", "referrer-policy", "content-security-policy"):
        ok(name in h, f"response header {name} is missing")
    ok(h.get("cache-control") == "no-store" and "server" not in h, "responses are cacheable or name the server software")
    ok(x.c.get("/readyz", headers={"Host": "evil.example"}).status_code == 400, "an unknown Host was answered")
    ok(x.c.get("/readyz", headers={"Host": "192.168.1.20:8000"}).status_code == 200, "a private address was refused as Host")
    cors = x.settings.cors_origins.strip().lower()
    if cors != "*":
        evil = x.c.get("/v1/auth/config", headers={"Origin": "https://evil.example"})
        ok("access-control-allow-origin" not in {k.lower() for k in evil.headers}, "a foreign web page may read API responses")
        pre = x.c.options("/v1/auth/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        ok(pre.status_code == 400, f"a foreign web page's preflight was accepted ({pre.status_code})")
    if cors == "local":
        mine = x.c.get("/v1/auth/config", headers={"Origin": "http://localhost:3000"})
        ok(mine.headers.get("access-control-allow-origin") == "http://localhost:3000", "the dashboard's origin is not allowed")
    big = x.c.post("/v1/auth/login", content=b'{"phone":"' + b"9" * 100000 + b'"}', headers={"Content-Type": "application/json"})
    ok(big.status_code == 413, f"a 100 KB sign-in request gave {big.status_code}")
    if x.settings.demo_login.strip().lower() == "local":
        u = x.users["rohan"]["user_id"]
        via = x.c.post("/v1/auth/demo-login", json={"user_id": u}, headers={"X-Forwarded-For": "203.0.113.9"})
        ok(via.status_code == 404, f"demo sign-in through a proxy gave {via.status_code}")
        ok(x.c.get("/v1/auth/demo-users", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 404, "the demo account list is visible through a proxy")
        cfg = x.c.get("/v1/auth/config", headers={"X-Forwarded-For": "203.0.113.9"}).json()
        ok(cfg["demo_login"] is False and x.call("GET", "/v1/auth/config")["demo_login"] is True, "the sign-in screen is told the wrong thing")
    post = x.call("GET", "/v1/security/posture", x.admin)
    ok(post["overall"] in ("ok", "warn", "fail") and len(post["checks"]) >= 10, "the security page has no checks")
    x.call("GET", "/v1/security/posture", x.staff["analyst@banka.demo"], expect=403)
    x.results_note = f"posture {post['overall']}: " + ", ".join(f"{c['key']}={c['status']}" for c in post["checks"] if c["status"] != "ok")


def at40(x: Ctx):
    """AT-40 Never-pay list: the block lifts by itself when its waiting time is over, and not before."""
    vpa, due = x.keep.get("unblock", (None, 0))
    ok(vpa is not None, "needs the unblock started in AT-35")
    tok = x.users["arjun"]["token"]
    if not x.slow:
        x.results_note = "skipped the wait (--fast)"
        return
    wait = due - time.time()
    if wait > 2:
        ok(x.assess("arjun", vpa, 300, entry="qr")["action"] == "DECLINE", "the block lifted early")
        time.sleep(wait)
    gone = x.wait(lambda: all(b["vpa"] != vpa for b in x.call("GET", "/v1/users/me/blocks", tok)["blocks"]), 12, 1.0)
    ok(gone, "the block was still listed after its waiting time")
    ok(x.assess("arjun", vpa, 300, entry="qr")["action"] == "ALLOW", "the payee is still declined after the block lifted")
    x.results_note = f"lifted after {x.settings.unblock_seconds_demo}s"


# Order matters: the guardian tests need a payer whom no flagged number has just called, so they run
# before the scenarios that stage such calls (a call from a flagged number affects the next ten minutes).
TESTS = [("AT-1", at1), ("AT-2", at2), ("AT-14", at14), ("AT-15", at15), ("AT-3", at3), ("AT-4", at4), ("AT-5", at5),
         ("AT-6", at6), ("AT-7", at7), ("AT-8", at8), ("AT-11", at11), ("AT-16", at16), ("AT-17", at17),
         ("AT-18", at18), ("AT-19", at19), ("AT-23", at23), ("AT-24", at24), ("AT-27", at27), ("AT-28", at28),
         ("AT-30", at30), ("AT-22", at22), ("AT-25", at25), ("AT-10", at10), ("AT-31", at31), ("AT-32", at32),
         ("AT-33", at33), ("AT-34", at34), ("AT-35", at35), ("AT-36", at36), ("AT-37", at37), ("AT-38", at38),
         ("AT-39", at39), ("AT-40", at40)]


def run_e2e(base: str = "http://localhost:8000", only: str = "", slow: bool = True) -> bool:
    x = Ctx(base)
    x.keep, x.slow, x.results_note = {}, slow, ""
    try:
        h = x.c.get("/readyz").json()
    except Exception as exc:
        print(f"Cannot reach the API at {base}: {exc}\nStart it with:  python manage.py api")
        return False
    print(f"API ready={h['ready']} model={h['model_version']} rules={h['rules_version']} degraded={h['degraded']}")
    x.reset()
    x.login_all()
    wanted = {t.strip().upper() for t in only.split(",") if t.strip()}
    passed = failed = 0
    for name, fn in TESTS:
        if wanted and name not in wanted:
            continue
        x.results_note = ""
        t0 = time.time()
        try:
            fn(x)
            passed += 1
            print(f"PASS {name:6s} {fn.__doc__.split(name, 1)[-1].strip()[:92]}"
                  + (f"\n            [{x.results_note}]" if x.results_note else "") + f"  ({time.time() - t0:.1f}s)")
        except Fail as e:
            failed += 1
            print(f"FAIL {name:6s} {fn.__doc__.strip()[:92]}\n            -> {e}")
        except Exception:
            failed += 1
            print(f"ERR  {name:6s} {fn.__doc__.strip()[:92]}\n{traceback.format_exc(limit=3)}")
    if not wanted:
        x.reset()
    print(f"\n{passed} passed, {failed} failed")
    return failed == 0
