"""免费 web 搜索 / 网页正文抓取 (无 API key, 走 DuckDuckGo HTML 端点)。

给 chat tools (web_search / web_read) 用, 让 agent 能取实时/事实性信息,
弥补底层模型"知识截止 + 无联网"的短板 (e.g. 最新非农数据、近期事件)。

为什么选 DuckDuckGo HTML 端点:
- 完全免费、无需 API key、无隐性计费 (用户明确要求)。
- 代价: 非官方 API, 可能限速 / 偶发结构变动; 失败时由调用方 (tools.py) 优雅
  降级成错误文本回给 LLM, 不让整轮 crash。
- 仅依赖项目已有的 httpx + beautifulsoup4, 不新增第三方依赖。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

_DDG_HTML = "https://html.duckduckgo.com/html/"
# 给一个常见浏览器 UA, 降低被当成 bot 直接拒的概率。
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_TIMEOUT = 15.0


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def _unwrap_ddg_url(href: str) -> str:
    """DDG 结果链接是 //duckduckgo.com/l/?uddg=<真实url> 跳转形式, 解出真实 URL。"""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        q = parse_qs(parsed.query)
        if q.get("uddg"):
            return q["uddg"][0]
    return href


def _parse_results(html: str, max_results: int) -> list[SearchResult]:
    """从 DDG HTML 解析结果列表 (抽出供测试直接喂 HTML, 不走网络)。"""
    soup = BeautifulSoup(html, "html.parser")
    results: list[SearchResult] = []
    for block in soup.select("div.result"):
        a = block.select_one("a.result__a")
        if not a:
            continue
        url = _unwrap_ddg_url(a.get("href", ""))
        title = a.get_text(" ", strip=True)
        snip_el = block.select_one(".result__snippet")
        snippet = snip_el.get_text(" ", strip=True) if snip_el else ""
        if url and title:
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= max_results:
            break
    return results


async def web_search(query: str, max_results: int = 5) -> list[SearchResult]:
    """搜 DuckDuckGo, 返回 [SearchResult]。

    网络/HTTP 错误向上抛 (httpx.HTTPError), 由 tools 层 catch 成文本给 LLM。
    """
    async with httpx.AsyncClient(
        timeout=_TIMEOUT, headers={"User-Agent": _UA}, follow_redirects=True
    ) as client:
        resp = await client.post(_DDG_HTML, data={"q": query})
        resp.raise_for_status()
    return _parse_results(resp.text, max_results)


def _extract_text(html: str, max_chars: int) -> str:
    """去脚本/样式/导航后取纯文本, 压缩空行, 截断到 max_chars。"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(
        ["script", "style", "noscript", "nav", "header", "footer", "aside", "form"]
    ):
        tag.decompose()
    raw = soup.get_text("\n", strip=True)
    lines = [ln for ln in (line.strip() for line in raw.splitlines()) if ln]
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... [truncated, 共 {len(text)} 字]"
    return text


async def web_read(url: str, max_chars: int = 4000) -> str:
    """抓取 URL 正文文本 (去脚本/样式/导航), 截断到 max_chars。"""
    async with httpx.AsyncClient(
        timeout=_TIMEOUT, headers={"User-Agent": _UA}, follow_redirects=True
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    return _extract_text(resp.text, max_chars)
