"""抽签批处理 worker。

按 offering_id hash 分片，每 worker 一个分片，分片间无锁竞争。
用 FOR UPDATE SKIP LOCKED 取意愿批次，同 seed 可复现。
对应《04 高并发引擎设计》「分轮次」。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from psycopg import AsyncConnection

SQL_FETCH_INTENTS = """
SELECT intent_id, student_id, offering_id, priority
  FROM course_selection.enrollment_intents
 WHERE semester = %s AND mod(abs(hashtext(offering_id)), %s) = %s
 ORDER BY offering_id, priority, random() * COALESCE(weight, 1.0)
   FOR UPDATE SKIP LOCKED
 LIMIT %s
"""

SQL_INSERT_ENROLLMENT = """
INSERT INTO course_selection.enrollments
    (enrollment_id, student_id, offering_id, semester, status, stage, enrolled_at, source)
VALUES (gen_random_uuid(), %s, %s, %s, 'enrolled', 'lottery', NOW(), 'student_self')
ON CONFLICT (student_id, offering_id, semester) DO NOTHING
RETURNING enrollment_id
"""

SQL_INCR_CAPACITY = """
UPDATE course_selection.course_capacity
   SET enrolled_count = enrolled_count + 1, version = version + 1
 WHERE offering_id = %s AND enrolled_count + 1 <= max_capacity
RETURNING enrolled_count
"""

SQL_DECR_CAPACITY = """
UPDATE course_selection.course_capacity
   SET enrolled_count = enrolled_count - 1
 WHERE offering_id = %s
"""


@dataclass(slots=True)
class LotteryShardResult:
    shard: int
    succeeded: int
    failed: int


async def run_shard(
    conn: AsyncConnection, *, semester: str, shard: int, total_shards: int, seed: int, batch: int = 1000
) -> LotteryShardResult:
    """处理一个分片的一批意愿。在调用方的事务边界内执行。"""
    rng = random.Random(seed + shard)  # noqa: S311 - 教学/可复现抽签，非密码学用途
    cur = await conn.execute(SQL_FETCH_INTENTS, (semester, total_shards, shard, batch))
    intents = await cur.fetchall()
    # 用确定性 rng 打散同优先级内顺序（seed 可复现）
    rng.shuffle(intents)
    succeeded = failed = 0
    for _intent_id, student_id, offering_id, _priority in intents:
        cap_cur = await conn.execute(SQL_INCR_CAPACITY, (offering_id,))
        if await cap_cur.fetchone() is None:
            failed += 1
            continue
        enr_cur = await conn.execute(SQL_INSERT_ENROLLMENT, (student_id, offering_id, semester))
        if await enr_cur.fetchone() is None:
            # 重复（已选），回退容量
            await conn.execute(SQL_DECR_CAPACITY, (offering_id,))
            failed += 1
        else:
            succeeded += 1
    return LotteryShardResult(shard=shard, succeeded=succeeded, failed=failed)
