// k6 压测脚本：POST /enrollments 直通路径。
// 目标（《04 高并发引擎设计》）：P99 < 200ms；50 并发 / 5min。
// 运行：k6 run tests/load/enrollment_load.js

import http from 'k6/http';
import { check } from 'k6';

export const options = {
  scenarios: {
    enroll: {
      executor: 'constant-vus',
      vus: 50,
      duration: '5m',
    },
  },
  thresholds: {
    http_req_duration: ['p(99)<200'],
    http_req_failed: ['rate<0.01'],
  },
};

const BASE = __ENV.BASE_URL || 'http://localhost:8003';

export default function () {
  const userId = `S-${Math.floor(Math.random() * 100000)}`;
  const res = http.post(
    `${BASE}/api/course-selection/v1/enrollments`,
    JSON.stringify({ offering_id: 'B-CS101-2026-1-01', stage: 'add_drop', idempotency_key: `${userId}-${__ITER}` }),
    {
      headers: {
        'Content-Type': 'application/json',
        'X-User-ID': userId,
        'X-User-Role': 'student',
        'X-Request-ID': `load-${userId}-${__ITER}`,
      },
    },
  );
  // 200 成功 / 202 排队 / 409 满员 都属预期业务结果
  check(res, { 'status is expected': (r) => [200, 202, 409, 422].includes(r.status) });
}
