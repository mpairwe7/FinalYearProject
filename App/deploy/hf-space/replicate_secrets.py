#!/usr/bin/env python3
"""Replicate the Crane Cloud app's runtime env into the HF Space as secrets,
giving the Space full parity (Sunbird speech + Gemini/Cloudflare translation+LLM).

Run it YOURSELF (it moves your own secrets to your own Space):

    ! python3 App/deploy/hf-space/replicate_secrets.py

Pure standard library — no huggingface_hub needed. Reads the HF write token from
.env (HF_TOKEN) and the Crane Cloud operator credentials from
~/.cranecloud/credentials. Prints only env-var NAMES and the Space build stage —
never any secret value.
"""
import json, os, ssl, urllib.request, urllib.error

REPO = "landwind22/ura-chatbot"
SPACE_URL = "https://landwind22-ura-chatbot.hf.space"
ENV = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))  # repo-root .env
CC_APP = "b01219c6-9555-41e2-84c3-f15d764fb938"
APPHOST = "ura-chatbot-6318a1b5.renu-01.cranecloud.io"   # valid-cert host (fallback routing)


def env_val(key):
    for line in open(ENV):
        s = line.strip()
        if s.startswith(key + "="):
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _req(url, *, headers, data=None, method="GET", timeout=60):
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.status, resp.read()


def cc(method, path, token=None, body=None):
    """Crane Cloud API. Try api.cranecloud.io directly; if its cert fails to
    verify (some networks route it to the RENU edge), fall back to the valid-cert
    app host and route via the Host header."""
    data = json.dumps(body).encode() if body is not None else None
    h = {}
    if token: h["Authorization"] = f"Bearer {token}"
    if body is not None: h["Content-Type"] = "application/json"
    try:
        s, b = _req(f"https://api.cranecloud.io{path}", headers=h, data=data, method=method)
        return s, json.loads(b)
    except (ssl.SSLError, urllib.error.URLError) as e:
        reason = getattr(e, "reason", None)
        if not isinstance(e, ssl.SSLError) and not isinstance(reason, ssl.SSLError):
            raise
    s, b = _req(f"https://{APPHOST}{path}", headers={**h, "Host": "api.cranecloud.io"},
                data=data, method=method)
    return s, json.loads(b)


def hf_add_secret(repo, token, key, value):
    data = json.dumps({"key": key, "value": value}).encode()
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        s, _ = _req(f"https://huggingface.co/api/spaces/{repo}/secrets", headers=h, data=data, method="POST")
        return s
    except urllib.error.HTTPError as e:
        return e.code


def main():
    hf_token = env_val("HF_TOKEN")
    if not hf_token:
        raise SystemExit("HF_TOKEN not found in .env")

    cred = json.load(open(os.path.expanduser("~/.cranecloud/credentials")))
    _, d = cc("POST", "/users/login", body=cred)
    tok = (d.get("data") or {}).get("access_token") or d.get("access_token")
    _, d = cc("GET", f"/apps/{CC_APP}", token=tok)
    cc_env = dict(((d.get("data") or {}).get("apps") or {}).get("env_vars") or {})

    merged = dict(cc_env)
    merged["CORS_ORIGINS"] = SPACE_URL    # Space origin for the browser UI
    merged["USE_DOH"] = "false"           # HF has native DNS; skip the DoH shim

    ok, fail = [], []
    for k, v in merged.items():
        if v is None:
            continue
        code = hf_add_secret(REPO, hf_token, k, str(v))
        (ok if code in (200, 201, 204) else fail).append(f"{k}({code})" if code not in (200, 201, 204) else k)
    print(f"secrets set OK ({len(ok)}): {sorted(ok)}")
    if fail:
        print(f"FAILED ({len(fail)}): {sorted(fail)}")

    # best-effort runtime stage
    try:
        s, b = _req(f"https://huggingface.co/api/spaces/{REPO}/runtime",
                    headers={"Authorization": f"Bearer {hf_token}"})
        print("space stage:", json.loads(b).get("stage"))
    except Exception:
        pass
    print("Space will rebuild/restart with the new secrets:", SPACE_URL)


if __name__ == "__main__":
    main()
