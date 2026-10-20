const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../src/price_monitor/static/app.js'),'utf8');

function view(mode, available=true) {
  const elements = new Map();
  const parent={querySelector(){return null},classList:{toggle(){} }};
  const $=selector=>{if(!elements.has(selector)) elements.set(selector,{hidden:false,innerHTML:'',textContent:'',parentElement:parent,insertAdjacentHTML(){}});return elements.get(selector)};
  const charts=[];
  function Chart(canvas,options){charts.push(options);this.destroy=()=>{}};
  const context=vm.createContext({$,chart:null,chartMode:mode,Chart,window:{Chart:available?Chart:null},Date,
    PRODUCT_META:{gasoil:{short:'Diesel',color:'#abc'},brent:{short:'Brent',color:'#def'},bitume:{short:'Bitume',color:'#aaa'}},
    ALERT_INSTRUMENTS:{},reducedMotion:{matches:true},formatDate:String,formatNumber:String,formatPct:String,
    displayUnit: unit => unit === 'USD/baril' ? 'USD/barrel' : unit,
    escapeHtml:value=>String(value??'').replace(/</g,'&lt;').replace(/>/g,'&gt;')});
  vm.runInContext(source.slice(source.indexOf('function renderChart('),source.indexOf('function renderLatest(')),context);
  return {$,charts,render:context.renderChart};
}
const data={gasoil:[{price:2,unit:'USD/gallon',source_date:'2026-01-01'},{price:3,unit:'USD/gallon',source_date:'2026-01-02'}],
  brent:[{price:80,unit:'USD/baril',source_date:'2026-01-01'},{price:90,unit:'USD/baril',source_date:'2026-01-02'}],
  bitume:[{price:550,unit:'indice',data_kind:'index',source_date:'2026-01-01'}]};

test('absolute prices and dashed continuations each use their own labelled unit axis',()=>{
  const {charts,render}=view('absolute');render(data);
  const config=charts[0];
  assert.deepEqual(Object.keys(config.options.scales),['x','y_gasoil','y_brent','y_bitume']);
  for(const row of config.data.datasets){
    assert.equal(config.options.scales[row.yAxisID].title.text.includes(row.unit),true);
    assert.equal(config.options.scales[row.yAxisID].stack,'markets');
  }
  assert.equal(config.data.datasets.filter(row=>row.isLatestCarryForward).length,3);
});
test('relative changes share only a dimensionless base-100 axis and disclose different origin dates',()=>{
  const {$,charts,render}=view('indexed');render(data);
  assert.deepEqual(Object.keys(charts[0].options.scales),['x','y']);
  assert.ok(charts[0].data.datasets.every(d=>d.yAxisID==='y'));
  assert.match($('#chart-note').textContent,/starting dates may differ/);
});
test('text summaries survive a missing chart library and clear when the period is empty',()=>{
  const {$,render}=view('absolute',false);render(data);
  assert.match($('#price-chart-summary').innerHTML,/Latest value 90 USD\/barrel/);
  assert.match($('#price-chart-summary').innerHTML,/minimum 80, maximum 90/);
  render({});
  assert.equal($('#price-chart-summary').textContent,'No dated observations for the selected period.');
});
test('an empty product never creates an unlabelled secondary axis',()=>{
  const {charts,render}=view('absolute');render({brent:data.brent});
  assert.deepEqual(Object.keys(charts[0].options.scales),['x','y_brent']);
  assert.ok(charts[0].data.datasets.every(d=>d.yAxisID==='y_brent'));
});
