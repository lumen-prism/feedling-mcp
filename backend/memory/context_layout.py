"""Shared IO memory context layout defaults.

Route B can import this directly. Route A resident consumer is an independent
deployment script, so it keeps local env-backed constants; tests enforce that
its defaults stay aligned with these contract values.
"""

LONG_TERM_MEMORY_INDEX_LIMIT_DEFAULT = 50
LONG_TERM_MEMORY_TOP_K_DEFAULT = 5
LONG_TERM_MEMORY_MAX_CHARS_DEFAULT = 1200

CONTEXT_SECTION_ORDER = (
    "identity",
    "long_term_memory",
    "recent_chat",
    "screen",
    "gps",
    "pending",
)
