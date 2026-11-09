"""Exercise row review selection, request fencing and inline fee edits in Node."""
import json
import subprocess
from overseas_costing.tests.test_workbench_frontend_state import PARTS


def run_ui(body):
    sources = [PARTS / name for name in ("78-material-fee-workspace.js", "87-freight.js")]
    script = "const fs=require('fs');const assert=require('assert').strict;"
    script += "const source=" + "+".join(f"fs.readFileSync({json.dumps(str(path))},'utf8')" for path in sources) + ";"
    script += "const Harness=Function('return class {'+source+'}')();"
    script += r"""
const w=new Harness();w.escape=v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
// expectedModified 的真源是服务端 batch 的 modified（Frappe 定宽 DATETIME(6)）：
// 写令牌只前进不回退，比较就建立在这个格式上，所以夹具必须用真实有序的时间戳。
const REV_OLD='2026-09-24 09:00:00.000000';const REV_NEW='2026-09-24 13:00:00.000000';
w.detailState={batchName:'B',versionName:'V',tab:'documents',editToken:'token',expectedModified:REV_OLD};
const state=w.ensureMaterialFeeState();w.renderMaterialAIReviewDialog=()=>{};w.renderMaterialFeeWorkspacePreservingPosition=()=>{};
w.loadMaterialFeeWorkspace=async()=>true;w.ensureEditSession=async()=>true;
global.frappe={show_alert:()=>{}};
const catalog={policy:'ai-field-review-7',fingerprint:'fp',rows:[
 {row_id:'source',origin:'source',action:'update',values:{material_code:'NEW',gross_weight_kg:0},can_fill:true,can_update:true,can_add:false,can_replace:true,default_selected:true,default_update_selected:true,default_replace_selected:true},
 {row_id:'current',origin:'current',action:'retain',values:{material_code:'OLD'},can_fill:false,can_update:false,can_add:false,can_replace:true,default_selected:false},
 {row_id:'ambiguous',origin:'source',action:'review',label:'待核对',values:{material_code:'DUP'},can_fill:false,can_update:false,can_add:false,can_replace:true,blocked_reason:'匹配不唯一'}
],source_groups:[{group_id:'packing',source_id:'PACKING-LIST',source_label:'国际物流装箱清单.xlsx',priority:1,row_ids:['source'],has_conflicts:false}],fees:[{proposal_id:'fee',payload:{expense_category:'运费',amount:0,currency:'RMB'},can_apply:true,default_selected:true},{proposal_id:'quote',payload:{expense_category:'旧报价',amount:999},can_apply:false,default_selected:true,blocked_reason:'已采用实际费用'}],
stage_snapshots:[{stage:'payment',rows:[],processes:[]},{stage:'international_logistics',rows:[],processes:[]},{stage:'purchase',rows:[],processes:[]}],
fee_stage_snapshots:[{stage:'payment',fees:[],processes:[]},{stage:'international_logistics',fees:[],processes:[]},{stage:'purchase',fees:[],processes:[]}]};
const ready=()=>{state.aiFill=w.initializeMaterialAIDraft({status:'READY',run_id:'run',row_review:catalog});return state.aiFill};
const calls=[];
"""
    script += "(async()=>{" + body + "})().catch(e=>{console.error(e);process.exit(1)});"
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_selections_modes_and_blocked_fees_are_independent():
    run_ui(r"""
const fill=ready();const review=fill.rowSelection;
assert.equal(fill.draftVisible,false,'Row selections must never overlay the saved material grid');
assert.equal(review.mode,'update_selected');assert.deepEqual([...review.rows],['source']);assert.deepEqual([...review.fees],['fee']);
w.scheduleMaterialAIRowPreview=()=>{};
w.changeMaterialAIRowSelection('rows','ambiguous',true);assert(!review.rows.has('ambiguous'));
w.changeMaterialAIRowSelection('fees','quote',true);assert(!review.fees.has('quote'));
w.changeMaterialAIRowSelection('rows','all',false);assert.equal(review.rows.size,0);assert(review.fees.has('fee'));
w.changeMaterialAIRowSelection('mode','update_selected');w.changeMaterialAIRowSelection('rows','all',true);
assert.deepEqual([...review.rows],['source']);
w.changeMaterialAIRowSelection('mode','fill_missing');assert.deepEqual([...review.rows],['source']);
const html=w.renderMaterialAIReviewDialogContent();
for(const text of ['只补缺失','更新所选行','已采用实际费用'])assert(html.includes(text),text);
assert(!html.includes('替换整表'));
assert(!html.includes('data-mf-ai-proposal-select'));assert(!html.includes('data-mf-ai-edit'));
assert(!html.includes('data-mf-ai-row-select="source"'));assert(!html.includes('data-mf-ai-source-group='));
assert(html.includes('data-mf-ai-fee-select="quote" disabled'));
""")


def test_purchase_value_candidate_and_readonly_derived_price_are_visible():
    run_ui(r"""
const fill=ready();fill.row_review={...fill.row_review,field_candidates:[
 {candidate_id:'LOG-V',item_name:'I1',fieldname:'goods_value',suggested_value:'800',source_label:'国际物流审批',workflow_stage:'international_logistics',workflow_rank:1,evidence_kind:'approval_form',can_apply:true,default_selected:true,resolution_reason:'货值、数量和币种来自同一行',presentation_group_id:'LOG-800',presentation_representative_candidate_id:'LOG-V',presentation_equivalent_candidate_ids:['LOG-V']},
],stage_snapshots:[
 {stage:'payment',status:'UNAVAILABLE',rows:[],processes:[]},
 {stage:'international_logistics',status:'AVAILABLE',rows:[{item_name:'I1',material_code:'SKU',field_candidates:{goods_value:['LOG-V']}}],processes:[]},
 {stage:'purchase',status:'UNAVAILABLE',rows:[],processes:[]},
]};delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);
selection.previewKey=w.materialAIRowSelectionKey(fill);
selection.preview={id:'P',revision:'R',rows:[{material_code:'SKU',quantity:4,unit_price:0,shipment_value_rmb:'800',adopted_price:{value:'200.00',currency:'RMB',unit:'kg',source_type:'purchase_total_derived'}}]};
const html=w.renderMaterialAIReviewDialogContent();
assert(html.includes('采购货值 RMB'));
assert(html.includes('data-mf-ai-field-selected="LOG-800"'));
assert(html.includes('200.00'));
assert(html.includes('按同来源货值÷数量计算'));
""")


def test_field_choice_indicators_and_counts_follow_current_server_preview():
    run_ui(r"""
const fill=ready();const candidates=[['V','goods_value','7365'],['N','product_name','Name'],['U','shipped_uom','套']]
 .map(([id,field,value])=>({candidate_id:id,item_name:'I1',fieldname:field,suggested_value:value,
  workflow_stage:'international_logistics',can_apply:true,default_selected:true,presentation_group_id:`G-${id}`,
  presentation_representative_candidate_id:id,presentation_equivalent_candidate_ids:[id]}));
fill.row_review={...fill.row_review,field_candidates:candidates,stage_snapshots:[
 {stage:'international_logistics',status:'AVAILABLE',rows:[{item_name:'I1',material_code:'SKU',
   field_candidates:{goods_value:['V'],product_name:['N'],shipped_uom:['U']}}]},
]};delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);
let html=w.renderMaterialAIReviewDialogContent();
assert(!html.includes('✓'),'a locally selected field has not been accepted by the server');
assert(html.includes('已选，待预览'));
selection.previewKey=w.materialAIRowSelectionKey(fill);
selection.preview={id:'P',revision:'R',rows:[],can_apply:false,field_results:[
 {item_name:'I1',fieldname:'goods_value',candidate_id:'V',status:'review',code:'UNIT',message:'单位冲突 <source>'},
 {item_name:'I1',fieldname:'product_name',candidate_id:'N',status:'retain',code:'MANUAL',message:'已有人工名称'},
 {item_name:'I1',fieldname:'shipped_uom',candidate_id:'U',status:'apply'},
]};
html=w.renderMaterialAIReviewDialogContent();
assert.equal((html.match(/✓/g)||[]).length,1);
for(const text of ['将采用 1 项','保留原值','待核对 1 项','单位冲突 &lt;source>','已有人工名称',
  'data-mf-ai-field-key="I1:goods_value" data-mf-ai-candidate-id=""','data-action="mf-ai-apply" disabled'])assert(html.includes(text),text);
w.scheduleMaterialAIRowPreview=()=>{};
w.changeMaterialAIRowSelection('fields','I1:goods_value','');
assert(!selection.fields.has('I1:goods_value'));
html=w.renderMaterialAIReviewDialogContent();
assert(!html.includes('✓'));assert(!html.includes('单位冲突'));assert(html.includes('采用 7365'));
""")


def test_switching_fill_modes_does_not_restore_a_deselected_default_field():
    run_ui(r"""
const fill=ready();fill.row_review.field_candidates=[{
 candidate_id:'V',item_name:'I1',fieldname:'goods_value',suggested_value:'7365',can_apply:true,
 default_selected:true,presentation_group_id:'G-V',presentation_representative_candidate_id:'V',
 presentation_equivalent_candidate_ids:['V'],
}];delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);
w.scheduleMaterialAIRowPreview=()=>{};
assert.equal(selection.fields.get('I1:goods_value'),'V');
w.changeMaterialAIRowSelection('fields','I1:goods_value','');
for(const mode of ['fill_missing','add_selected','update_selected']){
 w.changeMaterialAIRowSelection('mode',mode);
 assert.equal(selection.fields.size,0,`mode ${mode} must not restore a rejected field`);
}
""")


def test_final_preview_names_rows_outside_the_shipment_scope_before_confirmation():
    run_ui(r"""
const fill=ready();const selection=fill.rowSelection;
fill.row_review.stage_snapshots[1].rows=[{item_name:'I2',material_code:'SK-X',product_name:'模具 <型号>',
 baseline_values:{material_code:'SK-X',product_name:'模具 <型号>'},baseline_only:true,field_candidates:{}}];
selection.previewKey=w.materialAIRowSelectionKey(fill);
selection.preview={id:'P',revision:'R',can_apply:true,rows:[],removed_count:0,scope_excluded_item_names:['I2']};
const html=w.renderMaterialAIReviewDialogContent();
const scope=html.slice(html.indexOf('data-mf-ai-scope-exclusions'),html.indexOf('data-mf-ai-review-evidence'));
for(const text of ['本票将排除 1 行','SK-X · 模具 &lt;型号>','不参与本票计算','已排除物料','排除 1'])assert(scope.includes(text),text);
assert(!scope.includes('移除 0'));assert(!scope.includes('删除'));
assert(html.indexOf('data-mf-ai-scope-exclusions') < html.indexOf('data-action="mf-ai-apply"'));
""")


def test_final_preview_separates_purchased_total_from_shipment_and_missing_packing():
    run_ui(r"""
const fill=ready();const selection=fill.rowSelection;
selection.previewKey=w.materialAIRowSelectionKey(fill);
selection.preview={id:'P',revision:'R',can_apply:true,rows:[{
 name:'I1',material_code:'SKU',product_name:'模具',spec_model:'TPU',recipient:'收件方 A',supplier:'供应商 B',
 project_collection:'项目 C',quantity:2,actual_shipped_qty:1,shipped_uom:'套',volume_m3:0,package_count:0,
 goods_value:'7000',purchase_value_evidence:{original_amount:'1000',currency:'USD',amount_rmb:'7000',quantity:'2',uom:'套',status:'apply'},
 adopted_price:{value:'3500.00',currency:'RMB',unit:'套',source_type:'purchase_total_derived'},shipment_value_rmb:'3500',
}]};
const html=w.renderMaterialAIReviewDialogContent();
const final=html.slice(html.indexOf('data-mf-ai-final-preview'),html.indexOf('data-mf-ai-review-evidence'));
for(const text of ['采购原币','原币金额','采购货值 RMB','采购单价','本次发货货值 RMB','规格型号','收件人','供应商','项目归属',
 'TPU','收件方 A','供应商 B','项目 C','>1000<','>USD<','7000','3500.00','按同来源货值÷数量计算'])assert(final.includes(text),text);
assert.equal((final.match(/>待补</g)||[]).length,2,'unset cartons and volume must not look like measured zeroes');
""")


def test_distinct_monetary_bases_with_the_same_total_are_distinguishable_options():
    run_ui(r"""
const fill=ready();const candidates=['A','B'].map((id,i)=>({candidate_id:id,item_name:'I1',fieldname:'goods_value',suggested_value:'7365',
 workflow_stage:'international_logistics',source_label:`资料 ${id}`,can_apply:true,default_selected:false,
 value_basis:{currency:'RMB',amount_rmb:'7365',quantity:String(i+1),uom:'套'},presentation_group_id:`G-${id}`,
 presentation_representative_candidate_id:id,presentation_equivalent_candidate_ids:[id]}));
fill.row_review={...fill.row_review,field_candidates:candidates,stage_snapshots:[
 {stage:'international_logistics',status:'AVAILABLE',rows:[{item_name:'I1',material_code:'SKU',field_candidates:{goods_value:['A','B']}}]},
]};delete fill.rowSelection;
const html=w.renderMaterialAIReviewDialogContent();
const options=[...html.matchAll(/<option value="[AB]"[^>]*>([^<]*)<\/option>/g)].map(m=>m[1]);
assert.equal(options.length,2);assert.notEqual(options[0],options[1]);
for(const [i,option] of options.entries()){assert(option.includes('7365'));assert(option.includes(`${i+1} 套`));}
""")


