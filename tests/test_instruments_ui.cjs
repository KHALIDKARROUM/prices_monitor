const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../price_monitor/static/app.js'), 'utf8');

function alertForm() {
  const field = value => ({ value, querySelector: () => ({ disabled: false }) });
  const form = { product: field('brent'), direction: field('above'), channel: field('webhook'),
    basis: field('quote'), instrument: field(''), threshold: {} };
  const elements = new Map([['#alert-form', form]]);
  const $ = selector => { if (!elements.has(selector)) elements.set(selector, {}); return elements.get(selector); };
  const context = vm.createContext({ $, document: { querySelector: $ } });
  vm.runInContext(source.slice(source.indexOf('const ALERT_UNITS'), source.indexOf('const ALERT_CHANNEL_LABELS')), context);
  vm.runInContext(source.slice(source.indexOf('function updateAlertConditionFields'), source.indexOf('function renderAlertRules')), context);
  return { form, $, update: context.updateAlertConditionFields };
}

test('price alerts offer separate instruments and preserve an edited futures choice', () => {
  const { form, update } = alertForm();
  update();
  assert.equal(form.instrument.value, 'brent_spot');
  assert.match(form.instrument.innerHTML, /brent_futures/);
  form.instrument.value = 'brent_futures';
  update();
  assert.equal(form.instrument.value, 'brent_futures');
  form.product.value = 'gasoil';
  update();
  assert.equal(form.instrument.value, 'diesel_spot');
  assert.match(form.instrument.innerHTML, /heating_oil_futures/);
  assert.doesNotMatch(form.instrument.innerHTML, /brent_futures/);
});

test('freshness and bitumen basis changes select only their matching instrument', () => {
  const { form, $, update } = alertForm();
  form.direction.value = 'stale';
  update();
  assert.equal(form.instrument.value, 'brent_futures');
  assert.doesNotMatch(form.instrument.innerHTML, /brent_spot/);
  form.direction.value = 'above';
  form.product.value = 'bitume';
  form.basis.value = 'index';
  update();
  assert.equal(form.instrument.value, 'asphalt_index');
  assert.equal($('#alert-instrument-field').hidden, true);
  form.basis.value = 'quote';
  update();
  assert.equal(form.instrument.value, 'bitumen_quote');
});

test('an old dashboard response cannot replace the newly selected instrument', async () => {
  const requests = [];
  const rendered = [];
  const elements = new Map();
  const $ = selector => { if (!elements.has(selector)) elements.set(selector, { value: '', classList: { add() {} } }); return elements.get(selector); };
  $('#period-select').value = '30';
  $('#market-brent-instrument').value = 'brent_spot';
  const context = vm.createContext({ $, URLSearchParams, Date, dashboardRequest: 0,
    fetch: url => new Promise(resolve => requests.push({ url, resolve })),
    renderMetrics: data => rendered.push(data), renderChart() {}, renderLatest() {}, renderSourceComparison() {},
    formatDate: value => value, connectionMonitor: { render() {} } });
  vm.runInContext(source.slice(source.indexOf('async function loadDashboard()'), source.indexOf('async function loadLogs()')), context);
  const first = context.loadDashboard();
  $('#market-brent-instrument').value = 'brent_futures';
  const second = context.loadDashboard();
  assert.match(requests[0].url, /brent_instrument=brent_spot/);
  assert.match(requests[1].url, /brent_instrument=brent_futures/);
  const response = instrument => ({ ok: true, json: async () => ({ metrics: { instrument }, series: {}, latest: [], quality: {} }) });
  requests[1].resolve(response('brent_futures'));
  await second;
  requests[0].resolve(response('brent_spot'));
  await first;
  assert.deepEqual(rendered, [{ instrument: 'brent_futures' }]);
});
