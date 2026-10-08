# SokoPay test APKs (internal testing only)

| File | App | Android ID |
|---|---|---|
| `sokopay-customer.apk` | SokoPay (customers) | `gh.sokopay.customer` |
| `sokopay-agent.apk` | SokoPay Agent | `gh.sokopay.agent` |
| `sokopay-merchant.apk` | SokoPay Business | `gh.sokopay.business` |

All three are version 0.1.0 (build 1). They need Android 7.0 or newer (API 24) and target Android 16 (API 36). They contain code for ARM phones (arm64 and armeabi-v7a), which covers almost all phones in use.

## Before you share them

- **They are signed with a debug key.** Use them for internal testing only. Play Store and public builds need a private upload key (see `docs/review/02-trust.md`).
- **They point to a local development server:** `http://10.0.2.2:8000`, which an Android emulator uses to reach the developer's own computer. On a real phone they can't reach a server until one is deployed. Then rebuild with that server's HTTPS address:

  ```bash
  flutter build apk --release --dart-define=API_BASE_URL=https://staging.example/api/v1
  ```

  Plain HTTP is allowed only to `10.0.2.2` and `localhost`, so test servers on a real network must use HTTPS.
- **Push notifications are off** until Firebase is configured (see `mobile/README.md`).

## Installing on a phone

Copy the APK to the phone and open it. Android asks once to allow installs from that source. To install from a computer instead:

```bash
adb install sokopay-customer.apk
```

## Rebuilding

CI builds all three on every change and attaches them as the `sokopay-test-apks` artifact (`.github/workflows/ci.yml`). Locally, run the script below. It builds the apps one at a time and cleans up between them, because this machine has little free disk space:

```bash
bash /c/Users/User/dev/build_apks.sh
```
