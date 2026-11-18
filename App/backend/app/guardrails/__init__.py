"""OWASP LLM Top 10 (2025) controls, one module per job.

- ``input_checks``: prompt injection, direct and indirect (LLM01): ``InputGuard``, ``scan_*_text``
- ``fences``: the spotlight fences that keep retrieved and uploaded text as data
- ``pii``: Ugandan personal data patterns, redaction and the streaming DLP filter (LLM02)
- ``output_checks``: ``OutputGuard`` (grounding, abstention, escalation, prompt leakage, LLM05/07/09),
  the figure release gate and link provenance
- ``normalize``: Unicode canonicalisation both sides share
- ``premise``, ``friction``, ``pdf``: false-premise checks, friction against adaptive probing, PDF guards
"""
