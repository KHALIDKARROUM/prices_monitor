const PRODUCT_META = {
  gasoil: { label: 'Diesel / gasoil', short: 'Diesel', color: '#a9d5bc', purchaseUnit: 'gallon' },
  brent: { label: 'Brent crude oil', short: 'Brent', color: '#dfbb83', purchaseUnit: 'barrel' },
  bitume: { label: 'Bitumen', short: 'Bitumen', color: '#b4aed4', purchaseUnit: 'tonne' }
};
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
let chart;
let chartMode = 'indexed';
let forecastCharts = {};
let forecastData = null;
let alertRules = [];
let editingAlertId = null;
let dashboardRequest = 0;
const $ = (selector) => document.querySelector(selector);
const displayUnit = unit => ({ 'USD/baril': 'USD/barrel', 'baril': 'barrel', 'indice (déc. 1984 = 100)': 'index (Dec. 1984 = 100)', 'indice': 'index' }[unit] || unit || '');
const displaySource = source => ({ 'Yahoo Finance - NY Harbor Heating Oil HO=F (proxy gasoil)': 'Yahoo Finance - NY Harbor Heating Oil HO=F (diesel proxy)' }[source] || source || '');
const formatPrice = (value, unit) => value == null ? '—' : `${Number(value).toLocaleString('en-US', { maximumFractionDigits: 2 })} <small>${escapeHtml(displayUnit(unit))}</small>`;
const formatPct = (value) => value == null ? '—' : `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(2)} %`;
const formatDate = (value, withTime = false) => value ? new Date(value).toLocaleString('en-US', withTime ? { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' } : { day: '2-digit', month: 'short', year: 'numeric' }) : '—';
const formatNumber = (value, digits = 2) => Number(value).toLocaleString('en-US', { maximumFractionDigits: digits });
const formatMoney = (value, currency = 'USD') => value == null ? '—' : `${formatNumber(value)} ${currency}`;
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);
const API_KEY_STORAGE = 'priceMonitorApiKey';
const DASHBOARD_REFRESH_MS = 60 * 1000;

function getStoredApiKey() {
  try { return sessionStorage.getItem(API_KEY_STORAGE) || ''; } catch { return ''; }
}

function storeApiKey(value) {
  try { sessionStorage.setItem(API_KEY_STORAGE, value); } catch { /* Private browsing may disable storage. */ }
}

async function protectedFetch(url, options = {}, allowPrompt = true, overrideApiKey = '') {
  if (window.PriceMonitorAuth) return window.PriceMonitorAuth.fetch(url, options);
  const headers = new Headers(options.headers || {});
  const apiKey = overrideApiKey || getStoredApiKey();
  if (apiKey) headers.set('X-API-Key', apiKey);
  const response = await fetch(url, { ...options, headers });
  if (response.status !== 401 || !allowPrompt) return response;
  const enteredKey = window.prompt('This action requires the Price Monitor API key:');
  if (!enteredKey?.trim()) return response;
  storeApiKey(enteredKey.trim());
  return protectedFetch(url, options, false, enteredKey.trim());
}

function setSystemStatus(message, kind = '') {
  globalThis.PriceMonitorAttention?.update('connection', { message, kind });
  const status = $('#system-status');
  status.classList.remove('warning', 'error');
  if (kind) status.classList.add(kind);
  status.innerHTML = `<i></i> ${escapeHtml(message)}`;
}

let toastTimeout;
function showToast(message, error = false) {
  const toast = $('#toast'); toast.textContent = message; toast.classList.toggle('error-note', error); toast.classList.add('show');
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => toast.classList.remove('show'), 6000);
}

const connectionMonitor = new ConnectionMonitor({
  update: setSystemStatus,
  notify: showToast,
  onRecovery: () => refreshLiveDashboard()
});

function renderMetrics(metrics) {
  $('#metric-grid').innerHTML = Object.entries(PRODUCT_META).map(([key, meta]) => {
    const m = metrics[key] || {}; const cls = m.variation > 0 ? 'up' : m.variation < 0 ? 'down' : 'flat';
    const range = m.min == null ? '—' : `${formatNumber(m.min)}–${formatNumber(m.max)}`;
    const freshness = m.current == null
      ? '<span class="freshness missing">No data</span>'
      : m.data_kind === 'index'
        ? `<span class="freshness ${m.is_stale ? 'stale' : 'current'}">Monthly index · ${new Date(m.source_date).toLocaleDateString('en-US', { month: 'short', year: 'numeric' })}${m.is_stale ? ' · old' : ''}</span>`
      : m.market_closed
        ? `<span class="freshness" title="Next opening: ${escapeHtml(formatDate(m.next_market_open_at, true))}">Market closed · ${m.quote_at ? formatDate(m.quote_at, true) : 'time unknown'}</span>`
      : m.is_live
        ? `<span class="freshness current" title="${escapeHtml(displaySource(m.source || 'Market feed'))}">Live · ${formatDate(m.quote_at, true)}</span>`
      : m.quote_at
        ? `<span class="freshness stale" title="${escapeHtml(displaySource(m.source || 'Market feed'))}">Latest quote · ${formatDate(m.quote_at, true)}</span>`
      : m.source?.startsWith('Yahoo Finance - ')
        ? '<span class="freshness stale">Quote time unavailable</span>'
      : m.is_stale
        ? `<span class="freshness stale" title="Last publication: ${escapeHtml(m.source_date)}">Stale data · ${m.age_days} d</span>`
        : `<span class="freshness current">Up to date · ${formatDate(m.source_date)}</span>`;
    const context = m.data_kind === 'index' ? `<small class="metric-context">BLS WPU058 trend · ${m.is_stale ? 'Last known index retained' : 'Latest published month'}. ${m.supplier_quote ? `Latest quote: ${formatPrice(m.supplier_quote.price, m.supplier_quote.unit)} · ${formatDate(m.supplier_quote.source_date)}` : 'No supplier quote recorded.'}</small>` : '';
    return `<article class="metric-card" style="color:${meta.color}"><div class="metric-head"><span class="metric-label">${escapeHtml(m.label || meta.label)}</span>${freshness}<i class="metric-dot" style="background:${meta.color}"></i></div><div class="metric-price">${formatPrice(m.current, m.unit)}</div><span class="trend ${cls}">${m.variation == null ? '—' : (m.variation >= 0 ? '▲' : '▼') + ' ' + formatPct(m.variation)} <span class="muted">${m.data_kind === 'index' ? 'vs previous month' : 'vs previous'}</span></span>${context}<span class="metric-foot"><span>Avg. ${m.average == null ? '—' : formatNumber(m.average)}</span><span class="range">Min–max ${range}</span><span class="count">${m.count || 0} observation(s)${m.data_kind === 'index' ? ' · monthly' : ''}</span></span></article>`;
  }).join('');
}

function forecastDate(value) {
  return new Date(`${value}T00:00:00Z`).toLocaleDateString('en-US', { day: '2-digit', month: 'short' });
}

