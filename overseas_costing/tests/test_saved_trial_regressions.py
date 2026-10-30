"""Regressions found while reviewing the saved-trial integration."""

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import subprocess

import pytest

from overseas_costing.services import cost_preview_service, fee_service


@pytest.mark.parametrize("stored_key", [None, "", "  "])
def test_saved_trial_keeps_legacy_item_identity_without_backfilling(stored_key):
    items = [dict(name="OLD-1", stable_line_key=stored_key, quantity=10,
                  goods_value=100, chargeable_weight_kg=1, unit="个", unit_price_uom="个")]
    original = deepcopy(items)
    fees = fee_service.build_default_fee_templates("AIR")
    for fee in fees:
        fee.update(amount_status="ACTUAL", amount=0)
    fees[0]["amount"] = 20

    saved = cost_preview_service.build_saved_cost_data(items, fees, {}, "AIR")

    assert saved["items"][0]["stable_line_key"] == "legacy:OLD-1"
    assert saved["item_updates"][0]["name"] == "OLD-1"
    assert Decimal(saved["item_updates"][0]["total_cost_rmb"]) == Decimal("120")
    assert saved["items"][0]["shipping_unit_price"] == {
        "amount_rmb": "10.000000",
        "uom": "个",
    }
    assert saved["items"][0]["shipping_unit_cost"] == {
        "amount_rmb": "12.000000",
        "uom": "个",
    }
    assert saved["item_updates"][0]["total_unit_rmb"] == "12.000000"
    derived = json.loads(saved["item_updates"][0]["derived_json"])
    assert "shipping_unit_price" not in derived
    assert derived["shipping_unit_cost"] == saved["items"][0]["shipping_unit_cost"]
    assert "stable_line_key" not in saved["item_updates"][0]
    assert items == original


def _frontend_result(script):
    parts = Path(__file__).resolve().parents[1] / "page/overseas_cost_workbench/parts"
    completed = subprocess.run(
        ["node", "-e",
         "const fs=require('fs');"
         f"const parts={json.dumps(str(parts))};"
         "const source=fs.readFileSync(parts+'/30-calculation-erp.js','utf8').split('  setMainView(')[0]"
         "+fs.readFileSync(parts+'/78-material-fee-workspace.js','utf8');"
         "const Harness=Function(`return class ReviewHarness {${source}}`)();"
         "global.frappe={show_alert:()=>{}};"
         f"(async()=>{{{script}}})().catch(error=>{{console.error(error);process.exit(1)}});"],
        check=True, capture_output=True, text=True,
    )
    return json.loads(completed.stdout)


FRONTEND_SETUP = """
const h=new Harness();
h.detailState={batchName:'B',versionName:'V',tab:'documents',expectedModified:'m1',editToken:'T',header:{current_version:'V'}};
h.batches=[{name:'B',current_version:'V',modified:'m1'}];
h.$root={find:()=>({prop(){return this},text(){return this}}),attr:()=> 'detail'};
h.ensureEditSession=async()=>true;
let renders=0;h.renderMaterialFeeWorkspace=()=>{renders+=1};
const state=h.ensureMaterialFeeState();
const saved={ok:true,saved:true,read_only:false,batch_name:'B',version_name:'V',batch_modified:'m2',summary:{total_cost_rmb:'120'},summary_snapshot:{calculation_schema:2,total_cost_rmb:'120'}};
"""

FRONTEND_CONFIRM_SETUP = """
state.costTrialAI={runId:'RUN',status:'READY',draft:{fee_suggestions:[]},selections:[],preview:{preview_token:'P'}};
h.renderCostTrialAIReviewDialog=()=>{};
state.costTrialDialog={hide(){this.hidden=true}};
"""


@pytest.mark.parametrize("legacy_total", ["27900.00", "0.00", None])
def test_unsaved_preview_never_appears_as_a_saved_trial(legacy_total):
    result = _frontend_result(FRONTEND_SETUP + f"""
h.escape=value=>String(value ?? '');
h.detailState.header={{status:'Dirty',summary_snapshot:{json.dumps({"total_cost_rmb": legacy_total} if legacy_total is not None else {})}}};
state.preview={{read_only:true,summary:{{total_cost_rmb:'41941.38',included_fee_count:3}},
  items:[{{material_code:'UNSAVED-SKU',total_cost_rmb:'39085.15'}}]}};
console.log(JSON.stringify(h.renderMaterialFeeCostTable()));
""")
    assert "41941.38" not in result
    assert "UNSAVED-SKU" not in result
    assert "上次试算" not in result
    assert "开始试算" in result
    if legacy_total is None:
        assert "尚未试算" in result
        assert "RMB 0.00" not in result
    else:
        assert f"RMB {legacy_total}" in result
        assert "上次已保存成本" in result
        assert "待重新试算" in result


@pytest.mark.parametrize("status,label", [("Calculated", "当前试算总成本"), ("Dirty", "上次试算 · 结果待更新")])
def test_saved_trial_display_keeps_canonical_result_when_readonly_preview_changes(status, label):
    result = _frontend_result(FRONTEND_SETUP + f"""
h.escape=value=>String(value ?? '');
h.detailState.header={{status:{json.dumps(status)},summary_snapshot:{{calculation_schema:2,
  comprehensive_cost:{{summary:{{total_cost_rmb:'41941.38'}},items:[{{material_code:'SAVED-SKU'}}]}}}}}};
state.preview={{read_only:true,summary:{{total_cost_rmb:'99999.00'}},items:[{{material_code:'UNSAVED-SKU'}}]}};
console.log(JSON.stringify(h.renderMaterialFeeCostTable()));
""")
    assert "RMB 41941.38" in result and label in result
    assert "SAVED-SKU" in result and "UNSAVED-SKU" not in result
    assert "99999.00" not in result