def test_review_uses_compact_top_summary_and_keeps_full_scope_reason_only_in_evidence():
    run_ui(r"""
const fill=ready();fill.row_review={...fill.row_review,
 material_scope_source:'international_logistics',material_scope_fallback:false,
 material_scope_reason:'主表由国际物流 <unsafe> 定行。'};
delete fill.rowSelection;w.ensureMaterialAIRowSelection(fill);
const html=w.renderMaterialAIReviewDialogContent();
assert(html.includes('data-mf-ai-material-scope'));
assert(html.includes('定行依据：国际物流/支付申请/采购支出'));
assert(html.includes('将更新已选字段，不清空其他内容'));
assert(html.includes('<summary>更多设置</summary>'));
assert(html.includes('<details class="ocw-mf-ai-review-advanced" data-mf-ai-review-evidence><summary>查看依据与其他记录</summary>'));
assert(!html.includes('data-mf-ai-review-evidence open'));
const settings=html.slice(html.indexOf('<summary>更多设置</summary>'),html.indexOf('data-mf-ai-material-scope'));
for(const text of ['更新所选行','只补缺失','data-mf-ai-row-mode'])assert(settings.includes(text),text);
assert.equal((html.match(/主表由国际物流 &lt;unsafe> 定行。/g)||[]).length,1);
assert(html.indexOf('主表由国际物流 &lt;unsafe> 定行。')>html.indexOf('查看依据与其他记录'));
for(const retired of ['主表物料范围','物料逐字段选择，费用单独选择','只更新所选候选对应的现有物料行'])assert(!html.includes(retired),retired);
""")


def test_equivalent_numeric_values_render_one_selected_group_and_submit_representative():
    run_ui(r"""
const fill=ready();fill.source_progress=[];fill.row_review={...fill.row_review,policy:'ai-field-review-7',field_candidates:[
 {candidate_id:'REP-5',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:5,source_label:'来源 A',workflow_stage:'international_logistics',can_apply:true,default_selected:true,presentation_group_id:'G-5',presentation_representative_candidate_id:'REP-5',presentation_equivalent_candidate_ids:['REP-5','EQ-50','EQ-500']},
 {candidate_id:'EQ-50',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:5.0,source_label:'来源 B',workflow_stage:'international_logistics',can_apply:true,default_selected:false,presentation_group_id:'G-5',presentation_representative_candidate_id:'REP-5'},
 {candidate_id:'EQ-500',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:'5.00',source_label:'来源 C',workflow_stage:'international_logistics',can_apply:true,default_selected:false,presentation_group_id:'G-5',presentation_representative_candidate_id:'REP-5'},
],stage_snapshots:[
 {stage:'payment',status:'PARTIAL',rows:[{row_id:'BASE',item_name:'I1',material_code:'SKU-1',field_candidates:{}}],processes:[{process_instance_id:'PAY',label:'支付流程',status:'PARTIAL',evidence:[]}]},
 {stage:'international_logistics',status:'AVAILABLE',rows:[
  {row_id:'L1',process_instance_id:'P1',item_name:'I1',material_code:'SKU-1',product_name:'物料 1',field_candidates:{gross_weight_kg:['REP-5']}},
  {row_id:'L2',process_instance_id:'P2',item_name:'I1',material_code:'SKU-1',product_name:'物料 1',field_candidates:{gross_weight_kg:['EQ-50','EQ-500']}},
 ],processes:[
  {process_instance_id:'P1',label:'国际物流 1',evidence:[{evidence_id:'E1',source_label:'来源 A',read_status:'COMPLETED'}]},
  {process_instance_id:'P2',label:'国际物流 2',evidence:[{evidence_id:'E2',source_label:'来源 B',read_status:'COMPLETED'},{evidence_id:'E3',source_label:'来源 C',read_status:'COMPLETED'}]},
 ]},
 {stage:'purchase',status:'UNAVAILABLE',rows:[],processes:[]},
]};delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);
assert.deepEqual([...selection.fields.entries()],[['I1:gross_weight_kg','REP-5']]);
let html=w.renderMaterialAIReviewDialogContent();
const logistics=html.slice(html.indexOf('data-mf-ai-packing-stage="international_logistics"'),html.indexOf('data-mf-ai-packing-stage="purchase"'));
assert.equal((logistics.match(/data-mf-ai-material-row=/g)||[]).length,1,'same material must render once across processes');
assert(logistics.includes('data-mf-ai-field-selected="G-5"'));assert(logistics.includes('>5<'));
assert(!logistics.includes('<select'));assert(!logistics.includes('审批正文'));
const evidence=html.slice(html.indexOf('查看依据与其他记录'));
for(const text of ['来源 A','来源 B','来源 C','REP-5','EQ-50','EQ-500'])assert(evidence.includes(text),text);
w.call=async(method,args)=>{calls.push({method,args});return {ok:true,preview:{id:'P',revision:'R',can_apply:true,rows:[]}}};
await w.previewMaterialAIRowSelection();
assert.deepEqual(JSON.parse(calls[0].args.field_choices_json),{ 'I1:gross_weight_kg':'REP-5' });
assert.equal(calls[0].args.row_ids_json,'[]');
""")


def test_equivalent_candidate_ids_normalize_to_safe_server_representative_on_change_and_restore():
    run_ui(r"""
const fill=ready();fill.row_review={...fill.row_review,policy:'ai-field-review-7',field_candidates:[
 {candidate_id:'REP',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:'5',can_apply:true,default_selected:false,presentation_group_id:'G-5',presentation_representative_candidate_id:'REP',presentation_equivalent_candidate_ids:['REP','EQ']},
 {candidate_id:'EQ',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:'5.00',can_apply:true,default_selected:false,presentation_group_id:'G-5',presentation_representative_candidate_id:'REP'},
 {candidate_id:'CROSS-REP',item_name:'I2',fieldname:'gross_weight_kg',suggested_value:'7',can_apply:true,default_selected:false,presentation_group_id:'G-CROSS',presentation_representative_candidate_id:'CROSS-REP',presentation_equivalent_candidate_ids:['CROSS-REP','CROSS-EQ']},
 {candidate_id:'CROSS-EQ',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:'7',can_apply:true,default_selected:false,presentation_group_id:'G-CROSS',presentation_representative_candidate_id:'CROSS-REP'},
 {candidate_id:'RO-REP',item_name:'I1',fieldname:'volume_m3',suggested_value:'1',can_apply:false,default_selected:false,presentation_group_id:'G-RO',presentation_representative_candidate_id:'RO-REP',presentation_equivalent_candidate_ids:['RO-REP','RO-EQ']},
 {candidate_id:'RO-EQ',item_name:'I1',fieldname:'volume_m3',suggested_value:'1.0',can_apply:true,default_selected:false,presentation_group_id:'G-RO',presentation_representative_candidate_id:'RO-REP'},
]};delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);w.scheduleMaterialAIRowPreview=()=>{};
assert.equal(typeof w.materialAIFieldRepresentativeCandidateId,'function');
const candidates=fill.row_review.field_candidates;
assert.equal(typeof w.materialAIFieldCandidateIndex,'function');
const candidateIndex=w.materialAIFieldCandidateIndex(candidates);
assert.equal(w.materialAIFieldRepresentativeCandidateId(candidateIndex,'I1:gross_weight_kg','EQ'),'REP');
assert.equal(w.materialAIFieldRepresentativeCandidateId(candidateIndex,'I1:gross_weight_kg','CROSS-EQ'),'');
assert.equal(w.materialAIFieldRepresentativeCandidateId(candidateIndex,'I1:volume_m3','RO-EQ'),'');
w.changeMaterialAIRowSelection('fields','I1:gross_weight_kg','EQ');
assert.equal(selection.fields.get('I1:gross_weight_kg'),'REP');
const equivalent=candidates.find(candidate=>candidate.candidate_id==='EQ');equivalent.default_selected=true;
w.changeMaterialAIRowSelection('mode','add_selected');assert.equal(selection.fields.size,0);
w.changeMaterialAIRowSelection('mode','update_selected');assert.equal(selection.fields.get('I1:gross_weight_kg'),'REP');
delete fill.rowSelection;assert.equal(w.ensureMaterialAIRowSelection(fill).fields.get('I1:gross_weight_kg'),'REP');
""")


def test_existing_equivalent_selection_is_repaired_and_preview_boundary_submits_representative():
    run_ui(r"""
const fill=ready();fill.row_review={...fill.row_review,policy:'ai-field-review-7',field_candidates:[
 {candidate_id:'REP',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:'5',can_apply:true,default_selected:false,presentation_group_id:'G-5',presentation_representative_candidate_id:'REP',presentation_equivalent_candidate_ids:['REP','EQ']},
 {candidate_id:'EQ',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:'5.00',can_apply:true,default_selected:false,presentation_group_id:'G-5',presentation_representative_candidate_id:'REP'},
]};delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);
selection.fields.set('I1:gross_weight_kg','EQ');
assert.equal(w.ensureMaterialAIRowSelection(fill).fields.get('I1:gross_weight_kg'),'REP');
selection.fields.set('I1:gross_weight_kg','EQ');
w.ensureMaterialAIRowSelection=()=>selection;
w.call=async(method,args)=>{calls.push({method,args});return {ok:true,preview:{id:'P',revision:'R',can_apply:true,rows:[]}}};
await w.previewMaterialAIRowSelection();
assert.deepEqual(JSON.parse(calls[0].args.field_choices_json),{'I1:gross_weight_kg':'REP'});
""")


def test_field_candidate_indexing_is_linear_for_initial_defaults_and_steady_repairs():
    run_ui(r"""
let candidateIdReads=0;const size=200;
const candidates=Array.from({length:size},(_,index)=>{
 const candidateId=`C-${index}`;
 const candidate={item_name:`I-${index}`,fieldname:'gross_weight_kg',suggested_value:String(index),can_apply:true,default_selected:true,
  presentation_group_id:`G-${index}`,presentation_representative_candidate_id:candidateId,presentation_equivalent_candidate_ids:[candidateId]};
 Object.defineProperty(candidate,'candidate_id',{enumerable:true,get(){candidateIdReads+=1;return candidateId;}});
 return candidate;
});
const fill=ready();fill.row_review={...fill.row_review,policy:'ai-field-review-7',field_candidates:candidates};delete fill.rowSelection;
candidateIdReads=0;const first=w.ensureMaterialAIRowSelection(fill);const firstReads=candidateIdReads;
assert.equal(first.fields.size,size);assert(firstReads<=size*5,`initial candidate_id reads must stay linear: ${firstReads}`);
candidateIdReads=0;const steady=w.ensureMaterialAIRowSelection(fill);const steadyReads=candidateIdReads;
assert.equal(steady,first);assert.equal(steady.fields.size,size);
assert(steadyReads<=size*3,`steady candidate_id reads must stay linear without recomputing defaults: ${steadyReads}`);
""")


def test_equivalent_non_applicable_values_render_once_as_one_readonly_server_group():
    run_ui(r"""
const fill=ready();fill.row_review={...fill.row_review,policy:'ai-field-review-7',field_candidates:[
 {candidate_id:'RO-REP',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:5,source_label:'来源 A',workflow_stage:'international_logistics',can_apply:false,default_selected:false,presentation_group_id:'RO-G-5',presentation_representative_candidate_id:'RO-REP',presentation_equivalent_candidate_ids:['RO-REP','RO-50','RO-500']},
 {candidate_id:'RO-50',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:5.0,source_label:'来源 B',workflow_stage:'international_logistics',can_apply:false,default_selected:false,presentation_group_id:'RO-G-5',presentation_representative_candidate_id:'RO-REP'},
 {candidate_id:'RO-500',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:'5.00',source_label:'来源 C',workflow_stage:'international_logistics',can_apply:false,default_selected:false,presentation_group_id:'RO-G-5',presentation_representative_candidate_id:'RO-REP'},
],stage_snapshots:[
 {stage:'payment',status:'UNAVAILABLE',rows:[],processes:[]},
 {stage:'international_logistics',status:'PARTIAL',rows:[{item_name:'I1',material_code:'SKU-1',field_candidates:{gross_weight_kg:['RO-REP','RO-50','RO-500']}}],processes:[]},
 {stage:'purchase',status:'UNAVAILABLE',rows:[],processes:[]},
]};delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);
const candidates=fill.row_review.field_candidates;
const groups=w.materialAIPresentationGroups(candidates,new Map(candidates.map(candidate=>[candidate.candidate_id,candidate])));
assert.equal(groups.length,1);assert.equal(groups[0].candidates.length,3);assert.equal(groups[0].canApply,false);
assert.equal(selection.fields.size,0);
const html=w.renderMaterialAIReviewDialogContent();
const logistics=html.slice(html.indexOf('data-mf-ai-packing-stage="international_logistics"'),html.indexOf('data-mf-ai-packing-stage="purchase"'));
assert.equal((logistics.match(/data-mf-ai-presentation-group="RO-G-5"/g)||[]).length,1);
assert(logistics.includes('>5<b>只读</b>'));assert(!logistics.includes('5.00'));
assert(!logistics.includes('<select'));assert(!logistics.includes('data-action="mf-ai-adopt-field"'));
""")


