/* Same-origin account access, with CSRF headers kept out of external requests. */
(() => {
  const roles = { viewer: 1, editor: 2, admin: 3 };
  function requestHeaders(url, options, state, origin, key = '') {
    const headers = new Headers(options.headers || {});
    const target = new URL(url, origin);
    if (target.origin === origin && (target.pathname.startsWith('/api/') || target.pathname.startsWith('/export.') || target.pathname === '/metrics')) {
      if (!state.team_mode && !state.user && key) headers.set('X-API-Key', key);
      if (state.team_mode) headers.delete('X-API-Key');
      if (state.csrf_token && !['GET', 'HEAD'].includes((options.method || 'GET').toUpperCase())) headers.set('X-CSRF-Token', state.csrf_token);
    }
    return headers;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { requestHeaders, roles };
  if (typeof window === 'undefined' || typeof document === 'undefined') return;
  const nativeFetch = window.fetch.bind(window);
  const box = document.querySelector('#sign-in');
  const shell = document.querySelector('.app-shell');
  const form = document.querySelector('#sign-in-form');
  const errorBox = document.querySelector('#sign-in-error');
  let state = { team_mode: true, user: null, role: null, csrf_token: null };
  const storedKey = () => { try { return sessionStorage.getItem('priceMonitorApiKey') || ''; } catch { return ''; } };
  function showLogin() {
    shell.hidden = true;
    box.hidden = false;
    const keyMode = state.api_key_mode;
    document.querySelector('#sign-in-key-fields').hidden = !keyMode;
    document.querySelector('#sign-in-account-fields').hidden = Boolean(keyMode);
    form.querySelector('[name=username]').required = !keyMode;
    form.querySelector('[name=password]').required = !keyMode;
    form.querySelector('[name=api_key]').required = Boolean(keyMode);
    document.querySelector('#sign-in-cancel').hidden = state.team_mode || keyMode;
  }
  function applyPermissions() {
    const rank = roles[state.role] || 0;
    document.querySelectorAll('[data-permission]').forEach(element => {
      const allowed = rank >= roles[element.dataset.permission];
      if (!allowed) { element.hidden = true; element.dataset.permissionHidden = 'true'; }
      else if (element.dataset.permission === 'admin' || element.dataset.permissionHidden) { element.hidden = false; delete element.dataset.permissionHidden; }
      element.querySelectorAll('input,button,select,textarea').forEach(control => {
        if (!allowed && !control.disabled) { control.disabled = true; control.dataset.permissionDisabled = 'true'; }
        else if (allowed && control.dataset.permissionDisabled) { control.disabled = false; delete control.dataset.permissionDisabled; }
      });
    });
    document.querySelector('#collect-btn').disabled = rank < roles.editor;
    document.querySelector('#profile-button').textContent = state.user ? state.user.full_name.slice(0, 2).toUpperCase() : 'PM';
    document.querySelector('#account-name').textContent = state.user ? `${state.user.full_name} · ${state.user.username}` : 'Accès local personnel';
    document.querySelector('#account-role').textContent = ({ viewer: 'Lecteur — consultation et exports', editor: 'Éditeur — consultation et modifications', admin: 'Administrateur — accès et équipe' }[state.role] || 'Connexion requise');
    document.querySelector('#logout-button').hidden = !state.user && !state.api_key_mode;
    document.querySelector('#account-login-button').hidden = Boolean(state.user);
    document.querySelector('#personal-account-controls').hidden = !state.user;
    if (state.user) document.querySelectorAll('[name=actor]').forEach(input => { input.value = state.user.username; input.readOnly = true; });
  }
  async function refreshSession() {
    const headers = new Headers();
    if (!state.team_mode && !state.user && storedKey()) headers.set('X-API-Key', storedKey());
    const response = await nativeFetch('/api/auth/session', { headers, cache: 'no-store' });
    if (!response.ok) throw new Error('Connexion indisponible. Réessayez.');
    state = await response.json();
    if (!state.team_mode && state.api_key_mode && !state.role && storedKey()) {
      const retry = await nativeFetch('/api/auth/session', { headers: { 'X-API-Key': storedKey() }, cache: 'no-store' });
      if (retry.ok) state = await retry.json();
    }
    if (!state.role) showLogin();
    else { box.hidden = true; shell.hidden = false; applyPermissions(); }
    return state;
  }
  const ready = refreshSession().catch(error => { errorBox.textContent = error.message; showLogin(); return state; });
  async function authenticatedFetch(url, options = {}) {
    await ready;
    const target = new URL(typeof url === 'string' ? url : url.url, window.location.origin);
    const internal = target.origin === window.location.origin && (target.pathname.startsWith('/api/') || target.pathname.startsWith('/export.') || target.pathname === '/metrics');
    if (!internal) return nativeFetch(url, options);
    const headers = requestHeaders(target.href, options, state, window.location.origin, storedKey());
    const response = await nativeFetch(url, { ...options, headers, credentials: 'same-origin' });
    if (response.status === 401) { state.user = null; state.role = null; state.csrf_token = null; showLogin(); }
    return response;
  }
  window.PriceMonitorAuth = { fetch: authenticatedFetch, ready, get state() { return state; }, refresh: refreshSession };
  window.fetch = authenticatedFetch;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    errorBox.textContent = '';
    const button = form.querySelector('button[type=submit]');
    button.disabled = true;
    try {
      if (state.api_key_mode) {
        sessionStorage.setItem('priceMonitorApiKey', form.elements.api_key.value.trim());
        await refreshSession();
        if (!state.role) throw new Error('Clé incorrecte.');
      } else {
        const response = await nativeFetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: form.elements.username.value, password: form.elements.password.value }) });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Connexion refusée.');
        try { sessionStorage.removeItem('priceMonitorApiKey'); } catch {}
      }
      form.reset();
      window.location.reload();
    } catch (error) { errorBox.textContent = error.message; }
    finally { button.disabled = false; }
  });
  document.querySelector('#sign-in-cancel').addEventListener('click', () => { box.hidden = true; shell.hidden = false; });
  document.querySelector('#account-login-button').addEventListener('click', showLogin);
  document.querySelector('#profile-button').addEventListener('click', () => { window.location.hash = '#equipe'; });
  document.querySelector('#logout-button').addEventListener('click', async () => {
    const response = await authenticatedFetch('/api/auth/logout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    if (!response.ok) return;
    try { sessionStorage.removeItem('priceMonitorApiKey'); } catch {}
    state = { ...state, user: null, role: null, csrf_token: null };
    shell.hidden = true;
    window.location.reload();
  });
  window.addEventListener('pageshow', event => { if (event.persisted) { shell.hidden = true; refreshSession().catch(showLogin); } });
  window.addEventListener('focus', () => { refreshSession().catch(showLogin); });
})();