def test_changed_source_hides_complete_saved_result_as_history():
    html = _frontend_result(FRONTEND_SETUP + """
h.escape=value=>String(value ?? '');
h.detailState.header={status:'Calculated',summary_snapshot:{comprehensive_cost:{summary:{is_complete:true,total_cost_rmb:'60'},items:[]}}};
state.materials={calculation_stale:true};
console.log(JSON.stringify(h.renderMaterialFeeCostTable()));
""")
    assert '待重新试算' in html and '以下为历史结果' in html
    assert '>完整成本<' not in html


def test_cancel_ai_discards_run_and_closes_progress():
    result = _frontend_result(FRONTEND_SETUP + """
state.aiFill={runId:'RUN',status:'QUEUED'};
let hidden=false,calls=[];state.aiProgressDialog={hide(){hidden=true}};
h.call=async(endpoint,args)=>{calls.push([endpoint,args]);return {ok:true}};
h.loadMaterialFeeWorkspace=async()=>{};
await h.discardMaterialAIFill();
console.log(JSON.stringify({hidden,calls,fill:state.aiFill}));
""")
    assert result['hidden'] and result['fill'] is None
    assert result['calls'][0][0].endswith('.discard_source_ai_review')


@pytest.mark.parametrize("saved_current", [False, True])
def test_fee_inclusion_distinguishes_available_fees_from_saved_calculation(saved_current):
    result = _frontend_result(FRONTEND_SETUP + f"""
h.escape=value=>String(value ?? '');
const preview={{read_only:true,included_fees:[{{fee_key:'import_tax'}}]}};
state.preview=preview;
h.detailState.header={json.dumps({"status": "Calculated" if saved_current else "Dirty"})};
if({str(saved_current).lower()})h.detailState.header.summary_snapshot={{comprehensive_cost:preview}};
console.log(JSON.stringify(h.renderMaterialFeeRow({{logical_fee_key:'import_tax',amount_status:'ACTUAL',amount:20000,currency:'MXN'}})));
""")
    assert ("已计入试算" in result) == saved_current
    assert ("可计入 · 待试算" in result) != saved_current


def test_successful_trial_refreshes_header_before_rendering_result():
    result = _frontend_result(FRONTEND_SETUP + FRONTEND_CONFIRM_SETUP + """
h.detailState.header.status='Dirty';
const events=[];
h.renderDetailShell=()=>events.push(['header',h.detailState.header.status,h.detailState.expectedModified]);
h.updateEditLeaseStatus=()=>events.push(['lease',h.detailState.editToken]);
h.renderMaterialFeeWorkspace=()=>events.push(['result',state.preview.summary.total_cost_rmb]);
h.call=async()=>saved;
await h.confirmCostTrialAI();
console.log(JSON.stringify(events));
""")
    assert result == [["header", "Calculated", "m2"], ["lease", "T"], ["result", "120"]]


def test_start_trial_uses_defaults_then_waits_for_explicit_confirmation():
    result = _frontend_result(FRONTEND_SETUP + """
h.renderCostTrialAIProgress=()=>{};
h.openCostTrialAIReviewDialog=()=>{h.opened=true};h.renderDetailShell=()=>{};h.updateEditLeaseStatus=()=>{};
const calls=[];h.call=async(endpoint,args)=>{calls.push({endpoint,args});
  if(endpoint.endsWith('start_cost_trial_ai_review'))return {ok:true,run_id:'RUN',status:'READY',progress_revision:0};
  if(endpoint.endsWith('get_cost_trial_ai_review_status'))return {ok:true,run_id:'RUN',status:'READY',draft:{
    fee_suggestions:[{suggestion_id:'S',requires_user_choice:true,blocked:false,default_basis:'goods_value'}],
    default_selections:[{suggestion_id:'S',basis:'goods_value',reason:''}]
  }};
  if(endpoint.endsWith('preview_cost_trial'))return {ok:true,preview_token:'P',summary:{total_cost_rmb:'130'}};
  throw new Error('unexpected endpoint '+endpoint);
};
await h.refreshMaterialFeeCostPreview();
console.log(JSON.stringify({calls,opened:h.opened===true,trial:state.costTrialAI,savedPreview:state.preview||null}));
""")
    assert [row["endpoint"].split(".")[-1] for row in result["calls"]] == [
        "start_cost_trial_ai_review",
        "get_cost_trial_ai_review_status",
        "preview_cost_trial",
    ]
    assert result["opened"] is True
    assert result["trial"]["status"] == "READY"
    assert result["trial"]["preview"]["summary"]["total_cost_rmb"] == "130"
    assert result["savedPreview"] is None


