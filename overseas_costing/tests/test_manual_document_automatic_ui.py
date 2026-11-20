"""Upload continuation exercises the real workbench methods without a server."""

import json

import pytest

from overseas_costing.tests.test_workbench_frontend_state import PARTS, _fee_workspace_result


SETUP = r"""
global.ocTranslate=(message,values=[])=>message.replace(/\{(\d+)\}/g,(_,index)=>values[index]??'');
const workspace=new Harness();
const state={aiFill:null,aiPendingReady:null,aiRunGeneration:0};
workspace.materialFeeState=state;
workspace.detailState={batchName:'B1',versionName:'V1',tab:'documents'};
workspace.ensureMaterialFeeState=()=>state;
workspace.escape=(value)=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
workspace.updateMaterialAIProgressSurface=()=>{};
global.window={setTimeout:(resolve)=>resolve()};
"""


def result(script):
    manual_path = json.dumps(str(PARTS / "65-manual-documents.js"))
    return _fee_workspace_result(
        f"const manualSource=fs.readFileSync({manual_path},'utf8');"
        "const Manual=Function('return class Manual {'+manualSource+'}')();"
        "Object.defineProperties(Harness.prototype,Object.getOwnPropertyDescriptors(Manual.prototype));"
        + SETUP + script
    )


@pytest.mark.parametrize("manual_note", [False, True])
@pytest.mark.parametrize("automatic", [False, True])
def test_both_registration_paths_follow_processing_and_keep_legacy_responses(manual_note, automatic):
    output = result(r"""
const response={ok:true,message:'SAVED',attachment:{name:'A1'}};
if(AUTOMATIC)response.processing={status:'QUEUED',run_id:'R1',changed_count:0,blockers:[]};
workspace.call=async()=>response;
workspace.detectManualDocumentLogisticsType=()=> 'SEA';
let polls=0;
workspace.pollMaterialAIFill=async()=>{polls+=1};
if(MANUAL_NOTE)await workspace.saveManualGapFillRecord({name:'B1',current_version:'V1'},{hide:()=>{}},null,{manualNote:'采购凭据已补',slotCode:'goods'});
else await workspace.registerManualDocumentAttachment({name:'B1',current_version:'V1'},null,'SEA',{code:'goods',label:'采购凭据'},{file_url:'/private/files/bill.pdf'});
console.log(JSON.stringify({polls,automatic:state.aiFill?.automatic_processing?.status||'',alerts:alerts.map(row=>row.message)}));
""".replace("AUTOMATIC", str(automatic).lower()).replace("MANUAL_NOTE", str(manual_note).lower()))
    assert output["polls"] == int(automatic)
    assert output["automatic"] == ("QUEUED" if automatic else "")
    assert output["alerts"] == (["资料已收到，正在自动识别、补缺并试算。"] if automatic else ["SAVED"])


def test_automatic_ready_and_unchanged_updates_continue_until_calculated_without_manual_preview():
    output = result(r"""
state.aiFill={status:'QUEUED',automatic_processing:{status:'QUEUED'},progress_revision:0};
let opens=0,refreshes=0;
workspace.showMaterialAIReadyDraft=()=>{opens+=1};
workspace.loadMaterialFeeWorkspace=async()=>{refreshes+=1};
const replies=[
 {ok:true,status:'RUNNING',progress_revision:1,automatic_processing:{}},
 {ok:true,status:'READY',progress_revision:2,automatic_processing:{status:'PENDING',changed_count:0}},
 {ok:true,status:'APPLIED',progress_revision:3,unchanged:true,automatic_processing:{status:'RUNNING',changed_count:2}},
 {ok:true,status:'APPLIED',progress_revision:4,automatic_processing:{status:'CALCULATED',changed_count:2,blockers:[]}},
];
const revisions=[];
workspace.call=async(_method,args)=>{revisions.push(args.after_revision);return replies.shift()};
await workspace.pollMaterialAIFill(state,'B1','V1','R1');
console.log(JSON.stringify({opens,refreshes,revisions,pending:state.aiPendingReady,status:state.aiFill.automatic_processing.status,notice:alerts.at(-1).message}));
""")
    assert output == {"opens": 0, "refreshes": 1, "revisions": [0, 1, 2, 3], "pending": None,
                      "status": "CALCULATED", "notice": "资料已处理并完成试算"}


