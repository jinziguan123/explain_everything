CREATE DATABASE IF NOT EXISTS explain_agent
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE explain_agent;

CREATE TABLE IF NOT EXISTS explain_session (
  session_id      VARCHAR(64)  NOT NULL,
  raw_question    TEXT         NOT NULL,
  domain_id       VARCHAR(64)  DEFAULT NULL,
  target          VARCHAR(128) DEFAULT NULL,
  time_window_start DATE       DEFAULT NULL,
  time_window_end   DATE       DEFAULT NULL,
  status          VARCHAR(16)  NOT NULL DEFAULT 'created',
  total_cost      DOUBLE       NOT NULL DEFAULT 0,
  created_at      DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  finished_at     DATETIME(6)  DEFAULT NULL,
  PRIMARY KEY (session_id),
  KEY idx_created_at (created_at),
  KEY idx_target (target)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS explain_evidence_tree (
  session_id   VARCHAR(64) NOT NULL,
  tree_json    LONGTEXT    NOT NULL,
  narrative    LONGTEXT    DEFAULT NULL,
  confidence   VARCHAR(8)  DEFAULT NULL,
  created_at   DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (session_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS explain_news_corpus (
  news_id        VARCHAR(64)  NOT NULL,
  url_hash       CHAR(64)     NOT NULL,
  source         VARCHAR(32)  NOT NULL,
  url            VARCHAR(1024) DEFAULT NULL,
  title          VARCHAR(512) NOT NULL,
  content        LONGTEXT     NOT NULL,
  published_at   DATETIME     NOT NULL,
  fetched_at     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  tags           JSON         DEFAULT NULL,
  snapshot_id    VARCHAR(64)  DEFAULT NULL,
  is_indexed     TINYINT(1)   NOT NULL DEFAULT 0,
  PRIMARY KEY (news_id),
  UNIQUE KEY uk_url_hash (url_hash),
  KEY idx_published_at (published_at),
  KEY idx_is_indexed (is_indexed)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS explain_policy_corpus (
  policy_id      VARCHAR(64)  NOT NULL,
  url_hash       CHAR(64)     NOT NULL,
  source         VARCHAR(32)  NOT NULL,
  url            VARCHAR(1024) DEFAULT NULL,
  title          VARCHAR(512) NOT NULL,
  content        LONGTEXT     NOT NULL,
  issued_at      DATE         NOT NULL,
  fetched_at     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  tags           JSON         DEFAULT NULL,
  snapshot_id    VARCHAR(64)  DEFAULT NULL,
  is_indexed     TINYINT(1)   NOT NULL DEFAULT 0,
  PRIMARY KEY (policy_id),
  UNIQUE KEY uk_url_hash (url_hash),
  KEY idx_issued_at (issued_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS explain_research_corpus (
  research_id    VARCHAR(64)  NOT NULL,
  url_hash       CHAR(64)     NOT NULL,
  source         VARCHAR(32)  NOT NULL,
  url            VARCHAR(1024) DEFAULT NULL,
  title          VARCHAR(512) NOT NULL,
  abstract       LONGTEXT     DEFAULT NULL,
  institution    VARCHAR(128) DEFAULT NULL,
  industry       VARCHAR(128) DEFAULT NULL,
  published_at   DATE         NOT NULL,
  fetched_at     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  tags           JSON         DEFAULT NULL,
  snapshot_id    VARCHAR(64)  DEFAULT NULL,
  is_indexed     TINYINT(1)   NOT NULL DEFAULT 0,
  PRIMARY KEY (research_id),
  UNIQUE KEY uk_url_hash (url_hash),
  KEY idx_published_at (published_at),
  KEY idx_industry (industry)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS explain_snapshot_blob (
  snapshot_id    VARCHAR(64)  NOT NULL,
  content_type   VARCHAR(32)  NOT NULL,
  storage_path   VARCHAR(1024) NOT NULL,
  size_bytes     BIGINT       NOT NULL,
  created_at     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (snapshot_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS explain_followup_history (
  followup_id    VARCHAR(64)  NOT NULL,
  session_id     VARCHAR(64)  NOT NULL,
  question       TEXT         NOT NULL,
  answer         LONGTEXT     DEFAULT NULL,
  intent         VARCHAR(32)  DEFAULT NULL,
  created_at     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (followup_id),
  KEY idx_session (session_id, created_at)
) ENGINE=InnoDB;
