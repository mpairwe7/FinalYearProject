# plugins/ (Simulated URA & Government System Connectors)

Owns the six isolated local SQLite simulator connectors (`efris`, `digital_tax_stamps`, `ursb`, `bwims`, `tin_registration`, `payment_system`) and their safety boundary (`plugins/apps/common_ui.py`).

## Invariants

- **Strict Layer Isolation**: The six connector packages (`efris`, `digital_tax_stamps`, `ursb`, `bwims`, `tin_registration`, `payment_system`) and `plugins/apps/` must **never** import from `app.*` (`App/backend/app`). Only the top-level bridge (`plugins/base.py`, `plugins/orchestrator.py`) may reference `app.tools` when registering in-process tools.
- **Simulation Honesty (`live=False`)**: Every simulator response carries `X-Connector-Mode: simulation` and `X-Connector-Live: false`. Never claim a simulator record is a live URA, NIRA, URSB, or bank transaction.
- **Fail-Closed in Production**: When `APP_ENV=production`, `install_simulator_safety_boundary` refuses all non-`/health` requests with `503` (`tests/agents/test_plugin_apps_boundary.py`).
- **No Case-Colliding Symlinks**: Keep only canonical lowercase package directories (`efris`, `digital_tax_stamps`, `ursb`, `bwims`, `tin_registration`, `payment_system`).
