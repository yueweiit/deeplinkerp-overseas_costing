from pathlib import Path

from overseas_costing.tests.test_saved_trial_regressions import _frontend_result, FRONTEND_SETUP
from overseas_costing.tests.test_workbench_frontend_state import _fee_workspace_result

MATERIAL_GRID_PART = (Path(__file__).resolve().parents[1] / 'page' / 'overseas_cost_workbench'
                      / 'parts' / '78-material-fee-workspace.js')
MATERIAL_GRID_CSS = (Path(__file__).resolve().parents[1] / 'page' / 'overseas_cost_workbench'
                     / 'parts' / '48-material-fee-workspace.css')


def test_stale_cost_is_collapsed_history_not_complete_badge():
    html=_frontend_result(FRONTEND_SETUP + """
h.escape=x=>String(x ?? '');
h.detailState.header={status:'Dirty',summary_snapshot:{comprehensive_cost:{summary:{is_complete:true,total_cost_rmb:'88404.00'},items:[]}}};
console.log(JSON.stringify(h.renderMaterialFeeCostTable()));
""")
    assert '待重新试算' in html
    assert '<details class="ocw-mf-cost-history">' in html
    assert '完整成本' not in html


def test_saved_trial_rows_show_comparable_unit_price_and_comprehensive_unit_price():
    html = _frontend_result(FRONTEND_SETUP + """
h.escape=x=>String(x ?? '');
h.detailState.header={status:'Calculated',summary_snapshot:{comprehensive_cost:{summary:{is_complete:true,total_cost_rmb:'4795.57'},items:[{
  material_code:'CW000214',product_name:'狗牌Dog tag',goods_value_rmb:'2067.00',direct_fees_rmb:'0.00',
  allocated_fees_rmb:'2728.57',total_cost_rmb:'4795.57',
  shipping_unit_price:{amount_rmb:'1.215882',uom:'个'},
  shipping_unit_cost:{amount_rmb:'2.820924',uom:'个'},
  purchase_pricing_unit_cost:{amount_rmb:'999.000000',uom:'箱'}
}]}}};
console.log(JSON.stringify(h.renderMaterialFeeCostTable()));
""")

    assert "单价（RMB）" in html
    assert "综合单价（RMB）" in html
    assert "1.22 / 个" in html
    assert "2.82 / 个" in html
    assert "1.215882" not in html
    assert "2.820924" not in html
    assert "999.000000" not in html
    assert "每发货单位" not in html
    assert "每采购计价单位" not in html


def test_saved_trial_rows_explain_missing_unit_price_inputs():
    html = _frontend_result(FRONTEND_SETUP + """
h.escape=x=>String(x ?? '');
h.detailState.header={status:'Calculated',summary_snapshot:{comprehensive_cost:{summary:{is_complete:false,total_cost_rmb:'50.00'},items:[
  {material_code:'MISSING-VALUE',product_name:'货值待补',goods_value_rmb:'0.00',direct_fees_rmb:'0.00',allocated_fees_rmb:'50.00',total_cost_rmb:'50.00',
   valuation_source:{error:'GOODS_VALUE_MISSING'},shipping_unit_price:null,shipping_unit_cost:{amount_rmb:'10.000000',uom:'个'}},
  {material_code:'MISSING-QUANTITY',product_name:'数量待补',goods_value_rmb:'20.00',direct_fees_rmb:'0.00',allocated_fees_rmb:'0.00',total_cost_rmb:'20.00',
   shipping_unit_price:null,shipping_unit_cost:null},
  {material_code:'LEGACY-SNAPSHOT',product_name:'旧试算',goods_value_rmb:'30.00',direct_fees_rmb:'0.00',allocated_fees_rmb:'15.00',total_cost_rmb:'45.00',
   valuation_source:{error:''},shipping_unit_cost:{amount_rmb:'15.000000',uom:'个'}}
]}}};
console.log(JSON.stringify(h.renderMaterialFeeCostTable()));
""")

    assert "货值待补" in html
    assert html.count("待补发货数量/单位") == 2
    assert "10.00 / 个" in html
    assert "重新试算后显示" in html


def test_derived_purchase_price_is_readonly_and_names_its_source():
    html = _frontend_result(FRONTEND_SETUP + """
h.escape=x=>String(x ?? '');
const item={name:'I1',unit_price:'1.22',adopted_price:{value:'1.22',currency:'RMB',unit:'pieza',source_type:'purchase_total_derived',error:''}};
console.log(JSON.stringify(h.renderMaterialFeeGridCell(item,{field:'unit_price',label:'采购单价',numeric:true},new Set(),0)));
""")

    assert "1.22" in html
    assert "按货值÷采购数量计算" in html
    assert "data-mf-cell-input" not in html


def test_material_fee_workspace_has_a_visible_trial_entry_next_to_status() -> None:
    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={header:{status:'Dirty'}};
const state=w.ensureMaterialFeeState();state.materials={packing_groups:[{
  group_id:'G-OLD',status:'confirmed',blocking:true,member_keys:['OLD-1','OLD-2']}],items:[]};
state.fees={summary:{}};state.preview={summary:{is_complete:false},items:[],incomplete_reasons:[]};
w.escape=value=>String(value??'');w.materialFeeSavedCostPreview=()=>null;
const html=w.renderMaterialFeeCostTable();
console.log(JSON.stringify({html,blocked:w.hasBlockingPackingGroups(state)}));
""")

    assert result["blocked"] is True
    assert 'data-action="mf-preview-cost"' not in result["html"]
    assert 'data-action="detail-primary"' in result["html"]
    assert '开始试算' in result["html"]
    assert "请使用页头开始试算" not in result["html"]
    assert "请先重新确认装箱组" in result["html"]


def test_material_fee_workspace_with_preview_keeps_adjustment_and_retrial_entry() -> None:
    html = _frontend_result(FRONTEND_SETUP + """
