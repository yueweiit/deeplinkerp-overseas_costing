import json
import subprocess
from pathlib import Path

import pytest

PARTS = Path(__file__).resolve().parents[1] / 'page/overseas_cost_workbench/parts'


def run_js(script, with_erp_sites=False):
    """在 Node 里组装工作台 View 并执行断言脚本。

    ``with_erp_sites`` 才装载 ``79-erp-sites.js``：该分片的方法名都以
    ``erpSite``/``renderErpSite`` 开头，与其它分片无交集，但按需装载能保证
    既有用例的 View 组成一字不变。
    """
    erp_sites = (
        "fs.readFileSync(%s,'utf8')+" % json.dumps(str(PARTS / '79-erp-sites.js'))
        if with_erp_sites
        else ""
    )
    source = f"""
const fs=require('fs');
global.OverseasCostWorkbenchState=require({json.dumps(str(PARTS / '05-workbench-state.js'))});
const calculation=fs.readFileSync({json.dumps(str(PARTS / '30-calculation-erp.js'))},'utf8');
const vouchers=fs.readFileSync({json.dumps(str(PARTS / '40-vouchers.js'))},'utf8');
const drawer=fs.readFileSync({json.dumps(str(PARTS / '80-drawer-profit.js'))},'utf8');
const review=fs.readFileSync({json.dumps(str(PARTS / '88-review-communication.js'))},'utf8');
const View=Function('return class View {{'+calculation+vouchers+fs.readFileSync({json.dumps(str(PARTS / '35-workbench-view.js'))},'utf8')+drawer+fs.readFileSync({json.dumps(str(PARTS / '82-detail-page.js'))},'utf8')+review+{erp_sites}'}}')();
function makeView(task='cost') {{
 const v=new View(); v.viewState={{task,page:1,q:'',screen:'workbench'}};
 v.filters={{review_status:'pending',review_warning:'',issue:''}};
 v.html={{}};v.$root={{
   attr:key=>key==='data-screen'?v.viewState.screen:undefined,
   find:key=>({{html:value=>{{v.html[key]=value;}},toggleClass:()=>{{}}}})
 }};
 v.escape=x=>String(x??'').replaceAll('<','&lt;');
 v.formatMoney=x=>String(x);v.formatDateTimeMinute=x=>String(x||'');
 v.hasText=x=>String(x??'').trim().length>0;v.isPositive=x=>Number(x)>0;
 v.businessTypeLabel=x=>x;v.businessTypeOptions=[];v.selectOptions={{business_type:[]}};
 v.exceptionCounts={{}};v.reviewCounts={{pending:2,confirmed:1,estimated:1,evidence_missing:2}};
 return v;
}}
(async()=>{{{script}}})().catch(e=>{{console.error(e);process.exit(1)}});
"""
    return json.loads(subprocess.check_output(['node', '-e', source], text=True))


def test_review_url_state_and_filters_roundtrip():
    result=run_js("""
const s=OverseasCostWorkbenchState.parseWorkbenchState('/desk/x?task=cost&review_status=confirmed&review_warning=estimated');
const v=makeView();v.filters.review_status=s.reviewStatus;v.filters.review_warning=s.reviewWarning;
console.log(JSON.stringify({s,filters:v.workbenchFilters()}));
""")
    assert result['s']['reviewStatus']=='confirmed'
    assert result['filters']['review_status']=='confirmed'
    assert result['filters']['review_warning']=='estimated'


def test_workbench_shell_has_no_summary_card_row_for_any_task():
    source = (PARTS / '35-workbench-view.js').read_text(encoding='utf-8')
    assert 'data-area="exception-summary"' not in source
    assert 'ocw-summary-card' not in source
    assert 'renderExceptionSummary' not in source
    assert "data-action='set-review-filter'" not in source
    assert "data-action='set-issue'" not in source


def test_cost_row_uses_review_action_and_compact_warning_tags():
    text=run_js("""const v=makeView();console.log(JSON.stringify(v.renderWorkbenchBatchRow({
name:'B',batch_no:'B',primary_issue:'ready',primary_action:'review',review_state:'ready',
review_blockers:[],review_warnings:[{code:'ESTIMATED_AMOUNT',message:'含暂估费用，可先审核，最终确认前仍需补充实际金额。'}],
result_is_current:true,estimated_total_cost_rmb:100,status:'Calculated'})));""")
    assert '成本核算' in text
    assert '实际费用待确认' in text
    assert 'ocw-review-tag' in text
    assert '最终确认前' not in text
    assert 'data-primary-action="review"' in text


def test_list_rows_use_one_dynamic_action_and_no_legacy_more_button():
    result=run_js("""
const base={name:'B',batch_no:'B',review_state:'ready',cost_review_started:true,
 cost_review_eligible:true,review_blockers:[],review_warnings:[],result_is_current:true,
 estimated_total_cost_rmb:100,status:'Calculated'};
const pending=makeView('pending').renderWorkbenchBatchRow({...base,primary_issue:'purchase',primary_action:'supplement'});
const returned=makeView('pending').renderWorkbenchBatchRow({...base,remediation_state:'returned',unresolved_count:2,primary_issue:'remediation',primary_action:'review_remediation'});
const cost=makeView('cost').renderWorkbenchBatchRow({...base,primary_issue:'ready',primary_action:'review'});
console.log(JSON.stringify({pending,returned,cost}));
""")
    assert '继续处理' in result['pending']
    assert '待整改 2' in result['returned']
    assert '成本核算' in result['cost']
    for text in result.values():
        assert text.count('data-action="workbench-primary"') == 1
        assert 'data-action="row-more"' not in text
        assert '>更多<' not in text

    legacy_table = (PARTS / '75-table-and-list.js').read_text(encoding='utf-8')
    attachments = (PARTS / '70-attachments.js').read_text(encoding='utf-8')
    assert 'data-action="row-more"' not in legacy_table
    assert 'openRowMoreDialog(' not in attachments


def test_review_tab_is_stable_and_list_navigation_routes_to_expected_tab():
    result=run_js("""
const state=OverseasCostWorkbenchState.parseWorkbenchState('/desk/x?screen=detail&batch=B&tab=review');
console.log(JSON.stringify({tab:state.tab,resource:OverseasCostWorkbenchState.detailTabResource('review'),
 returned:OverseasCostWorkbenchState.detailTabForAction('review_remediation'),
 normal:OverseasCostWorkbenchState.detailTabForAction('review')}));
""")
    assert result == {
        'tab': 'review',
        'resource': 'review',
        'returned': 'review',
        'normal': 'documents',
    }


def test_result_preview_uses_server_adopted_price_and_shared_native_scrollbar():
    source = (PARTS / '35-workbench-view.js').read_text(encoding='utf-8')
    renderer = source.split('renderBatchResultPreview(data)', 1)[1].split('bindResultPreviewScrollControls', 1)[0]
    assert 'item.adopted_price' in renderer
    assert 'data-ocw-scrollbar' in renderer
    assert 'type="range"' not in renderer
    assert 'shouldCompactResultPreviewColumns' not in source


def test_drawer_and_erp_preview_use_server_adopted_price_projection():
    drawer = (PARTS / "80-drawer-profit.js").read_text(encoding="utf-8")
    erp = (PARTS / "30-calculation-erp.js").read_text(encoding="utf-8")

    assert "item.adopted_price?.value ?? item.unit_price" in drawer
    assert "按货值÷采购数量计算" in drawer
    assert "item.adopted_price?.value ?? item.original_unit_price" in erp
    assert "按货值÷采购数量计算" in erp


def test_result_and_sku_tables_share_one_scroll_controller_without_geometry_feedback():
    result_source = (PARTS / '35-workbench-view.js').read_text(encoding='utf-8')
    sku_source = (PARTS / '82-detail-page.js').read_text(encoding='utf-8')
    material_source = (PARTS / '78-material-fee-workspace.js').read_text(encoding='utf-8')
    allocation_source = (PARTS / '76-allocation.js').read_text(encoding='utf-8')
    material_css = (PARTS / '48-material-fee-workspace.css').read_text(encoding='utf-8')
    helper_source = (PARTS / '15-horizontal-scroll.js').read_text(encoding='utf-8')

    assert 'bindHorizontalScrollController' in helper_source
    assert 'shouldCompactResultPreviewColumns' not in result_source
    assert 'shouldCompactSkuColumns' not in sku_source
    assert 'bindHorizontalScrollController' in result_source
    assert 'bindHorizontalScrollController' in sku_source
    assert 'bindHorizontalScrollController' in material_source
    assert 'bindHorizontalScrollController' in allocation_source
    assert 'const bindPair =' not in allocation_source
    assert 'compactWidth' not in material_source
    assert '--mf-grid-reduction' not in material_css
    assert 'column.style.width' not in helper_source


