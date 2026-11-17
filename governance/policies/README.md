# policies/

| Slot | State | Artifact |
| --- | --- | --- |
| Acceptable use | Elsewhere | Section 2 of [week04-privacy-and-aup.md](../../docs/capstone/week04-privacy-and-aup.md) states permitted uses, limitations, prohibited uses and the consequences of misuse. It stays in the capstone portfolio, which numbers its weekly records. No board has approved it. |
| Prohibited uses | Here | [prohibited_uses.md](prohibited_uses.md) maps each EU AI Act Article 5 practice to the system, and points to what taxpayers may not do (section 2.3 of the same file). |
| Risk appetite | Here | [risk-register.yaml](../../docs/security/threat-model/risk-register.yaml) scores each threat by CVSS, annual likelihood, financial impact and annual risk. [accepted-risks.yaml](../../docs/security/threat-model/accepted-risks.yaml) records each accepted risk, its approver and its review date. `threat-model/validate_threats.py` and `scripts/validate-risk-register.py` read both at these paths. [risk_appetite.md](risk_appetite.md) states the numbers CI, the SLOs and the alerts enforce. The product owner has not yet approved them. |
| Ethics charter | Elsewhere | [week02-ethics-charter.md](../../docs/capstone/week02-ethics-charter.md) states seven principles. The [harm register](../../docs/capstone/week03-harm-register.md) scores harms with the same method. |
