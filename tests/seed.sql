-- 测试种子数据。供集成 / e2e 测试使用。
SET search_path TO course_selection;

-- 一门有容量的开课，用于防超卖与 e2e 检索
INSERT INTO course_capacity (offering_id, semester, max_capacity, enrolled_count)
VALUES ('B-CS101-2026-1-01', '2026-1', 100, 0)
ON CONFLICT (offering_id) DO NOTHING;

INSERT INTO cached_offerings
    (offering_id, course_code, course_name, teacher_id, teacher_name, semester, time_slots, classroom, campus)
VALUES
    ('B-CS101-2026-1-01', 'CS101', '软件工程', 'T-9001', '张老师', '2026-1',
     '[{"day": 1, "period": [1, 2], "weeks": "1-16"}]'::jsonb, '紫金港西1-201', '紫金港')
ON CONFLICT (offering_id) DO NOTHING;

-- 一条培养方案规则：总学分下限，用于 study-plan 校验 e2e
INSERT INTO curriculum_rules (rule_id, major_code, curriculum_version, rule_type, payload, priority)
VALUES (gen_random_uuid(), 'CS', '2023', 'min_credit_total', '{"min": 8, "subject_key": "total"}'::jsonb, 0)
ON CONFLICT DO NOTHING;