def test_shared_scroll_controller_does_not_cancel_smooth_button_scroll_with_feedback():
    helper_path = json.dumps(str(PARTS / "15-horizontal-scroll.js"))
    script = f"""
const fs=require('fs');
const helper=fs.readFileSync({helper_path},'utf8');
const Controller=Function('return class Controller {{'+helper+'}}')();
const eventQueue=[]; const frames=[];
global.window={{
  requestAnimationFrame:fn=>{{frames.push(fn);return frames.length;}}, cancelAnimationFrame:()=>{{}},
  addEventListener:()=>{{}}, removeEventListener:()=>{{}}
}};
global.ResizeObserver=undefined;
class Target {{
  constructor(clientWidth,scrollWidth){{this.clientWidth=clientWidth;this.scrollWidth=scrollWidth;this._left=0;this.listeners={{}};this.disabled=false;this.style={{}};this.classList={{toggle:()=>{{}}}};}}
  get scrollLeft(){{return this._left;}}
  set scrollLeft(value){{this.smoothTarget=null;this._left=Math.max(0,Math.min(value,this.scrollWidth-this.clientWidth));eventQueue.push(()=>this.emit('scroll'));}}
  addEventListener(name,fn){{(this.listeners[name]??=[]).push(fn);}}
  removeEventListener(){{}}
  emit(name){{for(const fn of this.listeners[name]||[])fn();}}
  querySelector(){{return null;}}
  scrollBy({{left}}){{this.smoothTarget=Math.max(0,Math.min(this._left+left,this.scrollWidth-this.clientWidth));this._left=Math.min(this.smoothTarget,this._left+1);eventQueue.push(()=>this.emit('scroll'));}}
}}
const flushEvents=()=>{{while(eventQueue.length)eventQueue.shift()();}};
const flushFrame=()=>{{while(frames.length)frames.shift()();for(const target of [content,bar]){{if(target.smoothTarget!=null){{target._left=target.smoothTarget;target.smoothTarget=null;eventQueue.push(()=>target.emit('scroll'));}}}}flushEvents();}};
const content=new Target(100,300),bar=new Target(100,300),left=new Target(0,0),right=new Target(0,0),spacer={{style:{{}}}};
new Controller().bindHorizontalScrollController({{content,scrollbar:bar,spacer,leftButton:left,rightButton:right}});
flushFrame(); right.emit('click'); flushEvents(); flushFrame();
const afterRight=content.scrollLeft;
left.emit('click'); flushEvents(); flushFrame();
console.log(JSON.stringify({{afterRight,afterLeft:content.scrollLeft,max:content.scrollWidth-content.clientWidth}}));
"""

    result = json.loads(subprocess.check_output(["node", "-e", script], text=True))

    assert result["afterRight"] > result["max"] * 0.5
    assert result["afterLeft"] == 0


def test_missing_status_copy_uses_danger_color_while_pending_sources_remain_amber():
    settlement_css = (PARTS / '47-settlement.css').read_text(encoding='utf-8')
    shell_css = (PARTS / '10-shell.css').read_text(encoding='utf-8')

    assert '.ocw-freight-missing { color: #b42318; }' in settlement_css
    assert '.ocw-purchase-approval-metric.is-missing strong' in shell_css
    assert 'color: #b42318;' in shell_css.split('.ocw-purchase-approval-metric.is-missing strong', 1)[1].split('}', 1)[0]
    assert 'color: #9a6700;' in shell_css.split('.ocw-purchase-approval-metric.is-pending strong', 1)[1].split('}', 1)[0]


def test_stale_row_shows_old_result_and_specific_blocker():
    text=run_js("""const v=makeView('pending');console.log(JSON.stringify(v.renderWorkbenchBatchRow({
name:'B',batch_no:'B',primary_issue:'calculation',primary_action:'recalculate',review_state:'processing',
review_blockers:[{code:'STALE',message:'费用已变化，请重新试算'}],review_warnings:[],
result_is_current:false,calculated_at:'2026-09-08',estimated_total_cost_rmb:100,status:'Dirty'})));""")
    assert '上次结果，待更新' in text
    assert '待重新计算' in text
    assert '费用已变化，请重新试算' not in text


def test_processing_row_limits_short_tags_and_hides_raw_messages():
    text=run_js("""const v=makeView('pending');console.log(JSON.stringify(v.renderWorkbenchBatchRow({
name:'B',batch_no:'B',primary_issue:'logistics',primary_action:'supplement_fees',review_state:'processing',
review_blockers:[
 {code:'AMOUNT_MISSING',message:'存在未填金额的费用，请补充费用。'},
 {code:'PURCHASE_SOURCE_INVALID',message:'采购来源已失效或无法读取，请先核对采购资料。'},
 {code:'UNEXPECTED_REVIEW_CODE',message:'这是一段不应直接出现在列表中的长说明。'}
],
review_warnings:[{code:'EVIDENCE_MISSING',message:'费用凭证缺失或尚未通过校验，可先审核。'}],
result_is_current:false,calculated_at:'2026-09-08',estimated_total_cost_rmb:100,status:'Dirty'})));""")
    assert '资料待补' in text
    assert '费用金额待补' in text
    assert '采购资料待补' in text
    assert '其他问题待处理' in text
    assert '另有 1 项' in text
    assert text.count('ocw-review-tag ') == 4
    assert '存在未填金额的费用' not in text
    assert '不应直接出现' not in text


def test_review_tag_labels_cover_current_allocation_and_final_fee_codes():
    result=run_js("""const v=makeView();console.log(JSON.stringify({
allocation:v.workbenchReviewTagLabel('ALLOCATION_REQUIRED'),
finalFee:v.workbenchReviewTagLabel('FINAL_FEE_REVIEW_REQUIRED')}));""")
    assert result == {
        'allocation': '分摊资料待补',
        'finalFee': '费用待核对',
    }


def test_out_of_order_success_and_error_cannot_replace_latest_task():
    result=run_js("""
const v=makeView('pending');const calls=[];const renders=[];
v.resetBatchResultPreview=()=>{};v.renderWorkbenchLoading=()=>{};
v.renderWorkbench=()=>renders.push(v.batches[0]?.name);
v.call=(method,args)=>new Promise((resolve,reject)=>calls.push({method,args,resolve,reject}));
v.renderWorkbenchError=e=>renders.push('error');
const first=v.loadBatches();v.viewState.task='cost';const second=v.loadBatches();
calls[2].resolve({ok:true,items:[{name:'READY'}],total:1,page:1});
calls[3].resolve({ok:true,counts:{},review_counts:{pending:1}});await second;
calls[0].reject(new Error('old request failed'));calls[1].resolve({counts:{purchase:99}});await first;
console.log(JSON.stringify({renders,items:v.batches,summaryTask:calls[3].args.task}));
""")
    assert result['renders']==['READY']
    assert result['items']==[{'name':'READY'}]
    assert result['summaryTask']=='cost'


def test_review_filter_change_clears_warning_and_restarts_page():
    result=run_js("""
const v=makeView();v.viewState.page=4;v.filters.review_warning='estimated';
v.syncWorkbenchFiltersToUrl=()=>{};v.loadBatches=async()=>{};
await v.setReviewFilter('confirmed');
console.log(JSON.stringify({page:v.viewState.page,filters:v.filters}));
""")
    assert result['page']==1
    assert result['filters']['review_status']=='confirmed'
    assert result['filters']['review_warning']==''


@pytest.mark.parametrize(
    ('task', 'review_status'),
    [('pending', 'pending'), ('cost', 'pending'), ('cost', 'confirmed'), ('erp', 'pending')],
)
def test_all_workbench_lists_use_logistics_process_start_time_header(task, review_status):
    html=run_js(f"""
global.window={{requestAnimationFrame:()=>{{}}}};
const v=makeView({json.dumps(task)});v.batches=[];v.workbenchTotal=0;
v.filters.review_status={json.dumps(review_status)};
v.renderWorkbenchBatchList();console.log(JSON.stringify(v.html));
""")
    text=''.join(html.values())
    assert '流程发起时间' in text
    assert '更新时间' not in text
    assert '确认时间' not in text


