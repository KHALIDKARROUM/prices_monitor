/* Provider schedule status on the Activity page. */
(() => {
  const PROVIDERS = { fred: 'FRED · daily prices', eia: 'EIA · daily prices', alpha_vantage: 'Alpha Vantage · daily Brent', bls: 'BLS · monthly index', yahoo: 'Yahoo Finance · live quotes', demo: 'Demo' };
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const cadence = minutes => minutes >= 1440 ? `${minutes / 1440} day(s)` : minutes >= 60 ? `${minutes / 60} h` : `${minutes} min`;
  function scheduleRows(providers) {
    return providers.map(row => {
      const state = row.reason || ({ pending: 'First collection pending', success: 'Up to date', partial: 'Partial result', error: 'Collection failed', deferred: 'Collection deferred', running: 'Collection running' }[row.status] || row.status);
      const quota = row.request_limit_24h ? `${row.requests_last_24h} / ${row.request_limit_24h}` : `${row.requests_last_24h}`;
      const next = new Date(row.next_collection_at).toLocaleString('en-US');
      return `<tr><td>${escape(PROVIDERS[row.provider] || row.provider)}</td><td>${escape(cadence(row.interval_minutes))}</td><td>${escape(state)}</td><td>${escape(next)}</td><td>${escape(quota)}</td></tr>`;
    }).join('');
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { scheduleRows, cadence };
  if (typeof document === 'undefined') return;
  const body = document.querySelector('#schedule-body');
  if (!body) return;
  async function refreshSchedules() {
    try {
      const response = await fetch('/api/collection-schedules');
      if (!response.ok) throw new Error('Schedules unavailable');
      const data = await response.json();
      globalThis.PriceMonitorAttention?.update('schedules', data.providers || []);
      body.innerHTML = scheduleRows(data.providers || []) || '<tr><td colspan="5">No providers configured.</td></tr>';
    } catch {
      body.innerHTML = '<tr><td colspan="5">Schedule temporarily unavailable.</td></tr>';
    }
  }
  refreshSchedules();
  setInterval(refreshSchedules, 60000);
  document.addEventListener('collection-finished', refreshSchedules);
})();
