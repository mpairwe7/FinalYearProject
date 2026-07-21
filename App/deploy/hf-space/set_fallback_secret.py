#!/usr/bin/env python3
"""Set SUNBIRD_FALLBACK_API_TOKEN as a secret on the HF Space (for Sunbird
primary->fallback resilience). Run it YOURSELF so the secret stays your action:

    ! python3 App/deploy/hf-space/set_fallback_secret.py

Pure standard library. Reads SUNBIRD_FALLBACK_API_TOKEN + HF_TOKEN from .env and
POSTs the secret to the Space. Prints only the key name and HTTP status — never
the value.
"""
import json, os, urllib.request, urllib.error

REPO = "landwind22/ura-chatbot"
ENV = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))


def env_val(key):
    for line in open(ENV):
        s = line.strip()
        if s.startswith(key + "="):
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def main():
    hf = env_val("HF_TOKEN")
    val = env_val("SUNBIRD_FALLBACK_API_TOKEN")
    if not hf or not val:
        raise SystemExit("HF_TOKEN or SUNBIRD_FALLBACK_API_TOKEN missing from .env")
    data = json.dumps({"key": "SUNBIRD_FALLBACK_API_TOKEN", "value": val}).encode()
    req = urllib.request.Request(
        f"https://huggingface.co/api/spaces/{REPO}/secrets", data=data, method="POST",
        headers={"Authorization": f"Bearer {hf}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            print(f"SUNBIRD_FALLBACK_API_TOKEN set on {REPO}: HTTP {r.status}")
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read()[:160]}")


if __name__ == "__main__":
    main()
