from datetime import datetime
from uuid import uuid4
from explain_agent.core.types import AdapterQuery, Evidence


class IndustryResolver:
    """按 sw_l1 → sw_l2 → sw_l3 精确匹配；不命中则用 LIKE 模糊匹配三级。"""

    def __init__(self, engine):
        self.engine = engine

    def resolve_industry_symbols(self, industry_name: str) -> list[int]:
        pairs = self.resolve_industry_symbols_with_codes(industry_name)
        return [sid for sid, _ in pairs]

    def resolve_industry_symbols_with_codes(self, industry_name: str) -> list[tuple[int, str]]:
        for level_col in ("sw_l1", "sw_l2", "sw_l3"):
            pairs = self._query_by_clause(
                f"ic.{level_col} = %(name)s", {"name": industry_name}
            )
            if pairs:
                return pairs
        for level_col in ("sw_l1", "sw_l2", "sw_l3"):
            pairs = self._query_by_clause(
                f"ic.{level_col} LIKE %(pat)s", {"pat": f"%{industry_name}%"}
            )
            if pairs:
                return pairs
        return []

    def _query_by_clause(self, where_clause: str, params: dict) -> list[tuple[int, str]]:
        sql = f"""
        SELECT s.symbol_id, s.symbol
        FROM quant_data.fr_industry_current ic
        JOIN quant_data.stock_symbol s
          ON s.symbol = ic.symbol OR s.code = ic.symbol
        WHERE {where_clause} AND s.is_active = 1
        """
        with self.engine.begin() as conn:
            rows = conn.exec_driver_sql(sql, params).fetchall()
        return [(r[0], r[1]) for r in rows]

    def resolve_symbol_meta(self, symbol_ids: list[int]) -> dict[int, tuple[str, str]]:
        """批量查 symbol_id → (code, name)，便于把 symbol_id 翻译成人类可读形式。"""
        if not symbol_ids:
            return {}
        from sqlalchemy import text, bindparam
        sql = """
        SELECT symbol_id, COALESCE(code, symbol) AS code, COALESCE(name, symbol) AS name
        FROM quant_data.stock_symbol
        WHERE symbol_id IN :ids
        """
        stmt = text(sql).bindparams(bindparam("ids", expanding=True))
        with self.engine.begin() as conn:
            rows = conn.execute(stmt, {"ids": list(symbol_ids)}).fetchall()
        return {int(r[0]): (str(r[1]), str(r[2])) for r in rows}


class ClickHouseMarketAdapter:
    name = "clickhouse_market"

    def __init__(self, ch_client, industry_resolver):
        self.ch = ch_client
        self.resolver = industry_resolver

    async def query(self, q: AdapterQuery) -> list[Evidence]:
        symbol_ids = self.resolver.resolve_industry_symbols(q.target)
        if not symbol_ids:
            return []

        sql = """
        SELECT
            symbol_id,
            argMax(close, trade_date) AS last_close,
            (argMax(close, trade_date) / argMin(close, trade_date) - 1) * 100 AS pct_chg,
            sum(amount_k) * 1000 AS total_amount
        FROM stock_bar_1d
        WHERE symbol_id IN %(ids)s
          AND trade_date BETWEEN %(start)s AND %(end)s
        GROUP BY symbol_id
        ORDER BY pct_chg DESC
        LIMIT 20
        """
        result = self.ch.query(
            sql,
            parameters={
                "ids": tuple(symbol_ids),
                "start": q.time_window[0],
                "end": q.time_window[1],
            },
        )
        rows = result.result_rows
        if not rows:
            return []

        leaders = rows[:3]
        meta: dict[int, tuple[str, str]] = {}
        try:
            raw_meta = self.resolver.resolve_symbol_meta([int(r[0]) for r in leaders])
            if isinstance(raw_meta, dict):
                meta = raw_meta
        except Exception:
            pass
        leader_parts = []
        for r in leaders:
            sid = int(r[0])
            entry = meta.get(sid)
            if entry and isinstance(entry, tuple) and len(entry) == 2:
                code, name = entry
            else:
                code, name = str(sid), f"id={sid}"
            leader_parts.append(f"{name}({code}) 涨跌={r[2]:.2f}%")
        leader_snippet = "; ".join(leader_parts)
        return [
            Evidence(
                id=str(uuid4()),
                source=self.name,
                source_type="market_data",
                snippet=f"{q.target} 板块 {q.time_window[0]}~{q.time_window[1]} 龙头股: {leader_snippet}",
                raw_payload={"rows": [list(r) for r in rows]},
                timestamp=datetime.now(),
                metadata={"target": q.target, "kind": "industry_leaders"},
            )
        ]