def test_workbench_row_uses_only_source_created_at_and_preserves_status_detail():
    result=run_js("""
const v=makeView();
const base={name:'B',batch_no:'B',primary_issue:'calculation',primary_action:'recalculate',
 review_blockers:[],review_warnings:[],estimated_total_cost_rmb:100};
const active=v.renderWorkbenchBatchRow({...base,review_state:'processing',result_is_current:false,
 source_created_at:'2026-09-01 08:15:00',modified:'2026-09-15 12:30:00',status:'Dirty'});
const confirmed=v.renderWorkbenchBatchRow({...base,review_state:'confirmed',result_is_current:true,
 source_created_at:'2026-09-02 09:20:00',reviewed_at:'2026-09-16 10:45:00',reviewed_version:'V3'});
const missing=v.renderWorkbenchBatchRow({...base,review_state:'processing',result_is_current:true,
 source_created_at:'',modified:'2026-09-17 11:00:00',status:'Draft'});
console.log(JSON.stringify({active,confirmed,missing}));
""")
    assert '2026-09-01 08:15:00' in result['active']
    assert '2026-09-15 12:30:00' not in result['active']
    assert '待重新试算' in result['active']
    assert '2026-09-02 09:20:00' in result['confirmed']
    assert '2026-09-16 10:45:00' not in result['confirmed']
    assert '确认版本 V3' in result['confirmed']
    assert '<strong>—</strong>' in result['missing']
    assert '2026-09-17 11:00:00' not in result['missing']
    assert '<span>Draft</span>' in result['missing']


def test_batch_action_column_is_right_aligned_on_desktop_and_left_aligned_on_mobile():
    css = (PARTS / '25-workbench-redesign.css').read_text(encoding='utf-8')
    assert '.ocw-batch-grid-head > :last-child {' in css
    assert 'justify-self: end;' in css
    assert 'text-align: right;' in css
    mobile = css.split('@media (max-width: 980px) {', 1)[1]
    assert '.ocw-row-actions { justify-self: start; justify-content: flex-start; }' in mobile


def test_detail_navigation_invalidates_pending_list_without_reopening_detail():
    result=run_js("""
const v=makeView('pending'), calls=[], renders=[];
v.batches=[];v.detailState={batchName:'',requestId:0,refreshRequestId:0};
v.resetBatchResultPreview=()=>{};v.renderWorkbenchLoading=()=>{};
v.renderWorkbench=()=>renders.push('list');v.renderWorkbenchError=()=>renders.push('error');
v.renderDetailLoading=()=>{};v.renderDetailShell=()=>renders.push('detail');
v.renderDetailError=e=>{throw e};v.switchDetailTab=async()=>{};v.findBatch=()=>null;
v.getDefaultPullDateRange=()=>({start_date:'2026-08-10',end_date:'2026-09-08'});
v.$root={attr:()=>{},find:()=>({prop:()=>{}})};
v.call=(method,args)=>method.includes('get_batch_detail')
 ? (calls.push({method}),Promise.resolve({ok:true,batch_name:'DETAIL',header:{name:'DETAIL'}}))
 : new Promise(resolve=>calls.push({method,resolve}));
const oldList=v.loadBatches();
global.window={location:{href:'/desk/x?task=cost&review_status=confirmed&screen=detail&batch=DETAIL&tab=overview&page=3',pathname:'/desk/x'}};
await v.handleWorkbenchPopState();v.detailState.dirty=true;
calls[0].resolve({ok:true,items:[{name:'PENDING-1'}],total:1,page:1});
calls[1].resolve({counts:{}});await oldList;
console.log(JSON.stringify({renders,dirty:v.detailState.dirty,page:v.viewState.page,
 detailCalls:calls.filter(c=>c.method.includes('get_batch_detail')).length}));
""")
    assert result == {'renders': ['detail'], 'dirty': True, 'page': 3, 'detailCalls': 1}


@pytest.mark.parametrize('start,end', [('', ''), ('2026-01-01', ''), ('', '2026-09-08')])
def test_explicitly_cleared_date_boundaries_survive_url_reload_and_history(start, end):
    result=run_js(f"""
const expected={{start_date:{json.dumps(start)},end_date:{json.dumps(end)}}};
const url=OverseasCostWorkbenchState.buildWorkbenchUrl('/desk/x?task=cost&page=3',expected);
const v=makeView();v.detailState={{batchName:'',requestId:0,skuRequestId:0,refreshRequestId:0}};
global.window={{location:{{href:url}},history:{{state:{{}}}},scrollTo:()=>{{}}}};
global.requestAnimationFrame=()=>{{}};
v.getDefaultPullDateRange=()=>({{start_date:'2026-08-10',end_date:'2026-09-08'}});
v.$root={{find:()=>({{prop:()=>{{}}}})}};let requested;
v.loadBatches=async()=>{{requested=v.workbenchFilters()}};
await v.handleWorkbenchPopState();
console.log(JSON.stringify({{url,parsed:OverseasCostWorkbenchState.parseWorkbenchState(url),requested,page:v.viewState.page}}));
""")
    assert result['parsed']['hasDateRange'] is True
    assert result['requested']['start_date'] == start
    assert result['requested']['end_date'] == end
    assert result['page'] == 3


def test_successful_list_recalculation_refreshes_authoritative_groups_and_cards():
    result=run_js("""
global.frappe={show_alert:()=>{}};
const v=makeView('pending'), calls=[];
const old={name:'B',current_version:'V',review_state:'processing',result_is_current:false};
v.batches=[old];v.findBatch=()=>old;v.detailState={batchName:''};
v.acceptSavedComprehensiveCost=()=>{};v.applyRecalculateSummary=()=>{};
v.resetBatchResultPreview=()=>{};v.renderWorkbenchLoading=()=>{};v.renderWorkbench=()=>{};
v.loadBatchItems=async()=>{};v.loadAuditLogs=async()=>{};v.renderTable=()=>{};
v.renderRecalculateResult=()=>{};v.renderWorkbenchBatchList=()=>{};v.recordUsage=()=>{};
v.showError=e=>{throw e};v.$root={attr:()=> 'workbench'};
v.call=async(method)=>{calls.push(method);
 if(method.endsWith('acquire')) return {ok:true,edit_token:'TOKEN',modified:'BEFORE'};
 if(method.endsWith('recalculate_batch')) return {ok:true,saved:true,summary_snapshot:{total_cost_rmb:130}};
 if(method.endsWith('get_batches')) return {ok:true,items:[],total:0,page:1};
 if(method.endsWith('get_summary')) return {counts:{calculation:0},review_counts:{pending:1}};
 return {ok:true};};
await v.recalculate('B');
console.log(JSON.stringify({calls,rows:v.batches,counts:v.exceptionCounts,review:v.reviewCounts}));
""")
    assert 'overseas_costing.api.workbench.get_batches' in result['calls']
    assert 'overseas_costing.api.workbench.get_summary' in result['calls']
    assert result['rows'] == []
    assert result['counts']['calculation'] == 0
    assert result['review']['pending'] == 1
    assert result['calls'][-1] == 'overseas_costing.api.edit_session.release'


@pytest.mark.parametrize(
    ('batch', 'expected_label'),
    [
        ({'status': 'Draft', 'summary_snapshot': {}}, '开始试算'),
        ({'status': 'Dirty', 'result_is_current': False, 'summary_snapshot': {}}, '开始试算'),
        ({'status': 'Dirty', 'calculated_at': '2026-09-12 15:00:00', 'result_is_current': False}, '重新试算'),
    ],
)
def test_detail_moves_single_calculation_entry_from_removed_status_rows_into_overview(batch, expected_label):
    result = run_js(f"""
const v=makeView('pending');
const batch={{name:'B',batch_no:'B',current_version:'V',primary_issue:'calculation',item_count:1,...{json.dumps(batch)}}};
v.detailState={{batchName:'B',tab:'overview',header:batch,dingtalkApproval:{{batch_name:'B'}},detail:{{}}}};
v.batches=[batch];v.getDetailBatch=()=>batch;
v.cleanupSkuScrollControls=()=>{{}};v.cleanupMaterialGridScrollControls=()=>{{}};
v.sourceStatusLabel=()=>'已齐备';v.erpWritebackStatusInfo=()=>({{label:'未开始',state:'neutral'}});
v.batchStatusInfo=()=>({{label:'待试算',needsRecalculate:true}});v.renderDetailErpAction=()=>'';
v.issueLabel=x=>x;v.transportLabel=x=>x;v.formatValue=x=>String(x??'');
v.loadDingtalkApprovalDetail=async()=>{{}};
v.renderDetailShell();const shell=v.html["[data-area='detail-screen']"];
v.renderOverviewDetailTab();const overview=v.html["[data-area='detail-content']"];
console.log(JSON.stringify({{shell,overview}}));
""")
    assert 'ocw-detail-statusarea' not in result['shell']
    assert 'ocw-detail-review-strip' not in result['shell']
    assert 'data-primary-action="recalculate"' not in result['shell']
    assert expected_label in result['overview']
    assert result['overview'].count('data-primary-action="recalculate"') == 1
    assert 'data-action="detail-recalculate"' not in result['overview']


