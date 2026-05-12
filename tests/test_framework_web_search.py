from explain_agent.graph.framework_loader import load_framework


def test_policy_dim_includes_web_search():
    fw = load_framework("cn_equity_sector_attribution")
    policy = next(d for d in fw["dimensions"] if d["id"] == "policy")
    assert "web_search" in policy["data_sources"]


def test_sentiment_event_dim_includes_web_search():
    fw = load_framework("cn_equity_sector_attribution")
    se = next(d for d in fw["dimensions"] if d["id"] == "sentiment_event")
    assert "web_search" in se["data_sources"]


def test_international_dim_includes_web_search():
    fw = load_framework("cn_equity_sector_attribution")
    intl = next(d for d in fw["dimensions"] if d["id"] == "international")
    assert "web_search" in intl["data_sources"]


def test_industry_chain_dim_NOT_include_web_search():
    fw = load_framework("cn_equity_sector_attribution")
    ic = next(d for d in fw["dimensions"] if d["id"] == "industry_chain")
    assert "web_search" not in ic["data_sources"]
