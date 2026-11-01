import http from 'k6/http';
import { check } from 'k6';

// 2 virtual users chatting non-stop for 3 minutes.
export const options = { vus: 2, duration: '3m' };

const prompt = 'Write a detailed, step-by-step guide to running PostgreSQL on Kubernetes, with examples.';

export default function () {
  const res = http.post(
    'http://localhost:8080/api/chat',
    JSON.stringify({ messages: [{ role: 'user', content: prompt }] }),
    { headers: { 'Content-Type': 'application/json' }, timeout: '120s' },
  );
  check(res, {
    'answered (200)': (r) => r.status === 200,
    'rate limited (429)': (r) => r.status === 429,
  });
}