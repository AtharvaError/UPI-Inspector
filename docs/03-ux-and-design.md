# 03 — UX and Design

[← Index](README.md)

This document defines **what is on every screen and what it says**. How it **looks** is derived from the two reference designs the team supplied (section 1).

## 1. Design source

**Status: supplied.** On 7 October 2026 the team supplied two reference designs and three instructions.

| Reference | What it is | What is taken from it |
|---|---|---|
| **Madgicx** home page | Near-black background with a violet tint and soft purple glows; a product dashboard with a slim icon rail on the left; rounded cards with hairline borders; pill buttons; a segmented tab control; violet and green progress bars | Background and glow treatment, violet as the primary colour, card and border styling, the left navigation rail, the segmented control, green as the "good" colour |
| **Scalenut** home page | Deep navy background fading to blue; a bright blue primary button beside a white pill button; cyan underlined links; a geometric sans-serif typeface | Blue as the secondary accent, cyan for links and highlights, the white pill as the secondary button, the typeface direction |

Instructions from the team:

1. Use the references as a starting point and adapt them; they are not to be copied screen for screen.
2. **Navigation sits on the left** in both apps.
3. The design applies to **both the payer app and the dashboard**.

### How the references are used

| Taken from the references | Not taken from the references |
|---|---|
| Colour palette and glow treatment | Their screens and layouts |
| Typeface direction, type scale, weights | Their text, labels and copy |
| Corner radii, borders, shadows | Their information architecture, beyond "navigation on the left" |
| Pill buttons, segmented control, card styling, progress bars | Their data, charts and example content |
| Left navigation rail | Their logos, brand names or illustrations |

Screens, content, copy and flows are as specified in this documentation.

### Rules for applying it

1. **One token file.** Every visual value lives in `packages/design-tokens/tokens.json`. The payer app's theme and the dashboard's theme are both generated from that file. No component hard-codes a colour, size or radius.
2. **Dark theme only.** Both references are dark. The prototype has no light theme.
3. **Navigation on the left.** The dashboard has a left sidebar with icon and label, collapsible to an icon rail. The payer app has a slim icon rail on the left of its top-level screens; it is hidden inside the payment flow, where the payer must focus on one thing.
4. **Tier colours are derived, in order.** Neither reference has a four-step risk scale, so green, amber, orange and red were chosen to read as an escalation on the dark background. Green is taken from the Madgicx progress bar.
5. **Accessibility overrides the references.** Text meets a contrast ratio of 4.5:1 against its background (3:1 for large text).
6. **Never colour alone.** A tier is always shown with an icon and a word as well as a colour.

## 2. Design token contract

These are the names the code uses, with the values derived from the references.

### Colour

| Token | Used for | Value |
|---|---|---|
| `color.bg` | App and page background | `#070614` |
| `color.bg.glow.violet` | Radial glow behind page headers and tier screens | `rgba(109, 74, 255, 0.22)` |
| `color.bg.glow.blue` | Second glow, lower on the page | `rgba(47, 107, 255, 0.12)` |
| `color.surface` | Cards, sheets, table rows | `#0F0D1F` |
| `color.surface.raised` | Modals, popovers, the segmented control | `#16142B` |
| `color.surface.sunken` | Inputs | `#0B0A18` |
| `color.border` | Dividers, card and input borders | `#221F3A` |
| `color.border.strong` | Focused inputs, selected cards | `#34315A` |
| `color.text.primary` | Body text, headings | `#F5F4FF` |
| `color.text.secondary` | Captions, helper text | `#A7A4C2` |
| `color.text.muted` | Placeholder, disabled | `#6F6C8F` |
| `color.text.inverse` | Text on white or light fills | `#0B0A18` |
| `color.brand` | Primary buttons, active navigation, progress | `#6D4AFF` |
| `color.brand.hover` | Hover and pressed state | `#7F61FF` |
| `color.brand.subtle` | Active navigation background, tinted chips | `rgba(109, 74, 255, 0.16)` |
| `color.brand.on` | Text and icons on `color.brand` | `#FFFFFF` |
| `color.accent` | Secondary accent, chart series | `#2F6BFF` |
| `color.accent.cyan` | Links, highlights | `#38D6FF` |
| `color.tier.safe`, `.subtle`, `.on` | Safe tier, Trusted band | `#34D399`, `rgba(52, 211, 153, 0.14)`, `#04130D` |
| `color.tier.warning`, `.subtle`, `.on` | Warning tier, Caution band | `#FBBF24`, `rgba(251, 191, 36, 0.14)`, `#1A1202` |
| `color.tier.stepup`, `.subtle`, `.on` | Step-Up tier | `#FB923C`, `rgba(251, 146, 60, 0.16)`, `#1C0D02` |
| `color.tier.block`, `.subtle`, `.on` | Block tier, Reported band | `#F43F5E`, `rgba(244, 63, 94, 0.16)`, `#FFFFFF` |
| `color.neutral.chip` | Neutral and No-history bands | `#2A2745` with text `#C9C6E6` |
| `color.chart.1` to `color.chart.6` | Chart series, graph nodes | `#6D4AFF`, `#38D6FF`, `#34D399`, `#FBBF24`, `#FB923C`, `#F43F5E` |

