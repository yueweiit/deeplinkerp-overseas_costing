"""Run the real fee picker and the existing serialized save queue in Node."""

import json
import csv
from pathlib import Path

import pytest

from overseas_costing.services.fee_service import build_other_fee_templates
from overseas_costing.tests.test_workbench_frontend_state import FEE_INPUT_FIXTURE, _fee_workspace_result


def run_picker(body):
    options = json.dumps(build_other_fee_templates(), ensure_ascii=False)
    return _fee_workspace_result(f"""
const w=new Harness();
w.detailState={{batchName:'B',versionName:'V',tab:'documents',expectedModified:'2026-10-23 10:00:00',editToken:'EDIT'}};
w.materialFeeState={{batchName:'B',fees:{{fees:[],other_fee_options:{options}}},materials:{{items:[]}}}};
w.ensureMaterialFeeState();
w.escape=v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
w.formatMoney=v=>Number(v).toFixed(2);
w.normalizeErrorMessage=e=>e.message||String(e);
w.ensureMaterialFeeEditSession=async()=>true;
w.renderMaterialFeeWorkspacePreservingPosition=()=>{{}};
w.loadMaterialFeeWorkspace=async()=>true;
const records=new Map();let revision=0;const calls=[];
w.call=async(method,args)=>{{
  calls.push({{method,args}});
  if(method.endsWith('get_fee_worklist'))return {{ok:true,fees:[...records.values()],other_fee_options:{options}}};
  if(method.endsWith('refresh_snapshot'))return {{ok:true,data:{{detail:{{ok:true,header:{{name:'B',modified:`2026-10-23 10:00:${{String(revision).padStart(2,'0')}}`}}}},fees:{{fees:[...records.values()],other_fee_options:{options}}},preview:{{}}}}}};
  const payload=JSON.parse(args.fee_payload);
  const saved={{...records.get(payload.logical_fee_key),...payload,name:'ROW-'+payload.logical_fee_key}};
  records.set(payload.logical_fee_key,saved);
  return {{ok:true,fee:saved,rule_name:saved.name,batch_modified:`2026-10-23 10:00:${{String(++revision).padStart(2,'0')}}`}};
}};
{body}
""")


@pytest.mark.parametrize("mode", ["SEA", "AIR", "EXPRESS"])
def test_add_button_replaces_checkbox_menu_and_new_row_has_fixed_category_and_editable_name(mode):
    result = run_picker(f"""
w.materialFeeState.fees.transport_mode={json.dumps(mode)};
const controls=w.renderOtherFeeControls([]);
const saved=await w.addOtherFeeRow();
console.log(JSON.stringify({{controls,row:w.renderMaterialFeeRow(saved),saved}}));
""")
    assert 'data-action="mf-add-other-fee"' in result["controls"]
    assert "＋" in result["controls"]
    assert 'type="checkbox"' not in result["controls"]
    assert "<strong>其他费用</strong>" in result["row"]
    assert 'data-mf-other-fee-name="1"' in result["row"]
    assert 'data-mf-other-fee-preset="1"' in result["row"]
    assert all(label in result["row"] for label in ["港口杂费", "码头费用", "代理费", "海关查验费"])
    assert 'value=""' in result["row"]
    assert result["saved"]["logical_fee_key"].startswith("other_custom:")
    assert result["saved"]["currency"] == "MXN"
    assert 'class="ocw-mf-fee-entry is-other"' in result["row"]
    assert 'data-action="mf-remove-other-fee"' in result["row"]
    assert "费用名称（可选或手填）" not in result["row"]
    assert "Enter 或失焦自动保存" not in result["row"]
    assert "每点一次" not in result["controls"]
    assert "仅汇总展示" not in result["controls"]


