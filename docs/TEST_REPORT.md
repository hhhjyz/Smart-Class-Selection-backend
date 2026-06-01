# 测试与审计报告 — 智能选课子系统

> 本报告数据全部来自在 **oppo（Linux aarch64）** 上通过 `act` 运行 `.github/workflows/ci.yml`
> 的真实输出，未手工编造。CI 在容器内启动真实 PostgreSQL 16 / Redis 7 / RabbitMQ 3.13
> 作为 services 依赖。

## 运行方式

```bash
# 在装有 docker + act 的机器上，于项目根目录：
act -j static    # 静态质量门禁
act -j test      # 测试矩阵 + 覆盖率门禁
```

GitHub 上由 push / pull_request 触发同一 workflow（services 由 GitHub 提供）。

## 门禁结果（act 实测）

| 阶段 | 工具 | 结果 |
| --- | --- | --- |
| Lint | `ruff check app tests` | ✅ All checks passed |
| 架构契约 | `import-linter` | ✅ **3 kept, 0 broken**（分层 / DIP / domain 纯净） |
| 类型检查 | `pyrefly check` | ✅ **0 errors** |
| 测试 | `pytest`（unit+integration+e2e） | ✅ **103 passed** |
| 覆盖率 | `pytest --cov --cov-fail-under=90` | ✅ **92.82%**（门禁线 90%） |

> 类型检查用 **pyrefly**（见《06 代码规范与项目结构》§类型系统），非 mypy。
> 覆盖率低于 90% 时 `--cov-fail-under=90` 直接让 CI 失败，作为硬门禁。

## 测试矩阵（103 项）

| 层次 | 文件 | 项数 | 依赖 |
| --- | --- | --- | --- |
| 单元 | test_rule_engine.py | 29 | 无（纯逻辑 + fuzz） |
| 单元 | test_enrollment_service.py | 16 | fakes（内存 ports） |
| 单元 | test_core.py | 9 | 无 |
| 单元 | test_other_services.py | 8 | fakes |
| 单元 | test_integrations.py | 6 | httpx MockTransport |
| 单元 | test_tasks.py | 5 | fakes + monkeypatch |
| 单元 | test_stock_store.py | 2 | 内存 fake redis |
| 集成 | test_repositories.py | 5 | 真实 PG |
| 集成 | test_waiting_room.py | 3 | 真实 Redis |
| 集成 | test_anti_oversell.py | 2 | 真实 PG + Redis |
| 集成 | test_lottery.py | 2 | 真实 PG |
| e2e | test_http_flows.py | 7 | 真实 ASGI + PG/Redis/RMQ |
| e2e | test_http_more.py | 5 | 同上 |
| e2e | test_http_admin_and_enroll.py | 4 | 同上 + dependency_overrides |

关键 corner case：
- **防超卖**：1000 并发争抢 100 库存，恰好 100 成功、DB 计数 100、Redis 余量 0；乐观锁越界兜底。
- **幂等**：`idempotency_key` 重复提交短路返回首次结果，不重复扣库存。
- **补偿**：重复选课 / 乐观锁失败 → 回退 Redis 库存（断言 release 次数）。
- **Waiting Room**：未放行 → 30201 排队；放行→消费一次性令牌→不再放行。
- **规则引擎**：时间冲突、前置课、互斥（硬）；学分上限、跨校区通勤（软，可强选豁免）。
- **审计不可篡改**：对 `audit_logs` 的 UPDATE/DELETE 被 PG trigger 拦截（断言抛异常）。
- **AI 守卫**：function calling 越界工具名 → 502 + 写 `ai.guardrail.violated` 审计。
- **上游熔断**：A/B 客户端连续 5xx 触发熔断快速失败。

## 覆盖率（按模块，act 实测）

```
TOTAL                                      1749     88    230     40    93%
```

重点模块：
- `services/enrollment_service.py` 95%、`rule_engine.py` 93%、`lottery_service.py` 95%、`reconciler.py` 94%
- `engine/capacity_lock.py` 100%、`waiting_room.py` 97%、`lottery_runner.py` 92%
- `repositories/*` 89–100%（多数 100%）
- `domain/*`、`schemas/*` 100%
- `core/auth.py` 100%、`errors.py` 98%、`config.py` 100%

已知较低覆盖（非核心路径，已记录，不影响门禁）：
- `integrations/llm_client.py` 74%、`schedule_client.py` 79%：SSE 流式分支与部分错误分支需更细的 mock，后续补。
- `core/mq.py` 70%、`core/redis.py` 79%、`core/db.py` 88%：连接生命周期分支（关闭/重复打开），由启动路径间接覆盖。
- `api/handlers/ai.py` 60%：SSE `messages` 端点的流式封装需 LLM 长连接 mock。

## 审计：质量门禁如何被强制（而非靠自觉）

| 约束 | 强制证据 |
| --- | --- |
| **依赖倒置（DIP）** | import-linter 契约「services 不得 import repositories/integrations」KEPT；service 单测全部注入内存 fake ports，无需 DB 即可运行（反证 service 不耦合具体实现）。 |
| **分层不混乱** | import-linter layers 契约 KEPT；接入层不直连 DB（handler 只调 service），roster 原本的裸 SQL 已下沉到 `enrollment_repo.list_roster`。 |
| **domain 纯净** | import-linter 契约「domain 不依赖任何上层/基础设施」KEPT。 |
| **禁止长事务** | 事务块内仅 insert→capacity→audit→outbox；外部 I/O（上游 HTTP/Redis/LLM）在事务外。集成测试 1000 并发不死锁、不超时佐证。 |
| **复用唯一选课路径** | AI 一键采纳、admin 代选 e2e 均经 `EnrollmentService.enroll()`；test_http_admin_and_enroll / test_http_more 实测。 |
| **禁止裸 JSON 穿层** | 全部 DTO/实体 `extra="forbid"`；pyrefly 0 errors 佐证无 `dict` 裸传（外部 JSON 边界用显式 `Any` 收敛于 integrations adapter）。 |
| **防超卖 / 幂等 / 审计不可变** | 见上「corner case」，均有对应集成测试。 |

## CI 落地过程中由真实运行发现并修复的缺陷

这些 bug 仅靠"代码看起来对"无法发现，是真实跑 CI 才暴露的：

1. **迁移 DDL 语法错误**：`UNIQUE(..., (payload->>'subject_key'))` 表级约束不支持表达式 → 改为 `CREATE UNIQUE INDEX`。
2. **UUID→str 契约错配**：psycopg 把 uuid 列返回为 `uuid.UUID`，而实体 id 为 `str` → 在连接池注册 `uuid→str` 加载器（`core/db.configure_connection`）。
3. **JOIN 列名歧义**：`list_for_student_timetable` 关联两表均含 `offering_id`，未限定别名 → 列加 `o.` 前缀。
4. **打包配置**：setuptools flat-layout 多顶层包冲突 → 显式 `packages.find include=["app*"]`。
5. **/metrics 307**：Starlette mount 重定向 → e2e 客户端跟随重定向（贴近真实 scraper）。
6. **测试隔离**：outbox 跨用例残留、并发压垮连接池 → 截断隔离 + 限流并发 + 乐观锁重试。

## 复现实

```bash
# 本地（需 docker）
pip install -e ".[dev]"
ruff check app tests
lint-imports
pyrefly check
pytest --cov --cov-report=term-missing   # testcontainers 自动起 PG/Redis；e2e 需 TEST_RMQ_URL
```