`.subtle` is a tinted background for banners. `.on` is the text colour on the solid tier colour.

### Typography

| Token | Used for | Value |
|---|---|---|
| `font.family.display` | Headlines | DM Sans, weight 700 |
| `font.family.body` | Everything else | DM Sans, weights 400, 500, 600 |
| `font.family.mono` | IDs, amounts in tables | JetBrains Mono |
| `font.family.devanagari` | Hindi text | Noto Sans Devanagari |
| `type.display` | Tier headline (Step-Up, Block) | 30 / 36, weight 700 |
| `type.title` | Screen titles | 22 / 28, weight 600 |
| `type.heading` | Section headings | 17 / 24, weight 600 |
| `type.body` | Body | 15 / 22, weight 400 |
| `type.body.large` | Body on tier screens, large-text mode | 18 / 27, weight 400 |
| `type.caption` | Helper text, table meta | 12.5 / 18, weight 500 |
| `type.amount` | Rupee amounts | 34 / 40, weight 700, tabular figures |

All three typefaces are free and open source.

### Shape, space and depth

| Token | Used for | Value |
|---|---|---|
| `space.1` to `space.8` | Spacing scale | 4, 8, 12, 16, 20, 24, 32, 40 |
| `radius.sm`, `.md`, `.lg`, `.xl`, `.pill` | Inputs, cards, modals, hero cards, buttons and chips | 8, 12, 20, 28, 999 |
| `shadow.card` | Cards | `0 0 0 1px rgba(255,255,255,0.06), 0 12px 40px rgba(8,6,24,0.6)` |
| `shadow.brand` | Primary button glow | `0 8px 30px rgba(109,74,255,0.35)` |
| `border.width` | Hairlines | 1 |
| `icon.set` | Icon library | Feather-style line icons, 1.75 stroke |

### Motion

| Token | Used for | Value |
|---|---|---|
| `motion.duration.fast`, `.base`, `.slow` | Micro, standard, tier transitions | 120 ms, 220 ms, 380 ms |
| `motion.easing.standard`, `.emphasised` | Easing curves | `cubic-bezier(0.2, 0, 0, 1)`, `cubic-bezier(0.2, 0.8, 0.2, 1)` |

### Components

| Component | Style |
|---|---|
| Button, primary | Violet fill, pill, white text, soft violet glow |
| Button, secondary | White fill, pill, dark text |
| Button, ghost | Transparent, 1 px border, pill |
| Button, destructive | Block-tier red fill, pill |
| Segmented control | Raised-surface pill container; the active segment is one step lighter |
| Card | Surface fill, 1 px border, large radius |
| Input | Sunken fill, 1 px border, medium radius; strong border on focus |
| Chip | Pill; tinted background in the band colour |
| Progress bar | 4 px track in border colour; violet or tier-coloured fill |
| Navigation rail | Surface fill; active item has the subtle violet background and a violet icon |

## 3. UX principles

1. **Invisible when safe.** A normal payment has no extra screen, tap or delay.
2. **One instruction per screen.** A person being pressured on a call can follow one thing.
3. **Say what was observed.** "You have never paid this UPI ID" is a fact. "This is fraud" is an accusation.
4. **Name the scam.** "This looks like a digital-arrest scam" is more useful than "high risk".
5. **Break the script.** A pause, a question and a voice do more than an OTP.
6. **Always a way out.** Every protective screen has Cancel. Every Block has a route to a human.
7. **Never a score.** Payers see words. Analysts see numbers.
8. **Readable under stress.** Large type on tier screens, short sentences, the payer's language.

## 4. Payer app

### 4.1 Navigation

The payer app has a slim **icon rail on the left** of its top-level screens: Home, Safety check, Check a message, Held payments, Guardian, Practice, History, Settings. The rail is hidden inside the payment flow (P-03 to P-10) and on the tier screens, and it scrolls on a short screen.

