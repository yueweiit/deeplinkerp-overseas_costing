"""Language changes must not change persisted batch/filter identities."""

import subprocess
from pathlib import Path


PARTS = Path(__file__).resolve().parents[1] / "page/overseas_cost_workbench/parts"


def test_translated_status_keeps_historical_filter_values():
    script = r"""
const fs = require('fs');
const assert = require('node:assert/strict');
global.OverseasCostWorkbenchState = require(require('path').join(require('path').dirname(process.argv[1]), '05-workbench-state.js'));
const source = fs.readFileSync(process.argv[1], 'utf8');
const Harness = Function(`return class Harness {${source}`)();
const workbench = Object.create(Harness.prototype);
workbench.lower = value => String(value || '').toLowerCase();
workbench.filters = {};
workbench.batchItems = {};
workbench.matchesWorkRole = () => true;
workbench.sortBatchesNewestFirst = value => value;
workbench.batches = [
  {name:'draft', status:'Draft'},
  {name:'imported', status:'Imported'},
  {name:'dirty', status:'Dirty'},
  {name:'calculated', status:'Calculated'},
  {name:'confirmed', status:'Calculated', confirm_status:'Confirmed'},
  {name:'trace', status:'Imported', source_type:'oa_logistics'},
];
for (const language of ['en', 'es', 'zh']) {
  globalThis.__ = text => language === 'zh' ? text : `${language}:${text}`;
  for (const [value, expected] of Object.entries({
    '草稿':['draft'], '已导入':['imported'], '待重算':['dirty'],
    '已试算':['calculated','confirmed'], '已确认':['confirmed'], '仅拉取审批':['trace'],
  })) {
    workbench.filters.calculation_status = value;
    assert.deepEqual(workbench.filterBatches().map(row => row.name), expected);
  }
  assert.equal(workbench.batchStatusInfo('Draft').label, globalThis.__('草稿'));
}
assert.equal(workbench.batches[0].status, 'Draft');
console.log('ok');
"""
    result = subprocess.run(
        ["node", "-e", script, str(PARTS / "110-helpers.js")],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_air_sea_translation_preserves_field_ids_and_calculation_values():
    page = PARTS.parents[1] / "air_sea_cost_comparison"
    script = r"""
const fs=require('fs'), vm=require('vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const template=fs.readFileSync(process.argv[2],'utf8');
const fields=[...template.matchAll(/data-field="([^"]+)"/g)].map(m=>m[1]);
for(const language of ['en','es','zh']){
 const context={console,crypto:require('crypto').webcrypto,frappe:{boot:{lang:language}}};
 context.__=(text,args=[])=>{
   const translated=language==='zh'?text:'localized:'+text.replace(/[\u3400-\u9fff]/g,'x');
   return translated.replace(/\{(\d+)\}/g,(_,index)=>args[index]??'');
 };
 vm.createContext(context);vm.runInContext(source,context);
 const C=context.OverseasAirSeaCalculator;
 const row=Object.assign(Array(30).fill(''),{1:'SKU-UNCHANGED',4:'PZ',20:'100',23:'10',25:'2'});
 const payload={rows:[row],parameters:{},currencies:{},source:null};
 const before=JSON.stringify(payload),result=C.calculate(payload);
 assert.equal(result.status,'Ready');assert.equal(result.unit,'件');
 assert.equal(result.v.quantity,100);assert.equal(result.airTransport,2400);
 assert.equal(JSON.stringify(payload),before);
 const nodes=Object.fromEntries(fields.map(id=>[id,{textContent:'',hidden:false,classList:{add(){}}}]));
 const page=Object.create(C.AirSeaComparisonPage.prototype);
 page.$=id=>{assert.ok(nodes[id], 'Unknown DOM field: '+id);return nodes[id]};
 page.session=new C.RecordSession();page.session.payload={rows:[],parameters:{},currencies:{},source:null};
 page.formulas=()=>{};page.alignRows=()=>{};page.render();
 if(language!=='zh')for(const node of Object.values(nodes))assert.doesNotMatch(String(node.textContent),/[\u3400-\u9fff]/);
}
console.log('ok');
"""
    result = subprocess.run(
        ["node", "-e", script, str(page / "air_sea_cost_comparison.js"),
         str(page / "parts/40-template.html")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