h.escape=x=>String(x ?? '');
h.detailState.header={status:'Calculated',summary_snapshot:{comprehensive_cost:{summary:{is_complete:true,total_cost_rmb:'100.00'},items:[]}}};
console.log(JSON.stringify(h.renderMaterialFeeCostTable()));
""")
    assert 'data-action="mf-adjust-cost"' in html
    assert 'data-action="mf-preview-cost"' not in html
    assert 'data-action="detail-primary"' in html
    assert '重新试算' in html


def test_autofill_renders_shipment_value_and_project_summary():
    html=_frontend_result(FRONTEND_SETUP + """
h.escape=x=>String(x ?? '');
const fill={selections:new Set(),draft:{autofill_preview:{items:[{material_code:'CW000191',shipment_value_rmb:'10560',project_collection:'TK宠物用品项目'}],
project_summary:[{project_collection:'TK宠物用品项目',goods_value_rmb:'10560.00',gross_weight_kg:'130',allocated_fees_rmb:'1151.43',total_cost_rmb:'11711.43'}]}}};
console.log(JSON.stringify(h.renderMaterialAIAutofillPreview(fill)));
""")
    assert '本次发货货值 RMB' in html
    assert '10560' in html and 'TK宠物用品项目' in html and '1151.43' in html


def test_edited_preview_does_not_display_obsolete_project_summary():
    result=_frontend_result(FRONTEND_SETUP + """
h.escape=x=>String(x ?? '');
const fill={selections:new Set(),manualUpdates:{weight:{item_name:'I',fieldname:'gross_weight_kg',value:20}},
draft:{autofill_preview:{items:[{name:'I'}],project_summary:[{project_collection:'P',allocated_fees_rmb:'1151.43'}]}}};
console.log(JSON.stringify(h.renderMaterialAIAutofillPreview(fill)));
""")
    assert '1151.43' not in result
    assert '重新试算' in result


def test_primary_value_distinguishes_valued_shipment_from_unmatched_purchase():
    result=_frontend_result(FRONTEND_SETUP + """
h.escape=x=>String(x ?? '');h.formatValue=x=>String(x);h.materialAICell=()=>null;
console.log(JSON.stringify(h.renderMaterialFeeGridCell({name:'I',shipment_value_rmb:'10560',
shipment_valuation:{method:'packing_row_total',status:'automatic',error:''}}, {field:'shipment_value_rmb',label:'本次发货货值 RMB',numeric:true,readonly:false},new Set(),0)));
""")
    assert '>10560<' in result and '自动估值' in result
    assert 'data-action="mf-correct-purchase"' in result and '<input' not in result


def test_shipment_value_is_editable_and_renders_all_escaped_valuation_states():
    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={};
w.materialFeeState={batchName:'',materialDrafts:{}};
w.escape=v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
w.formatValue=v=>String(v??'');w.materialAICell=()=>null;
const column=w.materialFeeGridColumns().find(row=>row.field==='shipment_value_rmb');
const make=(status,value,extra={})=>w.renderMaterialFeeGridCell({name:`I-${status}`,shipment_value_rmb:value,
  shipment_valuation:{status,...extra}},column,new Set(),3);
console.log(JSON.stringify({column,automatic:make('automatic','12.5'),manual:make('manual',0),
  conflict:make('conflict','40',{prior_amount_rmb:'<旧值>',calculated_amount_rmb:'60&新值'}),
  missing:make('missing',''),stale:make('stale',null,{prior_amount_rmb:'25'})}));
""")
    assert result["column"]["readonly"] is False
    assert 'data-mf-cell-input="1"' not in result["automatic"] and "自动估值" in result["automatic"]
    assert 'data-action="mf-correct-purchase"' in result["automatic"]
    assert '>0<' in result["manual"] and "人工确认" in result["manual"]
    assert "待确认" in result["conflict"]
    assert "采用旧值" in result["conflict"] and "采用计算值" in result["conflict"]
    assert "&lt;旧值&gt;" in result["conflict"] and "60&amp;新值" in result["conflict"]
    assert "<旧值>" not in result["conflict"] and "60&新值" not in result["conflict"]
    assert 'value=""' in result["missing"] and "待补" in result["missing"]
    assert "数量或单位已变化" in result["stale"]


def test_saved_blocked_purchase_value_is_visible_without_filling_the_shipment_value():
    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={};
w.escape=v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
w.formatValue=v=>String(v??'');w.materialAICell=()=>null;
const column=w.materialFeeGridColumns().find(row=>row.field==='shipment_value_rmb');
console.log(JSON.stringify(w.renderMaterialFeeGridCell({name:'I',shipment_value_rmb:null,
 shipment_valuation:{status:'missing',error:'PURCHASE_VALUE_IDENTITY_MISMATCH'},
 purchase_value_evidence:{status:'review',original_amount:'1000',currency:'USD',amount_rmb:'7365',message:'规格有变化 <原单>'}},
 column,new Set(['shipment_value_rmb']),3)));
