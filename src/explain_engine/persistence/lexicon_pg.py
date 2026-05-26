"""Phase 17.1: PostgreSQL + pgvector 替代 variables.json lexicon 落地.

Public API (跟旧 lexicon.py signature 保持兼容, 由 Wave 4+ 落地):
- async def flush_to_lexicon(session, storage, llm=None, llm_canonical_top_k=3) -> int
- def get_lexicon_top_k_for_compress(storage, k=20) -> list[dict]
- def get_top_n_vars(storage, n) -> list[VariableNode]
- def _render_lexicon_for_prompt(vars_list) -> str

Internal (Wave 2):
- LexiconDBError 异常类
- _get_dsn / get_async_pool / get_sync_pool / verify_connection
- _insert_var / _find_var_by_id / _update_var_fitness / _list_vars_top_k / _delete_var
"""
from __future__ import annotations


class LexiconDBError(Exception):
    """PG 连接 / query 失败统一错误类."""
