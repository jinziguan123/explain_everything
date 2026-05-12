import pytest
from explain_agent.graph.framework_loader import load_framework, FrameworkError


def test_load_cn_equity_framework():
    fw = load_framework("cn_equity_sector_attribution")
    assert fw["domain_id"] == "cn_equity_sector_attribution"
    assert len(fw["dimensions"]) == 6
    dim_ids = {d["id"] for d in fw["dimensions"]}
    assert dim_ids == {"policy", "industry_chain", "capital_flow",
                       "sentiment_event", "international", "technical"}
    assert fw["worker_config"]["max_rounds"] == 10
    assert fw["worker_config"]["max_concurrency"] == 6


def test_load_unknown_domain_raises():
    with pytest.raises(FrameworkError):
        load_framework("nonexistent_domain")


def test_load_from_explicit_path(tmp_path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("""
domain_id: test
display_name: test
match_patterns: []
dimensions:
  - id: a
    name: A
    priority: 1
    data_sources: [foo]
    query_template: "test"
worker_config:
  max_rounds: 5
  soft_terminate_no_gain_rounds: 1
  max_concurrency: 1
models:
  parse: weak
""", encoding="utf-8")
    fw = load_framework("test", search_paths=[tmp_path])
    assert fw["domain_id"] == "test"


def test_policy_dim_includes_research_corpus():
    fw = load_framework("cn_equity_sector_attribution")
    policy = next(d for d in fw["dimensions"] if d["id"] == "policy")
    assert "research_corpus" in policy["data_sources"]


def test_industry_chain_dim_includes_research_corpus():
    fw = load_framework("cn_equity_sector_attribution")
    ic = next(d for d in fw["dimensions"] if d["id"] == "industry_chain")
    assert "research_corpus" in ic["data_sources"]


def test_international_dim_includes_research_corpus():
    fw = load_framework("cn_equity_sector_attribution")
    intl = next(d for d in fw["dimensions"] if d["id"] == "international")
    assert "research_corpus" in intl["data_sources"]