def test_current_policy_groups_distinct_values_by_server_metadata_and_changes_one_field_only():
    run_ui(r"""
const fill=ready();fill.row_review={...fill.row_review,policy:'ai-field-review-7',field_candidates:[
 {candidate_id:'PAY-W',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:'9',workflow_stage:'payment',can_apply:true,default_selected:true,presentation_group_id:'PAY-9',presentation_representative_candidate_id:'PAY-W',presentation_equivalent_candidate_ids:['PAY-W']},
 {candidate_id:'LOG-W',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:'8',workflow_stage:'international_logistics',can_apply:true,default_selected:false,evidence_kind:'approval_form',presentation_group_id:'LOG-8',presentation_representative_candidate_id:'LOG-W',presentation_equivalent_candidate_ids:['LOG-W']},
 {candidate_id:'LOG-W-ALT',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:'7',workflow_stage:'international_logistics',can_apply:true,default_selected:false,evidence_kind:'dedicated_attachment',presentation_group_id:'LOG-7',presentation_representative_candidate_id:'LOG-W-ALT',presentation_equivalent_candidate_ids:['LOG-W-ALT']},
 {candidate_id:'LOG-V',item_name:'I1',fieldname:'volume_m3',suggested_value:'2',workflow_stage:'international_logistics',can_apply:true,default_selected:true,presentation_group_id:'LOG-2',presentation_representative_candidate_id:'LOG-V',presentation_equivalent_candidate_ids:['LOG-V']},
 {candidate_id:'LOG-N',item_name:'I1',fieldname:'net_weight_kg',suggested_value:'6',workflow_stage:'international_logistics',can_apply:true,default_selected:false,presentation_group_id:'LOG-6',presentation_representative_candidate_id:'LOG-N',presentation_equivalent_candidate_ids:['LOG-N','LOG-N-RO']},
 {candidate_id:'LOG-N-RO',item_name:'I1',fieldname:'net_weight_kg',suggested_value:'6.0',workflow_stage:'international_logistics',can_apply:false,default_selected:false,presentation_group_id:'LOG-6',presentation_representative_candidate_id:'LOG-N'},
 {candidate_id:'PUR-Q',item_name:'I2',fieldname:'actual_shipped_qty',suggested_value:'3',workflow_stage:'purchase',can_apply:true,default_selected:true,presentation_group_id:'PUR-3',presentation_representative_candidate_id:'PUR-Q',presentation_equivalent_candidate_ids:['PUR-Q']},
]};
fill.row_review.stage_snapshots=[
 {stage_snapshot_id:'SP',stage:'payment',stage_rank:0,stage_label:'支付申请',status:'AVAILABLE',fallback_reason:'',warnings:[],
  processes:[{process_instance_id:'PP',source_open_ref:'PP',can_open:true,label:'月结付款 2026-07',approval_no:'PAY-1',status:'AVAILABLE',evidence:[{evidence_id:'EF',evidence_kind:'approval_form',source_label:'月结正文',read_status:'COMPLETED'}]}],
  rows:[{row_id:'SRP',process_instance_id:'PP',item_name:'I1',material_code:'SKU-1',product_name:'物料1',field_candidates:{gross_weight_kg:['PAY-W']}}]},
 {stage_snapshot_id:'SL',stage:'international_logistics',stage_rank:1,stage_label:'国际物流',status:'PARTIAL',fallback_reason:'部分资料不可读，已跳过并继续使用本阶段可用候选。',warnings:[],
  processes:[{process_instance_id:'PL',source_open_ref:'PL',can_open:true,label:'国际物流审批',approval_no:'LOG-1',status:'PARTIAL',evidence:[{evidence_id:'EA',evidence_kind:'attachment',source_label:'装箱单.xlsx',read_status:'SKIPPED',skip_reason_text:'文件已损坏'}]}],
  rows:[{row_id:'SRL',process_instance_id:'PL',item_name:'I1',material_code:'SKU-1',product_name:'物料1',field_candidates:{gross_weight_kg:['LOG-W','LOG-W-ALT'],volume_m3:['LOG-V'],net_weight_kg:['LOG-N','LOG-N-RO']}}]},
 {stage_snapshot_id:'SU',stage:'purchase',stage_rank:2,stage_label:'采购支出',status:'AVAILABLE',fallback_reason:'',warnings:[],
  processes:[{process_instance_id:'PU1',source_open_ref:'PU1',can_open:true,label:'采购支出 1',approval_no:'PUR-1',status:'AVAILABLE',evidence:[]},{process_instance_id:'PU2',source_open_ref:'PU2',can_open:true,label:'采购支出 2',approval_no:'PUR-2',status:'AVAILABLE',evidence:[]}],
  rows:[{row_id:'SRU',process_instance_id:'PU2',item_name:'I2',material_code:'SKU-2',product_name:'物料2',field_candidates:{actual_shipped_qty:['PUR-Q']}}]},
];
delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);w.scheduleMaterialAIRowPreview=()=>{};
let html=w.renderMaterialAIReviewDialogContent();
assert.equal((html.match(/data-mf-ai-packing-stage=/g)||[]).length,3);
for(const stage of ['payment','international_logistics','purchase'])assert(html.includes(`data-mf-ai-packing-stage="${stage}"`));
assert.equal((html.match(/class="ocw-mf-ai-stage-matrix"/g)||[]).length,3);
assert(html.includes('月结付款 2026-07'));assert(html.includes('采购支出 1'));assert(html.includes('采购支出 2'));
assert.equal((html.match(/data-mf-ai-source-open-ref=/g)||[]).length,4);
for(const label of ['打开支付申请原单','打开国际物流原单','打开采购支出原单'])assert(html.includes(label),label);
assert.equal((html.match(/data-mf-ai-source-open-ref="PU1"/g)||[]).length,1);
assert.equal((html.match(/data-mf-ai-source-open-ref="PU2"/g)||[]).length,1);
const logistics=html.slice(html.indexOf('data-mf-ai-packing-stage="international_logistics"'),html.indexOf('data-mf-ai-packing-stage="purchase"'));
assert.equal((logistics.match(/<select data-mf-ai-field-select="I1:gross_weight_kg"/g)||[]).length,1);
for(const option of ['value="LOG-W"','value="LOG-W-ALT"','>8</option>','>7</option>'])assert(logistics.includes(option),option);
assert(logistics.includes('2 个值'));assert(!logistics.includes('审批正文'));assert(!logistics.includes('专用附件'));
const conflictStart=logistics.indexOf('<select data-mf-ai-field-select="I1:gross_weight_kg"');
const conflictCell=logistics.slice(conflictStart,logistics.indexOf('</td>',conflictStart));
assert(conflictCell.includes('data-action="mf-ai-show-evidence"'));assert(conflictCell.includes('查看依据'));
assert(logistics.includes('data-action="mf-ai-adopt-field"'));assert(logistics.includes('data-mf-ai-candidate-id="LOG-N"'));
assert(!selection.fields.has('I1:net_weight_kg'));assert(![...selection.fields.values()].includes('LOG-N-RO'));
for(const retired of ['所属流程','优先级','已采用此阶段','未采用此阶段','支付默认','低阶段可改选'])assert(!html.includes(retired),retired);
const before=[...selection.fields.entries()].sort();
w.changeMaterialAIRowSelection('fields','I1:gross_weight_kg','LOG-W');
w.changeMaterialAIRowSelection('fields','I1:net_weight_kg','LOG-N');
assert.deepEqual([...selection.fields.entries()].sort(),[['I1:gross_weight_kg','LOG-W'],['I1:net_weight_kg','LOG-N'],['I1:volume_m3','LOG-V'],['I2:actual_shipped_qty','PUR-Q']]);
assert.notDeepEqual([...selection.fields.entries()].sort(),before);
""")


def test_opening_stage_source_uses_read_only_resolver_and_keeps_preview_selection_on_failure():
    run_ui(r"""
const fill=ready();fill.runId='RUN-1';
const selection=w.ensureMaterialAIRowSelection(fill);selection.fields.set('I1:gross_weight_kg','CANDIDATE');
let opened=null;let alertMessage='';
w.openSettlementSource=source=>{opened=source};
frappe.show_alert=payload=>{alertMessage=payload.message};
w.call=async(method,args)=>{calls.push({method,args});return {ok:true,open_url:'dingtalk://dingtalkclient/page/link?safe=1',open_mode:'desktop_protocol',label:'月结付款'};};
await w.openMaterialAISourceProcess('proc_safe');
assert(calls[0].method.endsWith('get_source_ai_process_open_target'));
assert.deepEqual(calls[0].args,{batch_name:'B',run_id:'RUN-1',source_open_ref:'proc_safe'});
assert.equal(opened.label,'月结付款');
assert.equal(selection.fields.get('I1:gross_weight_kg'),'CANDIDATE');
w.call=async()=>({ok:false,message:'server detail'});opened=null;
await w.openMaterialAISourceProcess('proc_stale');
assert.equal(opened,null);
assert.equal(alertMessage,'原单链接已失效，请重新读取资料源');
assert.equal(selection.fields.get('I1:gross_weight_kg'),'CANDIDATE');
""")


def test_show_evidence_opens_scrolls_then_moves_keyboard_focus_to_summary():
    run_ui(r"""
const events=[];
const summary={focus:options=>events.push(`focus:${options?.preventScroll===true}`)};
const evidenceNode={
 scrollIntoView:options=>events.push(`scroll:${options.behavior}:${options.block}`),
 querySelector:selector=>{assert.equal(selector,'summary');return summary;},
};
const evidence={
 length:1,
 prop:(name,value)=>{events.push(`${name}:${value}`);return evidence;},
 get:index=>{assert.equal(index,0);return evidenceNode;},
};
const dialog={$wrapper:{find:selector=>{assert.equal(selector,'[data-mf-ai-review-evidence]');return {first:()=>evidence};}}};
assert.equal(typeof w.openMaterialAIReviewEvidence,'function');
w.openMaterialAIReviewEvidence(dialog);
assert.deepEqual(events,['open:true','scroll:smooth:start','focus:true']);
""")


def test_stage_warning_is_compact_and_full_safe_evidence_stays_in_bottom_details():
    run_ui(r"""
const fill=ready();fill.row_review={...fill.row_review,policy:'ai-field-review-7',field_candidates:[
 {candidate_id:'LOG-W',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:'8',workflow_stage:'international_logistics',can_apply:true,default_selected:true,presentation_group_id:'LOG-8',presentation_representative_candidate_id:'LOG-W',presentation_equivalent_candidate_ids:['LOG-W']},
],stage_snapshots:[
 {stage:'payment',stage_rank:0,stage_label:'支付申请',status:'UNAVAILABLE',rows:[],processes:[],warnings:[],fallback_reason:'本阶段未找到有效资料，默认值将从下一优先级阶段补充。'},
 {stage:'international_logistics',stage_rank:1,stage_label:'国际物流',status:'PARTIAL',fallback_reason:'部分资料不可读。',warnings:['<html><h1>Internal Server Error</h1></html>'],rows:[{row_id:'R',process_instance_id:'P',item_name:'I1',material_code:'SKU-1',field_candidates:{gross_weight_kg:['LOG-W']}}],processes:[{process_instance_id:'P',label:'国际物流',status:'PARTIAL',evidence:[{evidence_id:'BAD',evidence_kind:'attachment',source_label:'bad.pdf',read_status:'UNREADABLE',skip_reason_text:'文件格式不支持'},{evidence_id:'HTML',evidence_kind:'attachment',source_label:'server.pdf',read_status:'SKIPPED',skip_reason_text:'<html><h1>Internal Server Error</h1></html>'}]}]},
 {stage:'purchase',stage_rank:2,stage_label:'采购支出',status:'UNAVAILABLE',rows:[],processes:[],warnings:[],fallback_reason:'本阶段未找到有效资料。'},
],fee_stage_snapshots:[]};
delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);
selection.preview={id:'P',revision:'R',can_apply:true,rows:[]};selection.previewKey=w.materialAIRowSelectionKey(fill);
const html=w.renderMaterialAIReviewDialogContent();
assert(html.includes('无可用资料'));assert(html.includes('部分可用'));
const logistics=html.slice(html.indexOf('data-mf-ai-packing-stage="international_logistics"'),html.indexOf('data-mf-ai-packing-stage="purchase"'));
assert(logistics.includes('1 份资料未读取'));assert(logistics.includes('需核对'));assert(logistics.includes('data-action="mf-ai-show-evidence"'));
assert(!logistics.includes('文件格式不支持'));assert(!logistics.includes('已跳过，继续读取下一资料'));
const evidence=html.slice(html.indexOf('查看依据与其他记录'));
assert(evidence.includes('文件格式不支持'));assert(evidence.includes('已跳过，继续读取下一资料'));
assert(!html.includes('Internal Server Error'));assert(!html.includes('&lt;html'));assert(w.canConfirmMaterialAIRowSelection(fill));
const apply=html.match(/data-action="mf-ai-apply"([^>]*)>/);assert(apply&&!apply[1].includes('disabled'));
""")


