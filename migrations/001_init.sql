-- 智能选课子系统初始化 DDL。独占 schema course_selection。
-- 对应《02 数据库设计》。前向兼容变更（先加列再切读路径）。

CREATE SCHEMA IF NOT EXISTS course_selection;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid
-- CREATE EXTENSION IF NOT EXISTS vector;  -- pgvector，course_embeddings 用，按需启用

SET search_path TO course_selection;

-- ===== 培养方案域 =====
CREATE TABLE study_plans (
    plan_id               UUID PRIMARY KEY,
    student_id            TEXT NOT NULL,
    major_code            TEXT NOT NULL,
    curriculum_version    TEXT NOT NULL,
    total_credit_required NUMERIC(6,1) NOT NULL DEFAULT 0,
    status                TEXT NOT NULL CHECK (status IN ('draft','valid','invalid')),
    validated_at          TIMESTAMPTZ,
    rule_snapshot         JSONB,
    UNIQUE (student_id, curriculum_version)
);
CREATE INDEX idx_study_plans_student_status ON study_plans (student_id, status);

CREATE TABLE study_plan_items (
    plan_item_id      UUID PRIMARY KEY,
    plan_id           UUID NOT NULL REFERENCES study_plans(plan_id) ON DELETE CASCADE,
    course_code       TEXT NOT NULL,
    category          TEXT NOT NULL CHECK (category IN ('major_required','major_elective','general')),
    expected_semester TEXT NOT NULL,
    credit            NUMERIC(4,1) NOT NULL DEFAULT 0
);
CREATE INDEX idx_plan_items_plan ON study_plan_items (plan_id);
CREATE INDEX idx_plan_items_plan_cat ON study_plan_items (plan_id, category);

CREATE TABLE curriculum_rules (
    rule_id            UUID PRIMARY KEY,
    major_code         TEXT NOT NULL,
    curriculum_version TEXT NOT NULL,
    rule_type          TEXT NOT NULL CHECK (rule_type IN ('min_credit_total','min_credit_category','prerequisite','exclusive')),
    payload            JSONB NOT NULL,
    priority           INT NOT NULL DEFAULT 0,
    fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 表达式唯一性须用唯一索引（表级 UNIQUE 约束不支持表达式列）
CREATE UNIQUE INDEX uq_curriculum_rules
    ON curriculum_rules (major_code, curriculum_version, rule_type, (payload->>'subject_key'));
CREATE INDEX idx_curriculum_rules_major ON curriculum_rules (major_code, curriculum_version);

-- ===== 选课核心域 =====
CREATE TABLE enrollments (
    enrollment_id   UUID PRIMARY KEY,
    student_id      TEXT NOT NULL,
    offering_id     TEXT NOT NULL,
    semester        TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending_lottery','enrolled','waitlisted','canceled','failed')),
    stage           TEXT NOT NULL CHECK (stage IN ('preference','lottery','add_drop')),
    enrolled_at     TIMESTAMPTZ,
    canceled_at     TIMESTAMPTZ,
    source          TEXT NOT NULL DEFAULT 'student_self' CHECK (source IN ('student_self','admin_proxy')),
    idempotency_key TEXT,
    UNIQUE (student_id, offering_id, semester),
    UNIQUE (idempotency_key)
);
CREATE INDEX idx_enrollments_offering_status ON enrollments (offering_id, status);
CREATE INDEX idx_enrollments_student ON enrollments (student_id, semester, status);
CREATE INDEX idx_enrollments_status_time ON enrollments (status, enrolled_at);

CREATE TABLE course_capacity (
    offering_id        TEXT PRIMARY KEY,
    semester           TEXT NOT NULL,
    max_capacity       INT NOT NULL,
    enrolled_count     INT NOT NULL DEFAULT 0,
    waitlist_count     INT NOT NULL DEFAULT 0,
    version            INT NOT NULL DEFAULT 0,
    last_reconciled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (enrolled_count >= 0 AND enrolled_count <= max_capacity)
);

CREATE TABLE enrollment_intents (
    intent_id    UUID PRIMARY KEY,
    student_id   TEXT NOT NULL,
    offering_id  TEXT NOT NULL,
    semester     TEXT NOT NULL,
    priority     INT NOT NULL,
    weight       NUMERIC(4,2) NOT NULL DEFAULT 1.0,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (student_id, offering_id, semester)
);
CREATE INDEX idx_intents_offering_priority ON enrollment_intents (offering_id, priority);

CREATE TABLE lottery_runs (
    run_id         UUID PRIMARY KEY,
    semester       TEXT NOT NULL,
    triggered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    triggered_by   TEXT NOT NULL,
    offering_count INT NOT NULL DEFAULT 0,
    enrolled_count INT NOT NULL DEFAULT 0,
    seed           BIGINT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('running','completed','aborted')),
    report         JSONB
);
CREATE INDEX idx_lottery_runs_semester ON lottery_runs (semester, triggered_at DESC);

