const test = require('node:test');
const assert = require('node:assert/strict');
const { requestHeaders } = require('../price_monitor/static/auth.js');
const { auditRows } = require('../price_monitor/static/team.js');

test('session mutations send CSRF only to the application origin', () => {
  const state = { team_mode: true, csrf_token: 'private-csrf' };
  const local = requestHeaders('/api/alerts', { method: 'POST' }, state, 'https://prices.example');
  assert.equal(local.get('X-CSRF-Token'), 'private-csrf');
  assert.equal(local.get('X-API-Key'), null);
  const external = requestHeaders('https://other.example/api/alerts', { method: 'POST' }, state, 'https://prices.example', 'private-key');
  assert.equal(external.get('X-CSRF-Token'), null);
  assert.equal(external.get('X-API-Key'), null);
});

test('downloads use the session and team mode ignores a saved shared key', () => {
  const state = { team_mode: true, csrf_token: 'private-csrf' };
  const headers = requestHeaders('/export.csv', { headers: { 'X-API-Key': 'old-shared-key' } }, state, 'https://prices.example');
  assert.equal(headers.get('X-API-Key'), null);
  assert.equal(headers.get('X-CSRF-Token'), null);
  const account = requestHeaders('/api/history', {}, { team_mode: false, user: { username: 'me' } }, 'https://prices.example', 'old-shared-key');
  assert.equal(account.get('X-API-Key'), null);
});

test('audit rendering labels actions and treats names as text', () => {
  const rows = auditRows([{ occurred_at: '2026-10-13T12:00:00Z', actor: '<script>other user</script>',
    action: 'quote.corrected', resource: 'quote', resource_id: '123', outcome: 'success' }]);
  assert.match(rows, /Devis corrigé/);
  assert.match(rows, /&lt;script&gt;/);
  assert.doesNotMatch(rows, /<script>/);
});