""")
    assert '已存采购货值 7365 RMB · 仅供核对' in result
    assert '规格有变化 &lt;原单&gt;' in result
    assert 'value=""' in result and 'value="7365"' not in result
    assert '自动估值' not in result and 'mf-shipment-valuation-adopt' not in result


def test_shipment_value_zero_and_blank_use_existing_single_cell_save_contract():
    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={batchName:'B',versionName:'V',tab:'documents',editToken:'T',expectedModified:'M'};
const state=w.ensureMaterialFeeState();state.materials={items:[{name:'ZERO',shipment_value_rmb:'9'},{name:'CLEAR',shipment_value_rmb:'5'}]};
w.normalizeErrorMessage=e=>e.message;w.ensureMaterialFeeEditSession=async()=>true;w.loadMaterialFeeWorkspace=async()=>true;
const calls=[];w.call=async(method,args)=>{calls.push({method,args});return {ok:true,batch_modified:`M${calls.length}`}};
function input(item,value,original){const data={};const cell={addClass(){return this},removeClass(){return this},attr(){return this}};return {length:1,
 value,val(){return this.value},attr(name){return {'data-original-value':original,'data-item-name':item,'data-fieldname':'shipment_value_rmb'}[name]},
 data(name,next){if(arguments.length>1){data[name]=next;return this}return data[name]},prop(){return this},closest(){return cell}}}
await w.saveMaterialFeeCell(input('ZERO','0','9'));await w.saveMaterialFeeCell(input('CLEAR','','5'));
console.log(JSON.stringify({calls,pending:state.pendingWrites.size,drafts:state.materialDrafts}));
""")
    assert [call["args"]["value"] for call in result["calls"]] == ["0", ""]
    assert all(call["args"]["fieldname"] == "shipment_value_rmb" for call in result["calls"])
    assert all(call["args"]["edit_token"] == "T" for call in result["calls"])
    assert result["calls"][0]["args"]["expected_modified"] == "M"
    assert result["calls"][1]["args"]["expected_modified"] == "M1"
    assert result["pending"] == 0 and result["drafts"] == {}


def test_shipment_conflict_shortcut_writes_input_and_calls_real_save_method():
    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);let saved=null,updated=null;
const input={length:1,value:'40',val(next){if(arguments.length){this.value=next;return this}return this.value}};
const cell={find(selector){return selector==='[data-mf-cell-input]'?{...input,first(){return input}}:{length:0}}};
const button={attr:name=>name==='data-value'?'60':'',closest:()=>cell};global.$=value=>value;
w.updateMaterialDraftFromInput=value=>{updated=value.val()};w.saveMaterialFeeCell=async value=>{saved=value.val()};
await w.adoptShipmentValuationCandidate(button);
console.log(JSON.stringify({value:input.value,updated,saved}));
""")
    assert result == {"value": "60", "updated": "60", "saved": "60"}


MATERIAL_CELL_QUEUE_FIXTURE = r"""
const w=Object.create(Harness.prototype);w.detailState={batchName:'B',versionName:'V',tab:'documents',editToken:'T',expectedModified:'M1'};
const state=w.ensureMaterialFeeState();state.materials={items:[{name:'I',shipment_value_rmb:'40'}]};
w.normalizeErrorMessage=error=>error.message;w.ensureMaterialFeeEditSession=async()=>true;w.loadMaterialFeeWorkspace=async()=>true;
const calls=[];const releases=[];let active=0,maxActive=0;
w.call=async(method,args)=>{calls.push({method,args});active+=1;maxActive=Math.max(maxActive,active);
  const result=await new Promise(resolve=>releases.push(resolve));active-=1;return result};
const data={};const classes=new Set();const cell={addClass(name){classes.add(name);return this},removeClass(name){classes.delete(name);return this},attr(){return this},
  find(selector){return selector==='[data-mf-cell-input]'?{first(){return input}}:{length:0}}};
const input={length:1,value:'40',val(next){if(arguments.length){this.value=String(next);return this}return this.value},
  attr(name){return {'data-original-value':'40','data-item-name':'I','data-fieldname':'shipment_value_rmb'}[name]},
  data(name,next){if(arguments.length>1){data[name]=next;return this}return data[name]},prop(){return this},closest(){return cell}};
const button=value=>({attr:name=>name==='data-value'?String(value):'',closest:()=>cell});global.$=value=>value;
"""


def test_two_fast_shipment_shortcuts_serialize_and_save_latest_value():
    result = _fee_workspace_result(MATERIAL_CELL_QUEUE_FIXTURE + r"""
const first=w.adoptShipmentValuationCandidate(button(60));await new Promise(resolve=>setImmediate(resolve));
const second=w.adoptShipmentValuationCandidate(button(70));await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(calls.map(call=>call.args.value),['60']);
releases.shift()({ok:true,batch_modified:'M2'});await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(calls.map(call=>call.args.value),['60','70']);
releases.shift()({ok:true,batch_modified:'M3'});await Promise.all([first,second]);
console.log(JSON.stringify({values:calls.map(call=>call.args.value),modified:calls.map(call=>call.args.expected_modified),
  maxActive,pending:state.pendingWrites.size,drafts:state.materialDrafts,errors:state.materialSaveErrors}));
""")
    assert result == {
        "values": ["60", "70"],
        "modified": ["M1", "M2"],
        "maxActive": 1,
        "pending": 0,
        "drafts": {},
        "errors": {},
    }


def test_queued_shipment_save_failure_preserves_latest_draft_and_error():
    result = _fee_workspace_result(MATERIAL_CELL_QUEUE_FIXTURE + r"""
const first=w.adoptShipmentValuationCandidate(button(60));await new Promise(resolve=>setImmediate(resolve));
const second=w.adoptShipmentValuationCandidate(button(70));
releases.shift()({ok:true,batch_modified:'M2'});await new Promise(resolve=>setImmediate(resolve));
releases.shift()({ok:false,message:'并发保存失败'});await Promise.all([first,second]);
console.log(JSON.stringify({values:calls.map(call=>call.args.value),maxActive,pending:state.pendingWrites.size,
  draft:state.materialDrafts['I:shipment_value_rmb'],error:state.materialSaveErrors['I:shipment_value_rmb']}));
""")
    assert result["values"] == ["60", "70"] and result["maxActive"] == 1
    assert result["pending"] == 0
    assert result["draft"]["value"] == "70"
    assert result["draft"]["error"] == "并发保存失败"
    assert result["error"] == "并发保存失败"


def test_fast_regular_input_returning_to_saved_value_is_serialized_and_clears_draft():
    result = _fee_workspace_result(MATERIAL_CELL_QUEUE_FIXTURE + r"""
