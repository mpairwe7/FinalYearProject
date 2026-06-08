"""Cloud-provider fallback layer (Cloudflare Workers AI / Vectorize / R2 + Gemini).

All inference providers route through one Cloudflare AI Gateway and are gated by
per-channel circuit breakers + free-tier budget guards, so a fallback fires only
when the primary is down or over budget — matching the app's existing graceful
degradation philosophy. See ``gateway.py`` for the secret-safe two-credential
header pattern. Importing this package never requires any cloud SDK or key; each
provider self-disables when unconfigured (``config.is_*_configured()``).
"""
