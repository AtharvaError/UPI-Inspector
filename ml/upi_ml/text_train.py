"""Train the lightweight text classifier (TF-IDF + logistic regression) from generated templates.

Training texts are generated from templates with slot filling and surface variation
(English, Hinglish, Hindi; casing, typos, missing punctuation). The 40-message test set in
infra/seed/message_testset.json is hand-written and shares no template with the training data.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline

from app.config import ROOT
from app.core.textmodel import TextModel, normalise

BANKS = ["SBI", "HDFC Bank", "ICICI Bank", "Axis Bank", "PNB", "Kotak Bank", "Bank of Baroda", "Canara Bank", "Union Bank"]
BRANDS = ["Amazon", "Flipkart", "Paytm", "PhonePe", "Swiggy", "Zomato", "Myntra", "Meesho", "IRCTC", "BigBasket"]
BOARDS = ["BSES", "MSEDCL", "TNEB", "BESCOM", "Adani Electricity", "Tata Power", "UPPCL"]
AGENCIES = ["Mumbai Police", "Delhi Cyber Cell", "CBI", "Customs Department", "Narcotics Control Bureau", "TRAI",
            "Enforcement Directorate", "Cyber Crime Branch", "Income Tax Department"]
COURIERS = ["FedEx", "DHL", "Blue Dart", "DTDC", "India Post"]
NAMES = ["Rahul", "Priya", "Amit", "Sneha", "Vikram", "Anjali", "Rohit", "Neha", "Suresh", "Kavita", "Mr. Mehta",
         "Mrs. Iyer", "Ankit", "Pooja", "Manoj", "Deepa"]
HANDLES = ["okaxis", "ybl", "paytm", "oksbi", "ibl", "upi", "axl", "okhdfcbank", "apl"]
SHORT = ["bit.ly", "tinyurl.com", "cutt.ly", "rb.gy", "is.gd"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

T: dict[str, list[str]] = {
    "refund_bait": [
        "Dear customer, your {brand} refund of Rs {amt} is pending. Pay Rs {fee} processing fee to {upi} to receive it.",
        "Your {bank} refund of Rs.{amt} has been approved. To claim, scan the QR code and enter your UPI PIN. Helpline {phone}",
        "Cashback of ₹{amt} will be credited. Complete verification by paying ₹{fee} at {link}",
        "{brand} customer care: order cancelled, refund Rs {amt} initiated. Click {link} and approve the request to get your refund.",
        "Aapka refund Rs {amt} pending hai. Refund pane ke liye {upi} par Rs {fee} bhejein. Turant karein.",
        "आपका ₹{amt} का रिफंड लंबित है। रिफंड पाने के लिए {upi} पर ₹{fee} प्रोसेसिंग शुल्क भेजें।",
        "Sir I sent Rs {amt} to your number by mistake, please return it to {upi} urgently, it is for hospital.",
        "Income tax refund of Rs {amt} has been approved. Submit your request at {link} within {hrs} hours to receive it.",
        "Refund alert: Rs {amt} could not be credited. Verify your UPI by sending Rs {fee} to {upi}. Amount will be returned with refund.",
        "Your double payment of Rs {amt} to {brand} will be refunded today. Approve the collect request and enter PIN to receive.",
        "{bank} reward points worth Rs {amt} expiring today. Redeem as cashback now: {link}",
        "Refund ke liye apna UPI PIN daalein aur Rs {fee} ka token payment karein {upi} par. Paisa wapas mil jayega.",
        "Galti se aapke account mein Rs {amt} chale gaye hain, please {upi} par wapas bhej dijiye, bahut zaroori hai.",
        "Bhaiya maine galat number par {amt} rupaye bhej diye, wapas kar do {upi} pe, bacche ki fees ke paise hain.",
        "I transferred {amt} to you by mistake while paying my landlord. Kindly send it back to {upi} now.",
        "refund processing fee", "refund charges", "cashback claim fee", "refund verification", "refund token amount",
        "return of excess payment", "refund activation", "cashback processing",
    ],
    "authority_threat": [
        "This is {agency}. A parcel in your name with illegal items was seized. You are under digital arrest. Do not disconnect. Transfer Rs {amt} to the verification account {upi}.",
        "{agency} notice: your Aadhaar is linked to money laundering case no {case}. To avoid arrest transfer your funds to RBI safe account {upi} immediately. Do not tell anyone.",
        "Your mobile number will be blocked by TRAI in 2 hours for illegal activity. Call {phone} now. Legal action will follow.",
        "Customs: your {courier} parcel contains drugs and fake passports. Pay Rs {amt} clearance and verification to {upi} or an FIR will be registered.",
        "Aapke naam par arrest warrant jaari hua hai. Cyber cell se baat karein {phone}. Kisi ko mat batayein. Turant Rs {amt} jama karein.",
        "आपके नाम पर गिरफ्तारी वारंट जारी हुआ है। पुलिस जांच के लिए तुरंत ₹{amt} सत्यापन खाते {upi} में जमा करें। किसी को मत बताएं।",
        "Court summons: you failed to appear in case {case}. Pay penalty Rs {amt} to {upi} within {hrs} hours or police will arrest you.",
        "{agency}: your bank accounts are under investigation. Stay on video call. Move your balance to the government safe account for verification.",
        "Notice from {agency}. Your SIM was used in a fraud of Rs {amt}. You must cooperate. Do not inform family. Officer will call from {phone}.",
        "Aap digital arrest mein hain. Call mat kaatiye. Jaanch ke liye apna paisa RBI ke safe account {upi} mein transfer karein.",
        "case verification", "verification deposit", "safe account transfer", "court fee", "bail amount",
        "police verification amount", "customs clearance fee", "rbi safe account", "case settlement", "cbi verification",
    ],
    "kyc_bill_threat": [
        "Dear customer your {bank} account will be blocked today. Update KYC immediately at {link}",
        "Your electricity connection will be disconnected tonight at 9:30 pm because your last month bill was not updated. Call {phone} immediately. - {board}",
        "{bank}: Your KYC has expired. Pay Rs {fee} KYC update charge to {upi} within {hrs} hours to avoid account suspension.",
        "Your PAN is not linked. Your {bank} netbanking will be suspended today. Update now at {link}",
        "Aapka bijli connection aaj raat kat jayega. Bill update karne ke liye {phone} par call karein ya {upi} par Rs {fee} bhejein",
        "आपका {bank} खाता आज बंद हो जाएगा। केवाईसी अपडेट करने के लिए तुरंत {link} पर जाएं।",
        "Your SIM card will be deactivated in 24 hrs. Complete e-KYC by paying Rs {fee} to {upi}",
        "FASTag KYC pending. Your FASTag will be blacklisted. Update now {link}",
        "{board} final notice: power supply will be cut today. Pay pending Rs {fee} to officer UPI {upi} and send screenshot.",
        "Your {bank} debit card is blocked due to incomplete KYC. Reactivate by calling {phone} and sharing the OTP.",
        "Gas connection will be suspended. Update your KYC with Rs {fee} verification payment at {link}",
        "Aapka khata band ho jayega. KYC update ke liye abhi Rs {fee} {upi} par bhejein.",
        "kyc update charge", "kyc fee", "electricity bill update", "sim kyc charge", "account reactivation fee",
        "pan link fee", "kyc verification payment", "bill update charge",
    ],
    "investment_task": [
        "Earn Rs {amt} daily from home! Simple tasks: like YouTube videos. Join Telegram {link}. Deposit Rs {fee} to start.",
        "Guaranteed 30% returns in 7 days. Invest Rs {amt} in our trading plan. Pay to {upi}. Limited seats.",
        "Task 3 completed. To withdraw your earnings of Rs {amt}, deposit Rs {fee} as prepaid task amount to {upi}",
        "Part time job offer: daily income Rs {amt}. No experience needed. WhatsApp {phone}",
        "Double your money in 15 days with our crypto VIP plan. Minimum investment Rs {amt}. {link}",
        "Ghar baithe roz Rs {amt} kamayein. Sirf Rs {fee} registration. Abhi join karein {link}",
        "घर बैठे रोज़ ₹{amt} कमाएं। गारंटीड मुनाफा। निवेश करने के लिए {upi} पर ₹{fee} भेजें।",
        "Your trading profit of Rs {amt} is ready. Pay 10% tax Rs {fee} to release the withdrawal.",
        "Hotel review job: earn Rs {amt} per review. Recharge your merchant task wallet with Rs {fee} at {upi}.",
        "IPO allotment guaranteed through our institutional account. Transfer Rs {amt} to {upi} today for 3x profit.",
        "Stock market expert group: 500% profit this month. Join VIP by paying Rs {fee}. {link}",
        "SEBI registered advisor: sure shot calls, Rs {amt} ko double karein 30 din mein. Fees sirf Rs {fee}.",
        "Intraday tips group: daily jackpot calls with 100% accuracy. Joining fee Rs {fee}. WhatsApp {phone}",
        "Online work, earn {amt} per day by rating hotels and products. Reply YES to get your first task and bonus.",
        "task deposit", "investment plan a", "vip plan recharge", "trading deposit", "prepaid task", "withdrawal tax",
        "registration fee job", "task wallet recharge", "ipo application amount",
    ],
    "impersonation_request": [
        "Hi, this is {name} (new number). I am in a meeting and can't talk. Need you to pay a vendor urgently Rs {amt} to {upi}. Will explain later.",
        "Mom my phone broke, this is my new number. Please send Rs {amt} to {upi} urgently for the hospital.",
        "Hello dear, I am stuck abroad and lost my wallet. Please transfer Rs {amt} to my friend's UPI {upi}. Will return tomorrow.",
        "This is your MD. Process a confidential payment of Rs {amt} to {upi} immediately. Don't call, I'm in a conference.",
        "Bhai main {name} bol raha hoon, naya number hai. Urgent Rs {amt} chahiye, is UPI par bhej do {upi}. Kal lauta dunga.",
        "पापा, मेरा फोन खराब हो गया है, यह नया नंबर है। अस्पताल के लिए तुरंत ₹{amt} {upi} पर भेज दो।",
        "Sir, kindly release the vendor payment today itself. Our account details have changed, pay to {upi}. Regards {name}, Accounts",
        "Uncle this is {name}'s friend. He met with an accident. Send Rs {amt} to {upi} for the operation right now.",
        "I'm your boss, on a flight. Buy and send gift cards worth Rs {amt} or pay to {upi}. Keep it between us.",
        "Hey it's me, saved a new number. Can you quickly GPay Rs {amt} to {upi}? My UPI is not working. Urgent.",
        "urgent transfer for boss", "vendor payment urgent new account", "emergency hospital money", "urgent help friend",
        "confidential payment", "boss urgent payment", "new account vendor",
    ],
    "prize": [
        "Congratulations! You have won Rs {amt} in the KBC lottery. To claim pay Rs {fee} registration to {upi}. Call {phone}",
        "Your mobile number won an iPhone in our lucky draw. Pay delivery charge Rs {fee} at {link}",
        "You are selected for a Rs {amt} prize in the {brand} anniversary offer. Claim: {link}",
        "Badhai ho! Aapne Rs {amt} ki lottery jeeti hai. Processing fee Rs {fee} bhejein {upi}",
        "बधाई हो! आपने ₹{amt} का इनाम जीता है। इनाम पाने के लिए ₹{fee} शुल्क {upi} पर भेजें।",
        "Lucky winner! Your number is selected for a car. Pay GST Rs {fee} to receive the prize. WhatsApp {phone}",
        "lottery fee", "prize claim charge", "lucky draw delivery", "lottery gst", "prize registration",
    ],
    "benign": [
        "Your OTP for {bank} transaction of Rs {amt} is {otp}. Do not share it with anyone.",
        "Rs {amt} debited from A/c XX{last4} on {date} to {name}. Avl bal Rs {bal}. Not you? Call 1800{toll}",
        "Your {brand} order #{order} has been shipped and will arrive tomorrow.",
        "{board}: Your electricity bill of Rs {amt} for {month} is generated. Due date {date}. Pay through the official app.",
        "Hi, can you send me Rs {amt} for last night's dinner? My UPI is {upi}",
        "Reminder: your {bank} credit card bill of Rs {amt} is due on {date}.",
        "Dear customer, your refund of Rs {amt} for order #{order} has been credited to your account.",
        "Your appointment with Dr. {name} is confirmed for {date} at 5 pm.",
        "Salary of Rs {amt} credited to A/c XX{last4}.",
        "Kal shaam ko milte hain. Rent ka Rs {amt} bhej dena please.",
        "आपका ऑर्डर कल तक पहुंच जाएगा। धन्यवाद।",
        "Your cab is arriving in 3 mins. OTP {otp}.",
        "Meeting moved to 4 pm. Please bring the invoices.",
        "Thanks for the payment of Rs {amt}. Receipt attached.",
        "Happy birthday! Party at my place on Saturday.",
        "Your KYC is successfully updated. Thank you for banking with {bank}.",
        "Bhai kal ke movie tickets ke Rs {amt} bhej dena {upi} pe",
        "Mutual fund SIP of Rs {amt} processed successfully for folio {order}.",
        "Police verification for your passport is scheduled on {date}. Please keep your documents ready.",
        "Your FASTag has been recharged with Rs {amt}.",
        "{brand}: your order #{order} was delivered. Rate your experience.",
        "Rs {amt} credited to A/c XX{last4} by UPI from {name}. Ref {order}.",
        "Your {bank} statement for {month} is ready. View it in the app.",
        "Can you pick up milk and bread on the way home?",
        "{name} ka birthday gift ke liye sab Rs {amt} de rahe hain, mujhe {upi} par bhej dena",
        "मम्मी, आज मैं देर से आऊंगा। खाना रख देना।",
        "Your train ticket PNR {order} is confirmed. Coach B2.",
        "Thank you for shopping at FreshMart. You saved Rs {amt} today.",
        "School fees of Rs {amt} for {month} quarter are due on {date}. Pay at the school office or portal.",
        "Recharge of Rs {amt} successful for your mobile number. Validity 28 days.",
        "Dear investor, your demat account statement for {month} is now available.",
        "Hi {name}, sending you the Rs {amt} I owe you for the trip today.",
        "Your electricity bill payment of Rs {amt} was successful. Thank you. - {board}",
        "Rent received for {month}, thank you. - {name}",
        # ordinary uses of words that scams also use: update, verify, refund, account, blocked
        "Please update the sheet before end of day.",
        "Can you verify the numbers in the report and update me by evening?",
        "Team lunch on Friday, please confirm by today.",
        "Kindly update your timesheet for last week.",
        "Sprint review moved to Thursday, please update your tickets before the call.",
        "Reminder to submit your reimbursement claims by {date}.",
        "Your return for order #{order} was picked up. The refund will reflect in 5-7 business days.",
        "Refund of Rs {amt} has been initiated to your original payment method. No action is needed from you.",
        "We have processed your refund of Rs {amt}. It will be credited automatically.",
        "Your cheque book request has been processed and will be delivered in a week.",
        "{bank}: Never share your PIN or OTP. The bank will never call you for KYC updates.",
        "Your loan EMI of Rs {amt} was received on {date}. Thank you.",
        "A new version of the app is available. Update from the Play Store for the latest features.",
        "Please update your address in the profile section if you have moved.",
        "Papa ne paise bhej diye hain, check kar lena.",
        "Aaj office mein kaam zyada hai, late ho jaunga.",
        "Kal doctor ke paas jana hai, 10 baje ka appointment hai.",
        "The road near the school is blocked today, take the other route.",
        "Your account statement is attached. Let me know if any entry needs to be verified.",
        "Class is cancelled tomorrow, the teacher will share an update on the group.",
        "Your ticket has been cancelled and the refund will be credited to your wallet within 48 hours.",
        "Insurance premium of Rs {amt} is due on {date}. Renew through the official app.",
        "Thanks, got the money. Take care of yourself.",
        "आज बैठक 3 बजे है, कृपया समय पर आएं।",
        "rent", "lunch", "dinner split", "thanks", "groceries", "bill payment", "birthday gift", "school fees",
        "milk bill", "maintenance", "petrol", "movie tickets", "medicine", "for mom", "loan repayment", "trip share",
        "electricity bill", "salary", "donation", "emi", "advance", "booking", "token amount", "invoice 1023",
        "order 4521", "tuition fees", "rent october", "society maintenance", "gift", "cab fare", "chai", "recharge",
        "hospital bill", "wedding gift", "office lunch", "book purchase", "advance for booking", "deposit for flat",
        "tickets refund from me", "returning your money", "payment for sofa", "bike service",
    ],
}

TYPOS = [("immediately", "imediately"), ("account", "acount"), ("verify", "verfy"), ("verification", "verificaton"),
         ("customer", "costumer"), ("receive", "recieve"), ("payment", "paymnt"), ("refund", "refnd"),
         ("update", "updt"), ("please", "pls"), ("your", "ur"), ("urgent", "urgnt")]


def _fill(tpl: str, rng: random.Random) -> str:
    amt = rng.choice([rng.randint(100, 999), rng.randint(1000, 9999), rng.randint(10000, 99999), rng.randint(100000, 900000)])
    vals = {
        "bank": rng.choice(BANKS), "brand": rng.choice(BRANDS), "board": rng.choice(BOARDS),
        "agency": rng.choice(AGENCIES), "courier": rng.choice(COURIERS), "name": rng.choice(NAMES),
        "amt": f"{amt:,}" if rng.random() < 0.5 else str(amt),
        "fee": str(rng.choice([1, 5, 10, 49, 99, 199, 499, 999, 1499, 2999, 4999])),
        "upi": f"{rng.choice(['refund','verify','help','pay','desk','care','officer','kyc','task','win','vendor','rk','sk'])}"
               f"{rng.choice(['', '.', ''])}{rng.choice(['desk','cell','team','no1','india','support','1','7','99'])}"
               f"{rng.randint(1, 999)}@{rng.choice(HANDLES)}",
        "phone": f"{rng.choice('6789')}{rng.randint(100000000, 999999999)}",
        "link": f"{rng.choice(SHORT)}/{''.join(rng.choice('abcdefghijkmnpqrstuvwxyz23456789') for _ in range(6))}",
        "hrs": str(rng.choice([2, 4, 6, 12, 24, 48])), "case": f"{rng.choice(['MH','DL','KA','UP'])}/{rng.randint(1000, 99999)}/2026",
        "otp": str(rng.randint(100000, 999999)), "last4": str(rng.randint(1000, 9999)),
        "date": f"{rng.randint(1, 28)}-{rng.choice(MONTHS)}", "bal": f"{rng.randint(500, 250000):,}",
        "toll": str(rng.randint(100000, 999999)), "order": str(rng.randint(100000, 99999999)), "month": rng.choice(MONTHS),
    }
    return tpl.format(**vals)


def _vary(text: str, rng: random.Random) -> str:
    r = rng.random()
    if r < 0.18:
        text = text.lower()
    elif r < 0.26:
        text = text.upper()
    if rng.random() < 0.25:
        a, b = rng.choice(TYPOS)
        text = text.replace(a, b).replace(a.capitalize(), b.capitalize())
    if rng.random() < 0.15:
        text = text.replace(". ", " ").replace(",", "")
    if rng.random() < 0.10:
        text = rng.choice(["URGENT: ", "ALERT! ", "Dear user, ", "Notice - ", "Hi, "]) + text
    if rng.random() < 0.08:
        text = text + rng.choice([" 🙏", " !!", " ✅", " ..."])
    return text


def generate(n_per_class: int = 1500, seed: int = 11) -> tuple[list[str], list[str]]:
    rng = random.Random(seed)
    X: list[str] = []
    y: list[str] = []
    for label, templates in T.items():
        n = n_per_class * (2 if label == "benign" else 1)
        for _ in range(n):
            X.append(normalise(_vary(_fill(rng.choice(templates), rng), rng)))
            y.append(label)
    return X, y


def train_text(out_dir: Path | None = None, verbose: bool = True) -> dict:
    t0 = time.time()
    X, y = generate()
    pipe = Pipeline([
        ("tfidf", FeatureUnion([
            ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, sublinear_tf=True, max_features=60000)),
            ("word", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2, sublinear_tf=True, max_features=30000)),
        ])),
        ("lr", LogisticRegression(C=6.0, max_iter=500, class_weight="balanced")),
    ])
    pipe.fit(X, y)
    version = time.strftime("text-%Y.%m.%d-") + str(int(time.time()) % 1000)
    out_dir = out_dir or (ROOT / "ml" / "artifacts")
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": pipe, "version": version}, out_dir / "text.joblib", compress=3)

    model = TextModel(pipe, version)
    tests = json.loads((ROOT / "infra" / "seed" / "message_testset.json").read_text(encoding="utf-8"))
    scam = [t for t in tests if t["label"] != "benign"]
    ben = [t for t in tests if t["label"] == "benign"]
    caught = sum(1 for t in scam if model.classify(t["text"])[2] >= 0.7)
    right_class = sum(1 for t in scam if model.classify(t["text"])[0] == t["label"])
    flagged = sum(1 for t in ben if model.classify(t["text"])[2] >= 0.7)
    metrics = {
        "version": version, "n_train": len(X), "test_scam": len(scam), "test_benign": len(ben),
        "scam_recall": caught / max(1, len(scam)), "scam_class_accuracy": right_class / max(1, len(scam)),
        "benign_flagged": flagged / max(1, len(ben)), "seconds": round(time.time() - t0, 1),
    }
    (out_dir / "text_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    if verbose:
        print("text model:", json.dumps(metrics))
        for t in tests:
            label, _, p = model.classify(t["text"])
            ok = (p >= 0.7) == (t["label"] != "benign")
            if not ok:
                print(f"  MISS [{t['label']}] p={p:.2f} pred={label}: {t['text'][:90]}")
    return metrics


if __name__ == "__main__":
    train_text()
