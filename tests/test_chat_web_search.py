"""web_search / web_read 工具 + 解析逻辑测试 (全离线, 不打真实网络)。

- 解析层 (_parse_results / _extract_text / _unwrap_ddg_url) 直接喂 HTML, 纯函数。
- 工具层 (web_search_tool / web_read_tool) monkeypatch 掉网络函数, 验证格式化
  与异常优雅降级 (失败返回文本而非抛)。
"""

import pytest

from explain_engine.chat import web_search as ws
from explain_engine.chat.tools import ToolContext, web_read_tool, web_search_tool
from explain_engine.schema.state import CognitiveState


def _ctx() -> ToolContext:
    # web 工具不读 state, 给个最小 bootstrap 即可
    state = CognitiveState.bootstrap("q", budget=10)
    return ToolContext(state=state, llm=None)


# ───────────────────────── 解析层 ─────────────────────────

class TestParse:
    def test_parse_results_extracts_title_url_snippet(self) -> None:
        html = (
            '<div class="result"><a class="result__a" '
            'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">'
            'Title A</a><a class="result__snippet">Snippet A</a></div>'
            '<div class="result"><a class="result__a" href="https://b.com">'
            'Title B</a><div class="result__snippet">Snip B</div></div>'
        )
        rs = ws._parse_results(html, 5)
        assert len(rs) == 2
        assert rs[0].url == "https://example.com/a"  # uddg 解包
        assert rs[0].title == "Title A"
        assert rs[0].snippet == "Snippet A"
        assert rs[1].url == "https://b.com"

    def test_parse_results_respects_max(self) -> None:
        html = '<div class="result"><a class="result__a" href="https://x.com">T</a></div>' * 5
        assert len(ws._parse_results(html, 2)) == 2

    def test_unwrap_handles_plain_url(self) -> None:
        assert ws._unwrap_ddg_url("https://plain.com/p") == "https://plain.com/p"

    def test_extract_text_strips_script_nav(self) -> None:
        html = (
            "<html><body><script>var x=1</script><nav>menu</nav>"
            "<p>Hello</p><p>World</p><footer>foot</footer></body></html>"
        )
        text = ws._extract_text(html, 1000)
        assert "Hello" in text and "World" in text
        assert "menu" not in text and "var x" not in text and "foot" not in text

    def test_extract_text_truncates(self) -> None:
        html = "<p>" + ("a" * 100) + "</p>"
        out = ws._extract_text(html, 20)
        assert out.startswith("a" * 20)
        assert "truncated" in out


# ───────────────────────── 工具层 ─────────────────────────

class TestWebSearchTool:
    @pytest.mark.asyncio
    async def test_formats_results(self, monkeypatch) -> None:
        async def fake_search(query, max_results=5):
            return [ws.SearchResult("标题", "https://u.com", "摘要文本")]

        monkeypatch.setattr(ws, "web_search", fake_search)
        out = await web_search_tool.call(
            web_search_tool.input_schema(query="非农数据"), _ctx()
        )
        assert "标题" in out and "https://u.com" in out and "摘要文本" in out

    @pytest.mark.asyncio
    async def test_empty_results(self, monkeypatch) -> None:
        async def fake_search(query, max_results=5):
            return []

        monkeypatch.setattr(ws, "web_search", fake_search)
        out = await web_search_tool.call(
            web_search_tool.input_schema(query="zzz"), _ctx()
        )
        assert "无结果" in out

    @pytest.mark.asyncio
    async def test_network_error_degrades_to_text(self, monkeypatch) -> None:
        async def boom(query, max_results=5):
            raise RuntimeError("network down")

        monkeypatch.setattr(ws, "web_search", boom)
        out = await web_search_tool.call(
            web_search_tool.input_schema(query="x"), _ctx()
        )
        assert "web_search 失败" in out and "network down" in out


class TestWebReadTool:
    @pytest.mark.asyncio
    async def test_returns_page_text(self, monkeypatch) -> None:
        async def fake_read(url, max_chars=4000):
            return "正文内容"

        monkeypatch.setattr(ws, "web_read", fake_read)
        out = await web_read_tool.call(
            web_read_tool.input_schema(url="https://u.com"), _ctx()
        )
        assert "正文内容" in out and "https://u.com" in out

    @pytest.mark.asyncio
    async def test_error_degrades_to_text(self, monkeypatch) -> None:
        async def boom(url, max_chars=4000):
            raise RuntimeError("404")

        monkeypatch.setattr(ws, "web_read", boom)
        out = await web_read_tool.call(
            web_read_tool.input_schema(url="https://u.com"), _ctx()
        )
        assert "web_read 失败" in out