def test_header_calculation_entry_switches_to_documents_and_opens_trial_without_direct_recalculate():
    result = run_js("""
const v=makeView('pending'),events=[];
v.viewState={task:'pending',screen:'detail',batch:'B',tab:'overview'};
v.detailState={batchName:'B',tab:'overview'};
v.$root={attr:key=>key==='data-screen'?'detail':undefined};
v.switchDetailTab=async tab=>{events.push(`tab:${tab}`);v.detailState.tab=tab;v.viewState.tab=tab};
v.refreshMaterialFeeCostPreview=async scroll=>events.push(`trial:${scroll}`);
v.recalculate=async()=>events.push('direct-recalculate');
await v.startDetailCostTrial();
console.log(JSON.stringify({events,tab:v.detailState.tab}));
""")
    assert result == {'events': ['tab:documents', 'trial:true'], 'tab': 'documents'}


def test_header_calculation_entry_stops_if_detail_changed_while_switching_tabs():
    result = run_js("""
const v=makeView('pending'),events=[];
v.viewState={task:'pending',screen:'detail',batch:'B',tab:'overview'};
v.detailState={batchName:'B',tab:'overview'};
v.$root={attr:key=>key==='data-screen'?v.viewState.screen:undefined};
v.switchDetailTab=async tab=>{events.push(`tab:${tab}`);v.detailState={...v.detailState,batchName:'OTHER',tab};v.viewState={...v.viewState,batch:'OTHER',tab}};
v.refreshMaterialFeeCostPreview=async()=>events.push('trial');
await v.startDetailCostTrial();
console.log(JSON.stringify({events,batch:v.detailState.batchName}));
""")
    assert result == {'events': ['tab:documents'], 'batch': 'OTHER'}


def test_detail_and_workspace_sources_have_no_legacy_trial_handlers():
    workbench = (PARTS / '35-workbench-view.js').read_text(encoding='utf-8')
    workspace = (PARTS / '78-material-fee-workspace.js').read_text(encoding='utf-8')
    assert "[data-action='detail-recalculate']" not in workbench
    assert "[data-action='mf-preview-cost']" not in workspace


@pytest.mark.parametrize(
    ('task', 'batch'),
    [
        ('erp', {'status': 'Confirmed', 'confirm_status': 'Confirmed', 'is_locked': 1}),
        ('pending', {'status': 'Confirmed'}),
        ('pending', {'status': 'Calculated', 'is_locked': 1}),
    ],
)
def test_detail_hides_calculation_entry_outside_pending_editable_flow(task, batch):
    result = run_js(f"""
const v=makeView({json.dumps(task)});const batch={{name:'B',current_version:'V',...{json.dumps(batch)}}};
v.viewState={{task:{json.dumps(task)},screen:'detail',batch:'B',tab:'overview'}};
v.detailState={{batchName:'B',tab:'overview',header:batch}};v.batches=[batch];v.getDetailBatch=()=>batch;
v.cleanupSkuScrollControls=()=>{{}};v.cleanupMaterialGridScrollControls=()=>{{}};
v.sourceStatusLabel=()=>'已齐备';v.erpWritebackStatusInfo=()=>({{label:'未开始',state:'neutral'}});
v.batchStatusInfo=()=>({{label:'已试算',needsRecalculate:false}});v.renderDetailErpAction=()=>'';
v.issueLabel=x=>x;v.transportLabel=x=>x;v.formatValue=x=>String(x??'');
v.renderDetailShell();
console.log(JSON.stringify(v.html["[data-area='detail-screen']"]));
""")
    assert 'data-primary-action="recalculate"' not in result


def test_cost_review_keeps_one_retrial_entry():
    result = run_js("""
const v=makeView('cost');const batch={name:'B',current_version:'V',status:'Calculated',review_state:'ready',calculated_at:'2026-09-20'};
v.viewState={task:'cost',screen:'detail',batch:'B',tab:'overview'};
v.detailState={batchName:'B',tab:'overview',header:batch};v.batches=[batch];v.getDetailBatch=()=>batch;
v.cleanupSkuScrollControls=()=>{};v.cleanupMaterialGridScrollControls=()=>{};
v.sourceStatusLabel=()=>'已齐备';v.erpWritebackStatusInfo=()=>({label:'未开始',state:'neutral'});
v.batchStatusInfo=()=>({label:'已试算',needsRecalculate:false});v.renderDetailErpAction=()=>'';
v.issueLabel=x=>x;v.transportLabel=x=>x;v.formatValue=x=>String(x??'');
console.log(JSON.stringify(v.renderDetailOverviewDashboard(batch)));
""")
    assert result.count('data-primary-action="recalculate"') == 1
    assert '重新试算' in result


def test_gap_rows_keep_supplement_actions_but_rely_on_the_overview_calculation_entry():
    html = run_js("""
const v=makeView('pending');const batch={name:'B'};v.findBatch=()=>batch;
const html=v.renderErpFieldGapsWithActions({items:[{fieldname:'unit_price',label:'采购单价',item_name:'I-1'}]},'B',{});
console.log(JSON.stringify(html));
""")
    assert 'data-action="gap-open-item-edit"' in html
    assert 'gap-recalculate' not in html
    assert '页头' in html and '计算入口' in html


def test_saved_detail_recalculation_moves_ready_batch_to_cost_review_and_keeps_detail_open():
    result = run_js("""
global.frappe={show_alert:()=>{}};
global.window={location:{href:'/desk/x?task=pending&screen=detail&batch=B&tab=overview',pathname:'/desk/x'},history:{state:{}}};
const v=makeView('pending'), calls=[], replacements=[], opened=[];
const old={name:'B',current_version:'V',review_state:'processing',status:'Dirty'};
const ready={...old,review_state:'ready',review_blockers:[],status:'Calculated',calculated_at:'2026-09-20'};
v.viewState={task:'pending',screen:'detail',batch:'B',tab:'overview',page:1,q:''};
v.filters={review_status:'pending',review_warning:'',issue:''};v.batches=[old];
v.detailState={batchName:'B',tab:'overview',header:old,editToken:'TOKEN',expectedModified:'BEFORE'};
v.findBatch=name=>v.batches.find(row=>row.name===name)||null;v.ensureEditSession=async()=>true;
v.acceptSavedComprehensiveCost=()=>{};v.applyRecalculateSummary=()=>{};v.resetBatchResultPreview=()=>{};
v.renderWorkbenchLoading=()=>{};v.renderWorkbench=()=>{};v.renderDetailShell=()=>{};v.switchDetailTab=async()=>{};
v.refreshDetailSummary=async()=>{};v.recordUsage=()=>{};v.showError=e=>{throw e};
v.replaceViewState=values=>{replacements.push(values);v.viewState={...v.viewState,...values};};
v.openBatchDetail=async(name,tab)=>opened.push({name,tab,task:v.viewState.task});
v.call=async(method,args)=>{calls.push({method,args});
 if(method.endsWith('recalculate_batch'))return {ok:true,saved:true,summary_snapshot:{total_cost_rmb:130}};
 if(method.endsWith('get_batches')&&args.task==='cost'&&args.page_length===10)return {ok:true,items:[ready],total:1,page:1};
 if(method.endsWith('get_batches'))return {ok:true,items:[ready],total:1,page:1};
 if(method.endsWith('get_summary'))return {ok:true,counts:{},review_counts:{pending:1}};
 return {ok:true};};
await v.recalculate('B');
console.log(JSON.stringify({task:v.viewState.task,reviewStatus:v.filters.review_status,replacements,opened,calls,header:v.detailState.header}));
""")
    assert result['task'] == 'cost'
    assert result['reviewStatus'] == 'pending'
    assert result['replacements'][-1]['screen'] == 'detail'
    assert result['replacements'][-1]['batch'] == 'B'
    assert result['opened'][-1] == {'name': 'B', 'tab': 'overview', 'task': 'cost'}
    assert result['header']['review_state'] == 'ready'


