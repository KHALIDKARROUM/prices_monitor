/* Account administration, personal tokens and the administrator action journal. */
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const labels = { viewer: 'Viewer', editor: 'Editor', admin: 'Administrator' };
  const actions = { 'login.failed': 'Sign-in denied', 'login.succeeded': 'Signed in', logout: 'Signed out',
    'quote.created': 'Quote created', 'quote.corrected': 'Quote corrected', 'document.attached': 'Document attached',
    'document.retired': 'Document withdrawn', 'document.prepared': 'Document prepared', 'export.prepared': 'Export prepared',
    'observation.created': 'Price entered', 'budget.created': 'Budget saved', 'alert.created': 'Alert created',
    'alert.updated': 'Alert updated', 'alert.deleted': 'Alert deleted', 'collection.requested': 'Collection requested',
    'user.created': 'Account added', 'user.updated': 'Account updated', 'user.bootstrapped': 'First administrator created',
    'password.changed': 'Password changed', 'password.reset_by_operator': 'Password reset',
    'token.created': 'Token created', 'token.revoked': 'Token revoked', 'access.denied': 'Access denied' };
  function auditRows(events) {
    return events.map(row => `<tr><td>${escape(new Date(row.occurred_at).toLocaleString('en-US'))}</td><td>${escape(row.actor)}</td><td>${escape(actions[row.action] || row.action)}</td><td>${escape(row.resource)} ${escape(row.resource_id)}</td><td>${row.outcome === 'success' ? 'Recorded' : 'Denied'}</td></tr>`).join('');
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { auditRows };
  if (typeof document === 'undefined') return;
  let users = [];
  let auditPage = 1;
  const $team = selector => document.querySelector(selector);
  const jsonOptions = (method, payload) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  async function request(url, options = {}) {
    const response = await window.PriceMonitorAuth.fetch(url, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Action unavailable.');
    return result;
  }
  async function loadAudit() {
    const data = await request(`/api/audit?page=${auditPage}&page_size=25`);
    $team('#audit-body').innerHTML = auditRows(data.events) || '<tr><td colspan="5">No recorded actions.</td></tr>';
    $team('#audit-page').textContent = `Page ${data.page} of ${data.pages} · ${data.total} actions`;
    $team('#audit-prev').disabled = auditPage <= 1;
    $team('#audit-next').disabled = auditPage >= data.pages;
  }
  async function loadUsers() {
    const data = await request('/api/users');
    users = data.users;
    $team('#team-users').innerHTML = users.map(user => `<form class="team-user" data-user-id="${user.id}">
      <strong>${escape(user.username)}</strong><label>Name<input name="full_name" value="${escape(user.full_name)}" maxlength="100" required></label>
      <label>Role<select name="role">${Object.entries(labels).map(([key, label]) => `<option value="${key}"${user.role === key ? ' selected' : ''}>${label}</option>`).join('')}</select></label>
      <label>Access<select name="active"><option value="true"${user.active ? ' selected' : ''}>Active</option><option value="false"${!user.active ? ' selected' : ''}>Disabled</option></select></label>
      <label>New password (optional)<input name="password" type="password" minlength="12" maxlength="128" autocomplete="new-password"></label>
      <button type="submit" class="text-btn">Save</button></form>`).join('');
  }
  async function loadTokens() {
    const data = await request('/api/auth/tokens');
    $team('#token-list').innerHTML = data.tokens.map(row => `<div class="team-user"><strong>${escape(row.label)}</strong><span>Expires ${escape(new Date(row.expires_at * 1000).toLocaleDateString('en-US'))}</span>${row.revoked_at ? '<span>Revoked</span>' : `<button type="button" class="text-btn" data-revoke-token="${row.id}">Revoke</button>`}</div>`).join('');
  }
  async function refreshTeam() {
    try { await loadUsers(); await loadAudit(); } catch (error) { $team('#team-message').textContent = error.message; }
  }
  $team('#team-refresh').addEventListener('click', refreshTeam);
  $team('#user-form').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      await request('/api/users', jsonOptions('POST', Object.fromEntries(new FormData(event.target))));
      event.target.reset();
      $team('#team-message').textContent = 'Account added. Share the initial password privately.';
      await refreshTeam();
    } catch (error) { $team('#team-message').textContent = error.message; }
  });
  $team('#team-users').addEventListener('submit', async event => {
    event.preventDefault();
    const user = users.find(row => row.id === Number(event.target.dataset.userId));
    if (!user) return;
    const payload = Object.fromEntries(new FormData(event.target));
    payload.active = payload.active === 'true';
    payload.version = user.version;
    if (!payload.password) delete payload.password;
    try {
      await request(`/api/users/${user.id}`, jsonOptions('PUT', payload));
      $team('#team-message').textContent = 'Account updated.';
      await window.PriceMonitorAuth.refresh();
      await refreshTeam();
    } catch (error) { $team('#team-message').textContent = error.message; }
  });
  $team('#password-form').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      await request('/api/auth/password', jsonOptions('POST', Object.fromEntries(new FormData(event.target))));
      event.target.reset();
      window.location.reload();
    } catch (error) { $team('#account-message').textContent = error.message; }
  });
  $team('#token-form').addEventListener('submit', async event => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(event.target));
    payload.days = Number(payload.days);
    try {
      const result = await request('/api/auth/tokens', jsonOptions('POST', payload));
      $team('#new-token').value = result.token;
      $team('#new-token').hidden = $team('#new-token-message').hidden = false;
      event.target.reset();
      await loadTokens();
    } catch (error) { $team('#account-message').textContent = error.message; }
  });
  $team('#token-list').addEventListener('click', async event => {
    const button = event.target.closest('[data-revoke-token]');
    if (!button) return;
    try {
      await request(`/api/auth/tokens/${button.dataset.revokeToken}`, { method: 'DELETE' });
      $team('#new-token').value = '';
      $team('#new-token').hidden = $team('#new-token-message').hidden = true;
      await loadTokens();
    } catch (error) { $team('#account-message').textContent = error.message; }
  });
  $team('#audit-prev').addEventListener('click', () => { auditPage--; loadAudit().catch(error => { $team('#team-message').textContent = error.message; }); });
  $team('#audit-next').addEventListener('click', () => { auditPage++; loadAudit().catch(error => { $team('#team-message').textContent = error.message; }); });
  window.PriceMonitorAuth.ready.then(state => {
    if (state.role === 'admin') refreshTeam();
    if (state.user) loadTokens().catch(error => { $team('#account-message').textContent = error.message; });
  });
})();
