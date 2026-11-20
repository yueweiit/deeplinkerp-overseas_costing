"""Server document groups drive upload locations; file presence is not validation."""

import csv
import json

import pytest

from overseas_costing.tests.test_manual_document_automatic_ui import result
from overseas_costing.tests.test_workbench_frontend_state import PARTS, ROOT


@pytest.mark.parametrize("mode,expected", [
    ("SEA", ["sea_commercial_invoice"]),
    ("AIR", ["air_commercial_invoice"]),
    ("EXPRESS", ["express_commercial_invoice", "express_payment_voucher"]),
])
@pytest.mark.parametrize("field", ["goods_value", "unit_price", "purchase_currency"])
def test_purchase_fields_open_existing_invoice_and_payment_upload_locations(mode, expected, field):
    output = result(
        "workspace.detectManualDocumentLogisticsType=(batch)=>batch.transport_mode;"
        f"console.log(JSON.stringify(workspace.manualDocumentSlotsForGap({{transport_mode:{json.dumps(mode)}}},"
        f"{{fieldname:{json.dumps(field)}}})));"
    )
    assert output == expected


def test_uploaded_files_stay_highlighted_until_facts_are_checked_without_a_manual_parse_button():
    state_path = json.dumps(str(PARTS / "05-workbench-state.js"))
    output = result(
        f"global.OverseasCostWorkbenchState=require({state_path});"
        r"""
workspace.transportLabel=(mode)=>mode;
const batch={name:'B1',transport_mode:'SEA'};
const attachments=workspace.manualDocumentPlans('SEA').filter(row=>row.required).map(row=>({
 name:row.code,slot_code:row.code,logistics_type:'SEA',source_type:'Manual',file_name:'已上传.pdf',file_url:'/private/files/known.pdf'
}));
const html=workspace.renderManualDocumentPanel(batch,'SEA',attachments,{fieldname:'goods_value',label:'采购货值',slotCodes:['sea_commercial_invoice']});
workspace.addManualDocumentBatchParseButton(batch,{});
console.log(JSON.stringify({html}));
""")
    assert "所需文件已上传，待核实识别结果" in output["html"]
    assert "核算资料已补齐" not in output["html"]
    assert "上传后会自动识别、补缺并试算" in output["html"]
    assert 'is-gap-focus" data-slot-code="sea_commercial_invoice"' in output["html"]
    assert "识别完成后会更新缺项" in output["html"]
    assert "高亮会消失" not in output["html"]
    assert "解析手动补传" not in output["html"]
    assert 'data-action="upload-manual-document"' in output["html"]
    assert ">上传装箱资料</button>" in output["html"]


def test_authoritative_groups_show_distinct_rows_fees_and_actions_without_reclassifying_codes():
    output = result(r"""
const processing={status:'NEEDS_MATERIALS',changed_count:2,blockers:[{hint:'legacy duplicate must not render'}],
 required_documents:[{key:'purchase',label:'Purchase value and evidence',fieldname:'goods_value',source_coverage:'not_verified',
 hint:'Provide the purchase amount, currency and matching invoice or payment evidence.',blockers:[
  {code:'OPAQUE_SERVER_CODE',material_code:'SKU-X',row_no:1,field:'goods_value',hint:'金额待补'},
  {code:'OPAQUE_SERVER_CODE',material_code:'SKU-X',row_no:2,field:'purchase_currency',hint:'币种待补'},
 ]},{key:'fees',label:'Fee evidence',fieldname:'freight',blockers:[{fee_key:'international_air_freight',expense_category:'空运费',hint:'缺对应账单'}]}],
 actions:[{code:'FX_RATE_MISSING',hint:'汇率需核对 <img src=x onerror=alert(1)>'}]};
const html=workspace.renderMaterialAutomaticProcessing(processing);
console.log(JSON.stringify({html,label:workspace.materialAutomaticProcessingMessage(processing)}));
""")
    assert output["label"] == "请补充或核对下列资料"
    assert "SKU-X · 第 1 行 · 本次发货货值 · 金额待补" in output["html"]
    assert "SKU-X · 第 2 行 · 币种 · 币种待补" in output["html"]
    assert "空运费 · 缺对应账单" in output["html"]
    assert "待核实事项" in output["html"]
    assert "&lt;img" in output["html"] and "<img" not in output["html"]
    assert "legacy duplicate" not in output["html"] and "OPAQUE_SERVER_CODE" not in output["html"]
    assert output["html"].count('data-action="mf-auto-documents"') == 2
    assert "linked source archive coverage has not been verified" in output["html"]


def test_manual_notes_do_not_claim_that_required_files_have_been_uploaded():
    state_path = json.dumps(str(PARTS / "05-workbench-state.js"))
    output = result(
        f"global.OverseasCostWorkbenchState=require({state_path});"
        r"""
workspace.transportLabel=(mode)=>mode;
const notes=workspace.manualDocumentPlans('SEA').filter(row=>row.required).map(row=>({
 slot_code:row.code,logistics_type:'SEA',source_type:'Manual',file_name:row.label,file_url:''
}));
console.log(JSON.stringify({html:workspace.renderManualDocumentPanel({transport_mode:'SEA'},'SEA',notes)}));
""")
    assert "所需文件已上传" not in output["html"]
    assert "可按需补充" in output["html"]