def test_automatic_trial_ignores_choices_from_a_previous_hidden_adjustment_dialog():
    result = _frontend_result(FRONTEND_SETUP + """
const stale={val:()=> 'gross_weight',attr:name=>({'data-suggestion-id':'OLD','data-recommended-basis':'gross_weight'}[name]||'')};
global.$=value=>value;
state.costTrialDialog={hide(){this.hidden=true},$wrapper:{find(){return {each(callback){callback(0,stale)}}}}};
h.renderDetailShell=()=>{};h.updateEditLeaseStatus=()=>{};
let previewSelections=null;h.call=async(endpoint,args)=>{
  if(endpoint.endsWith('start_cost_trial_ai_review'))return {ok:true,run_id:'RUN',status:'READY',progress_revision:0};
  if(endpoint.endsWith('get_cost_trial_ai_review_status'))return {ok:true,run_id:'RUN',status:'READY',draft:{
    fee_suggestions:[{suggestion_id:'NEW',requires_user_choice:true,blocked:false,default_basis:'goods_value'}],
    default_selections:[{suggestion_id:'NEW',basis:'goods_value',reason:''}]
  }};
  if(endpoint.endsWith('preview_cost_trial')){previewSelections=JSON.parse(args.selections);return {ok:true,preview_token:'P'}};
  return saved;
};
await h.refreshMaterialFeeCostPreview();
console.log(JSON.stringify({previewSelections,dialog:state.costTrialDialog}));
""")

    assert result["previewSelections"] == [
        {"suggestion_id": "NEW", "basis": "goods_value", "reason": ""}
    ]
    assert result["dialog"] is None


def test_trial_shows_ai_stage_without_opening_dialog_while_deepseek_runs():
    result = _frontend_result(FRONTEND_SETUP + """
let releaseStatus;let opens=0;const stages=[];h.openCostTrialAIReviewDialog=()=>{opens+=1};
h.renderCostTrialAIReviewDialog=()=>{};h.renderDetailShell=()=>{};h.updateEditLeaseStatus=()=>{};
h.updateMaterialFeeWriteControls=(current)=>{stages.push(current.costTrialAI?.actionStage||'')};
h.call=async(endpoint)=>{
  if(endpoint.endsWith('start_cost_trial_ai_review'))return {ok:true,run_id:'RUN',status:'QUEUED',progress_revision:0,ai_candidate_count:2};
  if(endpoint.endsWith('get_cost_trial_ai_review_status'))return await new Promise(resolve=>{releaseStatus=resolve});
  if(endpoint.endsWith('preview_cost_trial'))return {ok:true,preview_token:'P'};
  if(endpoint.endsWith('confirm_cost_trial'))return saved;
};
const running=h.refreshMaterialFeeCostPreview();await new Promise(resolve=>setImmediate(resolve));
const before={opens,status:state.costTrialAI.status,stage:state.costTrialAI.actionStage};
releaseStatus({ok:true,run_id:'RUN',status:'READY',progress_revision:1,draft:{fee_suggestions:[],default_selections:[]}});
await running;
console.log(JSON.stringify({before,opens,status:state.costTrialAI.status,stages}));
""")

    assert result["before"] == {"opens": 0, "status": "QUEUED", "stage": "ai"}
    assert result["opens"] == 1
    assert result["status"] == "READY"
    assert "ai" in result["stages"]
    assert "preview" in result["stages"]


def test_trial_with_no_complete_basis_stops_before_preview_and_names_missing_data():
    result = _frontend_result(FRONTEND_SETUP + """
let opens=0;h.openCostTrialAIReviewDialog=()=>{opens+=1};
const calls=[];h.call=async(endpoint)=>{calls.push(endpoint.split('.').pop());
  if(endpoint.endsWith('start_cost_trial_ai_review'))return {ok:true,run_id:'RUN',status:'READY',progress_revision:0};
  return {ok:true,run_id:'RUN',status:'READY',draft:{fee_suggestions:[{
    suggestion_id:'S',expense_category:'国际快递费',blocked:true,
    missing_fields:['gross_weight_kg','chargeable_weight_kg'],available_alternatives:[]
  }],default_selections:[]}};
};
let error='';try{await h.refreshMaterialFeeCostPreview()}catch(exc){error=exc.message}
console.log(JSON.stringify({calls,opens,error,preview:state.preview}));
""")

    assert result["calls"] == [
        "start_cost_trial_ai_review",
        "get_cost_trial_ai_review_status",
    ]
    assert result["opens"] == 1
    assert "国际快递费" in result["error"]
    assert "毛重" in result["error"] and "计费重" in result["error"]


def test_trial_preview_and_confirmation_use_server_token_and_saved_result():
    result = _frontend_result(FRONTEND_SETUP + """
state.costTrialAI={runId:'RUN',status:'READY',draft:{fee_suggestions:[]},selections:[],preview:null};
h.collectCostTrialAISelections=()=>[{suggestion_id:'S',basis:'goods_value',reason:'确认'}];
const calls=[];h.call=async(endpoint,args)=>{calls.push({endpoint,args});
  if(endpoint.endsWith('preview_cost_trial'))return {ok:true,preview_token:'P',summary:{total_cost_rmb:'130'}};
  if(endpoint.endsWith('confirm_cost_trial'))return {...saved,trial_review:{run_id:'RUN',is_temporary:false}};
};
h.renderCostTrialAIReviewDialog=()=>{};h.renderDetailShell=()=>{};h.updateEditLeaseStatus=()=>{};
state.costTrialDialog={hide(){this.hidden=true}};
await h.previewCostTrialAI();await h.confirmCostTrialAI();
console.log(JSON.stringify({calls,hidden:state.costTrialDialog,modified:h.detailState.expectedModified,preview:state.preview}));
""")
    assert [row["endpoint"].split(".")[-1] for row in result["calls"]] == [
        "preview_cost_trial",
        "confirm_cost_trial",
    ]
    assert result["calls"][1]["args"]["preview_token"] == "P"
    assert result["calls"][1]["args"]["edit_token"] == "T"
    assert result["modified"] == "m2"
    assert result["preview"]["saved"] is True


