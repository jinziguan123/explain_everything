import sys
from pathlib import Path
from sqlalchemy import create_engine
from explain_agent.config import get_settings


def main() -> int:
    s = get_settings()
    migrations_dir = Path(__file__).parent.parent / "migrations" / "mysql"
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        print("no migration files")
        return 1

    bootstrap_url = (
        f"mysql+pymysql://{s.mysql_user}:{s.mysql_password}"
        f"@{s.mysql_host}:{s.mysql_port}/?charset=utf8mb4"
    )
    engine = create_engine(bootstrap_url)

    with engine.begin() as conn:
        for f in files:
            print(f"applying {f.name}...")
            sql = f.read_text(encoding="utf-8")
            for stmt in [x.strip() for x in sql.split(";") if x.strip()]:
                conn.exec_driver_sql(stmt)
    print("migrations done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
