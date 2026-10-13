const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '../price_monitor/static/app.js'), 'utf8');

function dashboard() {
  const elements = new Map();
  const $ = selector => {
    if (!elements.has(selector)) elements.set(selector, { hidden: true, textContent: '', innerHTML: '' });
    return elements.get(selector);
  };
  const context = vm.createContext({
    $, PRODUCT_META: { brent: { short: 'Brent' } },
    escapeHtml: value => String(value ?? '').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
    formatDate: value => value || '—', formatNumber: value => String(value)
  });
  const start = appSource.indexOf('function renderSourceComparison(data) {');
  const end = appSource.indexOf('async function loadDashboard()', start);
  vm.runInContext(appSource.slice(start, end), context);
  return { $, render: context.renderSourceComparison, context };
}

function comparison(mismatch = true) {
  return {
    mismatch_count: mismatch ? 1 : 0,
    comparable_pair_count: 1,
    message: mismatch ? 'Les prix des API diffèrent : un problème est possible.' : 'Les prix sont cohérents.',
    warnings: mismatch ? ['FRED = 100, EIA = 101 USD/baril ; écart de 1 USD/baril (1 %).'] : [],
    sources: [
      { code: 'fred_brent', product: 'brent', provider: 'FRED', price: 100, unit: 'USD/baril', availability: 'available' },
      { code: 'eia_brent', product: 'brent', provider: 'EIA', price: mismatch ? 101 : 100, unit: 'USD/baril', availability: 'available' }
    ],
    pairs: [{ product: 'brent', left: 'fred_brent', right: 'eia_brent', status: mismatch ? 'mismatch' : 'match', difference: mismatch ? 1 : 0, difference_pct: mismatch ? 1 : 0, unit: 'USD/baril', reason: 'Même série et même date.' }],
    note: ''
  };
}

test('each dashboard refresh keeps the warning and price details visible until agreement', () => {
  const { $, render } = dashboard();
  for (let i = 0; i < 3; i++) {
    render(comparison());
    assert.equal($('#api-price-warning').hidden, false);
    assert.match($('#api-price-warning-message').textContent, /un problème est possible/);
    assert.match($('#api-price-warning-details').textContent, /FRED = 100, EIA = 101/);
    assert.match($('#api-comparison-table').innerHTML, /À vérifier/);
  }
  render(comparison(false));
  assert.equal($('#api-price-warning').hidden, true);
  assert.equal($('#api-price-warning-message').textContent, '');
  assert.equal($('#api-price-warning-details').textContent, '');
  assert.match($('#comparison-summary').textContent, /cohérente/);
});

test('warning text is displayed as text and cannot inject markup', () => {
  const { $, render } = dashboard();
  const data = comparison();
  data.warnings = ['<img src=x onerror=alert(1)>'];
  render(data);
  assert.equal($('#api-price-warning-details').textContent, data.warnings[0]);
  assert.equal($('#api-price-warning-details').innerHTML, '');
});

test('missing or non-comparable prices are not presented as a verified discrepancy', () => {
  const { $, render } = dashboard();
  render({ ...comparison(false), comparable_pair_count: 0, pairs: [], warnings: [] });
  assert.equal($('#api-price-warning').hidden, true);
  assert.match($('#comparison-summary').textContent, /Aucune paire équivalente vérifiée/);
  render(null);
  assert.equal($('#api-price-warning').hidden, true);
  assert.match($('#comparison-summary').textContent, /indisponible/);
});

test('manual collections show the discrepancy immediately and use a warning toast', async () => {
  const { $, context } = dashboard();
  let collect;
  let refreshes = 0;
  const notices = [];
  $('#collect-btn').addEventListener = (event, callback) => { collect = callback; };
  context.protectedFetch = async () => ({ ok: true, json: async () => ({ status: 'success', rows: 2, source_comparison: comparison() }) });
  context.showToast = (message, warning) => notices.push({ message, warning });
  context.loadDashboard = context.loadLogs = () => { refreshes++; };
  const start = appSource.indexOf("$('#collect-btn').addEventListener");
  const end = appSource.indexOf("$('#bitumen-form').addEventListener", start);
  vm.runInContext(appSource.slice(start, end), context);
  await collect();
  assert.equal($('#api-price-warning').hidden, false);
  assert.match(notices[0].message, /un problème est possible/);
  assert.equal(notices[0].warning, true);
  assert.equal($('#collect-btn').disabled, false);
  assert.equal(refreshes, 2);
});