def test_process_conflict_warning_requires_review_without_claiming_a_read_failure():
    run_ui(r"""
const fill=ready();fill.row_review={...fill.row_review,policy:'ai-field-review-7',field_candidates:[
 {candidate_id:'LOG-W',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:'8',workflow_stage:'international_logistics',can_apply:true,default_selected:true,presentation_group_id:'LOG-8',presentation_representative_candidate_id:'LOG-W',presentation_equivalent_candidate_ids:['LOG-W']},
],stage_snapshots:[
 {stage:'payment',stage_label:'支付申请',status:'UNAVAILABLE',rows:[],processes:[],warnings:[]},
 {stage:'international_logistics',stage_label:'国际物流',status:'PARTIAL',warnings:['两个流程的毛重不一致，请核对。'],rows:[{item_name:'I1',material_code:'SKU-1',field_candidates:{gross_weight_kg:['LOG-W']}}],processes:[{label:'国际物流',evidence:[{evidence_id:'OK',source_label:'packing.xlsx',read_status:'COMPLETED'}]}]},
 {stage:'purchase',stage_label:'采购支出',status:'UNAVAILABLE',rows:[],processes:[],warnings:[]},
],fee_stage_snapshots:[]};
delete fill.rowSelection;w.ensureMaterialAIRowSelection(fill);
const html=w.renderMaterialAIReviewDialogContent();
const logistics=html.slice(html.indexOf('data-mf-ai-packing-stage="international_logistics"'),html.indexOf('data-mf-ai-packing-stage="purchase"'));
assert(logistics.includes('需核对'));assert(logistics.includes('data-action="mf-ai-show-evidence"'));
assert(!logistics.includes('资料未读取'));
const evidence=html.slice(html.indexOf('查看依据与其他记录'));
assert(evidence.includes('两个流程的毛重不一致，请核对。'));
""")


def test_every_policy_without_any_required_stage_snapshot_is_stale_and_never_previews_or_confirms():
    run_ui(r"""
const packingSnapshots=[{stage:'payment',rows:[],processes:[]}];
const feeSnapshots=[{stage:'payment',fees:[],processes:[]}];
const policies=['ai-field-review-7','ai-row-review-4',''];
const missingCases=[
 {label:'packing',stage_snapshots:undefined,fee_stage_snapshots:feeSnapshots},
 {label:'fees',stage_snapshots:packingSnapshots,fee_stage_snapshots:undefined},
 {label:'both',stage_snapshots:undefined,fee_stage_snapshots:undefined},
];
for(const policy of policies)for(const missing of missingCases){
 const label=`${policy||'empty'}:${missing.label}`;
 const fill=w.initializeMaterialAIDraft({status:'READY',run_id:`run-${label}`,row_review:{...catalog,policy,
  stage_snapshots:missing.stage_snapshots,fee_stage_snapshots:missing.fee_stage_snapshots}});
 state.aiFill=fill;
 assert.equal(fill.rowSelection,undefined,`${label} initialization must not create hidden defaults`);
 let autoPreviews=0;const schedule=w.scheduleMaterialAIRowPreview;
 w.scheduleMaterialAIRowPreview=()=>{autoPreviews+=1};w.showMaterialAIReadyDraft();w.scheduleMaterialAIRowPreview=schedule;
 assert.equal(autoPreviews,0,`${label} must not schedule an automatic preview`);
 const selection={mode:'update_selected',rows:new Set(['source']),fields:new Map(),packingAssignments:new Map(),fees:new Set(['fee']),request:0,loading:false,preview:null,error:'',timer:null};
 fill.rowSelection=selection;
 selection.loading=false;selection.preview={id:'P',revision:'R',can_apply:true,rows:[]};selection.previewKey=w.materialAIRowSelectionKey(fill);
 assert.equal(w.canConfirmMaterialAIRowSelection(fill),false,label);
 const html=w.renderMaterialAIReviewDialogContent();
 assert(html.includes('资料处理规则已更新，请重新分析资料'),label);
 assert(!html.includes('data-mf-ai-source-group='),label);assert(!html.includes('优先级'),label);
 const apply=html.match(/data-action="mf-ai-apply"([^>]*)>/);assert(apply&&apply[1].includes('disabled'),label);
 calls.length=0;w.call=async(method,args)=>{calls.push({method,args});return {ok:true,preview:{id:'HIDDEN',revision:1,can_apply:true}}};
 await w.previewMaterialAIRowSelection();await w.confirmMaterialAIRowSelection();
 assert.deepEqual(calls,[],`${label} snapshot absence must not submit hidden defaults`);
}
""")


def test_packing_and_fee_stages_open_first_actual_actionable_stage_only():
    run_ui(r"""
const fill=ready();fill.row_review={...fill.row_review,policy:'ai-field-review-7',field_candidates:[
  {candidate_id:'LOG-W',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:'8',workflow_stage:'international_logistics',can_apply:true,default_selected:true,presentation_group_id:'LOG-8',presentation_representative_candidate_id:'LOG-W',presentation_equivalent_candidate_ids:['LOG-W']},
 ],fees:[
  {proposal_id:'PAY-RO',workflow_stage:'payment',selection_role:'alternative',payload:{expense_category:'旧报价',amount:'90',currency:'RMB'},can_apply:false,default_selected:false},
  {proposal_id:'LOG-FEE',workflow_stage:'international_logistics',selection_role:'approved_quote',payload:{expense_category:'国际运费',amount:'100',currency:'RMB'},can_apply:true,default_selected:true},
 ],
 stage_snapshots:[
  {stage:'payment',stage_rank:0,stage_label:'支付申请',status:'PARTIAL',rows:[{row_id:'BASE',item_name:'I1',material_code:'SKU-1',field_candidates:{}}],warnings:[],fallback_reason:'已继续使用下一优先级阶段。',processes:[{process_instance_id:'PAY',label:'月结付款',status:'PARTIAL',evidence:[]}]},
  {stage:'international_logistics',stage_rank:1,stage_label:'国际物流',status:'PARTIAL',rows:[{row_id:'LOG',item_name:'I1',material_code:'SKU-1',field_candidates:{gross_weight_kg:['LOG-W']}}],processes:[],warnings:[],fallback_reason:''},
  {stage:'purchase',stage_rank:2,stage_label:'采购支出',status:'UNAVAILABLE',rows:[],processes:[],warnings:[],fallback_reason:''},
 ],fee_stage_snapshots:[
  {stage:'payment',stage_rank:0,stage_label:'支付申请',status:'PARTIAL',fees:[{proposal_id:'PAY-RO'}],warnings:[],fallback_reason:'已继续使用下一优先级阶段。',processes:[{process_instance_id:'PAY',label:'月结付款',status:'PARTIAL',evidence:[]}]},
  {stage:'international_logistics',stage_rank:1,stage_label:'国际物流',status:'PARTIAL',fees:[{proposal_id:'LOG-FEE'}],processes:[],warnings:[],fallback_reason:''},
 ]};
delete fill.rowSelection;w.ensureMaterialAIRowSelection(fill);
assert.equal(w.materialAIFirstActionableStageIndex([{stage:'payment'},{stage:'international_logistics'}],()=>false),-1);
let html=w.renderMaterialAIReviewDialogContent();
let packingPayment=html.slice(html.indexOf('data-mf-ai-packing-stage="payment"'),html.indexOf('data-mf-ai-packing-stage="international_logistics"'));
let packingLogistics=html.slice(html.indexOf('data-mf-ai-packing-stage="international_logistics"'),html.indexOf('data-mf-ai-packing-stage="purchase"'));
let feePayment=html.slice(html.indexOf('data-mf-ai-fee-stage="payment"'),html.indexOf('data-mf-ai-fee-stage="international_logistics"'));
let feeLogistics=html.slice(html.indexOf('data-mf-ai-fee-stage="international_logistics"'));
assert(!packingPayment.slice(0,packingPayment.indexOf('<summary>')).includes(' open'));
assert(packingPayment.includes('无可用资料'));assert(packingLogistics.slice(0,packingLogistics.indexOf('<summary>')).includes(' open'));
assert(!feePayment.slice(0,feePayment.indexOf('<summary>')).includes(' open'));
assert(feePayment.includes('无可用资料'));assert(feeLogistics.slice(0,feeLogistics.indexOf('<summary>')).includes(' open'));
fill.row_review.field_candidates.push({candidate_id:'PAY-W',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:'9',workflow_stage:'payment',can_apply:true,default_selected:false,presentation_group_id:'PAY-9',presentation_representative_candidate_id:'PAY-W',presentation_equivalent_candidate_ids:['PAY-W']});
fill.row_review.stage_snapshots[0].rows[0].field_candidates={gross_weight_kg:['PAY-W']};
html=w.renderMaterialAIReviewDialogContent();packingPayment=html.slice(html.indexOf('data-mf-ai-packing-stage="payment"'),html.indexOf('data-mf-ai-packing-stage="international_logistics"'));
assert(packingPayment.slice(0,packingPayment.indexOf('<summary>')).includes(' open'));
fill.row_review.field_candidates.forEach(candidate=>{candidate.can_apply=false;candidate.default_selected=false;});
fill.row_review.fees.forEach(fee=>{fee.can_apply=false;fee.default_selected=false;});
delete fill.rowSelection;w.ensureMaterialAIRowSelection(fill);html=w.renderMaterialAIReviewDialogContent();
const stageTags=[...html.matchAll(/<details class="ocw-mf-ai-stage-panel[^"]*" data-mf-ai-(?:packing|fee)-stage="[^"]+"[^>]*>/g)].map(match=>match[0]);
assert.equal(stageTags.length,6);assert(stageTags.every(tag=>!tag.includes(' open')));
""")


def test_ready_with_warnings_uses_server_can_apply_for_confirm_and_safe_status_copy():
    run_ui(r"""
const fill=w.initializeMaterialAIDraft({status:'READY_WITH_WARNINGS',run_id:'run-warning',source_completeness:'PARTIAL',row_review:catalog});
state.aiFill=fill;
const selection=w.ensureMaterialAIRowSelection(fill);
selection.preview={id:'P',revision:'R',can_apply:true,rows:[]};
selection.previewKey=w.materialAIRowSelectionKey(fill);
assert(w.canConfirmMaterialAIRowSelection(fill));
assert(w.canApplyMaterialAIFill(fill));
let review=w.renderMaterialAIReviewDialogContent();
let apply=review.match(/data-action="mf-ai-apply"([^>]*)>/);
assert(apply&&!apply[1].includes('disabled'));
let html=w.renderMaterialAIProgressDialogContent();
assert(html.includes('草稿已生成（部分资料待核对）'));
selection.preview={id:'P2',revision:'R2',can_apply:false,rows:[]};
selection.previewKey=w.materialAIRowSelectionKey(fill);
assert.equal(w.canConfirmMaterialAIRowSelection(fill),false);
assert.equal(w.canApplyMaterialAIFill(fill),false);
review=w.renderMaterialAIReviewDialogContent();
apply=review.match(/data-action="mf-ai-apply"([^>]*)>/);
assert(apply&&apply[1].includes('disabled'));
""")


def test_ready_warning_copy_distinguishes_skipped_sources_from_no_candidates_and_legacy():
    run_ui(r"""
const skipped={status:'READY_WITH_WARNINGS',source_completeness:'UNAVAILABLE',source_progress:[{status:'SKIPPED',read_status:'SKIPPED'}]};
const empty={status:'READY_WITH_WARNINGS',source_completeness:'UNAVAILABLE',source_progress:[{status:'COMPLETED',read_status:'READ'}]};
const legacy={status:'READY_WITH_WARNINGS'};
assert.equal(w.materialAIReadyTitle(skipped),'草稿已生成（未找到有效资料，部分资料已跳过）');
assert.equal(w.materialAIReadyTitle(empty),'草稿已生成（未找到可采用内容）');
assert.equal(w.materialAIReadyTitle(legacy),'草稿已生成（部分资料待核对）');
""")


def test_warning_banner_dialog_and_status_step_ignore_contradictory_legacy_progress_copy():
    run_ui(r"""
const cases=[
 {fill:{status:'READY_WITH_WARNINGS',source_completeness:'UNAVAILABLE',source_progress:[{status:'SKIPPED',read_status:'SKIPPED'}],progress_step:'草稿已生成（部分资料已跳过）'},title:'草稿已生成（未找到有效资料，部分资料已跳过）',step:'未找到有效资料；部分资料已跳过'},
 {fill:{status:'READY',source_completeness:'UNAVAILABLE',source_progress:[{status:'COMPLETED',read_status:'READ'}],progress_step:'草稿已生成（部分资料已跳过）'},title:'草稿已生成（未找到可采用内容）',step:'未找到可采用内容'},
 {fill:{status:'READY_WITH_WARNINGS',progress_step:'草稿已生成（部分资料已跳过）'},title:'草稿已生成（部分资料待核对）',step:'部分资料待核对'},
];
for(const row of cases){
 state.aiFill=row.fill;
 const banner=w.renderMaterialAIFillBanner();
 const dialog=w.renderMaterialAIProgressDialogContent();
 const chip=w.renderMaterialAIProgressChip();
 for(const html of [banner,dialog]){assert(html.includes(row.title),html);assert(html.includes(row.step),html);}
 assert(chip.includes(row.title.replace(/^草稿已生成/,'AI 草稿待查看')));
 if(row.step!=='部分资料已跳过'){
   assert(!banner.includes('<span>草稿已生成（部分资料已跳过）</span>'));
   assert(!dialog.includes('data-mf-ai-progress-step>草稿已生成（部分资料已跳过）</span>'));
 }
}
""")