def test_saved_detail_recalculation_keeps_processing_batch_pending_with_server_blocker():
    result = run_js("""
global.frappe={show_alert:()=>{}};
const v=makeView('pending'), calls=[];
const old={name:'B',current_version:'V',review_state:'processing',status:'Dirty'};
const processing={...old,review_blockers:[{code:'MISSING_PRICE',message:'还有 1 行采购单价待补'}]};
v.viewState={task:'pending',screen:'detail',batch:'B',tab:'overview',page:1,q:''};
v.filters={review_status:'pending',review_warning:'',issue:''};v.batches=[old];
v.detailState={batchName:'B',tab:'overview',header:old,editToken:'TOKEN',expectedModified:'BEFORE'};
v.findBatch=name=>v.batches.find(row=>row.name===name)||null;v.ensureEditSession=async()=>true;
v.acceptSavedComprehensiveCost=()=>{};v.applyRecalculateSummary=()=>{};v.resetBatchResultPreview=()=>{};
v.refreshDetailSummary=async()=>{};v.renderDetailShell=()=>{};v.switchDetailTab=async()=>{};
v.recordUsage=()=>{};v.showError=e=>{throw e};v.replaceViewState=values=>{v.viewState={...v.viewState,...values};};
v.loadBatches=async()=>{};
v.call=async(method,args)=>{calls.push({method,args});
 if(method.endsWith('recalculate_batch'))return {ok:true,saved:true,summary_snapshot:{total_cost_rmb:80}};
 if(method.endsWith('get_batches')&&args.task==='cost')return {ok:true,items:[],total:0,page:1};
 if(method.endsWith('get_batches')&&args.task==='pending')return {ok:true,items:[processing],total:1,page:1};
 return {ok:true};};
await v.recalculate('B');
console.log(JSON.stringify({task:v.viewState.task,header:v.detailState.header,calls}));
""")
    assert result['task'] == 'pending'
    assert result['header']['review_blockers'][0]['message'] == '还有 1 行采购单价待补'
    assert any(call['args'].get('task') == 'pending' for call in result['calls'] if call['method'].endswith('get_batches'))


def test_failed_recalculation_uses_single_locally_handled_error():
    result = run_js("""
global.frappe={show_alert:()=>{}};
const v=makeView('pending'),calls=[],errors=[];
const batch={name:'B',current_version:'V',review_state:'processing',status:'Dirty'};
v.batches=[batch];v.findBatch=()=>batch;
v.viewState={task:'pending',screen:'detail',batch:'B',tab:'documents',page:1};
v.detailState={batchName:'B',tab:'documents',header:batch,editToken:'TOKEN',expectedModified:'M1'};
v.ensureEditSession=async()=>true;v.recordUsage=()=>{};v.showError=error=>errors.push(error.message);
v.call=async(method,args,freeze=false,options={})=>{
 calls.push({method,args,freeze,options});
 if(method.endsWith('recalculate_batch'))throw new Error('还有 2 行本次发货货值缺失或失效，请先补齐后再试算。');
 return {ok:true};
};
await v.recalculate('B');
console.log(JSON.stringify({calls,errors}));
""")

    trial_call = next(call for call in result['calls'] if call['method'].endswith('recalculate_batch'))
    assert trial_call['freeze'] is True
    assert trial_call['options'] == {}
    assert result['errors'] == ['还有 2 行本次发货货值缺失或失效，请先补齐后再试算。']


def test_removed_detail_status_helpers_do_not_leave_dead_implementations():
    detail = (PARTS / '82-detail-page.js').read_text(encoding='utf-8')
    css = (PARTS / '45-detail-page.css').read_text(encoding='utf-8')
    assert 'detailStatusChip(' not in detail
    assert 'renderDetailReviewBlockers(' not in detail
    assert 'inferDetailIssue(' not in detail
    assert '.ocw-detail-statusarea' not in css
    assert '.ocw-detail-review-strip' not in css


def test_detail_overview_and_legacy_drawer_share_one_cost_flow_projection():
    drawer = (PARTS / '80-drawer-profit.js').read_text(encoding='utf-8')
    overview = drawer.split('renderBatchDrawerOverview(batch, items) {', 1)[1].split('\n  buildCostFlowPresentation(batch', 1)[0]
    erp_panel = drawer.split('renderErpFlowPanel(batch, items) {', 1)[1].split('renderBatchDrawerAllocation(', 1)[0]

    assert 'const model = this.buildCostFlowPresentation(batch, items);' in overview
    assert 'const model = this.buildCostFlowPresentation(batch, items);' in erp_panel


def test_calculation_confirmation_is_hidden_and_blocked_outside_cost_task():
    result = run_js("""
global.frappe={show_alert:()=>{}};
const v=makeView('pending'),calls=[];
const batch={name:'B',current_version:'V',status:'Calculated',item_count:1,subsidiary_code:'MX'};
v.findBatch=()=>batch;v.drawerBatchName='B';v.showPendingFeature=message=>{v.blocked=message};
v.call=async(method)=>{calls.push(method);return {ok:true,confirmed:true};};
v.recordUsage=()=>{};v.refreshBatch=async()=>{};v.showError=e=>{throw e};
v.hasText=x=>Boolean(x);v.isCalculationConfirmed=()=>false;v.batchStatusInfo=()=>({label:'已试算',needsRecalculate:false});
v.erpWritebackStatusInfo=()=>({label:'未开始',state:'neutral'});v.erpPushActionState=()=>({label:'推送 ERP',enabled:false,reason:'待核对'});
v.isPositive=x=>Number(x)>0;v.formatValue=x=>String(x??'');v.renderErpFlowBlockInline=()=>'';
const pendingHtml=v.renderErpFlowPanel(batch,[]);
await v.confirmCalculationResult('B');
v.viewState.task='cost';const costHtml=v.renderErpFlowPanel(batch,[]);
console.log(JSON.stringify({pendingHtml,costHtml,calls,blocked:v.blocked}));
""")
    assert 'data-action="confirm-calculation-result"' not in result['pendingHtml']
    assert '校验计算结果' not in result['pendingHtml']
    assert 'data-action="confirm-calculation-result"' in result['costHtml']
    assert 'data-action="confirm-calculation-result" disabled' in result['costHtml']
    assert result['calls'] == []
    assert '成本核对' in result['blocked']


def test_cost_confirmation_refreshes_authoritative_erp_queue_without_auto_push():
    result = run_js("""
global.frappe={show_alert:()=>{}};
const v=makeView('cost'),calls=[],replacements=[];
const batch={name:'B',current_version:'V',status:'Calculated',review_state:'ready'};v.batches=[batch];v.findBatch=()=>batch;
v.viewState={task:'cost',screen:'detail',batch:'B',tab:'overview',page:1};v.detailState={batchName:'B',tab:'overview'};
v.replaceViewState=values=>{replacements.push(values);v.viewState={...v.viewState,...values};};
v.loadBatches=async()=>calls.push('loadBatches');v.refreshBatch=async()=>calls.push('refreshBatch');v.recordUsage=()=>{};v.showError=e=>{throw e};
v.call=async(method,args)=>{calls.push(method);
 if(method.endsWith('get_batches'))return {ok:true,items:[batch],total:1,page:1,page_length:100};
 return {ok:true,confirmed:true,message:'已确认'};};
await v.confirmCalculationResult('B');
console.log(JSON.stringify({task:v.viewState.task,calls,replacements}));
""")
    assert result['task'] == 'erp'
    assert result['calls'].count('loadBatches') == 1
    assert not any('writeback_to_erp' in call or 'queue_erp' in call for call in result['calls'])
    assert result['replacements'][-1]['screen'] == 'detail'
    assert result['replacements'][-1]['batch'] == 'B'


def test_cost_confirmation_requires_local_ready_state_before_any_api_call():
    result = run_js("""
global.frappe={show_alert:()=>{}};
const v=makeView('cost'),calls=[];
const batch={name:'B',current_version:'V',status:'Calculated',review_state:'processing'};
v.batches=[batch];v.findBatch=()=>batch;v.drawerBatchName='B';
v.viewState={task:'cost',screen:'detail',batch:'B',tab:'overview',page:1};
v.detailState={batchName:'B',tab:'overview',header:batch};
v.showPendingFeature=message=>{v.blocked=message};v.replaceViewState=values=>{v.viewState={...v.viewState,...values};};
v.loadBatches=async()=>{};v.recordUsage=()=>{};v.showError=e=>{v.blocked=e.message};
v.call=async method=>{calls.push(method);return {ok:true}};
await v.confirmCalculationResult('B');
console.log(JSON.stringify({task:v.viewState.task,calls,blocked:v.blocked}));
""")
    assert result['calls'] == []
    assert result['task'] == 'pending'
    assert '待处理' in result['blocked']