def test_manual_ready_with_empty_automatic_metadata_still_opens_native_preview():
    output = result(r"""
state.aiFill={status:'RUNNING'};
let opens=0;
workspace.call=async()=>({ok:true,status:'READY',run_id:'R1',automatic_processing:{}});
workspace.initializeMaterialAIDraft=(fill)=>({...fill,selections:new Set()});
workspace.renderMaterialAIReviewDialog=()=>{opens+=1};
workspace.materialAIReviewCanUseSelection=()=>false;
await workspace.pollMaterialAIFill(state,'B1','V1','R1');
console.log(JSON.stringify({opens,pending:state.aiPendingReady,visible:state.aiFill.reviewDialogVisible,alerts}));
""")
    assert output == {"opens": 1, "pending": None, "visible": True, "alerts": []}


def test_explicit_successor_continues_polling_with_a_fresh_revision_before_stale_terminal():
    output = result(r"""
state.aiFill={status:'RUNNING',runId:'R1',progress_revision:7,automatic_processing:{status:'RUNNING'}};
let refreshes=0;
workspace.showMaterialAIReadyDraft=()=>{throw Error('must not open manual preview')};
workspace.loadMaterialFeeWorkspace=async()=>{refreshes+=1};
const replies=[
 {ok:true,status:'STALE',unchanged:true,progress_revision:8,replacement_run_id:'R2'},
 {ok:true,status:'APPLIED',progress_revision:1,automatic_processing:{status:'CALCULATED',run_id:'R2',changed_count:3}},
];
const requests=[];
workspace.call=async(_method,args)=>{requests.push(args);return replies.shift()};
await workspace.pollMaterialAIFill(state,'B1','V1','R1');
console.log(JSON.stringify({requests,refreshes,runId:state.aiFill.runId,status:state.aiFill.automatic_processing.status,polling:state.aiFill.polling,pending:state.aiPendingReady,notices:alerts.map(row=>row.message)}));
""")
    assert output == {
        "requests": [{"batch_name": "B1", "run_id": "R1", "after_revision": 7},
                     {"batch_name": "B1", "run_id": "R2", "after_revision": 0}],
        "refreshes": 1, "runId": "R2", "status": "CALCULATED", "polling": False,
        "pending": None, "notices": ["资料已处理并完成试算"],
    }


def test_empty_upload_processing_keeps_the_legacy_registration_flow():
    output = result("console.log(JSON.stringify({handled:workspace.followManualDocumentProcessing({name:'B1'},{}),alerts}));")
    assert output == {"handled": False, "alerts": []}


def test_leaving_documents_releases_polling_flag_so_returning_can_resume():
    output = result(r"""
state.aiFill={status:'RUNNING',automatic_processing:{status:'RUNNING'}};
workspace.call=async()=>{workspace.detailState.tab='overview';return {ok:true,status:'RUNNING'}};
await workspace.pollMaterialAIFill(state,'B1','V1','R1');
console.log(JSON.stringify({polling:state.aiFill.polling,alerts}));
""")
    assert output == {"polling": False, "alerts": []}


@pytest.mark.parametrize("native_status", ["FAILED", "STALE", "DISCARDED"])
def test_native_failure_stops_automatic_tracking_without_opening_manual_confirmation(native_status):
    output = result(r"""
state.aiFill={status:'RUNNING',automatic_processing:{status:'QUEUED'}};
let refreshes=0;
workspace.showMaterialAIReadyDraft=()=>{throw Error('must not open manual preview')};
workspace.loadMaterialFeeWorkspace=async()=>{refreshes+=1};
workspace.call=async()=>({ok:true,status:NATIVE_STATUS,automatic_processing:{},error_message:'请补充有效凭据'});
await workspace.pollMaterialAIFill(state,'B1','V1','R1');
console.log(JSON.stringify({refreshes,status:state.aiFill.automatic_processing.status,blockers:state.aiFill.automatic_processing.blockers}));
""".replace("NATIVE_STATUS", json.dumps(native_status)))
    assert output == {"refreshes": 1, "status": "REVIEW_REQUIRED", "blockers": [{"hint": "请补充有效凭据"}]}


