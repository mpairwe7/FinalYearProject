// Flat-config ESLint for Next.js 16.
//
// Next.js 16 removes the `next lint` command and ships eslint-config-next
// with native Flat Config exports.  Running `bun run lint` delegates to
// `eslint .` which picks up this file automatically.
//
// Reference: https://nextjs.org/docs/app/guides/upgrading/version-16
import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

const config = [
  ...coreWebVitals,
  ...typescript,
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "next-env.d.ts",
      "tsconfig.tsbuildinfo",
      "bun.lock",
    ],
  },
  {
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
    },
  },
];

export default config;