def test_legacy_ready_draft_only_offers_reanalysis_and_never_calls_raw_apply():
    run_ui(r"""
const fill=w.initializeMaterialAIDraft({status:'READY',run_id:'legacy',proposals:[{proposal_id:'P',default_selected:true}],draft:{proposal_count:1}});
state.aiFill=fill;fill.selections=new Set(['P']);fill.manualUpdates={'I:gross_weight_kg':{item_name:'I',fieldname:'gross_weight_kg',value:999}};
const html=w.renderMaterialAIReviewDialogContent();
assert(html.includes('草稿规则已升级'));
assert(html.includes('重新分析'));
assert(html.includes('data-action="mf-ai-row-preview"'));
assert(!html.includes('data-action="mf-ai-apply"'));
assert.equal(w.canApplyMaterialAIFill(fill),false);
w.call=async(method,args)=>{calls.push({method,args});return {ok:true}};
await w.applyMaterialAIFill();
assert.deepEqual(calls,[]);
""")


def test_real_catalog_renders_safe_skip_causes_and_legacy_generic_fallback():
    from copy import deepcopy
    from overseas_costing.services import material_ai_fill_service as ai
    from overseas_costing.services import material_ai_selection_service as selection
    from overseas_costing.tests.test_ai_selection_service import Repo

    repo=Repo()
    repo.sources=[
        {'source_id':'FORM','process_instance_id':'LOG-1','source_kind':'approval_form',
         'approval_role':'international_logistics','approval_title':'国际物流审批'},
        {'source_id':'CORRUPT','process_instance_id':'LOG-1','source_kind':'approval_attachment',
         'evidence_kind':'attachment','source_label':'packing.xlsx',
         'approval_role':'international_logistics','approval_title':'国际物流审批'},
        {'source_id':'TIMEOUT','process_instance_id':'LOG-1','source_kind':'approval_comment_attachment',
         'evidence_kind':'comment_attachment','source_label':'quote.pdf',
         'approval_role':'international_logistics','approval_title':'国际物流审批'},
        {'source_id':'LEGACY','process_instance_id':'LOG-1','source_kind':'approval_attachment',
         'evidence_kind':'attachment','source_label':'legacy.xls',
         'approval_role':'international_logistics','approval_title':'国际物流审批'},
    ]
    repo.run['source_manifest_json']=deepcopy(repo.sources)
    repo.run['source_progress_json']=[
        {'source_id':'FORM','read_status':'COMPLETED','status':'COMPLETED'},
        {'source_id':'CORRUPT','read_status':'SKIPPED','status':'SKIPPED',
         'skip_reason_code':'CORRUPT_DOCUMENT','skip_reason_text':'<html>secret</html>',
         'elapsed_ms':120},
        {'source_id':'TIMEOUT','read_status':'SKIPPED','status':'SKIPPED',
         'skip_reason_code':'PARSE_TIMEOUT','skip_reason_text':'Traceback secret',
         'elapsed_ms':15000},
        {'source_id':'LEGACY','read_status':'SKIPPED','status':'SKIPPED'},
    ]
    repo.run['candidates_json']=[{
        'proposal_id':'FORM','proposal_type':'item_update','target_item_name':'I1',
        'confidence':.99,'source_refs':[{'source_id':'FORM'}],
        'payload':{'fields':{'gross_weight_kg':2}},
    }]
    repo.run['input_fingerprint']=ai._source_review_fingerprint(
        'B1','V1',repo.items,repo.sources,'',context=repo.context)
    repo.run['draft_json']['material_input_fingerprint']=selection.material_fingerprint(
        repo.items,repo.sources,repo.context)
    catalog=selection.review_catalog(repo,'B1',repo.run)

    run_ui(f"""
const fill=ready();fill.row_review={json.dumps(catalog, ensure_ascii=False)};
delete fill.rowSelection;w.ensureMaterialAIRowSelection(fill);
const html=w.renderMaterialAIReviewDialogContent();
for(const text of ['资料文件已损坏','资料文件解析超时','未能读取该资料'])assert(html.includes(text),text);
assert((html.match(/已跳过，继续读取下一资料/g)||[]).length>=3);
for(const secret of ['<html>','secret','Traceback','/private/','token='])assert(!html.includes(secret),secret);
""")


def test_fee_roles_render_totals_components_and_alternatives_in_their_review_levels():
    run_ui(r"""
const fill=ready();fill.row_review.fees=[
 {proposal_id:'TOTAL',selection_role:'primary_total',payload:{expense_category:'应付总额',amount:'10347.00',currency:'RMB',source_label:'采购支出审批'},can_apply:true,default_selected:true,resolution_reason:'分项合计 10346.99，差额 0.01 RMB'},
 {proposal_id:'AIR',selection_role:'component',parent_proposal_id:'TOTAL',payload:{expense_category:'国际空运费',amount:'9367.46',currency:'RMB',source_label:'费用明细',remark:'空运分项'},can_apply:false,default_selected:false,blocked_reason:'已裁决总额的分项'},
 {proposal_id:'PORT',selection_role:'component',parent_proposal_id:'TOTAL',payload:{expense_category:'港杂与货代费',amount:'979.53',currency:'RMB',remark:'港杂分项'},can_apply:false,default_selected:false},
 {proposal_id:'ALT',selection_role:'alternative',payload:{expense_category:'其他空运报价',amount:'613.90',currency:'RMB',source_label:'历史报价'},can_apply:false,default_selected:false,blocked_reason:'已有明确总额，该报价不可采用'},
 {proposal_id:'AMB',selection_role:'ambiguous',payload:{expense_category:'待核对运费',amount:'700',currency:'RMB'},can_apply:false,default_selected:false},
 {proposal_id:'APPROVED',selection_role:'approved_quote',payload:{expense_category:'审批采用报价',amount:'800',currency:'RMB'},can_apply:true,default_selected:true},
 {proposal_id:'PLAIN',payload:{expense_category:'普通候选',amount:'12',currency:'RMB'},can_apply:true,default_selected:false},
];
delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);
assert.deepEqual([...selection.fees].sort(),['APPROVED','TOTAL']);
const html=w.renderMaterialAIReviewDialogContent();
const main=html.slice(html.indexOf('<h4>费用 '),html.indexOf('查看依据与其他记录'));
for(const id of ['TOTAL','APPROVED','PLAIN'])assert(main.includes(`data-mf-ai-fee-select="${id}"`),id);
for(const id of ['AIR','PORT','ALT','AMB'])assert(!main.includes(`data-mf-ai-fee-select="${id}"`),id);
assert(main.includes('data-mf-ai-fee-select="TOTAL"') && main.includes('checked'));
for(const hidden of ['9367.46','费用明细','空运分项','港杂与货代费','分项合计 10346.99，差额 0.01 RMB','原金额','来源与说明'])assert(!main.includes(hidden),hidden);
assert(!html.includes('data-mf-ai-fee-select="AIR"'));assert(!html.includes('data-mf-ai-fee-select="PORT"'));
const advanced=html.slice(html.indexOf('查看依据与其他记录'));
for(const text of ['国际空运费','9367.46','费用明细','空运分项','港杂与货代费','分项合计 10346.99，差额 0.01 RMB','其他空运报价','613.90','历史报价','待核对运费','不可采用'])assert(advanced.includes(text),text);
""")


def test_equivalent_fees_render_once_with_merged_sources_and_keep_raw_audit_rows():
    run_ui(r"""
const fill=ready();fill.source_progress=[];fill.row_review={...fill.row_review,fees:[
 {proposal_id:'approval-fee:LOG-1:1',workflow_stage:'international_logistics',selection_role:'approved_quote',result_origin:'SYSTEM',payload:{logical_fee_key:'international_express_fee',expense_category:'国际快递费',amount:'2385.37467',currency:'RMB'},can_apply:true,default_selected:true,presentation_group_id:'FEE-GROUP',presentation_representative_candidate_id:'approval-fee:LOG-1:1',presentation_equivalent_candidate_ids:['PROP-002','approval-fee:LOG-1:1'],presentation_sources:[{label:'国际物流审批 <A>',approval_no:'APP-1',origins:['SYSTEM','AI']},{label:'备用审批',approval_no:'APP-2',origins:['AI']}]},
 {proposal_id:'PROP-002',workflow_stage:'international_logistics',selection_role:'ambiguous',result_origin:'AI',payload:{logical_fee_key:'international_express_fee',expense_category:'国际快递费',amount:'2385.374670',currency:'RMB'},can_apply:true,default_selected:false,presentation_group_id:'FEE-GROUP',presentation_representative_candidate_id:'approval-fee:LOG-1:1'},
],fee_stage_snapshots:[
 {stage:'payment',status:'UNAVAILABLE',processes:[],fees:[]},
 {stage:'international_logistics',status:'AVAILABLE',processes:[],fees:[{proposal_id:'approval-fee:LOG-1:1'},{proposal_id:'PROP-002'}]},
 {stage:'purchase',status:'UNAVAILABLE',processes:[],fees:[]},
]};delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);
assert.deepEqual([...selection.fees],['approval-fee:LOG-1:1']);
selection.fees=new Set(['PROP-002']);
assert.deepEqual([...w.ensureMaterialAIRowSelection(fill).fees],['approval-fee:LOG-1:1']);
const groups=w.materialAIPresentationGroups(fill.row_review.fees);
assert.equal(groups.length,1);assert.equal(groups[0].representativeId,'approval-fee:LOG-1:1');
const html=w.renderMaterialAIReviewDialogContent();
const main=html.slice(html.indexOf('<h4>费用 '),html.indexOf('查看依据与其他记录'));
assert.equal((main.match(/data-mf-ai-fee-select=/g)||[]).length,1,main);
assert.equal((main.match(/>国际快递费</g)||[]).length,1);
assert(main.includes('<th>来源</th>'));
assert(main.includes('国际物流审批 &lt;A> · 系统直读 / AI识别'));
assert(main.includes('备用审批 · AI识别'));
assert(!main.includes('<A>'));
assert(main.includes('已选 1 项'));
const advanced=html.slice(html.indexOf('查看依据与其他记录'));
for(const id of ['approval-fee:LOG-1:1','PROP-002'])assert(advanced.includes(`data-mf-ai-fee-evidence="${id}"`),id);
w.call=async(method,args)=>{calls.push({method,args});return {ok:true,preview:{id:'P',revision:'R',can_apply:true,rows:[]}}};
await w.previewMaterialAIRowSelection();
assert.deepEqual(JSON.parse(calls[0].args.fee_ids_json),['approval-fee:LOG-1:1']);
""")


def test_current_policy_renders_three_fee_stages_with_total_components_and_other_records():
    run_ui(r"""
const fill=ready();fill.row_review={...fill.row_review,policy:'ai-field-review-4',field_candidates:[],fees:[
 {proposal_id:'TOTAL',workflow_stage:'payment',selection_role:'primary_total',payload:{logical_fee_key:'international_air_freight',expense_category:'国际空运费',amount:'10347',currency:'RMB',source_label:'月结付款'},can_apply:true,default_selected:true,resolution_reason:'分项合计 10346.99，差额 0.01 RMB'},
 {proposal_id:'AIR',workflow_stage:'payment',selection_role:'component',parent_proposal_id:'TOTAL',payload:{expense_category:'贸易项目运费',amount:'9367.46',currency:'RMB'},can_apply:false,default_selected:false},
 {proposal_id:'SHOP',workflow_stage:'payment',selection_role:'component',parent_proposal_id:'TOTAL',payload:{expense_category:'工业品电商运费',amount:'948.60',currency:'RMB'},can_apply:false,default_selected:false},
 {proposal_id:'PDD',workflow_stage:'payment',selection_role:'component',parent_proposal_id:'TOTAL',payload:{expense_category:'PDD运费',amount:'30.93',currency:'RMB'},can_apply:false,default_selected:false},
 {proposal_id:'NOISE',workflow_stage:'payment',selection_role:'alternative',payload:{expense_category:'其他记录',amount:'1.2',currency:'RMB'},can_apply:false,default_selected:false,blocked_reason:'无关金额'},
 {proposal_id:'CUSTOMS',workflow_stage:'payment',selection_role:'',payload:{logical_fee_key:'customs_clearance_fee',expense_category:'清关费',amount:'500',currency:'RMB'},can_apply:true,default_selected:true},
 {proposal_id:'LOG',workflow_stage:'international_logistics',selection_role:'approved_quote',payload:{logical_fee_key:'international_air_freight',expense_category:'国际空运费',amount:'10000',currency:'RMB'},can_apply:true,default_selected:false,resolution_reason:'低阶段可改选'},
]};
fill.row_review.stage_snapshots=[
 {stage:'payment',stage_rank:0,stage_label:'支付申请',status:'UNAVAILABLE',rows:[],processes:[],warnings:[],fallback_reason:'未找到有效资料'},
 {stage:'international_logistics',stage_rank:1,stage_label:'国际物流',status:'UNAVAILABLE',rows:[],processes:[],warnings:[],fallback_reason:'未找到有效资料'},
 {stage:'purchase',stage_rank:2,stage_label:'采购支出',status:'UNAVAILABLE',rows:[],processes:[],warnings:[],fallback_reason:'未找到有效资料'},
];
fill.row_review.fee_stage_snapshots=[
 {stage:'payment',stage_rank:0,stage_label:'支付申请',status:'AVAILABLE',fallback_reason:'',warnings:[],processes:[{process_instance_id:'P',label:'月结付款',status:'AVAILABLE',evidence:[]}],fees:['TOTAL','AIR','SHOP','PDD','NOISE','CUSTOMS'].map(proposal_id=>({proposal_id}))},
 {stage:'international_logistics',stage_rank:1,stage_label:'国际物流',status:'AVAILABLE',fallback_reason:'',warnings:[],processes:[{process_instance_id:'L',label:'国际物流审批',status:'AVAILABLE',evidence:[]}],fees:[{proposal_id:'LOG'}]},
];
delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);w.scheduleMaterialAIRowPreview=()=>{};
let html=w.renderMaterialAIReviewDialogContent();
assert.equal((html.match(/data-mf-ai-fee-stage=/g)||[]).length,3);
assert(!html.includes('来源 1 · 国际物流装箱清单.xlsx'));
assert(html.indexOf('data-mf-ai-fee-stage="payment"')<html.indexOf('data-mf-ai-fee-stage="international_logistics"'));
const payment=html.slice(html.indexOf('data-mf-ai-fee-stage="payment"'),html.indexOf('data-mf-ai-fee-stage="international_logistics"'));
assert(payment.includes('10347'));
for(const hidden of ['9367.46','948.60','30.93','总额分项（只读）','原金额','来源与说明'])assert(!payment.includes(hidden),hidden);
assert(payment.includes('data-mf-ai-fee-select="TOTAL"')&&payment.includes('checked'));
assert(!payment.includes('data-mf-ai-fee-select="AIR"'));assert(!payment.includes('data-mf-ai-fee-select="NOISE"'));
const advanced=html.slice(html.indexOf('查看依据与其他记录'));
for(const text of ['9367.46','948.60','30.93','总额分项','其他记录','1.2','只读 · 不可采用'])assert(advanced.includes(text),text);
const logistics=html.slice(html.indexOf('data-mf-ai-fee-stage="international_logistics"'));
assert(logistics.includes('data-mf-ai-fee-select="LOG"'));assert(!logistics.includes('checked'));
 w.changeMaterialAIRowSelection('fees','LOG',true);assert.deepEqual([...selection.fees].sort(),['CUSTOMS','LOG']);
w.call=async(method,args)=>{calls.push({method,args});return {ok:true,preview:{id:'P',revision:'R',can_apply:true,rows:[]}}};
await w.previewMaterialAIRowSelection();
assert.deepEqual(JSON.parse(calls[0].args.fee_ids_json).sort(),['CUSTOMS','LOG']);
assert(!('fees' in calls[0].args));
""")


