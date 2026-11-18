"""Test-wide setup that must run before ``app`` is imported."""

import os
import secrets

# A dev token signed with the public default secret names no one but the portal
# demo taxpayer (ADR-0062), so the suite signs with a secret only this run knows.
os.environ.setdefault("AUTH_DEV_SECRET", secrets.token_urlsafe(32))
