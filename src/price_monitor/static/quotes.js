/* Supplier quote dossiers and their private supporting evidence. */
let quoteOptions = null;
let quoteCurrent = null;
let quoteEditingId = null;
let quotePage = 1;
let quotePages = 0;
let quoteTotal = 0;
let quoteListRequest = 0;
let quoteDetailRequest = 0;
const selectedQuoteIds = new Set();
const QUOTE_FIELDS = {
  product: 'Product', supplier: 'Supplier', reference: 'Reference', grade: 'Grade', specification: 'Standard / specification',
  origin: 'Origin', incoterm: 'Incoterm', incoterm_place: 'Incoterm named place', destination: 'Destination',
  quantity: 'Quantity', unit: 'Unit', packaging: 'Packaging', packaging_detail: 'Packaging details',
  payment_terms: 'Payment terms', document_type: 'Commercial document type', quote_date: 'Quote date',
  valid_until: 'Valid until', delivery_date: 'Delivery date', currency: 'Currency', exchange_rate: 'USD per currency unit',
  unit_price: 'Unit price', total_cost_usd: 'Total landed cost · USD', landed_unit_cost_usd: 'Landed cost per unit · USD',
  status: 'Status', notes: 'Notes', documents: 'Supporting documents', other_costs: 'Other costs', other_costs_json: 'Other costs'
};

async function quoteRequest(url, options = {}) {
  const response = await protectedFetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Quote management unavailable.');
  return data;
}

function quoteJson(method, payload) {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) };
}

function addCostItem(container, item = {}) {
  if (container.querySelectorAll('.other-cost-row').length >= 20) {
    showToast('You can add at most 20 additional cost items.', true);
    return;
  }
  const row = document.createElement('div');
  row.className = 'other-cost-row';
  row.innerHTML = `<label>Cost description<input data-cost-label maxlength="100" required value="${escapeHtml(item.label || '')}"></label><label>Total amount<input data-cost-amount type="number" min="0" step="0.000001" required value="${escapeHtml(item.amount ?? 0)}"></label><button type="button" class="text-btn" data-remove-cost>Remove cost</button>`;
  row.querySelector('[data-remove-cost]').addEventListener('click', () => row.remove());
  container.append(row);
}

function readCostItems(container) {
  return [...container.querySelectorAll('.other-cost-row')].map(row => ({
    label: row.querySelector('[data-cost-label]').value,
    amount: row.querySelector('[data-cost-amount]').value
  }));
}

function setCostItems(container, items = []) {
  container.innerHTML = '';
  items.forEach(item => addCostItem(container, item));
}

function quoteValue(quote, key) {
  const value = quote[key];
  if (value == null || value === '') return '—';
  if (key === 'product') return PRODUCT_META[value]?.label || value;
  if (key === 'unit') return value === 'baril' ? 'barrel' : value;
  if (key === 'packaging') return quoteOptions?.packaging?.[value] || value;
  if (key === 'document_type') return quoteOptions?.document_types?.[value] || value;
  if (key === 'status') return value === 'withdrawn' ? 'Withdrawn' : quote.expired ? 'Expired' : 'Active';
  if (key === 'documents') return value.map(doc => `${doc.filename}${doc.removed_at ? ' (withdrawn)' : ''}`).join(', ') || '—';
  if (key === 'other_costs') return value.map(item => `${item.label} : ${item.amount}`).join(', ') || '—';
  if (typeof value === 'number') return formatNumber(value, key.includes('total') || key.endsWith('_cost') ? 2 : 6);
  return String(value);
}

