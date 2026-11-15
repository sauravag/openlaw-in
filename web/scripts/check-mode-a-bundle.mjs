#!/usr/bin/env node
// Fails if the Mode A (NEXT_PUBLIC_OPENLAW_AUTH_ENABLED unset) client bundle
// contains any sign-in internals. Run after `npm run build`.
//
// Scope: `.next/static` ONLY -- what ships to browsers. Server chunks under
// `.next/server` legitimately contain the auth Route Handlers' code (they exist
// in the build and return 404 at runtime in Mode A), so they are not checked.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const FORBIDDEN = ["/auth/v1/otp", "openlaw_session", "openlaw_pkce"];
const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const staticDir = process.argv[2] ?? join(root, ".next", "static");

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* files(path);
    else yield path;
  }
}

let scanned = 0;
const hits = [];
try {
  for (const path of files(staticDir)) {
    if (!/\.(js|css|html|json|txt|map)$/.test(path)) continue;
    scanned += 1;
    const text = readFileSync(path, "utf8");
    for (const needle of FORBIDDEN) if (text.includes(needle)) hits.push(`${path}: ${needle}`);
  }
} catch (error) {
  console.error(`check-mode-a-bundle: cannot read ${staticDir} (run \`npm run build\` first): ${error.message}`);
  process.exit(2);
}
if (scanned === 0) {
  console.error(`check-mode-a-bundle: no files found under ${staticDir}`);
  process.exit(2);
}
if (hits.length > 0) {
  console.error("check-mode-a-bundle: auth strings found in the client bundle:");
  for (const hit of hits) console.error(`  ${hit}`);
  process.exit(1);
}
console.log(`check-mode-a-bundle: OK (${scanned} files scanned, none contain ${FORBIDDEN.join(", ")})`);
