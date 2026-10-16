# threat-model/ (Threat Model as Code & STRIDE / OWASP / MITRE ATLAS Gate)

Owns the executable threat model (`tm.py`) and the CI threat-registry validator (`validate_threats.py`).

## Layout

| File | Role |
| --- | --- |
| `tm.py` | `pytm` architecture definition generating DFDs and sequence diagrams |
| `validate_threats.py` | Validates `THREAT_REGISTRY` against STRIDE, OWASP LLM Top 10, MITRE ATLAS, and code evidence |

## Invariants

- `threat-model/` must remain independent of the FastAPI runtime (`never import app.*`) so CI can validate threat mitigations statically without booting the server.
- Every threat in `THREAT_REGISTRY` must cite concrete repository file paths under `evidence` that exist on disk (`python3 threat-model/validate_threats.py`).
