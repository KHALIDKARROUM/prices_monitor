const PRODUCT_META = {
  gasoil: { label: 'Gasoil / diesel', short: 'Gasoil', color: '#a9d5bc', purchaseUnit: 'gallon' },
  brent: { label: 'Pétrole Brent', short: 'Brent', color: '#dfbb83', purchaseUnit: 'baril' },
  bitume: { label: 'Bitume', short: 'Bitume', color: '#b4aed4', purchaseUnit: 'tonne' }
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
const formatPrice = (value, unit) => value == null ? '—' : `${Number(value).toLocaleString('fr-FR', { maximumFractionDigits: 2 })} <small>${unit || ''}</small>`;
const formatPct = (value) => value == null ? '—' : `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(2).replace('.', ',')} %`;
const formatDate = (value, withTime = false) => value ? new Date(value).toLocaleString('fr-FR', withTime ? { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' } : { day: '2-digit', month: 'short', year: 'numeric' }) : '—';
const formatNumber = (value, digits = 2) => Number(value).toLocaleString('fr-FR', { maximumFractionDigits: digits });
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
  const headers = new Headers(options.headers || {});
  const apiKey = overrideApiKey || getStoredApiKey();
  if (apiKey) headers.set('X-API-Key', apiKey);
  const response = await fetch(url, { ...options, headers });
  if (response.status !== 401 || !allowPrompt) return response;
  const enteredKey = window.prompt('Cette action nécessite la clé API Price Monitor :');
  if (!enteredKey?.trim()) return response;
  storeApiKey(enteredKey.trim());
  return protectedFetch(url, options, false, enteredKey.trim());
}

function setSystemStatus(message, kind = '') {
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
      ? '<span class="freshness missing">Aucune donnée</span>'
      : m.data_kind === 'index'
        ? `<span class="freshness ${m.is_stale ? 'stale' : 'current'}">Indice mensuel · ${new Date(m.source_date).toLocaleDateString('fr-FR', { month: 'short', year: 'numeric' })}${m.is_stale ? ' · ancien' : ''}</span>`
      : m.is_live
        ? `<span class="freshness current" title="${escapeHtml(m.source || 'Flux de marché')}">En direct · ${formatDate(m.quote_at, true)}</span>`
      : m.quote_at
        ? `<span class="freshness stale" title="${escapeHtml(m.source || 'Flux de marché')}">Dernière cotation · ${formatDate(m.quote_at, true)}</span>`
      : m.source?.startsWith('Yahoo Finance - ')
        ? '<span class="freshness stale">Heure de cotation indisponible</span>'
      : m.is_stale
        ? `<span class="freshness stale" title="Dernière publication : ${escapeHtml(m.source_date)}">Donnée ancienne · ${m.age_days} j</span>`
        : `<span class="freshness current">À jour · ${formatDate(m.source_date)}</span>`;
    const context = m.data_kind === 'index' ? `<small class="metric-context">Tendance BLS WPU058 · ${m.is_stale ? 'Dernier indice conservé' : 'Dernier mois publié'}. ${m.supplier_quote ? `Dernier devis : ${formatPrice(m.supplier_quote.price, m.supplier_quote.unit)} · ${formatDate(m.supplier_quote.source_date)}` : 'Devis fournisseur : aucun enregistré.'}</small>` : '';
    return `<article class="metric-card" style="color:${meta.color}"><div class="metric-head"><span class="metric-label">${escapeHtml(m.label || meta.label)}</span>${freshness}<i class="metric-dot" style="background:${meta.color}"></i></div><div class="metric-price">${formatPrice(m.current, m.unit)}</div><span class="trend ${cls}">${m.variation == null ? '—' : (m.variation >= 0 ? '▲' : '▼') + ' ' + formatPct(m.variation)} <span class="muted">${m.data_kind === 'index' ? 'vs mois précédent' : 'vs précédent'}</span></span>${context}<span class="metric-foot"><span>Moy. ${m.average == null ? '—' : formatNumber(m.average)}</span><span class="range">Min–max ${range}</span><span class="count">${m.count || 0} relevés${m.data_kind === 'index' ? ' mensuels' : ''}</span></span></article>`;
  }).join('');
}

function forecastDate(value) {
  return new Date(`${value}T00:00:00Z`).toLocaleDateString('fr-FR', { day: '2-digit', month: 'short' });
}