def test_restored_ready_automatic_run_keeps_tracking_and_never_stages_a_manual_draft():
    output = result(r"""
state.requestId=1;
workspace.isMaterialFeeWorkspaceRequestCurrent=()=>true;
workspace.acceptMaterialAIClarification=()=>{};
workspace.renderMaterialFeeWorkspacePreservingPosition=()=>{};
workspace.call=async()=>({ok:true,status:'READY',run_id:'R1',automatic_processing:{status:'PENDING'}});
let polls=0;
workspace.pollMaterialAIFill=async()=>{polls+=1};
await workspace.restoreMaterialFeeWorkspaceAI(state,'B1','V1',1);
console.log(JSON.stringify({polls,pending:state.aiPendingReady,minimized:state.aiProgressMinimized}));
""")
    assert output == {"polls": 1, "pending": None, "minimized": True}


@pytest.mark.parametrize("extension", ["xlsx", "pdf"])
@pytest.mark.parametrize("automatic", [False, True])
def test_local_upload_skips_duplicate_manual_flow_only_when_automatic_processing_started(extension, automatic):
    output = result(r"""
workspace.getDetailBatch=()=>({name:'B1'});
let acquisitions=0;
workspace.ensureEditSession=async()=>{acquisitions+=1;return true};
workspace.detectManualDocumentLogisticsType=()=> 'SEA';
workspace.detailDocumentAdapter=()=>null;
workspace.localizeFrappeFileUploader=()=>{};
let upload;
frappe.ui={FileUploader:class{constructor(options){upload=options.on_success}}};
workspace.registerManualDocumentAttachment=async()=>({attachment:{name:'A1'},...(AUTOMATIC?{processing:{status:'QUEUED'}}:{})});
let dialogs=0,previews=0,restarts=0;
workspace.openWikiMaterialImportDialog=async()=>{dialogs+=1;return {}};
workspace.previewMaterialAttachmentSource=async()=>{previews+=1};
workspace.restartMaterialAIForChangedSources=()=>{restarts+=1};
workspace.openMaterialXlsxUploader();
await Promise.resolve();
upload({file_name:'packing.EXTENSION',file_url:'/private/files/packing'});
await Promise.resolve();await Promise.resolve();await Promise.resolve();
console.log(JSON.stringify({dialogs,previews,restarts,acquisitions}));
""".replace("AUTOMATIC", str(automatic).lower()).replace("EXTENSION", extension))
    assert output == {"dialogs": int(not automatic), "previews": int(not automatic and extension == "xlsx"),
                      "restarts": int(not automatic and extension == "pdf"), "acquisitions": 0}


def test_actual_material_edit_still_acquires_and_uses_the_native_lease():
    detail_path = json.dumps(str(PARTS / "82-detail-page.js"))
    output = result(
        f"const Detail=Function('return class Detail {{'+fs.readFileSync({detail_path},'utf8')+'}}')();"
        r"""
workspace.ensureEditSession=Detail.prototype.ensureEditSession;
workspace.getDetailBatch=()=>({name:'B1',modified:'M1'});
workspace.updateEditLeaseStatus=()=>{};
workspace.updateMaterialFeeExpectedModified=()=>{};
workspace.loadMaterialFeeWorkspace=async()=>{};
window.clearInterval=()=>{};window.setInterval=()=>1;
const requests=[];
workspace.call=async(method,args)=>{requests.push({method,args});return method.endsWith('.acquire')?{ok:true,edit_token:'TOKEN'}:{ok:true,changed_count:1}};
await workspace.applyMaterialPaste({hide:()=>{}},[{item_name:'I1',fieldname:'gross_weight_kg',value:'2'}]);
console.log(JSON.stringify({methods:requests.map(row=>row.method),token:requests[1].args.edit_token}));
""")
    assert output == {"methods": ["overseas_costing.api.edit_session.acquire",
                                  "overseas_costing.api.calculate.batch_update_items"], "token": "TOKEN"}


def test_automatic_results_show_missing_materials_and_escape_blocker_text_without_confirm_button():
    output = result(r"""
const processing={status:'NEEDS_MATERIALS',changed_count:2,blockers:[{code:'CURRENCY_REQUIRED',hint:'币种 <img src=x onerror=alert(1)>'}]};
state.aiFill={status:'READY',automatic_processing:processing};
const html=workspace.renderMaterialAIProgressDialogContent();
console.log(JSON.stringify({html,label:workspace.materialAutomaticProcessingMessage({status:'CALCULATED',blockers:[{hint:'缺采购金额'}]})}));
""")
    assert "采购金额、币种或对应凭据" in output["html"]
    assert "&lt;img" in output["html"] and "<img" not in output["html"]
    assert 'data-action="mf-ai-apply"' not in output["html"]
    assert output["label"] == "已完成试算，仍有资料待补"
