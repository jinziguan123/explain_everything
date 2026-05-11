import pytest
from sqlalchemy import inspect
from explain_agent.db.mysql import get_engine


@pytest.mark.integration
def test_explain_tables_exist():
    engine = get_engine("explain")
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    expected = {
        "explain_session",
        "explain_evidence_tree",
        "explain_news_corpus",
        "explain_policy_corpus",
        "explain_research_corpus",
        "explain_snapshot_blob",
        "explain_followup_history",
    }
    missing = expected - tables
    assert not missing, f"missing tables: {missing}"
