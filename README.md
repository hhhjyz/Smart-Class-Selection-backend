# 智能选课子系统（course-selection / C 组）

STSS 智能选课服务的实作代码。架构与设计见上级目录《04 架构设计》01–11 篇。

## 架构与质量约束

分层（依赖倒置）：

```
api/handlers          接入层：校验 / RBAC / 错误码映射，不含业务逻辑
  │  deps.py = 装配点（唯一把具体实现注入抽象 service 的地方）
services / engine     业务编排 + 高并发原语，只依赖 domain/ports 抽象
domain                实体 + ports（抽象接口）；纯净，不依赖任何上层
repositories          实现 repo ports（psycopg3，只接 conn）
integrations          实现 client ports（httpx，含熔断）
core                  基础设施：config / db / redis / mq / http / auth / errors / logging
```

代码层面强制的质量门禁（防"维护者乱来"）：

| 约束 | 强制手段 |
| --- | --- |
| 禁止长事务 | 所有外部 I/O 在 `db.transaction()` 之外；事务块内仅 insert→capacity→audit→outbox |
| 强制复用选课路径 | AI 采纳、admin 代选都复用 `EnrollmentService.enroll()`，全系统唯一写路径 |
| 依赖倒置 | services 只 import `domain.ports`；import-linter 禁止其 import `repositories`/`integrations` |
| 禁止裸 JSON 穿层 | 入出参走 `schemas/` DTO，跨层走 `domain/` 实体，`extra="forbid"` + pyrefly 类型检查 |
| 防超卖 | Redis DECR/INCR 补偿 + PG 乐观锁 version；1000 并发抢 100 集成测试必跑 |
| 幂等 | `idempotency_key` 唯一约束 + `ON CONFLICT DO NOTHING` |
| 审计不可篡改 | `audit_logs` PG trigger 拦截 UPDATE/DELETE |

## 本地运行

```bash
cp .env.example .env
docker compose -f deploy/docker-compose.yml up -d   # 起 PG / Redis / RabbitMQ + api + worker
# 或本地裸跑：
pip install -e ".[dev]"
psql "$PG_DSN" -f migrations/001_init.sql
MODE=api python -m app.main        # API
MODE=worker python -m app.main     # 后台任务（Outbox 投递 / 对账）
```

## 质量门禁（CI）

```bash
ruff check .
pyrefly check         # 静态类型检查（见《06 代码规范》）
lint-imports          # import-linter：校验分层 + DIP 契约
pytest tests/unit
pytest -m integration tests/integration   # testcontainers 起真 PG/Redis，含防超卖并发用例
```

## 测试与 CI

- CI：`.github/workflows/ci.yml`（static：ruff + import-linter + pyrefly；test：unit+integration+e2e + 覆盖率门禁 ≥90%）。
- 可用 `act -j static` / `act -j test` 在本地/自托管机跑同一套 CI。
- 实测结果（act on oppo）：**103 passed，覆盖率 92.82%，import-linter 3 kept/0 broken，pyrefly 0 errors**。详见 [docs/TEST_REPORT.md](docs/TEST_REPORT.md)。

## 文档（docs/）

- [接口契约-跨组对外.md](docs/接口契约-跨组对外.md) — 给 D/F 组对接（2 REST + 3 MQ 事件）
- [接口文档-前端对内.md](docs/接口文档-前端对内.md) — 给 C 组前端（全部端点）
- [TEST_REPORT.md](docs/TEST_REPORT.md) — 测试与审计报告

> 接口文档同时收录于知识库《04 架构设计/10、11》；本仓 `docs/` 副本便于直接随代码 clone 查阅。
