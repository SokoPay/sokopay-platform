# SokoPay mobile apps (Flutter)

Three Flutter apps share the design system and backend (`merchant/` — SokoPay
Business — is described below):

- `customer/` — the consumer app (pay bills, airtime/data, and shops). **Built.**
- `agent/` — the agent app (float, cash-in, cash-out). **Built.** The cash operations
  are a DEMI-licence activity: the backend serves them only when the DEMI licence is
  active, so the app works end to end once DEMI is granted.

Both apps depend on **`packages/sokopay_shared`**, a local Flutter package holding the
common code — theme/brand, token storage, the API client (JWT + auto-refresh), and the
phone → OTP → PIN auth flow. The apps import it as
`import 'package:sokopay_shared/sokopay_shared.dart';` and only contain their own
feature screens. No shared code is duplicated.

## Merchant app — "SokoPay Business"

For a merchant's team on their phones (the web portal remains the full back-office).
Signs in the same way (phone + OTP + PIN); the account must be a member of a business.

- **Counter QR**: full-screen QR + 8-character code for customers who can't scan.
- **Request payment**: enter an amount (or leave it open) → a one-sale QR with a
  15-minute countdown; the screen polls and switches to a big **Paid** confirmation
  the moment the customer pays. Cancel, and a list of recent requests.
- **Payments** *(everyone)*: history newest first, grouped by day, with status chips
  (All / Paid / Pending / Failed / Refunded) and a period picker (today / 7 / 30 days /
  all); infinite scroll; tap for details. Owner/Admin/Finance also see a period
  summary (paid count, gross, fees, what they receive) and each payment's fee and net;
  Cashiers see amounts and status only. Customer numbers are always masked.