def test_lower_stage_approved_quote_does_not_hide_higher_stage_server_default():
    run_ui(r"""
const fill=ready();fill.row_review={...fill.row_review,policy:'ai-field-review-4',field_candidates:[],fees:[
 {proposal_id:'PAY-DEFAULT',workflow_stage:'payment',workflow_rank:0,selection_role:'ambiguous',payload:{logical_fee_key:'international_air_freight',expense_category:'支付运费',amount:'110',currency:'RMB'},can_apply:true,default_selected:true},
 {proposal_id:'LOG-APPROVED',workflow_stage:'international_logistics',workflow_rank:1,selection_role:'approved_quote',payload:{logical_fee_key:'international_air_freight',expense_category:'物流报价',amount:'100',currency:'RMB'},can_apply:true,default_selected:false},
],stage_snapshots:[
 {stage:'payment',status:'UNAVAILABLE',rows:[],processes:[]},{stage:'international_logistics',status:'UNAVAILABLE',rows:[],processes:[]},{stage:'purchase',status:'UNAVAILABLE',rows:[],processes:[]},
],fee_stage_snapshots:[
 {stage:'payment',status:'AVAILABLE',processes:[],fees:[{proposal_id:'PAY-DEFAULT'}]},
 {stage:'international_logistics',status:'AVAILABLE',processes:[],fees:[{proposal_id:'LOG-APPROVED'}]},
]};
delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);
assert.deepEqual([...selection.fees],['PAY-DEFAULT']);
const html=w.renderMaterialAIReviewDialogContent();
const payment=html.slice(html.indexOf('data-mf-ai-fee-stage="payment"'),html.indexOf('data-mf-ai-fee-stage="international_logistics"'));
assert(payment.includes('data-mf-ai-fee-select="PAY-DEFAULT"'));assert(payment.includes('checked'));assert(payment.includes('110'));
const logistics=html.slice(html.indexOf('data-mf-ai-fee-stage="international_logistics"'));
assert(logistics.includes('data-mf-ai-fee-select="LOG-APPROVED"'));assert(logistics.includes('100'));
""")


def test_higher_stage_resolved_fee_keeps_lower_stage_server_candidate_and_switches_both_ways():
    run_ui(r"""
const fill=ready();fill.row_review={...fill.row_review,policy:'ai-field-review-4',field_candidates:[],fees:[
 {proposal_id:'PAY-APPROVED',workflow_stage:'payment',workflow_rank:0,selection_role:'approved_quote',payload:{logical_fee_key:'international_air_freight',expense_category:'支付运费',amount:'110',currency:'RMB'},can_apply:true,default_selected:true},
 {proposal_id:'LOG-AMBIGUOUS',workflow_stage:'international_logistics',workflow_rank:1,selection_role:'ambiguous',payload:{logical_fee_key:'international_air_freight',expense_category:'物流运费',amount:'100',currency:'RMB'},can_apply:true,default_selected:false},
],stage_snapshots:[
 {stage:'payment',status:'UNAVAILABLE',rows:[],processes:[]},{stage:'international_logistics',status:'UNAVAILABLE',rows:[],processes:[]},{stage:'purchase',status:'UNAVAILABLE',rows:[],processes:[]},
],fee_stage_snapshots:[
 {stage:'payment',status:'AVAILABLE',processes:[],fees:[{proposal_id:'PAY-APPROVED'}]},
 {stage:'international_logistics',status:'AVAILABLE',processes:[],fees:[{proposal_id:'LOG-AMBIGUOUS'}]},
]};
delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);w.scheduleMaterialAIRowPreview=()=>{};
assert.deepEqual([...selection.fees],['PAY-APPROVED']);
const html=w.renderMaterialAIReviewDialogContent();
for(const id of ['PAY-APPROVED','LOG-AMBIGUOUS'])assert(html.includes(`data-mf-ai-fee-select="${id}"`),id);
w.changeMaterialAIRowSelection('fees','LOG-AMBIGUOUS',true);assert.deepEqual([...selection.fees],['LOG-AMBIGUOUS']);
w.changeMaterialAIRowSelection('fees','PAY-APPROVED',true);assert.deepEqual([...selection.fees],['PAY-APPROVED']);
""")


def test_current_policy_keeps_unclassified_packing_and_fee_candidates_auditable_and_selectable_by_server_flags():
    run_ui(r"""
const fill=ready();fill.row_review={...fill.row_review,policy:'ai-field-review-7',field_candidates:[
 {candidate_id:'OTHER-W',item_name:'I1',fieldname:'gross_weight_kg',suggested_value:'12.5',workflow_stage:'other',source_label:'历史资料',can_apply:true,default_selected:true,resolution_reason:'未分类候选',presentation_group_id:'OTHER-12.5',presentation_representative_candidate_id:'OTHER-W',presentation_equivalent_candidate_ids:['OTHER-W']},
 {candidate_id:'OTHER-RO',item_name:'I1',fieldname:'volume_m3',suggested_value:'0.2',workflow_stage:'other',source_label:'旧附件',can_apply:false,default_selected:false,resolution_reason:'仅供核对',presentation_group_id:'OTHER-0.2',presentation_representative_candidate_id:'OTHER-RO',presentation_equivalent_candidate_ids:['OTHER-RO']},
],fees:[
 {proposal_id:'LEGACY-FEE',workflow_stage:'other',selection_role:'ambiguous',payload:{logical_fee_key:'customs_clearance_fee',expense_category:'历史清关费',amount:'88',currency:'RMB'},can_apply:true,default_selected:true},
 {proposal_id:'AUDIT-FEE',workflow_stage:'other',selection_role:'alternative',payload:{logical_fee_key:'international_air_freight',expense_category:'旧运费记录',amount:'66',currency:'RMB'},can_apply:false,default_selected:false,blocked_reason:'只读记录'},
],stage_snapshots:[
 {stage:'payment',status:'UNAVAILABLE',rows:[],processes:[]},{stage:'international_logistics',status:'UNAVAILABLE',rows:[],processes:[]},{stage:'purchase',status:'UNAVAILABLE',rows:[],processes:[]},
],fee_stage_snapshots:[
 {stage:'payment',status:'UNAVAILABLE',processes:[],fees:[]},{stage:'international_logistics',status:'UNAVAILABLE',processes:[],fees:[]},
]};
delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);
assert.deepEqual([...selection.fields.entries()],[['I1:gross_weight_kg','OTHER-W']]);assert.deepEqual([...selection.fees],['LEGACY-FEE']);
const html=w.renderMaterialAIReviewDialogContent();
assert(html.includes('data-mf-ai-unclassified-packing'));
assert(html.includes('data-mf-ai-field-selected="OTHER-12.5"'));assert(html.includes('12.5'));assert(html.includes('OTHER-RO'));assert(html.includes('0.2'));assert(html.includes('只读'));
assert(![...selection.fields.values()].includes('OTHER-RO'));
assert(html.includes('data-mf-ai-unclassified-fees'));
assert(html.includes('data-mf-ai-fee-select="LEGACY-FEE"'));assert(html.includes('checked'));assert(html.includes('历史清关费'));
const main=html.slice(0,html.indexOf('查看依据与其他记录'));
assert(!main.includes('AUDIT-FEE'));assert(!main.includes('旧运费记录'));assert(!main.includes('只读记录'));
const advanced=html.slice(html.indexOf('查看依据与其他记录'));
assert(advanced.includes('AUDIT-FEE'));assert(advanced.includes('旧运费记录'));assert(advanced.includes('只读记录'));
assert.equal((html.match(/data-mf-ai-fee-select="LEGACY-FEE"/g)||[]).length,1);
""")


def test_ambiguous_fees_remain_visible_and_are_single_choice_without_resolved_total():
    run_ui(r"""
const fill=ready();fill.row_review.fees=[
 {proposal_id:'A',selection_role:'ambiguous',payload:{expense_category:'运费候选 A',amount:'100',currency:'RMB'},can_apply:true,default_selected:false},
 {proposal_id:'B',selection_role:'ambiguous',payload:{expense_category:'运费候选 B',amount:'120',currency:'RMB'},can_apply:true,default_selected:false},
];
delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);w.scheduleMaterialAIRowPreview=()=>{};
let html=w.renderMaterialAIReviewDialogContent();
for(const id of ['A','B'])assert(html.includes(`type="checkbox" data-mf-ai-fee-select="${id}"`),id);
w.changeMaterialAIRowSelection('fees','A',true);assert.deepEqual([...selection.fees],['A']);
w.changeMaterialAIRowSelection('fees','B',true);assert.deepEqual([...selection.fees],['B']);
""")


def test_ambiguous_fees_only_replace_overlapping_coverage_and_keep_other_scopes():
    run_ui(r"""
const fill=ready();fill.row_review.fees=[
 {proposal_id:'FREIGHT',selection_role:'ambiguous',workflow_stage:'payment',payload:{logical_fee_key:'international_air_freight',amount:'100',currency:'RMB'},can_apply:true,default_selected:false},
 {proposal_id:'CUSTOMS',selection_role:'ambiguous',workflow_stage:'payment',payload:{logical_fee_key:'customs_clearance_fee',amount:'20',currency:'RMB'},can_apply:true,default_selected:false},
];
delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);w.scheduleMaterialAIRowPreview=()=>{};
w.changeMaterialAIRowSelection('fees','FREIGHT',true);w.changeMaterialAIRowSelection('fees','CUSTOMS',true);
assert.deepEqual([...selection.fees].sort(),['CUSTOMS','FREIGHT']);
const html=w.renderMaterialAIReviewDialogContent();
for(const id of ['FREIGHT','CUSTOMS'])assert(html.includes(`type="checkbox" data-mf-ai-fee-select="${id}"`),id);
""")


def test_server_blocked_ambiguous_and_unknown_roles_are_excluded_from_all_selection_paths():
    run_ui(r"""
const fill=ready();fill.row_review.fees=[
 {proposal_id:'TOTAL',selection_role:'primary_total',payload:{expense_category:'已裁决总额',amount:'200',currency:'RMB'},can_apply:true,default_selected:true},
 {proposal_id:'AMB',selection_role:'ambiguous',payload:{expense_category:'歧义运费',amount:'199',currency:'RMB'},can_apply:false,default_selected:true,blocked_reason:'已有裁决总额'},
 {proposal_id:'FUTURE',selection_role:'future_role',payload:{expense_category:'未知角色费用',amount:'88',currency:'RMB'},can_apply:true,default_selected:true},
];
delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);w.scheduleMaterialAIRowPreview=()=>{};
assert.deepEqual([...selection.fees],['TOTAL']);
const html=w.renderMaterialAIReviewDialogContent();
const main=html.slice(html.indexOf('<h4>费用 '),html.indexOf('查看依据与其他记录'));
assert(main.includes('data-mf-ai-fee-select="TOTAL"'));
for(const id of ['AMB','FUTURE'])assert(!main.includes(`data-mf-ai-fee-select="${id}"`),id);
for(const text of ['歧义运费','未知角色费用'])assert(!main.includes(text),text);
w.changeMaterialAIRowSelection('fees','AMB',true);w.changeMaterialAIRowSelection('fees','FUTURE',true);
assert.deepEqual([...selection.fees],['TOTAL']);
""")