input.value='60';const first=w.saveMaterialFeeCell(input);await new Promise(resolve=>setImmediate(resolve));
input.value='40';const second=w.saveMaterialFeeCell(input);
releases.shift()({ok:true,batch_modified:'M2'});await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(calls.map(call=>call.args.value),['60','40']);
releases.shift()({ok:true,batch_modified:'M3'});await Promise.all([first,second]);
console.log(JSON.stringify({values:calls.map(call=>call.args.value),modified:calls.map(call=>call.args.expected_modified),
  maxActive,pending:state.pendingWrites.size,drafts:state.materialDrafts,errors:state.materialSaveErrors}));
""")
    assert result == {
        "values": ["60", "40"],
        "modified": ["M1", "M2"],
        "maxActive": 1,
        "pending": 0,
        "drafts": {},
        "errors": {},
    }


def test_queued_failure_marks_rerendered_live_cell_and_live_retry_succeeds():
    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={batchName:'B',versionName:'V',tab:'documents',editToken:'T',expectedModified:'M1'};
const state=w.ensureMaterialFeeState();state.materials={items:[{name:'I',shipment_value_rmb:'40'}]};
w.normalizeErrorMessage=error=>error.message;w.ensureMaterialFeeEditSession=async()=>true;
function makeInput(value,original){const data={};const classes=new Set();const attrs={title:''};const cell={classes,attrs,
  addClass(name){classes.add(name);return this},removeClass(name){name.split(/\s+/).forEach(value=>classes.delete(value));return this},
  attr(name,next){if(arguments.length>1){attrs[name]=next;return this}return attrs[name]}};
  const input={length:1,value,cell,val(next){if(arguments.length){this.value=String(next);return this}return this.value},
    attr(name){return {'data-original-value':original,'data-item-name':'I','data-fieldname':'shipment_value_rmb'}[name]},
    data(name,next){if(arguments.length>1){data[name]=next;return this}return data[name]},prop(){return this},closest(){return cell}};
  return input}
const oldInput=makeInput('60','40');const liveInput=makeInput('70','60');let currentInput=oldInput;let reloads=0;
global.$=value=>value;w.$root={find(selector){return selector==='[data-mf-cell-input]'?{each(callback){callback(0,currentInput)}}:{each(){}}}};
w.loadMaterialFeeWorkspace=async()=>{reloads+=1;if(reloads===1){state.materials.items[0].shipment_value_rmb='60';currentInput=liveInput}return true};
const calls=[];const releases=[];let active=0,maxActive=0;w.call=async(method,args)=>{calls.push({method,args});active+=1;maxActive=Math.max(maxActive,active);
  const result=await new Promise(resolve=>releases.push(resolve));active-=1;return result};
const first=w.saveMaterialFeeCell(oldInput);await new Promise(resolve=>setImmediate(resolve));
oldInput.value='70';const second=w.saveMaterialFeeCell(oldInput);
releases.shift()({ok:true,batch_modified:'M2'});await new Promise(resolve=>setImmediate(resolve));
releases.shift()({ok:false,message:'第二次失败'});await Promise.all([first,second]);
const afterFailure={liveError:liveInput.cell.classes.has('is-save-error'),liveTitle:liveInput.cell.attrs.title,
  oldError:oldInput.cell.classes.has('is-save-error'),draft:state.materialDrafts['I:shipment_value_rmb'],pending:state.pendingWrites.size};
const retry=w.saveMaterialFeeCell(liveInput);await new Promise(resolve=>setImmediate(resolve));
releases.shift()({ok:true,batch_modified:'M3'});await retry;
console.log(JSON.stringify({afterFailure,values:calls.map(call=>call.args.value),modified:calls.map(call=>call.args.expected_modified),
  maxActive,pending:state.pendingWrites.size,drafts:state.materialDrafts,errors:state.materialSaveErrors,
  liveError:liveInput.cell.classes.has('is-save-error'),liveTitle:liveInput.cell.attrs.title}));
""")
    assert result["afterFailure"]["liveError"] is True
    assert "第二次失败" in result["afterFailure"]["liveTitle"]
    assert result["afterFailure"]["oldError"] is False
    assert result["afterFailure"]["draft"]["value"] == "70"
    assert result["afterFailure"]["draft"]["error"] == "第二次失败"
    assert result["afterFailure"]["pending"] == 0
    assert result["values"] == ["60", "70", "70"]
    assert result["modified"] == ["M1", "M2", "M2"]
    assert result["maxActive"] == 1
    assert result["pending"] == 0 and result["drafts"] == {} and result["errors"] == {}
    assert result["liveError"] is False and result["liveTitle"] == ""


def test_shipment_value_is_in_bulk_paste_columns_and_goods_value_is_audit_only():
    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={};w.materialFeeState={batchName:'',showAuxiliary:true};
const columns=w.materialFeeGridColumns();
console.log(JSON.stringify({editable:columns.filter(column=>!column.readonly).map(column=>column.field),
  goods:columns.find(column=>column.field==='goods_value')}));
""")
    assert "shipment_value_rmb" in result["editable"]
    assert "goods_value" not in result["editable"]
    assert result["goods"]["readonly"] is True
    assert result["goods"]["label"] == "货值兼容值 RMB"


def test_material_grid_starts_with_selection_and_row_number_without_actions_column():
    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={};w.ensureMaterialFeeState();
const columns=w.materialFeeGridColumns();
console.log(JSON.stringify({fields:columns.map(column=>column.field)}));
""")
    assert result["fields"][:4] == ["__group_select", "row_no", "material_code", "product_name"]
    assert "__actions" not in result["fields"]


def test_packing_group_renders_true_rowspan_without_per_row_actions():
    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={};const state=w.ensureMaterialFeeState();
w.escape=v=>String(v??'');w.formatValue=v=>String(v??'');w.materialAICell=()=>null;
const group={group_id:'G1',member_keys:['L1','L2','L3'],package_count:'21',net_weight_kg:'389',
 gross_weight_kg:'397.7',volume_m3:'0.40884',rowspan:3};
