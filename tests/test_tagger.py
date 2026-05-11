import json
from unittest.mock import MagicMock

from explain_agent.ingest.tagger import NewsTagger


def test_tagger_parses_llm_json():
    fake_llm = MagicMock()
    fake_llm.chat.return_value = json.dumps({
        "industries": ["半导体"],
        "concepts": ["国产替代", "HBM"],
        "policy_type": None,
        "event_type": "产业链",
    })

    tagger = NewsTagger(llm=fake_llm)
    tags = tagger.tag(title="美国扩大对华芯片出口管制", content="...")
    assert "半导体" in tags["industries"]
    assert "国产替代" in tags["concepts"]
    assert tags["event_type"] == "产业链"


def test_tagger_handles_invalid_json():
    fake_llm = MagicMock()
    fake_llm.chat.return_value = "抱歉我不能回答"
    tagger = NewsTagger(llm=fake_llm)
    tags = tagger.tag(title="标题", content="内容")
    assert tags == {"industries": [], "concepts": [], "policy_type": None, "event_type": None}


def test_tagger_handles_json_with_markdown_fence():
    fake_llm = MagicMock()
    fake_llm.chat.return_value = '''```json
{"industries": ["医药"], "concepts": [], "policy_type": null, "event_type": null}
```'''
    tagger = NewsTagger(llm=fake_llm)
    tags = tagger.tag(title="t", content="c")
    assert tags["industries"] == ["医药"]
