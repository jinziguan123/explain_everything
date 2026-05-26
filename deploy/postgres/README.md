# explain-engine PostgreSQL 部署

Phase 17.1 引入 — lexicon (cross-session variable 库) 从 local `variables.json` 迁到远程 PostgreSQL 16 + pgvector. 部署在 172.30.26.12 (内网).

## 一次性部署 (在 172.30.26.12)

### 1. 准备目录

```bash
ssh user@172.30.26.12
sudo mkdir -p /opt/explain-db/{pgdata,init}
sudo chown $USER /opt/explain-db -R
exit
```

### 2. Copy 文件 (本机操作)

```bash
scp deploy/postgres/docker-compose.yml user@172.30.26.12:/opt/explain-db/
scp deploy/postgres/init/01-init.sql user@172.30.26.12:/opt/explain-db/init/
```

### 3. 启动 (在 172.30.26.12)

```bash
ssh user@172.30.26.12
cd /opt/explain-db
EXPLAIN_DB_PASSWORD='<你设的密码, 强烈建议生成 24+ 字符随机串>' docker compose up -d
```

### 4. 验证 server

```bash
docker compose ps                                                      # 应显 healthy
docker compose exec postgres psql -U explain -c "\dx"                  # 应含 vector extension
docker compose exec postgres psql -U explain -c "\dt"                  # 应含 variables / lexicon_merge_audit / lexicon_meta 3 表
docker compose exec postgres psql -U explain -c "SELECT * FROM lexicon_meta"   # 应显 id=1 单 row
```

## 本机配置

```bash
echo 'export EXPLAIN_DB_URL="postgresql://explain:<你的密码>@172.30.26.12:5432/explain"' >> ~/.zshrc
source ~/.zshrc
```

可选环境变量:

| 变量 | 默认 | 用途 |
|---|---|---|
| `EXPLAIN_DB_URL` | 必需 | 主 DSN |
| `EXPLAIN_DB_POOL_MIN` | `2` | psycopg pool 最小 connection |
| `EXPLAIN_DB_POOL_MAX` | `10` | psycopg pool 最大 connection |
| `EXPLAIN_DB_CONNECT_TIMEOUT_S` | `5` | 连接超时秒数 |

## 数据迁移 (Phase 17.1 落地后跑)

```bash
# Dry-run 预览要 migrate 多少 var
.venv/bin/python -m explain_engine.cli migrate-lexicon-pg --dry-run

# 实跑 (idempotent — 重跑只插新, 已存在 skip)
.venv/bin/python -m explain_engine.cli migrate-lexicon-pg

# 成功后 variables.json → variables.json.migrated (backup, 不删)
# 失败时 json 仍在原位, 可 retry
```

## 备份 (可选, 推荐)

### 手动单次

```bash
ssh user@172.30.26.12 'docker exec explain-postgres pg_dump -U explain explain' \
  > backup_$(date +%Y%m%d).sql
```

### Cron 每日自动

```cron
# /etc/cron.daily/explain-pg-backup
0 3 * * * ssh user@172.30.26.12 \
  'docker exec explain-postgres pg_dump -U explain explain' \
  > /backup/explain_$(date +\%Y\%m\%d).sql 2>&1
```

### 或者用 docker-compose 内置 backup service

把以下加到 `docker-compose.yml`:

```yaml
  backup:
    image: prodrigestivill/postgres-backup-local
    restart: unless-stopped
    environment:
      POSTGRES_HOST: postgres
      POSTGRES_DB: explain
      POSTGRES_USER: explain
      POSTGRES_PASSWORD: ${EXPLAIN_DB_PASSWORD}
      SCHEDULE: "@daily"
      BACKUP_KEEP_DAYS: 7
    volumes:
      - ./backups:/backups
    depends_on:
      postgres:
        condition: service_healthy
```

## 升级

```bash
ssh user@172.30.26.12 'cd /opt/explain-db && docker compose pull && docker compose up -d'
```

## 故障排查

### chat REPL 启动报 `LexiconDBError: 无法连接 lexicon DB`

```bash
# 1. server 跑吗?
ssh user@172.30.26.12 'cd /opt/explain-db && docker compose ps'

# 2. 网络通吗?
ping 172.30.26.12
nc -zv 172.30.26.12 5432

# 3. EXPLAIN_DB_URL 对吗?
echo $EXPLAIN_DB_URL

# 4. 密码对吗?
psql "$EXPLAIN_DB_URL" -c "SELECT 1"
```

### Migration 报 `relation "variables" does not exist`

容器首次启动时 init/01-init.sql 才跑. 若 pgdata 目录原本就有数据 (老 PG 实例), init script **不会再跑**. 解决:

```bash
ssh user@172.30.26.12
cd /opt/explain-db
docker compose down
sudo rm -rf pgdata    # ⚠️ 清数据! 仅在确认是初次部署 / 数据无用时
docker compose up -d
```

或者手动跑 init.sql:

```bash
ssh user@172.30.26.12
cd /opt/explain-db
docker compose exec postgres psql -U explain -d explain -f /docker-entrypoint-initdb.d/01-init.sql
```

## 参考

- Design doc: `docs/plans/2026-05-25-lexicon-pg-perf-design.md` (§5 PG Schema, §10 部署附录)
- Plan doc: `docs/plans/2026-05-25-lexicon-pg-perf-plan.md` (Wave 0 部署 + Wave 1-10 实现)
- pgvector docs: https://github.com/pgvector/pgvector
- testcontainers (用于 dev test): https://testcontainers-python.readthedocs.io