```
P-00 Sign in / Register
P-01 Demo accounts (quick sign-in, demo mode only)
  └─ P-02 Home
       ├─ Pay ──────────► P-03 Enter payee ─► P-05 Amount and note ─► [assessment]
       ├─ Scan QR ──────► P-04 Scan QR ─────► P-05
       │                                        ├─ Safe ─────► P-09 PIN ─► P-10 Result
       │                                        ├─ Warning ──► banner on P-05 ─► P-09 or cancel
       │                                        ├─ Step-Up ──► P-06 (and P-20 if guardian) ─► P-09 or cancel
       │                                        ├─ Hold ─────► P-07 ─► later P-11
       │                                        └─ Decline ──► P-08
       ├─ Safety check ───► P-22   (look up, never-pay list, trends and help)
       ├─ Check a message ► P-12
       ├─ Held payments ──► P-11
       ├─ Guardian ───────► P-13   (and P-14 if the user is someone's guardian)
       ├─ Practice ───────► P-16
       ├─ History ────────► list ─► P-15 Report
       └─ Settings ───────► P-17
Overlays, demo only: P-18 Fake incoming call, P-19 Simulated SMS
Over everything: P-21 App lock; the flagged-caller banner (F-31)
```

### 4.2 Screens

**P-00 Sign in and register** (F-30)
- Sign in: phone number and 6-digit app PIN. Five wrong attempts lock the account, for longer each time.
- Register: name, phone number, a new 6-digit PIN entered twice, language, bank. Easy PINs are refused with the reason. Creates the account and a UPI ID, then signs in.
- On a phone, signing in with the PIN also enrols the phone for fingerprint confirmation (F-35).
- The demo accounts are offered only where the server allows passwordless sign-in: by default, on the laptop itself. Elsewhere a note says so.
- A notice when the last session ended by itself.

**P-01 Demo accounts** (demo only)
- A list of seeded demo users with name, segment and language. Tapping one signs in as that user without a PIN.

**P-02 Home**
- Greeting, simulated balance.
- Primary actions: **Pay**, **Scan QR**.
- Secondary actions: **Safety check**, **Check a message**, **Held payments** (badge with count), **Guardian**; the rest are on the rail.
- A card with this week's commonest scam pattern, which opens the trends (F-34).
- Recent payees row: tapping one opens P-05 with the payee filled in and `vpa_entry_method = contact`.
- Guardian users see an inbox badge when a request is waiting.

**P-03 Enter payee**
- A field for a UPI ID. Typing and pasting are both allowed; the app records which.
- An optional "Name you were given" field (the claimed name).
- A list of saved payees to pick from.
- When a valid UPI ID is entered, the **reputation chip** appears under the field, with the **registered name** in prominent type and the band.
- If the claimed name and registered name do not match, a line under the chip says so (F-22).
- Button: **Continue**. Disabled until a UPI ID resolves.
- States: resolving, resolved, not found ("This UPI ID does not exist"), offline.

**P-04 Scan QR**
- Camera view with a frame. Reads the UPI ID, the name on the code and any note. On the web build there is no camera: the screen offers three sample codes (demo mode) and a field to paste a `upi://` link.
- After a successful scan, shows the same chip and name-match line as P-03, then goes to P-05 with `vpa_entry_method = qr`.
- Fallback: "Enter UPI ID instead".

**P-05 Amount and note**
- Payee summary at the top: registered name, UPI ID, chip.
- Amount input, in rupees. Note input, optional, pre-filled if it came from a QR code or link.
- Button: **Pay ₹{amount}**.
- On tap: shows a short "Checking…" state while the assessment runs, then routes by tier.
- **Warning tier:** an inline banner appears above the button, with the tier icon, the family headline and the top reason, and "See why" to expand the other reasons. The button changes to **Continue anyway**, with **Cancel** beside it.
- The app counts app switches, amount edits and time on this screen from the moment it opens.

**P-06 Step-Up** (modal, cannot be dismissed by tapping outside)

| Step | Content | Control |
|---|---|---|
| 1 | Tier icon, family headline, body, up to three reasons. Spoken warning plays. Mute button. | **I understand, continue** / **Cancel payment** |
| 2 | Cool-off countdown ring with the body text still visible. "Take a moment. If someone is rushing you, that is a warning sign." | Continue is disabled until zero. **Cancel payment** is always enabled. |
| 3 | One question, two large answer buttons. The answer goes to the server on its own, before any authentication. | Answer |
| 4 | Authentication: device biometric prompt where the phone has one, otherwise **Send me a code**; the code arrives as a simulated SMS and goes in a 6-digit field. | **Verify** |

- Risky answer at step 3: the modal changes to the Decline screen (P-08).
- A link, "See how this scam works", is shown after the flow ends, never inside it.
- If a guardian approval is required, P-20 follows step 4.