function renderQuoteList(data) {
  $('#quote-comparison').hidden = true;
  quotePage = data.page;
  quotePages = data.pages;
  quoteTotal = data.total;
  $('#quote-list-status').textContent = `${data.total} quote(s) · ${selectedQuoteIds.size} selected`;
  $('#quote-page').textContent = data.pages ? `Page ${data.page} of ${data.pages}` : 'No quotes';
  $('#quote-prev').disabled = data.page <= 1;
  $('#quote-next').disabled = data.page >= data.pages;
  $('#quote-list').innerHTML = data.items.length ? data.items.map(quote => `<tr>
    <td><input type="checkbox" data-quote-select="${quote.id}" aria-label="Compare quote ${quote.id}" ${selectedQuoteIds.has(quote.id) ? 'checked' : ''}></td>
    <td><strong>${escapeHtml(quote.reference || `Quote #${quote.id}`)}</strong><br>${escapeHtml(quote.supplier)}<small class="source-cell"> · version ${quote.version}</small></td>
    <td>${escapeHtml(quoteValue(quote, 'product'))}<br>${escapeHtml(quote.grade)}</td><td>${escapeHtml(quote.incoterm)} · ${escapeHtml(quote.destination)}<br>${formatDate(quote.delivery_date)}</td>
    <td>${quote.status === 'withdrawn' ? 'Withdrawn' : quote.expired ? '<span class="error-note">Expired</span>' : 'Active'}<br>${formatDate(quote.valid_until)}</td>
    <td>${formatMoney(quote.total_cost_usd, 'USD')}</td><td><button type="button" class="text-btn" data-open-quote="${quote.id}">Open</button></td></tr>`).join('') : '<tr><td colspan="7" class="empty">No quotes yet. Create your first supplier quote.</td></tr>';
  updateQuoteSelection();
}

async function loadQuotes(page = quotePage) {
  const request = ++quoteListRequest;
  const filters = Object.fromEntries(new FormData($('#quote-filters')));
  const params = new URLSearchParams({ ...filters, page, page_size: 20 });
  try {
    const data = await quoteRequest(`/api/quotes?${params}`);
    if (request !== quoteListRequest) return;
    renderQuoteList(data);
  } catch (error) {
    if (request === quoteListRequest) $('#quote-list-status').textContent = error.message;
  }
}

function updateQuoteSelection() {
  $('#quote-list-status').textContent = `${quoteTotal} quote(s) · ${selectedQuoteIds.size} selected`;
  $('#quote-compare').disabled = selectedQuoteIds.size < 2;
  $('#quote-compare').textContent = `Compare ${selectedQuoteIds.size} selected quote(s)`;
}

function updateQuoteUnits() {
  const form = $('#quote-editor');
  $('#quote-unit').textContent = PRODUCT_META[form.elements.namedItem('product').value]?.purchaseUnit || '';
  form.elements.namedItem('packaging_detail').required = form.elements.namedItem('packaging').value === 'other';
}

function editQuote(quote = null) {
  if (window.PriceMonitorAuth && !['editor', 'admin'].includes(window.PriceMonitorAuth.state.role)) {
    showToast('Your role allows you to view quotes only.', true);
    return;
  }
  const form = $('#quote-editor');
  quoteEditingId = quote?.id || null;
  form.reset();
  if (quote) {
    [...form.elements].forEach(field => {
      if (field.name && quote[field.name] != null) field.value = quote[field.name];
    });
    form.elements.namedItem('expected_version').value = quote.version;
  } else {
    form.elements.namedItem('quote_date').value = new Date().toISOString().slice(0, 10);
  }
  if (window.PriceMonitorAuth?.state.user) form.elements.namedItem('actor').value = window.PriceMonitorAuth.state.user.username;
  setCostItems($('#quote-other-costs'), quote?.other_costs || []);
  $('#quote-reason-field').hidden = !quote;
  form.elements.namedItem('change_reason').required = !!quote;
  $('#quote-editor-title').textContent = quote ? `Correct quote #${quote.id} · version ${quote.version}` : 'New quote';
  $('#quote-editor-message').textContent = '';
  form.hidden = false;
  updateQuoteUnits();
  form.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function costBreakdownHtml(record) {
  return `<div class="table-scroll"><table><thead><tr><th>Item</th><th>${escapeHtml(record.currency)}</th><th>USD</th></tr></thead><tbody>${(record.cost_breakdown || []).map(item => `<tr><td>${escapeHtml(item.label)}</td><td>${formatMoney(item.amount, record.currency)}</td><td>${formatMoney(item.amount_usd, 'USD')}</td></tr>`).join('')}<tr><th>Total landed cost</th><th>${formatMoney(record.total_cost, record.currency)}</th><th>${formatMoney(record.total_cost_usd, 'USD')}</th></tr></tbody></table></div>`;
}

function renderQuoteHistory(revisions) {
  $('#quote-revisions').innerHTML = revisions.map((revision, index) => {
    const before = revisions[index + 1]?.snapshot || {};
    const changes = revision.changed_fields.map(field => {
      const key = field === 'other_costs_json' ? 'other_costs' : field;
      return `<tr><th>${escapeHtml(QUOTE_FIELDS[key] || quoteOptions?.cost_fields?.[key] || key)}</th><td>${escapeHtml(quoteValue(before, key))}</td><td>${escapeHtml(quoteValue(revision.snapshot, key))}</td></tr>`;
    }).join('');
    return `<details class="quote-revision"><summary>Version ${revision.version} · ${formatDate(revision.created_at, true)} · ${escapeHtml(revision.actor)} · ${escapeHtml(revision.reason)}</summary><div class="table-scroll"><table><thead><tr><th>Field</th><th>Before</th><th>After</th></tr></thead><tbody>${changes}</tbody></table></div></details>`;
  }).join('');
}

function renderQuoteDetail(quote, revisions) {
  quoteCurrent = quote;
  $('#quote-detail').hidden = false;
  $('#quote-detail-title').textContent = `${quote.reference || `Quote #${quote.id}`} · ${quote.supplier} · version ${quote.version}`;
  const keys = ['product', 'grade', 'specification', 'origin', 'incoterm', 'incoterm_place', 'destination', 'quantity', 'unit', 'packaging', 'packaging_detail', 'payment_terms', 'quote_date', 'valid_until', 'delivery_date', 'document_type', 'status', 'notes'];
  $('#quote-detail-content').innerHTML = `<dl class="quote-fields">${keys.map(key => `<div><dt>${QUOTE_FIELDS[key]}</dt><dd>${escapeHtml(quoteValue(quote, key))}</dd></div>`).join('')}</dl>${costBreakdownHtml(quote)}<p class="field-help">Quoted price: ${formatNumber(quote.unit_price, 6)} ${escapeHtml(quote.currency)}/${escapeHtml(quoteValue(quote, 'unit'))} · exchange rate: ${formatNumber(quote.exchange_rate, 8)} USD per currency unit · landed cost: ${formatNumber(quote.landed_unit_cost_usd, 6)} USD/${escapeHtml(quoteValue(quote, 'unit'))}.</p>`;
  $('#quote-budget').disabled = quote.status !== 'active' || quote.expired || quote.quote_date > new Date().toISOString().slice(0, 10);
  $('#quote-documents').innerHTML = quote.documents.length ? quote.documents.map(doc => `<div class="quote-document"><span class="${doc.removed_at ? 'retired' : ''}">${escapeHtml(doc.filename)} · ${escapeHtml(quoteOptions?.document_types?.[doc.document_type] || doc.document_type)} · ${doc.size_bytes < 1024 ? `${doc.size_bytes} bytes` : `${formatNumber(doc.size_bytes / 1024, 1)} KB`}${doc.removed_at ? ' · withdrawn, kept in history' : ''}</span><button type="button" class="text-btn" data-download-document="${doc.id}">Download</button>${doc.removed_at ? '' : `<button type="button" class="text-btn" data-permission="editor" data-retire-document="${doc.id}">Withdraw</button>`}</div>`).join('') : '<p class="empty">No supporting documents attached.</p>';
  renderQuoteHistory(revisions);
  if (window.PriceMonitorAuth?.state.user) $('#quote-document-form').elements.namedItem('actor').value = window.PriceMonitorAuth.state.user.username;
  window.PriceMonitorAuth?.applyPermissions();
}

async function openQuote(id, scroll = true) {
  const request = ++quoteDetailRequest;
  try {
    const [data, history] = await Promise.all([quoteRequest(`/api/quotes/${id}`), quoteRequest(`/api/quotes/${id}/history`)]);
    if (request !== quoteDetailRequest) return;
    if (data.quote.version !== history.revisions[0]?.version) throw new Error('This quote changed while loading. Open it again.');
    renderQuoteDetail(data.quote, history.revisions);
    if (scroll) $('#quote-detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) { if (request === quoteDetailRequest) showToast(error.message, true); }
}

function renderQuoteComparison(data) {
  const panel = $('#quote-comparison');
  panel.hidden = false;
  const fields = ['product', 'grade', 'specification', 'origin', 'incoterm', 'incoterm_place', 'destination', 'quantity', 'unit', 'packaging', 'packaging_detail', 'delivery_date', 'payment_terms', 'document_type', 'quote_date', 'valid_until', 'unit_price', 'currency', 'exchange_rate', ...Object.keys(quoteOptions?.cost_fields || {}), 'other_costs', 'total_cost_usd', 'landed_unit_cost_usd'];
  const headers = data.quotes.map(quote => `<th>${escapeHtml(quote.supplier)}<br>${escapeHtml(quote.reference || `Quote #${quote.id}`)} · v${quote.version}</th>`).join('');
  const rows = fields.map(key => {
    const label = QUOTE_FIELDS[key] || quoteOptions?.cost_fields?.[key] || key;
    return `<tr><th>${escapeHtml(label)}</th>${data.quotes.map(quote => {
      const differs = data.differences.find(item => item.quote_id === quote.id)?.reasons.includes(label);
      return `<td class="${differs ? 'non-equivalent' : ''}">${escapeHtml(quoteValue(quote, key))}</td>`;
    }).join('')}</tr>`;
  }).join('');
  const ranks = data.quotes.map(quote => {
    const rank = data.ranking.find(item => item.quote_id === quote.id)?.rank;
    return `<td class="${rank === 1 ? 'best-quote' : ''}">${rank ? `Rank ${rank}` : 'Unranked'}<br>${escapeHtml(data.differences.find(item => item.quote_id === quote.id)?.reasons.join(' · ') || '')}</td>`;
  }).join('');
  panel.innerHTML = `<h3>Comparison as of ${formatDate(data.as_of)}</h3><p class="${data.equivalent ? '' : 'error-note'}">${escapeHtml(data.message)}</p><div class="table-scroll"><table class="quote-comparison-table"><thead><tr><th>Terms</th>${headers}</tr></thead><tbody>${rows}<tr><th>Ranking and differences</th>${ranks}</tr></tbody></table></div><p class="field-help">Each quote uses its saved exchange rate. Additional charges come from the quote records.</p>`;
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function resetProcurementQuoteLink() {
  const form = $('#procurement-form');
  form.elements.namedItem('quote_id').value = '';
  form.elements.namedItem('quote_version').value = '';
  ['product', 'supplier', 'quantity', 'currency', 'unit_price', 'exchange_rate', ...Object.keys(quoteOptions?.cost_fields || {})].forEach(key => {
    const field = form.elements.namedItem(key);
    if (field) { field.disabled = false; field.readOnly = false; }
  });
  $('#procurement-other-costs').querySelectorAll('input,button').forEach(field => { field.disabled = false; field.readOnly = false; });
  $('#procurement-add-cost').disabled = false;
  $('#procurement-quote-link').hidden = true;
  $('#procurement-unlink-quote').hidden = true;
}

function budgetFromQuote(quote) {
  const form = $('#procurement-form');
  form.reset();
  resetProcurementQuoteLink();
  const currency = form.elements.namedItem('currency');
  if (![...currency.options].some(option => option.value === quote.currency)) currency.add(new Option(quote.currency, quote.currency));
  ['product', 'supplier', 'quantity', 'currency', 'unit_price', 'exchange_rate', ...Object.keys(quoteOptions.cost_fields)].forEach(key => {
    const field = form.elements.namedItem(key);
    field.value = quote[key];
    if (field.tagName === 'SELECT') field.disabled = true;
    else field.readOnly = true;
  });
  form.elements.namedItem('purchase_date').value = new Date().toISOString().slice(0, 10);
  form.elements.namedItem('quote_id').value = quote.id;
  form.elements.namedItem('quote_version').value = quote.version;
  setCostItems($('#procurement-other-costs'), quote.other_costs);
  $('#procurement-other-costs').querySelectorAll('input,button').forEach(field => { field.disabled = true; });
  $('#procurement-add-cost').disabled = true;
  $('#procurement-quote-link').textContent = `Budget prepared from quote #${quote.id}, version ${quote.version} · ${quote.supplier}. The terms and amounts from this version will be preserved.`;
  $('#procurement-quote-link').hidden = false;
  $('#procurement-unlink-quote').hidden = false;
  updateProcurementUnits();
  window.history.pushState(null, '', '#budget');
  showPage();
}

async function documentAction(action, id) {
  const quote = quoteCurrent;
  const form = $('#quote-document-form');
  $('#quote-document-message').textContent = '';
  try {
    if (action === 'download') {
      const response = await protectedFetch(`/api/quotes/${quote.id}/documents/${id}`);
      if (!response.ok) throw new Error((await response.json()).error || 'Document unavailable.');
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = quote.documents.find(doc => doc.id === id)?.filename || 'document';
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      return;
    }
    const reason = form.elements.namedItem('change_reason').value.trim();
    if (!reason) throw new Error('Enter a reason for withdrawal in the supporting documents form.');
    await quoteRequest(`/api/quotes/${quote.id}/documents/${id}`, quoteJson('DELETE', {
      expected_version: quote.version, actor: form.elements.namedItem('actor').value, change_reason: reason
    }));
    if (quoteCurrent?.id === quote.id) await openQuote(quote.id, false);
    loadQuotes();
  } catch (error) { $('#quote-document-message').textContent = error.message; }
}

function fileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = () => reject(new Error('Could not read the file.'));
    reader.readAsDataURL(file);
  });
}

async function initialiseQuotes() {
  try {
    quoteOptions = await quoteRequest('/api/quotes/options');
    $('#quote-incoterm').innerHTML = '<option value="">Choose…</option>' + quoteOptions.incoterms.map(code => `<option>${escapeHtml(code)}</option>`).join('');
    $('#quote-packaging').innerHTML = Object.entries(quoteOptions.packaging).map(([key, label]) => `<option value="${escapeHtml(key)}">${escapeHtml(label)}</option>`).join('');
    $('#quote-document-type').innerHTML = Object.entries(quoteOptions.document_types).map(([key, label]) => `<option value="${escapeHtml(key)}">${escapeHtml(label)}</option>`).join('');
    $('#quote-cost-fields').innerHTML = Object.entries(quoteOptions.cost_fields).map(([key, label]) => `<label>${escapeHtml(label)}<input type="number" name="${escapeHtml(key)}" min="0" step="0.000001" value="0"></label>`).join('');
    $('#quote-new').disabled = false;
    await loadQuotes(1);
  } catch (error) { $('#quote-list-status').textContent = error.message; $('#quote-new').disabled = true; }
}

$('#quote-new').addEventListener('click', () => editQuote());
$('#quote-cancel').addEventListener('click', () => { $('#quote-editor').hidden = true; });
$('#quote-product').addEventListener('change', updateQuoteUnits);
$('#quote-packaging').addEventListener('change', updateQuoteUnits);
$('#quote-currency').addEventListener('change', event => {
  event.target.value = event.target.value.trim().toUpperCase();
  $('#quote-editor').elements.namedItem('exchange_rate').value = event.target.value === 'USD' ? '1' : '';
});
$('#quote-add-cost').addEventListener('click', () => addCostItem($('#quote-other-costs')));
$('#procurement-add-cost').addEventListener('click', () => addCostItem($('#procurement-other-costs')));
$('#procurement-unlink-quote').addEventListener('click', resetProcurementQuoteLink);
$('#quote-edit').addEventListener('click', () => editQuote(quoteCurrent));
$('#quote-budget').addEventListener('click', () => budgetFromQuote(quoteCurrent));
$('#quote-detail-close').addEventListener('click', () => { quoteDetailRequest++; quoteCurrent = null; $('#quote-detail').hidden = true; });
$('#quote-filters').addEventListener('submit', event => { event.preventDefault(); loadQuotes(1); });
$('#quote-prev').addEventListener('click', () => loadQuotes(quotePage - 1));
$('#quote-next').addEventListener('click', () => loadQuotes(quotePage + 1));
$('#quote-list').addEventListener('click', event => {
  const button = event.target.closest('[data-open-quote]');
  if (button) openQuote(Number(button.dataset.openQuote));
});
$('#quote-list').addEventListener('change', event => {
  const id = Number(event.target.dataset.quoteSelect);
  if (!id) return;
  if (event.target.checked && selectedQuoteIds.size >= 6) { event.target.checked = false; showToast('Compare at most 6 quotes at a time.', true); return; }
  event.target.checked ? selectedQuoteIds.add(id) : selectedQuoteIds.delete(id);
  updateQuoteSelection();
});
$('#quote-clear-selection').addEventListener('click', () => { selectedQuoteIds.clear(); $('#quote-comparison').hidden = true; loadQuotes(); });
$('#quote-compare').addEventListener('click', async () => {
  try { renderQuoteComparison(await quoteRequest(`/api/quotes/compare?${new URLSearchParams({ ids: [...selectedQuoteIds].join(','), as_of: $('#quote-compare-date').value })}`)); }
  catch (error) { showToast(error.message, true); }
});
$('#quote-editor').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.target;
  const data = Object.fromEntries(new FormData(form));
  data.currency = data.currency.toUpperCase();
  data.other_costs = readCostItems($('#quote-other-costs'));
  if (!quoteEditingId) delete data.change_reason;
  $('#quote-save').disabled = true;
  try {
    const result = await quoteRequest(quoteEditingId ? `/api/quotes/${quoteEditingId}` : '/api/quotes', quoteJson(quoteEditingId ? 'PUT' : 'POST', data));
    form.hidden = true;
    showToast('Quote saved with its revision history.');
    await Promise.all([loadQuotes(), openQuote(result.quote.id)]);
  } catch (error) { $('#quote-editor-message').textContent = error.message; }
  finally { $('#quote-save').disabled = false; }
});
$('#quote-documents').addEventListener('click', event => {
  const button = event.target.closest('[data-download-document],[data-retire-document]');
  if (button) documentAction(button.dataset.downloadDocument ? 'download' : 'retire', Number(button.dataset.downloadDocument || button.dataset.retireDocument));
});
$('#quote-document-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.target;
  const quote = quoteCurrent;
  const file = form.elements.namedItem('file').files[0];
  $('#quote-upload').disabled = true;
  $('#quote-document-message').textContent = '';
  try {
    if (!file || file.size > quoteOptions.max_document_bytes) throw new Error('Select a document no larger than 5 MB.');
    await quoteRequest(`/api/quotes/${quote.id}/documents`, quoteJson('POST', {
      expected_version: quote.version, filename: file.name, content_base64: await fileBase64(file),
      document_type: form.elements.namedItem('document_type').value, actor: form.elements.namedItem('actor').value,
      change_reason: form.elements.namedItem('change_reason').value
    }));
    form.reset();
    if (window.PriceMonitorAuth?.state.user) form.elements.namedItem('actor').value = window.PriceMonitorAuth.state.user.username;
    if (quoteCurrent?.id === quote.id) await openQuote(quote.id, false);
    loadQuotes();
  } catch (error) { $('#quote-document-message').textContent = error.message; }
  finally { $('#quote-upload').disabled = false; }
});
$('#quote-compare-date').value = new Date().toISOString().slice(0, 10);
initialiseQuotes();