function renderForecastProduct(key) {
  const meta = PRODUCT_META[key];
  const product = forecastData?.products?.[key];
  const canvas = $(`#forecast-${key}-chart`);
  const stats = $(`#forecast-${key}-stats`);
  const quality = $(`#forecast-${key}-quality`);
  if (forecastCharts[key]) { forecastCharts[key].destroy(); delete forecastCharts[key]; }
  if (!product || product.status !== 'ready') {
    globalThis.PriceMonitorAttention?.update(`forecast-${key}`, { product });
    canvas.hidden = true;
    quality.hidden = true;
    quality.innerHTML = '';
    stats.innerHTML = `<span class="error-note">${escapeHtml(product?.message || 'Forecast unavailable.')}</span>`;
    const summary = $(`#forecast-${key}-summary`);
    if (summary) summary.textContent = product?.message || 'Forecast unavailable.';
    return;
  }
  const horizon = $(key === 'bitume' ? '#forecast-bitume-horizon' : '#forecast-horizon').value;
  const selection = product.model_selection?.[horizon] || {};
  const selectedModel = selection.selected_model || (product.model === 'damped_holt' ? 'holt' : product.model);
  const modelLabel = selectedModel === 'holt' ? 'Damped Holt' : selectedModel === 'naive' ? 'Last known price' : 'Model unspecified';
  const warnings = selection.warnings || [{ message: 'Model comparison unavailable for this forecast.' }];
  globalThis.PriceMonitorAttention?.update(`forecast-${key}`, { product, warnings });
  const bandLabel = product.interval_method === 'chronological_absolute_log_error_quantile' ? 'Calibrated interval · nominal 95%' : 'Indicative interval';
  quality.hidden = false;
  quality.classList.toggle('has-warning', warnings.length > 0);
  quality.innerHTML = `<strong>Selected model: ${modelLabel}</strong>${warnings.map(warning => `<p>${escapeHtml(warning.message)}</p>`).join('')}`;
  const actual = product.history || [];
  const future = product.forecasts?.[horizon] || [];
  const actualValues = actual.map(row => Number(row.value));
  const labels = [...actual.map(row => row.date), ...future.map(row => row.date)];
  const bridgeLength = Math.max(0, actual.length - 1);
  const bridge = (values) => [...Array(bridgeLength).fill(null), actualValues.at(-1), ...values];
  const lower = bridge(future.map(row => row.lower == null ? null : Number(row.lower)));
  const upper = bridge(future.map(row => row.upper == null ? null : Number(row.upper)));
  const forecast = bridge(future.map(row => Number(row.value)));
  const actualSeries = [...actualValues, ...Array(future.length).fill(null)];
  const lastActual = actual.at(-1);
  const finalForecast = future.at(-1);
  const backtest = product.backtest?.[horizon] || {};
  const change = lastActual?.value && finalForecast ? ((finalForecast.value - lastActual.value) / lastActual.value) * 100 : null;
  const summary = $(`#forecast-${key}-summary`);
  if (summary) summary.textContent = `Latest published value: ${formatNumber(lastActual?.value)} ${displayUnit(product.unit)}, on ${formatDate(lastActual?.date)}. ${horizon}-${key === 'bitume' ? 'month' : 'day'} forecast: ${finalForecast ? formatNumber(finalForecast.value) : 'unavailable'} ${displayUnit(product.unit)}. ${modelLabel}. ${finalForecast?.lower == null || finalForecast?.upper == null ? '95% interval unavailable.' : `Nominal 95% interval: ${formatNumber(finalForecast.lower)} to ${formatNumber(finalForecast.upper)} ${displayUnit(product.unit)}.`} ${warnings.map(warning => warning.message).join(' ')}`;
  stats.innerHTML = `<div><span>Latest actual</span><strong>${formatNumber(lastActual?.value, lastActual?.value < 10 ? 3 : 2)}</strong></div><div><span>${key === 'bitume' ? 'Month +' : 'Day +'}${horizon}</span><strong>${finalForecast ? formatNumber(finalForecast.value, finalForecast.value < 10 ? 3 : 2) : '—'}</strong><em class="${change >= 0 ? 'up' : 'down'}">${formatPct(change)}</em></div><div><span>MAPE · final test (${backtest.samples || 0} forecasts)</span><strong>${backtest.mape == null ? '—' : `${formatNumber(backtest.mape, 1)} %`}</strong></div><div><span>MAE gain vs constant price</span><strong>${backtest.mae_skill_vs_naive == null ? '—' : `${formatNumber(100 * backtest.mae_skill_vs_naive, 1)} %`}</strong></div><div><span>Coverage · final test / nominal 95%</span><strong>${backtest.interval_coverage == null ? '—' : `${formatNumber(100 * backtest.interval_coverage, 1)} %`}</strong></div><div><span>Forecast origin</span><strong>${formatDate(product.forecast_origin || lastActual?.date)}</strong></div>`;
  canvas.hidden = false;
  if (!window.Chart) {
    canvas.hidden = true;
    return;
  }
  const chartContext = canvas.getContext('2d');
  forecastCharts[key] = new Chart(chartContext, {
    type: 'line',
    data: { labels: labels.map(forecastDate), datasets: [
      { label: 'Lower bound', data: lower, borderColor: 'transparent', backgroundColor: 'rgba(200, 200, 200, .1)', pointRadius: 0, fill: false, spanGaps: false },
      { label: bandLabel, data: upper, borderColor: 'transparent', backgroundColor: 'rgba(200, 200, 200, .1)', pointRadius: 0, fill: '-1', spanGaps: false },
      { label: 'Actual', data: actualSeries, borderColor: meta.color, backgroundColor: meta.color, borderWidth: 2, pointRadius: 0, tension: .3, spanGaps: true },
      { label: modelLabel, data: forecast, borderColor: '#f0f7f9', backgroundColor: '#f0f7f9', borderWidth: 2, borderDash: [6, 4], pointRadius: 0, tension: .3, spanGaps: true }
    ] },
    options: { animation: reducedMotion.matches ? false : { duration: 350 }, responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false }, plugins: { legend: { display: false }, tooltip: { backgroundColor: '#202020', padding: 10, callbacks: { label: item => ` ${item.dataset.label}: ${formatNumber(item.raw, item.raw < 10 ? 3 : 2)} ${displayUnit(product.unit)}` } } }, scales: { x: { grid: { display: false }, ticks: { color: '#a0a0a0', maxTicksLimit: 8, font: { size: 10 } } }, y: { grid: { color: 'rgba(255,255,255,.07)' }, ticks: { color: '#a0a0a0', font: { size: 10 }, callback: value => formatNumber(value, value < 10 ? 3 : 0) }, border: { display: false } } } }
  });
}

function renderForecast() {
  renderForecastProduct('gasoil');
  renderForecastProduct('brent');
  renderForecastProduct('bitume');
}

async function loadForecast() {
  try {
    const response = await fetch('/api/forecast?history_days=1825');
    if (!response.ok) throw new Error('Could not calculate forecasts.');
    forecastData = await response.json();
    const generated = forecastData.generated_at ? formatDate(forecastData.generated_at, true) : 'just now';
    const stale = Object.values(forecastData.products || {}).filter(product => product.is_stale);
    const compared = Object.values(forecastData.products || {}).every(product => product.status !== 'ready' || product.model_selection);
    $('#forecast-status').textContent = `${compared ? 'Model selected by horizon' : 'Model comparison unavailable'} · prices: calendar days · bitumen index: months · calculated ${generated}${stale.length ? ' · Caution: latest data is stale; forecasts start at the source date.' : ''}`;
    $('#forecast-status').classList.toggle('error-note', stale.length > 0);
    renderForecast();
  } catch (error) {
    $('#forecast-status').textContent = error.message;
    ['gasoil', 'brent', 'bitume'].forEach(key => { $(`#forecast-${key}-stats`).innerHTML = '<span class="error-note">Forecast temporarily unavailable.</span>'; });
  }
}

const ALERT_UNITS = { gasoil: 'USD/gallon', brent: 'USD/barrel', bitume: 'USD/tonne' };
const ALERT_INSTRUMENTS = {
  gasoil: [['diesel_spot', 'New York Harbor diesel · spot'], ['heating_oil_futures', 'Heating oil HO=F · diesel proxy']],
  brent: [['brent_spot', 'European Brent · spot'], ['brent_futures', 'Brent BZ=F · futures']],
  bitume: [['bitumen_quote', 'Bitumen · supplier quote'], ['asphalt_index', 'Bitumen · BLS index']]
};
const ALERT_CHANNEL_LABELS = { webhook: 'Webhook', email: 'Email', both: 'Webhook + email' };
const ALERT_STATUS_LABELS = { active: 'Active', recovered: 'Recovered', muted: 'Paused', normal: 'Monitoring' };

function resetAlertForm() {
  editingAlertId = null;
  $('#alert-form').reset();
  $('#alert-form-title').textContent = 'Create a rule';
  $('#alert-save').innerHTML = `Create alert <span>→</span>`;
  $('#alert-cancel').hidden = true;
  updateAlertConditionFields();
}

function updateAlertConditionFields() {
  const form = $('#alert-form');
  const freshness = form.direction.value === 'stale';
  $('#alert-threshold-label').textContent = freshness ? 'Time without a new quote (minutes)' : 'Price threshold';
  $('#alert-freshness-help').hidden = !freshness;
  form.threshold.min = freshness ? '2' : '0.000001';
  form.threshold.max = freshness ? '1440' : '';
  form.threshold.step = freshness ? '1' : 'any';
  form.threshold.placeholder = freshness ? '15' : '90';
  form.product.querySelector('option[value="bitume"]').disabled = freshness;
  form.channel.querySelector('option[value="webhook"]').disabled = freshness;
  if (freshness && form.product.value === 'bitume') form.product.value = 'gasoil';
  if (freshness && form.channel.value === 'webhook') form.channel.value = 'email';
  const bitumen = form.product.value === 'bitume' && !freshness;
  $('#alert-basis-field').hidden = !bitumen;
  if (!bitumen) form.basis.value = 'quote';
  const previousInstrument = form.instrument.value;
  const choices = ALERT_INSTRUMENTS[form.product.value].filter(([id]) =>
    freshness ? id === (form.product.value === 'brent' ? 'brent_futures' : 'heating_oil_futures') :
    bitumen ? id === (form.basis.value === 'index' ? 'asphalt_index' : 'bitumen_quote') : true);
  form.instrument.innerHTML = choices.map(([id, label]) => `<option value="${id}">${label}</option>`).join('');
  form.instrument.value = choices.some(([id]) => id === previousInstrument) ? previousInstrument : choices[0][0];
  $('#alert-instrument-field').hidden = bitumen;
  if (!freshness) $('#alert-threshold-label').textContent = bitumen && form.basis.value === 'index' ? 'BLS index threshold (Dec. 1984 = 100)' : `Price threshold (${ALERT_UNITS[form.product.value]})`;
}