**P-07 Hold**
- Full screen. Tier icon, "Payment paused".
- Family headline, body and reasons. Spoken warning plays.
- "You can send this after {time}. We will ask you again before anything is sent."
- A countdown to that time.
- Buttons: **Cancel payment** (primary), **Report this as a scam** (opens P-15), **Call 1930**, and **OK, I will decide later**, which leaves the payment paused and returns home.
- If a guardian exists: "We have told {guardian name}."

**P-08 Decline**
- Full screen. Tier icon, "Payment stopped".
- Family headline, body and reasons. Spoken warning plays.
- Buttons: **Call 1930** (primary), **Create complaint pack** (opens P-15), **Back to home**.
- Text link: **This is not a scam** → a short form (optional comment) that files a review request and confirms "A person will review this."
- After leaving: link to the matching rehearsal.

**P-09 PIN entry** (mock)
- A 4- or 6-digit PIN pad styled like a UPI PIN screen, clearly labelled "Demo — do not enter a real PIN".
- Any 4 to 6 digits are accepted. Submitting calls the mock PSP. The digits never leave the device: the app sends a placeholder of the same length.

**P-10 Result**
- Success: tick, amount, payee, transaction ID, **Done**.
- Cancelled: "Payment cancelled. No money was sent." **Done**.
- Failed: reason, **Try again**.

**P-11 Held payments**
- A list of the user's holds with payee, amount, status and time remaining.
- A hold that is **ready**: a card with "Do you still want to send ₹{amount} to {name}?" and **Yes, check and send** / **No, cancel**.
- "Yes" runs a new assessment and routes by its tier. A second Block goes to P-08.

**P-12 Check a message**
- A large text box: "Paste the message you received". Button: **Check**.
- Result card:
  - Verdict with icon: **Looks like a scam** / **Be careful** / **Looks fine**.
  - The scam family, if any.
  - The message with red-flag phrases highlighted. Tapping a highlight shows why it is a red flag.
  - What was found: UPI IDs, phone numbers, links.
  - Advice, two or three short lines.
  - If a flag was set: "For the next 24 hours we will be extra careful with your payments."
- Buttons: **Check another**, **Report** (for a scam verdict; opens P-15 without a transaction).
- Demo only: when the message came from a staged scenario that starts from a link, **Demo: tap the payment link in this message** opens P-05 with the payee, amount and note filled in and `vpa_entry_method = link`.
- A short list of previously checked messages.

**P-13 Guardian settings**
- No guardian yet: an explanation in two sentences, and **Add a guardian** → enter phone number or UPI ID → choose **Notify** or **Co-approve** → for Co-approve, an amount threshold → send the invitation.
- Pending: "Waiting for {name} to accept." **Cancel invitation**.
- Active: guardian's name, mode, threshold. **Change mode**, **Remove guardian**.
- Removing or loosening shows: "This will take effect in 24 hours. {Name} will be told now." with **Confirm** / **Keep protection**.
- If a removal is pending: a banner with the time it takes effect and **Undo**.

**P-14 Guardian inbox** (shown to users who are a guardian)
- Invitations to accept or decline.
- Requests, newest first, with status.
- Request detail: "{Name} is trying to send ₹{amount}", payee registered name and UPI ID, the family headline, the reasons, time left to respond.
- Buttons for an approval request: **Approve**, **Deny**. A notify-only alert has **Dismiss**.
- A line advises calling the person on a number the guardian already has. There is no Call button: the prototype stores phone numbers only as hashes.
- A note says the guardian sees only this one payment.

**P-15 Report and complaint pack**
- Summary of what will go into the pack: transaction, payee, timeline of calls and messages.
- Optional description field.
- **Create complaint pack** → progress → the payment and its timeline of calls and messages, as they appear in the pack.
- Then: **Open PDF** (web) or **Share PDF** (phone), **Call 1930**, **Open cybercrime portal**.
- A note: "This app does not file the complaint for you."

**P-16 Practice**
- A list of scripts with title, scam family, length and whether completed.
- Player: a chat-style conversation. The "scammer" messages appear one at a time; two or three reply buttons; after each choice a feedback card (safe or risky, one sentence why).
- End screen: safe choices out of total, "Three signs to remember", **Try another**.
- Clearly marked "Practice — this is not real".