- **Settlements** *(Owner/Admin/Finance)*: available balance, settlement accounts
  (verified ✓ or pending SokoPay's name check), history with live status
  (awaiting approval / processing / paid / failed — failed payouts are already back in
  the balance). **Settle now** and **Add account** are Owner/Finance only and ask for
  the PIN again (server-checked, same lockout as sign-in). Bank accounts are offered
  only once the payment partner enables bank payouts.
- Push with deep links: **Payment received** (every team member) opens that payment's
  detail; **Settlement paid / didn't go through** (Owner/Admin/Finance) and **New
  settlement account** (Owner) open Settlements. Works when the app is closed
  (launched by the tap), in the background, or open (an in-app banner with **View**).
  If the session has expired, the link opens right after sign-in. Merchant alerts go
  to the Business app only, never to the same person's customer app. Link handling:
  `lib/core/deep_links.dart` (unit tests in `test/deep_links_test.dart`).

Roles: Cashiers see only the QR features; balances and settlements are hidden.
API: `/merchant-app/*` (backend `apps/merchants/app_views.py`).

```bash
cd mobile/merchant
flutter pub get
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1
```

## Agent app

Signs in the same way (phone + OTP + PIN; the user's account has an agent profile).
Home shows the agent's float balance and the two actions — **cash in** (credit a
customer's wallet from float) and **cash out** (give a customer cash, debit their
wallet). Talks to `/agent/me`, `/agent/cash-in`, `/agent/cash-out`, `/agent/transactions`.

**Transactions** (home → *See all*): full history with Cash in / Cash out / Top-ups
chips and a period picker; a summary card (totals and counts per kind for the period,
plus the float now); infinite scroll grouped by day; each row shows its effect on the
float (cash-in spends float, cash-out and top-ups add to it). Tap for details and an
**AG-** reference to quote to support. Customer numbers are masked everywhere.
API: `/agent/history`, `/agent/transactions/<id>`.

**Notifications + inbox** (bell on home): *You're now a SokoPay agent*, *Float topped
up*, and *Float running low* — sent once when a cash-in takes the float below
GH₵200 (`AGENT_LOW_FLOAT_MINOR`), so the agent tops up before turning customers away.
Tapping any of them (push or inbox) opens home with the float; other messages open
the inbox. The inbox screen is shared by all three apps (`sokopay_shared`
`InboxScreen`), each with its own route whitelist (`lib/core/deep_links.dart`).

```bash
cd mobile/agent
flutter pub get
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1
```

## Customer app

### What it does today
- **Layout** (SokoPay colours and logo): bottom bar Home · Transfers · **Scan QR** (raised
  centre button) · Offers · More. Home has the wallet card — **wallet ID**, balance with
  hide/show, **Deposit · Cash out · Statements** — and service tabs **For you · Pay · Buy ·
  Lifestyle · Finance**. Transfers hub: SokoPay user, mobile money, bank, other wallets,
  cross border, and *Recently paid*. Services SokoPay will aggregate from licensed partners
  but hasn't connected yet (Save, Invest, Pension, Food, Tickets, Cross border) open an
  honest "coming soon" page — nothing pretends to work.
- **Cash out at an agent**: press Cash out → give the agent your number or wallet ID →
  approve their request with your PIN. No agent can take money without your PIN.
- **Biometric unlock** (all three apps): fingerprint / Face ID, opt-in under More (or the
  lock icon in the agent/Business apps). Locks on restart and after 2 minutes away; PIN
  fallback is checked by the server. Payments still ask for the PIN.
- **App-switcher privacy** (all three apps, everyone): when the app leaves the foreground
  its content is covered with the SokoPay logo, so the recent-apps preview never shows a
  balance or account details.
- Sign in by phone + one-time SMS code; first-time users set a 6-digit PIN; returning
  users sign in with their PIN. Tokens are JWTs kept in encrypted device storage and
  refreshed automatically.
- Home with quick actions: bills, airtime, data, wallet, send money, insurance & loans.
- **Bills & airtime**: pick a biller, check the account holder's name, enter the amount,
  pay with MoMo or the wallet, and watch the status resolve. ECG prepaid tokens are
  shown with a copy button.
- **Data bundles**: choose MTN, Telecel or AT, pick a bundle (sample bundles are
  labelled), buy for yourself or someone else.
- **Wallet** *(DEMI)*: balance, activity, add money, send to SokoPay users, and
  **Limits & verification**: see your tier and usage, and verify your Ghana Card to
  raise them.
- **Send money** *(DEMI)*: to any MoMo wallet, a bank, or G-Money/Zeepay. The
  recipient's name must be confirmed before sending; history included.
- **Activity**: one history of everything that moved your money — bills, airtime, data,
  shop payments, transfers, money sent/received, cash in/out at agents, top-ups,
  money from abroad, loans/premiums, salary. Filters (All / Payments / Transfers /
  Wallet), period picker, a money in / money out summary, grouped by day, infinite
  scroll. Payments open their receipt; others open a details sheet. People appear as
  "Kofi B.", never by phone number. API: `/activity` (backend `apps/activity`).
- **Insurance & loans**: browse partner products, apply with explicit consent, track
  applications, and pay approved insurance premiums from the wallet *(DEMI)*.
- **Scan to pay** *(DEMI)*: scan a shop's QR (or type its 8-character code), confirm
  the shop name and amount, pay from the wallet. Fixed-amount QRs lock the amount.
- **Selfie verification**: at tier 1 (Ghana Card verified), take a live selfie to reach
  tier 2 and the higher BoG limits.
- **Inbox + push with deep links**: every money event (top-up, money received,
  transfer, bill result) lands in the in-app inbox and is pushed to the phone. Tapping
  a push — app closed, in background, or open (in-app banner with **View**) — or an
  inbox message opens the right screen: a payment → its **receipt** (live status, ECG
  token), wallet events → Wallet, transfers → Send money, loans → Insurance & loans;
  anything else → the inbox. If signed out, it opens right after sign-in. Customer
  pushes go only to the customer app. The link handling is shared with the Business
  app (`sokopay_shared` `DeepLinks`); each app keeps its own route whitelist in
  `lib/core/deep_links.dart` (tests in `test/deep_links_test.dart`).
- **Sign out** revokes the tokens on the server (not just locally); changing your PIN
  signs out every other device.

Features that need a licence SokoPay doesn't hold yet show a clear "available once
SokoPay receives the licence" message (the backend's `not_licensed` response).

It talks to: `/auth/*`, `/billers`, `/billers/{code}/lookup`, `/telcos/{n}/bundles`,
`/payments/{bill,airtime,data}`, `/payments/{reference}`, `/wallet*`,
`/wallet/transfer*`, `/marketplace/*`, `/kyc`.

### Run it
Requires the Flutter SDK (3.19+) and a running backend.

```bash
cd mobile/customer
flutter pub get

# Point the app at your backend. On the Android emulator the host is 10.0.2.2.
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1
```

Start the backend first (see ../backend), run `python manage.py seed_billers`, and in
development the OTP code is written to the backend log (no real SMS is sent yet).

### Structure
```
mobile/
  packages/sokopay_shared/     shared package (depended on by both apps)
    lib/sokopay_shared.dart    barrel export
    lib/src/core/              config, theme, token storage, API client
    lib/src/auth/              phone → OTP → PIN screens + AuthController
  customer/lib/
    features/home/             home screen with quick actions
    features/bills/            biller list, pay-bill/airtime flow, bills service
    features/data/             bundle list + buy flow
    features/wallet/           wallet, add money, send to SokoPay users
    features/transfer/         send to MoMo / bank / other fintechs + history
    features/marketplace/      insurance & loans, apply, applications, premiums
    features/kyc/              limits & Ghana Card verification
    features/common/           payment tracker, pay-with picker
    main.dart                  app shell + go_router routes + providers
  agent/lib/
    features/agent/            home (float), cash-in/out, agent service
    main.dart                  app shell + routes + providers
```

### Push notifications setup (one-off per app)
Create a Firebase project, add the Android and iOS apps, and run `flutterfire configure`
in `customer/` and `agent/` (it drops `google-services.json` /
`GoogleService-Info.plist` in place). Until then `FirebasePush.init()` logs and the app
runs without push. Backend: set `PUSH_PROVIDER=fcm`, `FCM_PROJECT_ID` and
`FCM_SERVICE_ACCOUNT_FILE` (a service-account JSON with the Messaging role).

### Biometric setup (after `flutter create` generates the platform folders)
Android: `MainActivity` must extend `FlutterFragmentActivity`; add the `USE_BIOMETRIC`
permission. iOS: add `NSFaceIDUsageDescription` to `Info.plist`.
Optional on Android, for a guaranteed blank recent-apps preview on every phone: set
`FLAG_SECURE` in `MainActivity.onCreate` (see `app_lock.dart`) — note it also blocks
screenshots and screen recording of the app.

### Not yet wired
Partner-dependent services (see "coming soon" tiles) — they switch on as each licensed
partner is connected on the backend.
The Dart code has not been compiled yet (no Flutter SDK on the build machine) — run
`flutter analyze` in each app as the first step.

## Android builds (added 2026-10-08)

- Each app has an `android/` project. The application IDs are `gh.sokopay.customer`, `gh.sokopay.agent` and `gh.sokopay.business`, with minSdk 24 and targetSdk 36.
- **Release builds are debug-signed.** They are for internal testing only. Add a private upload key before any store release.
- **Network rule:** only HTTPS is allowed, except plain HTTP to `10.0.2.2` and `localhost` for local development (`res/xml/network_security_config.xml`).
- **Camera:** only the customer app asks for camera permission, for QR scanning and the KYC selfie.
- **Pinned plugins:** `path_provider_foundation` 2.4.4 and `path_provider_android` 2.2.23 are pinned in each app's `dependency_overrides`. Newer versions add native build hooks (`objective_c`, `jni`) that hang `flutter test` on Windows and need more NDK work. Behaviour is the same. Revisit when upgrading Flutter.
- **Building:** CI builds the APKs. Locally, use `bash /c/Users/User/dev/build_apks.sh`. Gradle is capped at 2 GB of memory because builds filled the disk on a small machine. Built files go to `dist/` (see `dist/README.md`).
- **Legal links:** every app links to `/legal/...` pages on the API host. They appear on the sign-in screen and under Help & legal.
