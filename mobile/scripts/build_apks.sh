#!/usr/bin/env bash
# Build the three Android APKs, one at a time, into mobile/dist.
#
#   API_BASE_URL=https://test.sokopay.example/api/v1 bash mobile/scripts/build_apks.sh
#
# API_BASE_URL is the server the apps talk to (HTTPS for anything but a local emulator).
# Without it the apps use http://10.0.2.2:8000/api/v1 (an emulator on this computer).
# APPS="customer" builds just one. Builds clean up after themselves (small disks).
set -u
M="$(cd "$(dirname "$0")/.." && pwd)"
URL="${API_BASE_URL:-}"
DEFINE=()
[ -n "$URL" ] && DEFINE=(--dart-define="API_BASE_URL=$URL")
mkdir -p "$M/dist"
for app in ${APPS:-customer agent merchant}; do
  echo "== $app ${URL:+-> $URL} ($(df -h . | tail -1 | awk '{print $4}') free)"
  cd "$M/$app"
  flutter build apk --release --target-platform android-arm64,android-arm "${DEFINE[@]}" < /dev/null 2>&1 \
    | grep -E "Built build|FAILURE|What went wrong|rror:" | head -8
  if [ -f build/app/outputs/flutter-apk/app-release.apk ]; then
    cp build/app/outputs/flutter-apk/app-release.apk "$M/dist/sokopay-$app.apk"
    echo "OK $app"
  else
    echo "FAILED $app"
  fi
  (cd android && ./gradlew --stop >/dev/null 2>&1)
  rm -rf build android/.gradle
done