def test_two_adds_and_same_names_stay_independent_with_distinct_amounts_and_existing_save_queue():
    result = run_picker(FEE_INPUT_FIXTURE + """
const first=await w.addOtherFeeRow();
const one=makeFeeInput({feeKey:first.logical_fee_key,amount:'100',originalAmount:'0',currency:'MXN',originalCurrency:'MXN',expenseCategory:'查验费',originalExpenseCategory:''});
await w.saveMaterialFeeInlineAmount(one.nameInput);
const second=await w.addOtherFeeRow();
const two=makeFeeInput({feeKey:second.logical_fee_key,amount:'200',originalAmount:'0',currency:'MXN',originalCurrency:'MXN',expenseCategory:'查验费',originalExpenseCategory:''});
await w.saveMaterialFeeInlineAmount(two.amountInput);
console.log(JSON.stringify({fees:[...records.values()],calls,firstKey:first.logical_fee_key,secondKey:second.logical_fee_key}));
""")
    assert result["firstKey"] != result["secondKey"]
    assert len(result["fees"]) == 2
    assert [row["expense_category"] for row in result["fees"]] == ["查验费", "查验费"]
    assert [row["amount"] for row in result["fees"]] == ["100", "200"]
    assert all(row["allocation_basis"] == "gross_weight" for row in result["fees"])
    writes = [call for call in result["calls"] if call["method"].endswith("save_fee")]
    assert len(writes) == 4
    assert [call["args"]["expected_modified"] for call in writes] == [
        "2026-10-23 10:00:00", "2026-10-23 10:00:01", "2026-10-23 10:00:02", "2026-10-23 10:00:03"]


def test_controls_totals_include_all_instances_once_and_no_summary_row():
    result = run_picker("""
const fees=[{logical_fee_key:'other_custom:'+'a'.repeat(32),amount:'100',currency:'MXN',amount_status:'ESTIMATED'},
{logical_fee_key:'other_custom:'+'b'.repeat(32),amount:'200',currency:'MXN',amount_status:'ACTUAL'},
{logical_fee_key:'other_agency',amount:'999',currency:'MXN',amount_status:'NOT_INCURRED'},
{logical_fee_key:'other_custom',amount:'888',currency:'MXN',amount_status:'INCLUDED'},
{logical_fee_key:'international_sea_freight',amount:'5000',currency:'RMB',amount_status:'ACTUAL'}];
const before=JSON.stringify(fees),html=w.renderOtherFeeControls(fees);
console.log(JSON.stringify({html,unchanged:JSON.stringify(fees)===before,records:records.size}));
""")
    assert "300.00" in result["html"] and "MXN" in result["html"]
    assert "999.00" not in result["html"] and "5000.00" not in result["html"]
    assert result["unchanged"] and result["records"] == 0


@pytest.mark.parametrize("language", ["en", "es"])
def test_localized_other_fee_controls_preserve_saved_names_and_identifiers(language):
    with (Path(__file__).parents[1] / "translations" / f"{language}.csv").open() as source:
        translations = dict(csv.reader(source))
    result = run_picker(f"""
const dictionary={json.dumps(translations, ensure_ascii=False)};
globalThis.__=(text,args=[])=>String(dictionary[text]||text).replace(/\\{{(\\d+)\\}}/g,(_,i)=>args[i]??'');
const saved=await w.addOtherFeeRow();
const input={{...saved,expense_category:'我的自定义名称',amount:'125',currency:'MXN'}};
const before=JSON.stringify(input),row=w.renderMaterialFeeRow(input);
console.log(JSON.stringify({{row,controls:w.renderOtherFeeControls([input]),unchanged:JSON.stringify(input)===before,
 error:w.validateMaterialFeeInlineDraft({{expense_category:'',amount:'125',currency:'MXN'}})}}));
""")
    assert translations["＋ 添加其他费用"] in result["controls"]
    assert translations["选择或填写费用名称"] in result["row"]
    assert translations["删除此费用"] in result["row"]
    assert f'value="海关查验费">{translations["海关查验费"]}</option>' in result["row"]
    assert 'value="我的自定义名称"' in result["row"]
    assert result["error"] == translations["请填写具体费用名称。"]
    assert result["unchanged"]


@pytest.mark.parametrize("lock", ["readonly", "historical", "source"])
def test_locked_context_cannot_add_other_fee_rows(lock):
    setup = {"readonly": "w.detailState.readOnly=true;",
             "historical": "w.materialFeeState.settlementData={historical:true};",
             "source": "w.materialFeeState.fees.source_context={root_kind:'expense'};"}[lock]
    result = run_picker(setup + """
await w.addOtherFeeRow();
console.log(JSON.stringify({records:records.size,controls:w.renderOtherFeeControls([])}));
""")
    assert result["records"] == 0
    assert "disabled" in result["controls"]


