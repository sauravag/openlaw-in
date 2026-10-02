import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // `vitest run --coverage` writes its own bundled HTML/JS reporter here.
    // Generated third-party output, already gitignored; linting it only
    // produced warnings about its own eslint-disable comments.
    "coverage/**",
  ]),
]);

export default eslintConfig;
