"""How long a conversation's working memory lives.

:class:`~app.memory.service.MemoryService` keeps working state in the shared
analytics backend (``db.upsert_working_memory``), so reads and erasure behave
the same in every worker.
"""

WORKING_TTL_SECONDS = 30 * 60