@pytest.mark.parametrize("language", ["zh", "en", "es"])
@pytest.mark.parametrize("reason", ["expired", "owner"])
@pytest.mark.parametrize("operation", ["add", "remove"])
def test_failed_fee_retry_keeps_identity_and_reacquires_invalid_lease(language, reason, operation):
    translations = {}
    if language != "zh":
        with (Path(__file__).parents[1] / "translations" / f"{language}.csv").open() as source:
            translations = dict(csv.reader(source))
    template = "编辑会话已过期，请重新进入编辑。" if reason == "expired" else "当前批次正在被{0}编辑。"
    message = translations.get(template, template).replace("{0}", "Another.User+test@example.com")
    setup = "const target=await w.addOtherFeeRow();const act=()=>w.removeOtherFeeRow(target.logical_fee_key);" if operation == "remove" else "const act=()=>w.addOtherFeeRow();"
    result = run_picker(f"""
const dictionary={json.dumps(translations, ensure_ascii=False)};
globalThis.__=(text,args=[])=>String(dictionary[text]||text).replace(/\\{{(\\d+)\\}}/g,(_,i)=>args[i]??'');
const failureMessage={json.dumps(message)};
{setup}
""" + """
let acquired=0;
w.ensureMaterialFeeEditSession=async()=>{if(!w.detailState.editToken){acquired++;w.detailState.editToken='NEW';}return true;};
const save=w.call;let fail=true;const keys=[];
w.call=async(method,args)=>{
  if(method.endsWith('save_fee')){keys.push(JSON.parse(args.fee_payload).logical_fee_key);
    if(fail){fail=false;throw new Error(failureMessage)}}
  return save(method,args);
};
let error='';try{await act()}catch(e){error=e.message}
const token=w.detailState.editToken,pending=w.materialFeeState.otherFeePending;
await act();
console.log(JSON.stringify({error,token,pending,keys,acquired,fees:[...records.values()],unrelated:w.materialFeeEditSessionInvalid(new Error('invalid amount'))}));
""")
    assert result["error"] == message
    assert result["token"] == "" and not result["pending"]
    assert len(result["keys"]) == 2 and result["keys"][0] == result["keys"][1]
    assert result["acquired"] == 1
    assert not result["unrelated"]
    assert len(result["fees"]) == 1
    if operation == "remove":
        assert result["fees"][0]["is_active"] == 0


def test_new_row_waits_for_existing_save_and_does_not_double_create_while_pending():
    result = run_picker("""
let release;const previous=new Promise(resolve=>release=resolve);
w.materialFeeState.materialFeeWriteQueue=previous;
const adding=w.addOtherFeeRow();
const during={writes:calls.length,controls:w.renderOtherFeeControls([])};
const duplicate=await w.addOtherFeeRow();
release();await adding;
console.log(JSON.stringify({during,duplicate,count:records.size}));
""")
    assert result["during"]["writes"] == 0
    assert "disabled" in result["during"]["controls"]
    assert result["duplicate"] is False
    assert result["count"] == 1


@pytest.mark.parametrize("read_fails", [False, True])
def test_lost_add_response_recovers_revision_and_reuses_confirmed_row_without_resaving(read_fails):
    result = run_picker(f"""
const save=w.call;let lost=true,reads=0;const writes=[];
w.call=async(method,args)=>{{
 if(method.endsWith('save_fee')){{writes.push(args.expected_modified);
   if(args.expected_modified!==`2026-10-23 10:00:${{String(revision).padStart(2,'0')}}`)throw new Error('旧版本不能保存');
   const response=await save(method,args);if(lost){{lost=false;throw new Error('响应丢失')}}return response;
 }}
 if(method.endsWith('refresh_snapshot')){{reads++;
   if({json.dumps(read_fails)}&&reads===1)throw new Error('读取失败');
   return {{ok:true,data:{{detail:{{ok:true,header:{{name:'B',modified:`2026-10-23 10:00:${{String(revision).padStart(2,'0')}}`}}}},fees:{{fees:[...records.values()],other_fee_options:w.materialFeeState.fees.other_fee_options}},preview:{{}}}}}};
 }}
 return save(method,args);
}};
let error='',first;try{{first=await w.addOtherFeeRow()}}catch(e){{error=e.message}}
if(!first)first=await w.addOtherFeeRow();
const confirmed={{count:records.size,key:first.logical_fee_key,revision:w.detailState.expectedModified,writes:[...writes]}};
await w.addOtherFeeRow();
console.log(JSON.stringify({{confirmed,count:records.size,writes,reads,error}}));
""")
    assert result["confirmed"]["count"] == 1
    assert result["confirmed"]["revision"] == "2026-10-23 10:00:01"
    assert result["confirmed"]["writes"] == ["2026-10-23 10:00:00"]
    assert result["count"] == 2
    assert result["writes"] == ["2026-10-23 10:00:00", "2026-10-23 10:00:01"]