**P-17 Settings**
- Language: English / हिन्दी.
- Voice warnings: on / off.
- Large text: on / off.
- Account: name, UPI ID, bank, last four digits of the phone number.
- Security (F-35), for a session opened with a PIN: when the app locks and when the session ends, whether this phone is enrolled, **Change app PIN**, **Lock now**, **Sign out on every device**. A demo session says that it does not lock.
- Demo: sign out or switch user, the server address and whether the live channel is connected. Hold and pause lengths are set on the server (`DEMO_MODE`), not in the app.
- **Play a sample warning** tests the voice.

**P-20 Waiting for guardian**
- "Waiting for {name} to approve." A countdown to expiry.
- **Cancel payment**.
- Approved → P-09. Denied → "{Name} did not approve this payment." → P-10 cancelled. Expired → P-10 cancelled.

**P-21 App lock** (F-35). Covers everything. A lock icon, the person's name, six dots and a number pad. Wrong PIN: "Wrong PIN. 4 tries left." On an enrolled phone, **Unlock with fingerprint or face**. **Sign out instead**. One line says why the app locks.

**P-22 Safety check** (F-31 to F-34). Three segmented tabs.
- *Look up.* One field for a UPI ID or a phone number, and **Check**. A UPI ID shows the payee card (name, band, verified, personal or business) and a list of facts with ranges, a pattern card if one applies, then **Never pay this UPI ID** (red) and **Pay this UPI ID**. A number shows its level in words in the tier colours, where the information comes from, and **Report this number**. "No reports" always carries the caution that it is not proof.
- *Never pay.* A field to add a UPI ID with an **Also report it as fraud** switch, then the list. Each entry has **Take off the list**; a pending one shows an amber countdown and **Keep blocked**. A line explains the wait.
- *Trends.* Up to three cards: pattern name, a rising/falling/steady/new chip, a rounded count, advice, **Practise spotting it**. Then **Official help**: 1930 (dials), cybercrime.gov.in and Sanchar Saathi (open in the browser), and the bank reminder.

**The flagged-caller banner** (F-31). While a call from a reported number is active: the in-call bar turns amber and a red banner under it reads "Reported as a scam number. Other people have reported this number. Do not pay anyone or share any code during this call." with **Report this caller**. Hidden on the tier, PIN and result screens.

**Additions to earlier screens.** P-03 and P-05 show a red line when the payee is on the never-pay list. P-06 offers the fingerprint only on an enrolled phone, and ends with "Too many wrong attempts" after three wrong codes. P-07 and P-08 gain "Was the caller trying to scam you?" (after a call) and **Never pay this UPI ID**. P-08 has a second form, "You blocked this UPI ID", for a payment stopped by the payer's own list. P-09 says "This check has expired" when a decision is more than ten minutes old.

## 5. Guardian experience

A guardian is an ordinary user of the same app. They see P-14 in addition to everything else. They never see the protected person's balance or history, only the single payment a request is about.

## 6. Analyst dashboard

Layout: a **left sidebar** with icon and label for each page, collapsible to an icon rail; the signed-in person's name, role and Sign out at its foot; a top bar with tenant switcher and mode badge (**Enforcing** or **Shadow**); content area.

**D-01 Sign in** (F-30). Email and password. Shows the signed-in person's name and role in the sidebar afterwards, with Sign out. In demo mode the page also lists the seeded accounts for one-click sign-in. Roles: admin, analyst, auditor. The sidebar shows only the pages a role may open.

**D-02 Live feed**
- Counters across the top: decisions in the last hour, share at each tier, p95 latency, degraded decisions.
- A table that updates live: time, payer (masked), payee, amount, tier tag, action, score, top reason, family, flags (call linked, message linked, guardian, shadow).
- Filters: tier, action, family, linked signals, time range, text search on UPI ID.
- A row opens D-03.

**D-03 Decision detail**
- Header: amount, payer, payee with registered name and claimed name, tier, action, score, model and rules versions, latency, enforced or shadow.
- **Summary** (F-27): AI-generated text with a label, key evidence, suggested action, Regenerate.
- **Reasons** as shown to the payer, in the payer's language and in English.
- **Why the score:** a waterfall of signal-group contributions; the list of fired rules with their effects.
- **Timeline:** calls, messages and the payment on one time axis, with caller risk and message verdict.
- **Payee:** reputation band, trust score, counters, graph features, consortium signal. Link to D-05.
- **Session:** entry method, app switches, time on screen, amount edits.
- **Outcome:** what the payer did; guardian response; hold status.
- **Case actions:** Confirm fraud (choose a family), Mark false positive, Add note, Create complaint pack.

**D-04 Near-miss feed.** Payments abandoned after a warning: time, payee, amount, tier, how it ended, weight. Grouped view by payee shows which UPI IDs people keep backing away from.

