# Incident log

Production incidents: what broke, when, and what fixed it. Each row links the
record that holds the evidence. Add a row when a production fault reaches
taxpayers or staff, in the pull request that fixes it, or as soon as it is
found if no fix is ready.

[incident_response_sim.json](../../../Results/incident_response_sim.json) is the
output of a drill (`App/scripts/incident_response_sim.py`), not a production
incident.

| Found | What broke | Impact | Fix | State | Record |
| --- | --- | --- | --- | --- | --- |
| 2026-11-09 | Both Sunbird AI accounts answer 403 `ACCOUNT_SUSPENDED` on every endpoint. | Answer generation, speech recognition, text-to-speech and translation through Sunbird fail. Answers fall to Workers AI, the tier behind Sunbird. | The account owner must contact Sunbird AI. | Open | G539 in [GAPS](../../../docs/GAPS_AND_AGENTIC_ROADMAP.md), found while building [ADR-0036](../../../docs/adr/0036-sunbird-primary-answer-generation.md) |
| 2026-11-09 | Gemini, then the cloud primary, refused its IP-restricted key (`API_KEY_IP_ADDRESS_BLOCKED`), and the Workers AI relay answered 502. | Every answer on the Space was a raw passage with a "could not write a tailored answer" notice. | Sunflower through Sunbird leads, Workers AI is called directly when the relay fails, and Gemini no longer generates. | Fixed 2026-11-09 | G537, [ADR-0036](../../../docs/adr/0036-sunbird-primary-answer-generation.md) |
| 2026-09-02 | PAYE questions asked in everyday words fell through to retrieval and were answered from superseded handbooks. | The tax-free monthly salary was quoted as UGX 235,000 instead of 335,000. | Rate lookups no longer need a rate word and a named tax to reach the calculator. | Fixed by 2026-09-02 | G44 in [GAPS](../../../docs/GAPS_AND_AGENTIC_ROADMAP.md) |
