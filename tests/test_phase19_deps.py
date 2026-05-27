"""Phase 19 Wave 3 Task 12: textual + pyfiglet 依赖可 import.

只验 import + 一次最小调用. textual / pyfiglet 的 API 细节由后续 task 验.
"""


def test_textual_importable() -> None:
    """textual.app + textual.widgets 可 import."""
    import textual.app
    import textual.widgets

    assert hasattr(textual.app, "App")
    assert hasattr(textual.widgets, "Input")
    assert hasattr(textual.widgets, "RichLog")


def test_pyfiglet_importable() -> None:
    """pyfiglet 可 import + figlet_format 跑通."""
    import pyfiglet

    out = pyfiglet.figlet_format("test", font="standard")
    assert isinstance(out, str)
    assert out.strip() != ""