function renderAlertRules() {
  $('#alert-list').innerHTML = alertRules.length ? alertRules.map(rule => {
    const meta = { ...(PRODUCT_META[rule.product] || {}), label: ALERT_INSTRUMENTS[rule.product]?.find(([id]) => id === rule.instrument)?.[1] || rule.product };
    const status = ALERT_STATUS_LABELS[rule.status] || ALERT_STATUS_LABELS.normal;
    const freshness = rule.direction === 'stale';
    const condition = freshness ? 'No new quote for' : rule.direction === 'above' ? 'above' : 'below';
    const threshold = freshness ? `${formatNumber(rule.threshold, 0)} min` : `${formatNumber(rule.threshold, rule.threshold < 10 ? 3 : 2)} ${rule.basis === 'index' ? 'index (Dec. 1984 = 100)' : ALERT_UNITS[rule.product]}`;
    const lastValue = rule.last_value == null ? 'Not evaluated yet' : freshness ? `Quote age: ${formatNumber(rule.last_value, 0)} min` : `${rule.basis === 'index' ? 'Latest index' : 'Latest price'}: ${formatNumber(rule.last_value, rule.last_value < 10 ? 3 : 2)} ${rule.basis === 'index' ? 'index (Dec. 1984 = 100)' : ALERT_UNITS[rule.product]}`;
    return `<article class="alert-rule ${rule.status}"><div class="alert-rule-main"><span class="alert-dot"></span><div><strong>${escapeHtml(meta.label)}</strong><p>${condition} <b>${threshold}</b></p><small>${lastValue} · ${escapeHtml(ALERT_CHANNEL_LABELS[rule.channel] || rule.channel)}</small></div></div><div class="alert-rule-actions"><span class="alert-status ${rule.status}">${status}</span><button class="text-btn" type="button" data-permission="editor" data-alert-action="edit" data-alert-id="${rule.id}">Edit</button><button class="text-btn" type="button" data-permission="editor" data-alert-action="mute" data-alert-id="${rule.id}">${rule.muted ? 'Resume' : 'Pause'}</button><button class="text-btn danger-btn" type="button" data-permission="editor" data-alert-action="delete" data-alert-id="${rule.id}">Delete</button></div></article>`;
  }).join('') : '<div class="empty">No alert rules configured. Create a threshold to start monitoring.</div>';
  globalThis.PriceMonitorAuth?.applyPermissions();
}

function renderAlertChannels(channels = {}) {
  const configured = Object.entries(channels).filter(([key, value]) => key !== 'both' && value).map(([key]) => ALERT_CHANNEL_LABELS[key]);
  $('#alert-channel-status').textContent = configured.length ? `Configured channels: ${configured.join(' · ')}.${!channels.email ? ' For email alerts, set PRICE_MONITOR_ALERT_EMAIL_TO and SMTP settings in .env.' : ''}` : 'No notification channel configured. To receive email alerts, set PRICE_MONITOR_ALERT_EMAIL_TO and SMTP settings in .env.';
  $('#alert-channel-status').classList.toggle('error-note', !configured.length);
}

async function loadAlerts() {
  try {
    const response = await fetch('/api/alerts');
    if (!response.ok) throw new Error('Could not load alerts.');
    const data = await response.json();
    alertRules = data.alerts || [];
    globalThis.PriceMonitorAttention?.update('alerts', alertRules);
    renderAlertChannels(data.channels);
    renderAlertRules();
  } catch (error) {
    $('#alert-list').innerHTML = `<div class="empty error-note">${escapeHtml(error.message)}</div>`;
  }
}

