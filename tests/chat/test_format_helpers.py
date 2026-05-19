"""Phase 12 (2026-05-19): /show + /graph detail helpers test."""


class TestFormatEpiShort:
    def test_fact(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        assert _format_epi_short("fact") == "fact"

    def test_observation(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        assert _format_epi_short("observation") == "obs"

    def test_inference(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        assert _format_epi_short("inference") == "inf"

    def test_insight(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        assert _format_epi_short("insight") == "ins"

    def test_speculation(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        assert _format_epi_short("speculation") == "spec"

    def test_unknown_returns_input(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        # 防御: 未知 epi 返原值 (新加 Epistemic literal 时不 crash)
        assert _format_epi_short("emerging") == "emerging"
