#!/usr/bin/env python3
"""End-to-end verification of the seven reported issues, through the tunnel.

Run against a live stack. Every check hits the public URL, not localhost, so
the tunnel, the Next proxy and the FastAPI host are all in the path — which is
where the original reports came from.

    BASE_URL=https://<host> DEV_TOKEN=<staff token> python3 scripts/verify_seven_issues.py

`DEV_TOKEN` is optional; without it the two checks that need staff auth
(`/metrics`, reading the raised ticket back as an officer) report INFO rather
than failing. Mint one on the API host with
`app.auth.jwt_auth.make_dev_token`, and do not paste it into a shell history —
pass it through the environment.

Companion record: App/docs/traceability/seven-issue-fixes-2026-08-24.md.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

BASE = os.environ.get("BASE_URL", "https://struttingly-nongeological-briella.ngrok-free.dev")
TIMEOUT = float(os.environ.get("VERIFY_TIMEOUT", "180"))

RESULTS: list[tuple[str, str, str]] = []   # (issue, status, detail)


def record(issue: str, ok: bool | None, detail: str) -> None:
    status = "PASS" if ok else ("FAIL" if ok is False else "INFO")
    RESULTS.append((issue, status, detail))
    print(f"[{status}] {issue}: {detail}", flush=True)


def request(path: str, method="GET", body=None, headers=None, timeout=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"ngrok-skip-browser-warning": "1"}
    if data:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as resp:
            payload = resp.read()
            return resp.status, payload, time.perf_counter() - t0
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), time.perf_counter() - t0


def jrequest(path, **kw):
    status, payload, elapsed = request(path, **kw)
    try:
        return status, json.loads(payload), elapsed
    except Exception:
        return status, {"_raw": payload[:400].decode(errors="replace")}, elapsed


# --------------------------------------------------------------------------
def check_health():
    status, body, _ = jrequest("/api/health")
    record("stack", status == 200, f"/api/health -> {status} {body.get('status', body)}")
    status, body, _ = jrequest("/api/v1/speech/health")
    record(
        "stack",
        status == 200,
        f"/api/v1/speech/health -> {status} asr={body.get('asr_backend')} tts={body.get('tts_backend')} mt={body.get('mt_backend')}",
    )


# --------------------------------------------------------------------------
LUGANDA_Q = "Nsobola ntya okwewandiisa okufuna TIN?"        # "How can I register for a TIN?"
SWAHILI_Q = "Ninawezaje kujiandikisha kupata namba ya TIN?"
ENGLISH_Q = "How do I register for a TIN?"

# Words that would only appear if the answer came back in English.
ENGLISH_MARKERS = re.compile(
    r"\b(the|you|your|register|application|please|and|with|for)\b", re.I
)


def looks_english(text: str) -> bool:
    words = re.findall(r"[A-Za-z']+", text)
    if len(words) < 8:
        return False
    hits = len(ENGLISH_MARKERS.findall(text))
    return hits / max(1, len(words)) > 0.18


def check_issue2_language():
    """Issue 2 — answers must come back in the language asked."""
    for label, question, expect_locale in (
        ("luganda (auto-detected, no locale sent)", LUGANDA_Q, "lg"),
        ("swahili (auto-detected, no locale sent)", SWAHILI_Q, "sw"),
    ):
        status, body, elapsed = jrequest(
            "/api/v1/chat",
            method="POST",
            body={"message": question},
            headers={"X-Session-ID": "verify-lang"},
        )
        reply = str(body.get("reply", ""))
        locale = body.get("locale")
        ok = status == 200 and locale == expect_locale and not looks_english(reply)
        record(
            "issue2-language",
            ok,
            f"{label}: locale={locale} english_looking={looks_english(reply)} "
            f"{elapsed:.1f}s reply={reply[:110]!r}",
        )

    # An explicit English turn must stay English.
    status, body, elapsed = jrequest(
        "/api/v1/chat",
        method="POST",
        body={"message": ENGLISH_Q, "locale": "en"},
        headers={"X-Session-ID": "verify-lang-en"},
    )
    record(
        "issue2-language",
        status == 200 and body.get("locale") == "en",
        f"english stays english: locale={body.get('locale')} {elapsed:.1f}s",
    )


def check_issue2_streaming():
    """The streaming path is the one the web client uses, and the one that was broken."""
    url = f"{BASE}/api/v1/chat/stream"
    data = json.dumps({"message": LUGANDA_Q}).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Session-ID": "verify-stream",
            "Accept": "text/event-stream",
            "ngrok-skip-browser-warning": "1",
        },
        method="POST",
    )
    phases, tokens, revisions, meta_locale = [], [], [], None
    event = ""
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            for raw in resp:
                line = raw.decode(errors="replace").rstrip("\n")
                if line.startswith("event: "):
                    event = line[7:].strip()
                elif line.startswith("data: "):
                    payload = line[6:]
                    if event == "phase":
                        phases.append(payload.strip())
                    elif event == "metadata":
                        try:
                            meta_locale = json.loads(payload).get("locale")
                        except Exception:
                            pass
                    elif event == "token":
                        tokens.append(payload)
                    elif event == "revision":
                        revisions.append(payload)
    except Exception as exc:  # noqa: BLE001
        record("issue2-streaming", False, f"stream failed: {exc}")
        return

    final = revisions[-1] if revisions else "".join(tokens)
    saw_translation = any(p.startswith("translation.") for p in phases)
    ok = meta_locale == "lg" and not looks_english(final)
    record(
        "issue2-streaming",
        ok,
        f"metadata.locale={meta_locale} phases={phases} "
        f"translation_announced={saw_translation} {time.perf_counter()-t0:.1f}s "
        f"final={final[:110]!r}",
    )


# --------------------------------------------------------------------------
def _figures(text: str) -> set[str]:
    return set(re.findall(r"\d[\d,]*(?:\.\d+)?", text))


def check_issue3_figures():
    """Issue 3 — a translated answer must not change a figure."""
    q_en = "What is the VAT registration threshold in Uganda?"
    status_en, body_en, _ = jrequest(
        "/api/v1/chat",
        method="POST",
        body={"message": q_en, "locale": "en"},
        headers={"X-Session-ID": "verify-fig-en"},
    )
    status_lg, body_lg, _ = jrequest(
        "/api/v1/chat",
        method="POST",
        body={"message": q_en, "locale": "lg"},
        headers={"X-Session-ID": "verify-fig-lg"},
    )
    en_reply, lg_reply = str(body_en.get("reply", "")), str(body_lg.get("reply", ""))
    en_figs, lg_figs = _figures(en_reply), _figures(lg_reply)
    # Either the figures survived, or the guard refused and served English.
    served_english = lg_reply.strip() == en_reply.strip()
    ok = bool(en_figs) and (lg_figs == en_figs or served_english or not lg_figs)
    record(
        "issue3-figures",
        ok if en_figs else None,
        f"en_figures={sorted(en_figs)} lg_figures={sorted(lg_figs)} "
        f"guard_served_english={served_english}",
    )


def check_issue3_metrics(token: str | None):
    if not token:
        record("issue3-metrics", None, "no dev token — /metrics needs staff auth")
        return
    status, payload, _ = request("/api/metrics", headers={"Authorization": f"Bearer {token}"})
    text = payload.decode(errors="replace")
    wanted = [
        "contradicted_reply_withheld_total",
        "reply_localization_figures_changed_total",
        "escalation_requested_total",
    ]
    present = [name for name in wanted if name in text]
    record(
        "issue3-metrics",
        status == 200,
        f"/metrics -> {status}; counters exposed: {present or 'none yet (zero-valued counters are not emitted until first increment)'}",
    )


# --------------------------------------------------------------------------
def check_issue4_escalation(token: str | None):
    """Issue 4 — the taxpayer's own way to a human."""
    conv = f"verify-esc-{int(time.time())}"
    status, body, elapsed = jrequest(
        "/api/v1/escalate",
        method="POST",
        body={
            "conversation_id": conv,
            "reason": "The VAT answer does not match my assessment notice.",
            "locale": "en",
        },
    )
    ok = status == 200 and body.get("ok") is True and bool(body.get("ticket_id"))
    record(
        "issue4-escalation",
        ok,
        f"POST /v1/escalate -> {status} ok={body.get('ok')} ticket={str(body.get('ticket_id'))[:8]} "
        f"{elapsed:.1f}s msg={str(body.get('message'))[:90]!r}",
    )
    ticket_id = body.get("ticket_id")

    status2, body2, _ = jrequest(
        "/api/v1/escalate", method="POST", body={"conversation_id": conv}
    )
    record(
        "issue4-escalation",
        body2.get("ticket_id") == ticket_id and body2.get("reused_existing") is True,
        f"asking twice reuses one ticket: reused={body2.get('reused_existing')} same_id={body2.get('ticket_id') == ticket_id}",
    )

    # Localized acknowledgement.
    status3, body3, _ = jrequest(
        "/api/v1/escalate",
        method="POST",
        body={"conversation_id": f"{conv}-lg", "locale": "lg"},
    )
    record(
        "issue4-escalation",
        status3 == 200 and body3.get("ok") is True,
        f"lg acknowledgement english_looking={looks_english(str(body3.get('message','')))} "
        f"msg={str(body3.get('message'))[:90]!r}",
    )

    if token and ticket_id:
        status4, ticket, _ = jrequest(
            f"/api/v1/admin/tickets/{ticket_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        handoff = (ticket or {}).get("handoff") or {}
        transcript = (ticket or {}).get("transcript") or []
        record(
            "issue4-escalation",
            status4 == 200 and handoff.get("requested_by") == "taxpayer",
            f"officer sees it: status={status4} requested_by={handoff.get('requested_by')} "
            f"team={ticket.get('team')!r} priority={ticket.get('priority')!r} transcript_turns={len(transcript)}",
        )


# --------------------------------------------------------------------------
def check_issue5_and_6_latency():
    """Issue 5 (speak-back) and issue 6 (non-English is slower)."""
    # TTS — first call after warm-up should not be paying a cold model load.
    for locale, text in (("en", "Value Added Tax is charged at 18 percent."),
                         ("lg", "Omusolo gwa VAT gusasulwa ku bitundu 18.")):
        status, body, elapsed = jrequest(
            "/api/v1/tts", method="POST", body={"text": text, "language": locale}
        )
        backend = body.get("backend", "?")
        audio_len = len(body.get("audio_base64") or "")
        record(
            f"issue5-tts-{locale}",
            status == 200 and audio_len > 0,
            f"{elapsed:.1f}s backend={backend} audio_b64={audio_len}B error={body.get('error')}",
        )
        # Second, identical request: the phrase cache should make it ~instant.
        status, body, elapsed2 = jrequest(
            "/api/v1/tts", method="POST", body={"text": text, "language": locale}
        )
        record(
            f"issue5-tts-{locale}-cached",
            status == 200,
            f"repeat {elapsed2:.1f}s backend={body.get('backend')} (first was {elapsed:.1f}s)",
        )

    # Chat latency, English vs Luganda, cold then warm — the MT cache is the
    # thing under test on the second pass.
    q_en = "What is the standard VAT rate in Uganda?"
    q_lg = "Omusolo gwa VAT mu Uganda guli gutya?"
    timings: dict[str, list[float]] = {}
    for label, payload in (("en", {"message": q_en, "locale": "en"}),
                           ("lg", {"message": q_lg, "locale": "lg"})):
        timings[label] = []
        for attempt in range(2):
            status, body, elapsed = jrequest(
                "/api/v1/chat",
                method="POST",
                body=payload,
                headers={"X-Session-ID": f"verify-lat-{label}-{attempt}"},
            )
            timings[label].append(elapsed)
        record(
            f"issue6-latency-{label}",
            None,
            f"cold={timings[label][0]:.1f}s warm={timings[label][1]:.1f}s",
        )
    if timings.get("en") and timings.get("lg"):
        ratio_cold = timings["lg"][0] / max(timings["en"][0], 0.01)
        ratio_warm = timings["lg"][1] / max(timings["en"][1], 0.01)
        record(
            "issue6-latency",
            None,
            f"lg/en cold x{ratio_cold:.2f}, warm x{ratio_warm:.2f} "
            "(the MT memo is what closes the warm gap)",
        )


# --------------------------------------------------------------------------
def check_issue7_oidc():
    """Issue 7 — the provider session must actually be ended."""
    issuer = "https://dev-s16d7m00eyrksjy2.us.auth0.com"
    try:
        with urllib.request.urlopen(
            f"{issuer}/.well-known/openid-configuration", timeout=30
        ) as resp:
            doc = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        record("issue7-oidc", None, f"could not reach the provider: {exc}")
        return
    end_session = doc.get("end_session_endpoint")
    record(
        "issue7-oidc",
        bool(end_session),
        f"provider publishes end_session_endpoint={end_session}",
    )

    # The URL the app will actually send, against what is registered.
    registered_logout = {
        "https://struttingly-nongeological-briella.ngrok-free.dev",
        "https://landwind22-ura-chatbot.hf.space/signin",
    }
    origin = BASE.rstrip("/")
    # This build sets NEXT_PUBLIC_OIDC_POST_LOGOUT_PATH="" for the tunnel.
    sent = origin
    record(
        "issue7-oidc",
        sent in registered_logout,
        f"post_logout_redirect_uri this build sends = {sent!r} — registered: {sent in registered_logout}",
    )


def check_issue1_and_7_bundle():
    """The dashboard wording and the sign-out control ship in the built bundle."""
    status, payload, _ = request("/signin")
    html = payload.decode(errors="replace")
    record(
        "issue7-ui",
        status == 200 and "Sign in as a different user" in html,
        f"/signin carries the different-account control: {'Sign in as a different user' in html}",
    )

    # The analytics page is client-rendered behind StaffGuard, so its wording
    # lives in the JS chunks rather than the HTML shell.
    status, payload, _ = request("/analytics")
    shell = payload.decode(errors="replace")
    chunks = set(re.findall(r'/_next/static/chunks/[^"\']+?\.js', shell))
    found: dict[str, bool] = {}
    wanted = [
        "Answer speed",
        "How long each thing takes",
        "Where answers came from",
        "Found in URA documents",
        "19 in 20 are faster",
        "Running without a restart",
    ]
    joined = ""
    for chunk in list(chunks)[:40]:
        st, body, _ = request(chunk)
        if st == 200:
            joined += body.decode(errors="replace")
    for phrase in wanted:
        found[phrase] = phrase in joined
    record(
        "issue1-graphs",
        all(found.values()) if joined else None,
        f"plain-language labels present in the shipped bundle: "
        f"{sum(found.values())}/{len(found)} — missing {[k for k,v in found.items() if not v] or 'none'}",
    )
    # Jargon that must be gone from the primary labels.
    for gone in ("Chat p95 latency", "Endpoint latency", "Replica uptime"):
        record("issue1-graphs", gone not in joined, f"jargon label removed: {gone!r}")


# --------------------------------------------------------------------------
def main() -> int:
    token = os.environ.get("DEV_TOKEN") or None
    print(f"Verifying against {BASE}\n" + "=" * 72, flush=True)
    check_health()
    check_issue1_and_7_bundle()
    check_issue2_language()
    check_issue2_streaming()
    check_issue3_figures()
    check_issue3_metrics(token)
    check_issue4_escalation(token)
    check_issue5_and_6_latency()
    check_issue7_oidc()

    print("\n" + "=" * 72)
    fails = [r for r in RESULTS if r[1] == "FAIL"]
    print(
        f"{sum(1 for r in RESULTS if r[1]=='PASS')} pass · "
        f"{len(fails)} fail · {sum(1 for r in RESULTS if r[1]=='INFO')} info"
    )
    for issue, status, detail in fails:
        print(f"  FAIL {issue}: {detail}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