const first={name:'I1',stable_line_key:'L1',packing_group_id:'G1',packing_group_position:0,packing_group_size:3,packing_group:group};
const second={name:'I2',stable_line_key:'L2',packing_group_id:'G1',packing_group_position:1,packing_group_size:3,packing_group:{...group,rowspan:0}};
const columns=w.materialFeeGridColumns();const physical=columns.filter(c=>['package_count','net_weight_kg','gross_weight_kg','volume_m3'].includes(c.field));
const rendered=physical.map((column,index)=>w.renderMaterialFeeGridCell(first,column,new Set(),index)).join('');
const hidden=physical.map((column,index)=>w.renderMaterialFeeGridCell(second,column,new Set(),index)).join('');
console.log(JSON.stringify({rendered,hidden,fields:physical.map(c=>c.field)}));
""")
    assert result["fields"] == ["package_count", "net_weight_kg", "gross_weight_kg", "volume_m3"]
    assert result["rendered"].count('rowspan="3"') == 4
    assert all(value in result["rendered"] for value in ["21", "389", "397.7", "0.40884"])
    assert result["hidden"] == ""


def test_historical_material_grid_disables_packing_group_edits():
    """历史/只读批次里编辑动作全部收口：没有行内操作列，勾选框只是选择控件
    （有身份即可勾选查看，见「全选本页一直可点」契约），真正被禁的是写入
    —— 包括行内那个「选择供应商」入口，它和工具栏共用同一个可写判据。"""

    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={readOnly:true};const state=w.ensureMaterialFeeState();
state.materials={packing_group_editable:false,reference_editable:false};w.escape=v=>String(v??'');w.formatValue=v=>String(v??'');
const item={name:'I1',stable_line_key:'L1',packing_group_id:'G1',packing_group_position:0,packing_group_size:2,packing_group:{group_id:'G1'}};
const columns=w.materialFeeGridColumns();
const select=w.renderMaterialFeeGridCell(item,columns.find(c=>c.field==='__group_select'),new Set(),3);
const row=Object.assign({},item,{supplier:''});
const readonlyPicker=w.renderMaterialFeeGridCell(row,columns.find(c=>c.field==='supplier'),new Set(),4);
const context=w.materialSelectionContext();
w.detailState={readOnly:false};
w.ensureMaterialFeeState().materials={packing_group_editable:true,reference_editable:true};
const writablePicker=w.renderMaterialFeeGridCell(row,columns.find(c=>c.field==='supplier'),new Set(),4);
console.log(JSON.stringify({select,fields:columns.map(column=>column.field),readonlyPicker,writablePicker,
  add:context.actions.add,merge:context.actions.merge,project:context.actions.project,supplier:context.actions.supplier}));
""")
    # 勾选控件在只读批次仍然可用（有身份的行），不再是编辑禁用的代理断言。
    assert 'disabled' not in result['select']
    assert result['add']['enabled'] is False
    assert result['merge']['enabled'] is False
    assert result['project']['enabled'] is False
    assert result['supplier']['enabled'] is False
    assert '__actions' not in result['fields']
    # 只读批次里点开选择器必然被服务端拒绝，所以入口本身就该是禁用的；可写批次照常可点。
    assert 'disabled' in result['readonlyPicker']
    assert '选择供应商' in result['readonlyPicker']
    assert 'disabled' not in result['writablePicker']


def test_confirmed_batch_still_maintains_supplier_and_project_attribution():
    """已确认 / 已锁定的当前版本：成本字段与结构操作冻结，但供应商与项目归属仍可按行维护。

    这两项是采购在成本确认**之后**才补的 ERP 基础资料、不参与金额计算，服务端单开一档放行
    （`reference_editable` ≡ `_assert_current_item_version(..., reference_fields=True)`），
    写入后还会把已存结果的输入指纹重新对齐。前端若拿成本字段那个判据把它们一起禁掉，
    采购就再也补不了资料 —— 这正是用户报的「修正 / 选择供应商全灰」。
    """

    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={readOnly:false};const state=w.ensureMaterialFeeState();
state.materials={packing_group_editable:false,reference_editable:true,items:[
  {name:'I1',stable_line_key:'L1',row_no:1,project_collection:'',supplier:''},
  {name:'I2',stable_line_key:'L2',row_no:2,project_collection:'Guangzhou Lingxi',supplier:''}]};
state.packingGroupSelections=new Set(['L1','L2']);
w.escape=v=>String(v??'');w.formatValue=v=>String(v??'');
const columns=w.materialFeeGridColumns();
const cell=(item,field)=>w.renderMaterialFeeGridCell(item,columns.find(c=>c.field===field),new Set(),0);
const context=w.materialSelectionContext();
console.log(JSON.stringify({
  projectPicker:cell(state.materials.items[1],'project_collection'),
  supplierPicker:cell(state.materials.items[0],'supplier'),
  project:context.actions.project,supplier:context.actions.supplier,
  add:context.actions.add,remove:context.actions.remove,
  readonly:context.readonly,referenceReadonly:context.referenceReadonly,
  note:w.materialReadonlyNoteHtml(),toolbar:w.renderMaterialSelectionToolbar()
}));
""")
    # 两个参考入口按行可点：有值给「修正」，空值给「选择供应商」。
    assert 'disabled' not in result['projectPicker']
    assert '修正' in result['projectPicker']
    assert 'disabled' not in result['supplierPicker']
    assert '选择供应商' in result['supplierPicker']
    assert result['project']['enabled'] is True
    assert result['supplier']['enabled'] is True
    # 成本字段与结构操作仍然冻结 —— 放开只限这两项。
    assert result['add']['enabled'] is False
    assert result['remove']['enabled'] is False
    assert result['readonly'] is True
    assert result['referenceReadonly'] is False
    # 只读提示必须说清「哪些不能改、哪些还能补」，否则采购会以为资料补不了。
    assert '基础资料可维护' in result['note']
    assert '成本字段只读 · 可补供应商/项目归属' in result['toolbar']
    assert '批量设置不可用' not in result['toolbar']


def test_reference_fields_stay_locked_on_archived_versions():
    """历史 / 归档版本（reference_editable=false）照旧只读：这一档只放宽「当前版本」。"""

    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={readOnly:false};const state=w.ensureMaterialFeeState();
state.materials={packing_group_editable:false,reference_editable:false,items:[
  {name:'I1',stable_line_key:'L1',row_no:1,project_collection:'',supplier:''}]};
state.packingGroupSelections=new Set(['L1']);
w.escape=v=>String(v??'');w.formatValue=v=>String(v??'');
const columns=w.materialFeeGridColumns();
const cell=(item,field)=>w.renderMaterialFeeGridCell(item,columns.find(c=>c.field===field),new Set(),0);
const context=w.materialSelectionContext();
console.log(JSON.stringify({
  projectPicker:cell(state.materials.items[0],'project_collection'),
  supplierPicker:cell(state.materials.items[0],'supplier'),
  project:context.actions.project,referenceReadonly:context.referenceReadonly,
  note:w.materialReadonlyNoteHtml(),toolbar:w.renderMaterialSelectionToolbar()
}));
""")
    assert 'disabled' in result['projectPicker']
    assert 'disabled' in result['supplierPicker']
    assert result['project']['enabled'] is False
    assert '历史或归档版本' in result['project']['reason']
    assert result['referenceReadonly'] is True
    assert '历史或归档版本' in result['note']
    assert '批量设置不可用' in result['toolbar']


