from datetime import datetime
from pathlib import Path
from uuid import uuid4


class SnapshotStore:
    """把网页正文落到本地磁盘 + MySQL 指针，让 citation 链接失效后仍能追溯。"""

    def __init__(self, base_dir, engine):
        self.base_dir = Path(base_dir)
        self.engine = engine

    def save(self, content: str, content_type: str = "news") -> str | None:
        """落正文到 {base_dir}/{yyyy/mm/dd}/{snap_id}.txt + INSERT blob 表。
        返回 snapshot_id；content 为空时返回 None 不落盘。
        """
        if not content:
            return None

        snap_id = f"snap_{uuid4().hex[:16]}"
        now = datetime.now()
        sub_dir = self.base_dir / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}"
        sub_dir.mkdir(parents=True, exist_ok=True)
        path = sub_dir / f"{snap_id}.txt"
        path.write_text(content, encoding="utf-8")
        size = path.stat().st_size

        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                """
                INSERT INTO explain_agent.explain_snapshot_blob
                  (snapshot_id, content_type, storage_path, size_bytes, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (snap_id, content_type, str(path), size, now),
            )
        return snap_id

    def load(self, snapshot_id: str) -> str | None:
        """根据 snapshot_id 读回正文，不存在返回 None。"""
        if not snapshot_id:
            return None
        for path in self.base_dir.rglob(f"{snapshot_id}.txt"):
            return path.read_text(encoding="utf-8")
        return None