**D-05 Payee detail.** Search a UPI ID. Registered name and kind; band and trust score with a history chart; counters; recent payers (count only); reports; consortium signals and their source; a small neighbourhood graph with a link to D-06.

**D-06 Network.** A force-directed graph centred on a chosen payee. Nodes are UPI IDs coloured by band; solid edges are payments weighted by amount with direction arrows; dashed edges are shared devices. Controls: hops (1 to 3), time window, minimum amount. Clicking a node shows its summary and offers "Centre here" and "Open payee".

**D-07 Policy lab**
- Mode switch: Enforce / Shadow (admin only).
- Window selector.
- Three threshold sliders with numeric inputs.
- Result tiles: scams caught at Step-Up or Block, legitimate payments at each tier, interruptions per 1,000 payments, rupees protected, precision at a chosen real-world fraud rate (input, default 0.05%).
- A curve of scams caught against payers interrupted, with markers for current and proposed settings.
- A side-by-side "Current vs proposed" table.
- **Apply** (admin only, with a confirmation) and **Reset to defaults**.

**D-08 Model health**
- Active model version, training date, headline metrics. Registry table with candidates and their gate results.
- Drift: stability index per feature and for the score, with status tags; tier mix over time.
- Fairness: a table by age band, segment and language with the share of legitimate payments at each tier, the false-positive rate, and the disparity ratio with a flag.

**D-09 System health.** Latency percentiles by stage against their budgets, requests per second, degraded decisions, timeouts, database fallbacks, event backlog, decisions by hour, the versions in use and the background jobs.

**D-10 Review queue.** False-positive review requests and fraud reports awaiting a decision, oldest first. Each opens D-03 with the request's comment. Actions: Uphold, Overturn, Confirm fraud.

**D-11 Demo control** (demo only)
- Pick a demo user.
- Scenario buttons: Digital arrest, Fake refund, Mule transfer, Impersonation, KYC threat, Investment scam. Each stages the call, message and payee the scenario needs and shows what was staged.
- Individual triggers: fake incoming call (choose caller risk), deliver a scam message, inject shifted traffic (for the drift demo), replay simulator traffic.
- **Reset demo**.

**D-12 Audit log.** Who changed thresholds, modes, labels, accounts and models, and when. Read-only. Admin and auditor.

**D-13 Users and roles** (admin only, F-30)
- Staff table: name, email, role, status, last sign-in. Actions: add a staff member, change role, deactivate or reactivate, reset password.
- App users table: name, masked phone, bank, segment, status. Action: deactivate or reactivate.
- An admin cannot deactivate or demote their own account.

**D-14 Security** (admin and auditor, F-35). Four figures: failed sign-ins and wrong PINs in 24 hours, accounts locked now, sessions signed out and still blocked, app users with an enrolled phone. A configuration table: each control, a status chip (in place, demo setting, check, not safe outside a demo) and what is set. Counts of security events in the last 24 hours, and the most recent ones with who and from which address.

**Sidebar.** **Change password** opens a dialog (current, new, new again). The dashboard signs itself out after 15 minutes without a key press or a click and says so on the sign-in page.

## 7. Copy catalogue

The Hindi text below is a first draft and must be reviewed by a native speaker before the demo.

### 7.1 Scam families

Each family has a headline, a body, one question and the answer that counts as risky.

