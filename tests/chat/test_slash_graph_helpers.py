"""Phase 12: /graph helper tests (tmpdir / digraph builder / renderer detect)."""

import os
import shutil


class TestGetSessionTmpdir:
    def setup_method(self):
        """Reset module-global tmpdir before each test (isolation)."""
        import explain_engine.chat.slash_commands as sc
        if sc._SESSION_TMPDIR is not None:
            shutil.rmtree(sc._SESSION_TMPDIR, ignore_errors=True)
        sc._SESSION_TMPDIR = None

    def test_lazy_init_first_call_creates(self):
        from explain_engine.chat.slash_commands import _get_session_tmpdir
        tmpdir = _get_session_tmpdir()
        assert os.path.isdir(tmpdir)
        assert "explain_graph_" in tmpdir

    def test_second_call_reuses(self):
        from explain_engine.chat.slash_commands import _get_session_tmpdir
        tmpdir1 = _get_session_tmpdir()
        tmpdir2 = _get_session_tmpdir()
        assert tmpdir1 == tmpdir2

    def test_atexit_registered(self, monkeypatch):
        """First call 应 atexit.register(shutil.rmtree, tmpdir, ignore_errors=True)."""
        import explain_engine.chat.slash_commands as sc

        captured: list[tuple] = []

        def fake_register(func, *args, **kwargs):
            captured.append((func, args, kwargs))

        # patch atexit.register on the slash_commands aliased import (_atexit.register)
        monkeypatch.setattr(sc._atexit, "register", fake_register)
        sc._SESSION_TMPDIR = None  # force re-init

        from explain_engine.chat.slash_commands import _get_session_tmpdir
        tmpdir = _get_session_tmpdir()

        assert len(captured) == 1
        func, args, kwargs = captured[0]
        assert func is shutil.rmtree
        assert args == (tmpdir,)
        assert kwargs == {"ignore_errors": True}
