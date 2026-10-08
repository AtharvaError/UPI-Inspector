"""Synthetic world and event stream (F-14; docs/06, section 14).

Everything here is fictional. Handles @banka and @bankb are not real UPI handles.
The output is a time-ordered list of events: calls, message checks, payments and reports.
"""
from __future__ import annotations

import math
import random
import time
import uuid
from dataclasses import dataclass, field

import numpy as np

from app.security import phone_hash

NS = uuid.UUID("6f1d2c3a-8b7e-4c55-9d10-3e5a7b9c1d2f")
DAY = 86400.0
HOUR = 3600.0
TENANTS = [("bank_a", "Bank A", "banka"), ("bank_b", "Bank B", "bankb")]
HANDLE = {"bank_a": "banka", "bank_b": "bankb"}

FIRST = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan", "Rohan", "Kabir",
         "Ananya", "Diya", "Aadhya", "Saanvi", "Kiara", "Myra", "Anika", "Riya", "Ira", "Meera", "Kavya", "Tara",
         "Suresh", "Ramesh", "Mahesh", "Rajesh", "Sunil", "Anil", "Vijay", "Sanjay", "Manoj", "Deepak", "Prakash",
         "Sunita", "Anita", "Kavita", "Savita", "Lata", "Usha", "Asha", "Rekha", "Geeta", "Seema", "Pooja", "Neha",
         "Farhan", "Imran", "Zoya", "Ayesha", "Harpreet", "Gurpreet", "Simran", "Joseph", "Maria", "Thomas"]
LAST = ["Sharma", "Verma", "Gupta", "Patel", "Shah", "Mehta", "Joshi", "Kulkarni", "Deshmukh", "Patil", "Pawar",
        "Iyer", "Nair", "Menon", "Reddy", "Rao", "Naidu", "Das", "Ghosh", "Banerjee", "Singh", "Kaur", "Gill",
        "Khan", "Shaikh", "Ansari", "Yadav", "Tiwari", "Mishra", "Pandey", "Chauhan", "Fernandes", "DSouza", "Bose"]
SHOP_A = ["Fresh", "City", "Royal", "Shree", "New", "Green", "Golden", "Super", "Quick", "Daily", "Annapurna",
          "Sai", "Laxmi", "Metro", "Urban", "Classic", "Star", "Prime", "Happy", "Modern"]
SHOP_B = ["Mart", "Medicals", "Kirana", "Bakery", "Sweets", "Electronics", "Fashion", "Cafe", "Restaurant",
          "Dairy", "Stationers", "Pharmacy", "Vegetables", "Fuel Station", "Salon", "Books", "Hardware",
          "Tiffin Centre", "Mobile Store", "Footwear"]

SEGMENTS = {
    # share, payments/day, (merchant median, sigma), (peer median, sigma), balance range
    "student":        dict(share=0.20, rate=1.0, merch=(150, 0.8), peer=(300, 0.9), bal=(8e3, 4e4)),
    "salaried":       dict(share=0.45, rate=1.6, merch=(400, 0.9), peer=(1500, 1.0), bal=(4e4, 4e5)),
    "elderly":        dict(share=0.15, rate=0.6, merch=(350, 0.8), peer=(1000, 0.9), bal=(8e4, 8e5)),
    "small_business": dict(share=0.20, rate=2.4, merch=(900, 1.0), peer=(5000, 1.1), bal=(1e5, 1.5e6)),
}
HOUR_W = np.array([0.2, 0.1, 0.05, 0.05, 0.05, 0.2, 0.6, 1.5, 2.5, 3, 3.2, 3.5, 4, 3.8, 3, 3, 3.2, 3.8, 4.5, 5, 4.5,
                   3.5, 2, 0.8])

BENIGN_NOTES = ["rent", "lunch", "dinner split", "thanks", "groceries", "bill payment", "birthday gift",
                "school fees", "milk bill", "maintenance", "petrol", "movie tickets", "medicine", "for mom",
                "trip share", "emi", "advance", "booking", "token amount", "invoice 1023", "order 4521",
                "tuition fees", "society maintenance", "cab fare", "recharge", "hospital bill", "office lunch"]
NOTES = {
    "DIGITAL_ARREST": ["case verification", "verification deposit", "safe account transfer", "court fee",
                       "police verification amount", "rbi safe account", "cbi verification"],
    "FAKE_REFUND": ["refund processing fee", "refund charges", "cashback claim fee", "refund verification",
                    "refund token amount", "refund activation"],
    "MULE_TRANSFER": ["advance", "booking", "token amount", "deposit for flat", "advance for booking",
                      "payment for sofa", "bike booking amount"],
    "IMPERSONATION": ["urgent transfer for boss", "vendor payment urgent new account", "emergency hospital money",
                      "urgent help friend", "confidential payment", "invoice 2231"],
    "KYC_BILL_THREAT": ["kyc update charge", "kyc fee", "electricity bill update", "sim kyc charge",
                        "account reactivation fee", "bill update charge"],
    "INVESTMENT_TASK": ["task deposit", "investment plan a", "vip plan recharge", "trading deposit", "prepaid task",
                        "withdrawal tax", "task wallet recharge"],
}
CLAIMED = {
    "DIGITAL_ARREST": ["Cyber Cell Verification", "RBI Safe Account", "CBI Verification Desk",
                       "Customs Clearance Dept", "Police Verification Account"],
    "FAKE_REFUND": ["SBI Refund Desk", "Amazon Refunds", "Flipkart Customer Care", "Paytm Cashback Team",
                    "HDFC Bank Refund Cell"],
    "KYC_BILL_THREAT": ["SBI KYC Update", "BSES Bill Desk", "Electricity Board Officer", "Jio KYC Verification",
                        "HDFC KYC Support"],
    "IMPERSONATION": ["Mehta Suppliers Pvt Ltd", "Sharma Traders", "Apex Logistics", "Verma Associates",
                      "National Steel Suppliers"],
    "INVESTMENT_TASK": ["Alpha Trading Desk", "VIP Task Team", "Growth Capital Advisors", "Prime Earnings Club"],
}
MESSAGES = {
    "DIGITAL_ARREST": ("authority_threat",
                       "{agency} notice: your Aadhaar is linked to a money laundering case. To avoid arrest transfer your funds to the verification account {upi} immediately. Do not tell anyone."),
    "FAKE_REFUND": ("refund_bait",
                    "Dear customer, your refund of Rs {amt} is pending. Pay Rs {fee} processing fee to {upi} to receive it. Helpline {phone}"),
    "IMPERSONATION": ("impersonation_request",
                      "Hi, this is {name} (new number). I am in a meeting and can't talk. Need you to pay a vendor urgently Rs {amt} to {upi}. Will explain later."),
    "KYC_BILL_THREAT": ("kyc_bill_threat",
                        "Your electricity connection will be disconnected tonight. Pay pending Rs {fee} to officer UPI {upi} and call {phone} immediately."),
    "INVESTMENT_TASK": ("investment_task",
                        "Guaranteed 30% returns in 7 days. Task completed, deposit Rs {fee} as prepaid task amount to {upi} to withdraw Rs {amt}."),
}
FAMILIES = ["DIGITAL_ARREST", "FAKE_REFUND", "MULE_TRANSFER", "IMPERSONATION", "KYC_BILL_THREAT", "INVESTMENT_TASK"]
TARGET_W = {   # which segments each family targets
    "DIGITAL_ARREST": {"elderly": 5, "salaried": 2, "small_business": 2, "student": 0.5},
    "FAKE_REFUND": {"elderly": 2, "salaried": 2, "small_business": 1.5, "student": 2},
    "MULE_TRANSFER": {"elderly": 1, "salaried": 2, "small_business": 1.5, "student": 2.5},
    "IMPERSONATION": {"elderly": 1.5, "salaried": 1.5, "small_business": 4, "student": 0.5},
    "KYC_BILL_THREAT": {"elderly": 4, "salaried": 2, "small_business": 1.5, "student": 0.7},
    "INVESTMENT_TASK": {"elderly": 0.7, "salaried": 3, "small_business": 1.5, "student": 3},
}

