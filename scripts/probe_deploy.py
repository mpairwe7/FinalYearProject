#!/usr/bin/env python3
"""Drive a *running deployment* over real HTTP, with nothing mocked.

    python scripts/probe_deploy.py https://landwind22-ura-chatbot.hf.space

Why this exists as well as the test suites. Every Playwright spec intercepts
`**/api/**`, and the backend suites run the app in-process against stubbed
models. Both are the right call for CI — deterministic and fast — but together
they leave one thing untested: whether the deployed image, with its real config,
actually does what the API says. That gap hid a live defect. `/v1/speech/voices`
advertised four English speakers and `_synthesize_edge_tts` ignored the
requested one, so every English voice in the settings picker played as
en-US-AriaNeural. Nothing failed, because nothing asked the deployment.

Read-only and idempotent. It never deletes an account, indexes a document, or
mutates a ticket, so it is safe against production. It does spend real
inference: one chat completion, one translation, and a few TTS calls.

Exit code is 0 only if every check passes, so it can gate a rollout.

Two rules for anything added here:
  * Cache-bust anything whose answer could be cached. A repeated phrase returns
    the cached speaker and the voice assertions pass without proving anything.
  * Assert against what the deployment reports, not what the local code says.
    Config differs per deployment — that is the whole point.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://landwind22-ura-chatbot.hf.space"

# Locales whose narration comes from Sunbird's native speakers.
NATIVE_LOCALES = ("lg", "ach", "nyn", "sw")


class Probe:
    def __init__(self, base: str, timeout: int) -> None:
        self.base = base.rstrip("/")
        self.api = f"{self.base}/api"
        self.timeout = timeout
        self.results: list[tuple[bool, str, str]] = []
        # Degradations: real, worth surfacing, but caused by an upstream the
        # deployment does not control. Kept out of the pass/fail tally so a
        # slow Sunbird cannot make a healthy rollout look broken.
        self.notes: list[str] = []
        # Cache-buster: the phrase cache keys on (text, voice, language), so a
        # repeated phrase would answer from cache and prove nothing about voice
        # resolution. Wall-clock is enough to be unique per run.
        self.nonce = str(int(time.time()))

    # -- plumbing ----------------------------------------------------------
    def call(self, method: str, path: str, body: dict | None = None, timeout: int | None = None):
        """Return (status, parsed-or-text, elapsed). Never raises on HTTP error."""
        url = path if path.startswith("http") else f"{self.api}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 — scheme checked below
        if req.type not in ("http", "https"):
            # urllib would happily open file:// or ftp://. The base URL comes
            # from argv, and this prints response bodies, so a mistyped scheme
            # would dump local files to the terminal.
            raise ValueError(f"refusing non-HTTP scheme: {req.type}")
        if data:
            req.add_header("Content-Type", "application/json")
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:  # noqa: S310 — checked above
                raw, code = r.read(), r.status
        except urllib.error.HTTPError as e:
            raw, code = e.read(), e.code
        except Exception as e:  # noqa: BLE001 — DNS, TLS, timeout all report alike
            return 0, str(e), time.monotonic() - started
        elapsed = time.monotonic() - started
        try:
            return code, json.loads(raw), elapsed
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Whole body, not a prefix: an earlier version truncated to 400
            # bytes and then reported the <h1> it had cut off as missing.
            return code, raw.decode("utf-8", "replace"), elapsed

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        self.results.append((bool(ok), label, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}", flush=True)
        return bool(ok)

    def note(self, message: str) -> None:
        self.notes.append(message)
        print(f"  WARN  {message}", flush=True)

    # -- sections ----------------------------------------------------------
    def liveness(self) -> None:
        print("\n[1] Liveness")
        for path in ("/health", "/ready"):
            code, payload, dt = self.call("GET", path)
            self.check(code == 200, f"GET {path} -> 200", f"{code}, {dt:.2f}s")
        code, _, _ = self.call("GET", "/tags")
        self.check(code == 200, "GET /tags -> 200", str(code))

    def voice_catalogue(self) -> dict:
        print("\n[2] Voice catalogue")
        code, cat, _ = self.call("GET", "/v1/speech/voices")
        if not self.check(code == 200, "GET /v1/speech/voices -> 200", str(code)):
            return {}
        voices = cat.get("voices", {})
        self.check(bool(voices), "catalogue is not empty", f"{sum(map(len, voices.values()))} voices")
        self.check(
            set(NATIVE_LOCALES).issubset(voices) and "en" in voices,
            "every supported locale is listed",
            ", ".join(sorted(voices)),
        )
        for locale, opts in sorted(voices.items()):
            defaults = [o for o in opts if o.get("default")]
            self.check(len(defaults) == 1, f"{locale}: exactly one default", f"{len(defaults)} of {len(opts)}")
            self.check(
                all(o.get("native") is (locale != "en") for o in opts),
                f"{locale}: native flag is consistent",
            )
            # No `label` here on purpose: the API returns ids and the client
            # renders the display name (lib/voices.ts voiceDisplayName).
            self.check(
                all(o.get("id") and o.get("provider") for o in opts),
                f"{locale}: every option has id + provider",
            )
        return cat

    def voice_selection(self, cat: dict) -> None:
        """The check that matters: does a chosen speaker reach the synthesizer?

        A pick can only be honoured by the provider that owns it, so this reads
        the `backend` the response reports before judging the `voice`. When
        Sunbird breaches SPEECH_CLOUD_DEADLINE_S the chain degrades to edge-tts
        and answers Luganda in an English stand-in — by design, and observed on
        the live Space with the same voice honoured exactly on the next call.
        Failing on that would make the probe red for an upstream hiccup and
        train everyone to ignore it. It is reported as a degradation instead;
        only a provider that answered and used the wrong speaker is a failure.
        """
        print("\n[3] TTS honours the selected voice")
        voices = cat.get("voices", {})
        for locale, phrase in (("lg", "Emisolo gy'eggwanga"), ("en", "The tax year")):
            opts = [o["id"] for o in voices.get(locale, [])]
            owner = {o["id"]: o.get("provider") for o in voices.get(locale, [])}
            if len(opts) < 2:
                self.check(False, f"{locale}: catalogue offers >=2 voices to compare", str(opts))
                continue
            text = f"{phrase} {self.nonce}"  # unique per run, so never a cache hit
            seen: dict[str, str] = {}
            for pick in opts[:2]:
                code, payload, dt = self.call(
                    "POST", "/v1/tts", {"text": text, "language": locale, "voice": pick}, 180
                )
                if not self.check(code == 200, f"{locale}: POST /v1/tts ({pick}) -> 200", f"{dt:.1f}s"):
                    continue
                self.check(bool(payload.get("audio_base64")), f"{locale}: {pick} returned audio")
                got, backend = payload.get("voice"), (payload.get("backend") or "")
                # The catalogue names a provider ("sunbird"), the response names
                # the backend that answered ("sunbird_cloud", or with "+cache"
                # appended on a cache hit). Compare on the stem and allow the
                # provider as a prefix, or every honoured Luganda call reads as
                # a degradation.
                served_by = backend.split("+")[0]
                if not served_by.startswith(owner.get(pick) or "\0"):
                    self.note(
                        f"{locale}: {pick} could not be honoured — chain degraded to "
                        f"{served_by} ({got}) after {dt:.0f}s; {owner.get(pick)} was unreachable"
                    )
                    continue
                seen[pick] = got
                self.check(got == pick, f"{locale}: {pick} was the speaker used", f"got {got}")
            if len(seen) == 2:
                self.check(
                    len(set(seen.values())) == 2,
                    f"{locale}: two picks give two different speakers",
                    " vs ".join(seen.values()),
                )
            elif seen:
                self.note(f"{locale}: only one pick reached its provider, cannot compare two speakers")
            # An unknown name must degrade to the locale default, not 4xx: a
            # stale client should still get audio.
            code, payload, _ = self.call(
                "POST", "/v1/tts", {"text": text + "x", "language": locale, "voice": "no-such-voice"}, 180
            )
            if self.check(code == 200, f"{locale}: an unknown voice still synthesizes", str(code)):
                self.check(
                    payload.get("voice") in opts,
                    f"{locale}: unknown voice fell back into the catalogue",
                    str(payload.get("voice")),
                )

    def inference(self) -> None:
        print("\n[4] Inference")
        code, payload, dt = self.call("POST", "/classify", {"text": "How do I file my VAT return?"})
        if self.check(code == 200, "POST /classify -> 200", f"{dt:.1f}s"):
            self.check(bool(payload.get("predictions")), "classifier returns predictions")

        code, _, dt = self.call("POST", "/v1/translate", {"text": "Good morning", "target_lang": "lg"}, 120)
        self.check(code == 200, "POST /v1/translate -> 200", f"{dt:.1f}s")

        code, payload, dt = self.call(
            "POST", "/v1/chat", {"message": "What is the VAT rate in Uganda?"}, 240
        )
        if self.check(code == 200, "POST /v1/chat -> 200", f"{dt:.1f}s"):
            reply = payload.get("reply") or ""
            self.check(len(reply) > 20, "chat returns a substantive reply", f"{len(reply)} chars")
            self.check(bool(payload.get("model")), "chat names the model", str(payload.get("model")))

        code, payload, _ = self.call("GET", "/v1/speech/health")
        if self.check(code == 200, "GET /v1/speech/health -> 200", str(code)):
            self.check(payload.get("status") == "ready", "speech pipeline is ready", str(payload.get("status")))

    def auth_gates(self) -> None:
        """Endpoints that must not answer without a token, on this deployment."""
        print("\n[5] Auth gates")
        # /v1/me is deliberately public — it is the "who am I" probe the client
        # calls before it has a session, and it answers authenticated:false.
        # Its sub-resources, which return personal data, are the gated ones.
        code, payload, _ = self.call("GET", "/v1/me")
        if self.check(code == 200, "GET /v1/me -> 200 (public identity probe)", str(code)):
            self.check(
                payload.get("authenticated") is False,
                "unauthenticated /v1/me says so plainly",
                str(payload.get("role")),
            )
        for path in ("/v1/me/profile", "/v1/me/consents", "/v1/me/export"):
            code, _, _ = self.call("GET", path)
            self.check(code == 401, f"GET {path} without a token -> 401", str(code))
        # Staff surfaces answer 503 when no staff IdP is configured — closed by
        # absence rather than by rejection, which is the stricter of the two.
        for path in ("/v1/admin/tickets", "/v1/admin/voice_audit"):
            code, _, _ = self.call("GET", path)
            self.check(code in (401, 403, 503), f"GET {path} is not open -> {code}", "")

    def frontend(self) -> None:
        print("\n[6] Frontend routes")
        for path, needle in (("/", "URA"), ("/signin", "Sign in"), ("/signup", "Create an account")):
            code, html, _ = self.call("GET", f"{self.base}{path}")
            if self.check(code == 200, f"GET {path} -> 200", str(code)):
                self.check(needle.lower() in str(html).lower(), f"{path} renders {needle!r}")
        # Whether an IdP is wired is per-deployment; either state is valid, but
        # a page offering a live button with no IdP behind it is not.
        _, html, _ = self.call("GET", f"{self.base}/signin")
        configured = "not configured" not in str(html)
        print(f"        (identity provider {'configured' if configured else 'absent'} on this deployment)")

    def run(self) -> int:
        print(f"\nLive probe of {self.base}\n{'=' * 72}")
        self.liveness()
        cat = self.voice_catalogue()
        if cat:
            self.voice_selection(cat)
        self.inference()
        self.auth_gates()
        self.frontend()
        failed = [r for r in self.results if not r[0]]
        print(f"\n{'=' * 72}\n{len(self.results) - len(failed)}/{len(self.results)} passed"
              f"{f', {len(self.notes)} degraded' if self.notes else ''}")
        for _, label, detail in failed:
            print(f"  FAIL  {label}  {detail}")
        for message in self.notes:
            print(f"  WARN  {message}")
        return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", nargs="?", default=DEFAULT_BASE, help=f"deployment URL (default: {DEFAULT_BASE})")
    ap.add_argument("--timeout", type=int, default=90, help="per-request timeout in seconds")
    args = ap.parse_args()
    return Probe(args.base, args.timeout).run()


if __name__ == "__main__":
    sys.exit(main())
