const test = require('node:test');
const assert = require('node:assert/strict');
const { scheduleRows, cadence } = require('../src/price_monitor/static/schedules.js');

test('daily and monthly feeds show their cadence and remaining quota context', () => {
  const rows = scheduleRows([{ provider: 'alpha_vantage', interval_minutes: 720, requests_last_24h: 25,
    request_limit_24h: 25, status: 'deferred', reason: 'Quota reached.', next_collection_at: '2026-10-14T12:00:00Z' }]);
  assert.match(rows, /daily Brent/);
  assert.match(rows, /12 h/);
  assert.match(rows, /25 \/ 25/);
  assert.match(rows, /Quota reached/);
  assert.equal(cadence(1440), '1 day(s)');
});

test('provider closure reasons remain readable and cannot inject HTML', () => {
  const rows = scheduleRows([{ provider: 'yahoo', interval_minutes: 1, requests_last_24h: 0,
    request_limit_24h: null, status: 'deferred', reason: 'Market closed. <script>', next_collection_at: '2026-10-18T22:00:00Z' }]);
  assert.match(rows, /Market closed/);
  assert.match(rows, /&lt;script&gt;/);
  assert.doesNotMatch(rows, /<script>/);
});
