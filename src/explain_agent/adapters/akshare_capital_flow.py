from datetime import datetime
from uuid import uuid4
import akshare as ak
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from explain_agent.core.types import AdapterQuery, Evidence


class AkshareCapitalFlowAdapter:
    name = "akshare_capital_flow"

    async def query(self, q: AdapterQuery) -> list[Evidence]:
        return self._industry_main_flow(q)

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
        retry=retry_if_exception_type(ConnectionError),
        reraise=False,
    )
    def _fetch_df(self, target: str) -> pd.DataFrame | None:
        try:
            return ak.stock_sector_fund_flow_hist(symbol=target)
        except ConnectionError:
            raise
        except Exception:
            return None

    def _industry_main_flow(self, q: AdapterQuery) -> list[Evidence]:
        try:
            df = self._fetch_df(q.target)
        except Exception:
            return []
        if df is None or df.empty:
            return []

        df = df.copy()
        if "日期" in df.columns:
            df["日期"] = pd.to_datetime(df["日期"]).dt.date
            mask = (df["日期"] >= q.time_window[0]) & (df["日期"] <= q.time_window[1])
            df = df[mask]
        if df.empty:
            return []

        net_col = next((c for c in df.columns if "主力净流入-净额" in c), None)
        pct_col = next((c for c in df.columns if "主力净流入-净占比" in c), None)
        if net_col is None:
            return []

        total = df[net_col].sum()
        avg_pct = df[pct_col].mean() if pct_col else 0
        sign = "净流入" if total > 0 else "净流出"
        snippet = (
            f"{q.target} 板块在 {q.time_window[0]}~{q.time_window[1]} 期间主力{sign}"
            f"累计 {abs(total)/1e8:.2f} 亿，平均日占比 {avg_pct:.2f}%"
        )
        return [
            Evidence(
                id=str(uuid4()),
                source=self.name,
                source_type="capital_flow",
                snippet=snippet,
                raw_payload={"records": df.to_dict(orient="records")},
                timestamp=datetime.now(),
                metadata={"target": q.target, "kind": "industry_main_flow"},
            )
        ]