function editAlert(id) {
  const rule = alertRules.find(item => item.id === id);
  if (!rule) return;
  editingAlertId = id;
  const form = $('#alert-form');
  form.product.value = rule.product;
  form.basis.value = rule.basis || 'quote';
  form.direction.value = rule.direction;
  updateAlertConditionFields();
  form.instrument.value = rule.instrument || form.instrument.value;
  form.threshold.value = rule.threshold;
  form.channel.value = rule.channel;
  form.muted.checked = rule.muted;
  $('#alert-form-title').textContent = 'Edit rule';
  $('#alert-save').innerHTML = `Save changes <span>→</span>`;
  $('#alert-cancel').hidden = false;
  form.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function mutateAlert(id, payload) {
  const response = await protectedFetch(`/api/alerts/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Could not update this alert.');
  return result;
}

function renderChart(series) {
  const marketDate = row => row.chart_date || row.source_date || row.collected_at?.slice(0, 10);
  const allRows = Object.values(series || {}).flat().sort((a, b) => marketDate(a).localeCompare(marketDate(b)));
  const labels = [...new Set(allRows.map(marketDate).filter(Boolean))];
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  // Include the real current day on the axis. Prices are never fabricated: a
  // dashed segment simply carries the most recently published quote to now.
  if (labels.some(date => date < today)) labels.push(today);
  labels.sort();
  const ctx = $('#price-chart');
  const empty = $('#chart-empty');
  const summary = $('#price-chart-summary');
  if (chart) { chart.destroy(); chart = null; }
  ctx.parentElement.querySelector('.chart-fallback')?.remove();
  if (!labels.length) {
    ctx.hidden = true;
    empty.hidden = false;
    empty.innerHTML = '<strong>No observations for this period</strong><span>Choose 12 months or 5 years, or start a new collection.</span>';
    $('#legend').innerHTML = '';
    $('#chart-note').textContent = 'The chart will appear when a dated observation is available for this period.';
    if (summary) summary.textContent = 'No dated observations for the selected period.';
    return;
  }
  empty.hidden = true;
  const latestSegments = [];
  const datasets = Object.entries(PRODUCT_META).map(([key, meta]) => {
    const rows = [...(series[key] || [])].sort((a, b) => {
      const dateOrder = marketDate(a).localeCompare(marketDate(b));
      return dateOrder || String(a.collected_at || '').localeCompare(String(b.collected_at || ''));
    });
    const byDate = Object.fromEntries(rows.map(r => [marketDate(r), Number(r.price)]));
    const first = rows.length ? Number(rows[0].price) : null;
    const data = labels.map(date => {
      const value = byDate[date];
      return value == null ? null : chartMode === 'indexed' && first ? (value / first) * 100 : value;
    });
    const latest = rows.at(-1);
    const latestDate = latest && marketDate(latest);
    if (latestDate && latestDate < today) {
      const latestPrice = Number(latest.price);
      const displayedLatestPrice = chartMode === 'indexed' && first ? (latestPrice / first) * 100 : latestPrice;
      latestSegments.push({
        label: `${meta.short} · latest quote`,
        data: labels.map(date => date === latestDate || date === today ? displayedLatestPrice : null),
        actualData: labels.map(date => date === latestDate || date === today ? latestPrice : null),
        borderColor: meta.color,
        backgroundColor: meta.color,
        borderWidth: 2,
        borderDash: [6, 5],
        pointRadius: 0,
        tension: 0,
        spanGaps: true,
        unit: displayUnit(latest.unit), isLatestCarryForward: true, yAxisID: chartMode === 'indexed' ? 'y' : `y_${key}`
      });
    }
    return { label: key === 'bitume' && rows.some(row => row.data_kind === 'index') ? 'Bitumen · BLS index' : ALERT_INSTRUMENTS[key]?.find(([id]) => id === rows[0]?.instrument)?.[1] || meta.short, unit: displayUnit(rows[0]?.unit), yAxisID: chartMode === 'indexed' ? 'y' : `y_${key}`, data, actualData: labels.map(date => byDate[date] ?? null), borderColor: meta.color, backgroundColor: meta.color, borderWidth: 2, pointRadius: 0, tension: .35, spanGaps: true };
  });
  $('#legend').innerHTML = datasets.map(d => `<span><i style="background:${d.borderColor}"></i>${d.label}</span>`).join('');
  datasets.push(...latestSegments);
  const bitumenNote = (series.bitume || []).some(row => row.data_kind === 'index') ? ' Bitumen: monthly BLS index; the latest published month anchors periods that begin afterward.' : '';
  const chartDescription = (chartMode === 'indexed' ? 'Relative change from each series’ first available observation; starting dates may differ.' : 'Absolute values use separate axes with each series’ unit. Vertical positions across different units are not comparable.') + bitumenNote;
  if (summary) summary.innerHTML = `<ul>${Object.entries(PRODUCT_META).map(([key, meta]) => {
    const rows = [...(series[key] || [])].sort((a, b) => marketDate(a).localeCompare(marketDate(b)) || String(a.collected_at || '').localeCompare(String(b.collected_at || '')));
    if (!rows.length) return `<li>${escapeHtml(meta.short)}: no observations in this period.</li>`;
    const first = rows[0], last = rows.at(-1), values = rows.map(row => Number(row.price));
    const change = Number(first.price) ? (Number(last.price) / Number(first.price) - 1) * 100 : null;
    return `<li><strong>${escapeHtml(datasets[Object.keys(PRODUCT_META).indexOf(key)].label)}</strong>: ${rows.length} point(s), from ${formatDate(first.source_date || marketDate(first))} to ${formatDate(last.source_date || marketDate(last))}. Latest value ${formatNumber(last.price)} ${escapeHtml(displayUnit(last.unit))}; minimum ${formatNumber(Math.min(...values))}, maximum ${formatNumber(Math.max(...values))}; change ${formatPct(change)}. Extended segments are not new observations.</li>`;
  }).join('')}</ul>`;
  ctx.parentElement.classList.toggle('absolute-chart', chartMode === 'absolute');
  $('#chart-note').textContent = latestSegments.length
    ? `${chartDescription} The dashed line extends the last published quote to today; it does not represent a new price.`
    : `${chartDescription} All available series were published today.`;
  if (!window.Chart) { ctx.hidden = true; empty.hidden = true; if (!ctx.parentElement.querySelector('.chart-fallback')) ctx.insertAdjacentHTML('afterend', '<div class="empty chart-fallback">The chart will appear when Chart.js loads.</div>'); return; }
  ctx.hidden = false; ctx.parentElement.querySelector('.chart-fallback')?.remove();
  const axis = (label, color) => ({ type: 'linear', axis: 'y', position: 'left', stack: 'markets', stackWeight: 1, grid: { color: 'rgba(255,255,255,.07)' }, ticks: { color, font: { size: 10 }, callback: (value, index, ticks) => chartMode === 'absolute' && ticks.length > 3 && (index === 0 || index === ticks.length - 1) ? '' : formatNumber(value, value < 10 ? 3 : 1) }, title: { display: true, text: label, color, font: { size: 10, weight: '600' } }, border: { display: false } });
  const scales = { x: { grid: { display: false }, ticks: { color: '#a0a0a0', maxTicksLimit: 7, font: { size: 10 } } } };
  if (chartMode === 'indexed') scales.y = { ...axis('Base 100 · each series starts on its own date', '#a0a0a0'), stack: undefined };
  else Object.entries(PRODUCT_META).forEach(([key, meta]) => { if (series[key]?.length) scales[`y_${key}`] = axis(`${meta.short} · ${displayUnit(series[key][0].unit) || 'unknown unit'}`, meta.color); });
  // Omit empty series so Chart.js cannot create an unlabelled default axis.
  const visibleDatasets = datasets.filter(dataset => dataset.actualData.some(value => value != null));
  chart = new Chart(ctx, { type: 'line', data: { labels: labels.map(d => new Date(`${d}T00:00:00`).toLocaleDateString('en-US', { day: '2-digit', month: 'short' })), datasets: visibleDatasets }, options: { animation: reducedMotion.matches ? false : { duration: 350 }, responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false }, plugins: { legend: { display: false }, tooltip: { backgroundColor: '#202020', padding: 12, titleColor: '#fff', bodyColor: '#cccccc', callbacks: { label: item => { const actual = item.dataset.actualData?.[item.dataIndex]; const value = chartMode === 'indexed' ? `${formatNumber(item.raw, 1)} · ${actual == null ? '—' : formatNumber(actual)}` : formatNumber(actual ?? item.raw); const label = item.dataset.isLatestCarryForward ? 'Last known quote (extended)' : item.dataset.label; return ` ${label}: ${value} ${item.dataset.unit || ''}`; } } } }, scales } });
}

function renderLatest(rows) {
  $('#latest-table').innerHTML = rows.length ? rows.map(row => {
    const meta = PRODUCT_META[row.product]; const pct = row.variation_pct; const cls = pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
    return `<tr><td><div class="product-cell"><i style="background:${meta.color}"></i><strong>${meta.label}${row.data_kind === 'index' ? ' · BLS index' : row.product === 'bitume' ? ' · quote' : ''}</strong></div></td><td class="price-cell">${formatPrice(row.price, row.unit)}</td><td><span class="variation trend ${cls}">${formatPct(pct)}</span></td><td>${formatDate(row.source_date)}</td><td>${formatDate(row.collected_at, true)}</td><td class="source-cell">${escapeHtml(displaySource(row.source))}<br>${row.is_unchanged ? '<span class="unchanged">Publication unchanged</span>' : ''}</td><td></td></tr>`;
  }).join('') : '<tr><td colspan="7" class="empty">No observations in this period.</td></tr>';
}

function renderProcurementSummary(purchase) {
  const summary = $('#procurement-summary');
  const budgetClass = purchase.budget_variance == null ? '' : purchase.budget_variance > 0 ? 'up' : 'down';
  const impactClass = purchase.price_impact_unit_usd == null ? '' : purchase.price_impact_unit_usd > 0 ? 'up' : 'down';
  summary.innerHTML = `<div class="purchase-stat"><span>Total cost</span><strong>${formatMoney(purchase.total_cost, purchase.currency)}</strong><small>${formatMoney(purchase.total_cost_usd, 'USD')}</small></div><div class="purchase-stat"><span>Budget difference</span><strong class="${budgetClass}">${formatMoney(purchase.budget_variance, purchase.currency)}</strong><small>${purchase.budget_variance_usd == null ? 'No budget entered' : formatMoney(purchase.budget_variance_usd, 'USD')}</small></div><div class="purchase-stat"><span>Reference market price</span><strong>${formatMoney(purchase.market_price_usd, 'USD')}</strong><small>Latest observation per ${PRODUCT_META[purchase.product]?.purchaseUnit || ''}</small></div><div class="purchase-stat"><span>Price impact</span><strong class="${impactClass}">${formatMoney(purchase.price_impact_unit_usd, 'USD')} / ${PRODUCT_META[purchase.product]?.purchaseUnit || 'unit'}</strong><small>${purchase.price_impact_pct == null ? 'Reference unavailable' : `${formatPct(purchase.price_impact_pct)} · ${formatMoney(purchase.price_impact_total_usd, 'USD')} in total`}</small></div>`;
  summary.hidden = false;
  $('#procurement-breakdown').innerHTML = `<h3>Landed cost breakdown</h3>${costBreakdownHtml(purchase)}<p class="field-help">Landed cost per unit: ${formatNumber(purchase.landed_unit_cost_usd, 6)} USD/${escapeHtml(PRODUCT_META[purchase.product]?.purchaseUnit || purchase.unit)}.${purchase.quote_id ? ` Quote #${purchase.quote_id}, version ${purchase.quote_version}, saved with this estimate.` : ''} Market price comparison is indicative: grade, origin, and delivery terms have not been verified.</p>`;
  $('#procurement-breakdown').hidden = false;
}

function setProcurementHistoryMessage(message, error = false) {
  $('#procurement-history-table').hidden = true;
  $('#procurement-history-state').hidden = false;
  $('#procurement-history-message').textContent = message;
  $('#procurement-history-message').classList.toggle('error-note', error);
  $('#procurement-history-retry').hidden = !error;
}

function renderProcurements(rows) {
  if (!rows.length) {
    $('#procurement-table').innerHTML = '';
    setProcurementHistoryMessage('No purchases recorded. Budget estimates will appear here after calculation.');
    return;
  }
  $('#procurement-history-state').hidden = true;
  $('#procurement-history-table').hidden = false;
  $('#procurement-table').innerHTML = rows.length ? rows.map(row => {
    const meta = PRODUCT_META[row.product] || { label: row.product, color: '#a7bac2', purchaseUnit: row.unit };
    const varianceClass = row.budget_variance == null ? '' : row.budget_variance > 0 ? 'negative' : 'positive';
    const impactClass = row.price_impact_pct == null ? '' : row.price_impact_pct > 0 ? 'negative' : 'positive';
    const budgetPct = row.budget_amount > 0 ? ` (${formatPct(row.budget_variance / row.budget_amount * 100)})` : '';
    const detail = (row.cost_breakdown || []).filter(item => item.key !== 'merchandise' && item.amount > 0).map(item => `<div>${escapeHtml(item.label)} : ${formatMoney(item.amount, row.currency)}</div>`).join('');
    return `<tr><td>${formatDate(row.purchase_date)}</td><td><div class="product-cell"><i style="background:${meta.color}"></i><strong>${meta.label}</strong></div></td><td>${escapeHtml(row.supplier)}${row.quote_id ? `<br><small>Quote #${row.quote_id} · v${row.quote_version}</small>` : ''}</td><td>${formatNumber(row.quantity, 2)} ${meta.purchaseUnit}</td><td class="price-cell">${formatMoney(row.total_cost, row.currency)}<br><span class="source-cell">${formatMoney(row.total_cost_usd, 'USD')}</span>${detail ? `<details><summary>View costs</summary>${detail}</details>` : ''}</td><td class="${varianceClass}">${row.budget_variance == null ? '—' : `${formatMoney(row.budget_variance, row.currency)}${budgetPct}`}</td><td class="${impactClass}">${row.price_impact_pct == null ? '—' : `${formatPct(row.price_impact_pct)}<br><span class="source-cell">${formatMoney(row.price_impact_total_usd, 'USD')}</span>`}</td></tr>`;
  }).join('') : '<tr><td colspan="7" class="empty">No purchases recorded.</td></tr>';
}

async function loadProcurements() {
  try {
    const response = await protectedFetch('/api/procurements?limit=20');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not load purchases.');
    renderProcurements(data.purchases || []);
  } catch (error) {
    setProcurementHistoryMessage(`Purchase history unavailable. ${error.message}`, true);
  }
}

const REGION_LABELS = {
  global: 'International', europe: 'Europe', africa_middle_east: 'Africa & Middle East',
  asia_pacific: 'Asia-Pacific', americas: 'Americas', local: 'Local'
};

function renderMarketContext(data) {
  const context = data.market_context || {};
  const price = context.latest_price;
  const priceLabel = data.product === 'bitume' ? 'Latest saved quote' : 'Latest diesel benchmark';
  const priceDetail = price
    ? `<strong>${formatNumber(price.price, price.price < 10 ? 3 : 2)} ${escapeHtml(displayUnit(price.unit))}</strong><small>${formatDate(price.source_date)} · ${escapeHtml(displaySource(price.source))}</small>`
    : '<strong>—</strong><small>No observations available.</small>';
  const benchmarkCards = (context.benchmarks || []).map(row =>
    `<div class="market-card"><span>${escapeHtml(row.label)}</span><strong>${formatNumber(row.value, 3)} ${escapeHtml(displayUnit(row.unit))}</strong><small>${formatDate(row.source_date)} · ${escapeHtml(row.geography)} · ${formatPct(row.variation_pct)}</small></div>`
  ).join('');
  $('#market-context').innerHTML = `<div class="market-card"><span>${priceLabel}</span>${priceDetail}</div>${benchmarkCards}<div class="market-card market-warning"><strong>Before negotiating</strong>${escapeHtml(context.warning || '')}</div>`;
}

function renderSupplierDirectory(rows) {
  $('#supplier-directory').innerHTML = rows.length ? rows.map(row => {
    const links = [
      row.website_url ? `<a class="text-btn" href="${escapeHtml(row.website_url)}" target="_blank" rel="noopener noreferrer">Official website ↗</a>` : '',
      row.contact_url && row.contact_url !== row.website_url ? `<a class="text-btn" href="${escapeHtml(row.contact_url)}" target="_blank" rel="noopener noreferrer">Contact / order ↗</a>` : ''
    ].filter(Boolean).join('');
    return `<article class="supplier-card"><div class="supplier-card-head"><h4>${escapeHtml(row.name)}</h4><span class="supplier-region">${escapeHtml(REGION_LABELS[row.region] || row.region)}</span></div><p>${escapeHtml(row.coverage)}</p><dl><dt>Access</dt><dd>${escapeHtml(row.channel)}</dd><dt>Delivery</dt><dd>${escapeHtml(row.typical_terms)}</dd><dt>Specification</dt><dd>${escapeHtml(row.specifications)}</dd></dl><small class="field-help">${escapeHtml(row.buyer_note)}</small><div class="supplier-links">${links}</div></article>`;
  }).join('') : '<div class="empty">No supplier channels match these criteria.</div>';
}

function renderProcurementGuide(data) {
  renderMarketContext(data);
  renderSupplierDirectory(data.suppliers || []);
  $('#buy-steps').innerHTML = (data.steps || []).map(step => `<li><span class="step-number">${step.number}</span><b>${escapeHtml(step.title)}</b><span>${escapeHtml(step.detail)}</span></li>`).join('');
  $('#buy-specification').innerHTML = `<strong>Specification:</strong> ${escapeHtml(data.specification)}`;
  $('#buy-logistics').innerHTML = `<strong>Logistics:</strong> ${escapeHtml(data.logistics)}`;
  $('#rfq-checklist').innerHTML = (data.rfq_fields || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
  $('#landed-cost-formula').innerHTML = `<strong>Comparison formula:</strong> ${escapeHtml(data.landed_cost_formula)}`;
  $('#buy-disclaimer').textContent = data.disclaimer || '';
}

async function loadProcurementGuide() {
  const product = $('#buy-product').value;
  const region = $('#buy-region').value;
  try {
    const response = await fetch(`/api/procurement-guide?product=${encodeURIComponent(product)}&region=${encodeURIComponent(region)}`);
    if (!response.ok) { const body = await response.json(); throw new Error(body.error || 'Purchasing guide unavailable.'); }
    renderProcurementGuide(await response.json());
  } catch (error) {
    $('#market-context').innerHTML = `<div class="empty error-note">${escapeHtml(error.message)}</div>`;
    $('#supplier-directory').innerHTML = '<div class="empty error-note">Supplier channels unavailable.</div>';
  }
}

function updateProcurementUnits() {
  const product = $('#procurement-product').value;
  const currency = $('#procurement-currency').value;
  const meta = PRODUCT_META[product];
  $('#quantity-unit').textContent = meta.purchaseUnit;
  $('#unit-price-unit').textContent = `${currency}/${meta.purchaseUnit}`;
  $('#procurement-cost-currency').textContent = currency;
}

let historyPage = 1;
let historyPages = 0;
let historyRequest = 0;
let bitumenHistoryPage = 1;
let bitumenHistoryPages = 0;
let bitumenHistoryRequest = 0;

function renderBitumenHistory(data) {
  const rows = data.items || [];
  bitumenHistoryPage = data.page || 1;
  bitumenHistoryPages = data.pages || 0;
  $('#bitumen-history-count').textContent = `${data.total || 0} published months`;
  $('#bitumen-history-page-label').textContent = bitumenHistoryPages ? `Page ${bitumenHistoryPage} of ${bitumenHistoryPages}` : 'No pages';
  $('#bitumen-history-prev').disabled = bitumenHistoryPage <= 1;
  $('#bitumen-history-next').disabled = !bitumenHistoryPages || bitumenHistoryPage >= bitumenHistoryPages;
  $('#bitumen-history-table').innerHTML = rows.length ? rows.map(row => {
    const month = new Date(`${row.source_date.slice(0, 10)}T00:00:00Z`).toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
    const pct = row.variation_pct;
    const cls = pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
    return `<tr><td>${month}</td><td class="price-cell">${formatNumber(row.price, 3)}</td><td><span class="trend ${cls}">${formatPct(pct)}</span></td></tr>`;
  }).join('') : '<tr><td colspan="3" class="empty">No monthly bitumen index recorded.</td></tr>';
}

async function loadBitumenHistory(page = 1) {
  const request = ++bitumenHistoryRequest;
  const params = new URLSearchParams({ product: 'bitume', data_kind: 'index', page, page_size: '12' });
  try {
    const response = await protectedFetch(`/api/history?${params}`);
    if (!response.ok) throw new Error('Bitumen history unavailable.');
    const data = await response.json();
    if (request === bitumenHistoryRequest) renderBitumenHistory(data);
  } catch (error) {
    if (request !== bitumenHistoryRequest) return;
    $('#bitumen-history-table').innerHTML = '<tr><td colspan="3" class="empty error-note">Bitumen history temporarily unavailable. Use Refresh to try again.</td></tr>';
    $('#bitumen-history-count').textContent = 'Unavailable';
  }
}

function historyFilterParams(includeDates = true) {
  const values = Object.fromEntries(new FormData($('#history-filters')));
  const params = new URLSearchParams();
  ['product', 'supplier', 'source', 'min_price', 'max_price', 'data_kind', 'instrument'].forEach(key => { if (values[key]) params.set(key, values[key]); });
  if (includeDates) {
    ['date_from', 'date_to'].forEach(key => { if (values[key]) params.set(key, values[key]); });
  }
  return params;
}

function updateHistoryExportLinks() {
  const query = historyFilterParams().toString();
  $('#history-csv').href = `/export.csv${query ? `?${query}` : ''}`;
  $('#history-xlsx').href = `/export.xlsx${query ? `?${query}` : ''}`;
}

function renderHistory(data) {
  const rows = data.items || [];
  $('#history-count').textContent = `${data.total || 0} observation(s)`;
  historyPage = data.page || 1;
  historyPages = data.pages || 0;
  $('#history-page-label').textContent = historyPages ? `Page ${historyPage} of ${historyPages}` : 'No pages';
  $('#history-prev').disabled = historyPage <= 1;
  $('#history-next').disabled = !historyPages || historyPage >= historyPages;
  $('#history-table').innerHTML = rows.length ? rows.map(row => {
    const meta = PRODUCT_META[row.product] || { label: row.product, color: '#a7bac2' };
    const pct = row.variation_pct; const cls = pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
    return `<tr><td>${formatDate(row.source_date)}</td><td><div class="product-cell"><i style="background:${meta.color}"></i><strong>${meta.label}${row.data_kind === 'index' ? ' · BLS index' : row.product === 'bitume' ? ' · quote' : ''}</strong></div></td><td class="price-cell">${formatPrice(row.price, row.unit)}</td><td>${escapeHtml(row.supplier || '—')}</td><td class="source-cell">${escapeHtml(displaySource(row.source))}</td><td>${formatDate(row.collected_at, true)}</td><td><span class="trend ${cls}">${formatPct(pct)}</span></td></tr>`;
  }).join('') : '<tr><td colspan="7" class="empty">No observations match these filters.</td></tr>';
  updateHistoryExportLinks();
}

async function loadHistory(page = 1) {
  const request = ++historyRequest;
  const params = historyFilterParams();
  params.set('page', page);
  params.set('page_size', '50');
  try {
    const response = await protectedFetch(`/api/history?${params.toString()}`);
    if (!response.ok) throw new Error('Could not load history.');
    const data = await response.json();
    if (request === historyRequest) renderHistory(data);
  } catch (error) {
    if (request !== historyRequest) return;
    $('#history-table').innerHTML = '<tr><td colspan="7" class="empty error-note">History temporarily unavailable.</td></tr>';
    $('#history-count').textContent = 'Unavailable';
  }
}

function setDefaultComparePeriods() {
  const end = new Date();
  const formatInputDate = value => value.toISOString().slice(0, 10);
  const shift = days => { const value = new Date(end); value.setDate(value.getDate() + days); return value; };
  const form = $('#compare-form');
  form.period_a_from.value = formatInputDate(shift(-60));
  form.period_a_to.value = formatInputDate(shift(-31));
  form.period_b_from.value = formatInputDate(shift(-30));
  form.period_b_to.value = formatInputDate(end);
}

function renderComparison(data) {
  $('#compare-table').innerHTML = Object.entries(data.products || {}).map(([key, row]) => {
    const meta = PRODUCT_META[key] || { label: key, color: PRODUCT_META.bitume.color };
    const changeClass = row.average_change > 0 ? 'negative' : row.average_change < 0 ? 'positive' : '';
    return `<tr><td><div class="product-cell"><i style="background:${meta.color}"></i><strong>${escapeHtml(meta.label)}</strong></div></td><td>${formatPrice(row.period_a.average, row.unit)}</td><td>${formatPrice(row.period_b.average, row.unit)}</td><td class="${changeClass}">${row.average_change == null ? '—' : `${formatNumber(row.average_change)} ${escapeHtml(displayUnit(row.unit))} · ${formatPct(row.average_change_pct)}`}</td><td>${row.period_a.count} / ${row.period_b.count}</td></tr>`;
  }).join('') || '<tr><td colspan="5" class="empty">No data to compare.</td></tr>';
}

async function compareHistory() {
  const values = Object.fromEntries(new FormData($('#compare-form')));
  const params = new URLSearchParams(values);
  historyFilterParams(false).forEach((value, key) => params.set(key, value));
  try {
    const response = await protectedFetch(`/api/history/compare?${params.toString()}`);
    if (!response.ok) { const result = await response.json(); throw new Error(result.error || 'Could not compare periods.'); }
    renderComparison(await response.json());
  } catch (error) {
    $('#compare-table').innerHTML = `<tr><td colspan="5" class="empty error-note">${escapeHtml(error.message)}</td></tr>`;
  }
}

function renderSourceComparison(data) {
  const warning = $('#api-price-warning');
  warning.hidden = !data?.mismatch_count;
  $('#api-price-warning-message').textContent = warning.hidden ? '' : data.message;
  $('#api-price-warning-details').textContent = warning.hidden ? '' : (data.warnings || []).join(' ');
  const summary = $('#comparison-summary');
  const prices = $('#api-price-table');
  const comparisons = $('#api-comparison-table');
  if (!data) {
    summary.textContent = 'Comparison unavailable';
    summary.className = 'comparison-badge';
    prices.innerHTML = '<tr><td colspan="6" class="empty">Could not check API data.</td></tr>';
    comparisons.innerHTML = '<tr><td colspan="5" class="empty">Comparison unverified.</td></tr>';
    $('#comparison-note').textContent = '';
    return;
  }
  const availability = {
    available: 'Data received', no_data: 'No data collected',
    not_configured: 'API not configured / disabled', unavailable: 'Collection failed · saved price retained'
  };
  const verdicts = {
    match: ['Consistent', 'match'], mismatch: ['Needs review', 'mismatch'],
    not_comparable: ['Not comparable', ''], unverified: ['Unverified', '']
  };
  const byCode = Object.fromEntries(data.sources.map(row => [row.code, row]));
  summary.textContent = data.mismatch_count
    ? `${data.mismatch_count} difference(s) to review`
    : data.comparable_pair_count ? `${data.comparable_pair_count} consistent equivalent pair(s)` : 'No equivalent pairs verified';
  summary.className = `comparison-badge ${data.mismatch_count ? 'mismatch' : data.comparable_pair_count ? 'match' : ''}`;
  prices.innerHTML = data.sources.map(row => {
    const product = PRODUCT_META[row.product]?.short || row.product;
    const value = row.price == null ? '—' : `${formatNumber(row.price, 4)} <small>${escapeHtml(displayUnit(row.unit))}</small>`;
    return `<tr><td><strong>${escapeHtml(product)}</strong></td><td><strong>${escapeHtml(row.provider)}</strong><small class="comparison-detail">${escapeHtml(row.instrument)}</small></td><td class="price-cell">${value}</td><td>${formatDate(row.source_date)}</td><td>${formatDate(row.last_success_at, true)}</td><td>${escapeHtml(availability[row.availability] || 'Unverified')}</td></tr>`;
  }).join('');
  comparisons.innerHTML = data.pairs.map(pair => {
    const [label, kind] = verdicts[pair.status] || verdicts.unverified;
    const product = PRODUCT_META[pair.product]?.short || pair.product;
    const providers = `${byCode[pair.left]?.provider || pair.left} / ${byCode[pair.right]?.provider || pair.right}`;
    const difference = pair.difference == null ? '—' : `${formatNumber(pair.difference, 6)} <small>${escapeHtml(displayUnit(pair.unit))}</small>`;
    const percent = pair.difference_pct == null ? '—' : `${formatNumber(pair.difference_pct, 3)} %`;
    return `<tr><td>${escapeHtml(product)}</td><td>${escapeHtml(providers)}</td><td class="price-cell">${difference}</td><td>${percent}</td><td><span class="comparison-badge ${kind}">${label}</span><small class="comparison-detail">${escapeHtml(pair.reason)}</small></td></tr>`;
  }).join('') || '<tr><td colspan="5" class="empty">No price pairs available.</td></tr>';
  $('#comparison-note').textContent = data.note;
}

async function loadDashboard() {
  const request = ++dashboardRequest;
  const days = $('#period-select').value; $('#period-label').textContent = days == 365 ? 'Last 12 months' : days == 1825 ? 'Last 5 years' : `Last ${days} days`;
  try {
    const params = new URLSearchParams({ days });
    ['brent', 'gasoil'].forEach(product => { const value = $(`#market-${product}-instrument`)?.value; if (value) params.set(`${product}_instrument`, value); });
    const response = await fetch(`/api/dashboard?${params}`, { cache: 'no-store' }); if (!response.ok) throw new Error('Could not load the dashboard.');
    const data = await response.json();
    if (request !== dashboardRequest) return;
    globalThis.PriceMonitorAttention?.update('dashboard', data);
    renderMetrics(data.metrics); renderChart(data.series); renderLatest(data.latest);
    renderSourceComparison(data.source_comparison);
    $('#quality-score').textContent = data.quality?.score ?? '—';
    $('#mode-label').textContent = data.demo_mode ? 'Demo active' : 'Production / API';
    $('#last-update').textContent = data.latest.length ? formatDate(data.latest.reduce((a, b) => new Date(a.collected_at) > new Date(b.collected_at) ? a : b).collected_at, true) : '—';
    $('#source-count').textContent = `${data.quality?.source_count ?? data.latest.length} sources`;
    $('#observation-count').textContent = `${data.quality?.observation_count ?? 0}`;
    connectionMonitor.render();
  } catch (error) {
    if (request !== dashboardRequest) return;
    globalThis.PriceMonitorAttention?.update('dashboard', null);
    renderSourceComparison(null);
    renderChart({});
    $('#chart-empty').innerHTML = '<strong>Data service unavailable</strong><span>Restart the application, then refresh this page.</span>';
    connectionMonitor.check();
  }
}

async function loadLogs() {
  try {
    const response = await fetch('/api/logs'); if (!response.ok) throw new Error('Could not load the log.');
    const data = await response.json(); const logs = data.logs || []; $('#activity-count').textContent = `${logs.length} events`;
    $('#activity-list').innerHTML = logs.length ? logs.slice(0, 5).map(log => `<div class="activity-item"><span class="activity-dot ${log.status === 'error' ? 'error' : ''}"></span><div class="activity-text"><strong>${log.status === 'success' ? 'Collection complete' : 'Collection error'}</strong> · ${log.rows_collected} observation(s)<small>${formatDate(log.finished_at, true)} · ${escapeHtml(log.message === 'Collecte terminée avec succès.' ? 'Collection completed successfully.' : log.message)}</small></div></div>`).join('') : '<div class="empty">No collections have run since startup.</div>';
  } catch (error) { $('#activity-count').textContent = 'Unavailable'; $('#activity-list').innerHTML = '<div class="empty error-note">Log temporarily unavailable.</div>'; }
}

document.querySelectorAll('.mode-btn').forEach(button => button.addEventListener('click', () => {
  chartMode = button.dataset.chartMode;
  document.querySelectorAll('.mode-btn').forEach(item => { const active = item === button; item.classList.toggle('active', active); item.setAttribute('aria-pressed', active ? 'true' : 'false'); });
  loadDashboard();
}));

const navigation = $('.sidebar');
const navigationToggle = $('#nav-toggle');
const navigationItems = [...document.querySelectorAll('.nav-item')];
const pageViews = [...document.querySelectorAll('[data-page]')];
const mobileNavigation = window.matchMedia('(max-width: 760px)');
const pageDetails = {
  marches: ['Markets', 'Key prices at a glance.'],
  tendances: ['Trends', 'Explore 7, 30, and 90-day forecasts.'],
  veille: ['Alerts', 'Monitor the thresholds that matter.'],
  archives: ['History', 'Review observations and compare periods.'],
  'nouveau-prix': ['New price', 'Add a verified bitumen price or quote.'],
  fournisseurs: ['Suppliers', 'Find contacts and prepare price requests.'],
  budget: ['Budget', 'Estimate purchase costs and their impact.'],
  activite: ['Activity', 'Review data quality and recent collections.'],
  equipe: ['My account / Team', 'Manage access and review your team’s actions.']
};
// Preserve links saved before the page names changed.
const pageAliases = {
  overview: 'marches', forecast: 'tendances', alerts: 'veille',
  history: 'archives', 'history-page': 'archives', compare: 'archives',
  manual: 'nouveau-prix', 'buying-guide': 'fournisseurs',
  procurement: 'budget', sources: 'activite'
};
let activePage = null;

function setNavigationOpen(open) {
  navigation.classList.toggle('nav-open', open);
  navigationToggle.setAttribute('aria-expanded', String(open));
  navigationToggle.querySelector('.sr-only').textContent = open ? 'Close navigation' : 'Open navigation';
  $('#primary-navigation').inert = mobileNavigation.matches && !open;
}

function resolvePage(hash) {
  const key = hash.replace(/^#/, '');
  if (Object.hasOwn(pageDetails, key)) return key;
  return Object.hasOwn(pageAliases, key) ? pageAliases[key] : 'marches';
}

function updatePageChrome(key) {
  const [title, description] = pageDetails[key];
  $('#page-title').textContent = title;
  $('#page-description').textContent = description;
  document.title = `${title} · Price Monitor`;
  navigationItems.forEach(item => {
    const selected = item.getAttribute('href') === `#${key}`;
    item.classList.toggle('active', selected);
    if (selected) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  });
}

function showPage({ animate = true, focus = true, scroll = true } = {}) {
  const key = resolvePage(window.location.hash);
  const next = document.getElementById(key);
  setNavigationOpen(false);
  if (window.location.hash !== `#${key}`) window.history.replaceState(null, '', `#${key}`);
  const changed = activePage !== next;
  pageViews.forEach(page => { page.hidden = false; });
  activePage = next;
  updatePageChrome(key);
  if (scroll && changed) {
    next.scrollIntoView({ behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'start' });
    if (focus) $('#page-title').focus({ preventScroll: true });
  }
  if (key === 'marches') chart?.resize();
  if (key === 'tendances') Object.values(forecastCharts).forEach(instance => instance.resize());
  if (changed && animate && !reducedMotion.matches && typeof next.animate === 'function') {
    next.animate(
      [{ opacity: .72, transform: 'translateY(10px)' }, { opacity: 1, transform: 'translateY(0)' }],
      { duration: 260, easing: 'cubic-bezier(.22, 1, .36, 1)' }
    );
  }
}

navigationToggle.addEventListener('click', () => setNavigationOpen(!navigation.classList.contains('nav-open')));
mobileNavigation.addEventListener('change', () => setNavigationOpen(false));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && navigation.classList.contains('nav-open')) {
    setNavigationOpen(false);
    navigationToggle.focus();
  }
});
document.addEventListener('click', event => {
  const link = event.target.closest('a[href^="#"]');
  if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const hash = link.getAttribute('href');
  if (hash === '#main-content') return;
  const target = hash.slice(1);
  if (!Object.hasOwn(pageDetails, target) && !Object.hasOwn(pageAliases, target)) return;
  event.preventDefault();
  const nextHash = `#${resolvePage(hash)}`;
  if (window.location.hash !== nextHash) window.history.pushState(null, '', nextHash);
  showPage();
});
window.addEventListener('hashchange', () => {
  if (window.location.hash !== '#main-content') showPage();
});
if ('IntersectionObserver' in window) {
  const pageObserver = new IntersectionObserver(entries => {
    const visible = entries.filter(entry => entry.isIntersecting)
      .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    const key = visible.target.dataset.page;
    if (!Object.hasOwn(pageDetails, key) || activePage === visible.target) return;
    activePage = visible.target;
    updatePageChrome(key);
    window.history.replaceState(null, '', `#${key}`);
  }, { rootMargin: '-18% 0px -62% 0px', threshold: [0.05, 0.2, 0.5] });
  pageViews.forEach(page => pageObserver.observe(page));
}
document.documentElement.classList.add('pages-ready');
setNavigationOpen(false);
showPage({ animate: false, focus: false, scroll: false });

