const test = require('node:test');
const assert = require('node:assert/strict');
const ConnectionMonitor = require('../price_monitor/static/connectivity.js');

function dashboard() {
  const notices = [];
  const badges = [];
  const recoveries = [];
  const monitor = new ConnectionMonitor({
    notify: (message, error = false) => notices.push({ message, error }),
    update: (message, kind) => badges.push({ message, kind }),
    onRecovery: () => recoveries.push(true)
  });
  return { monitor, notices, badges, recoveries };
}

const status = (internet, collection = 'success') => ({ status: internet, collection: { status: collection } });

test('an open dashboard announces loss and recovery once despite repeated failures', () => {
  const { monitor, notices, badges, recoveries } = dashboard();
  monitor.acceptStatus(status('online'));
  assert.equal(notices.length, 0);
  for (let i = 0; i < 20; i++) monitor.acceptStatus(status('offline', 'error'));
  assert.equal(notices.length, 1);
  assert.equal(badges.at(-1).kind, 'error');
  for (let i = 0; i < 20; i++) monitor.acceptStatus(status('online'));
  assert.equal(notices.length, 2);
  assert.match(notices[1].message, /Internet rétablie/);
  assert.equal(recoveries.length, 1);
});

test('opening offline shows one warning and a persistent status', () => {
  const { monitor, notices, badges } = dashboard();
  monitor.setBrowserOnline(false);
  monitor.acceptStatus(status('offline', 'error'));
  monitor.setBrowserOnline(false);
  assert.equal(notices.length, 1);
  assert.equal(badges.at(-1).message, 'Connexion Internet indisponible');
});

test('browser loss is immediate and recovery waits for external confirmation', () => {
  const { monitor, notices, badges } = dashboard();
  monitor.acceptStatus(status('online'));
  monitor.setBrowserOnline(false);
  monitor.acceptStatus(status('online'));
  assert.equal(badges.at(-1).kind, 'error');
  monitor.setBrowserOnline(true);
  assert.equal(notices.length, 1);
  assert.match(badges.at(-1).message, /Vérification/);
  monitor.acceptStatus(status('offline', 'error'));
  assert.equal(notices.length, 1);
  monitor.acceptStatus(status('online', 'error'));
  assert.equal(notices.length, 2);
  assert.match(notices[1].message, /En attente d’une collecte réussie/);
});

test('provider failures and partial recovery do not pretend the Internet is lost', () => {
  const { monitor, notices, badges } = dashboard();
  monitor.acceptStatus(status('online'));
  monitor.acceptStatus(status('online', 'error'));
  monitor.acceptStatus(status('online', 'error'));
  assert.equal(notices.length, 1);
  assert.match(notices[0].message, /Internet est accessible/);
  assert.equal(badges.at(-1).kind, 'warning');
  monitor.acceptStatus(status('online', 'partial'));
  monitor.acceptStatus(status('online', 'partial'));
  assert.equal(notices.length, 2);
  assert.match(notices[1].message, /collecte fonctionne à nouveau/);
  monitor.acceptStatus(status('online'));
  assert.equal(notices.length, 3);
  assert.equal(notices.at(-1).error, false);
});

test('server failures are distinct and notify only once', () => {
  const { monitor, notices } = dashboard();
  monitor.acceptStatus(status('online'));
  monitor.setServiceAvailable(false);
  monitor.setServiceAvailable(false);
  assert.equal(notices.length, 1);
  assert.match(notices[0].message, /application perdue/);
  monitor.acceptStatus(status('online'));
  assert.equal(notices.length, 2);
  assert.match(notices[1].message, /application rétablie/);
});

test('demo data does not conceal an offline connection', () => {
  const { monitor, notices, badges } = dashboard();
  monitor.acceptStatus({ ...status('online'), demo_mode: true });
  assert.match(badges.at(-1).message, /démonstration/);
  monitor.acceptStatus({ ...status('offline'), demo_mode: true });
  assert.equal(notices.length, 1);
  assert.equal(badges.at(-1).kind, 'error');
});

test('polls never overlap and stale responses cannot undo a browser event', async t => {
  const { monitor, notices } = dashboard();
  let resolve;
  let calls = 0;
  t.mock.method(globalThis, 'fetch', () => {
    calls += 1;
    return new Promise(done => { resolve = done; });
  });
  const pending = monitor.check();
  await monitor.check();
  assert.equal(calls, 1);
  monitor.setBrowserOnline(false);
  monitor.setBrowserOnline(true);
  resolve({ ok: true, json: async () => status('online') });
  await pending;
  assert.equal(notices.length, 1);
  assert.equal(monitor.internet, 'checking');
  assert.equal(monitor.pending, false);
});

test('failed status requests do not repeat notifications and successful polls recover', async t => {
  const { monitor, notices } = dashboard();
  t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('Failed to fetch'); });
  await monitor.check();
  await monitor.check();
  assert.equal(notices.length, 1);
  t.mock.method(globalThis, 'fetch', async () => ({ ok: true, json: async () => status('online') }));
  await monitor.check();
  assert.equal(notices.length, 2);
});

test('rate limiting does not create a false connection-loss warning', async t => {
  const { monitor, notices } = dashboard();
  monitor.acceptStatus(status('online'));
  t.mock.method(globalThis, 'fetch', async () => ({ ok: false, status: 429 }));
  await monitor.check();
  assert.equal(notices.length, 0);
  assert.equal(monitor.serviceAvailable, true);
});