def test_trial_confirmation_refreshes_preview_after_choice_changes():
    result = _frontend_result(FRONTEND_SETUP + """
state.costTrialAI={runId:'RUN',status:'READY',draft:{fee_suggestions:[]},
  selections:[{suggestion_id:'S',basis:'goods_value',reason:''}],preview:{preview_token:'P'}};
h.collectCostTrialAISelections=()=>[{suggestion_id:'S',basis:'gross_weight',reason:'改按重量'}];
h.renderCostTrialAIReviewDialog=()=>{};h.renderDetailShell=()=>{};h.updateEditLeaseStatus=()=>{};
const calls=[];h.call=async(endpoint,args)=>{calls.push({endpoint,args});
  if(endpoint.endsWith('preview_cost_trial'))return {ok:true,preview_token:'P2',summary:{total_cost_rmb:'130'}};
  return saved;
};
await h.confirmCostTrialAI();
console.log(JSON.stringify({calls}));
""")

    assert [row["endpoint"].split(".")[-1] for row in result["calls"]] == [
        "preview_cost_trial",
        "confirm_cost_trial",
    ]
    assert result["calls"][1]["args"]["preview_token"] == "P2"


def test_trial_confirmation_previews_then_saves_in_one_click():
    result = _frontend_result(FRONTEND_SETUP + """
state.costTrialAI={runId:'RUN',status:'READY',draft:{fee_suggestions:[]},selections:[],preview:null};
h.collectCostTrialAISelections=()=>[{suggestion_id:'S',basis:'goods_value',reason:''}];
h.renderCostTrialAIReviewDialog=()=>{};h.renderDetailShell=()=>{};h.updateEditLeaseStatus=()=>{};
const calls=[];h.call=async(endpoint,args)=>{calls.push({endpoint,args});
  if(endpoint.endsWith('preview_cost_trial'))return {ok:true,preview_token:'P',summary:{total_cost_rmb:'130'}};
  if(endpoint.endsWith('confirm_cost_trial'))return saved;
  throw new Error('unexpected endpoint '+endpoint);
};
const dialog=state.costTrialDialog={hide(){this.hidden=true}};
await h.confirmCostTrialAI();
console.log(JSON.stringify({calls,hidden:dialog.hidden,dialogCleared:state.costTrialDialog===null,confirming:state.costTrialAI.confirming}));
""")

    assert [row["endpoint"].split(".")[-1] for row in result["calls"]] == [
        "preview_cost_trial",
        "confirm_cost_trial",
    ]
    assert result["calls"][1]["args"]["preview_token"] == "P"
    assert result["hidden"] is True
    assert result["dialogCleared"] is True
    assert result["confirming"] is False


def test_trial_preview_failure_never_calls_save_and_keeps_choices():
    result = _frontend_result(FRONTEND_SETUP + """
const choices=[{suggestion_id:'S',basis:'gross_weight',reason:'改按重量'}];
state.costTrialAI={runId:'RUN',status:'READY',draft:{fee_suggestions:[]},selections:choices,preview:null};
h.collectCostTrialAISelections=()=>choices;h.renderCostTrialAIReviewDialog=()=>{};
const calls=[];h.call=async(endpoint)=>{calls.push(endpoint.split('.').pop());throw new Error('预览失败')};
let error='';try{await h.confirmCostTrialAI()}catch(exc){error=exc.message}
console.log(JSON.stringify({calls,error,selections:state.costTrialAI.selections,preview:state.costTrialAI.preview,confirming:state.costTrialAI.confirming}));
""")

    assert result["calls"] == ["preview_cost_trial"]
    assert result["error"] == "预览失败"
    assert result["selections"] == [
        {"suggestion_id": "S", "basis": "gross_weight", "reason": "改按重量"}
    ]
    assert result["preview"] is None
    assert result["confirming"] is False


def test_trial_choice_change_while_preview_is_running_never_saves_old_selection():
    result = _frontend_result(FRONTEND_SETUP + """
let choices=[{suggestion_id:'S',basis:'goods_value',reason:''}];
state.costTrialAI={runId:'RUN',status:'READY',draft:{fee_suggestions:[]},selections:[],preview:null};
h.collectCostTrialAISelections=()=>choices;h.renderCostTrialAIReviewDialog=()=>{};
const calls=[];h.call=async(endpoint)=>{calls.push(endpoint.split('.').pop());
  choices=[{suggestion_id:'S',basis:'gross_weight',reason:'改按重量'}];
  return {ok:true,preview_token:'OLD'};
};
let error='';try{await h.confirmCostTrialAI()}catch(exc){error=exc.message}
console.log(JSON.stringify({calls,error,preview:state.costTrialAI.preview,selections:state.costTrialAI.selections}));
""")

    assert result["calls"] == ["preview_cost_trial"]
    assert "变化" in result["error"]
    assert result["preview"] is None
    assert result["selections"] == [
        {"suggestion_id": "S", "basis": "gross_weight", "reason": "改按重量"}
    ]