def test_material_selection_is_exact_and_toolbar_enforces_action_matrix():
    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={readOnly:false};const state=w.ensureMaterialFeeState();
state.materials={packing_group_editable:true,items:[
  {stable_line_key:'L1',row_no:1,packing_group_id:'G1'},
  {stable_line_key:'L2',row_no:2,packing_group_id:'G1'},
  {stable_line_key:'L3',row_no:3},{stable_line_key:'L4',row_no:4}],
  packing_groups:[{group_id:'G1',member_keys:['L1','L2','LX'],status:'confirmed'}]};
state.packingGroupSelections=new Set();
w.toggleMaterialSelection('L1',true);
const group=w.materialSelectionContext();
w.toggleMaterialSelection('L2',false);
const cleared=[...state.packingGroupSelections];
state.packingGroupSelections=new Set(['L3','L4']);
const merge=w.materialSelectionContext();
state.packingGroupSelections=new Set(['L1','L2','LX','L3']);
const mixed=w.materialSelectionContext();
console.log(JSON.stringify({group:{selected:[...group.selectedKeys],cross:group.crossPageCount,
  edit:group.actions.edit.enabled,unmerge:group.actions.unmerge.enabled,merge:group.actions.merge.enabled},
  cleared,merge:merge.actions.merge,mixed:{edit:mixed.actions.edit,unmerge:mixed.actions.unmerge,
  remove:mixed.actions.remove}}));
""")
    assert result['group']['selected'] == ['L1']
    assert result['group']['cross'] == 0
    assert result['group']['edit'] is False and result['group']['unmerge'] is False
    assert result['group']['merge'] is False
    assert result['cleared'] == ['L1']
    assert result['merge']['enabled'] is True
    assert result['mixed']['edit']['enabled'] is False
    assert result['mixed']['unmerge']['enabled'] is False
    assert result['mixed']['remove']['enabled'] is True


def test_material_page_checkbox_is_tristate_and_toolbar_is_always_rendered():
    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={readOnly:false};const state=w.ensureMaterialFeeState();
w.escape=v=>String(v??'');
state.materials={packing_group_editable:true,items:[
  {stable_line_key:'L1',row_no:1},{stable_line_key:'L2',row_no:2}],packing_groups:[]};
state.packingGroupSelections=new Set(['L1']);
const partial=w.materialPageSelectionState();
const toolbar=w.renderMaterialSelectionToolbar();
w.toggleMaterialPageSelection(true);
const all=w.materialPageSelectionState();
w.toggleMaterialPageSelection(false);
const none=w.materialPageSelectionState();
console.log(JSON.stringify({partial,all,none,toolbar}));
""")
    assert result['partial']['indeterminate'] is True and result['partial']['checked'] is False
    assert result['all']['checked'] is True and result['all']['indeterminate'] is False
    assert result['none']['checked'] is False and result['none']['indeterminate'] is False
    for label in ['已选 1 行', '新增物料', '合并装箱组', '编辑装箱组', '解除合并', '删除所选', '清除选择']:
        assert label in result['toolbar']


def test_toolbar_carries_a_labelled_select_all_page_action():
    """表头那个复选框是唯一入口且没有文字，用户找不到；工具栏上要有同名动作。"""
    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={readOnly:false};const state=w.ensureMaterialFeeState();
w.escape=v=>String(v??'');
state.materials={packing_group_editable:true,items:[
  {stable_line_key:'L1',row_no:1},{stable_line_key:'L2',row_no:2}],packing_groups:[]};