function renderForecastProduct(key) {
  const meta = PRODUCT_META[key];
  const product = forecastData?.products?.[key];
  const canvas = $(`#forecast-${key}-chart`);
  const stats = $(`#forecast-${key}-stats`);
  const quality = $(`#forecast-${key}-quality`);
  if (forecastCharts[key]) { forecastCharts[key].destroy(); delete forecastCharts[key]; }
  if (!product || product.status !== 'ready') {
    canvas.hidden = true;
    quality.hidden = true;
    quality.innerHTML = '';
    stats.innerHTML = `<span class="error-note">${escapeHtml(product?.message || 'Prévision indisponible.')}</span>`;
    return;
  }
  const horizon = $(key === 'bitume' ? '#forecast-bitume-horizon' : '#forecast-horizon').value;
  const selection = product.model_selection?.[horizon] || {};
  const modelLabel = selection.selected_model === 'holt' ? 'Holt amorti' : 'Dernier prix connu';
  const warnings = selection.warnings || [];
  quality.hidden = false;
  quality.classList.toggle('has-warning', warnings.length > 0);
  quality.innerHTML = `<strong>Modèle retenu : ${modelLabel}</strong>${warnings.map(warning => `<p>${escapeHtml(warning.message)}</p>`).join('')}`;
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
  canvas.hidden = false;
  if (!window.Chart) {
    stats.innerHTML = '<span class="error-note">Chart.js est indisponible.</span>';
    return;
  }
  const chartContext = canvas.getContext('2d');
  forecastCharts[key] = new Chart(chartContext, {
    type: 'line',
    data: { labels: labels.map(forecastDate), datasets: [
      { label: 'Borne basse', data: lower, borderColor: 'transparent', backgroundColor: 'rgba(200, 200, 200, .1)', pointRadius: 0, fill: false, spanGaps: false },
      { label: 'Bande calibrée · 95 % nominal', data: upper, borderColor: 'transparent', backgroundColor: 'rgba(200, 200, 200, .1)', pointRadius: 0, fill: '-1', spanGaps: false },
      { label: 'Réel', data: actualSeries, borderColor: meta.color, backgroundColor: meta.color, borderWidth: 2, pointRadius: 0, tension: .3, spanGaps: true },
      { label: modelLabel, data: forecast, borderColor: '#f0f7f9', backgroundColor: '#f0f7f9', borderWidth: 2, borderDash: [6, 4], pointRadius: 0, tension: .3, spanGaps: true }
    ] },
    options: { animation: reducedMotion.matches ? false : { duration: 350 }, responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false }, plugins: { legend: { display: false }, tooltip: { backgroundColor: '#202020', padding: 10, callbacks: { label: item => ` ${item.dataset.label}: ${formatNumber(item.raw, item.raw < 10 ? 3 : 2)} ${escapeHtml(product.unit)}` } } }, scales: { x: { grid: { display: false }, ticks: { color: '#a0a0a0', maxTicksLimit: 8, font: { size: 10 } } }, y: { grid: { color: 'rgba(255,255,255,.07)' }, ticks: { color: '#a0a0a0', font: { size: 10 }, callback: value => formatNumber(value, value < 10 ? 3 : 0) }, border: { display: false } } } }
  });
  const lastActual = actual.at(-1);
  const finalForecast = future.at(-1);
  const backtest = product.backtest?.[horizon] || {};
  const change = lastActual?.value ? ((finalForecast.value - lastActual.value) / lastActual.value) * 100 : null;
  stats.innerHTML = `<div><span>Dernier réel</span><strong>${formatNumber(lastActual?.value, lastActual?.value < 10 ? 3 : 2)}</strong></div><div><span>${key === 'bitume' ? 'À M+' : 'À J+'}${horizon}</span><strong>${formatNumber(finalForecast?.value, finalForecast?.value < 10 ? 3 : 2)}</strong><em class="${change >= 0 ? 'up' : 'down'}">${formatPct(change)}</em></div><div><span>MAPE · test final (${backtest.samples || 0} prévisions)</span><strong>${backtest.mape == null ? '—' : `${formatNumber(backtest.mape, 1)} %`}</strong></div><div><span>Gain MAE vs prix constant</span><strong>${backtest.mae_skill_vs_naive == null ? '—' : `${formatNumber(100 * backtest.mae_skill_vs_naive, 1)} %`}</strong></div><div><span>Couverture · test final / 95 % nominal</span><strong>${backtest.interval_coverage == null ? '—' : `${formatNumber(100 * backtest.interval_coverage, 1)} %`}</strong></div><div><span>Origine de la prévision</span><strong>${formatDate(product.forecast_origin || lastActual?.date)}</strong></div>`;
}

function renderForecast() {
  renderForecastProduct('gasoil');
  renderForecastProduct('brent');
  renderForecastProduct('bitume');
}

async function loadForecast() {
  try {
    const response = await fetch('/api/forecast?history_days=1825');
    if (!response.ok) throw new Error('Impossible de calculer les prévisions.');
    forecastData = await response.json();
    const generated = forecastData.generated_at ? formatDate(forecastData.generated_at, true) : 'à l’instant';
    const stale = Object.values(forecastData.products || {}).filter(product => product.is_stale);
    $('#forecast-status').textContent = `Modèle choisi par horizon · prix : jours calendaires · indice bitume : mois · calculé ${generated}${stale.length ? ' · Attention : dernières données anciennes, projections ancrées sur leur date source.' : ''}`;
    $('#forecast-status').classList.toggle('error-note', stale.length > 0);
    renderForecast();
  } catch (error) {
    $('#forecast-status').textContent = error.message;
    ['gasoil', 'brent', 'bitume'].forEach(key => { $(`#forecast-${key}-stats`).innerHTML = '<span class="error-note">Prévision momentanément indisponible.</span>'; });
  }
}

const ALERT_UNITS = { gasoil: 'USD/gallon', brent: 'USD/baril', bitume: 'USD/tonne' };
const ALERT_INSTRUMENTS = {
  gasoil: [['diesel_spot', 'Diesel New York Harbor · spot'], ['heating_oil_futures', 'Heating oil HO=F · proxy gasoil']],
  brent: [['brent_spot', 'Brent Europe · spot'], ['brent_futures', 'Brent BZ=F · contrat à terme']],
  bitume: [['bitumen_quote', 'Bitume · devis fournisseur'], ['asphalt_index', 'Bitume · indice BLS']]
};
const ALERT_CHANNEL_LABELS = { webhook: 'Webhook', email: 'Email', both: 'Webhook + email' };
const ALERT_STATUS_LABELS = { active: 'Active', recovered: 'Rétablie', muted: 'En pause', normal: 'Surveillée' };

