"""Test-wide setup that must run before ``app`` is imported."""

import os
import secrets
import sys
from pathlib import Path

# `app.*` resolves to App/backend/app for every test here. Only agents/conftest.py
# did this, so a test outside that folder passed only when one inside it was
# collected first.
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# A dev token signed with the public default secret names no one but the portal
# demo taxpayer (ADR-0062), so the suite signs with a secret only this run knows.
os.environ.setdefault("AUTH_DEV_SECRET", secrets.token_urlsafe(32))