def test_only_latest_server_preview_is_displayed_and_can_be_confirmed():
    run_ui(r"""
const fill=ready();fill.rowSelection.mode='fill_missing';const pending=[];w.call=(method,args)=>{calls.push({method,args});return new Promise(resolve=>pending.push(resolve))};
const first=w.previewMaterialAIRowSelection();
fill.rowSelection.rows.clear();const second=w.previewMaterialAIRowSelection();
assert.equal(w.canConfirmMaterialAIRowSelection(fill),false);
assert.equal(pending.length,1,'Serialize previews so a late older request cannot replace the server current preview');
pending[0]({ok:true,preview:{id:'old',revision:1,can_apply:true,rows:[{material_code:'STALE'}]}});await first;
assert.equal(fill.rowSelection.preview,null);assert.equal(w.canConfirmMaterialAIRowSelection(fill),false);
pending[1]({ok:true,preview:{id:'new',revision:2,can_apply:true,rows:[{material_code:'SERVER',gross_weight_kg:0}],added_count:3,removed_count:1,missing_fields:['volume_m3']}});await second;
assert.equal(fill.rowSelection.preview.id,'new');assert(w.canConfirmMaterialAIRowSelection(fill));
const html=w.renderMaterialAIReviewDialogContent();assert(html.includes('SERVER'));assert(!html.includes('STALE'));assert(html.includes('新增 3'));assert(html.includes('移除 1'));assert(html.includes('缺项 1'));
assert.equal(calls[1].args.row_ids_json,'[]');assert(!('rows' in calls[1].args));
w.scheduleMaterialAIRowPreview();assert.equal(w.canConfirmMaterialAIRowSelection(fill),false);clearTimeout(fill.rowSelection.timer);
""")


def test_unmatched_material_requires_separate_add_mode_and_clears_fees():
    run_ui(r"""
const fill=ready();fill.row_review.rows.push({row_id:'new-row',origin:'source',action:'add_candidate',
  values:{material_code:'SKU-NEW'},can_fill:false,can_update:false,can_add:true,blocked_reason:'请单独确认新增'});
w.scheduleMaterialAIRowPreview=()=>{};delete fill.rowSelection;const selection=w.ensureMaterialAIRowSelection(fill);
let html=w.renderMaterialAIReviewDialogContent();assert(html.includes('单独确认新增'));
w.changeMaterialAIRowSelection('rows','new-row',true);assert(!selection.rows.has('new-row'));
w.changeMaterialAIRowSelection('mode','add_selected');assert.equal(selection.fees.size,0);
w.changeMaterialAIRowSelection('rows','new-row',true);assert.deepEqual([...selection.rows],['new-row']);
html=w.renderMaterialAIReviewDialogContent();assert(html.includes('确认新增'));
assert(html.includes('data-mf-ai-fee-select="fee" disabled'));
""")


def test_preview_failure_and_version_change_keep_selection_without_stale_confirmation():
    run_ui(r"""
const fill=ready();w.call=async()=>({ok:false,message:'来源已变化'});await w.previewMaterialAIRowSelection();
assert.equal(fill.rowSelection.error,'来源已变化');assert.deepEqual([...fill.rowSelection.rows],['source']);assert(!w.canConfirmMaterialAIRowSelection(fill));
let release;w.call=()=>new Promise(r=>release=r);const request=w.previewMaterialAIRowSelection();w.detailState.versionName='V2';
release({ok:true,preview:{id:'wrong',revision:1,can_apply:true}});await request;assert(!fill.rowSelection.preview);
""")


def test_preview_queue_coalesces_intermediate_choices_and_replace_requires_one_row():
    run_ui(r"""
const fill=ready();const pending=[];w.call=(method,args)=>{calls.push({method,args});return new Promise(resolve=>pending.push(resolve))};
const first=w.previewMaterialAIRowSelection();fill.rowSelection.rows.clear();const middle=w.previewMaterialAIRowSelection();
fill.rowSelection.rows.add('source');fill.rowSelection.fees.clear();const latest=w.previewMaterialAIRowSelection();
assert.equal(calls.length,1);pending[0]({ok:true,preview:{id:'old',revision:1,can_apply:true}});await first;await middle;
assert.equal(calls.length,2);assert.equal(calls[1].args.fee_ids_json,'[]');assert.equal(calls[1].args.row_ids_json,'["source"]');
pending[1]({ok:true,preview:{id:'latest',revision:2,can_apply:true}});await latest;assert(w.canConfirmMaterialAIRowSelection(fill));
fill.rowSelection.mode='update_selected';fill.rowSelection.rows.clear();fill.rowSelection.previewKey=w.materialAIRowSelectionKey(fill);
assert.equal(w.canConfirmMaterialAIRowSelection(fill),true,'A fee-only preview may be confirmed without a material row');
""")


def test_row_review_discard_error_stays_inside_dialog_and_preserves_choice():
    run_ui(r"""
const fill=ready();w.renderMaterialFeeWorkspace=()=>{};w.call=async()=>({ok:false,message:'暂时无法放弃'});
await w.discardMaterialAIFill();assert.equal(state.aiFill,fill);assert.equal(fill.discarding,false);
assert.equal(fill.rowSelection.error,'暂时无法放弃');assert.deepEqual([...fill.rowSelection.rows],['source']);
""")


def test_row_review_refresh_keeps_table_and_dialog_scroll_position():
    run_ui(r"""
ready();const body={top:420,scrollTop(value){if(value===undefined)return this.top;this.top=value;return this}};
const table={left:180,scrollLeft(value){if(value===undefined)return this.left;this.left=value;return this}};
global.$=node=>node;
const tables={each:callback=>callback(0,table)};
const wrapper={length:1,addClass:()=>{},find:selector=>selector==='.ocw-mf-ai-dialog-body'?body:tables};
state.aiProgressDialog={$wrapper:wrapper,fields_dict:{progress_html:{$wrapper:{html:()=>{body.top=0;table.left=0}}}}};
Harness.prototype.renderMaterialAIReviewDialog.call(w);assert.equal(body.top,420);assert.equal(table.left,180);
""")


def test_confirmation_submits_only_server_identifiers_and_preserves_failed_draft():
    run_ui(r"""
const fill=ready();w.call=async()=>({ok:true,preview:{id:'p',revision:7,can_apply:true,rows:[]}});await w.previewMaterialAIRowSelection();
w.call=async(method,args)=>{calls.push({method,args});return {ok:false,message:'并发修改，请重试'}};
await w.applyMaterialAIFill();assert.equal(fill.rowSelection.error,'并发修改，请重试');assert.equal(state.aiFill,fill);assert.deepEqual([...fill.rowSelection.rows],['source']);
assert.equal(calls[0].method,'overseas_costing.api.materials.confirm_source_ai_selection');
assert.deepEqual(calls[0].args,{batch_name:'B',run_id:'run',preview_id:'p',preview_revision:7,edit_token:'token',expected_modified:REV_OLD});
w.call=async()=>({ok:true,status:'APPLIED',version_name:'V2',batch_modified:REV_NEW});await w.applyMaterialAIFill();
assert.equal(state.aiFill,null);assert.equal(w.detailState.versionName,'V2');assert.equal(w.detailState.expectedModified,REV_NEW);
""")


def test_second_forced_run_drops_first_run_ui_state_and_uses_new_row_review():
    run_ui(r"""
const run1=ready();run1.rowSelection.mode='fill_missing';run1.selections=new Set(['legacy']);
run1.manualUpdates={'I:goods_value':{value:'99'}};run1.draftVisible=true;
run1.draft={autofill_preview:{items:[{material_code:'OLD'}]}};
run1.source_progress=[{source_id:'SOURCE-1'}];run1.rowSelection.preview={id:'preview-1',revision:1,can_apply:true};
run1.rowSelection.previewKey=w.materialAIRowSelectionKey(run1);
w.openMaterialAIProgressDialog=()=>{};w.updateMaterialAIProgressSurface=()=>{};
const nextReview={...catalog,fingerprint:'fp-2',rows:[{row_id:'new-source',origin:'source',action:'update',values:{material_code:'NEW'},can_fill:true,can_update:true,can_replace:true,default_selected:true,default_update_selected:true,default_replace_selected:true}]};
w.call=async()=>({ok:true,status:'APPLIED',version_name:'V2',batch_modified:REV_NEW});
w.loadMaterialFeeWorkspace=async()=>{state.aiFill={...run1,status:'APPLIED',source_progress:[{source_id:'SOURCE-1'}]};return true};
await w.applyMaterialAIFill();assert.equal(w.detailState.versionName,'V2');
let releaseStart;w.call=()=>new Promise(resolve=>{releaseStart=resolve});
w.pollMaterialAIFill=async(current)=>{const ready2={ok:true,status:'READY',run_id:'run-2',version_name:'V2',
  source_progress:[{source_id:'SOURCE-2'}],row_review:nextReview};current.aiFill={...current.aiFill,...ready2,runId:'run-2'};
  current.aiPendingReady=ready2;w.showMaterialAIReadyDraft()};
const running=w.startMaterialAIFill({force:true,restart:true});
assert.deepEqual(state.aiFill.source_progress,[],'STARTING must not retain prior-run source progress or UI state');
for(const key of ['rowSelection','selections','manualUpdates','draft'])assert.equal(state.aiFill[key],undefined,key);
releaseStart({ok:true,status:'QUEUED',run_id:'run-2',source_progress:[{source_id:'SOURCE-2'}]});await running;
const fill=state.aiFill;assert.equal(fill.runId,'run-2');assert.equal(fill.rowSelection.mode,'update_selected');
assert.deepEqual([...fill.rowSelection.rows],['new-source']);assert.deepEqual(fill.source_progress,[{source_id:'SOURCE-2'}]);
assert.equal(fill.selections.size,0);assert.deepEqual(fill.manualUpdates,{});
const html=w.renderMaterialAIReviewDialogContent();assert(html.includes('data-mf-ai-packing-stage="payment"'));
assert(!html.includes('data-mf-ai-row-select="new-source"'));assert(!html.includes('data-mf-ai-source-group='));
assert(!html.includes('data-mf-ai-autofill-preview'));assert(!html.includes('OLD'));
""")


def test_explicit_reread_starts_a_fresh_unified_three_stage_analysis():
    run_ui(r"""
ready();w.openMaterialAIProgressDialog=()=>{};w.pollMaterialAIFill=async()=>{};
w.call=async(method,args)=>{calls.push({method,args});return {ok:true,status:'QUEUED',run_id:'fresh',progress_revision:0}};
await w.restartMaterialAIFromCurrentSources();
assert.equal(calls[0].method,'overseas_costing.api.materials.start_source_ai_review');
assert.equal(calls[0].args.force,1);assert(!('reanalyze_original_sources' in calls[0].args));
assert(!('payment_candidate_refs_json' in calls[0].args));
assert(!('selected_source_ids_json' in calls[0].args));
""")


def test_selected_payment_scope_is_forwarded_as_id_only_ai_input():
    run_ui(r"""
ready();w.openMaterialAIProgressDialog=()=>{};w.pollMaterialAIFill=async()=>{};
const refs=[{candidate_id:'C1',revision:'R1',version:w.detailState.versionName}];
w.call=async(method,args)=>{calls.push({method,args});return {ok:true,status:'QUEUED',run_id:'fresh',progress_revision:0}};
await w.startMaterialAIFill({force:true,restart:true,paymentPreflightComplete:true,paymentCandidateRefs:refs});
const args=calls[0].args;assert.equal(calls[0].method,'overseas_costing.api.materials.start_source_ai_review');
assert.deepEqual(JSON.parse(args.payment_candidate_refs_json),refs);
for(const forbidden of ['amount','gross_weight_kg','source_snapshot'])assert(!args.payment_candidate_refs_json.includes(forbidden));
""")


def test_unscoped_start_leaves_payment_preflight_to_the_unified_endpoint():
    run_ui(r"""
ready();w.openMaterialAIProgressDialog=()=>{};w.pollMaterialAIFill=async()=>{};
w.call=async(method,args)=>{calls.push({method,args});return {ok:true,status:'QUEUED',run_id:'fresh',progress_revision:0}};
await w.startMaterialAIFill({force:true,restart:true});
assert(calls[0].method.endsWith('start_source_ai_review'));
assert.equal(calls.length,1);assert(!('payment_candidate_refs_json' in calls[0].args));
""")


def test_row_review_candidate_and_final_tables_show_server_valuations():
    run_ui(r"""
const fill=ready();fill.row_review.rows[0].values={...fill.row_review.rows[0].values,
  unit_price:'4.40',purchase_currency:'RMB',shipment_value_rmb:'10560'};
fill.rowSelection.preview={id:'P',revision:'R',can_apply:true,rows:[fill.row_review.rows[0].values]};
fill.rowSelection.previewKey=w.materialAIRowSelectionKey(fill);
const html=w.renderMaterialAIReviewDialogContent();
for(const text of ['采购单价','采购原币','本次发货货值 RMB','4.40','10560'])assert(html.includes(text),text);
""")