def test_action_only_result_does_not_request_another_file():
    output = result(r"""
const html=workspace.renderMaterialAutomaticProcessing({status:'REVIEW_REQUIRED',changed_count:0,
 actions:[{code:'EDIT_LOCKED',hint:'当前批次正在编辑'}],required_documents:[],blockers:[{hint:'duplicate'}]});
console.log(JSON.stringify({html}));
""")
    assert "当前批次正在编辑" in output["html"]
    assert "mf-auto-documents" not in output["html"]
    assert "duplicate" not in output["html"]


def test_incomplete_linked_source_coverage_shows_the_existing_file_and_does_not_request_uploads():
    output = result(r"""
const html=workspace.renderMaterialAutomaticProcessing({status:'REVIEW_REQUIRED',changed_count:0,
 source_coverage:{status:'PARTIAL',linked_count:2,hydrated_count:1,blockers:[{file_name:'现有采购发票.pdf',hint:'归档尚待读取'}]},
 required_documents:[],actions:[{material_code:'SKU-X',row_no:3,hint:'来源金额待核实'}]});
console.log(JSON.stringify({html}));
""")
    assert "已关联资料仍待核对，请先查看下方原因，无需重复上传。" in output["html"]
    assert "现有采购发票.pdf · 归档尚待读取" in output["html"]
    assert "SKU-X · 第 3 行 · 来源金额待核实" in output["html"]
    assert "mf-auto-documents" not in output["html"]


def test_complete_coverage_does_not_repeat_the_legacy_unverified_coverage_notice():
    output = result(r"""
const html=workspace.renderMaterialAutomaticProcessing({status:'NEEDS_MATERIALS',changed_count:0,
 source_coverage:{status:'COMPLETE',linked_count:1,hydrated_count:1,blockers:[]},
 required_documents:[{label:'Purchase value and evidence',fieldname:'goods_value',source_coverage:'not_verified',blockers:[]}]});
console.log(JSON.stringify({html}));
""")
    assert "linked source archive coverage has not been verified" not in output["html"]
    assert 'data-action="mf-auto-documents"' in output["html"]


def test_real_progress_dialog_button_opens_the_existing_focused_source_panel_without_writing():
    output = result(r"""
state.aiFill={status:'READY',automatic_processing:{status:'NEEDS_MATERIALS',required_documents:[
 {key:'purchase',label:'Purchase value and evidence',hint:'采购凭据',fieldname:'goods_value',blockers:[]}]}};
let click,hidden=false,opened;
const wrapper={length:1,addClass(){return this},off(){return this},find(){return {length:0}},
 on(_event,selector,callback){if(selector==='[data-action]')click=callback;return this}};
frappe.ui={Dialog:class{constructor(){this.$wrapper=wrapper;this.fields_dict={progress_html:{$wrapper:{html(){}}}}}show(){}hide(){hidden=true}}};
global.$=(node)=>({attr:(name)=>node[name]});
workspace.call=async()=>{throw Error('must not write or analyze')};
workspace.openSourceCenterDialog=(batch,gap)=>{opened={batch,gap}};
workspace.openMaterialAIProgressDialog();
click({currentTarget:{'data-action':'mf-auto-documents','data-document-index':'0'}});
workspace.detectManualDocumentLogisticsType=()=> 'SEA';
console.log(JSON.stringify({opened,hidden,minimized:state.aiProgressMinimized,slots:workspace.manualDocumentSlotsForGap({},opened.gap)}));
""")
    assert output == {"opened": {"batch": "B1", "gap": {"fieldname": "goods_value", "label": "Purchase value and evidence"}},
                      "hidden": True, "minimized": True, "slots": ["sea_commercial_invoice"]}


@pytest.mark.parametrize("language", ["zh", "es"])
def test_user_facing_automatic_document_keys_have_exact_translations(language):
    with (ROOT / "translations" / f"{language}.csv").open() as stream:
        catalog = {row[0]: row[1] for row in csv.reader(stream) if len(row) >= 2}
    keys = [
        "请补充采购金额、币种或对应凭据；有冲突的资料需核对。", "已补齐 {0} 项",
        "上传后会自动识别、补缺并试算；已有资料无需重复上传。", "所需文件已上传，待核实识别结果",
        "请补充或核对下列资料", "查看或补充资料", "待核实事项",
        "上传装箱资料", "已关联资料仍待核对，请先查看下方原因，无需重复上传。",
        "Purchase value and evidence", "Shipping and packing documents", "Fee evidence",
        "Provide the purchase amount, currency and matching invoice or payment evidence.",
        "Provide the shipping quantities, units and packing details for the listed rows.",
        "Provide the bill or fee evidence for the listed fees.",
        "Current calculation inputs are incomplete; linked source archive coverage has not been verified.",
        "The current main approval archive is unavailable.",
        "A linked approval is not available in the upstream archive.",
        "Linked approval archive lookup could not be completed.",
        "A linked attachment could not be prepared from the archive.",
        "A linked approval is withdrawn, rejected or unavailable.",
        "A linked attachment has not been archived upstream.",
        "A linked attachment is not yet available in the local cache.",
    ]
    assert all(catalog.get(key) for key in keys)
    if language == "es":
        assert all(catalog[key] != key for key in keys)