def test_add_recovery_cannot_apply_a_snapshot_after_same_batch_version_switch():
    result = run_picker("""
let release;w.call=async()=>new Promise(resolve=>release=resolve);
const recovering=w.recoverMaterialFeeReadonlyState('B');
w.detailState.versionName='V2';
release({ok:true,data:{detail:{ok:true,version_name:'V',header:{modified:'OLD'}},fees:{fees:[]},preview:{}}});
const recovered=await recovering;
console.log(JSON.stringify({recovered,version:w.detailState.versionName,revision:w.detailState.expectedModified}));
""")
    assert result == {"recovered": False, "version": "V2", "revision": "2026-10-23 10:00:00"}


def test_new_row_does_not_discard_an_invalid_existing_draft():
    result = run_picker("""
w.materialFeeState.feeDrafts.other_custom={amount:'80',currency:'MXN',expense_category:'',error:'请填写名称'};
let error='';try{await w.addOtherFeeRow()}catch(e){error=e.message}
console.log(JSON.stringify({error,count:records.size,draft:w.materialFeeState.feeDrafts.other_custom}));
""")
    assert "先保存" in result["error"]
    assert result["count"] == 0 and result["draft"]["amount"] == "80"


@pytest.mark.parametrize("key,label", [("other_port_misc", "港口杂费"), ("other_agency", "代理费"),
                                      ("other_custom", "海关查验费"), ("other_custom:" + "a" * 32, "自定义装卸费")])
def test_legacy_and_instance_rows_keep_generic_left_label_and_specific_middle_name(key, label):
    result = run_picker(f"""
const fee={{...w.materialFeeState.fees.other_fee_options.find(x=>x.logical_fee_key==='other_custom'),
logical_fee_key:{json.dumps(key)},expense_category:{json.dumps(label)},amount:'80',virtual:false}};
const html=w.renderMaterialFeeRow(fee);
console.log(JSON.stringify({{html}}));
""")
    assert "<strong>其他费用</strong>" in result["html"]
    assert f'value="{label}"' in result["html"]
    assert f"<strong>{label}</strong>" not in result["html"]


@pytest.mark.parametrize("owner", ["source", "ai"])
def test_source_owned_and_ai_proposed_rows_do_not_gain_manual_name_editor(owner):
    result = run_picker(f"""
const key='other_custom:'+'a'.repeat(32);
const fee={{logical_fee_key:key,expense_category:'查验费',amount:'80',currency:'MXN'}};
if({json.dumps(owner)}==='source'){{fee.source_binding_id='SOURCE';w.renderMaterialFeeFreightEditor=()=>'';}}
else{{w.materialFeeState.aiFill={{status:'READY',draftVisible:true,proposals:[{{proposal_type:'fee_update',proposal_id:'P',payload:{{logical_fee_key:key,amount:'90',currency:'MXN'}}}}]}};w.isMaterialAIReadyStatus=()=>true;}}
console.log(JSON.stringify({{html:w.renderMaterialFeeRow(fee)}}));
""")
    assert 'data-mf-other-fee-name="1"' not in result["html"]


