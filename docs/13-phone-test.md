# 13 — Testing on a real phone

[← Index](README.md)

Nothing in this project has run on a physical phone yet. Everything was built and checked in a desktop browser, where the camera, the fingerprint prompt, the secure storage and the screen-capture block do not exist. This page is the test to run when two Android phones are available. It takes about 30 minutes. Write the result next to each line; anything that fails goes into [12, section 6](12-as-built.md#6-known-gaps).

## 1. Before you start

On the laptop:

```
python manage.py phone-check --export
```

It checks what can be checked without a phone: the native modules are installed, the app type-checks, the Android bundle builds, the device-proof arithmetic matches the server's, and the API answers on the laptop's network address. On 7 October 2026 all of these passed.

Then:

| Step | What to do |
|---|---|
| Network | Put the laptop and both phones on the **same network that you control**: one phone's hotspot is best. Do not use venue Wi-Fi. Traffic between phone and laptop is plain HTTP, so on a shared network a PIN or a token could be read in transit. |
| Firewall | The first time, Windows asks whether Python and Node may accept connections. Allow both on private networks. |
| Servers | `python manage.py api` and, in a second terminal, `python manage.py app`. The second prints a QR code. |
| Phones | Install **Expo Go** from the Play Store. Scan the QR code with it. |
| Sign-in | The tap-to-sign-in demo accounts are offered only on the laptop itself (`DEMO_LOGIN=local`). On a phone, sign in with a phone number and the PIN: the demo people are `90000 00001` (Meera) to `90000 00005` (Sunita), listed in `ml/upi_ml/simulator.py`, and they all share the PIN in `SEED_USER_PIN` in `.env`. To offer the tap-to-sign-in list to phones on your own hotspot, set `DEMO_LOGIN=lan` and restart the API. |

## 2. The test

Use Meera (`90000 00001`, Hindi, has a guardian) on phone A and Arjun (`90000 00002`, her guardian) on phone B.

### 2.1 Start-up and sign-in

| # | Do | Expect | Result |
|---|---|---|---|
| 1 | Open the app from Expo Go | The sign-in screen within about 10 seconds; fonts load; the left rail shows two icons (sign in, register) | |
| 2 | Look at the bottom of the sign-in screen | The server address is the laptop's network address, not `localhost` | |
| 3 | Sign in with a wrong PIN | "Wrong sign-in details"; not signed in | |
| 4 | Sign in with the right PIN | Home screen; Meera's app is in Hindi and the text is Devanagari, not boxes | |
| 5 | Settings → Security | "This phone is enrolled…" (the phone made a device key at sign-in and stored it in the system keystore). If it says "not enrolled", secure storage failed: note it | |
| 6 | Close Expo Go completely and open the app again | The lock screen, not the home screen and not the sign-in screen | |
| 7 | On the lock screen, tap "Unlock with fingerprint or face" (shown only if the phone has a fingerprint set up) | The system fingerprint prompt; a match opens the app | |
| 8 | Lock again (Settings → Lock now), enter a wrong PIN | "Wrong PIN. 4 tries left."; still locked; still signed in | |
| 9 | Leave the app in the background for 70 seconds and come back | Locked | |

### 2.2 Camera

| # | Do | Expect | Result |
|---|---|---|---|
| 10 | Home → Scan QR | A permission prompt for the camera, with the text "The camera is used to scan UPI QR codes." | |
| 11 | Point the camera at a UPI QR code (any shop's printed code, or one generated from `upi://pay?pa=freshmart@banka&pn=FreshMart`) | The payee screen opens with that UPI ID; the entry method recorded is "qr" | |
| 12 | Deny the camera permission and open Scan again | A message and the option to paste the code instead; no crash | |

### 2.3 The spoken warning

| # | Do | Expect | Result |
|---|---|---|---|
| 13 | Settings → Play a sample warning (phone not on silent) | The sentence is spoken, in Hindi on Meera's phone | |
| 14 | On the laptop dashboard: Demo control → Digital arrest → Meera. On phone A accept the call, then Pay → paste `cyber.cell.verify@bankb` → ₹1,80,000 | The Hold screen; the warning is read aloud once, in Hindi. If no Hindi voice is installed the phone may stay silent or use another voice: note which | |
| 15 | Tap the speaker button | Speech stops; tapping again repeats it | |

### 2.4 The caller warning (new)

| # | Do | Expect | Result |
|---|---|---|---|
| 16 | During step 14, before paying, look at the incoming-call screen | The red "Reported as a scam number" box under the number | |
| 17 | After accepting, look at any ordinary screen | An amber bar "On call…" and a red banner under it with "Report this caller" | |
| 18 | Tap "Report this caller" | "Thank you. The caller has been reported."; the link disappears | |

### 2.5 Step-Up with fingerprint (new)

| # | Do | Expect | Result |
|---|---|---|---|
| 19 | Dashboard: Demo control → start a call from an unknown number to Arjun (risk 0). On phone B: Pay → `kavita@banka` → ₹40,000 | Step-Up: warning, countdown, question | |
| 20 | Answer "No", then on the confirm step tap "Use fingerprint or face" | The system fingerprint prompt | |
| 21 | Use the right finger | The PIN screen follows. On the dashboard, the decision's outcome later reads "Passed step-up and paid". This shows the device proof was accepted by the server | |
| 22 | Repeat 19 and 20, then cancel the fingerprint prompt and tap "Use a code instead" → "Send me a code" | A banner with a 6-digit code arrives; entering it continues to the PIN | |
| 23 | Repeat once more and enter a wrong code three times | "Too many wrong attempts. This payment has been stopped." | |

### 2.6 Guardian, on two phones

| # | Do | Expect | Result |
|---|---|---|---|
| 24 | Phone A (Meera): Pay → `rohan@banka` → ₹25,000 | "Waiting for Arjun" with a countdown | |
| 25 | Phone B (Arjun) | A notice within 3 seconds; opening it shows the amount, the payee and the reasons | |
| 26 | Phone B: Approve | Phone A moves to the PIN screen within 3 seconds | |

### 2.7 Look-up and never-pay list (new)

| # | Do | Expect | Result |
|---|---|---|---|
| 27 | Rail → Safety check → look up `91111 11103` | "Known scam number" | |
| 28 | Look up `freshmart@banka` | A verified shop with its facts; "Never pay this UPI ID" and "Pay this UPI ID" | |
| 29 | Tap "Never pay this UPI ID", then try to pay FreshMart | The amount screen already says it is on the list; paying shows "You blocked this UPI ID" | |
| 30 | Safety check → Never pay → Take off the list | A one-minute countdown (24 hours outside demo mode); "Keep blocked" cancels it | |

### 2.8 Things that only a phone shows

| # | Check | Result |
|---|---|---|
| 31 | The left rail fits the screen height without clipping (8 icons); on a short phone it scrolls | |
| 32 | The keyboard does not cover the field being typed in (payee, amount, one-time code) | |
| 33 | The hardware Back key: leaves ordinary screens; does nothing on Hold, Decline and Step-Up | |
| 34 | Rotate the phone: the app stays in portrait | |
| 35 | Large text (Settings): nothing is cut off on the Hold and Decline screens | |
| 36 | Hindi: no button label wraps onto three lines or is cut off | |
| 37 | With the laptop's API stopped: the app says it cannot reach the server; it does not hang | |

## 3. What cannot be tested in Expo Go

| Thing | Why | What it would take |
|---|---|---|
| The screen-capture block on the PIN, code and lock screens | It is switched off in demo mode, because a demo phone is usually mirrored to a projector | Set `DEMO_MODE=false` on a test server and try a screenshot on those screens: it should come out black on Android |
| Reading the real call log, so that a real incoming call is seen | Expo Go cannot hold the phone or call-log permissions | A development build with a native call-screening module (Android only; iOS does not allow it) |
| A key that the fingerprint itself unlocks | The device key is in the system keystore, but any code in the app can read it once the phone is unlocked; the fingerprint check is done by the app | A development build using a hardware-backed key that requires biometric authentication for every use |
| Detecting a rooted phone, or a modified copy of the app | Needs Play Integrity | A published build |

## 4. Recording the result

Copy the three "Result" columns into the pull request or the team chat with the phone models and Android versions. Then update:

- [12, section 6](12-as-built.md#6-known-gaps): replace "nothing has run on a physical phone" with what was found.
- [10, section 2](10-testing-and-demo.md#2-acceptance-tests): AT-20 (the spoken warning in Hindi), and the "Manual, on a phone" row in section 1.
