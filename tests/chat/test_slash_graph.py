"""Phase 12: /graph slash handler test."""

import pytest

from explain_engine.chat.slash_commands import dispatch_slash


def _make_session_with_graph(sid: str, *, l0=1, l1=1):
    """Build a session with l0/l1 nodes. Uses _make_done_session pattern.

    sid must match s_[0-9a-f]{8} regex (SessionMeta validator).
    """
    from explain_engine.chat.session import ChatSession
    from explain_engine.schema.nodes import VariableNode
    from tests.test_chat_session import _make_done_session

    _make_done_session(sid)
    chat = ChatSession(sid)
    g = chat.state.graph
    for nid in list(g.nodes):
        g.remove_node(nid)
    for i in range(l0):
        g.add_node(VariableNode(
            id=f"p_{i+1:03d}", name=f"obs{i+1}", description="d",
            abstraction_level=0, confidence=0.85, epistemic="observation",
        ))
    for i in range(l1):
        g.add_node(VariableNode(
            id=f"c_{i+1:03d}", name=f"concept{i+1}", description="d",
            abstraction_level=1, confidence=0.78, epistemic="insight",
        ))
    return chat


class TestSlashGraph:
    @pytest.mark.asyncio
    async def test_empty_graph_returns_warning(self):
        """0 nodes → 不调 graphviz, 输 '(因果图为空, 无内容可渲染)'."""
        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session
        _make_done_session("s_a5e0b001")
        chat = ChatSession("s_a5e0b001")
        for nid in list(chat.state.graph.nodes):
            chat.state.graph.remove_node(nid)
        events = await dispatch_slash(chat, "/graph")
        assert events[0].type == "slash_graph"
        # Phase 15: 中文化错误文案
        assert "因果图为空" in events[0].content

    @pytest.mark.asyncio
    async def test_dot_missing_returns_friendly_error(self, monkeypatch):
        """dot binary 缺 → 友好 error + brew install 提示, 不 crash."""
        chat = _make_session_with_graph("s_a5e0b002")
        monkeypatch.setattr("shutil.which", lambda x: None if x == "dot" else "/fake")
        events = await dispatch_slash(chat, "/graph")
        assert events[0].type == "slash_graph"
        content = events[0].content.lower()
        assert "dot" in content
        assert "brew install graphviz" in content

    @pytest.mark.asyncio
    async def test_renders_via_chafa(self, monkeypatch):
        """Non-empty graph + chafa available → subprocess called with chafa."""
        chat = _make_session_with_graph("s_a5e0b003")

        def fake_which(x):
            if x == "dot":
                return "/usr/local/bin/dot"
            if x == "chafa":
                return "/usr/local/bin/chafa"
            return None
        monkeypatch.setattr("shutil.which", fake_which)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)

        # Mock graphviz render — write dummy PNG, don't shell to dot
        def fake_render(self, filename, **kwargs):
            png_path = str(filename) + "." + kwargs.get("format", "png")
            with open(png_path, "wb") as f:
                f.write(b"\x89PNG\r\n")
            return png_path

        monkeypatch.setattr("graphviz.Digraph.render", fake_render)

        # Mock subprocess.run capture inline call
        called: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            called.append(cmd)
            class Result:
                returncode = 0
                stdout = b""
                stderr = b""
            return Result()

        monkeypatch.setattr("subprocess.run", fake_run)

        events = await dispatch_slash(chat, "/graph")
        assert events[0].type == "slash_graph"
        assert any("chafa" in c[0] for c in called if c)

    @pytest.mark.asyncio
    async def test_no_renderer_outputs_path(self, monkeypatch):
        """No iTerm/Kitty/chafa → output PNG path + install hint, 不 crash."""
        chat = _make_session_with_graph("s_a5e0b004")
        monkeypatch.setattr(
            "shutil.which",
            lambda x: "/usr/local/bin/dot" if x == "dot" else None,
        )
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)

        def fake_render(self, filename, **kwargs):
            png_path = str(filename) + ".png"
            with open(png_path, "wb") as f:
                f.write(b"\x89PNG")
            return png_path

        monkeypatch.setattr("graphviz.Digraph.render", fake_render)

        events = await dispatch_slash(chat, "/graph")
        content = events[0].content
        assert "PNG saved" in content or "PNG:" in content
        assert "chafa" in content  # install hint

    @pytest.mark.asyncio
    async def test_output_contains_multisignal_footer(self, monkeypatch):
        """Output 末尾含 接受度评估 verdict (一致性 / 本质重要性 / 覆盖率)."""
        chat = _make_session_with_graph("s_a5e0b005")
        monkeypatch.setattr(
            "shutil.which",
            lambda x: "/usr/local/bin/dot" if x == "dot" else None,
        )
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)

        def fake_render(self, filename, **kwargs):
            png_path = str(filename) + ".png"
            with open(png_path, "wb") as f:
                f.write(b"\x89PNG")
            return png_path

        monkeypatch.setattr("graphviz.Digraph.render", fake_render)

        events = await dispatch_slash(chat, "/graph")
        content = events[0].content
        # Phase 15: footer 中文化 ("接受度评估: 一致性=...")
        assert "接受度评估" in content or "一致性" in content

    @pytest.mark.asyncio
    async def test_textual_mode_skips_inline_renderer_uses_detached_opener(
        self, monkeypatch
    ):
        """崩溃修: Textual 模式 (chat.tui_app 非 None) 不跑终端内联渲染器
        (imgcat/kitty/chafa) — 它们会污染 stdin 致 BlockingIOError 崩溃.
        改用脱离终端的 OS 看图器 (Popen start_new_session + std 流 DEVNULL).
        """
        chat = _make_session_with_graph("s_a5e0b007")
        # 标记 Textual 模式 (真实运行下由 tui_app.py set self.chat.tui_app = self)
        chat.tui_app = object()

        # dot + chafa + open 都"在" — 验证即便 chafa 可用也不被内联调用
        def fake_which(x):
            return f"/usr/local/bin/{x}" if x in ("dot", "chafa", "open") else None

        monkeypatch.setattr("shutil.which", fake_which)
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)

        def fake_render(self, filename, **kwargs):
            png_path = str(filename) + ".png"
            with open(png_path, "wb") as f:
                f.write(b"\x89PNG")
            return png_path

        monkeypatch.setattr("graphviz.Digraph.render", fake_render)

        # subprocess.run (内联渲染器) 绝不该被调用
        run_calls: list[list[str]] = []
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: run_calls.append(cmd),
        )
        # subprocess.Popen (脱离终端的看图器) 应被调用, 且带隔离 stdin/start_new_session
        popen_calls: list[tuple[list[str], dict]] = []

        def fake_popen(cmd, **kw):
            popen_calls.append((cmd, kw))
            return object()

        monkeypatch.setattr("subprocess.Popen", fake_popen)

        events = await dispatch_slash(chat, "/graph")

        assert events[0].type == "slash_graph"
        # 内联渲染器子进程 (会崩 textual) 没被跑
        assert run_calls == []
        # 用了脱离终端的看图器
        assert len(popen_calls) == 1
        cmd, kw = popen_calls[0]
        assert cmd[0] == "open"
        import subprocess as _sp
        assert kw.get("stdin") == _sp.DEVNULL
        assert kw.get("start_new_session") is True

    @pytest.mark.asyncio
    async def test_render_failure_returns_friendly_error(self, monkeypatch):
        """dg.render() 失败 (e.g. dot 跑挂 / permission / disk full) → 友好 error, 不 crash."""
        chat = _make_session_with_graph("s_a5e0b006")
        # dot binary "在" 但 render 失败
        monkeypatch.setattr(
            "shutil.which",
            lambda x: "/usr/local/bin/dot" if x == "dot" else None,
        )

        def fake_render_fail(self, filename, **kwargs):
            raise RuntimeError("dot subprocess returned 1: oom")

        monkeypatch.setattr("graphviz.Digraph.render", fake_render_fail)

        events = await dispatch_slash(chat, "/graph")
        assert events[0].type == "slash_graph"
        content = events[0].content.lower()
        # 不 crash, 输友好 error
        assert "render" in content or "dot" in content
        assert "failed" in content or "失败" in content