KIOSK_NAMES = ["SBI Customer Service Point", "Jio Recharge Store", "Amazon Easy Store", "HDFC Bank CSP",
               "Airtel Payments Bank Point", "PNB Customer Service Point", "Flipkart Pickup Store",
               "Electricity Bill Collection Centre", "ICICI Bank Business Correspondent", "Paytm Service Agent",
               "India Post Courier Agent", "Axis Bank Mini Branch", "Bank of Baroda Kiosk", "Tata Power Bill Desk",
               "Vodafone Idea Recharge Point"]

DEMO_USERS = [
    dict(key="meera", name="Meera Joshi", tenant="bank_a", segment="elderly", age_band="60+", locale="hi",
         vpa="meera@banka", phone="9000000001", balance=420000.0),
    dict(key="arjun", name="Arjun Joshi", tenant="bank_a", segment="salaried", age_band="26-59", locale="en",
         vpa="arjun@banka", phone="9000000002", balance=185000.0),
    dict(key="kavita", name="Kavita Rao", tenant="bank_a", segment="small_business", age_band="26-59", locale="en",
         vpa="kavita@banka", phone="9000000003", balance=640000.0),
    dict(key="rohan", name="Rohan Das", tenant="bank_a", segment="student", age_band="18-25", locale="en",
         vpa="rohan@banka", phone="9000000004", balance=38000.0),
    dict(key="sunita", name="Sunita Verma", tenant="bank_b", segment="salaried", age_band="26-59", locale="hi",
         vpa="sunita@bankb", phone="9000000005", balance=210000.0),
]
DEMO_PAYEES = [
    dict(vpa="sharma.rent@banka", name="Sharma Properties", kind="person", verified=False, age_days=900),
    dict(vpa="freshmart@banka", name="FreshMart Groceries", kind="merchant", verified=True, age_days=1200),
    dict(vpa="newcafe@banka", name="Third Wave Cafe", kind="merchant", verified=False, age_days=5),
    dict(vpa="refund.desk7@bankb", name="Rakesh Kumar", kind="person", verified=False, age_days=4),
    dict(vpa="cyber.cell.verify@bankb", name="Imran Shaikh", kind="person", verified=False, age_days=3),
    dict(vpa="quick.returns@banka", name="Deepak Nair", kind="person", verified=False, age_days=12),
    dict(vpa="layer2.acct@bankb", name="Pooja Singh", kind="person", verified=False, age_days=40),
    dict(vpa="vendor.payments@banka", name="Anil Traders", kind="person", verified=False, age_days=2),
    dict(vpa="kyc.update.sbi@bankb", name="Sanjay Yadav", kind="person", verified=False, age_days=6),
    dict(vpa="booking.advance@bankb", name="Vikram Sethi", kind="person", verified=False, age_days=3),
]
DEMO_CALLERS = {0: "9111111100", 1: "9111111101", 2: "9111111102", 3: "9111111103"}
DEMO_MULE_FP = "fp-demo-ring"


@dataclass
class SimConfig:
    n_users: int = 1000
    n_merchants: int = 250
    days: int = 90
    n_campaigns: int = 66
    seed: int = 7
    end_ts: float = field(default_factory=time.time)

    @property
    def start_ts(self) -> float:
        return self.end_ts - self.days * DAY


@dataclass
class World:
    cfg: SimConfig
    users: list[dict] = field(default_factory=list)
    by_vpa: dict[str, dict] = field(default_factory=dict)       # user by primary vpa
    vpas: dict[str, dict] = field(default_factory=dict)         # directory
    vpa_fps: dict[str, set] = field(default_factory=dict)
    merchants: list[dict] = field(default_factory=list)
    campaigns: list[dict] = field(default_factory=list)
    caller_risk: dict[str, int] = field(default_factory=dict)   # caller hash -> risk
    demo: dict[str, dict] = field(default_factory=dict)


def uid(*parts: str) -> str:
    return str(uuid.uuid5(NS, "/".join(parts)))


def _slug(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum() or ch == ".")


def _lognorm(rng: random.Random, median: float, sigma: float, lo: float = 10.0, hi: float = 5e6) -> float:
    return float(max(lo, min(hi, round(median * math.exp(sigma * rng.gauss(0, 1))))))


def _add_vpa(w: World, vpa: str, *, tenant: str, name: str, kind: str, verified: bool, first_seen: float,
             owner: str | None = None, shop_name: str | None = None) -> dict:
    v = dict(vpa=vpa, tenant=tenant, registered_name=name, kind=kind, verified=verified, first_seen=first_seen,
             owner_user_id=owner, shop_name=shop_name or name, on_denylist=False)
    w.vpas[vpa] = v
    return v


def _new_user(w: World, rng: random.Random, i: int, *, tenant: str, name: str, segment: str, age_band: str,
              locale: str, vpa: str, phone: str, balance: float, first_seen: float, is_demo: bool = False,
              is_mule: bool = False, key: str | None = None) -> dict:
    user_id = uid("user", key or vpa)
    seg = SEGMENTS[segment]
    hours = np.roll(HOUR_W, rng.choice([-2, -1, 0, 0, 0, 1, 2])) ** rng.uniform(1.0, 1.6)
    u = dict(user_id=user_id, idx=i, tenant=tenant, name=name, segment=segment, age_band=age_band, locale=locale,
             vpa=vpa, phone=phone, phone_hash=phone_hash(phone), balance=float(balance), first_seen=first_seen,
             device_id=uid("device", user_id, "0"), fingerprint=f"fp-{user_id[:12]}", is_demo=is_demo,
             is_mule=is_mule, key=key, rate=seg["rate"] * rng.uniform(0.6, 1.5), hours=hours / hours.sum(),
             favourites=[], peers=[], landlord=None, rent=0.0, device_switch_ts=None, new_device_id=None)
    w.users.append(u)
    w.by_vpa[vpa] = u
    _add_vpa(w, vpa, tenant=tenant, name=name, kind="person", verified=False, first_seen=first_seen, owner=user_id)
    w.vpa_fps.setdefault(vpa, set()).add(u["fingerprint"])
    return u


