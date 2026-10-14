/* A compact, actionable summary built from the same data as the dashboard. */
(() => {
  const names = { gasoil: 'Diesel', brent: 'Brent', bitume: 'Bitumen' };
  const providers = { fred: 'FRED', eia: 'EIA', alpha_vantage: 'Alpha Vantage', bls: 'BLS', yahoo: 'Yahoo' };
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  function attentionItems(state) {
    const items = [];
    if (state.connection?.kind === 'error') items.push({ id: 'connection', text: state.connection.message, href: '#activite' });
    if (state.dashboard === null) items.push({ id: 'dashboard', text: 'Prices temporarily unavailable', href: '#marches' });
    for (const [key, metric] of Object.entries(state.dashboard?.metrics || {})) {
      if (metric.current == null) items.push({ id: `data-${key}`, text: `${names[key] || key}: data missing`, href: '#marches' });
      else if (metric.is_stale && !metric.market_closed) items.push({ id: `data-${key}`, text: `${names[key] || key}: latest data is stale`, href: '#marches' });
    }
    const mismatch = state.dashboard?.source_comparison?.mismatch_count || 0;
    if (mismatch) items.push({ id: 'comparison', text: `${mismatch} mismatch(es) between equivalent feeds`, href: '#source-comparison' });
    const active = (state.alerts || []).filter(rule => rule.active && rule.enabled !== false && !rule.muted);
    if (active.length) items.push({ id: 'alerts', text: `${active.length} active alert(s)`, href: '#veille' });
    for (const [key, data] of Object.entries(state)) {
      if (!key.startsWith('forecast-')) continue;
      const name = names[key.slice(9)] || key.slice(9);
      if (!data.product || data.product.status !== 'ready') items.push({ id: key, text: `${name}: forecast unavailable`, href: '#tendances' });
      else if (data.warnings?.length) items.push({ id: key, text: `${name}: use forecast with caution`, href: '#tendances' });
    }
    for (const row of state.schedules || []) {
      if (row.status === 'running' || /^(Marché fermé|Market closed)/.test(row.reason || '')) continue;
      const quota = row.request_limit_24h && row.requests_last_24h >= row.request_limit_24h;
      if (quota || row.failures > 0 || row.status === 'error') items.push({ id: `provider-${row.provider}`, text: `${providers[row.provider] || row.provider}: ${quota ? 'quota reached' : 'collection retrying'}`, href: '#activite' });
    }
    return items;
  }
  function attentionHtml(items) {
    return items.map(item => `<li><a href="${escape(item.href)}">${escape(item.text)} <span aria-hidden="true">→</span></a></li>`).join('');
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { attentionItems, attentionHtml };
  if (typeof document === 'undefined') return;
  const state = {};
  const panel = document.querySelector('#needs-attention');
  globalThis.PriceMonitorAttention = { update(key, value) {
    state[key] = value;
    const items = attentionItems(state);
    panel.classList.toggle('has-attention', items.length > 0);
    document.querySelector('#attention-count').textContent = items.length ? `${items.length} item(s) to review` : 'Nothing needs attention';
    document.querySelector('#attention-items').innerHTML = attentionHtml(items);
    document.querySelector('#attention-note').textContent = items.length ? 'Open an item to see details.' : 'Available data summarized; checks continue in the background.';
  } };
})();