def test_trial_confirmation_failure_keeps_preview_and_choices_for_retry():
    result = _frontend_result(FRONTEND_SETUP + """
const choices=[{suggestion_id:'S',basis:'goods_value',reason:''}];
state.costTrialAI={runId:'RUN',status:'READY',draft:{fee_suggestions:[]},selections:choices,preview:{preview_token:'P'}};
h.collectCostTrialAISelections=()=>choices;h.renderCostTrialAIReviewDialog=()=>{};
h.call=async()=>{throw new Error('保存失败')};
let error='';try{await h.confirmCostTrialAI()}catch(exc){error=exc.message}
console.log(JSON.stringify({error,preview:state.costTrialAI.preview,selections:state.costTrialAI.selections,confirming:state.costTrialAI.confirming}));
""")

    assert result["error"] == "保存失败"
    assert result["preview"] == {"preview_token": "P"}
    assert result["selections"] == [
        {"suggestion_id": "S", "basis": "goods_value", "reason": ""}
    ]
    assert result["confirming"] is False


def test_trial_choice_change_invalidates_existing_preview_token():
    result = _frontend_result(FRONTEND_SETUP + """
state.costTrialAI={runId:'RUN',status:'READY',preview:{preview_token:'OLD'},selections:[{suggestion_id:'S',basis:'goods_value',reason:''}]};
let dialogRenders=0;h.renderCostTrialAIReviewDialog=()=>{dialogRenders+=1};
h.invalidateCostTrialAIPreview();
console.log(JSON.stringify({preview:state.costTrialAI.preview,selections:state.costTrialAI.selections,renders:dialogRenders}));
""")

    assert result == {
        "preview": None,
        "selections": [{"suggestion_id": "S", "basis": "goods_value", "reason": ""}],
        "renders": 1,
    }


def test_trial_primary_enables_after_basis_without_requiring_reason():
    result = _frontend_result(FRONTEND_SETUP + """
const basis={value:'',attrs:{'data-suggestion-id':'S','data-recommended-basis':'volume'},val(){return this.value},attr(name){return this.attrs[name]||''}};
global.$=value=>value;
const collection=element=>({each(callback){callback(0,element)}});
const wrapper={find(selector){return selector==='[data-cost-trial-basis]'?collection(basis):{each(){},prop(){return this}}}};
const button={disabled:null,label:'',prop(name,value){if(name==='disabled')this.disabled=value;return this},text(value){this.label=value;return this}};
state.costTrialAI={runId:'RUN',status:'READY',draft:{fee_suggestions:[{suggestion_id:'S',recommended_basis:'volume',blocked:false}]},preview:null,selections:[]};
state.costTrialDialog={$wrapper:wrapper,get_primary_btn:()=>button};
h.updateCostTrialAIPrimaryAction();const missingBasis=button.disabled;
basis.value='gross_weight';h.updateCostTrialAIPrimaryAction();const missingReason=button.disabled;
console.log(JSON.stringify({missingBasis,missingReason,complete:button.disabled,label:button.label}));
""")

    assert result == {
        "missingBasis": True,
        "missingReason": False,
        "complete": False,
        "label": "应用调整并试算",
    }


def test_tax_evidence_total_mismatch_is_visible_but_keeps_manual_basis_controls():
    html = _frontend_result(FRONTEND_SETUP + """
h.escape=value=>String(value ?? '');
state.costTrialAI={draft:{model:'deepseek-test',fee_suggestions:[{
  suggestion_id:'S',fee_key:'import_tax',expense_category:'进口税费',currency:'RMB',amount:'100',
  recommended_basis:'goods_value',recommended_basis_label:'货值',recommended_basis_available:true,
  confidence:'0.8',reason:'按货值',available_alternatives:[{basis:'goods_value',label:'货值'}],
  evidence_locked:false,evidence_issue:'TOTAL_MISMATCH',evidence_amount_rmb:'90.00',blocked:false
}]}};
console.log(JSON.stringify(h.renderCostTrialAIReview()));
""")

    assert "凭证分项合计" in html and "费用金额" in html
    assert 'data-cost-trial-basis' in html
    assert 'data-cost-trial-reason' not in html


def test_adjustment_starts_in_reuse_only_mode_and_never_requests_ai():
    result = _frontend_result(FRONTEND_SETUP + """
let opens=0;h.openCostTrialAIReviewDialog=()=>{opens+=1};
const calls=[];h.call=async(endpoint,args)=>{calls.push({endpoint,args});
  if(endpoint.endsWith('start_cost_trial_ai_review'))return {ok:true,run_id:'ADJUST',status:'READY',progress_revision:0};
  if(endpoint.endsWith('get_cost_trial_ai_review_status'))return {ok:true,run_id:'ADJUST',status:'READY',draft:{
    fee_suggestions:[{suggestion_id:'S',default_basis:'gross_weight',blocked:false}],
    default_selections:[{suggestion_id:'S',basis:'gross_weight',reason:''}]
  }};
  throw new Error('unexpected endpoint '+endpoint);
};
await h.openCostTrialAdjustment();
console.log(JSON.stringify({calls,opens,trial:state.costTrialAI}));
""")

    assert [row["endpoint"].split(".")[-1] for row in result["calls"]] == [
        "start_cost_trial_ai_review",
        "get_cost_trial_ai_review_status",
    ]
    assert result["calls"][0]["args"]["reuse_only"] == 1
    assert result["calls"][0]["args"]["force"] == 0
    assert result["opens"] == 1
    assert result["trial"]["dialogMode"] == "adjust"


