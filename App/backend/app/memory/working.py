"""The expiry ``MemoryService.update_working`` gives a taxpayer's working memory.

Working state is one row per tenant and taxpayer in the shared analytics
backend (``db.upsert_working_memory``), so reads and erasure behave the same in
every worker. The resolution ledger writes the same row with its own TTL
(``RESOLUTION_CASE_TTL_HOURS``), and the last write sets the row's expiry.
"""

WORKING_TTL_SECONDS = 30 * 60
