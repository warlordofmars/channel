<!-- Copyright (c) 2026 John Carter. All rights reserved. -->
# Apple signing bootstrap

One-time setup (and renewal procedure) for code-signing the Channel
desktop app on macOS. Cert lasts 5 years; the App Store Connect API
key should be rotated annually.

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
- Download the `.p8` once — it is never downloadable again.
- Note the **Key ID** (10-char) and **Issuer ID** (UUID).

## 5. Set GitHub Actions repo secrets

```bash
base64 -i ~/secrets/channel-developer-id.p12 | pbcopy
gh secret set APPLE_CERT_P12_BASE64        # paste, then Enter, Ctrl-D
gh secret set APPLE_CERT_PASSWORD          # the password from step 3
base64 -i ~/secrets/AuthKey_XXXXXXXXXX.p8 | pbcopy
gh secret set APPLE_API_KEY_P8_BASE64
gh secret set APPLE_API_KEY_ID             # 10-char string
gh secret set APPLE_API_ISSUER_ID          # UUID
```

## 6. Verify locally

```bash
cd desktop && npm run build:current
./scripts/sign_local.sh release/mac-arm64/Channel.app
```

The script signs against the Keychain cert, submits to notarytool,
staples the ticket, and runs `spctl -a -vv` to confirm Gatekeeper
accepts the result. Expected final line: `source=Notarized Developer ID`.

## Renewal

- **Cert** — Apple emails 6 weeks before expiry. Re-export the new
  `.p12`, `gh secret set APPLE_CERT_P12_BASE64` with the new value.
- **API key** — `+` a new one, set the secrets, then revoke the old
  key. Do not revoke before the new key is in CI.