def build_world(cfg: SimConfig) -> World:
    rng = random.Random(cfg.seed)
    w = World(cfg=cfg)
    end = cfg.end_ts

    # merchants
    seen: set[str] = set()
    for i in range(cfg.n_merchants):
        shop = f"{rng.choice(SHOP_A)} {rng.choice(SHOP_B)}"
        tenant = rng.choice(["bank_a", "bank_a", "bank_b"])
        vpa = f"{_slug(shop)}{i}@{HANDLE[tenant]}"
        if vpa in seen:
            continue
        seen.add(vpa)
        proprietor = rng.random() < 0.08          # registered in the owner's personal name: a legitimate mismatch
        reg = f"{rng.choice(FIRST)} {rng.choice(LAST)}" if proprietor else shop
        new_shop = rng.random() < 0.10            # opened during the simulated period
        m = _add_vpa(w, vpa, tenant=tenant, name=reg, kind="merchant",
                     verified=rng.random() < (0.3 if new_shop else 0.7),
                     first_seen=end - (rng.uniform(1, cfg.days) if new_shop else rng.uniform(100, 2000)) * DAY,
                     shop_name=shop)
        m["weight"] = 1.0 / (1 + i) ** 0.7
        w.merchants.append(m)
    for dp in DEMO_PAYEES:
        if dp["kind"] == "merchant":
            tenant = "bank_a" if dp["vpa"].endswith("@banka") else "bank_b"
            m = _add_vpa(w, dp["vpa"], tenant=tenant, name=dp["name"], kind="merchant", verified=dp["verified"],
                         first_seen=end - dp["age_days"] * DAY)
            m["weight"] = 0.9 if dp["vpa"].startswith("freshmart") else 0.0
            if m["weight"] > 0:
                w.merchants.append(m)
    # five merchants that launch during the period with a genuine burst of first-time customers
    launches = []
    for i in range(5):
        shop = f"{rng.choice(SHOP_A)} {rng.choice(SHOP_B)} Launch{i}"
        vpa = f"{_slug(shop)}@{HANDLE['bank_a']}"
        day = rng.uniform(8, cfg.days - 3)
        m = _add_vpa(w, vpa, tenant="bank_a", name=shop, kind="merchant", verified=rng.random() < 0.4,
                     first_seen=cfg.start_ts + (day - rng.uniform(0.5, 3)) * DAY)
        launches.append((m, cfg.start_ts + day * DAY))
    w.demo["launches"] = launches

    # users
    seg_names = list(SEGMENTS)
    seg_w = [SEGMENTS[s]["share"] for s in seg_names]
    for d in DEMO_USERS:
        _new_user(w, rng, len(w.users), tenant=d["tenant"], name=d["name"], segment=d["segment"],
                  age_band=d["age_band"], locale=d["locale"], vpa=d["vpa"], phone=d["phone"], balance=d["balance"],
                  first_seen=end - rng.uniform(700, 1500) * DAY, is_demo=True, key=d["key"])
        w.demo[d["key"]] = w.users[-1]
    for i in range(cfg.n_users):
        seg = rng.choices(seg_names, seg_w)[0]
        first, last = rng.choice(FIRST), rng.choice(LAST)
        tenant = "bank_a" if rng.random() < 0.6 else "bank_b"
        age = {"student": "18-25", "elderly": "60+"}.get(seg) or ("18-25" if rng.random() < 0.1 else
                                                                 "60+" if rng.random() < 0.08 else "26-59")
        hi = 0.6 if seg == "elderly" else 0.35
        lo, up = SEGMENTS[seg]["bal"]
        _new_user(w, rng, len(w.users), tenant=tenant, name=f"{first} {last}", segment=seg, age_band=age,
                  locale="hi" if rng.random() < hi else "en", vpa=f"{_slug(first)}.{_slug(last)}{i}@{HANDLE[tenant]}",
                  phone=f"7{100000000 + i:09d}", balance=round(math.exp(rng.uniform(math.log(lo), math.log(up)))),
                  first_seen=end - (rng.uniform(1, 60) if rng.random() < 0.12 else rng.uniform(100, 1500)) * DAY)
    regular = [u for u in w.users if not u["is_mule"]]
    mw = [m["weight"] for m in w.merchants]
    for u in regular:
        u["favourites"] = list({m["vpa"] for m in rng.choices(w.merchants, mw, k=rng.randint(5, 15))})
        u["peers"] = [p["vpa"] for p in rng.sample(regular, min(len(regular), rng.randint(3, 8))) if p is not u]
        if rng.random() < 0.25:
            u["landlord"] = rng.choice(regular)["vpa"]
            u["rent"] = float(rng.choice([6000, 8000, 9500, 12000, 15000, 18000, 22000, 30000]))
        if rng.random() < 0.03:
            u["device_switch_ts"] = cfg.start_ts + rng.uniform(5, cfg.days - 2) * DAY
            u["new_device_id"] = uid("device", u["user_id"], "1")
    # Legitimate look-alikes of scam signals:
    # individuals who run a bank or brand service point, so an institutional name sits on a personal account
    plain = [u for u in regular if not u["is_demo"]]
    for u, shop in zip(rng.sample(plain, min(15, len(plain))), KIOSK_NAMES):
        w.vpas[u["vpa"]]["shop_name"] = shop
        u["kiosk"] = True
        for fan in rng.sample(plain, max(1, len(plain) // 40)):
            fan["favourites"].append(u["vpa"])
    # and people who collect money from friends and pay it on within minutes
    for u in rng.sample(plain, max(1, len(plain) // 50)):
        u["collector"] = True

    # demo payees that are people
    for dp in DEMO_PAYEES:
        if dp["kind"] == "person":
            tenant = "bank_a" if dp["vpa"].endswith("@banka") else "bank_b"
            is_mule = dp["vpa"] not in ("sharma.rent@banka",)
            u = _new_user(w, rng, len(w.users), tenant=tenant, name=dp["name"], segment="salaried",
                          age_band="26-59", locale="en", vpa=dp["vpa"], phone=f"6{len(w.users):09d}",
                          balance=20000.0, first_seen=end - dp["age_days"] * DAY, is_mule=is_mule)
            u["favourites"], u["peers"] = [], []
            if is_mule and dp["vpa"] in ("cyber.cell.verify@bankb", "layer2.acct@bankb", "refund.desk7@bankb"):
                w.vpa_fps[dp["vpa"]].add(DEMO_MULE_FP)
    meera = w.demo["meera"]
    meera["landlord"], meera["rent"] = "sharma.rent@banka", 12000.0
    for key in ("meera", "arjun", "kavita", "rohan", "sunita"):
        u = w.demo[key]
        if "freshmart@banka" not in u["favourites"]:
            u["favourites"].append("freshmart@banka")
        u["device_switch_ts"] = None
    meera["peers"] = ["arjun@banka"] + [p for p in meera["peers"] if p != "arjun@banka"][:3]
    for risk, phone in DEMO_CALLERS.items():
        if risk:
            w.caller_risk[phone_hash(phone)] = risk
    _build_campaigns(w, rng)
    return w


def _build_campaigns(w: World, rng: random.Random) -> None:
    cfg = w.cfg
    regular = [u for u in w.users if not u["is_mule"] and not u["is_demo"]]
    layer2: list[dict] = []
    for j in range(12):
        tenant = rng.choice(["bank_a", "bank_b"])
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        u = _new_user(w, rng, len(w.users), tenant=tenant, name=name, segment="salaried", age_band="26-59",
                      locale="en", vpa=f"{_slug(name.split()[0])}.l2x{j}@{HANDLE[tenant]}",
                      phone=f"6{500000000 + j:09d}", balance=50000.0,
                      first_seen=cfg.end_ts - rng.uniform(40, 200) * DAY, is_mule=True)
        u["favourites"], u["peers"] = [], []
        layer2.append(u)
    test_start_day = cfg.days - 15
    # Rented accounts: real people whose accounts criminals use. Rare, and reused across campaigns.
    pool_src = [u for u in regular if not u.get("kiosk") and u["first_seen"] < cfg.start_ts]
    rented_pool = rng.sample(pool_src, min(len(pool_src), max(4, len(regular) // 70)))
    for u in rented_pool:
        u["rented"] = True
    for c in range(cfg.n_campaigns):
        family = FAMILIES[c % len(FAMILIES)]
        start_day = rng.uniform(3, cfg.days - 1.5) if c >= 18 else rng.uniform(test_start_day, cfg.days - 1.5)
        variant = "unseen" if (start_day >= test_start_day and rng.random() < 0.35) else "base"
        ring_fp = f"fp-ring-{c}"
        l2 = rng.choice(layer2)
        w.vpa_fps[l2["vpa"]].add(ring_fp)
        mules = []
        for k in range(rng.randint(2, 4)):
            if rented_pool and rng.random() < 0.35:   # a real person's account, rented out
                mu = rng.choice(rented_pool)
                if mu not in mules:
                    w.vpa_fps[mu["vpa"]].add(ring_fp)
                    mules.append(mu)
                    continue
            tenant = rng.choice(["bank_a", "bank_b"])
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            age = rng.uniform(20, 40) if (variant == "unseen" and family == "MULE_TRANSFER") else rng.uniform(0.5, 8)
            stem = rng.choice(["pay", "desk", "help", "store", "deal", "team", "care", "official", "online", "rk"])
            mu = _new_user(w, rng, len(w.users), tenant=tenant, name=name, segment="salaried", age_band="26-59",
                           locale="en", vpa=f"{stem}.{_slug(name.split()[0])}{c}x{k}@{HANDLE[tenant]}",
                           phone=f"6{600000000 + c * 10 + k:09d}", balance=5000.0,
                           first_seen=cfg.start_ts + (start_day - age) * DAY, is_mule=True)
            mu["favourites"], mu["peers"] = [], []
            w.vpa_fps[mu["vpa"]].add(ring_fp)
            mules.append(mu)
        risk = rng.choices([3, 2, 1, 0], [0.4, 0.25, 0.15, 0.2])[0]
        callers = []
        for k in range(2):
            ph = f"8{700000000 + c * 10 + k:09d}"
            callers.append(ph)
            if risk:
                w.caller_risk[phone_hash(ph)] = risk
        n_victims = rng.randint(30, 70) if family == "MULE_TRANSFER" else rng.randint(15, 42)
        weights = [TARGET_W[family][u["segment"]] for u in regular]
        victims = rng.choices(regular, weights, k=n_victims)
        w.campaigns.append(dict(id=f"C{c:03d}", family=family, variant=variant, start_day=start_day, mules=mules,
                                layer2=l2, callers=callers, caller_risk=risk, victims=victims))


# ---------------------------------------------------------------------------------- events
def _session(rng: random.Random, method: str, legit_kind: str | None = None) -> dict:
    if method == "pasted":
        sw = rng.choices([0, 1, 2, 3], [0.3, 0.45, 0.2, 0.05])[0]
    else:
        sw = rng.choices([0, 1, 2, 3], [0.86, 0.10, 0.03, 0.01])[0]
    med = 50 if legit_kind == "big" else 18
    return {"vpa_entry_method": method, "app_switch_count": sw,
            "time_on_pay_screen_s": round(max(4, min(240, med * math.exp(0.5 * rng.gauss(0, 1)))), 1),
            "amount_edit_count": rng.choices([0, 1, 2], [0.92, 0.07, 0.01])[0]}


def _scam_session(rng: random.Random, methods: dict, sw: tuple[int, int], dwell: tuple[int, int]) -> dict:
    method = rng.choices(list(methods), list(methods.values()))[0]
    return {"vpa_entry_method": method, "app_switch_count": rng.randint(*sw),
            "time_on_pay_screen_s": float(rng.randint(*dwell)), "amount_edit_count": rng.choices([0, 1, 2], [0.6, 0.3, 0.1])[0]}


class _Ids:
    def __init__(self) -> None:
        self.n = 0

    def txn(self) -> str:
        self.n += 1
        return f"SIM-{self.n:07d}"


def _pay(ids: _Ids, ts: float, u: dict, payee: str, amount: float, session: dict, *, note: str = "",
         claimed: str | None = None, fraud: bool = False, family: str | None = None, campaign: str | None = None,
         variant: str | None = None, exclude: bool = False) -> dict:
    dev = u["new_device_id"] if (u.get("device_switch_ts") and ts >= u["device_switch_ts"]) else u["device_id"]
    return dict(t="pay", ts=ts, order=2, user=u, payee=payee, amount=float(round(amount, 2)), session=session,
                note=note, claimed=claimed, device_id=dev, fraud=fraud, family=family, campaign=campaign,
                variant=variant, exclude=exclude, txn_id=ids.txn())


def _call(ts_start: float, ts_end: float | None, u: dict, caller_phone: str, known: bool, risk: int) -> list[dict]:
    cid = str(uuid.uuid4())
    ch = phone_hash(caller_phone)
    ev = [dict(t="call", ts=ts_start, order=0, event="started", call_id=cid, user=u, caller_hash=ch, known=known,
               risk=risk)]
    if ts_end is not None:
        ev.append(dict(t="call", ts=ts_end, order=3, event="ended", call_id=cid, user=u, caller_hash=ch, known=known,
                       risk=risk))
    return ev


def _msg(ts: float, u: dict, family: str, vpa: str, rng: random.Random, flagged: bool = True) -> dict:
    label, tpl = MESSAGES.get(family, MESSAGES["FAKE_REFUND"])
    text = tpl.format(agency=rng.choice(["Mumbai Police", "CBI", "Delhi Cyber Cell"]), upi=vpa,
                      amt=rng.choice([1999, 4999, 12500, 49000]), fee=rng.choice([10, 49, 99, 499]),
                      phone=f"9{rng.randint(100000000, 999999999)}", name=rng.choice(FIRST))
    return dict(t="msg", ts=ts, order=1, user=u, family=family, label=label, text=text, vpas=[vpa], flagged=flagged,
                check_id=str(uuid.uuid4()))


def _legit_events(w: World, rng: random.Random, nrng: np.random.Generator, ids: _Ids) -> list[dict]:
    cfg = w.cfg
    ev: list[dict] = []
    regular = [u for u in w.users if not u["is_mule"]]
    new_users = [u for u in regular if u["first_seen"] > cfg.start_ts and not u["is_demo"]]
    kiosks = [u for u in regular if u.get("kiosk")]
    mw = [m["weight"] for m in w.merchants]
    hours = np.arange(24)
    for u in regular:
        seg = SEGMENTS[u["segment"]]
        counts = nrng.poisson(u["rate"], cfg.days)
        for day in range(cfg.days):
            for _ in range(int(counts[day])):
                ts = cfg.start_ts + day * DAY + float(nrng.choice(hours, p=u["hours"])) * HOUR + rng.uniform(0, HOUR)
                if ts >= cfg.end_ts - 600 or ts < u["first_seen"]:
                    continue
                r = rng.random()
                kind = None
                claimed = None
                if r < 0.55 and u["favourites"]:
                    payee = rng.choice(u["favourites"])
                    amount = _lognorm(rng, *seg["merch"])
                    method = rng.choices(["qr", "contact", "typed", "pasted", "link"], [0.68, 0.2, 0.07, 0.02, 0.03])[0]
                elif r < 0.63:
                    payee = rng.choices(w.merchants, mw)[0]["vpa"]
                    amount = _lognorm(rng, *seg["merch"])
                    method = rng.choices(["qr", "typed", "link", "pasted"], [0.7, 0.1, 0.15, 0.05])[0]
                elif r < 0.90 and u["peers"]:
                    payee = rng.choice(u["peers"])
                    amount = _lognorm(rng, *seg["peer"])
                    method = rng.choices(["contact", "typed", "pasted", "qr", "link"], [0.74, 0.1, 0.1, 0.04, 0.02])[0]
                elif r < 0.964:
                    payee = rng.choice(regular)["vpa"]                       # someone new
                    amount = _lognorm(rng, seg["peer"][0], 1.0)
                    method = rng.choices(["pasted", "typed", "qr", "contact", "link"], [0.35, 0.3, 0.2, 0.05, 0.1])[0]
                elif r < 0.968:
                    payee = rng.choice(regular)["vpa"]                       # big first-time: deposit, tuition, medical
                    amount = float(min(u["balance"] * 0.7, _lognorm(rng, 60000, 0.7, lo=15000)))
                    method = rng.choices(["pasted", "typed", "link", "qr"], [0.4, 0.4, 0.1, 0.1])[0]
                    kind = "big"
                elif r < 0.978:
                    # Honest payments that share several scam signals at once: paying a seller or a tradesman
                    # found online while they are on the phone, or sending emergency money to family.
                    look = rng.choice(["seller", "seller", "family", "kiosk"])
                    young = [x for x in new_users if x["first_seen"] < ts and x is not u]
                    target = rng.choice(young) if (young and look == "seller") else rng.choice(regular)
                    if look == "kiosk" and kiosks:
                        target = rng.choice(kiosks)
                    payee = target["vpa"]
                    amount = float(min(u["balance"] * 0.6, _lognorm(rng, seg["peer"][0] * rng.uniform(3, 12), 0.5)))
                    method = "qr" if look == "kiosk" else rng.choices(["pasted", "typed", "link"], [0.6, 0.3, 0.1])[0]
                    kind = "lookalike:" + look
                else:
                    payee = rng.choice(u["favourites"] or [w.merchants[0]["vpa"]])
                    amount = _lognorm(rng, *seg["merch"])
                    method = "qr"
                if payee == u["vpa"]:
                    continue
                v = w.vpas[payee]
                if v["first_seen"] > ts:
                    continue
                if method in ("qr", "link"):
                    claimed = v["shop_name"]
                elif method in ("typed", "pasted") and rng.random() < 0.15:
                    claimed = v["registered_name"].split()[0]
                note = rng.choice(BENIGN_NOTES) if rng.random() < 0.12 else ""
                sess = _session(rng, method, kind)
                if kind and kind.startswith("lookalike"):
                    sess["app_switch_count"] = rng.randint(1, 4)
                    sess["time_on_pay_screen_s"] = float(rng.randint(30, 200))
                    note = rng.choice({"lookalike:seller": ["advance", "booking", "token amount", "advance for booking",
                                                            "payment for sofa", ""],
                                       "lookalike:family": ["hospital bill", "emergency", "urgent help", ""],
                                       "lookalike:kiosk": ["bill payment", "recharge", ""]}[kind])
                    start = ts - rng.uniform(60, 1200)
                    ev += _call(start, ts + rng.uniform(20, 300) if rng.random() < 0.6 else ts - rng.uniform(5, 200), u,
                                f"9{rng.randint(200000000, 999999999)}", kind == "lookalike:family", 0)
                ev.append(_pay(ids, ts, u, payee, amount, sess, note=note, claimed=claimed))
                if kind and kind.startswith("lookalike"):
                    continue
                c = rng.random()
                if c < 0.04:        # on or just after a call with someone they know
                    start = ts - rng.uniform(60, 900)
                    ev += _call(start, ts + rng.uniform(30, 600) if rng.random() < 0.5 else ts - rng.uniform(1, 50), u,
                                f"9{rng.randint(200000000, 999999999)}", True, 0)
                elif c < 0.05:      # an unknown but harmless caller: delivery, cab
                    start = ts - rng.uniform(60, 600)
                    ev += _call(start, ts - rng.uniform(1, 50), u, f"9{rng.randint(200000000, 999999999)}", False, 0)
                elif c < 0.0515:    # an unknown caller wrongly rated as risky
                    start = ts - rng.uniform(60, 600)
                    ev += _call(start, ts - rng.uniform(1, 50), u, f"9{rng.randint(200000000, 999999999)}", False,
                                rng.choice([1, 1, 2]))
        if u["landlord"] and u["landlord"] != u["vpa"]:
            for back in (62, 32, 2):
                ts = cfg.end_ts - back * DAY + rng.uniform(-0.3, 0.3) * DAY
                if ts > cfg.start_ts:
                    ev.append(_pay(ids, ts, u, u["landlord"], u["rent"], _session(rng, "contact"), note="rent"))
        if rng.random() < 0.004 and not u["is_demo"]:
            # received a scam message, checked it, and did not pay the scammer
            c = rng.choice(w.campaigns)
            ev.append(_msg(cfg.start_ts + rng.uniform(4, cfg.days - 1) * DAY, u,
                           c["family"] if c["family"] in MESSAGES else "FAKE_REFUND", rng.choice(c["mules"])["vpa"], rng))
    for u in regular:
        if not u.get("collector"):
            continue
        for _ in range(rng.randint(3, 8)):
            t0 = cfg.start_ts + rng.uniform(2, cfg.days - 1) * DAY
            t0 = t0 - (t0 % DAY) + rng.uniform(5, 16) * HOUR
            if t0 < u["first_seen"] or t0 > cfg.end_ts - 2 * HOUR:
                continue
            share = float(rng.choice([150, 200, 250, 400, 500, 800, 1200]))
            friends = [f for f in rng.sample(regular, min(len(regular), rng.randint(3, 6)))
                       if f is not u and f["first_seen"] < t0]
            last = t0
            for fr in friends:
                last = t0 + rng.uniform(0, 20) * 60
                ev.append(_pay(ids, last, fr, u["vpa"], share,
                               _session(rng, rng.choice(["contact", "contact", "pasted", "qr"])), note="trip share"))
            if friends:
                target = rng.choice(u["favourites"] or [w.merchants[0]["vpa"]])
                if w.vpas[target]["first_seen"] < t0:
                    ev.append(_pay(ids, t0 + rng.uniform(22, 45) * 60, u, target, share * len(friends) * rng.uniform(0.9, 1.05),
                                   _session(rng, "qr"), claimed=w.vpas[target]["shop_name"]))
    # New sellers: people who start selling online. From the payee side they look like a fresh mule account
    # (a young UPI ID, many first-time payers, pasted IDs, "advance" notes) but nothing is forwarded on,
    # nobody reports them, and the callers are not flagged.
    sellers = rng.sample(new_users, min(len(new_users), 10))
    for s_user in sellers:
        s_user["seller"] = True
        t_open = s_user["first_seen"] + rng.uniform(0.5, 3) * DAY
        for buyer in rng.sample(regular, min(len(regular), rng.randint(20, 60))):
            ts = t_open + rng.uniform(0, 6) * DAY
            ts = ts - (ts % DAY) + rng.uniform(4, 16) * HOUR
            if buyer is s_user or buyer["is_demo"] or ts >= cfg.end_ts - HOUR or ts < buyer["first_seen"]:
                continue
            method = rng.choices(["pasted", "link", "qr", "typed"], [0.4, 0.3, 0.2, 0.1])[0]
            sess = _session(rng, method)
            sess["time_on_pay_screen_s"] = float(rng.randint(15, 120))
            note = rng.choice(["advance", "booking", "token amount", "payment for sofa", "order 4521", "", "", ""])
            if rng.random() < 0.2:
                ev += _call(ts - rng.uniform(60, 900), ts - rng.uniform(5, 60), buyer,
                            f"9{rng.randint(200000000, 999999999)}", False, 0)
            ev.append(_pay(ids, ts, buyer, s_user["vpa"], _lognorm(rng, 1500, 0.8, lo=100), sess, note=note,
                           claimed=s_user["name"].split()[0] if rng.random() < 0.4 else None))
    for m, launch_ts in w.demo["launches"]:       # genuine fan-in burst at a new shop
        for u in rng.sample(regular, min(len(regular), rng.randint(60, 150))):
            if u["is_demo"]:
                continue
            ts = launch_ts + rng.uniform(0, 6) * HOUR
            ev.append(_pay(ids, ts, u, m["vpa"], _lognorm(rng, 250, 0.6), _session(rng, "qr"), claimed=m["shop_name"]))
    return ev


def _campaign_events(w: World, rng: random.Random, ids: _Ids) -> list[dict]:
    cfg = w.cfg
    ev: list[dict] = []
    reports_per_ring: dict[str, int] = {}

    def soften(p: dict, u: dict) -> None:
        """A quarter of scams leave little trace: the victim types the UPI ID, the amount is ordinary,
        nothing scam-like is written, and the call may not have been visible."""
        if p["variant"] != "weak":
            return
        p["session"] = {"vpa_entry_method": rng.choice(["typed", "typed", "pasted", "qr"]),
                        "app_switch_count": rng.choice([0, 0, 1, 1, 2]),
                        "time_on_pay_screen_s": float(rng.randint(12, 70)), "amount_edit_count": 0}
        p["note"] = ""
        p["claimed"] = None
        typical = SEGMENTS[u["segment"]]["peer"][0]
        p["amount"] = float(round(min(p["amount"], typical * rng.uniform(1.0, 5.0))))

    def after_payment(c: dict, u: dict, mule: dict, ts: float, pay: dict) -> None:
        soften(pay, u)
        amount, txn_id = pay["amount"], pay["txn_id"]
        if rng.random() < 0.8:      # the mule forwards the money on
            fts = ts + rng.uniform(5, 25) * 60
            ev.append(_pay(ids, fts, mule, c["layer2"]["vpa"], amount * rng.uniform(0.9, 1.0),
                           {"vpa_entry_method": "typed", "app_switch_count": 0, "time_on_pay_screen_s": 9.0,
                            "amount_edit_count": 0}, campaign=c["id"], exclude=True))
        if rng.random() < 0.55:     # the victim reports
            rts = ts + rng.uniform(1, 72) * HOUR
            ev.append(dict(t="report", ts=rts, order=4, tenant=u["tenant"], vpa=mule["vpa"], kind="confirmed",
                           weight=5.0, ref=txn_id, reporter=u))
            n = reports_per_ring.get(c["id"], 0) + 1
            reports_per_ring[c["id"]] = n
            if n == 3:              # investigation confirms the next-layer account, at both banks
                lts = rts + rng.uniform(6, 48) * HOUR
                for tenant in ("bank_a", "bank_b"):
                    ev.append(dict(t="report", ts=lts, order=4, tenant=tenant, vpa=c["layer2"]["vpa"],
                                   kind="confirmed", weight=5.0, ref=f"inv-{c['id']}-{tenant}", reporter=None))

    for c in w.campaigns:
        fam, unseen = c["family"], c["variant"] == "unseen"
        base = cfg.start_ts + c["start_day"] * DAY
        burst = fam == "MULE_TRANSFER" and not unseen
        span = rng.uniform(3, 10) * HOUR if burst else rng.uniform(2, 6) * DAY
        for u in c["victims"]:
            t0 = base + rng.uniform(0, span)
            if not burst:           # mostly daytime
                t0 = t0 - (t0 % DAY) + rng.uniform(3.5, 16) * HOUR
            if t0 >= cfg.end_ts - 2 * HOUR or t0 <= cfg.start_ts:
                continue
            if rng.random() < 0.10:
                continue            # hung up; nothing happened
            mule = rng.choice(c["mules"])
            caller = rng.choice(c["callers"])
            risk = c["caller_risk"]
            weak = (not unseen) and rng.random() < 0.25
            kw = dict(fraud=True, family=fam, campaign=c["id"], variant="weak" if weak else c["variant"])
            bal = max(2000.0, u["balance"])

            if fam == "DIGITAL_ARREST":
                if not unseen and rng.random() < 0.2:
                    m = _msg(t0 - rng.uniform(10, 120) * 60, u, fam, mule["vpa"], rng, flagged=rng.random() < 0.3)
                    if m["flagged"]:
                        ev.append(m)
                pay_ts = t0 + rng.uniform(20, 120) * 60
                if not unseen:      # unseen variant: the call is on an internet-calling app and leaves no signal
                    ev += _call(t0, pay_ts + rng.uniform(5, 40) * 60, u, caller, False, risk)
                amount = (rng.uniform(0.6, 0.95) * bal if u["segment"] == "elderly"
                          else min(0.95 * bal, _lognorm(rng, 120000, 0.7, lo=20000)))
                sess = ({"vpa_entry_method": "typed", "app_switch_count": rng.randint(3, 7),
                         "time_on_pay_screen_s": float(rng.randint(60, 400)), "amount_edit_count": 1} if unseen else
                        _scam_session(rng, {"pasted": 0.65, "typed": 0.3, "link": 0.05}, (2, 7), (60, 400)))
                claimed = None if unseen or rng.random() < 0.4 else rng.choice(CLAIMED[fam])
                note = "" if unseen or rng.random() < 0.5 else rng.choice(NOTES[fam])
                p = _pay(ids, pay_ts, u, mule["vpa"], amount, sess, note=note, claimed=claimed, **kw)
                ev.append(p)
                after_payment(c, u, mule, pay_ts, p)
                if rng.random() < 0.3:
                    ts2 = pay_ts + rng.uniform(8, 35) * 60
                    m2 = rng.choice(c["mules"])
                    a2 = min(0.9 * bal, amount * rng.uniform(0.3, 0.9))
                    p2 = _pay(ids, ts2, u, m2["vpa"], a2, dict(sess), note=note, claimed=claimed, **kw)
                    ev.append(p2)
                    after_payment(c, u, m2, ts2, p2)

            elif fam == "FAKE_REFUND":
                checked = False
                if rng.random() < 0.45:
                    checked = rng.random() < 0.5
                    if checked:
                        ev.append(_msg(t0 - rng.uniform(2, 40) * 60, u, fam, mule["vpa"], rng))
                        if rng.random() < 0.7:
                            continue        # recognised the scam and stopped
                dur = rng.uniform(3, 15) * 60
                during = rng.random() < 0.5
                pay_ts = t0 + (rng.uniform(0.4, 0.95) * dur if during else dur + rng.uniform(2, 20) * 60)
                if not unseen:
                    ev += _call(t0, t0 + dur, u, caller, False, risk)
                amount = min(0.8 * bal, _lognorm(rng, 4000, 1.0, lo=10))
                sess = ({"vpa_entry_method": "qr", "app_switch_count": rng.randint(0, 2),
                         "time_on_pay_screen_s": float(rng.randint(20, 120)), "amount_edit_count": 0} if unseen else
                        _scam_session(rng, {"link": 0.45, "qr": 0.35, "pasted": 0.15, "typed": 0.05}, (1, 4), (30, 200)))
                note = "" if unseen or rng.random() < 0.3 else rng.choice(NOTES[fam])
                p = _pay(ids, pay_ts, u, mule["vpa"], amount, sess, note=note, claimed=rng.choice(CLAIMED[fam]), **kw)
                ev.append(p)
                after_payment(c, u, mule, pay_ts, p)

            elif fam == "MULE_TRANSFER":
                pay_ts = t0
                if rng.random() < 0.1:
                    ev += _call(t0 - rng.uniform(120, 900), t0 - rng.uniform(1, 60), u, caller, False, min(risk, 1))
                amount = min(0.8 * bal, _lognorm(rng, 6000, 0.8, lo=200))
                sess = _scam_session(rng, {"pasted": 0.45, "typed": 0.25, "link": 0.2, "qr": 0.1}, (0, 3), (20, 120))
                claimed = f"{rng.choice(FIRST)} {rng.choice(LAST)}" if rng.random() < 0.5 else None
                note = rng.choice(NOTES[fam]) if rng.random() < 0.6 else ""
                p = _pay(ids, pay_ts, u, mule["vpa"], amount, sess, note=note, claimed=claimed, **kw)
                ev.append(p)
                after_payment(c, u, mule, pay_ts, p)

            elif fam == "IMPERSONATION":
                if not unseen and rng.random() < 0.65 and rng.random() < 0.25:
                    ev.append(_msg(t0 - rng.uniform(2, 60) * 60, u, fam, mule["vpa"], rng))
                    if rng.random() < 0.6:
                        continue
                if rng.random() < 0.15:
                    ev += _call(t0 - rng.uniform(120, 600), t0 - rng.uniform(1, 60), u, caller, False, 0)
                med = 80000 if u["segment"] == "small_business" else 35000
                amount = min(0.9 * bal, _lognorm(rng, 20000 if unseen else med, 0.8, lo=2000))
                sess = ({"vpa_entry_method": "typed", "app_switch_count": rng.randint(0, 2),
                         "time_on_pay_screen_s": float(rng.randint(20, 120)), "amount_edit_count": 0} if unseen else
                        _scam_session(rng, {"pasted": 0.7, "typed": 0.25, "link": 0.05}, (1, 5), (30, 240)))
                note = rng.choice(NOTES[fam]) if (not unseen and rng.random() < 0.4) else ""
                p = _pay(ids, t0, u, mule["vpa"], amount, sess, note=note, claimed=rng.choice(CLAIMED[fam]), **kw)
                ev.append(p)
                after_payment(c, u, mule, t0, p)

            elif fam == "KYC_BILL_THREAT":
                if not unseen and rng.random() < 0.8 and rng.random() < 0.4:
                    ev.append(_msg(t0 - rng.uniform(2, 90) * 60, u, fam, mule["vpa"], rng))
                    if rng.random() < 0.65:
                        continue
                if unseen or rng.random() < 0.45:
                    dur = rng.uniform(2, 10) * 60
                    ev += _call(t0 - dur - rng.uniform(0, 300),
                                t0 - rng.uniform(1, 200) if rng.random() < 0.6 else t0 + rng.uniform(60, 900),
                                u, caller, False, risk)
                amount = (float(rng.randint(10, 999)) if rng.random() < 0.6 else min(0.8 * bal, _lognorm(rng, 8000, 0.8)))
                sess = ({"vpa_entry_method": "typed", "app_switch_count": rng.randint(1, 3),
                         "time_on_pay_screen_s": float(rng.randint(20, 150)), "amount_edit_count": 0} if unseen else
                        _scam_session(rng, {"link": 0.6, "qr": 0.1, "pasted": 0.2, "typed": 0.1}, (1, 4), (25, 180)))
                note = rng.choice(NOTES[fam]) if rng.random() < 0.6 else ""
                p = _pay(ids, t0, u, mule["vpa"], amount, sess, note=note, claimed=rng.choice(CLAIMED[fam]), **kw)
                ev.append(p)
                after_payment(c, u, mule, t0, p)

            else:  # INVESTMENT_TASK
                if rng.random() < 0.55 and rng.random() < 0.2:
                    ev.append(_msg(t0 - rng.uniform(5, 200) * 60, u, fam, mule["vpa"], rng))
                    if rng.random() < 0.5:
                        continue
                steps = 1 if unseen else rng.randint(2, 5)
                amount = _lognorm(rng, 30000, 0.6, lo=5000) if unseen else _lognorm(rng, 800, 0.7, lo=100)
                ts = t0
                target = mule
                for s in range(steps):
                    amount = min(0.85 * bal, amount)
                    sess = _scam_session(rng, {"pasted": 0.5, "link": 0.3, "typed": 0.15, "qr": 0.05}, (0, 4), (15, 150))
                    note = "" if unseen or rng.random() < 0.35 else rng.choice(NOTES[fam])
                    claimed = rng.choice(CLAIMED[fam]) if rng.random() < 0.4 else None
                    if ts >= cfg.end_ts - HOUR:
                        break
                    p = _pay(ids, ts, u, target["vpa"], amount, sess, note=note, claimed=claimed, **kw)
                    ev.append(p)
                    after_payment(c, u, target, ts, p)
                    amount *= rng.uniform(2, 4)
                    ts += rng.uniform(4, 30) * 60 if rng.random() < 0.3 else rng.uniform(6, 40) * HOUR
                    if rng.random() < 0.3:
                        target = rng.choice(c["mules"])
    # for low-signal scams, six in ten of the surrounding calls and message checks were never captured
    drop: set[str] = set()
    weak_pays = [e for e in ev if e["t"] == "pay" and e.get("variant") == "weak" and not e["exclude"]]
    hidden = {(e["user"]["user_id"], e["ts"]) for e in weak_pays if rng.random() < 0.6}
    for e in ev:
        if e["t"] in ("call", "msg"):
            uid_ = e["user"]["user_id"]
            if any(u2 == uid_ and -4 * HOUR <= e["ts"] - t2 <= HOUR for u2, t2 in hidden):
                drop.add(e.get("call_id") or e.get("check_id"))
    ev = [e for e in ev if (e.get("call_id") or e.get("check_id")) not in drop]
    return ev


def _demo_events(w: World, rng: random.Random, ids: _Ids) -> list[dict]:
    """Fixed state for the demo payees (docs/05, section 5.3)."""
    cfg = w.cfg
    end = cfg.end_ts
    ev: list[dict] = []
    payers = [u for u in w.users if not u["is_mule"] and not u["is_demo"]]
    quiet = {"vpa_entry_method": "pasted", "app_switch_count": 2, "time_on_pay_screen_s": 70.0, "amount_edit_count": 0}
    fwd = {"vpa_entry_method": "typed", "app_switch_count": 0, "time_on_pay_screen_s": 8.0, "amount_edit_count": 0}
    kw = dict(fraud=True, exclude=True, variant="demo")
    l2 = w.by_vpa["layer2.acct@bankb"]
    for back in (10, 6):
        for tenant in ("bank_a", "bank_b"):
            ev.append(dict(t="report", ts=end - back * DAY, order=4, tenant=tenant, vpa=l2["vpa"], kind="confirmed",
                           weight=5.0, ref=f"demo-l2-{back}-{tenant}", reporter=None))
    cc = w.by_vpa["cyber.cell.verify@bankb"]
    for k in range(6):
        u = rng.choice(payers)
        ts = end - rng.uniform(4, 44) * HOUR
        amt = float(rng.randint(40000, 220000))
        ev.append(_pay(ids, ts, u, cc["vpa"], amt, dict(quiet), family="DIGITAL_ARREST", campaign="demo-da", **kw))
        ev.append(_pay(ids, ts + rng.uniform(4, 12) * 60, cc, l2["vpa"], amt * 0.96, dict(fwd), campaign="demo-da",
                       exclude=True))
    rd = w.by_vpa["refund.desk7@bankb"]
    bank_a_payers = [u for u in payers if u["tenant"] == "bank_a"]
    for k in range(5):
        u = rng.choice(bank_a_payers)
        ts = end - rng.uniform(5, 60) * HOUR
        ev.append(_pay(ids, ts, u, rd["vpa"], float(rng.randint(900, 9000)), dict(quiet), family="FAKE_REFUND",
                       campaign="demo-rf", claimed="SBI Refund Desk", note="refund processing fee", **kw))
    ev.append(dict(t="report", ts=end - 20 * HOUR, order=4, tenant="bank_a", vpa=rd["vpa"], kind="report", weight=1.0,
                   ref="demo-rd-report", reporter=None))
    for k in range(2):
        ev.append(dict(t="report", ts=end - (10 + k) * HOUR, order=4, tenant="bank_a", vpa=rd["vpa"], kind="nearmiss",
                       weight=0.3, ref=f"demo-rd-nm{k}", reporter=None))
    qr = w.by_vpa["quick.returns@banka"]
    for k in range(4):
        u = rng.choice(payers)
        ev.append(_pay(ids, end - rng.uniform(1, 9) * DAY, u, qr["vpa"], float(rng.randint(500, 6000)), dict(quiet),
                       family="INVESTMENT_TASK", campaign="demo-inv", note="task deposit", **kw))
    for k in range(2):
        u = rng.choice(payers)
        ev.append(_pay(ids, end - rng.uniform(0.5, 4) * DAY, u, "newcafe@banka", float(rng.randint(120, 600)),
                       _session(rng, "qr"), claimed="Third Wave Cafe"))
    return ev


def generate_events(w: World) -> list[dict]:
    rng = random.Random(w.cfg.seed + 1)
    nrng = np.random.default_rng(w.cfg.seed + 2)
    ids = _Ids()
    ev = _legit_events(w, rng, nrng, ids) + _campaign_events(w, rng, ids) + _demo_events(w, rng, ids)
    ev = [e for e in ev if w.cfg.start_ts <= e["ts"] <= w.cfg.end_ts]
    ev.sort(key=lambda e: (e["ts"], e["order"]))
    return ev