def test_cost_confirmation_rechecks_authoritative_ready_state_before_confirming():
    result = run_js("""
global.frappe={show_alert:()=>{}};
const v=makeView('cost'),calls=[];
const batch={name:'B',batch_no:'B-NO',current_version:'V',status:'Calculated',review_state:'ready'};
v.batches=[batch];v.findBatch=()=>batch;v.drawerBatchName='B';
v.viewState={task:'cost',screen:'detail',batch:'B',tab:'overview',page:1};v.detailState={batchName:'B',tab:'overview',header:batch,requestId:4};
v.replaceViewState=values=>{v.viewState={...v.viewState,...values};};v.loadBatches=async()=>{};
v.recordUsage=()=>{};v.showError=e=>{throw e};
v.call=async(method,args)=>{calls.push({method,args});
 if(method.endsWith('get_batches'))return {ok:true,items:[batch],total:1,page:1,page_length:100};
 if(method.endsWith('confirm_calculation_result'))return {ok:true,confirmed:true};
 throw new Error(method);};
await v.confirmCalculationResult('B');
console.log(JSON.stringify({calls,task:v.viewState.task}));
""")
    assert [call['method'].rsplit('.', 1)[-1] for call in result['calls']] == [
        'get_batches',
        'confirm_calculation_result',
    ]
    assert result['calls'][0]['args']['task'] == 'cost'


def test_cost_confirmation_downgrade_blocks_confirm_and_returns_to_pending():
    result = run_js("""
const v=makeView('cost'),calls=[];
const local={name:'B',batch_no:'B-NO',current_version:'V',status:'Calculated',review_state:'ready'};
const processing={...local,review_state:'processing',review_blockers:[{code:'STALE',message:'费用已变更'}]};
v.batches=[local];v.findBatch=()=>local;v.drawerBatchName='B';
v.viewState={task:'cost',screen:'detail',batch:'B',tab:'overview',page:1};v.detailState={batchName:'B',tab:'overview',header:local,requestId:2};
v.replaceViewState=values=>{v.viewState={...v.viewState,...values};};v.loadBatches=async()=>{};
v.renderDetailShell=()=>{};v.switchDetailTab=async()=>{};v.showPendingFeature=message=>{v.blocked=message};
v.recordUsage=()=>{};v.showError=e=>{v.blocked=e.message};
v.call=async(method,args)=>{calls.push({method,args});
 if(method.endsWith('get_batches')&&args.task==='cost')return {ok:true,items:[],total:0,page:1,page_length:100};
 if(method.endsWith('get_batches')&&args.task==='pending')return {ok:true,items:[processing],total:1,page:1,page_length:100};
 throw new Error('confirmation must stay blocked');};
await v.confirmCalculationResult('B');
console.log(JSON.stringify({calls,task:v.viewState.task,header:v.detailState.header,blocked:v.blocked}));
""")
    assert [call['args']['task'] for call in result['calls']] == ['cost', 'pending']
    assert result['task'] == 'pending'
    assert result['header']['review_state'] == 'processing'
    assert '待处理' in result['blocked']


def test_authoritative_classification_walks_pages_until_exact_batch_is_found():
    result = run_js("""
const v=makeView('pending'),calls=[];
const current={name:'TARGET',batch_no:'B-NO'};v.batches=[current];v.findBatch=()=>current;v.detailState={header:current};
v.call=async(method,args)=>{calls.push(args);
 if(args.task==='cost'&&args.page===1)return {ok:true,items:Array.from({length:100},(_,i)=>({name:`OTHER-${i}`})),total:101,page:1,page_length:100};
 if(args.task==='cost'&&args.page===2)return {ok:true,items:[{...current,review_state:'ready'}],total:101,page:2,page_length:100};
 throw new Error('pending should not be queried');};
const found=await v.getAuthoritativeReviewClassification('TARGET');
console.log(JSON.stringify({found,calls}));
""")
    assert result['found']['task'] == 'cost'
    assert result['found']['batch']['name'] == 'TARGET'
    assert [call['page'] for call in result['calls']] == [1, 2]
    assert all(call['page_length'] == 100 for call in result['calls'])


def test_saved_recalculation_refresh_failure_warns_without_marking_calculation_failed():
    result = run_js("""
const alerts=[];global.frappe={show_alert:value=>alerts.push(value)};
const v=makeView('pending'),events=[];
const batch={name:'B',batch_no:'B-NO',current_version:'V',review_state:'processing',status:'Dirty'};
v.batches=[batch];v.findBatch=()=>batch;v.viewState={task:'pending',screen:'detail',batch:'B',tab:'overview',page:1};
v.detailState={batchName:'B',tab:'overview',header:batch,editToken:'TOKEN',expectedModified:'M1',requestId:1};
v.ensureEditSession=async()=>true;v.acceptSavedComprehensiveCost=()=>{};v.applyRecalculateSummary=()=>{};v.resetBatchResultPreview=()=>{};
v.call=async method=>{if(method.endsWith('recalculate_batch'))return {ok:true,saved:true,summary_snapshot:{}};throw new Error('分类服务不可用')};
v.refreshDetailSummary=async()=>{events.push('fallback')};v.recordUsage=(action,payload)=>events.push(payload.status||'Success');v.showError=e=>events.push(`error:${e.message}`);
await v.recalculate('B');
console.log(JSON.stringify({events,alerts}));
""")
    assert result['events'] == ['fallback', 'Success']
    assert any('已保存' in alert['message'] for alert in result['alerts'])
    assert not any('试算失败' in alert['message'] for alert in result['alerts'])


def test_stale_recalculation_classification_cannot_navigate_a_new_detail_context():
    result = run_js("""
const alerts=[];global.frappe={show_alert:value=>alerts.push(value)};
const v=makeView('pending'),replacements=[];
const batch={name:'B',batch_no:'B-NO',current_version:'V',review_state:'processing',status:'Dirty'};
v.batches=[batch];v.findBatch=name=>v.batches.find(row=>row.name===name)||null;
v.viewState={task:'pending',screen:'detail',batch:'B',tab:'overview',page:1};
v.detailState={batchName:'B',tab:'overview',header:batch,editToken:'TOKEN',expectedModified:'M1',requestId:7};
v.ensureEditSession=async()=>true;v.acceptSavedComprehensiveCost=()=>{};v.applyRecalculateSummary=()=>{};v.resetBatchResultPreview=()=>{};
let release;v.call=async(method,args)=>{if(method.endsWith('recalculate_batch'))return {ok:true,saved:true,summary_snapshot:{}};
 return await new Promise(resolve=>{release=resolve})};
v.replaceViewState=values=>replacements.push(values);v.loadBatches=async()=>{};v.refreshDetailSummary=async()=>{};v.recordUsage=()=>{};v.showError=e=>{throw e};
const running=v.recalculate('B');await new Promise(resolve=>setImmediate(resolve));
v.detailState.batchName='OTHER';v.detailState.requestId=8;v.viewState.batch='OTHER';
release({ok:true,items:[{...batch,review_state:'ready'}],total:1,page:1,page_length:100});await running;
console.log(JSON.stringify({task:v.viewState.task,batch:v.detailState.batchName,replacements}));
""")
    assert result == {'task': 'pending', 'batch': 'OTHER', 'replacements': []}


def test_double_confirmation_shares_one_batch_version_inflight_operation_and_releases_it():
    result = run_js("""
const alerts=[];global.frappe={show_alert:value=>alerts.push(value)};
const v=makeView('cost'),calls=[];
const batch={name:'B',batch_no:'B-NO',current_version:'V1',status:'Calculated',review_state:'ready'};
v.batches=[batch];v.findBatch=()=>batch;v.drawerBatchName='B';
v.viewState={task:'cost',screen:'detail',batch:'B',tab:'overview',page:1};v.detailState={batchName:'B',tab:'overview',header:batch,requestId:3};
v.replaceViewState=values=>{v.viewState={...v.viewState,...values}};v.loadBatches=async()=>{};
v.recordUsage=()=>{};v.showError=e=>{throw e};
const releaseClassifications=[];v.call=async(method,args)=>{calls.push({method,args});
 if(method.endsWith('get_batches'))return await new Promise(resolve=>{releaseClassifications.push(resolve)});
 if(method.endsWith('confirm_calculation_result'))return {ok:true,confirmed:true,message:'已确认'};
 throw new Error(method)};
const first=v.confirmCalculationResult('B');const second=v.confirmCalculationResult('B');
await new Promise(resolve=>setImmediate(resolve));const before=calls.map(call=>call.method);
releaseClassifications.forEach(release=>release({ok:true,items:[batch],total:1,page:1,page_length:100}));await Promise.all([first,second]);
console.log(JSON.stringify({before,calls:calls.map(call=>call.method),task:v.viewState.task,inflight:v._confirmCalculationInFlight?.size||0}));
""")
    assert len(result['before']) == 1
    assert result['calls'] == [
        'overseas_costing.api.workbench.get_batches',
        'overseas_costing.api.writeback.confirm_calculation_result',
    ]
    assert result['task'] == 'erp'
    assert result['inflight'] == 0