CREATE TABLE enrollment_windows (
    window_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    semester   TEXT NOT NULL,
    stage      TEXT NOT NULL CHECK (stage IN ('preference','lottery','add_drop')),
    start_at   TIMESTAMPTZ NOT NULL,
    end_at     TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (semester, stage)
);

CREATE TABLE add_drop_logs (
    log_id      BIGSERIAL PRIMARY KEY,
    student_id  TEXT NOT NULL,
    offering_id TEXT NOT NULL,
    action      TEXT NOT NULL CHECK (action IN ('add','drop','swap')),
    succeeded   BOOLEAN NOT NULL,
    reason_code INT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_add_drop_student ON add_drop_logs (student_id, occurred_at DESC);
CREATE INDEX idx_add_drop_offering ON add_drop_logs (offering_id, occurred_at DESC);

-- ===== 上游缓存域 =====
CREATE TABLE cached_offerings (
    offering_id       TEXT PRIMARY KEY,
    course_code       TEXT NOT NULL,
    course_name       TEXT NOT NULL,
    teacher_id        TEXT NOT NULL,
    teacher_name      TEXT NOT NULL,
    semester          TEXT NOT NULL,
    time_slots        JSONB NOT NULL DEFAULT '[]'::jsonb,
    classroom         TEXT,
    campus            TEXT,
    max_capacity_hint INT,
    fetched_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_version    TEXT
);
CREATE INDEX idx_cached_offerings_sem_code ON cached_offerings (semester, course_code);
CREATE INDEX idx_cached_offerings_teacher ON cached_offerings (teacher_id, semester);
CREATE INDEX idx_cached_offerings_slots ON cached_offerings USING GIN (time_slots);

-- ===== AI / RAG 域 =====
CREATE TABLE ai_conversations (
    conversation_id UUID PRIMARY KEY,
    student_id      TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    summary         TEXT
);
CREATE INDEX idx_ai_conv_student ON ai_conversations (student_id, last_active_at DESC);

CREATE TABLE ai_messages (
    message_id      UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES ai_conversations(conversation_id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool')),
    content         TEXT,
    tokens          INT,
    tool_calls      JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_ai_messages_conv ON ai_messages (conversation_id, created_at);

CREATE TABLE ai_recommendation_logs (
    rec_id           UUID PRIMARY KEY,
    student_id       TEXT NOT NULL,
    offering_ids     TEXT[] NOT NULL DEFAULT '{}',
    prompt_hash      TEXT,
    model            TEXT,
    latency_ms       INT,
    accepted         BOOLEAN NOT NULL DEFAULT false,
    accepted_results JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===== 审计与异步出账 =====
CREATE TABLE audit_logs (
    audit_id    BIGSERIAL PRIMARY KEY,
    actor_id    TEXT NOT NULL,
    actor_role  TEXT NOT NULL,
    action      TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    before      JSONB,
    after       JSONB,
    ip          TEXT,
    request_id  TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_actor ON audit_logs (actor_id, occurred_at DESC);
CREATE INDEX idx_audit_target ON audit_logs (target_type, target_id);

-- 审计不可篡改：拦截 UPDATE / DELETE
CREATE OR REPLACE FUNCTION course_selection.reject_audit_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs 不可变：禁止 % 操作', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION course_selection.reject_audit_mutation();
CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION course_selection.reject_audit_mutation();

CREATE TABLE outbox_events (
    event_id       UUID PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    aggregate_id   TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    payload        JSONB NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','published','dead')),
    retry_count    INT NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at   TIMESTAMPTZ
);
CREATE INDEX idx_outbox_status ON outbox_events (status, created_at);
