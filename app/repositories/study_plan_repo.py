"""培养方案数据访问，实现 ports.StudyPlanRepository。"""

from __future__ import annotations

import json
from collections.abc import Sequence

from psycopg import AsyncConnection

from app.domain.enums import ItemCategory, PlanStatus, RuleType
from app.domain.study_plan import CurriculumRule, StudyPlan, StudyPlanItem

SQL_GET_PLAN = """
SELECT plan_id, student_id, major_code, curriculum_version,
       total_credit_required, status, validated_at
  FROM course_selection.study_plans
 WHERE student_id = %s
"""

SQL_GET_ITEMS = """
SELECT plan_item_id, course_code, category, expected_semester, credit
  FROM course_selection.study_plan_items
 WHERE plan_id = %s
 ORDER BY expected_semester, course_code
"""

SQL_UPSERT_PLAN = """
INSERT INTO course_selection.study_plans
    (plan_id, student_id, major_code, curriculum_version,
     total_credit_required, status, validated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (student_id, curriculum_version) DO UPDATE
   SET total_credit_required = EXCLUDED.total_credit_required,
       status = EXCLUDED.status,
       validated_at = EXCLUDED.validated_at
RETURNING plan_id
"""

SQL_DELETE_ITEMS = "DELETE FROM course_selection.study_plan_items WHERE plan_id = %s"

SQL_INSERT_ITEM = """
INSERT INTO course_selection.study_plan_items
    (plan_item_id, plan_id, course_code, category, expected_semester, credit)
VALUES (%s, %s, %s, %s, %s, %s)
"""

SQL_DELETE_ITEM = """
DELETE FROM course_selection.study_plan_items i
 USING course_selection.study_plans p
 WHERE i.plan_id = p.plan_id AND p.student_id = %s AND i.plan_item_id = %s
"""

SQL_GET_RULES = """
SELECT rule_id, major_code, curriculum_version, rule_type, payload, priority
  FROM course_selection.curriculum_rules
 WHERE major_code = %s AND curriculum_version = %s
 ORDER BY priority
"""


class PgStudyPlanRepository:
    async def get_by_student(self, conn: AsyncConnection, student_id: str) -> StudyPlan | None:
        cur = await conn.execute(SQL_GET_PLAN, (student_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        plan_id = row[0]
        items_cur = await conn.execute(SQL_GET_ITEMS, (plan_id,))
        items = tuple(
            StudyPlanItem(
                plan_item_id=r[0],
                course_code=r[1],
                category=ItemCategory(r[2]),
                expected_semester=r[3],
                credit=r[4],
            )
            for r in await items_cur.fetchall()
        )
        return StudyPlan(
            plan_id=plan_id,
            student_id=row[1],
            major_code=row[2],
            curriculum_version=row[3],
            total_credit_required=row[4],
            status=PlanStatus(row[5]),
            validated_at=row[6],
            items=items,
        )

    async def upsert(self, conn: AsyncConnection, plan: StudyPlan) -> StudyPlan:
        cur = await conn.execute(
            SQL_UPSERT_PLAN,
            (
                plan.plan_id,
                plan.student_id,
                plan.major_code,
                plan.curriculum_version,
                plan.total_credit_required,
                plan.status.value,
                plan.validated_at,
            ),
        )
        row = await cur.fetchone()
        plan_id = row[0] if row else plan.plan_id
        # 全量替换 items
        await conn.execute(SQL_DELETE_ITEMS, (plan_id,))
        for it in plan.items:
            await conn.execute(
                SQL_INSERT_ITEM,
                (it.plan_item_id, plan_id, it.course_code, it.category.value, it.expected_semester, it.credit),
            )
        return plan.model_copy(update={"plan_id": plan_id})

    async def delete_item(self, conn: AsyncConnection, student_id: str, plan_item_id: str) -> bool:
        cur = await conn.execute(SQL_DELETE_ITEM, (student_id, plan_item_id))
        return cur.rowcount > 0

    async def get_curriculum_rules(
        self, conn: AsyncConnection, major_code: str, curriculum_version: str
    ) -> Sequence[CurriculumRule]:
        cur = await conn.execute(SQL_GET_RULES, (major_code, curriculum_version))
        rows = await cur.fetchall()
        return [
            CurriculumRule(
                rule_id=r[0],
                major_code=r[1],
                curriculum_version=r[2],
                rule_type=RuleType(r[3]),
                payload=r[4] if isinstance(r[4], dict) else json.loads(r[4]),
                priority=r[5],
            )
            for r in rows
        ]
