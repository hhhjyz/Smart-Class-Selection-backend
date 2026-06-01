"""规则引擎（Strategy 模式）。

每条规则实现统一接口 ``Rule.check(ctx) -> Violation | None``，引擎聚合结果。
新增规则只需新增子类并注册。规则是选课的最终裁判（LLM 仅建议）。
对应《08 构件与设计模式》Rule Engine 与《01 需求文档》模块 1。

本模块为纯函数 / 纯计算，不做任何 I/O —— 所需数据由调用方预先取好放入 ctx，
保证规则校验绝不在事务内触发外部调用。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.core import errors
from app.domain.enums import RuleType, Severity
from app.domain.offering import GradeRecord, Offering
from app.domain.study_plan import CurriculumRule, Violation


@dataclass(slots=True)
class RuleContext:
    """规则校验所需的全部输入（调用方预先组装，规则只读）。"""

    target: Offering
    existing_offerings: Sequence[Offering]  # 学生本学期已选
    passed_courses: frozenset[str]  # 已修通过的 course_code
    grades: Sequence[GradeRecord] = ()
    curriculum_rules: Sequence[CurriculumRule] = ()
    current_total_credit: float = 0.0
    target_credit: float = 0.0
    credit_cap: float = 30.0
    allow_override: bool = False  # 教务特批可强选软规则


class Rule(Protocol):
    def check(self, ctx: RuleContext) -> Violation | None: ...


class TimeConflictRule:
    """硬规则：与已选课程时间段不得重叠。"""

    def check(self, ctx: RuleContext) -> Violation | None:
        for existing in ctx.existing_offerings:
            for a in ctx.target.time_slots:
                for b in existing.time_slots:
                    if a.day == b.day and _periods_overlap(a.period, b.period):
                        return Violation(
                            code=errors.ERR_TIME_CONFLICT,
                            rule_type=None,
                            message=f"与《{existing.course_name}》时间冲突",
                            severity=Severity.HARD,
                        )
        return None


class PrerequisiteRule:
    """硬规则：前置课程必须已修。"""

    def check(self, ctx: RuleContext) -> Violation | None:
        for rule in ctx.curriculum_rules:
            if rule.rule_type is not RuleType.PREREQUISITE:
                continue
            subject = rule.payload.get("subject_key")
            requires = _str_list(rule.payload.get("requires"))
            if subject != ctx.target.course_code:
                continue
            missing = [c for c in requires if c not in ctx.passed_courses]
            if missing:
                return Violation(
                    code=errors.ERR_PREREQUISITE,
                    rule_type=RuleType.PREREQUISITE,
                    message=f"前置课程未修：{', '.join(missing)}",
                    severity=Severity.HARD,
                )
        return None


class CreditCapRule:
    """软规则：单学期学分上限。可强选则降为 warning（这里仍记 soft）。"""

    def check(self, ctx: RuleContext) -> Violation | None:
        if ctx.current_total_credit + ctx.target_credit > ctx.credit_cap:
            if ctx.allow_override:
                return None
            return Violation(
                code=errors.ERR_CREDIT_CAP,
                rule_type=None,
                message=f"学分超限：{ctx.current_total_credit + ctx.target_credit} > {ctx.credit_cap}",
                severity=Severity.SOFT,
            )
        return None


class CampusCommuteRule:
    """软规则：连续两节不同校区，提示通勤时间不足，允许强选。"""

    def check(self, ctx: RuleContext) -> Violation | None:
        if ctx.allow_override or ctx.target.campus is None:
            return None
        for existing in ctx.existing_offerings:
            if existing.campus and existing.campus != ctx.target.campus:
                for a in ctx.target.time_slots:
                    for b in existing.time_slots:
                        if a.day == b.day and _periods_adjacent(a.period, b.period):
                            return Violation(
                                code=errors.ERR_CREDIT_CAP,  # 复用软规则段，前端按 severity 处理
                                rule_type=None,
                                message=f"与《{existing.course_name}》跨校区且时段相邻，通勤可能不足",
                                severity=Severity.SOFT,
                            )
        return None


class ExclusiveRule:
    """硬规则：互斥课程不可同时选（如同名重复修读）。"""

    def check(self, ctx: RuleContext) -> Violation | None:
        for rule in ctx.curriculum_rules:
            if rule.rule_type is not RuleType.EXCLUSIVE:
                continue
            group = set(_str_list(rule.payload.get("group")))
            if ctx.target.course_code not in group:
                continue
            for existing in ctx.existing_offerings:
                if existing.course_code in group and existing.course_code != ctx.target.course_code:
                    return Violation(
                        code=errors.ERR_RULE_REJECTED,
                        rule_type=RuleType.EXCLUSIVE,
                        message=f"与已选《{existing.course_name}》互斥",
                        severity=Severity.HARD,
                    )
        return None


def _periods_overlap(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    return bool(set(a) & set(b))


def _periods_adjacent(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    if not a or not b:
        return False
    return min(a) - max(b) == 1 or min(b) - max(a) == 1


def _str_list(v: object) -> list[str]:
    """把规则 payload（dict[str, object]）里的列表字段安全转 list[str]。"""
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return []


def _default_rules() -> list[Rule]:
    return [
        TimeConflictRule(),
        PrerequisiteRule(),
        ExclusiveRule(),
        CreditCapRule(),
        CampusCommuteRule(),
    ]


@dataclass(slots=True)
class RuleEngine:
    rules: list[Rule] = field(default_factory=_default_rules)

    def validate(self, ctx: RuleContext) -> list[Violation]:
        """运行全部规则，聚合 violations（含软规则）。"""
        out: list[Violation] = []
        for rule in self.rules:
            v = rule.check(ctx)
            if v is not None:
                out.append(v)
        return out

    def has_hard_violation(self, violations: list[Violation]) -> bool:
        return any(v.severity is Severity.HARD for v in violations)