def test_failed_confirmation_releases_inflight_guard_for_retry():
    result = run_js("""
global.frappe={show_alert:()=>{}};
const v=makeView('cost'),calls=[];
const batch={name:'B',batch_no:'B-NO',current_version:'V1',status:'Calculated',review_state:'ready'};
v.batches=[batch];v.findBatch=()=>batch;v.drawerBatchName='B';
v.viewState={task:'cost',screen:'detail',batch:'B',tab:'overview',page:1};v.detailState={batchName:'B',tab:'overview',header:batch,requestId:3};
v.recordUsage=()=>{};v.showError=e=>{v.errors=(v.errors||0)+1};
v.call=async method=>{calls.push(method);throw new Error('分类失败')};
await v.confirmCalculationResult('B');await v.confirmCalculationResult('B');
console.log(JSON.stringify({calls,errors:v.errors,inflight:v._confirmCalculationInFlight?.size||0}));
""")
    assert result['calls'] == [
        'overseas_costing.api.workbench.get_batches',
        'overseas_costing.api.workbench.get_batches',
    ]
    assert result['errors'] == 2
    assert result['inflight'] == 0


def test_authoritative_apply_rechecks_navigation_after_list_reload():
    result = run_js("""
const v=makeView('cost'),replacements=[],renders=[];
const batch={name:'B',review_state:'ready'};const processing={name:'B',review_state:'processing'};
v.batches=[batch];v.viewState={task:'cost',screen:'detail',batch:'B',tab:'overview',page:1};
v.detailState={batchName:'B',tab:'overview',header:batch,requestId:5};
v.replaceViewState=values=>{replacements.push(values);v.viewState={...v.viewState,...values}};
v.renderDetailShell=()=>renders.push('render');v.switchDetailTab=async()=>renders.push('tab');
let release;v.loadBatches=()=>new Promise(resolve=>{release=resolve});
const context=v.captureReviewNavigationContext('B');
const running=v.applyAuthoritativeReviewClassification('B',{task:'pending',batch:processing},context);
await new Promise(resolve=>setImmediate(resolve));
v.viewState.task='erp';v.detailState.header={name:'B',marker:'new-navigation'};
release();const applied=await running;
console.log(JSON.stringify({applied,task:v.viewState.task,header:v.detailState.header,renders,replacements}));
""")
    assert result['applied'] is False
    assert result['task'] == 'erp'
    assert result['header'] == {'name': 'B', 'marker': 'new-navigation'}
    assert result['renders'] == []


def test_authoritative_cost_list_placement_starts_review_even_with_processing_state():
    result = run_js("""
const v=makeView('pending'),replacements=[];
const batch={name:'B',review_state:'processing',cost_review_started:true};
v.batches=[batch];v.viewState={task:'pending',screen:'detail',batch:'B',tab:'documents',page:1};
v.detailState={batchName:'B',tab:'documents',header:batch,requestId:2};
v.replaceViewState=values=>{replacements.push(values);v.viewState={...v.viewState,...values}};
v.loadBatches=async()=>{};
const context=v.captureReviewNavigationContext('B');
const applied=await v.applyAuthoritativeReviewClassification('B',{task:'cost',batch},context);
console.log(JSON.stringify({applied,task:v.viewState.task,reviewStatus:v.filters.review_status,replacements}));
""")
    assert result['applied'] is True
    assert result['task'] == 'cost'
    assert result['reviewStatus'] == 'pending'
    assert result['replacements'][-1]['screen'] == 'detail'


def test_confirm_success_after_navigation_does_not_overwrite_new_view():
    result = run_js("""
const alerts=[];global.frappe={show_alert:value=>alerts.push(value)};
const v=makeView('cost'),calls=[],replacements=[],usage=[];
const batch={name:'B',batch_no:'B-NO',current_version:'V1',status:'Calculated',review_state:'ready'};
v.batches=[batch];v.findBatch=()=>batch;v.drawerBatchName='B';
v.viewState={task:'cost',screen:'detail',batch:'B',tab:'overview',page:1};v.detailState={batchName:'B',tab:'overview',header:batch,requestId:1};
v.replaceViewState=values=>{replacements.push(values);v.viewState={...v.viewState,...values}};v.loadBatches=async()=>{};
v.recordUsage=(action,payload)=>usage.push({action,status:payload.status||'Success'});v.showError=e=>{throw e};
let releaseConfirm;v.call=async(method,args)=>{calls.push(method);
 if(method.endsWith('get_batches'))return {ok:true,items:[batch],total:1,page:1,page_length:100};
 if(method.endsWith('confirm_calculation_result'))return await new Promise(resolve=>{releaseConfirm=resolve});
 throw new Error(method)};
const running=v.confirmCalculationResult('B');await new Promise(resolve=>setImmediate(resolve));
v.viewState={...v.viewState,task:'pending',batch:'OTHER'};v.detailState={...v.detailState,batchName:'OTHER',requestId:2};
releaseConfirm({ok:true,confirmed:true,message:'服务端已确认'});await running;
console.log(JSON.stringify({task:v.viewState.task,batch:v.detailState.batchName,replacements,alerts,usage,calls}));
""")
    assert result['task'] == 'pending'
    assert result['batch'] == 'OTHER'
    assert result['replacements'] == []
    assert any('已确认' in alert['message'] for alert in result['alerts'])
    assert result['usage'] == [{'action': 'CONFIRM_RESULT', 'status': 'Success'}]


def test_authoritative_classification_falls_through_to_erp_then_confirmed_scope():
    result = run_js("""
const v=makeView('cost'),calls=[];const batch={name:'B',batch_no:'B-NO',review_state:'confirmed',confirm_status:'Confirmed'};
v.batches=[batch];v.findBatch=()=>batch;v.detailState={header:batch};
let mode='erp';v.call=async(method,args)=>{const filters=JSON.parse(args.filters_json);calls.push({task:args.task,review_status:filters.review_status});
 if(mode==='erp'&&args.task==='erp')return {ok:true,items:[batch],total:1,page:1};
 if(mode==='confirmed'&&args.task==='cost'&&filters.review_status==='confirmed')return {ok:true,items:[batch],total:1,page:1};
 return {ok:true,items:[],total:0,page:1}};
const erp=await v.getAuthoritativeReviewClassification('B');const erpCalls=calls.splice(0);
mode='confirmed';const confirmed=await v.getAuthoritativeReviewClassification('B');
console.log(JSON.stringify({erp,erpCalls,confirmed,confirmedCalls:calls}));
""")
    assert result['erp']['task'] == 'erp'
    assert [call['task'] for call in result['erpCalls']] == ['cost', 'pending', 'erp']
    assert result['confirmed']['task'] == 'confirmed'
    assert [call['task'] for call in result['confirmedCalls']] == ['cost', 'pending', 'erp', 'cost']
    assert result['confirmedCalls'][-1]['review_status'] == 'confirmed'


def test_unknown_authoritative_classification_only_refreshes_current_detail():
    result = run_js("""
const v=makeView('cost'),replacements=[];const batch={name:'B',batch_no:'B-NO',review_state:'ready'};
v.batches=[batch];v.findBatch=()=>batch;v.viewState={task:'cost',screen:'detail',batch:'B',tab:'overview',page:1};
v.detailState={batchName:'B',tab:'overview',header:batch,requestId:4};
v.call=async()=>({ok:true,items:[],total:0,page:1});v.replaceViewState=values=>replacements.push(values);
v.refreshDetailSummary=async()=>{v.refreshed=(v.refreshed||0)+1};v.loadBatches=async()=>{v.loaded=(v.loaded||0)+1};
const found=await v.getAuthoritativeReviewClassification('B');await v.refreshRecalculatedDetailClassification('B');
console.log(JSON.stringify({found,task:v.viewState.task,replacements,refreshed:v.refreshed||0,loaded:v.loaded||0}));
""")
    assert result['found']['task'] == 'unknown'
    assert result['task'] == 'cost'
    assert result['replacements'] == []
    assert result['refreshed'] == 1
    assert result['loaded'] == 0


