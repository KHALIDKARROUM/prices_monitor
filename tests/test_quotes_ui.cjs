const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/price_monitor/static/quotes.js'), 'utf8');
function dashboard() {
  const elements = new Map();
  const $ = selector => {
    if (!elements.has(selector)) elements.set(selector, { innerHTML: '', textContent: '', hidden: true, disabled: false, scrollIntoView() {} });
    return elements.get(selector);
  };
  const context = vm.createContext({ $, PRODUCT_META: { bitume: { label: 'Bitumen' } },
    escapeHtml: value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    formatNumber: String, formatMoney: String, formatDate: String,
  });
  vm.runInContext(source.slice(0, source.indexOf("$('#quote-new').addEventListener")), context);
  vm.runInContext("quoteOptions = {packaging:{bulk:'Bulk'}, document_types:{quotation:'Quotation'}, cost_fields:{insurance_cost:'Insurance'}}", context);
  return { $, context };
}

function comparison(equivalent) {
  const a = { id: 1, version: 1, supplier: 'ABC', grade: '50/70', product: 'bitume', total_cost_usd: 5000 };
  const b = { id: 2, version: 4, supplier: '<img src=x>', grade: equivalent ? '50/70' : '70/100', product: 'bitume', total_cost_usd: 1000 };
  return { quotes: [a,b], as_of: '2026-10-13', equivalent, message: equivalent ? 'Equivalent quotes' : 'Non-equivalent quotes',
    differences: [{quote_id:1,reasons:[]},{quote_id:2,reasons:equivalent?[]:['Grade']}],
    ranking: equivalent ? [{quote_id:2,rank:1},{quote_id:1,rank:2}] : [] };
}

test('non-equivalent offers are shown side by side without declaring a cheap winner', () => {
  const { $, context } = dashboard();
  context.renderQuoteComparison(comparison(false));
  const html = $('#quote-comparison').innerHTML;
  assert.match(html, /Non-equivalent quotes/);
  assert.match(html, /non-equivalent/);
  assert.match(html, /Unranked/);
  assert.doesNotMatch(html, /Rank 1|best-quote/);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<img/);
});

test('only equivalent offers display the cost ranking and current versions', () => {
  const { $, context } = dashboard();
  context.renderQuoteComparison(comparison(true));
  assert.match($('#quote-comparison').innerHTML, /Rank 1/);
  assert.match($('#quote-comparison').innerHTML, /best-quote/);
  assert.match($('#quote-comparison').innerHTML, /v4/);
});

test('correction history shows old and corrected terms with escaped author and reason', () => {
  const { $, context } = dashboard();
  context.renderQuoteHistory([
    {version:2,actor:'<b>ABC</b>',reason:'Price corrected',created_at:'2026-10-13',changed_fields:['grade'],snapshot:{grade:'70/100'}},
    {version:1,actor:'ABC',reason:'Created',created_at:'2026-10-12',changed_fields:['grade'],snapshot:{grade:'50/70'}}
  ]);
  const html = $('#quote-revisions').innerHTML;
  assert.match(html, /Before/);
  assert.match(html, /50\/70/);
  assert.match(html, /70\/100/);
  assert.match(html, /&lt;b&gt;ABC/);
  assert.doesNotMatch(html, /<b>ABC/);
});

test('pagination counts stay visible and selections survive moving between pages', () => {
  const { $, context } = dashboard();
  vm.runInContext('selectedQuoteIds.add(1); selectedQuoteIds.add(2)', context);
  context.renderQuoteList({page:2,pages:3,total:5,items:[]});
  assert.equal($('#quote-list-status').textContent, '5 quote(s) · 2 selected');
  assert.equal($('#quote-prev').disabled, false);
  assert.equal($('#quote-next').disabled, false);
  assert.equal($('#quote-compare').disabled, false);
  assert.equal($('#quote-comparison').hidden, true);
});
