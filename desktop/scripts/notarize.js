// Copyright (c) 2026 John Carter. All rights reserved.
import { notarize } from "@electron/notarize";
import { writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Buffer } from "node:buffer";

export default async function notarizeHook(context) {
  const { electronPlatformName, appOutDir, packager } = context;
  if (electronPlatformName !== "darwin") return;
  if (!process.env.APPLE_API_KEY_P8_BASE64) return; // local builds: skip silently

  const appName = packager.appInfo.productFilename;
  const appPath = join(appOutDir, `${appName}.app`);

  const keyPath = join(tmpdir(), `AuthKey_${process.env.APPLE_API_KEY_ID}.p8`);
  writeFileSync(
    keyPath,
    Buffer.from(process.env.APPLE_API_KEY_P8_BASE64, "base64"),
    { mode: 0o600 },
  );

  await notarize({
    appPath,
    appleApiKey: keyPath,
    appleApiKeyId: process.env.APPLE_API_KEY_ID,
    appleApiIssuer: process.env.APPLE_API_ISSUER_ID,
  });
}