function resetAlertForm() {
  editingAlertId = null;
  $('#alert-form').reset();
  $('#alert-form-title').textContent = 'Créer une règle';
  $('#alert-save').innerHTML = `Créer l'alerte <span>→</span>`;
  $('#alert-cancel').hidden = true;
  updateAlertConditionFields();
}

function updateAlertConditionFields() {
  const form = $('#alert-form');
  const freshness = form.direction.value === 'stale';
  $('#alert-threshold-label').textContent = freshness ? 'Délai sans nouvelle cotation (minutes)' : 'Seuil de prix';
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
  if (!freshness) $('#alert-threshold-label').textContent = bitumen && form.basis.value === 'index' ? 'Seuil de l’indice BLS (déc. 1984 = 100)' : `Seuil de prix (${ALERT_UNITS[form.product.value]})`;
}

function renderAlertRules() {
  $('#alert-list').innerHTML = alertRules.length ? alertRules.map(rule => {
    const meta = { ...(PRODUCT_META[rule.product] || {}), label: ALERT_INSTRUMENTS[rule.product]?.find(([id]) => id === rule.instrument)?.[1] || rule.product };
    const status = ALERT_STATUS_LABELS[rule.status] || ALERT_STATUS_LABELS.normal;
    const freshness = rule.direction === 'stale';
    const condition = freshness ? 'Cotation non actualisée depuis' : rule.direction === 'above' ? 'au-dessus de' : 'en-dessous de';
    const threshold = freshness ? `${formatNumber(rule.threshold, 0)} min` : `${formatNumber(rule.threshold, rule.threshold < 10 ? 3 : 2)} ${rule.basis === 'index' ? 'indice (déc. 1984 = 100)' : ALERT_UNITS[rule.product]}`;
    const lastValue = rule.last_value == null ? 'Pas encore évaluée' : freshness ? `Âge de la cotation : ${formatNumber(rule.last_value, 0)} min` : `${rule.basis === 'index' ? 'Dernier indice' : 'Dernier prix'} : ${formatNumber(rule.last_value, rule.last_value < 10 ? 3 : 2)} ${rule.basis === 'index' ? 'indice (déc. 1984 = 100)' : ALERT_UNITS[rule.product]}`;
    return `<article class="alert-rule ${rule.status}"><div class="alert-rule-main"><span class="alert-dot"></span><div><strong>${escapeHtml(meta.label)}</strong><p>${condition} <b>${threshold}</b></p><small>${lastValue} · ${escapeHtml(ALERT_CHANNEL_LABELS[rule.channel] || rule.channel)}</small></div></div><div class="alert-rule-actions"><span class="alert-status ${rule.status}">${status}</span><button class="text-btn" type="button" data-alert-action="edit" data-alert-id="${rule.id}">Modifier</button><button class="text-btn" type="button" data-alert-action="mute" data-alert-id="${rule.id}">${rule.muted ? 'Réactiver' : 'Mettre en pause'}</button><button class="text-btn danger-btn" type="button" data-alert-action="delete" data-alert-id="${rule.id}">Supprimer</button></div></article>`;
  }).join('') : '<div class="empty">Aucune règle configurée. Créez un seuil pour commencer la surveillance.</div>';
}

function renderAlertChannels(channels = {}) {
  const configured = Object.entries(channels).filter(([key, value]) => key !== 'both' && value).map(([key]) => ALERT_CHANNEL_LABELS[key]);
  $('#alert-channel-status').textContent = configured.length ? `Canaux configurés : ${configured.join(' · ')}.${!channels.email ? ' Pour les alertes email, configurez PRICE_MONITOR_ALERT_EMAIL_TO et les paramètres SMTP dans .env.' : ''}` : 'Aucun canal de notification configuré. Pour recevoir les alertes email, renseignez PRICE_MONITOR_ALERT_EMAIL_TO et les paramètres SMTP dans .env.';
  $('#alert-channel-status').classList.toggle('error-note', !configured.length);
}

