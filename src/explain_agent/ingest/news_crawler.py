import hashlib
from datetime import datetime
from uuid import uuid4
import akshare as ak
import pandas as pd
from pydantic import BaseModel


class NewsItem(BaseModel):
    news_id: str
    url_hash: str
    source: str
    url: str | None
    title: str
    content: str
    published_at: datetime


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


class AkshareNewsCrawler:
    def crawl_symbol(self, symbol_or_keyword: str) -> list[NewsItem]:
        try:
            df: pd.DataFrame = ak.stock_news_em(symbol=symbol_or_keyword)
        except Exception:
            return []
        if df is None or df.empty:
            return []

        df = df.copy()
        col_title = next(c for c in df.columns if "标题" in c)
        col_content = next((c for c in df.columns if "内容" in c), None)
        col_time = next(c for c in df.columns if "时间" in c)
        col_source = next((c for c in df.columns if "来源" in c), None)
        col_url = next((c for c in df.columns if "链接" in c), None)

        seen: set[str] = set()
        out: list[NewsItem] = []
        for _, row in df.iterrows():
            url = str(row.get(col_url, "") or "")
            url_h = _url_hash(url or str(row[col_title]) + str(row[col_time]))
            if url_h in seen:
                continue
            seen.add(url_h)
            out.append(
                NewsItem(
                    news_id=str(uuid4()),
                    url_hash=url_h,
                    source=str(row.get(col_source, "akshare")) if col_source else "akshare",
                    url=url or None,
                    title=str(row[col_title]),
                    content=str(row.get(col_content, "")) if col_content else "",
                    published_at=pd.to_datetime(row[col_time]).to_pydatetime(),
                )
            )
        return out