| Family | English | Hindi |
|---|---|---|
| **Digital arrest** — headline | This looks like a "digital arrest" scam | यह "डिजिटल अरेस्ट" ठगी जैसा लग रहा है |
| body | Police, CBI and courts never ask for money over UPI and never keep you on a call. Hang up. Call 1930. | पुलिस, सीबीआई या अदालत कभी UPI से पैसे नहीं माँगते और न ही आपको कॉल पर रोके रखते हैं। कॉल काट दें। 1930 पर कॉल करें। |
| question (risky: Yes) | Is someone on a call telling you to make this payment? | क्या कोई कॉल पर आपसे यह भुगतान करने को कह रहा है? |
| **Fake refund** — headline | You are about to PAY money, not receive it | आप पैसे भेजने जा रहे हैं, पाने नहीं |
| body | No one needs your UPI PIN or a payment from you to send you a refund. | रिफंड पाने के लिए न UPI पिन डालना पड़ता है, न कोई भुगतान करना पड़ता है। |
| question (risky: Yes) | Were you told this payment is needed to receive a refund? | क्या आपसे कहा गया है कि रिफंड पाने के लिए यह भुगतान ज़रूरी है? |
| **Mule transfer** — headline | This UPI ID is collecting money from many strangers | इस UPI ID पर कई अनजान लोग पैसे भेज रहे हैं |
| body | It was created recently and many new people have paid it in the last hour. | यह UPI ID हाल ही में बनी है और पिछले एक घंटे में कई नए लोगों ने इस पर भुगतान किया है। |
| question (risky: No) | Do you personally know the person you are paying? | क्या आप उस व्यक्ति को खुद जानते हैं जिसे आप पैसे भेज रहे हैं? |
| **Impersonation** — headline | Check this request with the person directly | पहले उस व्यक्ति से सीधे बात करके पुष्टि करें |
| body | You have never paid this UPI ID before. Call them on a number you already have. | आपने इस UPI ID पर पहले कभी भुगतान नहीं किया है। उनके पुराने, जाने-पहचाने नंबर पर कॉल करके पूछ लें। |
| question (risky: No) | Have you spoken to this person on a number you already had? | क्या आपने इस व्यक्ति से उनके पहले से सेव नंबर पर बात की है? |
| **KYC or bill threat** — headline | Banks and electricity boards do not take payments this way | बैंक और बिजली विभाग इस तरह भुगतान नहीं लेते |
| body | Messages that threaten to block your account or cut your power unless you pay now are a common scam. | खाता बंद करने या बिजली काटने की धमकी देकर तुरंत भुगतान माँगने वाले संदेश अक्सर ठगी होते हैं। |
| question (risky: Yes) | Did a message or call threaten to block your account or cut a service? | क्या किसी संदेश या कॉल में खाता बंद करने या सेवा काटने की धमकी दी गई थी? |
| **Investment or task scam** — headline | Guaranteed returns are a warning sign | पक्के मुनाफ़े का वादा ख़तरे का संकेत है |
| body | Scammers pay small amounts first, then ask for larger "deposits" you never get back. | ठग पहले छोटी रकम लौटाते हैं, फिर बड़ी "जमा राशि" माँगते हैं जो कभी वापस नहीं मिलती। |
| question (risky: Yes) | Were you promised a return or reward for making this payment? | क्या इस भुगतान के बदले आपसे मुनाफ़े या इनाम का वादा किया गया है? |
| **Unclassified** — headline | This payment looks unusual | यह भुगतान असामान्य लग रहा है |
| body | Take a moment to check who you are paying and why. | एक पल रुककर जाँच लें कि आप किसे और क्यों भुगतान कर रहे हैं। |
| question (risky: Yes) | Did someone you have not met in person ask you to make this payment? | क्या किसी ऐसे व्यक्ति ने यह भुगतान करने को कहा है जिससे आप कभी मिले नहीं हैं? |

### 7.2 Reason sentences

