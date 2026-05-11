USE explain_agent;

ALTER TABLE explain_news_corpus
  CHANGE COLUMN tags_json tags JSON DEFAULT NULL;

ALTER TABLE explain_policy_corpus
  CHANGE COLUMN tags_json tags JSON DEFAULT NULL;

ALTER TABLE explain_research_corpus
  CHANGE COLUMN tags_json tags JSON DEFAULT NULL;
