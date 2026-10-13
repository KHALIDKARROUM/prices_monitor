const test = require('node:test');
const assert = require('node:assert/strict');
const {attentionItems, attentionHtml} = require('../price_monitor/static/attention.js');

test('attention links distinguish stale data, active alerts, weak forecasts and quota waits', () => {
  const items = attentionItems({dashboard:{metrics:{brent:{current:80,is_stale:true},gasoil:{current:null},bitume:{current:500,is_stale:false}},source_comparison:{mismatch_count:1}},
    alerts:[{active:true,enabled:true},{active:true,muted:true},{active:false}],
    'forecast-brent':{product:{status:'ready'},warnings:[{message:'No demonstrated skill'}]},
    schedules:[{provider:'alpha_vantage',requests_last_24h:25,request_limit_24h:25,status:'deferred'},
      {provider:'yahoo',status:'deferred',reason:'Marché fermé.',failures:1}, {provider:'fred',status:'running',failures:1}]});
  assert.equal(items.length,6);
  assert.equal(items.filter(i=>i.id==='alerts')[0].text,'1 alerte(s) active(s)');
  assert.ok(items.find(i=>i.href==='#tendances'));
  assert.ok(items.find(i=>i.id==='provider-alpha_vantage'));
  assert.ok(!items.find(i=>i.id==='provider-yahoo'||i.id==='provider-fred'));
});
test('healthy and normally closed markets do not invent an attention item', () => {
  assert.deepEqual(attentionItems({dashboard:{metrics:{brent:{current:80,is_stale:true,market_closed:true}}},alerts:[],schedules:[]}),[]);
});
test('replacing the latest data clears recovered items and escapes displayed provider names', () => {
  const state = {dashboard:{metrics:{brent:{current:80,is_stale:true}}}};
  assert.equal(attentionItems(state).length,1);
  state.dashboard.metrics.brent.is_stale=false;
  assert.deepEqual(attentionItems(state),[]);
  const html=attentionHtml(attentionItems({schedules:[{provider:'<img src=x onerror=alert(1)>',status:'error'}]}));
  assert.match(html,/&lt;img/);
  assert.doesNotMatch(html,/<img/);
});
