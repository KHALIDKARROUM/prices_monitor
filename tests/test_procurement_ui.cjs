const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../price_monitor/static/app.js'), 'utf8');

function budget(fetch) {
  const elements = new Map();
  const $ = selector => {
    if (!elements.has(selector)) {
      const classes = new Set();
      elements.set(selector, {
        hidden: false, innerHTML: '', textContent: '',
        classList: { toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); }, contains: name => classes.has(name) }
      });
    }
    return elements.get(selector);
  };
  const context = vm.createContext({
    document: { querySelector: $ }, protectedFetch: fetch,
    PRODUCT_META: { bitume: { label: 'Bitume', purchaseUnit: 'tonne', color: '#b4aed4' } }
  });
  vm.runInContext(source.slice(source.indexOf('const $ ='), source.indexOf('const API_KEY_STORAGE')), context);
  vm.runInContext(source.slice(source.indexOf('function setProcurementHistoryMessage'), source.indexOf('const REGION_LABELS')), context);
  return { $, context };
}

test('an empty purchase history is shown as a normal empty state', async () => {
  const { $, context } = budget(async () => ({ ok: true, json: async () => ({ purchases: [] }) }));
  await context.loadProcurements();
  assert.match($('#procurement-history-message').textContent, /Aucun achat enregistré/);
  assert.equal($('#procurement-history-message').classList.contains('error-note'), false);
  assert.equal($('#procurement-history-table').hidden, true);
  assert.equal($('#procurement-history-state').hidden, false);
  assert.equal($('#procurement-history-retry').hidden, true);
});

test('a failed request explains the cause and can recover on retry', async () => {
  let failing = true;
  const { $, context } = budget(async () => ({ ok: !failing, json: async () => failing ? { error: 'Authentification requise.' } : { purchases: [] } }));
  await context.loadProcurements();
  assert.match($('#procurement-history-message').textContent, /Authentification requise/);
  assert.equal($('#procurement-history-retry').hidden, false);
  assert.equal($('#procurement-history-message').classList.contains('error-note'), true);
  failing = false;
  await context.loadProcurements();
  assert.match($('#procurement-history-message').textContent, /Aucun achat enregistré/);
  assert.equal($('#procurement-history-retry').hidden, true);
  assert.equal($('#procurement-history-message').classList.contains('error-note'), false);
});

test('saved estimates show the history table and calculated amounts', async () => {
  const purchase = { product: 'bitume', supplier: 'ABC', purchase_date: '2026-10-08', quantity: 10, total_cost: 5100, total_cost_usd: 5100, currency: 'USD', budget_amount: 5000, budget_variance: 100, price_impact_pct: null };
  const { $, context } = budget(async () => ({ ok: true, json: async () => ({ purchases: [purchase] }) }));
  await context.loadProcurements();
  assert.equal($('#procurement-history-state').hidden, true);
  assert.equal($('#procurement-history-table').hidden, false);
  assert.match($('#procurement-table').innerHTML, /Bitume/);
  assert.match($('#procurement-table').innerHTML, /10 tonne/);
  assert.match($('#procurement-table').innerHTML, /100 USD/);
});