def test_saved_result_shows_decision_source_counts_and_adjustment_action():
    html = _frontend_result(FRONTEND_SETUP + """
h.escape=value=>String(value ?? '');
h.detailState.header={status:'Calculated',summary_snapshot:{comprehensive_cost:{
  summary:{total_cost_rmb:'120'},items:[],
  trial_review:{fee_choices:[
    {decision_source:'REUSED'},{decision_source:'AI'},{decision_source:'AI'},
    {decision_source:'SYSTEM_FALLBACK'},{decision_source:'EVIDENCE'}
  ]}
}}};
console.log(JSON.stringify(h.renderMaterialFeeCostTable()));
""")

    assert 'data-action="mf-adjust-cost"' in html
    assert "沿用上次 1" in html
    assert "AI 判断 2" in html
    assert "系统兜底 1" in html
    assert "凭证优先 1" in html


def test_ai_trial_dialog_shows_server_calculated_alternative_differences():
    html = _frontend_result(FRONTEND_SETUP + """
h.escape=value=>String(value ?? '');
state.costTrialAI={draft:{model:'deepseek-test',fee_suggestions:[{
  suggestion_id:'S',fee_key:'freight',expense_category:'国际运费',currency:'RMB',amount:'100',
  recommended_basis:'volume',recommended_basis_label:'体积',recommended_basis_available:false,
  confidence:'0.8',reason:'抛货',missing_fields:['volume_m3'],blocked:false,evidence_locked:false,
  available_alternatives:[
    {basis:'goods_value',label:'货值',allocation_preview:[{item_key:'A',amount_rmb:'25.00'},{item_key:'B',amount_rmb:'75.00'}]},
    {basis:'gross_weight',label:'毛重',allocation_preview:[{item_key:'A',amount_rmb:'75.00'},{item_key:'B',amount_rmb:'25.00'}]}
  ]
}]}};
console.log(JSON.stringify(h.renderCostTrialAIReview()));
""")

    assert "可选口径的逐行试算差异" in html
    assert "货值：RMB 25.00 / 75.00" in html
    assert "毛重：RMB 75.00 / 25.00" in html


def test_ai_trial_dialog_shows_calculation_day_fx_source_and_values():
    html = _frontend_result(FRONTEND_SETUP + """
h.escape=value=>String(value ?? '');
state.costTrialAI={status:'READY',draft:{fee_suggestions:[],fx_resolution:{
  ok:true,calculation_date:'2026-09-20',fx_usd_to_rmb:6.71,fx_rmb_to_mxn:2.564103,is_estimated:false,
  rates:{USD:{source:'fx_api',source_label:'当日汇率 API',rate_date:'2026-09-20'},MXN:{source:'fx_api',source_label:'当日汇率 API',rate_date:'2026-09-20'}},
  blocking_errors:[]
}}};
console.log(JSON.stringify(h.renderCostTrialAIReview()));
""")

    assert "汇率日期 2026-09-20" in html
    assert "当日汇率 API" in html
    assert "1 USD = 6.71 RMB" in html
    assert "1 RMB = 2.564103 MXN" in html


def test_ai_trial_review_content_does_not_render_a_second_footer():
    html = _frontend_result(FRONTEND_SETUP + """
h.escape=value=>String(value ?? '');
state.costTrialAI={status:'READY',draft:{fee_suggestions:[],default_selections:[]}};
console.log(JSON.stringify(h.renderCostTrialAIReview()));
""")

    assert "<footer" not in html
    assert 'data-action="cost-trial-retry"' not in html


def test_ai_trial_dialog_marks_historical_fx_estimated():
    html = _frontend_result(FRONTEND_SETUP + """
h.escape=value=>String(value ?? '');
state.costTrialAI={status:'READY',draft:{fee_suggestions:[],fx_resolution:{
  ok:true,calculation_date:'2026-09-20',fx_usd_to_rmb:6.5,fx_rmb_to_mxn:2.5,is_estimated:true,
  rates:{USD:{source:'historical_currency_exchange',rate_date:'2024-01-02'},MXN:{source:'historical_currency_exchange',rate_date:'2023-12-29'}},
  blocking_errors:[]
}}};
console.log(JSON.stringify(h.renderCostTrialAIReview()));
""")

    assert "历史汇率暂估" in html
    assert "2024-01-02" in html
    assert "2023-12-29" in html


def test_ai_trial_legacy_rate_warns_and_does_not_claim_a_source_date():
    html = _frontend_result(FRONTEND_SETUP + """
h.escape=value=>String(value ?? '');
state.costTrialAI={status:'READY',draft:{fee_suggestions:[],fx_resolution:{
  ok:true,calculation_date:'2026-10-30',date_source_label:'真实付款日',
  fx_usd_to_rmb:6.6583,fx_rmb_to_mxn:2.837751,legacy_default_currencies:['USD','MXN'],
  rates:{USD:{source:'currency_exchange'},MXN:{source:'currency_exchange'}},blocking_errors:[]
}}};
console.log(JSON.stringify(h.renderCostTrialAIReview()));
""")

    assert "来源日期未记录" in html
    assert "检测到历史默认汇率" in html
    assert "实际采用 2026-10-30" not in html


def test_ai_trial_missing_fx_disables_confirmation_and_shows_chinese_error():
    result = _frontend_result(FRONTEND_SETUP + """
h.escape=value=>String(value ?? '');
state.costTrialAI={status:'READY',draft:{fee_suggestions:[],default_selections:[],fx_resolution:{
  ok:false,calculation_date:'2026-09-20',fx_usd_to_rmb:null,fx_rmb_to_mxn:null,
  rates:{},blocking_errors:[{currency:'MXN',message:'未取得 2026-09-20 的 MXN 汇率，汇率库也没有可用历史值。'}]
}}};
console.log(JSON.stringify({ready:h.costTrialAIReadyToConfirm(),html:h.renderCostTrialAIReview()}));
""")

    assert result["ready"] is False
    assert "未取得 2026-09-20 的 MXN 汇率" in result["html"]


