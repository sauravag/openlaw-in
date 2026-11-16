#!/usr/bin/env node
// Fails if the Mode A (NEXT_PUBLIC_OPENLAW_AUTH_ENABLED unset) build ships
// sign-in internals to browsers. Run after `npm run build`.
//
// Scope: what a browser can receive.
//   - `.next/static`: client JS/CSS chunks.
//   - `.next/server/app/**` prerendered output (*.html, *.rsc, *.body), which is
//     served as-is. Route-handler JS under `.next/server/app/api` is EXCLUDED:
//     the auth Route Handlers legitimately contain their code (they return 404
//     at runtime in Mode A), and server chunks never reach the client.
//
// `AUTH_ENABLED` is a compile-time literal (`next.config.ts` always defines
// `NEXT_PUBLIC_OPENLAW_AUTH_ENABLED`), so with the flag off the minifier drops
// every sign-in branch and `/signup` is absent from browser output too; it is a
// sentinel so a regression in either shows up here.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const FORBIDDEN = [
  "/auth/v1/otp",
  "openlaw_session",
  "openlaw_pkce",
  "/api/auth/sign-in",
  "/api/auth/sign-out",
  "/api/auth/migrate",
  "/account/delete",
  "/signup",
  // The sidebar's titled fetch lives in a Mode B-only module.
  "include_titles",
];

const STATIC_EXTENSIONS = /\.(js|css|html|json|txt|map)$/;
const PRERENDER_EXTENSIONS = /\.(html|rsc|body)$/;

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* files(path);
    else yield path;
  }
}

/** Files a browser can receive under `nextDir` (a `.next` directory). */
function* clientReachableFiles(nextDir) {
  for (const path of files(join(nextDir, "static"))) {
    if (STATIC_EXTENSIONS.test(path)) yield path;
  }
  const appDir = join(nextDir, "server", "app");
  const apiDir = join(appDir, "api") + sep;
  for (const path of files(appDir)) {
    if (path.startsWith(apiDir)) continue;
    if (PRERENDER_EXTENSIONS.test(path)) yield path;
  }
}

/** Returns `{ scanned, hits }`; throws if the directory cannot be read. */
export function scan(nextDir) {
  let scanned = 0;
  const hits = [];
  for (const path of clientReachableFiles(nextDir)) {
    scanned += 1;
    const text = readFileSync(path, "utf8");
    for (const needle of FORBIDDEN) {
      if (text.includes(needle)) hits.push(`${relative(nextDir, path)}: ${needle}`);
    }
  }
  return { scanned, hits };
}

function main() {
  const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
  const nextDir = process.argv[2] ?? join(root, ".next");
  let result;
  try {
    result = scan(nextDir);
  } catch (error) {
    console.error(`check-mode-a-bundle: cannot read ${nextDir} (run \`npm run build\` first): ${error.message}`);
    process.exit(2);
  }
  if (result.scanned === 0) {
    console.error(`check-mode-a-bundle: no files found under ${nextDir}`);
    process.exit(2);
  }
  if (result.hits.length > 0) {
    console.error("check-mode-a-bundle: auth strings found in browser-reachable output:");
    for (const hit of result.hits) console.error(`  ${hit}`);
    process.exit(1);
  }
  console.log(`check-mode-a-bundle: OK (${result.scanned} files scanned, none contain ${FORBIDDEN.join(", ")})`);
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) main();
