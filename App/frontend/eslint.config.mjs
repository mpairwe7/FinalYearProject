// Flat-config ESLint for Next.js 16.
//
// Next.js 16 removes the `next lint` command and ships @next/eslint-plugin-next
// with Flat Config as the default.  Running `bun run lint` delegates to
// `eslint .` which picks up this file automatically.
//
// Reference: https://nextjs.org/docs/app/guides/upgrading/version-16
import { FlatCompat } from "@eslint/eslintrc";
import { dirname } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

export default [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "next-env.d.ts",
      "tsconfig.tsbuildinfo",
      "bun.lock",
    ],
  },
];