def test_retry_ai_keeps_revision_guards_and_opens_the_refreshed_review():
    result = _frontend_result(FRONTEND_SETUP + """
state.requestId=3;state.feeRequestId=4;state.inputRevision=5;
state.costTrialAI={runId:'OLD',status:'READY'};state.costTrialDialog={hide(){}};
const endpoints=[];h.call=async(endpoint)=>{endpoints.push(endpoint.split('.').pop());
  if(endpoint.endsWith('discard_cost_trial_ai_review'))return {ok:true,status:'DISCARDED'};
  if(endpoint.endsWith('start_cost_trial_ai_review'))return {ok:true,run_id:'NEW',status:'QUEUED',progress_revision:0};
  return {ok:true,run_id:'NEW',status:'READY',progress_revision:1,draft:{fee_suggestions:[]}};
};
h.openCostTrialAIReviewDialog=()=>{h.opened=true};
await h.retryCostTrialAI();
console.log(JSON.stringify({endpoints,opened:h.opened===true,trial:state.costTrialAI}));
""")

    assert result["endpoints"] == [
        "discard_cost_trial_ai_review",
        "start_cost_trial_ai_review",
        "get_cost_trial_ai_review_status",
    ]
    assert result["opened"] is True
    assert result["trial"]["requestId"] == 3
    assert result["trial"]["feeRequestId"] == 4
    assert result["trial"]["inputRevision"] == 5


def test_retry_ai_double_click_uses_one_discard_and_one_forced_start():
    result = _frontend_result(FRONTEND_SETUP + """
state.requestId=3;state.feeRequestId=4;state.inputRevision=5;
state.costTrialAI={runId:'OLD',status:'READY'};state.costTrialDialog={hide(){}};
let releaseDiscard;const endpoints=[];h.call=async(endpoint)=>{endpoints.push(endpoint.split('.').pop());
  if(endpoint.endsWith('discard_cost_trial_ai_review'))return await new Promise(resolve=>{releaseDiscard=resolve});
  if(endpoint.endsWith('start_cost_trial_ai_review'))return {ok:true,run_id:'NEW',status:'READY',progress_revision:0};
  return {ok:true,run_id:'NEW',status:'READY',progress_revision:1,draft:{fee_suggestions:[],default_selections:[]}};
};
h.openCostTrialAIReviewDialog=()=>{};
const first=h.retryCostTrialAI();const second=h.retryCostTrialAI();await new Promise(resolve=>setImmediate(resolve));
const before=[...endpoints];releaseDiscard({ok:true,status:'DISCARDED'});await Promise.all([first,second]);
console.log(JSON.stringify({before,endpoints,restarting:state.costTrialRestarting===true}));
""")

    assert result["before"] == ["discard_cost_trial_ai_review"]
    assert result["endpoints"] == [
        "discard_cost_trial_ai_review",
        "start_cost_trial_ai_review",
        "get_cost_trial_ai_review_status",
    ]
    assert result["restarting"] is False


def test_overview_recalculate_runs_after_visiting_material_workspace():
    result = _frontend_result(FRONTEND_SETUP + """
h.detailState.tab='overview';h.getActiveBatch=()=>h.batches[0];
const calls=[];h.call=async(endpoint,args)=>{calls.push({endpoint,args});
  if(endpoint.endsWith('get_batches'))return {ok:true,items:[{...h.batches[0],review_state:'ready'}],total:1,page:1};
  return saved};
const errors=[];h.showError=error=>errors.push(error.message);h.recordUsage=()=>{};
h.viewState={task:'pending',screen:'detail',batch:'B',tab:'overview',page:1};
h.filters={review_status:'pending',review_warning:'',issue:''};h.findBatch=name=>h.batches.find(row=>row.name===name);
h.replaceViewState=values=>{h.viewState={...h.viewState,...values}};h.loadBatches=async()=>{};
h.renderDetailShell=()=>{};h.switchDetailTab=async()=>{};
h.applyRecalculateSummary=()=>{};h.refreshDetailSummary=async()=>{};
await h.recalculate();console.log(JSON.stringify({calls,errors,modified:h.detailState.expectedModified}));
""")
    assert result["errors"] == []
    assert len(result["calls"]) == 2
    assert result["calls"][0]["endpoint"].endswith("recalculate_batch")
    assert result["calls"][1]["endpoint"].endswith("get_batches")
    assert result["calls"][1]["args"]["task"] == "cost"
    assert result["modified"] == "m2"


def test_overview_recalculate_reports_pending_material_drafts():
    result = _frontend_result(FRONTEND_SETUP + """
h.detailState.tab='overview';h.getActiveBatch=()=>h.batches[0];
state.materialDrafts['I:goods_value']={itemName:'I',fieldname:'goods_value',value:'200'};
let calls=0;h.call=async()=>{calls+=1;return saved};
const errors=[];h.showError=error=>errors.push(error.message);h.recordUsage=()=>{};
await h.recalculate();console.log(JSON.stringify({calls,errors}));
""")
    assert result["calls"] == 0
    assert result["errors"] and "资料与费用" in result["errors"][0]


