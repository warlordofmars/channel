<!-- Copyright (c) 2026 John Carter. All rights reserved. -->
# Apple signing bootstrap

One-time setup (and renewal procedure) for code-signing the Channel
desktop app on macOS. Cert lasts 5 years; the App Store Connect API
key should be rotated annually.

After this runbook completes, the `Publish desktop (macOS)` CI job
(`.github/workflows/ci.yml`, the `publish-desktop-mac` job) can sign
and notarise builds end-to-end against the GitHub Actions repo secrets
this runbook provisions. The five required secrets are
`APPLE_CERT_P12_BASE64`, `APPLE_CERT_PASSWORD`,
`APPLE_API_KEY_P8_BASE64`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER_ID` —
consumed by the `Import Apple signing cert` and `Build + sign + notarise`
steps and by `desktop/scripts/notarize.js` (the `electron-builder`
`afterSign` hook).

## 1. Enrol in the Apple Developer Program

- Visit https://developer.apple.com/programs/enroll/
- Sign in with the Apple ID you want to associate with Channel.
- Choose **Individual / Sole Proprietor**, not Company. The legal name
  on the account is the name that appears on the signed builds'
  certificate ("Developer ID Application: <Your Name> (TEAMID)").
- $99 USD/year. Identity verification can take 24–48h.

## 2. Generate the Developer ID Application certificate

- Open Xcode → Settings → Accounts.
- Add your Apple ID if not present.
- Select the team → **Manage Certificates…** → `+` → **Developer ID
  Application**.
- Xcode generates the cert in the portal and stores the private key
  in your login Keychain.

## 3. Export the cert + key as a .p12

- Open Keychain Access → login keychain → My Certificates.
- Right-click "Developer ID Application: …" → **Export…**
- Save as `~/secrets/channel-developer-id.p12` and set a strong
  password (this is `APPLE_CERT_PASSWORD`).

## 4. Create an App Store Connect API key

- https://appstoreconnect.apple.com/access/integrations/api
- `+` → **Generate API Key**.
- Name: `channel-notarytool`. Access: **Developer** (sufficient for
  notarisation, no broader scope).
- Download the `.p8` once — it is never downloadable again. Save it
  to `~/secrets/AuthKey_<KEY_ID>.p8`.
- Note the **Key ID** (10-char) and **Issuer ID** (UUID).

## 5. Set GitHub Actions repo secrets

```bash
base64 -i ~/secrets/channel-developer-id.p12 | pbcopy
gh secret set APPLE_CERT_P12_BASE64        # paste, then Enter, Ctrl-D
gh secret set APPLE_CERT_PASSWORD          # the password from step 3
base64 -i ~/secrets/AuthKey_<KEY_ID>.p8 | pbcopy
gh secret set APPLE_API_KEY_P8_BASE64
gh secret set APPLE_API_KEY_ID             # 10-char string
gh secret set APPLE_API_ISSUER_ID          # UUID
```

Verify all five are present:

```bash
gh secret list | grep '^APPLE_'
```

Expected output (the `Updated` timestamps will be recent):

```
APPLE_API_ISSUER_ID     Updated YYYY-MM-DD
APPLE_API_KEY_ID        Updated YYYY-MM-DD
APPLE_API_KEY_P8_BASE64 Updated YYYY-MM-DD
APPLE_CERT_P12_BASE64   Updated YYYY-MM-DD
APPLE_CERT_PASSWORD     Updated YYYY-MM-DD
```

## 6. Verify locally

`desktop/scripts/sign_local.sh` requires three env vars: the path to
the `.p8` (default `~/secrets/AuthKey.p8`, override with
`APPLE_API_KEY_PATH`), the key id, and the issuer id. The script
defaults the signing identity to `Developer ID Application` — that
matches the cert from step 2 as long as only one such cert exists in
the login Keychain.

```bash
cd desktop && npm run build:current && cd ..

APPLE_API_KEY_PATH=$HOME/secrets/AuthKey_<KEY_ID>.p8 \
APPLE_API_KEY_ID=<KEY_ID> \
APPLE_API_ISSUER_ID=<ISSUER_ID> \
./desktop/scripts/sign_local.sh desktop/release/mac-universal/Channel.app
```

The script signs against the Keychain cert, submits to notarytool,
staples the ticket, and runs `spctl -a -vv` to confirm Gatekeeper
accepts the result. Expected final two lines:

```
desktop/release/mac-universal/Channel.app: accepted
source=Notarized Developer ID
```

If `spctl` reports `rejected`, re-check that step 2 generated the
right cert type (Developer ID Application, not Mac Development) and
that the keychain holds exactly one matching identity (`security
find-identity -v -p codesigning` should list one
`Developer ID Application` line).

## Renewal

- **Cert** — Apple emails 6 weeks before expiry. Re-export the new
  `.p12` per steps 3 + 5, including resetting `APPLE_CERT_PASSWORD`
  if you chose a new export password.
- **API key** — generate a new key per step 4, set
  `APPLE_API_KEY_P8_BASE64` / `APPLE_API_KEY_ID` / `APPLE_API_ISSUER_ID`
  per step 5, then revoke the old key in App Store Connect. Do not
  revoke before the new key is in CI.

After either renewal, re-run §6 locally to confirm the new credentials
work end-to-end before relying on the next CI run.
