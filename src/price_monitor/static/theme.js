(() => {
  const storageKey = 'priceMonitorTheme';
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  const storedTheme = () => {
    try {
      const value = localStorage.getItem(storageKey);
      return value === 'dark' || value === 'light' ? value : null;
    } catch { return null; }
  };
  const preferredTheme = () => storedTheme() || (systemTheme.matches ? 'dark' : 'light');

  function applyTheme(theme, persist = false) {
    if (theme !== 'dark' && theme !== 'light') return;
    if (persist) {
      try { localStorage.setItem(storageKey, theme); } catch { /* Storage can be disabled. */ }
    }
    const previous = document.documentElement.dataset.theme;
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#0b151d' : '#f3f7f8');
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.setAttribute('aria-label', `Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`);
      button.setAttribute('aria-pressed', String(theme === 'dark'));
      button.querySelector('[data-theme-icon]').textContent = theme === 'dark' ? '☾' : '☀';
      button.querySelector('[data-theme-label]').textContent = theme === 'dark' ? 'Dark' : 'Light';
    });
    if (previous && previous !== theme) window.dispatchEvent(new CustomEvent('price-monitor-theme-change', { detail: { theme } }));
  }

  window.PriceMonitorTheme = {
    current: () => document.documentElement.dataset.theme,
    set: theme => applyTheme(theme, true),
    palette: () => document.documentElement.dataset.theme === 'light'
      ? { tick: '#637782', grid: 'rgba(25, 60, 72, .12)', forecast: '#0d6c79', band: 'rgba(19, 150, 138, .15)', tooltip: '#18313d', tooltipText: '#f7fbfc', series: { gasoil: '#008d7f', brent: '#bf6c2b', bitume: '#7654b6' } }
      : { tick: '#9fb2bc', grid: 'rgba(201, 230, 232, .11)', forecast: '#f0cd83', band: 'rgba(55, 207, 180, .14)', tooltip: '#20323d', tooltipText: '#f7fbfc', series: { gasoil: '#39cfb5', brent: '#efae6d', bitume: '#b499eb' } }
  };

  applyTheme(preferredTheme());
  document.addEventListener('DOMContentLoaded', () => {
    applyTheme(preferredTheme());
    document.querySelectorAll('[data-theme-toggle]').forEach(button => button.addEventListener('click', () => {
      applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark', true);
    }));
  });
  const followSystem = () => { if (!storedTheme()) applyTheme(preferredTheme()); };
  if (systemTheme.addEventListener) systemTheme.addEventListener('change', followSystem);
  else systemTheme.addListener?.(followSystem);
  window.addEventListener('storage', event => { if (event.key === storageKey) applyTheme(preferredTheme()); });
})();