def test_custom_name_draft_retains_escaped_value_on_rerender():
    result = run_picker("""
const fee=await w.addOtherFeeRow();
w.materialFeeState.feeDrafts[fee.logical_fee_key]={amount:'80',currency:'MXN',expense_category:'查验"<费',error:'网络失败'};
console.log(JSON.stringify({html:w.renderMaterialFeeRow(fee)}));
""")
    assert 'value="查验&quot;&lt;费"' in result["html"]
    assert 'value="80"' in result["html"]
    assert "网络失败" in result["html"]


@pytest.mark.parametrize("name,error", [("  ", "请填写具体费用名称"), ("费" * 141, "140"), ("随手填写的装卸费", "")])
def test_inline_fee_name_validation(name, error):
    result = run_picker(f"""
console.log(JSON.stringify({{error:w.validateMaterialFeeInlineDraft({{amount:'80',currency:'MXN',expense_category:{json.dumps(name)}}})}}));
""")
    assert error in result["error"] if error else result["error"] == ""


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("key", ["other_custom", "other_custom:" + "a" * 32])
def test_name_only_inline_save_and_failed_draft_retention(fails, key):
    result = run_picker(FEE_INPUT_FIXTURE + f"""
const fee={{...w.materialFeeState.fees.other_fee_options.find(x=>x.logical_fee_key==='other_custom'),logical_fee_key:{json.dumps(key)},amount:'80',expense_category:'查验费'}};
w.materialFeeState.fees.fees=[fee];records.set(fee.logical_fee_key,fee);
const fixture=makeFeeInput({{feeKey:fee.logical_fee_key,amount:'80',originalAmount:'80',currency:'MXN',originalCurrency:'MXN',expenseCategory:' 仓储费 ',originalExpenseCategory:'查验费'}});
if({json.dumps(fails)}){{w.call=async(method,args)=>{{calls.push({{method,args}});throw new Error('网络失败')}};w.recoverMaterialFeeReadonlyState=async()=>false;}}
await w.saveMaterialFeeInlineAmount(fixture.nameInput);
console.log(JSON.stringify({{writes:calls.filter(x=>x.method.endsWith('save_fee')),saved:records.get(fee.logical_fee_key),draft:w.materialFeeState.feeDrafts[fee.logical_fee_key]||null,original:fixture.nameInput.attr('data-original-value'),disabled:fixture.nameInput.disabled}}));
""")
    assert len(result["writes"]) == 1
    payload = json.loads(result["writes"][0]["args"]["fee_payload"])
    assert (payload["expense_category"], payload["amount"], payload["currency"]) == ("仓储费", "80", "MXN")
    assert (payload["allocation_basis"], payload["scope_type"]) == ("gross_weight", "ALL_ITEMS")
    assert result["disabled"] is False
    if fails:
        assert result["saved"]["expense_category"] == "查验费"
        assert result["draft"]["expense_category"].strip() == "仓储费"
        assert "网络失败" in result["draft"]["error"]
    else:
        assert result["saved"]["expense_category"] == result["original"] == "仓储费"
        assert result["draft"] is None


def test_preset_choice_uses_same_editable_name_and_inline_save_as_free_text():
    result = run_picker(FEE_INPUT_FIXTURE + """
const fee=await w.addOtherFeeRow();calls.length=0;
const fixture=makeFeeInput({feeKey:fee.logical_fee_key,amount:'80',originalAmount:'0',currency:'MXN',originalCurrency:'MXN',expenseCategory:'',originalExpenseCategory:''});
let selected='海关查验费';
const select={val(next){if(arguments.length){selected=next;return this}return selected},closest(){return fixture.cell}};
await w.applyOtherFeePreset(select);
console.log(JSON.stringify({name:fixture.nameInput.val(),selected,saved:records.get(fee.logical_fee_key)}));
""")
    assert result["name"] == result["saved"]["expense_category"] == "海关查验费"
    assert result["saved"]["amount"] == "80"
    assert result["selected"] == ""


