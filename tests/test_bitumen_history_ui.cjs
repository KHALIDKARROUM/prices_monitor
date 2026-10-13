const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../price_monitor/static/app.js'), 'utf8');
const snapshot = fs.readFileSync(path.join(__dirname, '../data/processed/bitumen_index.csv'), 'utf8')
  .trim().split(/\r?\n/).slice(1).map(line => {
    const [source_date, price] = line.split(',');
    return { source_date, price: Number(price), product: 'bitume', data_kind: 'index', variation_pct: null };
  }).reverse();

function archive(fetch) {
  const elements = new Map();
  const $ = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      textContent: '', innerHTML: '', disabled: false, values: {}, listeners: {},
      addEventListener(event, listener) { this.listeners[event] = listener; }
    });
    return elements.get(selector);
  };
  const context = vm.createContext({
    document: { querySelector: $ }, Date, URLSearchParams,
    FormData: class { constructor(form) { return Object.entries(form.values); } },
    PRODUCT_META: { bitume: { label: 'Bitume', color: '#b4aed4' } },
    protectedFetch: fetch
  });
  vm.runInContext(source.slice(source.indexOf('const $ ='), source.indexOf('const API_KEY_STORAGE')), context);
  vm.runInContext(source.slice(source.indexOf('let historyPage ='), source.indexOf('function setDefaultComparePeriods')), context);
  return { $, context };
}

function monthlyResponse(url) {
  const params = new URL(url, 'http://localhost').searchParams;
  assert.equal(params.get('product'), 'bitume');
  assert.equal(params.get('data_kind'), 'index');
  assert.equal(params.get('page_size'), '12');
  assert.equal(params.get('date_from'), null);
  assert.equal(params.get('supplier'), null);
  const page = Number(params.get('page'));
  return { ok: true, json: async () => ({ items: snapshot.slice((page - 1) * 12, page * 12), total: 60, page, pages: 5 }) };
}

test('Bitume history is visible independently of the mixed archive filters', async () => {
  const { $, context } = archive(monthlyResponse);
  $('#history-filters').values = { product: 'brent', data_kind: 'quote', date_from: '2026-10-01', supplier: 'ABC' };
  await context.loadBitumenHistory();
  assert.equal($('#bitumen-history-count').textContent, '60 mois publiés');
  assert.match($('#bitumen-history-table').innerHTML, /août 2026/);
  assert.match($('#bitumen-history-table').innerHTML, /546,938/);
  assert.equal(($('#bitumen-history-table').innerHTML.match(/<tr>/g) || []).length, 12);
  assert.equal($('#bitumen-history-prev').disabled, true);
  assert.equal($('#bitumen-history-next').disabled, false);
});

test('all five years of monthly Bitume history are reachable through pagination', async () => {
  const { $, context } = archive(monthlyResponse);
  await context.loadBitumenHistory(2);
  assert.match($('#bitumen-history-table').innerHTML, /août 2025/);
  assert.equal($('#bitumen-history-prev').disabled, false);
  await context.loadBitumenHistory(5);
  assert.match($('#bitumen-history-table').innerHTML, /septembre 2021/);
  assert.equal($('#bitumen-history-page-label').textContent, 'Page 5 / 5');
  assert.equal($('#bitumen-history-next').disabled, true);
});

test('late responses cannot replace a newer Bitume history page', async () => {
  const requests = [];
  const { $, context } = archive(url => new Promise(resolve => requests.push({ url, resolve })));
  const first = context.loadBitumenHistory(1);
  const last = context.loadBitumenHistory(5);
  requests[1].resolve(monthlyResponse(requests[1].url));
  await last;
  requests[0].resolve(monthlyResponse(requests[0].url));
  await first;
  assert.equal($('#bitumen-history-page-label').textContent, 'Page 5 / 5');
  assert.match($('#bitumen-history-table').innerHTML, /septembre 2021/);
});

test('changing the archive product or data type applies the selection immediately', () => {
  const { $, context } = archive(monthlyResponse);
  const pages = [];
  context.loadHistory = page => pages.push(page);
  const start = source.indexOf("$('#history-filters').addEventListener('submit'");
  const end = source.indexOf("$('#compare-form').addEventListener", start);
  vm.runInContext(source.slice(start, end), context);
  const change = $('#history-filters').listeners.change;
  change({ target: { name: 'product' } });
  change({ target: { name: 'data_kind' } });
  change({ target: { name: 'source' } });
  assert.deepEqual(pages, [1, 1]);
});