def test_finance_remediation_drawer_is_client_draft_until_one_formal_submit():
    source = (PARTS / '88-review-communication.js').read_text(encoding='utf-8')
    css = (PARTS / '52-review-communication.css').read_text(encoding='utf-8')

    assert 'data-action="open-review-remediation"' in source
    assert '提出整改' in source
    assert '退回整改 (' in source
    assert 'overseas_costing.api.review.return_for_remediation' in source
    assert 'data-review-draft-description' in source
    assert 'data-review-draft-attachment' in source
    assert '问题类型' not in source
    assert '责任方' not in source
    assert '.ocw-review-drawer__body' in css
    assert 'overflow-y: auto' in css
    assert '.ocw-review-drawer__footer' in css
    assert '.ocw-review-draft-card > label {' in css
    label_rule = css.split('.ocw-review-draft-card > label {', 1)[1].split('}', 1)[0]
    textarea_rule = css.split('.ocw-review-draft-card textarea {', 1)[1].split('}', 1)[0]
    assert 'display: block' in label_rule
    assert 'width: 100%' in label_rule
    assert 'display: block' in textarea_rule
    assert 'box-sizing: border-box' in textarea_rule


def test_review_communication_tab_supports_reply_resubmit_resolution_and_direct_target():
    source = (PARTS / '88-review-communication.js').read_text(encoding='utf-8')
    detail_source = (PARTS / '82-detail-page.js').read_text(encoding='utf-8')

    assert '复核沟通' in detail_source
    assert 'renderReviewCommunicationTab' in detail_source
    assert 'overseas_costing.api.review.get_review_communication' in source
    assert 'overseas_costing.api.review.address_review_issue' in source
    assert 'overseas_costing.api.review.resubmit_review_round' in source
    assert 'overseas_costing.api.review.resolve_review_issue' in source
    assert '回复并标记已处理' in source
    assert '提交财务复核' in source
    assert '确认整改完成' in source
    assert '去修改' in source
    assert '返回整改问题' in source
    assert 'scrollIntoView' in source
    assert 'ocw-review-target-highlight' in source


def test_contextual_feedback_hooks_only_use_stable_fee_and_sku_targets():
    review_source = (PARTS / '88-review-communication.js').read_text(encoding='utf-8')
    fee_source = (PARTS / '78-material-fee-workspace.js').read_text(encoding='utf-8')
    detail_source = (PARTS / '82-detail-page.js').read_text(encoding='utf-8')

    assert 'renderReviewFeedbackButton' in review_source
    assert 'renderReviewFeedbackButton' in fee_source
    assert 'renderReviewFeedbackButton' in detail_source
    assert 'target_tab' in review_source
    assert 'target_field' in review_source
    assert 'target_row' in review_source
    assert 'target_item' in review_source


def test_browser_history_navigation_does_not_discard_review_draft_without_confirmation():
    result = run_js("""
global.window={location:{href:'/desk/x?task=pending',pathname:'/desk/x'},history:{state:{},pushState:()=>{},replaceState:()=>{}},scrollTo:()=>{}};
global.requestAnimationFrame=callback=>callback();
const v=makeView('cost');v.viewState={task:'cost',screen:'detail',batch:'B',tab:'documents',page:1,q:''};
v.detailState={batchName:'B',tab:'documents',header:{name:'B'},requestId:1,skuRequestId:0,refreshRequestId:0};
v.getDefaultPullDateRange=()=>({start_date:'2026-07-01',end_date:'2026-09-21'});
v.confirmDiscardDetailChanges=async()=>{v.confirmed=(v.confirmed||0)+1;return false};
v.openBatchDetail=async()=>{v.opened=(v.opened||0)+1};v.loadBatches=async()=>{v.loaded=(v.loaded||0)+1};
await v.handleWorkbenchPopState();
console.log(JSON.stringify({confirmed:v.confirmed||0,opened:v.opened||0,loaded:v.loaded||0,screen:v.viewState.screen,batch:v.viewState.batch}));
""")
    assert result == {'confirmed': 1, 'opened': 0, 'loaded': 0, 'screen': 'detail', 'batch': 'B'}


def test_remediation_audit_actions_have_human_readable_labels():
    source = (PARTS / '90-audit-logs.js').read_text(encoding='utf-8')
    assert 'REVIEW_RETURN: ocTranslate("退回整改")' in source
    assert 'REVIEW_REPLY: ocTranslate("整改回复")' in source
    assert 'REVIEW_RESUBMIT: ocTranslate("提交财务复核")' in source
    assert 'REVIEW_RESOLVE: ocTranslate("确认整改")' in source


def test_erp_queue_offers_not_started_filter_and_links_pushed_rows_to_erp():
    result = run_js("""
const v=makeView('erp');
v.filters={review_status:'pending',review_warning:'',issue:'',erp_status:''};
v.renderWorkbenchSearch();
const search=v.html["[data-area='search-panel']"]||'';
const pushed=v.renderWorkbenchBatchRow({name:'S',batch_no:'202609221831000580722',writeback_status:'Success',confirm_status:'Confirmed',review_state:'confirmed',item_count:25});
const fresh=v.renderWorkbenchBatchRow({name:'N',batch_no:'202609221831000581000',writeback_status:'Not Started',confirm_status:'Confirmed',review_state:'confirmed',item_count:3});
console.log(JSON.stringify({
 not_started_option: search.includes('value="not_started"'),
 not_started_label: search.includes('未推送'),
 pushed_uses_view_label: pushed.includes('查看 ERP'),
 pushed_keeps_erp_entry: pushed.includes('data-primary-action="erp_retry"'),
 fresh_uses_process_label: fresh.includes('处理 ERP'),
}));
""")
    assert result == {
        'not_started_option': True,
        'not_started_label': True,
        'pushed_uses_view_label': True,
        'pushed_keeps_erp_entry': True,
        'fresh_uses_process_label': True,
    }


def test_detail_erp_panel_lists_local_sync_queue_with_per_request_status():
    """账本逐条请求要摊开：站点级「还欠处理」看不出这一票欠了哪几条。"""

    result = run_js("""
const v=makeView('erp');
v.erpSiteState={batchName:'B',plan:null,ledger:{items:[
 {request_id:'aaaaaaaaaaaa1111',site_code:'S1',operation:'CREATE',status:'PENDING',attempt_count:0,cost_result_hash:'h1'},
 {request_id:'bbbbbbbbbbbb2222',site_code:'DEEPLINKERP',operation:'CREATE',status:'FAILED',attempt_count:3,error_code:'ERP_PUSH_FAILED',error_message:'远端拒绝建单',cost_result_hash:''},
 {request_id:'cccccccccccc3333',site_code:'DEEPLINKERP',operation:'CREATE',status:'SUCCESS',attempt_count:1,cost_result_hash:'h2'},
 {request_id:'dddddddddddd4444',site_code:'DEEPLINKERP',operation:'CREATE',status:'UNCERTAIN',attempt_count:1,cost_result_hash:'h2'},
]},work:null,failure:'',loading:false,requestId:0,inFlight:new Set()};
const html=v.renderErpSiteQueueBlock();
const unknown=v.erpSiteRequestStatusMeta('WEIRD').label;
v.erpSiteState.ledger=null;
const noLedger=v.renderErpSiteQueueBlock();
v.erpSiteState.ledger={items:[]};
const empty=v.renderErpSiteQueueBlock();
console.log(JSON.stringify({
 has_area: html.includes('data-area="erp-sync-queue"'),
 counted: html.includes('本地同步队列（4 条）'),
 pending: html.includes('待推送'),
 failed: html.includes('推送失败'),
 success: html.includes('已推送'),
 uncertain: html.includes('结果待核对'),
 retries: html.includes('已尝试 3 次'),
 reason: html.includes('远端拒绝建单'),
 row_keyed: html.includes('data-request-id="bbbbbbbbbbbb2222"'),
 unknown_falls_back: unknown==='WEIRD',
 hidden_without_ledger: noLedger==='',
 empty_message: empty.includes('尚未生成同步请求'),
}));
""", with_erp_sites=True)
    assert result == {
        'has_area': True,
        'counted': True,
        'pending': True,
        'failed': True,
        'success': True,
        'uncertain': True,
        'retries': True,
        'reason': True,
        'row_keyed': True,
        'unknown_falls_back': True,
        'hidden_without_ledger': True,
        'empty_message': True,
    }


def test_detail_erp_status_panel_mounts_sync_queue_block():
    """方法写了不等于挂上去了：站点面板必须真的渲染队列块。"""

    source = (PARTS / '79-erp-sites.js').read_text(encoding='utf-8')
    assert 'renderErpSiteStatusPanel() {' in source
    body = source.split('renderErpSiteStatusPanel() {', 1)[1].split('\n  }', 1)[0]
    assert 'renderErpSiteQueueBlock()' in body