def test_row_review_displays_server_packing_group_candidates_and_autofill_price_columns():
    run_ui(r"""
const fill=ready();fill.draft={packing_group_candidates:[{candidate_id:'G1',member_keys:['L1','L2','L3'],package_count:'21',
    net_weight_kg:'389',gross_weight_kg:'397.7',volume_m3:'0.40884',sheet_name:'Packing',default_selected:true,can_apply:true,
    assignment_options:[{assignment_id:'G1:ALL',mode:'one_box_group',member_keys:['L1','L2','L3'],label:'3 个物料共同装为 1 箱',default_selected:true,can_apply:true}]}]};
fill.rowSelection=null;w.ensureMaterialAIRowSelection(fill);fill.rowSelection.preview={id:'P',revision:'R',can_apply:true,rows:[],packing_group_candidates:fill.draft.packing_group_candidates};
fill.rowSelection.previewKey=w.materialAIRowSelectionKey(fill);
let html=w.renderMaterialAIReviewDialogContent();
for(const text of ['装箱组候选','3 行','21','389','397.7','0.40884'])assert(html.includes(text),text);
assert(html.includes('type="radio"'));assert(html.includes('data-mf-ai-packing-assignment="G1"'));
assert(html.includes('value="G1:ALL"'));assert(html.includes('checked'));
assert.deepEqual([...fill.rowSelection.packingAssignments.entries()],[['G1','G1:ALL']]);
fill.draft.autofill_preview={items:[{unit_price:'4.40',purchase_currency:'RMB',shipment_value_rmb:'10560'}],fees:[],notes:[],project_summary:[],unresolved:[]};
html=w.renderMaterialAIAutofillPreview(fill);
for(const text of ['采购单价','币种','本次发货货值 RMB','4.40','10560'])assert(html.includes(text),text);
""")


def test_non_actionable_packing_group_hint_is_not_rendered_as_a_candidate_section():
    run_ui(r"""
const fill=ready();fill.draft={packing_group_candidates:[{candidate_id:'G-NONE',member_keys:[],
    gross_weight_kg:'42.05',volume_m3:'0.01518',source_label:'评论',default_selected:false,can_apply:false,
    assignment_options:[],resolution_reason:'评论中整票重量和尺寸已识别，但物料成员匹配不唯一。'}]};
fill.rowSelection=null;w.ensureMaterialAIRowSelection(fill);
const html=w.renderMaterialAIReviewDialogContent();
assert(!html.includes('装箱组候选'));
assert(!html.includes('G-NONE'));
""")


def test_ambiguous_packing_assignment_is_selectable_and_mutually_exclusive():
    run_ui(r"""
const fill=ready();fill.draft={packing_group_candidates:[{candidate_id:'G-NEW',member_keys:['L1'],
    gross_weight_kg:'42.05',sheet_name:'评论',default_selected:true,can_apply:true,needs_member_confirmation:true,
    assignment_options:[
      {assignment_id:'G-NEW:L1',mode:'single_item',member_keys:['L1'],label:'仅归属 MWV101144',default_selected:false,can_apply:true},
      {assignment_id:'G-NEW:L2',mode:'single_item',member_keys:['L2'],label:'仅归属 MWV101145',default_selected:false,can_apply:true},
      {assignment_id:'G-NEW:ALL',mode:'one_box_group',member_keys:['L1','L2'],label:'两个物料共同装为 1 箱',default_selected:true,can_apply:true},
    ],evidence:[{kind:'trusted_comment_text'}]}]};
fill.rowSelection=null;w.ensureMaterialAIRowSelection(fill);
let html=w.renderMaterialAIReviewDialogContent();
assert.equal((html.match(/name="packing-assignment-G-NEW"/g)||[]).length,3);
assert.equal((html.match(/name="packing-assignment-G-NEW"[^>]*checked/g)||[]).length,1);
assert(!html.includes('data-mf-ai-packing-group-select'));
w.scheduleMaterialAIRowPreview=()=>{};
w.changeMaterialAIRowSelection('packingAssignments','G-NEW','G-NEW:L1');
assert.deepEqual([...fill.rowSelection.packingAssignments.entries()],[['G-NEW','G-NEW:L1']]);
""")


def test_shared_box_is_rendered_once_in_final_preview_instead_of_repeating_group_totals():
    run_ui(r"""
const fill=ready();fill.draft={packing_group_candidates:[{candidate_id:'G-NEW',member_keys:['L1'],
    package_count:null,gross_weight_kg:'42.05',volume_m3:'0.01518',sheet_name:'评论',default_selected:true,can_apply:true,
    assignment_options:[
      {assignment_id:'G-NEW:L1',mode:'single_item',member_keys:['L1'],label:'仅归属 MWV101144',default_selected:false,can_apply:true},
      {assignment_id:'G-NEW:ALL',mode:'one_box_group',member_keys:['L1','L2'],package_count_override:'1',label:'MWV101144、MWV101145 共同装为 1 箱',default_selected:true,can_apply:true},
    ],evidence:[{kind:'trusted_comment_text'}]}]};
fill.rowSelection=null;w.ensureMaterialAIRowSelection(fill);
fill.rowSelection.preview={id:'P',revision:'R',can_apply:true,rows:[
  {name:'I1',stable_line_key:'L1',material_code:'MWV101144',product_name:'薇武士IP17 PRO',actual_shipped_qty:1,package_count:1,gross_weight_kg:'42.05',volume_m3:'0.01518'},
  {name:'I2',stable_line_key:'L2',material_code:'MWV101145',product_name:'薇武士IP17 PRO MAX',actual_shipped_qty:1,package_count:1,gross_weight_kg:'42.05',volume_m3:'0.01518'},
],packing_group_candidates:fill.draft.packing_group_candidates};
fill.rowSelection.previewKey=w.materialAIRowSelectionKey(fill);
const html=w.renderMaterialAIReviewDialogContent();
const finalTable=html.slice(html.indexOf('class="ocw-mf-ai-final-table"'));
assert(finalTable.includes('rowspan="2"'));
assert(finalTable.includes('共享 1 箱总计'));
assert.equal((finalTable.match(/>42\.05</g)||[]).length,1);
assert.equal((finalTable.match(/>0\.01518</g)||[]).length,1);
assert.equal((finalTable.match(/>1<\/td>/g)||[]).length,2); // 两条实发数量
assert.equal((finalTable.match(/rowspan="2">1<small>共享 1 箱总计/g)||[]).length,1);
""")


def test_material_row_recovery_is_previewed_before_confirming_new_version():
    run_ui(r"""
let confirmation='';global.frappe.confirm=(html,yes)=>{confirmation=html;yes()};
w.call=async(method,args)=>{calls.push({method,args});if(method.endsWith('preview_material_row_recovery'))return {ok:true,can_confirm:true,id:'restore',revision:'r1',current_version:'V',current_count:1,restored_count:7,before_rows:[{action:'current',material_code:'A'}],after_rows:[{action:'keep_updated',material_code:'A'},{action:'restore',material_code:'B'}]};return {ok:true,version_name:'V2',batch_modified:REV_NEW,restored_count:7,item_count:8}};
await w.previewMaterialRowRecovery();
assert(confirmation.includes('将恢复 7 行'));assert(confirmation.includes('A'));assert(confirmation.includes('B'));
assert(confirmation.includes('恢复前'));assert(confirmation.includes('恢复后'));
assert(calls[0].method.endsWith('preview_material_row_recovery'));
assert(calls[1].method.endsWith('confirm_material_row_recovery'));
assert.deepEqual(calls[1].args,{batch_name:'B',version_name:'V',preview_id:'restore',revision:'r1',edit_token:'token',expected_modified:REV_OLD});
assert.equal(w.detailState.versionName,'V2');assert.equal(w.detailState.expectedModified,REV_NEW);
""")


def test_review_source_and_css_retire_duplicate_evidence_and_second_sticky_column():
    source = (PARTS / "78-material-fee-workspace.js").read_text()
    css = (PARTS / "48-material-fee-workspace.css").read_text()
    stage_css = css[css.index('.ocw-mf-ai-stage-panel'):css.index('.ocw-mf-ai-unclassified')]

    assert 'renderMaterialAIStageEvidence(' not in source
    assert 'data-mf-ai-source-group=' not in source
    assert 'const hasFeeStages' not in source
    assert 'const sourceTables' not in source
    assert 'ocw-mf-ai-stage-evidence' not in css
    assert '.ocw-mf-ai-source-candidate-group' not in css
    assert '.ocw-mf-ai-source-priority-reason' not in css
    assert '.ocw-mf-ai-field-source-group' not in css
    assert '.ocw-mf-ai-fee-components' not in css
    assert '.ocw-mf-ai-other-fees' not in css
    assert 'th:first-child' in stage_css
    assert 'nth-child(2)' not in stage_css
    assert 'overflow-x: auto' in css
    assert ':focus-visible' in stage_css
    assert '@media (max-width: 980px)' in css
    assert '@media (max-width: 700px)' in css


INLINE = r"""
const fee={logical_fee_key:'freight',source_binding_id:'claim',source_label:'银行支付流程',amount:80,currency:'RMB'};
state.fees={fees:[fee]};
const data={ok:true,freight_mode:true,viewed_version:'V',freight:{revision:'r1',claims:[{id:'claim',line_id:'line',label:'运输费',amount:80,applied_amount:80,original_amount:100,currency:'RMB',approval_no:'PAY-OLD'}]},candidates:[{id:'new',revision:'r2',expense:{approved:true,approval_no:'PAY-NEW'},lines:[{id:'line2',amount:60,currency:'RMB',available:true,label:'运输费'}]}]};
w.call=async(method,args)=>{calls.push({method,args});return method.endsWith('get_batch_settlement')?data:{ok:true,version:'V2'}};
w.renderMaterialFeeFreightSurface=()=>{};
"""


def test_inline_amount_keeps_zero_and_failure_inputs_and_never_opens_modal():
    run_ui(INLINE + r"""
w.openBatchSettlementDialog=()=>{throw Error('Must stay inline')};
await w.openMaterialFeeFreightEditor('freight','amount');const editor=state.freightEditor;
assert.equal(editor.freightView.claim_id,'claim');assert.equal(calls.length,1);
const html=w.renderMaterialFeeFreightEditor('freight');assert(html.startsWith('<tr'));assert(html.includes('原始证据金额'));assert(html.includes('保存'));assert(html.includes('取消'));
editor.freightDraft={amount:'0',reason:'核对原单'};
w.call=async(method,args)=>{calls.push({method,args});return {ok:false,message:'版本冲突'}};
await w.saveMaterialFeeFreightEditor();assert.equal(editor.freightError,'版本冲突');assert.equal(editor.freightDraft.amount,'0');assert.equal(editor.freightDraft.reason,'核对原单');
assert.equal(calls[1].args.amount,'0');assert.equal(calls[1].args.expected_revision,'r1');assert(!('currency' in calls[1].args));
editor.freightDraft.amount='-5';await w.saveMaterialFeeFreightEditor();assert(editor.freightError.includes('负数'));assert.equal(calls.length,2);
""")


def test_inline_source_replacement_delta_and_success_refresh_keep_new_version():
    run_ui(INLINE + r"""
await w.openMaterialFeeFreightEditor('freight','replace');const editor=state.freightEditor;
editor.freightDraft={candidate_id:'new',line_ids:['line2'],reason:'本票新账单'};
assert(w.renderMaterialFeeFreightEditor('freight').includes('-20 RMB'));assert.equal(calls.length,1);
w.loadMaterialFeeWorkspace=async()=>{throw Error('模拟刷新失败')};
await w.saveMaterialFeeFreightEditor();assert.equal(w.detailState.versionName,'V2');assert(editor.saved);assert.equal(calls[1].args.action,'replace');assert.equal(calls[1].args.line_ids,'["line2"]');
assert(editor.freightError.includes('已保存'));await w.saveMaterialFeeFreightEditor();assert.equal(calls.length,2);
""")


def test_inline_pending_read_is_fenced_and_cancel_does_not_write():
    run_ui(INLINE + r"""
let release;w.call=()=>new Promise(r=>release=r);
const request=w.openMaterialFeeFreightEditor('freight','amount');const editor=state.freightEditor;
state.freightEditor=null;release(data);await request;assert.equal(state.freightEditor,null);assert(!editor.freightView);
w.call=async()=>({...data,historical:true});await w.openMaterialFeeFreightEditor('freight','amount');
assert(state.freightEditor.freightError.includes('历史版本'));assert(!state.freightEditor.freightView);
""")


def test_inline_source_row_uses_payment_label_and_expands_below_its_own_row():
    run_ui(INLINE + r"""
w.formatMoney=v=>String(v);w.materialFeeSavedCostPreview=()=>null;
fee.original_amount=100;fee.applied_amount=80;fee.amount_status='ACTUAL';
await w.openMaterialFeeFreightEditor('freight','replace');
const html=w.renderMaterialFeeRow(fee);
for(const text of ['银行支付流程','原始证据金额：RMB 100','当前采用金额','改金额','换来源','撤销采用'])assert(html.includes(text),text);
assert(!html.includes('物流采购支出'));assert(html.indexOf('data-mf-freight-editor')>html.indexOf('</tr>'));
state.freightEditor.freightDraft.candidate_id='new';
assert(!w.renderMaterialFeeFreightEditor('freight').includes('data-settlement-action="freight-evidence"'),'Inline candidate evidence must not use an unbound modal action');
""")