def test_overview_recalculate_does_not_duplicate_an_inflight_saved_trial():
    result = _frontend_result(FRONTEND_SETUP + FRONTEND_CONFIRM_SETUP + """
let resolve;const calls=[];h.call=async(endpoint,args)=>{calls.push({endpoint,args});
  if(endpoint.endsWith('confirm_cost_trial'))return await new Promise(r=>{resolve=r});
  return {...saved,batch_modified:'m3'};
};
h.getActiveBatch=()=>h.batches[0];const errors=[];h.showError=error=>errors.push(error.message);
h.recordUsage=()=>{};h.applyRecalculateSummary=()=>{};h.refreshDetailSummary=async()=>{};
const trial=h.confirmCostTrialAI();await new Promise(r=>setImmediate(r));
h.detailState.tab='overview';const recalculating=h.recalculate();await new Promise(r=>setImmediate(r));
const callsBefore=calls.length;resolve(saved);await Promise.all([trial,recalculating]);
console.log(JSON.stringify({callsBefore,calls,errors,modified:h.detailState.expectedModified}));
""")
    assert result["callsBefore"] == 1
    assert result["errors"] == []
    assert len(result["calls"]) == 1
    assert result["calls"][0]["endpoint"].endswith("confirm_cost_trial")
    assert result["modified"] == "m2"


def test_saved_trial_acknowledges_mutation_while_preserving_new_draft():
    result = _frontend_result(FRONTEND_SETUP + FRONTEND_CONFIRM_SETUP + """
let resolve;h.call=()=>new Promise(r=>{resolve=r});
const calculating=h.confirmCostTrialAI();await new Promise(r=>setImmediate(r));
state.inputRevision+=1;state.materialDrafts['I:goods_value']={value:'200'};h.detailState.dirty=true;
resolve(saved);const rendered=await calculating;
console.log(JSON.stringify({rendered,renders,modified:h.detailState.expectedModified,dirty:h.detailState.dirty,draft:state.materialDrafts['I:goods_value'],preview:state.preview}));
""")
    assert result["rendered"] is False and result["renders"] == 0
    assert result["modified"] == "m2"
    assert result["dirty"] is True
    assert result["draft"] == {"value": "200"}
    assert result["preview"]["saved"] is True


def test_inline_write_waits_for_trial_acknowledgement_before_reading_revision():
    result = _frontend_result(FRONTEND_SETUP + FRONTEND_CONFIRM_SETUP + """
let resolve;h.call=()=>new Promise(r=>{resolve=r});
const calculating=h.confirmCostTrialAI();await new Promise(r=>setImmediate(r));
let writeRevision=null;const writing=h.trackMaterialFeeWrite(async()=>{writeRevision=h.detailState.expectedModified});
await new Promise(r=>setImmediate(r));const before=writeRevision;
resolve(saved);await Promise.all([calculating,writing]);
console.log(JSON.stringify({before,writeRevision}));
""")
    assert result == {"before": None, "writeRevision": "m2"}


def test_saved_trial_does_not_replace_a_newer_loaded_result():
    result = _frontend_result(FRONTEND_SETUP + FRONTEND_CONFIRM_SETUP + """
let resolve;h.call=()=>new Promise(r=>{resolve=r});
const calculating=h.confirmCostTrialAI();await new Promise(r=>setImmediate(r));
h.detailState.expectedModified='m3';h.detailState.header={modified:'m3',summary_snapshot:{total_cost_rmb:'300'}};
h.batches[0].modified='m3';state.preview={saved:true,batch_modified:'m3'};
resolve(saved);const rendered=await calculating;
console.log(JSON.stringify({rendered,renders,modified:h.detailState.expectedModified,header:h.detailState.header,preview:state.preview}));
""")
    assert result["rendered"] is False and result["renders"] == 0
    assert result["modified"] == "m3"
    assert result["header"]["summary_snapshot"]["total_cost_rmb"] == "300"
    assert result["preview"]["batch_modified"] == "m3"


def test_saved_trial_does_not_clear_edits_started_on_another_tab():
    result = _frontend_result(FRONTEND_SETUP + FRONTEND_CONFIRM_SETUP + """
let resolve;h.call=()=>new Promise(r=>{resolve=r});
const calculating=h.confirmCostTrialAI();await new Promise(r=>setImmediate(r));
h.detailState.tab='items';h.detailState.dirty=true;
resolve(saved);await calculating;
console.log(JSON.stringify({modified:h.detailState.expectedModified,dirty:h.detailState.dirty}));
""")
    assert result == {"modified": "m2", "dirty": True}


@pytest.mark.parametrize("navigation", ["tab", "version", "batch"])
def test_saved_trial_acknowledgement_follows_batch_and_version(navigation):
    change = {
        "tab": "h.detailState.tab='overview';",
        "version": "h.detailState.versionName='V2';h.detailState.expectedModified='m3';h.batches[0].current_version='V2';",
        "batch": "h.detailState.batchName='B2';h.detailState.versionName='V2';h.detailState.expectedModified='n1';",
    }[navigation]
    result = _frontend_result(FRONTEND_SETUP + FRONTEND_CONFIRM_SETUP + """
let resolve;h.call=()=>new Promise(r=>{resolve=r});
const calculating=h.confirmCostTrialAI();await new Promise(r=>setImmediate(r));
""" + change + """
resolve(saved);await calculating;
console.log(JSON.stringify({modified:h.detailState.expectedModified,listModified:h.batches[0].modified,renders}));
""")
    assert result["renders"] == 0
    assert result["modified"] == {"tab": "m2", "version": "m3", "batch": "n1"}[navigation]
    assert result["listModified"] == ("m1" if navigation == "version" else "m2")