state.packingGroupSelections=new Set();
const none=w.materialSelectionContext().actions.selectPage;
const noneToolbar=w.renderMaterialSelectionToolbar();
w.toggleMaterialPageSelection(true);
const all=w.materialSelectionContext().actions.selectPage;
const allToolbar=w.renderMaterialSelectionToolbar();
console.log(JSON.stringify({none,noneToolbar,all,allToolbar,selected:w.materialPageSelectionState().selected}));
""")
    assert result['none']['label'] == '全选本页' and result['none']['enabled'] is True
    assert result['none']['reason'] == ''
    assert 'data-action="mf-select-page"' in result['noneToolbar']
    assert result['all']['label'] == '取消全选本页'
    assert result['selected'] == 2
    assert '已选 2 行' in result['allToolbar'] and '取消全选本页' in result['allToolbar']


def test_select_all_page_action_stays_clickable_and_selects_identity_rows_when_readonly():
    """用户要求「全选本页」一直可点：只读批次不再禁用，有身份的行都能勾选查看；
    只有空页（没有任何可勾选行）才没有可选项。批量写入动作仍被各自的
    editable 门槛把守——选择放宽不弱化写保护。"""

    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={readOnly:false};const state=w.ensureMaterialFeeState();
w.escape=v=>String(v??'');
state.materials={packing_group_editable:true,items:[],packing_groups:[]};
state.packingGroupSelections=new Set();
const emptyPage=w.materialSelectionContext().actions.selectPage;
const emptyToolbar=w.renderMaterialSelectionToolbar();
w.detailState={readOnly:true};
state.materials={packing_group_editable:false,reference_editable:false,items:[{stable_line_key:'L1',row_no:1}],packing_groups:[]};
const readonly=w.materialSelectionContext().actions.selectPage;
w.toggleMaterialPageSelection(true);
const readonlyToggled=w.materialSelectionContext().actions.selectPage;
const selectedCount=w.materialPageSelectionState().selected;
const readonlyToolbar=w.renderMaterialSelectionToolbar();
const writeActions=w.materialSelectionContext().actions;
console.log(JSON.stringify({emptyPage,emptyToolbar,readonly,readonlyToggled,selectedCount,readonlyToolbar,
  project:writeActions.project,supplier:writeActions.supplier,remove:writeActions.remove}));
""")
    assert result['emptyPage']['enabled'] is False
    assert result['emptyPage']['reason'] == '当前页没有可选择的物料行'
    assert 'disabled' in result['emptyToolbar']
    assert result['readonly']['enabled'] is True
    assert result['readonly']['label'] == '全选本页'
    assert result['readonlyToggled']['label'] == '取消全选本页'
    assert result['selectedCount'] == 1
    assert '已选 1 行' in result['readonlyToolbar']
    # 选择放宽了，批量写入门槛没有：只读批次里批量动作依旧禁用并给出只读原因。
    # 两颗批量「设置」按钮走的是参考字段那一档，所以原因是那一档的（历史 / 归档版本不可维护）；
    # 已确认 / 已锁定的当前版本会在这一档上放行，见 test_confirmed_batch_still_maintains_...。
    assert result['project']['enabled'] is False
    assert result['project']['reason'] == '历史或归档版本不可维护供应商与项目归属'
    assert result['supplier']['enabled'] is False
    assert result['remove']['enabled'] is False


def test_bulk_field_actions_follow_the_selection_identity_and_structure_actions_need_line_keys():
    """「全选本页」勾的是行身份（stable_line_key 或 name），批量设置要跟着同一个身份放宽：
    缺键行（OA 骨架行）能批量写项目归属/供应商（服务端按行 name 定位），但合并/解除合并/
    软排除按 stable_line_key 匹配行，缺键行仍要挡住并说明原因，而不是整条工具栏一起变灰。"""

    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={readOnly:false};const state=w.ensureMaterialFeeState();
w.escape=v=>String(v??'');
state.materials={packing_group_editable:true,items:[
  {name:'I1',row_no:1},{name:'I2',row_no:2},
  {name:'I3',stable_line_key:'L3',row_no:3},{name:'I4',stable_line_key:'L4',row_no:4}],packing_groups:[]};
state.packingGroupSelections=new Set();
w.toggleMaterialPageSelection(true);
const keyless=w.materialSelectionContext();
const keylessToolbar=w.renderMaterialSelectionToolbar();
state.packingGroupSelections=new Set(['L3','L4']);
const keyed=w.materialSelectionContext();
const keyedToolbar=w.renderMaterialSelectionToolbar();
console.log(JSON.stringify({keyless:{selected:keyless.selectedCount,project:keyless.actions.project,
  supplier:keyless.actions.supplier,merge:keyless.actions.merge,remove:keyless.actions.remove},
  keylessToolbar,keyed:{project:keyed.actions.project,remove:keyed.actions.remove},keyedToolbar}));
