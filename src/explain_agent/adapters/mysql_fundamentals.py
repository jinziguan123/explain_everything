from datetime import datetime
from uuid import uuid4
from explain_agent.core.types import AdapterQuery, Evidence


class MySQLFundamentalsAdapter:
    name = "mysql_fundamentals"

    def __init__(self, engine, industry_resolver):
        self.engine = engine
        self.resolver = industry_resolver

    async def query(self, q: AdapterQuery) -> list[Evidence]:
        pairs = self.resolver.resolve_industry_symbols_with_codes(q.target)
        if not pairs:
            return []
        symbols = [s for _, s in pairs]
        placeholders = ",".join(["%s"] * len(symbols))

        sql = f"""
        SELECT
            symbol,
            roe_avg,
            np_margin,
            gp_margin,
            net_profit,
            report_date
        FROM quant_data.fr_fundamental_profit
        WHERE symbol IN ({placeholders})
          AND announcement_date BETWEEN %s AND %s
        ORDER BY announcement_date DESC, roe_avg DESC
        LIMIT 50
        """
        params = (*symbols, q.time_window[0], q.time_window[1])
        with self.engine.begin() as conn:
            rows = conn.exec_driver_sql(sql, params).fetchall()
        if not rows:
            return []

        valid_roe = [r[1] for r in rows if r[1] is not None]
        valid_np = [r[2] for r in rows if r[2] is not None]
        avg_roe = sum(valid_roe) / len(valid_roe) if valid_roe else 0
        avg_np_margin = sum(valid_np) / len(valid_np) if valid_np else 0
        top3 = rows[:3]
        top_snippet = "; ".join(f"{r[0]} ROE={float(r[1])*100:.2f}%" for r in top3)
        return [
            Evidence(
                id=str(uuid4()),
                source=self.name,
                source_type="market_data",
                snippet=(
                    f"{q.target} 行业近期基本面快照（{len(rows)} 家公司）："
                    f"平均 ROE {float(avg_roe)*100:.2f}%、平均净利率 {float(avg_np_margin)*100:.2f}%；"
                    f"ROE Top 3: {top_snippet}"
                ),
                raw_payload={"rows": [list(r) for r in rows]},
                timestamp=datetime.now(),
                metadata={"target": q.target, "kind": "industry_fundamentals"},
            )
        ]
