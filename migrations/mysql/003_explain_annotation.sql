USE explain_agent;

CREATE TABLE IF NOT EXISTS explain_annotation (
  annotation_id    VARCHAR(64)  NOT NULL,
  session_id       VARCHAR(64)  NOT NULL,
  thread_index     INT          NOT NULL,
  thread_title     VARCHAR(256) NOT NULL,
  label            VARCHAR(16)  NOT NULL,
  note             TEXT         DEFAULT NULL,
  created_at       DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (annotation_id),
  KEY idx_session (session_id),
  KEY idx_label (label),
  UNIQUE KEY uk_session_thread (session_id, thread_index)
) ENGINE=InnoDB;
