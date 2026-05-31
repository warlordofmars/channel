#!/usr/bin/env bash
# Copyright (c) 2026 John Carter. All rights reserved.
#
# Locally sign + notarise a built Channel.app against the Developer ID
# cert in the user's Keychain and the App Store Connect API key in
# ~/secrets/. Idempotent; safe to re-run.
#
# Usage: ./desktop/scripts/sign_local.sh <path-to-Channel.app>

set -euo pipefail

APP_PATH="${1:?path to Channel.app required}"
ENTITLEMENTS="$(dirname "$0")/../resources/entitlements.mac.plist"
IDENTITY="${APPLE_SIGNING_IDENTITY:-Developer ID Application}"
API_KEY_PATH="${APPLE_API_KEY_PATH:-$HOME/secrets/AuthKey.p8}"
API_KEY_ID="${APPLE_API_KEY_ID:?APPLE_API_KEY_ID env var required}"
API_ISSUER_ID="${APPLE_API_ISSUER_ID:?APPLE_API_ISSUER_ID env var required}"

if [[ ! -d "$APP_PATH" ]]; then
  echo "sign_local: $APP_PATH does not exist or is not a .app bundle" >&2
  exit 1
fi

echo "==> Signing $APP_PATH against '$IDENTITY'"
codesign --force --options runtime --timestamp \
  --entitlements "$ENTITLEMENTS" \
  --sign "$IDENTITY" \
  --deep "$APP_PATH"

echo "==> Zipping for notarytool submission"
ZIP_PATH="${APP_PATH%.app}.zip"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

echo "==> Submitting to Apple notary"
xcrun notarytool submit "$ZIP_PATH" \
  --key "$API_KEY_PATH" \
  --key-id "$API_KEY_ID" \
  --issuer "$API_ISSUER_ID" \
  --wait

echo "==> Stapling ticket"
xcrun stapler staple "$APP_PATH"

echo "==> Verifying with spctl"
spctl -a -vv "$APP_PATH"

echo "==> Done. Cleaning up zip."
rm -f "$ZIP_PATH"