async function loadAlerts() {
  try {
    const response = await fetch('/api/alerts');
    if (!response.ok) throw new Error('Impossible de charger les alertes.');
    const data = await response.json();
    alertRules = data.alerts || [];
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
  $('#alert-form-title').textContent = 'Modifier la règle';
  $('#alert-save').innerHTML = `Enregistrer les changements <span>→</span>`;
  $('#alert-cancel').hidden = false;
  form.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function mutateAlert(id, payload) {
  const response = await protectedFetch(`/api/alerts/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Impossible de modifier cette alerte.');
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
  if (chart) { chart.destroy(); chart = null; }
  ctx.parentElement.querySelector('.chart-fallback')?.remove();
  if (!labels.length) {
    ctx.hidden = true;
    empty.hidden = false;
    empty.innerHTML = '<strong>Aucun relevé pour cette période</strong><span>Choisissez 12 mois ou 5 ans, ou lancez une nouvelle collecte.</span>';
    $('#legend').innerHTML = '';
    $('#chart-note').textContent = 'Le graphique apparaîtra dès qu’un relevé daté de cette période sera disponible.';
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
        label: `${meta.short} · dernière cotation`,
        data: labels.map(date => date === latestDate || date === today ? displayedLatestPrice : null),
        actualData: labels.map(date => date === latestDate || date === today ? latestPrice : null),
        borderColor: meta.color,
        backgroundColor: meta.color,
        borderWidth: 2,
        borderDash: [6, 5],
        pointRadius: 0,
        tension: 0,
        spanGaps: true,
        unit: latest.unit || '', isLatestCarryForward: true
      });
    }
    return { label: key === 'bitume' && rows.some(row => row.data_kind === 'index') ? 'Bitume · indice BLS' : ALERT_INSTRUMENTS[key]?.find(([id]) => id === rows[0]?.instrument)?.[1] || meta.short, unit: rows[0]?.unit || '', data, actualData: labels.map(date => byDate[date] ?? null), borderColor: meta.color, backgroundColor: meta.color, borderWidth: 2, pointRadius: 0, tension: .35, spanGaps: true };
  });
  $('#legend').innerHTML = datasets.map(d => `<span><i style="background:${d.borderColor}"></i>${d.label}</span>`).join('');
  datasets.push(...latestSegments);
  const bitumenNote = (series.bitume || []).some(row => row.data_kind === 'index') ? ' Bitume : indice BLS mensuel; le dernier mois connu sert de point de départ si la période commence après sa publication.' : '';
  const chartDescription = (chartMode === 'indexed' ? 'Évolution relative depuis le premier relevé de la période.' : 'Valeurs brutes : chaque série conserve son unité d’origine.') + bitumenNote;
  $('#chart-note').textContent = latestSegments.length
    ? `${chartDescription} Le trait pointillé prolonge la dernière cotation publiée jusqu’à maintenant ; il ne représente pas un nouveau prix.`
    : `${chartDescription} Toutes les séries disponibles sont publiées aujourd’hui.`;
  if (!window.Chart) { ctx.hidden = true; empty.hidden = true; if (!ctx.parentElement.querySelector('.chart-fallback')) ctx.insertAdjacentHTML('afterend', '<div class="empty chart-fallback">Le graphique sera disponible après chargement de Chart.js.</div>'); return; }
  ctx.hidden = false; ctx.parentElement.querySelector('.chart-fallback')?.remove();
  chart = new Chart(ctx, { type: 'line', data: { labels: labels.map(d => new Date(`${d}T00:00:00`).toLocaleDateString('fr-FR', { day: '2-digit', month: 'short' })), datasets }, options: { animation: reducedMotion.matches ? false : { duration: 350 }, responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false }, plugins: { legend: { display: false }, tooltip: { backgroundColor: '#202020', padding: 12, titleColor: '#fff', bodyColor: '#cccccc', callbacks: { label: item => { const actual = item.dataset.actualData?.[item.dataIndex]; const value = chartMode === 'indexed' ? `${formatNumber(item.raw, 1)} · ${actual == null ? '—' : formatNumber(actual)}` : formatNumber(actual ?? item.raw); const label = item.dataset.isLatestCarryForward ? 'Dernière cotation connue (prolongée)' : item.dataset.label; return ` ${label}: ${value} ${item.dataset.unit || ''}`; } } } }, scales: { x: { grid: { display: false }, ticks: { color: '#a0a0a0', maxTicksLimit: 7, font: { size: 10 } } }, y: { grid: { color: 'rgba(255,255,255,.07)' }, ticks: { color: '#a0a0a0', font: { size: 10 }, callback: value => chartMode === 'indexed' ? `${value}` : formatNumber(value, 0) }, title: { display: true, text: chartMode === 'indexed' ? 'Base 100 sur la période' : 'Prix / indice', color: '#a0a0a0', font: { size: 10, weight: '600' } }, border: { display: false } } } } });
}

function renderLatest(rows) {
  $('#latest-table').innerHTML = rows.length ? rows.map(row => {
    const meta = PRODUCT_META[row.product]; const pct = row.variation_pct; const cls = pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
    return `<tr><td><div class="product-cell"><i style="background:${meta.color}"></i><strong>${meta.label}${row.data_kind === 'index' ? ' · indice BLS' : row.product === 'bitume' ? ' · devis' : ''}</strong></div></td><td class="price-cell">${formatPrice(row.price, row.unit)}</td><td><span class="variation trend ${cls}">${formatPct(pct)}</span></td><td>${formatDate(row.source_date)}</td><td>${formatDate(row.collected_at, true)}</td><td class="source-cell">${escapeHtml(row.source)}<br>${row.is_unchanged ? '<span class="unchanged">Publication inchangée</span>' : ''}</td><td></td></tr>`;
  }).join('') : '<tr><td colspan="7" class="empty">Aucun relevé sur la période.</td></tr>';
}

function renderProcurementSummary(purchase) {
  const summary = $('#procurement-summary');
  const budgetClass = purchase.budget_variance == null ? '' : purchase.budget_variance > 0 ? 'up' : 'down';
  const impactClass = purchase.price_impact_unit_usd == null ? '' : purchase.price_impact_unit_usd > 0 ? 'up' : 'down';
  summary.innerHTML = `<div class="purchase-stat"><span>Coût total</span><strong>${formatMoney(purchase.total_cost, purchase.currency)}</strong><small>${formatMoney(purchase.total_cost_usd, 'USD')}</small></div><div class="purchase-stat"><span>Écart au budget</span><strong class="${budgetClass}">${formatMoney(purchase.budget_variance, purchase.currency)}</strong><small>${purchase.budget_variance_usd == null ? 'Budget non renseigné' : formatMoney(purchase.budget_variance_usd, 'USD')}</small></div><div class="purchase-stat"><span>Prix marché de référence</span><strong>${formatMoney(purchase.market_price_usd, 'USD')}</strong><small>Dernier relevé ${PRODUCT_META[purchase.product]?.purchaseUnit || ''}</small></div><div class="purchase-stat"><span>Impact du prix</span><strong class="${impactClass}">${formatMoney(purchase.price_impact_unit_usd, 'USD')} / ${PRODUCT_META[purchase.product]?.purchaseUnit || 'unité'}</strong><small>${purchase.price_impact_pct == null ? 'Référence indisponible' : `${formatPct(purchase.price_impact_pct)} · ${formatMoney(purchase.price_impact_total_usd, 'USD')} au total`}</small></div>`;
  summary.hidden = false;
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
    setProcurementHistoryMessage('Aucun achat enregistré. Vos estimations de budget apparaîtront ici après un calcul.');
    return;
  }
  $('#procurement-history-state').hidden = true;
  $('#procurement-history-table').hidden = false;
  $('#procurement-table').innerHTML = rows.length ? rows.map(row => {
    const meta = PRODUCT_META[row.product] || { label: row.product, color: '#a7bac2', purchaseUnit: row.unit };
    const varianceClass = row.budget_variance == null ? '' : row.budget_variance > 0 ? 'negative' : 'positive';
    const impactClass = row.price_impact_pct == null ? '' : row.price_impact_pct > 0 ? 'negative' : 'positive';
    const budgetPct = row.budget_amount > 0 ? ` (${formatPct(row.budget_variance / row.budget_amount * 100)})` : '';
    return `<tr><td>${formatDate(row.purchase_date)}</td><td><div class="product-cell"><i style="background:${meta.color}"></i><strong>${meta.label}</strong></div></td><td>${escapeHtml(row.supplier)}</td><td>${formatNumber(row.quantity, 2)} ${meta.purchaseUnit}</td><td class="price-cell">${formatMoney(row.total_cost, row.currency)}<br><span class="source-cell">${formatMoney(row.total_cost_usd, 'USD')}</span></td><td class="${varianceClass}">${row.budget_variance == null ? '—' : `${formatMoney(row.budget_variance, row.currency)}${budgetPct}`}</td><td class="${impactClass}">${row.price_impact_pct == null ? '—' : `${formatPct(row.price_impact_pct)}<br><span class="source-cell">${formatMoney(row.price_impact_total_usd, 'USD')}</span>`}</td></tr>`;
  }).join('') : '<tr><td colspan="7" class="empty">Aucun achat enregistré.</td></tr>';
}

async function loadProcurements() {
  try {
    const response = await protectedFetch('/api/procurements?limit=20');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Impossible de charger les achats.');
    renderProcurements(data.purchases || []);
  } catch (error) {
    setProcurementHistoryMessage(`Historique des achats indisponible. ${error.message}`, true);
  }
}

const REGION_LABELS = {
  global: 'International', europe: 'Europe', africa_middle_east: 'Afrique & Moyen-Orient',
  asia_pacific: 'Asie-Pacifique', americas: 'Amériques', local: 'Local'
};

function renderMarketContext(data) {
  const context = data.market_context || {};
  const price = context.latest_price;
  const priceLabel = data.product === 'bitume' ? 'Dernier devis enregistré' : 'Dernier benchmark gasoil';
  const priceDetail = price
    ? `<strong>${formatNumber(price.price, price.price < 10 ? 3 : 2)} ${escapeHtml(price.unit)}</strong><small>${formatDate(price.source_date)} · ${escapeHtml(price.source)}</small>`
    : '<strong>—</strong><small>Aucun relevé disponible.</small>';
  const benchmarkCards = (context.benchmarks || []).map(row =>
    `<div class="market-card"><span>${escapeHtml(row.label)}</span><strong>${formatNumber(row.value, 3)} ${escapeHtml(row.unit)}</strong><small>${formatDate(row.source_date)} · ${escapeHtml(row.geography)} · ${formatPct(row.variation_pct)}</small></div>`
  ).join('');
  $('#market-context').innerHTML = `<div class="market-card"><span>${priceLabel}</span>${priceDetail}</div>${benchmarkCards}<div class="market-card market-warning"><strong>À lire avant de négocier</strong>${escapeHtml(context.warning || '')}</div>`;
}

function renderSupplierDirectory(rows) {
  $('#supplier-directory').innerHTML = rows.length ? rows.map(row => {
    const links = [
      row.website_url ? `<a class="text-btn" href="${escapeHtml(row.website_url)}" target="_blank" rel="noopener noreferrer">Site officiel ↗</a>` : '',
      row.contact_url && row.contact_url !== row.website_url ? `<a class="text-btn" href="${escapeHtml(row.contact_url)}" target="_blank" rel="noopener noreferrer">Contact / commande ↗</a>` : ''
    ].filter(Boolean).join('');
    return `<article class="supplier-card"><div class="supplier-card-head"><h4>${escapeHtml(row.name)}</h4><span class="supplier-region">${escapeHtml(REGION_LABELS[row.region] || row.region)}</span></div><p>${escapeHtml(row.coverage)}</p><dl><dt>Accès</dt><dd>${escapeHtml(row.channel)}</dd><dt>Livraison</dt><dd>${escapeHtml(row.typical_terms)}</dd><dt>Spécification</dt><dd>${escapeHtml(row.specifications)}</dd></dl><small class="field-help">${escapeHtml(row.buyer_note)}</small><div class="supplier-links">${links}</div></article>`;
  }).join('') : '<div class="empty">Aucun canal fournisseur enregistré pour ces critères.</div>';
}

function renderProcurementGuide(data) {
  renderMarketContext(data);
  renderSupplierDirectory(data.suppliers || []);
  $('#buy-steps').innerHTML = (data.steps || []).map(step => `<li><span class="step-number">${step.number}</span><b>${escapeHtml(step.title)}</b><span>${escapeHtml(step.detail)}</span></li>`).join('');
  $('#buy-specification').innerHTML = `<strong>Spécification :</strong> ${escapeHtml(data.specification)}`;
  $('#buy-logistics').innerHTML = `<strong>Logistique :</strong> ${escapeHtml(data.logistics)}`;
  $('#rfq-checklist').innerHTML = (data.rfq_fields || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
  $('#landed-cost-formula').innerHTML = `<strong>Formule de comparaison :</strong> ${escapeHtml(data.landed_cost_formula)}`;
  $('#buy-disclaimer').textContent = data.disclaimer || '';
}

async function loadProcurementGuide() {
  const product = $('#buy-product').value;
  const region = $('#buy-region').value;
  try {
    const response = await fetch(`/api/procurement-guide?product=${encodeURIComponent(product)}&region=${encodeURIComponent(region)}`);
    if (!response.ok) { const body = await response.json(); throw new Error(body.error || 'Guide d’achat indisponible.'); }
    renderProcurementGuide(await response.json());
  } catch (error) {
    $('#market-context').innerHTML = `<div class="empty error-note">${escapeHtml(error.message)}</div>`;
    $('#supplier-directory').innerHTML = '<div class="empty error-note">Canaux fournisseurs indisponibles.</div>';
  }
}

function updateProcurementUnits() {
  const product = $('#procurement-product').value;
  const currency = $('#procurement-currency').value;
  const meta = PRODUCT_META[product];
  $('#quantity-unit').textContent = meta.purchaseUnit;
  $('#unit-price-unit').textContent = `${currency}/${meta.purchaseUnit}`;
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
  $('#bitumen-history-count').textContent = `${data.total || 0} mois publiés`;
  $('#bitumen-history-page-label').textContent = bitumenHistoryPages ? `Page ${bitumenHistoryPage} / ${bitumenHistoryPages}` : 'Aucune page';
  $('#bitumen-history-prev').disabled = bitumenHistoryPage <= 1;
  $('#bitumen-history-next').disabled = !bitumenHistoryPages || bitumenHistoryPage >= bitumenHistoryPages;
  $('#bitumen-history-table').innerHTML = rows.length ? rows.map(row => {
    const month = new Date(`${row.source_date.slice(0, 10)}T00:00:00Z`).toLocaleDateString('fr-FR', { month: 'long', year: 'numeric', timeZone: 'UTC' });
    const pct = row.variation_pct;
    const cls = pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
    return `<tr><td>${month}</td><td class="price-cell">${formatNumber(row.price, 3)}</td><td><span class="trend ${cls}">${formatPct(pct)}</span></td></tr>`;
  }).join('') : '<tr><td colspan="3" class="empty">Aucun indice mensuel du bitume enregistré.</td></tr>';
}

async function loadBitumenHistory(page = 1) {
  const request = ++bitumenHistoryRequest;
  const params = new URLSearchParams({ product: 'bitume', data_kind: 'index', page, page_size: '12' });
  try {
    const response = await protectedFetch(`/api/history?${params}`);
    if (!response.ok) throw new Error('Historique du bitume indisponible.');
    const data = await response.json();
    if (request === bitumenHistoryRequest) renderBitumenHistory(data);
  } catch (error) {
    if (request !== bitumenHistoryRequest) return;
    $('#bitumen-history-table').innerHTML = '<tr><td colspan="3" class="empty error-note">Historique du bitume momentanément indisponible. Utilisez Actualiser pour réessayer.</td></tr>';
    $('#bitumen-history-count').textContent = 'Indisponible';
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
  $('#history-count').textContent = `${data.total || 0} relevé(s)`;
  historyPage = data.page || 1;
  historyPages = data.pages || 0;
  $('#history-page-label').textContent = historyPages ? `Page ${historyPage} / ${historyPages}` : 'Aucune page';
  $('#history-prev').disabled = historyPage <= 1;
  $('#history-next').disabled = !historyPages || historyPage >= historyPages;
  $('#history-table').innerHTML = rows.length ? rows.map(row => {
    const meta = PRODUCT_META[row.product] || { label: row.product, color: '#a7bac2' };
    const pct = row.variation_pct; const cls = pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
    return `<tr><td>${formatDate(row.source_date)}</td><td><div class="product-cell"><i style="background:${meta.color}"></i><strong>${meta.label}${row.data_kind === 'index' ? ' · indice BLS' : row.product === 'bitume' ? ' · devis' : ''}</strong></div></td><td class="price-cell">${formatPrice(row.price, row.unit)}</td><td>${escapeHtml(row.supplier || '—')}</td><td class="source-cell">${escapeHtml(row.source)}</td><td>${formatDate(row.collected_at, true)}</td><td><span class="trend ${cls}">${formatPct(pct)}</span></td></tr>`;
  }).join('') : '<tr><td colspan="7" class="empty">Aucun relevé pour ces critères.</td></tr>';
  updateHistoryExportLinks();
}

async function loadHistory(page = 1) {
  const request = ++historyRequest;
  const params = historyFilterParams();
  params.set('page', page);
  params.set('page_size', '50');
  try {
    const response = await protectedFetch(`/api/history?${params.toString()}`);
    if (!response.ok) throw new Error('Impossible de charger l’historique.');
    const data = await response.json();
    if (request === historyRequest) renderHistory(data);
  } catch (error) {
    if (request !== historyRequest) return;
    $('#history-table').innerHTML = '<tr><td colspan="7" class="empty error-note">Historique momentanément indisponible.</td></tr>';
    $('#history-count').textContent = 'Indisponible';
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
    return `<tr><td><div class="product-cell"><i style="background:${meta.color}"></i><strong>${escapeHtml(row.label || meta.label)}</strong></div></td><td>${formatPrice(row.period_a.average, row.unit)}</td><td>${formatPrice(row.period_b.average, row.unit)}</td><td class="${changeClass}">${row.average_change == null ? '—' : `${formatNumber(row.average_change)} ${escapeHtml(row.unit || '')} · ${formatPct(row.average_change_pct)}`}</td><td>${row.period_a.count} / ${row.period_b.count}</td></tr>`;
  }).join('') || '<tr><td colspan="5" class="empty">Aucune donnée à comparer.</td></tr>';
}

async function compareHistory() {
  const values = Object.fromEntries(new FormData($('#compare-form')));
  const params = new URLSearchParams(values);
  historyFilterParams(false).forEach((value, key) => params.set(key, value));
  try {
    const response = await protectedFetch(`/api/history/compare?${params.toString()}`);
    if (!response.ok) { const result = await response.json(); throw new Error(result.error || 'Impossible de comparer les périodes.'); }
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
    summary.textContent = 'Comparaison indisponible';
    summary.className = 'comparison-badge';
    prices.innerHTML = '<tr><td colspan="6" class="empty">Impossible de vérifier les données des API.</td></tr>';
    comparisons.innerHTML = '<tr><td colspan="5" class="empty">Comparaison non vérifiée.</td></tr>';
    $('#comparison-note').textContent = '';
    return;
  }
  const availability = {
    available: 'Donnée reçue', no_data: 'Aucune donnée collectée',
    not_configured: 'API non configurée / désactivée', unavailable: 'Collecte en échec · prix conservé'
  };
  const verdicts = {
    match: ['Cohérent', 'match'], mismatch: ['À vérifier', 'mismatch'],
    not_comparable: ['Non comparable', ''], unverified: ['Non vérifié', '']
  };
  const byCode = Object.fromEntries(data.sources.map(row => [row.code, row]));
  summary.textContent = data.mismatch_count
    ? `${data.mismatch_count} écart(s) à vérifier`
    : data.comparable_pair_count ? `${data.comparable_pair_count} paire(s) équivalente(s) cohérente(s)` : 'Aucune paire équivalente vérifiée';
  summary.className = `comparison-badge ${data.mismatch_count ? 'mismatch' : data.comparable_pair_count ? 'match' : ''}`;
  prices.innerHTML = data.sources.map(row => {
    const product = PRODUCT_META[row.product]?.short || row.product;
    const value = row.price == null ? '—' : `${formatNumber(row.price, 4)} <small>${escapeHtml(row.unit)}</small>`;
    return `<tr><td><strong>${escapeHtml(product)}</strong></td><td><strong>${escapeHtml(row.provider)}</strong><small class="comparison-detail">${escapeHtml(row.instrument)}</small></td><td class="price-cell">${value}</td><td>${formatDate(row.source_date)}</td><td>${formatDate(row.last_success_at, true)}</td><td>${escapeHtml(availability[row.availability] || 'Non vérifié')}</td></tr>`;
  }).join('');
  comparisons.innerHTML = data.pairs.map(pair => {
    const [label, kind] = verdicts[pair.status] || verdicts.unverified;
    const product = PRODUCT_META[pair.product]?.short || pair.product;
    const providers = `${byCode[pair.left]?.provider || pair.left} / ${byCode[pair.right]?.provider || pair.right}`;
    const difference = pair.difference == null ? '—' : `${formatNumber(pair.difference, 6)} <small>${escapeHtml(pair.unit)}</small>`;
    const percent = pair.difference_pct == null ? '—' : `${formatNumber(pair.difference_pct, 3)} %`;
    return `<tr><td>${escapeHtml(product)}</td><td>${escapeHtml(providers)}</td><td class="price-cell">${difference}</td><td>${percent}</td><td><span class="comparison-badge ${kind}">${label}</span><small class="comparison-detail">${escapeHtml(pair.reason)}</small></td></tr>`;
  }).join('') || '<tr><td colspan="5" class="empty">Aucune paire de prix disponible.</td></tr>';
  $('#comparison-note').textContent = data.note;
}

async function loadDashboard() {
  const request = ++dashboardRequest;
  const days = $('#period-select').value; $('#period-label').textContent = days == 365 ? '12 derniers mois' : days == 1825 ? '5 dernières années' : `${days} derniers jours`;
  try {
    const params = new URLSearchParams({ days });
    ['brent', 'gasoil'].forEach(product => { const value = $(`#market-${product}-instrument`)?.value; if (value) params.set(`${product}_instrument`, value); });
    const response = await fetch(`/api/dashboard?${params}`, { cache: 'no-store' }); if (!response.ok) throw new Error('Impossible de charger le tableau de bord.');
    const data = await response.json();
    if (request !== dashboardRequest) return;
    renderMetrics(data.metrics); renderChart(data.series); renderLatest(data.latest);
    renderSourceComparison(data.source_comparison);
    $('#quality-score').textContent = data.quality?.score ?? '—';
    $('#mode-label').textContent = data.demo_mode ? 'Démonstration active' : 'Production / API';
    $('#last-update').textContent = data.latest.length ? formatDate(data.latest.reduce((a, b) => new Date(a.collected_at) > new Date(b.collected_at) ? a : b).collected_at, true) : '—';
    $('#source-count').textContent = `${data.quality?.source_count ?? data.latest.length} sources`;
    $('#observation-count').textContent = `${data.quality?.observation_count ?? 0}`;
    connectionMonitor.render();
  } catch (error) {
    if (request !== dashboardRequest) return;
    renderSourceComparison(null);
    renderChart({});
    $('#chart-empty').innerHTML = '<strong>Service de données indisponible</strong><span>Redémarrez l’application, puis actualisez cette page.</span>';
    connectionMonitor.check();
  }
}

async function loadLogs() {
  try {
    const response = await fetch('/api/logs'); if (!response.ok) throw new Error('Impossible de charger le journal.');
    const data = await response.json(); const logs = data.logs || []; $('#activity-count').textContent = `${logs.length} événements`;
    $('#activity-list').innerHTML = logs.length ? logs.slice(0, 5).map(log => `<div class="activity-item"><span class="activity-dot ${log.status === 'error' ? 'error' : ''}"></span><div class="activity-text"><strong>${log.status === 'success' ? 'Collecte terminée' : 'Erreur de collecte'}</strong> · ${log.rows_collected} relevé(s)<small>${formatDate(log.finished_at, true)} · ${escapeHtml(log.message)}</small></div></div>`).join('') : '<div class="empty">Aucune collecte exécutée depuis le démarrage.</div>';
  } catch (error) { $('#activity-count').textContent = 'Indisponible'; $('#activity-list').innerHTML = '<div class="empty error-note">Journal momentanément indisponible.</div>'; }
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
  marches: ['Marchés', 'L’essentiel des prix, en un regard.'],
  tendances: ['Tendances', 'Explorez les projections à 7, 30 et 90 jours.'],
  veille: ['Veille', 'Gardez un œil sur les seuils qui comptent.'],
  archives: ['Archives', 'Retrouvez vos relevés et comparez les périodes.'],
  'nouveau-prix': ['Nouveau prix', 'Ajoutez un devis ou un prix de bitume validé.'],
  fournisseurs: ['Fournisseurs', 'Trouvez vos contacts et préparez vos demandes de prix.'],
  budget: ['Budget', 'Estimez le coût de vos achats et leur impact.'],
  activite: ['Activité', 'Consultez la qualité des données et les dernières collectes.']
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
  navigationToggle.querySelector('.sr-only').textContent = open ? 'Fermer la navigation' : 'Ouvrir la navigation';
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
$('#refresh-btn').addEventListener('click', () => { loadDashboard(); loadForecast(); loadAlerts(); loadLogs(); loadHistory(historyPage); loadBitumenHistory(bitumenHistoryPage); loadProcurements(); loadProcurementGuide(); showToast('Tableau de bord actualisé.'); });
$('#collect-btn').addEventListener('click', async () => {
  const button = $('#collect-btn');
  button.disabled = true;
  button.innerHTML = 'Collecte en cours…';
  try {
    const response = await protectedFetch('/api/collect', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
    const result = await response.json();
    if (!response.ok || result.status === 'error') throw new Error(result.message || result.error || 'La collecte a échoué.');
    renderSourceComparison(result.source_comparison);
    const mismatch = result.source_comparison?.mismatch_count > 0;
    showToast(mismatch ? result.source_comparison.message : `${result.rows} relevé(s) enregistré(s).`, mismatch);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.innerHTML = '<span>↻</span> Lancer une collecte';
    loadDashboard();
    loadLogs();
  }
});
$('#bitumen-form').addEventListener('submit', async (event) => { event.preventDefault(); const data = Object.fromEntries(new FormData(event.target)); data.product = 'bitume'; data.unit = 'USD/tonne'; try { const response = await protectedFetch('/api/observations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }); const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Échec de l’enregistrement.'); showToast('Relevé bitume enregistré avec succès.'); event.target.reset(); event.target.source_date.value = new Date().toISOString().slice(0, 10); loadDashboard(); loadHistory(historyPage); } catch (error) { showToast(error.message, true); } });
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
    const response = await protectedFetch('/api/procurements', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Impossible d’évaluer cet achat.');
    renderProcurementSummary(result.purchase);
    showToast('Impact d’achat calculé et enregistré.');
    event.target.reset();
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
      showToast(rule.muted ? 'Alerte réactivée.' : 'Alerte mise en pause.');
    }
    if (button.dataset.alertAction === 'delete') {
      if (!window.confirm(`Supprimer la règle ${rule.product} ${rule.direction} ${rule.threshold} ?`)) return;
      const response = await protectedFetch(`/api/alerts/${id}`, { method: 'DELETE' });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Impossible de supprimer cette alerte.');
      if (editingAlertId === id) resetAlertForm();
      await loadAlerts();
      showToast('Alerte supprimée.');
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
    if (!response.ok) throw new Error(result.error || 'Impossible d’enregistrer cette alerte.');
    resetAlertForm();
    await loadAlerts();
    showToast(wasEditing ? 'Alerte modifiée.' : 'Alerte créée.');
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
// The external chart library must not delay connection notifications offline.
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
