"""The URA chatbot backend."""

import os

# APP_ENV is read in 41 places, in several forms, and only a few of them strip it.
# "Production " from a secrets form read as production in those few and as development
# in the rest, which skipped the startup validation and the forced-on flags (G687).
# Normalised once here, before any other module of the package is imported.
if "APP_ENV" in os.environ:
    os.environ["APP_ENV"] = os.environ["APP_ENV"].strip().lower()
