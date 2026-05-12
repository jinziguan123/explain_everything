import hashlib
from datetime import date, datetime
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel


class ResearchItem(BaseModel):
    research_id: str
    url_hash: str
    source: str
    url: str | None
    title: str
    institution: str
    industry: str
    published_at: datetime
    q_type: int  # 1=行业研报, 2=宏观策略
    researcher: str
    info_code: str


def _url_hash(info_code: str) -> str:
    return hashlib.sha256(info_code.encode("utf-8")).hexdigest()


class EastmoneyResearchCrawler:
    """直接调东财底层 API, 绕过 akshare 的 per-symbol 封装。"""

    BASE_URL = "https://reportapi.eastmoney.com/report/list"

    def crawl(
        self,
        q_type: Literal[1, 2],
        begin: date,
        end: date,
        page_size: int = 50,
        max_pages: int = 5,
    ) -> list[ResearchItem]:
        out: list[ResearchItem] = []
        for page in range(1, max_pages + 1):
            params = {
                "industryCode": "*", "pageSize": str(page_size), "industry": "*",
                "rating": "*", "ratingChange": "*",
                "beginTime": begin.isoformat(), "endTime": end.isoformat(),
                "pageNo": str(page), "qType": str(q_type), "code": "*",
                "p": str(page), "pageNum": str(page), "pageNumber": str(page),
            }
            try:
                resp = httpx.get(self.BASE_URL, params=params, timeout=15.0)
                data = resp.json()
            except Exception:
                break

            rows = data.get("data") or []
            if not rows:
                break

            for row in rows:
                try:
                    info_code = row.get("infoCode") or ""
                    if not info_code:
                        continue
                    industry_name = row.get("industryName") or ""
                    if q_type == 2 and not industry_name:
                        industry_name = "宏观策略"

                    published_str = (row.get("publishDate") or "").split(".")[0]
                    if not published_str:
                        continue
                    published = datetime.strptime(published_str, "%Y-%m-%d %H:%M:%S")

                    out.append(ResearchItem(
                        research_id=str(uuid4()),
                        url_hash=_url_hash(info_code),
                        source="东方财富研报",
                        url=f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf",
                        title=row.get("title") or "",
                        institution=row.get("orgSName") or "",
                        industry=industry_name,
                        published_at=published,
                        q_type=q_type,
                        researcher=row.get("researcher") or "",
                        info_code=info_code,
                    ))
                except Exception:
                    continue  # 单条解析失败不影响整体

            if page >= int(data.get("TotalPage") or 0):
                break

        return out