$('#period-select').addEventListener('change', loadDashboard);
$('#forecast-horizon').addEventListener('change', renderForecast);
$('#refresh-btn').addEventListener('click', () => { loadDashboard(); loadForecast(); loadAlerts(); loadLogs(); loadHistory(historyPage); loadBitumenHistory(bitumenHistoryPage); loadProcurements(); loadProcurementGuide(); showToast('Dashboard refreshed.'); });
$('#collect-btn').addEventListener('click', async () => {
  const button = $('#collect-btn');
  button.disabled = true;
  button.innerHTML = 'Collecting…';
  try {
    const response = await protectedFetch('/api/collect', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
    const result = await response.json();
    if (!response.ok || result.status === 'error') throw new Error(result.message || result.error || 'Collection failed.');
    renderSourceComparison(result.source_comparison);
    const mismatch = result.source_comparison?.mismatch_count > 0;
    const deferred = (result.providers || []).filter(provider => provider.status === 'deferred');
    const info = result.status === 'deferred' ? result.message : `${result.rows} observation(s) saved.${deferred.length ? ` ${deferred.length} provider(s) waiting — details in Activity.` : ''}`;
    showToast(mismatch ? result.source_comparison.message : info, mismatch || result.status === 'partial');
  } catch (error) {
    showToast(error.message, true);
  } finally {
    document.dispatchEvent(new Event('collection-finished'));
    button.disabled = false;
    button.innerHTML = '<span>↻</span> Collect now';
    loadDashboard();
    loadLogs();
  }
});
$('#bitumen-form').addEventListener('submit', async (event) => { event.preventDefault(); const data = Object.fromEntries(new FormData(event.target)); data.product = 'bitume'; data.unit = 'USD/tonne'; try { const response = await protectedFetch('/api/observations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }); const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Could not save the observation.'); showToast('Bitumen observation saved.'); event.target.reset(); event.target.source_date.value = new Date().toISOString().slice(0, 10); loadDashboard(); loadHistory(historyPage); } catch (error) { showToast(error.message, true); } });
$('#procurement-product').addEventListener('change', updateProcurementUnits);
$('#procurement-history-retry').addEventListener('click', loadProcurements);
$('#buy-product').addEventListener('change', loadProcurementGuide);
$('#buy-region').addEventListener('change', loadProcurementGuide);
$('#procurement-currency').addEventListener('change', () => {
  const input = $('#exchange-rate');
  if ($('#procurement-currency').value === 'USD') input.value = '1';
  else if (input.value === '1') input.value = '';
  updateProcurementUnits();
});
$('#procurement-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.target));
  try {
    if (typeof quoteOptions === 'undefined' || !quoteOptions) {
      if (typeof initialiseQuotes === 'function') await initialiseQuotes();
      if (typeof quoteOptions === 'undefined' || !quoteOptions) throw new Error('Full cost calculation unavailable. Restart the application after updating it, then try again.');
    }
    data.other_costs = readCostItems($('#procurement-other-costs'));
    const response = await protectedFetch('/api/procurements', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not evaluate this purchase.');
    renderProcurementSummary(result.purchase);
    showToast('Purchase impact calculated and saved.');
    event.target.reset();
    resetProcurementQuoteLink();
    setCostItems($('#procurement-other-costs'));
    event.target.purchase_date.value = new Date().toISOString().slice(0, 10);
    event.target.exchange_rate.value = '1';
    updateProcurementUnits();
    loadProcurements();
  } catch (error) { showToast(error.message, true); }
});
$('#history-filters').addEventListener('submit', event => { event.preventDefault(); loadHistory(1); });
$('#history-filters').addEventListener('change', event => {
  if (event.target.name === 'product') {
    const form = $('#history-filters');
    const values = Object.fromEntries(new FormData(form));
    const product = values.product;
    const selected = values.instrument;
    const matches = !selected || !product || ALERT_INSTRUMENTS[product].some(([id]) => id === selected);
    if (!matches) form.instrument.value = '';
  }
  if (event.target.name === 'instrument') {
    const form = $('#history-filters');
    const owner = Object.entries(ALERT_INSTRUMENTS).find(([, choices]) => choices.some(([id]) => id === form.instrument.value))?.[0];
    if (owner && form.product.value && form.product.value !== owner) form.product.value = owner;
  }
  if (['product', 'data_kind', 'instrument'].includes(event.target.name)) loadHistory(1);
});
$('#history-reset').addEventListener('click', () => { $('#history-filters').reset(); loadHistory(1); });
$('#history-prev').addEventListener('click', () => { if (historyPage > 1) loadHistory(historyPage - 1); });
$('#history-next').addEventListener('click', () => { if (historyPage < historyPages) loadHistory(historyPage + 1); });
$('#bitumen-history-prev').addEventListener('click', () => { if (bitumenHistoryPage > 1) loadBitumenHistory(bitumenHistoryPage - 1); });
$('#bitumen-history-next').addEventListener('click', () => { if (bitumenHistoryPage < bitumenHistoryPages) loadBitumenHistory(bitumenHistoryPage + 1); });
$('#compare-form').addEventListener('submit', event => { event.preventDefault(); compareHistory(); });
$('#alert-new').addEventListener('click', resetAlertForm);
$('#alert-cancel').addEventListener('click', resetAlertForm);
$('#alert-form').direction.addEventListener('change', updateAlertConditionFields);
$('#alert-form').product.addEventListener('change', updateAlertConditionFields);
$('#alert-form').basis.addEventListener('change', updateAlertConditionFields);
$('#forecast-bitume-horizon').addEventListener('change', () => renderForecastProduct('bitume'));
['brent', 'gasoil'].forEach(product => $(`#market-${product}-instrument`).addEventListener('change', loadDashboard));
$('#alert-list').addEventListener('click', async event => {
  const button = event.target.closest('button[data-alert-action]');
  if (!button) return;
  const id = Number(button.dataset.alertId);
  const rule = alertRules.find(item => item.id === id);
  if (!rule) return;
  try {
    if (button.dataset.alertAction === 'edit') return editAlert(id);
    if (button.dataset.alertAction === 'mute') {
      await mutateAlert(id, { muted: !rule.muted });
      await loadAlerts();
      showToast(rule.muted ? 'Alert resumed.' : 'Alert paused.');
    }
    if (button.dataset.alertAction === 'delete') {
      if (!window.confirm(`Delete the ${rule.product} ${rule.direction} ${rule.threshold} rule?`)) return;
      const response = await protectedFetch(`/api/alerts/${id}`, { method: 'DELETE' });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not delete this alert.');
      if (editingAlertId === id) resetAlertForm();
      await loadAlerts();
      showToast('Alert deleted.');
    }
  } catch (error) { showToast(error.message, true); }
});
$('#alert-form').addEventListener('submit', async event => {
  event.preventDefault();
  const wasEditing = Boolean(editingAlertId);
  const values = Object.fromEntries(new FormData(event.target));
  values.threshold = Number(values.threshold);
  values.muted = event.target.muted.checked;
  try {
    const url = editingAlertId ? `/api/alerts/${editingAlertId}` : '/api/alerts';
    const response = await protectedFetch(url, { method: editingAlertId ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not save this alert.');
    resetAlertForm();
    await loadAlerts();
    showToast(wasEditing ? 'Alert updated.' : 'Alert created.');
  } catch (error) { showToast(error.message, true); }
});
$('#bitumen-form').source_date.value = new Date().toISOString().slice(0, 10);
$('#procurement-form').purchase_date.value = new Date().toISOString().slice(0, 10);
setDefaultComparePeriods();
updateProcurementUnits();
updateAlertConditionFields();
loadDashboard(); loadForecast(); loadAlerts(); loadLogs(); loadHistory(); loadBitumenHistory(); loadProcurements(); loadProcurementGuide();
connectionMonitor.setBrowserOnline(navigator.onLine !== false);
connectionMonitor.check();
// The local chart library can finish after the first saved-data response.
$('#chart-library').addEventListener('load', () => {
  loadDashboard();
  if (forecastData) renderForecast();
});
window.addEventListener('offline', () => connectionMonitor.setBrowserOnline(false));
window.addEventListener('online', () => {
  connectionMonitor.setBrowserOnline(true);
  connectionMonitor.check();
});
window.setInterval(() => {
  if (!document.hidden) connectionMonitor.check();
}, 5000);

// Keep an open dashboard current without making background tabs do work. The
// server performs the actual collection; this only displays its latest result.
function refreshLiveDashboard() {
  if (document.hidden) return;
  loadDashboard();
  loadLogs();
  loadForecast();
}
window.setInterval(refreshLiveDashboard, DASHBOARD_REFRESH_MS);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    connectionMonitor.setBrowserOnline(navigator.onLine !== false);
    connectionMonitor.check();
    refreshLiveDashboard();
  }
});