""")
    assert result['keyless']['selected'] == 4
    assert result['keyless']['project']['enabled'] is True
    assert result['keyless']['supplier']['enabled'] is True
    assert result['keyless']['merge']['enabled'] is False
    assert result['keyless']['remove']['enabled'] is False
    assert 'stable_line_key' in result['keyless']['remove']['reason']

    def button(action, html):
        head, tail = html.split(f'data-action="{action}"', 1)
        return head.rsplit('<button', 1)[1] + tail.split('>', 1)[0]

    # 用户看到的就是 disabled 属性：缺键批次里批量设置可点、删除所选禁用。
    assert 'disabled' not in button('mf-set-project', result['keylessToolbar'])
    assert 'disabled' not in button('mf-set-supplier', result['keylessToolbar'])
    assert 'disabled' in button('mf-exclude-selected', result['keylessToolbar'])
    # 全部带键时结构操作也放行（放宽的是分轨，不是取消门槛）。
    assert result['keyed']['project']['enabled'] is True
    assert result['keyed']['remove']['enabled'] is True
    assert 'disabled' not in button('mf-exclude-selected', result['keyedToolbar'])

    # 删除所选：可点时必须真的标红，而不是只挂一个没人渲染的 class。
    assert 'is-danger' in button('mf-exclude-selected', result['keyedToolbar'])
    css = MATERIAL_GRID_CSS.read_text(encoding='utf-8')
    danger = css.split('.ocw-mf-selection-toolbar button.is-danger:not(:disabled) {', 1)[1].split('}', 1)[0]
    assert '#c43232' in danger and '#fff5f4' in danger


def test_toolbar_disabled_buttons_look_disabled():
    """工具栏里的禁用按钮必须看得出来。

    0-foundation 的 `.ocw-outline-btn` 没有 `:disabled` 样式，禁用和可用渲染完全一样；
    工具栏里只有批量按钮带紫色禁用态。物料区整体只读时「新增物料／删除所选」其实都点不了，
    用户却只看到批量按钮变灰，就把它归因成「勾选不够」。这条守住通用禁用态的存在。
    """
    css = MATERIAL_GRID_CSS.read_text(encoding='utf-8')
    rule = css.split('.ocw-mf-selection-toolbar button:disabled {', 1)[1].split('}', 1)[0]
    assert 'not-allowed' in rule
    assert '#98a2b3' in rule
    # 批量按钮的紫色禁用态仍要保留（选择器更具体，不会被上面这条覆盖）。
    assert '.ocw-mf-selection-toolbar .ocw-mf-batch-btn:disabled {' in css
    # 只读提示的胶囊样式与工具栏同处一个文件，不能只渲染没有样式。
    lock = css.split('.ocw-mf-selection-lock {', 1)[1].split('}', 1)[0]
    assert '#fff7e6' in lock and '#8a5a00' in lock


def test_select_all_page_action_reuses_the_shared_page_toggle():
    """点击入口必须落在既有的 toggleMaterialPageSelection 上，且只覆盖当前页。"""
    source = MATERIAL_GRID_PART.read_text(encoding='utf-8')
    handler = source.split('mf-select-page', 1)[1].split('});', 1)[0]
    assert 'toggleMaterialPageSelection(!this.materialPageSelectionState().checked)' in handler
    assert 'renderMaterialFeeWorkspacePreservingPosition()' in handler
    assert 'materialPageSelectionState' in source and 'toggleMaterialPageSelection' in source
    assert source.count('state.packingGroupSelections.add(') == 1


def test_batch_actions_sit_in_a_right_aligned_purple_group():
    """全选本页 / 批量设置项目归属 / 批量设置供应商一按就成片影响物料行，
    要跟左侧的逐行操作分开：靠右单独成组、用紫色区分，并整体留在同一条工具栏里。"""
    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={readOnly:false};const state=w.ensureMaterialFeeState();
w.escape=v=>String(v??'');
state.materials={packing_group_editable:true,items:[{stable_line_key:'L1',row_no:1}],packing_groups:[]};
state.packingGroupSelections=new Set(['L1']);
console.log(JSON.stringify({html:w.renderMaterialSelectionToolbar()}));
""")
    html = result['html']
    batch = ['全选本页', '批量设置项目归属', '批量设置供应商']
    group = html.split('ocw-mf-toolbar-batch', 1)[1].split('</span>', 1)[0]
    assert group.count('ocw-mf-batch-btn') == 3
    for label in batch:
        assert label in group
    # 左侧不能再出现这三个动作，否则「分组」只是摆设
    head = html.split('ocw-mf-toolbar-batch', 1)[0]
    for label in batch:
        assert label not in head
    assert '已选 1 行' in head and '新增物料' in head

    css = MATERIAL_GRID_CSS.read_text(encoding='utf-8')
    group_rule = css.split('.ocw-mf-selection-toolbar .ocw-mf-toolbar-batch {', 1)[1].split('}', 1)[0]
    assert 'margin-left: auto' in group_rule
    purple = css.split('.ocw-mf-selection-toolbar .ocw-mf-batch-btn {', 1)[1].split('}', 1)[0]
    assert '#5b46b5' in purple and '#f6f2ff' in purple


def test_top_toolbar_uses_atomic_batch_endpoints_and_clears_only_after_success():
    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={batchName:'B',versionName:'V',expectedModified:'M'};
const state=w.ensureMaterialFeeState();state.materials={packing_group_editable:true,items:[
  {name:'I1',stable_line_key:'L1',packing_group_id:'G1'},
  {name:'I2',stable_line_key:'L2',packing_group_id:'G1'}],
  packing_groups:[{group_id:'G1',member_keys:['L1','L2'],status:'confirmed'}]};
state.fees={};state.preview={};state.packingGroupSelections=new Set(['L1','L2']);
const calls=[];w.ensureEditSession=async()=>true;w.updateMaterialFeeExpectedModified=()=>{};
w.loadMaterialFeeWorkspace=async()=>true;w.showError=()=>{};
frappe.confirm=(message,yes)=>yes();
w.call=async(method,args)=>{calls.push({method,args});
  if(method.endsWith('preview_material_packing_group_batch')) return {ok:true,preview_id:'P',revision:'R',affected_member_keys:['L1','L2']};
  return {ok:true,removed_group_ids:['G1'],excluded_count:2,batch_modified:'M2'};};
await w.removeSelectedPackingGroups();
const afterUnmerge=state.packingGroupSelections.size;
state.packingGroupSelections=new Set(['L1','L2']);
await w.excludeSelectedMaterials();
const afterDelete=state.packingGroupSelections.size;
state.packingGroupSelections=new Set(['L1','L2']);
w.call=async()=>({ok:false,message:'network failed'});
await w.excludeSelectedMaterials();
console.log(JSON.stringify({methods:calls.map(call=>call.method),afterUnmerge,afterDelete,
  afterFailure:[...state.packingGroupSelections]}));
""")
    assert result['methods'] == [
        'overseas_costing.api.materials.preview_material_packing_group_batch',
        'overseas_costing.api.materials.confirm_material_packing_group_batch',
        'overseas_costing.api.materials.exclude_material_items',
    ]
    assert result['afterUnmerge'] == 0 and result['afterDelete'] == 0
    assert result['afterFailure'] == ['L1', 'L2']


def test_nonblocking_purchase_and_manual_notes_are_folded():
    html=_frontend_result(FRONTEND_SETUP + """
h.escape=x=>String(x ?? '');
console.log(JSON.stringify(h.renderMaterialAIAutofillPreview({selections:new Set(),draft:{autofill_preview:{
items:[],notes:[{message:'采购待关联；发货货值 10560 已取得，不影响试算。'}]}}})));
""")
    assert '<details class="ocw-mf-ai-review-notes">' in html
    assert '10560' in html and '仍需补充 / 核对' not in html
