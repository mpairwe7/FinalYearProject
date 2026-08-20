# Threat Modeling Workshop -- Visual Tools

Visual threat modeling environment for the quarterly 60-minute workshops
described in Section 7 of the [threat model](../../threat-model.md).

## Quick Start

```bash
cd docs/security/threat-model/workshop

# Start OWASP Threat Dragon
docker compose up -d

# Open in browser
open http://localhost:8080    # macOS
xdg-open http://localhost:8080  # Linux
```

## Workshop Workflow

### Before the Workshop (Security Lead, 15 min)

1. Start Threat Dragon: `docker compose up -d`
2. Open `http://localhost:8080`
3. Import `ura-threat-dragon-model.json` (File > Import > select JSON)
4. Verify the DFD loads correctly with all components and threats
5. Screen-share the model during the workshop

### During the Workshop (60 min)

| Phase | Time | Tool Usage |
|-------|------|------------|
| **Open** | 0:00-0:05 | Show risk register metrics (from CI output / `scripts/validate-risk-register.py`) |
| **Update** | 0:05-0:15 | Edit DFD in Threat Dragon: add/remove/modify components, update trust boundaries |
| **Identify** | 0:15-0:30 | Right-click components in DFD → Add Threat → use STRIDE/MAESTRO/LINDDUN prompts |
| **Rate** | 0:30-0:45 | Set severity on each new threat in Threat Dragon. Use CVSS 4.0 calculator |
| **Assign** | 0:45-0:55 | Add mitigation notes to threats. Assign owner in description field |
| **Close** | 0:55-1:00 | Export model → overwrite `ura-threat-dragon-model.json` → commit |

### After the Workshop (Security Lead, 30 min)

1. Export updated model: File > Export > JSON → save as `ura-threat-dragon-model.json`
2. Transfer new threats to the YAML risk register:
   ```bash
   # Add to docs/security/threat-model/risk-register.yaml
   python3 scripts/validate-risk-register.py
   ```
3. Commit both the Threat Dragon JSON and updated YAML registers
4. Stop Threat Dragon: `docker compose down`

## Pre-Built Model

`ura-threat-dragon-model.json` contains:

- **Components**: Taxpayer Web Client, Admin/Staff UI, FastAPI Gateway, RAG Hybrid Retriever, Qdrant Vector DB, Qwen LLM Service, SQLite Analytics / Audit DB, URA Mock/Live Backend API
- **Trust Boundaries**: Public Internet, API Gateway Perimeter, Internal Backend VPC, LLM & Storage Subnet
- **Data Flows**: with protocols and TLS / auth methods labeled
- **Pre-loaded Threats**: mapped from the risk register (STRIDE, MAESTRO, LINDDUN, PASTA)
