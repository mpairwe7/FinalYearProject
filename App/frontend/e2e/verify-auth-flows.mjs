/**
 * Registration and sign-in, end to end, against the production image.
 *
 * Drives the real /signup and /signin pages, clicks the real CTAs, and reads
 * the authorize URL the app actually navigates to — then checks it against
 * OAuth 2.1 + PKCE (RFC 7636) and the OIDC registration draft.
 *
 * The IdP login form itself is not filled in: that needs a real Auth0 account,
 * and what can break on our side is everything up to the redirect and
 * everything after the callback. Both are covered here.
 *
 * Not a .spec.ts, so Playwright does not collect it: the interesting assertions
 * only mean anything when an identity provider is actually configured, and
 * NEXT_PUBLIC_OIDC_* is baked at build time — CI builds without one, so there
 * the CTA is correctly disabled and there is no authorize URL to inspect. Run
 * it by hand against a deployment or a container built with an IdP:
 *
 *   BASE=http://localhost:18080 node e2e/verify-auth-flows.mjs
 *   BASE=https://landwind22-ura-chatbot.hf.space node e2e/verify-auth-flows.mjs
 */
import { firefox } from "@playwright/test";

const BASE = process.env.BASE || "http://localhost:18080";
const problems = [];
const check = (ok, label, detail = "") => {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${detail ? `  — ${detail}` : ""}`);
  if (!ok) problems.push(`${label}${detail ? ` (${detail})` : ""}`);
  return Boolean(ok); // callers gate follow-up assertions on this
};

const browser = await firefox.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();

const consoleErrors = [];
const cspViolations = [];
page.on("pageerror", (e) => consoleErrors.push(e.message));
page.on("console", (m) => {
  const t = m.text();
  if (m.type() === "error") consoleErrors.push(t);
  if (/Content Security Policy|violates the following/i.test(t)) cspViolations.push(t);
});
await page.addInitScript(() => localStorage.setItem("ura_analytics_consent", "false"));

// Stop at the IdP boundary: record where we were sent instead of following.
let authorizeUrl = null;
await page.route("**/authorize*", async (route) => {
  const url = route.request().url();
  if (!url.startsWith(BASE)) {
    authorizeUrl = url;
    // abort, not fulfill: fulfilling would navigate to the auth0 origin, and
    // the PKCE verifier lives in OUR origin's storage — reading it after a
    // cross-origin navigation returns the IdP's empty storage instead.
    await route.abort();
    return;
  }
  await route.continue();
});

function assertAuthorize(url, { registration }) {
  const u = new URL(url);
  const q = u.searchParams;
  const tag = registration ? "signup" : "signin";
  check(u.origin !== new URL(BASE).origin, `${tag}: goes to an external IdP`, u.origin);
  check(u.pathname === "/authorize", `${tag}: hits /authorize`, u.pathname);
  check(q.get("response_type") === "code", `${tag}: authorization-code flow`, String(q.get("response_type")));
  check(Boolean(q.get("client_id")), `${tag}: sends a client_id`);
  check(
    q.get("code_challenge_method") === "S256",
    `${tag}: PKCE S256 (RFC 7636)`,
    String(q.get("code_challenge_method")),
  );
  const verifierLike = q.get("code_challenge") || "";
  check(verifierLike.length >= 43, `${tag}: code_challenge is a real digest`, `${verifierLike.length} chars`);
  check(Boolean(q.get("state")), `${tag}: sends state (CSRF)`);
  // Not asserting a nonce. It is OPTIONAL for the code flow (OIDC Core
  // §3.1.2.1) and binds an ID token to the session — this client never accepts
  // one, so a nonce would be unvalidated decoration. Asserting it here was my
  // error, not the app's. What IS worth pinning is that the omission stays
  // deliberate: if an id_token ever starts being read, the nonce must arrive
  // with a validating check, and this assertion should come back with it.
  check(!q.get("nonce"), `${tag}: no unvalidated nonce (id_token is never consumed)`);
  check(
    (q.get("scope") || "").includes("openid"),
    `${tag}: requests the openid scope`,
    String(q.get("scope")),
  );
  const redirect = q.get("redirect_uri") || "";
  check(
    redirect.startsWith(BASE) && redirect.includes("/signin/callback"),
    `${tag}: redirect_uri points back at this origin`,
    redirect,
  );
  if (registration) {
    // Two spellings on purpose: prompt=create is the OIDC registration draft,
    // screen_hint=signup is what Auth0 actually honours today.
    check(q.get("prompt") === "create", "signup: prompt=create (OIDC registration draft)", String(q.get("prompt")));
    check(q.get("screen_hint") === "signup", "signup: screen_hint=signup (Auth0)", String(q.get("screen_hint")));
  } else {
    check(q.get("prompt") !== "create", "signin: does NOT ask the IdP to register");
  }
  return q;
}

// ---------------------------------------------------------------- signup ----
console.log("\n[1] Registration — /signup");
await page.goto(`${BASE}/signup`, { waitUntil: "networkidle" });
check(await page.getByRole("heading", { name: "Create an account" }).isVisible(), "the page renders");
const signupCta = page.getByRole("button", { name: /Continue to registration/ });
check(await signupCta.isVisible(), "offers a live registration CTA");
check(await signupCta.isEnabled(), "the CTA is enabled (IdP configured in this image)");
await signupCta.click();
await page.waitForTimeout(2500);
if (check(Boolean(authorizeUrl), "clicking it reaches the IdP")) {
  assertAuthorize(authorizeUrl, { registration: true });
}
const signupState = await page.evaluate(() =>
  Object.keys(sessionStorage).concat(Object.keys(localStorage)).filter((k) => /oidc|pkce|verifier|state/i.test(k)),
);
check(signupState.length > 0, "PKCE verifier + state are persisted for the callback", signupState.join(", "));

// ---------------------------------------------------------------- signin ----
console.log("\n[2] Sign-in — /signin");
authorizeUrl = null;
await page.goto(`${BASE}/signin`, { waitUntil: "networkidle" });
check(await page.getByRole("heading", { name: "Sign in", exact: true }).isVisible(), "the page renders");
const signinCta = page.getByRole("button", { name: /Continue with URA identity provider/ });
check(await signinCta.isVisible(), "offers a live sign-in CTA");
await signinCta.click();
await page.waitForTimeout(2500);
if (check(Boolean(authorizeUrl), "clicking it reaches the IdP")) {
  assertAuthorize(authorizeUrl, { registration: false });
}

// -------------------------------------------------------------- callback ----
console.log("\n[3] Callback hardening");
await page.goto(`${BASE}/signin/callback?error=access_denied&error_description=User+cancelled`, {
  waitUntil: "networkidle",
});
const denied = await page.locator("body").innerText();
check(!/access_denied/i.test(denied) || /cancell?ed|denied|try again/i.test(denied),
  "a denied consent is explained, not dumped raw", denied.replace(/\s+/g, " ").slice(0, 80));

await page.goto(`${BASE}/signin/callback?code=fake-code&state=not-the-state-we-issued`, {
  waitUntil: "networkidle",
});
const body = await page.locator("body").innerText();
check(
  !/signed in|welcome/i.test(body),
  "a mismatched state does not produce a session",
  body.replace(/\s+/g, " ").slice(0, 80),
);
const token = await page.evaluate(() => localStorage.getItem("ura_auth_token"));
check(!token, "no token is stored from a forged callback", String(token));

// ------------------------------------------------------------- anonymous ----
console.log("\n[4] Signed-out state is coherent");
await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
check(await page.locator("a.rail-acct-primary").isVisible(), "sidebar offers Sign in");
check(
  (await page.locator("a.rail-acct-ghost").getAttribute("href")) === "/signup",
  "header offers Sign up",
);
const me = await page.evaluate(async (b) => {
  // Tolerate a non-JSON body. Pointed at a frontend with no backend behind
  // /api, this used to throw inside evaluate and abort the whole run — the
  // harness reporting its own environment as a crash rather than a result.
  const r = await fetch(`${b}/api/v1/me`).catch((e) => ({ status: 0, text: async () => String(e) }));
  const raw = await r.text();
  try {
    return { status: r.status, body: JSON.parse(raw) };
  } catch {
    return { status: r.status, body: null, raw: raw.slice(0, 60) };
  }
}, BASE);
if (me.body === null) {
  console.log(`  SKIP  /v1/me — no backend behind /api here (HTTP ${me.status}: ${me.raw})`);
} else {
  check(me.status === 200 && me.body.authenticated === false,
    "/v1/me reports unauthenticated rather than erroring", `${me.status} ${JSON.stringify(me.body)}`);
}

// ------------------------------------------------------------------- CSP ----
console.log("\n[5] No CSP violations during the flows");
check(cspViolations.length === 0, "no Content-Security-Policy violations", cspViolations[0] || "");
const realErrors = consoleErrors.filter((e) => !/favicon|manifest|ERR_|Failed to fetch/i.test(e));
check(realErrors.length === 0, "no unexpected console errors", realErrors.slice(0, 2).join(" | "));

await browser.close();
console.log(`\n${problems.length === 0 ? "ALL CHECKS PASSED" : `${problems.length} FAILED:`}`);
problems.forEach((p) => console.log(` - ${p}`));
process.exit(problems.length ? 1 : 0);