@pytest.mark.parametrize("prior_writes", [False, True])
def test_preset_selection_after_blur_preserves_latest_choice_with_noop_or_real_inflight_save(prior_writes):
    setup = "const amount='80', priorName='原费用名称';" if prior_writes else "const amount='0', priorName='';"
    result = run_picker(FEE_INPUT_FIXTURE + setup + """
const fee=await w.addOtherFeeRow();calls.length=0;
const fixture=makeFeeInput({feeKey:fee.logical_fee_key,amount,originalAmount:'0',currency:'MXN',originalCurrency:'MXN',expenseCategory:priorName,originalExpenseCategory:''});
const blur=w.saveMaterialFeeInlineAmount(fixture.nameInput);
let selected='海关查验费';
const select={val(next){if(arguments.length){selected=next;return this}return selected},closest(){return fixture.cell}};
await Promise.all([blur,w.applyOtherFeePreset(select)]);
console.log(JSON.stringify({saved:records.get(fee.logical_fee_key),writes:calls.filter(x=>x.method.endsWith('save_fee')).length}));
""")
    assert result["saved"]["expense_category"] == "海关查验费"
    assert result["writes"] == (2 if prior_writes else 1)


def test_delayed_preset_does_not_write_after_version_switch():
    result = run_picker(FEE_INPUT_FIXTURE + """
const fee=await w.addOtherFeeRow();calls.length=0;
const fixture=makeFeeInput({feeKey:fee.logical_fee_key,amount:'0',originalAmount:'0',currency:'MXN',originalCurrency:'MXN',expenseCategory:'',originalExpenseCategory:''});
let release;w.materialFeeState.feeCellWrites.set(fee.logical_fee_key,new Promise(resolve=>release=resolve));
const select={val(next){return arguments.length?this:'代理费'},closest(){return fixture.cell}};
const saving=w.applyOtherFeePreset(select);
w.detailState.versionName='V2';release();await saving;
console.log(JSON.stringify({writes:calls.filter(x=>x.method.endsWith('save_fee')).length,saved:records.get(fee.logical_fee_key).expense_category}));
""")
    assert result == {"writes": 0, "saved": "其他费用"}


def test_blank_name_keeps_amount_without_writing_and_does_not_affect_normal_fees():
    result = run_picker(FEE_INPUT_FIXTURE + """
const fee=await w.addOtherFeeRow();calls.length=0;
const fixture=makeFeeInput({feeKey:fee.logical_fee_key,amount:'80',originalAmount:'0',currency:'MXN',originalCurrency:'MXN',expenseCategory:'',originalExpenseCategory:''});
await w.saveMaterialFeeInlineAmount(fixture.amountInput);
const normal=makeFeeInput({feeKey:'local_delivery',amount:'90',originalAmount:'0',currency:'MXN',originalCurrency:'MXN'});
const normalDraft=w.updateMaterialFeeDraftFromInput(normal.amountInput);
console.log(JSON.stringify({writes:calls.length,draft:w.materialFeeState.feeDrafts[fee.logical_fee_key],error:fixture.errorElement.value,normalDraft}));
""")
    assert result["writes"] == 0 and result["draft"]["amount"] == "80"
    assert "请填写具体费用名称" in result["error"]
    assert "expense_category" not in result["normalDraft"]


def test_more_settings_remains_compatible_with_instance_name():
    result = run_picker("""
const fee=await w.addOtherFeeRow();let config;
global.frappe.ui={Dialog:class{constructor(c){config=c;this.$wrapper={addClass:()=>{}}}show(){}}};
w.openMaterialFeeDialog(fee.logical_fee_key);
const fields=config.fields.map(f=>f.fieldname);
const values={expense_category:'任意服务费',amount_status:'ESTIMATED',amount:'50',currency:'MXN',scope_type:'ALL_ITEMS',remark:'保留'};
const dialog={get_values:()=>values,$wrapper:{find:()=>({toArray:()=>[]})},hide:()=>{}};
await w.saveMaterialFeeDialog(dialog,fee);
console.log(JSON.stringify({fields,saved:records.get(fee.logical_fee_key)}));
""")
    assert "expense_category" in result["fields"]
    assert result["saved"]["expense_category"] == "任意服务费"


