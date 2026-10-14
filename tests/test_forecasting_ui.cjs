const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../price_monitor/static/app.js'), 'utf8');

function dashboard(product, chartAvailable = true) {
  const elements = new Map();
  const $ = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      value: '7', hidden: false, innerHTML: '', textContent: '',
      classList: { toggle() {} }, getContext() { return {}; }
    });
    return elements.get(selector);
  };
  const charts = [];
  function Chart(context, options) { charts.push(options); this.destroy = () => {}; }
  const context = vm.createContext({
    $, Chart, window: { Chart: chartAvailable ? Chart : null },
    forecastCharts: {}, forecastData: { products: { brent: product } },
    PRODUCT_META: { brent: { color: '#aaa' } }, reducedMotion: { matches: true },
    escapeHtml: value => String(value ?? '').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
    formatNumber: String, formatPct: String, formatDate: String,
    displayUnit: unit => unit === 'USD/baril' ? 'USD/barrel' : unit,
  });
  vm.runInContext(source.slice(source.indexOf('function forecastDate('), source.indexOf('const ALERT_UNITS')), context);
  return { $, charts, context, render: () => context.renderForecastProduct('brent') };
}

function product() {
  return {
    status: 'ready', unit: 'USD/baril', history: [{ date: '2026-01-01', value: 80 }],
    forecasts: { '7': [{ date: '2026-01-08', value: 80, lower: null, upper: null }],
      '30': [{ date: '2026-01-31', value: 85, lower: 75, upper: 95 }] },
    model_selection: {
      '7': { selected_model: 'naive', warnings: [{ message: 'No demonstrated predictive gain. Insufficient calibration.' }] },
      '30': { selected_model: 'holt', warnings: [{ message: 'Low coverage: 60% instead of 95%.' }] },
    },
    backtest: { '7': { samples: 12 }, '30': { samples: 30, mape: 12, interval_coverage: .6, mae_skill_vs_naive: -.2 } }
  };
}

test('model and prominent quality warnings follow the selected horizon', () => {
  const { $, charts, render } = dashboard(product());
  render();
  assert.match($('#forecast-brent-quality').innerHTML, /Last known price/);
  assert.match($('#forecast-brent-quality').innerHTML, /No demonstrated predictive gain/);
  assert.match($('#forecast-brent-stats').innerHTML, /12 forecasts/);
  $('#forecast-horizon').value = '30';
  render();
  assert.match($('#forecast-brent-quality').innerHTML, /Damped Holt/);
  assert.match($('#forecast-brent-quality').innerHTML, /Low coverage/);
  assert.doesNotMatch($('#forecast-brent-quality').innerHTML, /Insufficient calibration/);
  assert.equal(charts.at(-1).data.datasets[3].label, 'Damped Holt');
});

test('absent calibration bands stay null instead of drawing a zero-price band', () => {
  const { charts, render } = dashboard(product());
  render();
  assert.equal(charts[0].data.datasets[0].data.at(-1), null);
  assert.equal(charts[0].data.datasets[1].data.at(-1), null);
  assert.equal(charts[0].data.datasets[0].spanGaps, false);
});

test('quality warnings remain visible when the chart library is unavailable and are escaped', () => {
  const data = product();
  data.model_selection['7'].warnings = [{ message: '<img src=x onerror=alert(1)>' }];
  const { $, render } = dashboard(data, false);
  render();
  assert.equal($('#forecast-brent-quality').hidden, false);
  assert.match($('#forecast-brent-quality').innerHTML, /&lt;img/);
  assert.doesNotMatch($('#forecast-brent-quality').innerHTML, /<img/);
});

test('unavailable forecasts clear previous quality text and hide the chart', () => {
  const data = product();
  const { $, render } = dashboard(data);
  render();
  data.status = 'insufficient_data';
  data.message = 'History too short.';
  render();
  assert.equal($('#forecast-brent-quality').hidden, true);
  assert.equal($('#forecast-brent-quality').innerHTML, '');
  assert.equal($('#forecast-brent-chart').hidden, true);
});

test('a server awaiting restart keeps legacy Holt and heuristic bands honestly labelled', () => {
  const data = product();
  delete data.model_selection;
  data.model = 'damped_holt';
  data.interval_method = 'heuristic_residual_sqrt_time';
  const { $, charts, render } = dashboard(data);
  render();
  assert.match($('#forecast-brent-quality').innerHTML, /Damped Holt/);
  assert.match($('#forecast-brent-quality').innerHTML, /Model comparison unavailable/);
  assert.doesNotMatch($('#forecast-brent-quality').innerHTML, /Last known price/);
  assert.equal(charts[0].data.datasets[1].label, 'Indicative interval');
});

test('live performance remains distinct from the historical test score', () => {
  const data = product();
  data.live_performance = { horizons: { '7': { window: 90, metrics: {
    samples: 5, pending: 2, mae: 1.5, bias: -.2, improvement_over_baseline: .25,
    interval_coverage: .8, interval_samples: 5
  } } } };
  const { $, render } = dashboard(data);
  render();
  assert.match($('#forecast-brent-stats').innerHTML, /Live MAE/);
  assert.match($('#forecast-brent-stats').innerHTML, /5 resolved/);
  assert.match($('#forecast-brent-stats').innerHTML, /Live improvement vs baseline/);
  assert.match($('#forecast-brent-summary').textContent, /Live ledger \(5 resolved\)/);
});