The 20 reason templates are defined with their codes in [06, section 10](06-risk-engine-and-ml.md#10-explainability). Hindi versions of the templates are a content task in the build plan.

### 7.3 Reputation chip

| Band | English | Hindi |
|---|---|---|
| Trusted | Established payee | स्थापित प्राप्तकर्ता |
| Neutral | (no label; registered name only) | |
| No history | Little or no payment history | भुगतान का बहुत कम या कोई इतिहास नहीं |
| Caution | Be careful — {one-line reason} | सावधान रहें — {कारण} |
| Reported | Reported for fraud | धोखाधड़ी के लिए रिपोर्ट किया गया |
| Name mismatch line | The name you were given does not match the registered name | आपको बताया गया नाम पंजीकृत नाम से मेल नहीं खाता |

### 7.4 Fixed strings

| Key | English | Hindi |
|---|---|---|
| `tier.warning.title` | Check before you pay | भुगतान से पहले जाँच लें |
| `tier.stepup.title` | Please pause | कृपया रुकें |
| `tier.hold.title` | Payment paused | भुगतान रोका गया है |
| `tier.decline.title` | Payment stopped | भुगतान रोक दिया गया |
| `stepup.cooloff` | Take a moment. If someone is rushing you, that is a warning sign. | एक पल रुकें। अगर कोई आपको जल्दी करने को कह रहा है, तो यह ख़तरे का संकेत है। |
| `hold.body` | You can send this after {time}. We will ask you again before anything is sent. | आप यह भुगतान {time} के बाद भेज सकते हैं। कुछ भी भेजने से पहले हम आपसे दोबारा पूछेंगे। |
| `hold.ready` | Do you still want to send ₹{amount} to {name}? | क्या आप अब भी {name} को ₹{amount} भेजना चाहते हैं? |
| `cancel.payment` | Cancel payment | भुगतान रद्द करें |
| `continue.anyway` | Continue anyway | फिर भी जारी रखें |
| `call.1930` | Call 1930 | 1930 पर कॉल करें |
| `not.a.scam` | This is not a scam | यह ठगी नहीं है |
| `review.filed` | A person will review this. | एक व्यक्ति इसकी समीक्षा करेगा। |
| `guardian.told` | We have told {name}. | हमने {name} को बता दिया है। |
| `guardian.waiting` | Waiting for {name} to approve. | {name} की मंज़ूरी का इंतज़ार है। |
| `guardian.denied` | {name} did not approve this payment. | {name} ने इस भुगतान को मंज़ूरी नहीं दी। |
| `guardian.remove.delay` | This will take effect in 24 hours. {name} will be told now. | यह 24 घंटे बाद लागू होगा। {name} को अभी सूचित किया जाएगा। |
| `msg.verdict.scam` | Looks like a scam | यह ठगी जैसा लग रहा है |
| `msg.verdict.careful` | Be careful | सावधान रहें |
| `msg.verdict.fine` | Looks fine | ठीक लग रहा है |
| `msg.flag.set` | For the next 24 hours we will be extra careful with your payments. | अगले 24 घंटे हम आपके भुगतानों पर अतिरिक्त सावधानी बरतेंगे। |
| `pack.disclaimer` | This app does not file the complaint for you. | यह ऐप आपकी ओर से शिकायत दर्ज नहीं करता। |
| `pin.demo` | Demo — do not enter a real PIN | डेमो — असली पिन न डालें |

### 7.5 Message-checker advice

| Verdict | Advice lines |
|---|---|
| Scam | Do not reply, click links or call numbers in this message. · Do not pay anyone it mentions. · If you already paid, call 1930 now. |
| Careful | Check with the organisation using a number from their official website or app. · Do not share any code or PIN. |
| Fine | We did not find signs of a scam. Stay careful with any request for money or codes. |

## 8. Audio

- **As built: on-device text-to-speech only.** It reads the tier title, then the family headline and body, in the payer's language. No clips were recorded. The plan below is kept for when they are.
- One clip per scam family per language: the headline followed by the body. Seven families × two languages = 14 clips, 6 to 12 seconds each.
- Calm, clear voice, moderate pace. No alarm sounds or music.
- File names: `audio/{locale}/{family_code}.mp3`. The API returns `audio_key = {family_code}`.
- If a clip is missing, on-device text-to-speech reads the same text in the same locale.
- Plays once per decision at Step-Up step 1, on the Hold screen and on the Decline screen. A button mutes it or plays it again.

## 9. Accessibility

- Text contrast of at least 4.5:1; 3:1 for large text and for icons that carry meaning.
- Touch targets at least 48 × 48 dp. Answer buttons at Step-Up are full width.
- Tier screens use `type.body.large`. A large-text setting scales all payer text by 1.3.
- Every tier has an icon and a word as well as a colour.
- Screen-reader labels on all controls. Tier changes are announced.
- The cool-off countdown is announced at start and end, not every second.
- Nothing flashes. Tier transitions respect the system's reduced-motion setting.
- Hindi renders in a Devanagari face with correct conjuncts and enough line height.

## 10. Motion

- Safe: no transition of our own. The PIN screen appears as it would normally.
- Warning: the banner slides in above the button and the button label cross-fades. `motion.duration.base`.
- Step-Up: the modal rises with a dimmed backdrop. `motion.duration.slow`, `motion.easing.emphasised`. Steps cross-fade.
- Block: a full-screen transition that is clearly different from the normal flow. No shake, no flashing.
- Countdown ring: a smooth sweep.
- Dashboard: new feed rows fade in at the top; counters tick; graph nodes settle once and then stay still.
- The dashboard uses CSS transitions. The payer app uses React Native's built-in `Animated`. Durations and easings come from the tokens.

## 11. Demo-only UI

These exist to stage scenarios. They are visually marked "Demo" and are not part of the product.

**P-18 Fake incoming call.** A full-screen incoming-call look-alike triggered from D-11: caller number, "Unknown", Accept and Decline, and a red "Reported as a scam number" box when the number is one other people have reported (F-31). Accepting shows an in-call bar at the top of the app with a timer, so the audience can see the payment being made "during the call". It plays no audio and makes no real call. Ending the call from the bar sends the call-ended event.

**P-19 Simulated SMS.** A banner at the top of the app that looks like a message notification. Used for one-time codes ("Your code is 482913") and for delivering a scam message in a scenario; tapping a scam message offers "Check this message", which opens P-12 with the text filled in.

**Demo badge.** A small persistent "Demo data" tag on both apps.

**Phone frame and two sessions.** In a wide browser window the payer app draws itself in a phone-sized frame. A window opened with `?slot=2` keeps its own signed-in user, so two windows on one laptop can be the payer and the guardian.