@pytest.mark.parametrize("queued", [False, True])
def test_removal_reuses_latest_record_and_preserves_evidence_and_other_drafts(queued):
    queued_save = """
w.findMaterialFee(key).remark='原备注';
const fixture=makeFeeInput({feeKey:key,amount:'125',originalAmount:'0',currency:'USD',originalCurrency:'MXN',expenseCategory:'查验费',originalExpenseCategory:''});
const saving=w.saveMaterialFeeInlineAmount(fixture.amountInput);
const removing=w.removeOtherFeeRow(key);
await Promise.all([saving,removing]);
""" if queued else "await w.removeOtherFeeRow(key);"
    result = run_picker(FEE_INPUT_FIXTURE + """
const fee=await w.addOtherFeeRow(),key=fee.logical_fee_key;
const latest={...records.get(key),expense_category:'查验费',amount:'125',currency:'USD',remark:'原备注',evidence:[{attachment:'A1'}]};
records.set(key,latest);w.materialFeeState.cacheDirty=true;
w.materialFeeState.feeDrafts[key]={amount:'',expense_category:'',error:'名称不能为空'};
w.materialFeeState.feeDrafts.unrelated={amount:'99',currency:'MXN'};
calls.length=0;
""" + queued_save + """
await w.removeOtherFeeRow(key);
console.log(JSON.stringify({saved:records.get(key),writes:calls.filter(x=>x.method.endsWith('save_fee')),html:w.renderMaterialFeeTable(w.materialFeeState.fees.fees),drafts:w.materialFeeState.feeDrafts}));
""")
    saved = result["saved"]
    assert (saved["is_active"], saved["is_enabled"]) == (0, 0)
    assert (saved["amount"], saved["currency"], saved["remark"]) == ("125", "USD", "原备注")
    assert saved["evidence"] == [{"attachment": "A1"}]
    assert len(result["writes"]) == (2 if queued else 1)
    assert result["drafts"] == {"unrelated": {"amount": "99", "currency": "MXN"}}
    assert 'data-mf-other-fee-name="1"' not in result["html"]


@pytest.mark.parametrize("guard", ["readonly", "historical", "source", "bound", "duplicate", "ordinary", "ai"])
def test_protected_rows_cannot_be_removed(guard):
    setup = {
        "readonly": "w.detailState.readOnly=true;",
        "historical": "w.materialFeeState.settlementData={historical:true};",
        "source": "w.materialFeeState.fees.source_context={root_kind:'expense'};",
        "bound": "fee.source_binding_id='SOURCE';",
        "duplicate": "fee.duplicate_rule_names=['ONE','TWO'];",
        "ordinary": "fee.logical_fee_key='import_tax';",
        "ai": "w.materialFeeState.aiFill={status:'READY',draftVisible:true,proposals:[{proposal_type:'fee_update',payload:{logical_fee_key:fee.logical_fee_key}}]};w.isMaterialAIReadyStatus=()=>true;",
    }[guard]
    result = run_picker("""
const fee=await w.addOtherFeeRow();
""" + setup + """
w.materialFeeState.fees.fees=[fee];calls.length=0;
let error='';try{await w.removeOtherFeeRow(fee.logical_fee_key)}catch(e){error=e.message}
console.log(JSON.stringify({writes:calls.filter(x=>x.method.endsWith('save_fee')).length,error}));
""")
    assert result["writes"] == 0
    assert result["error"] == ""


@pytest.mark.parametrize("lost", [False, True])
def test_remove_failure_keeps_row_or_recovers_confirmed_retirement_without_duplicate_write(lost):
    result = run_picker(f"""
const fee=await w.addOtherFeeRow();const original=w.call;let writes=0;
w.call=async(method,args)=>{{
 if(method.endsWith('save_fee')){{writes++;if({json.dumps(lost)})await original(method,args);throw new Error('网络失败')}}
 return original(method,args);
}};
let error='',removed;try{{removed=await w.removeOtherFeeRow(fee.logical_fee_key)}}catch(e){{error=e.message}}
const html=w.renderMaterialFeeTable(w.materialFeeState.fees.fees);
console.log(JSON.stringify({{removed:Boolean(removed),error,writes,html,pending:w.materialFeeState.otherFeePending}}));
""")
    assert result["writes"] == 1 and not result["pending"]
    if lost:
        assert result["removed"] and not result["error"]
        assert 'data-mf-other-fee-name="1"' not in result["html"]
    else:
        assert "网络失败" in result["error"]
        assert 'data-mf-other-fee-name="1"' in result["html"]
