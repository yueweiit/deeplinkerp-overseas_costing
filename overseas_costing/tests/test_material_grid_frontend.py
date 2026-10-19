import re

from overseas_costing.tests.test_workbench_frontend_state import _fee_workspace_result, PARTS


def test_grid_renderer_preserves_source_blanks_and_separates_results():
    result = _fee_workspace_result('''
    const w = Object.create(Harness.prototype);
    w.escape = v => String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
    w.formatDateTimeMinute = v => v || '';
    w.formatValue = v => String(v ?? '');
    const preview = {source:{kind:'wiki_sheet',label:'<script>bad</script>'},
      source_grid:{header_row:1,cells:[
        [{display_value:'物料'},{display_value:'数量'}],
        [{display_value:'FL000429'},{raw_value:96000,display_value:'96,000'}],
        [{display_value:''},{raw_value:4000,display_value:'4,000'}]],
        merge_ranges:[],candidate_regions:[{start_row:2,end_row:3,start_column:1,end_column:1,reason:'编码待核对'}]},
      rows:[{source_row:2,source_rows:[2,3],incoming:{material_code:'FL000429',actual_shipped_qty:'100000'},physical_status:'incomplete',changes:[]}],
      out_of_batch_groups:[{group_id:'package-9'}]};
    const html=w.renderWikiMaterialImportPreview(preview);
    console.log(JSON.stringify({html}));
    ''')
    html = result['html']
    assert 'data-pg-tab="source"' in html and 'aria-selected="true"' in html
    assert 'data-pg-panel="result"' in html and 'hidden' in html
    assert 'data-pg-cell="3:1"' in html and 'rowspan=' not in html
    assert '96,000' in html and '100000' in html
    assert '净重 kg</th>' in html and '毛重 kg</th>' in html and '体积 m³</th>' in html
    assert '<script>' not in html
    assert 'package-9' not in html
    assert '确认写入物料表' in html and 'class="pg-primary"' in html


def test_grid_real_merge_and_copy_keep_display_and_formula():
    result = _fee_workspace_result('''
    const w=Object.create(Harness.prototype);w.escape=v=>String(v??'');w.formatDateTimeMinute=v=>v;
    const grid={header_row:1,cells:[[{display_value:'物料'}],[{raw_value:'SKU',display_value:'SKU',formula:'=A1'}],[{display_value:''}]],
      merge_ranges:[{start_row:2,end_row:3,start_column:1,end_column:1,evidence_kind:'xlsx_merge'}]};
    const html=w.renderWikiMaterialImportPreview({source:{},source_grid:grid,rows:[]});
    console.log(JSON.stringify({html, text:w.packingSelectionText(grid,{start_row:2,end_row:3,start_column:1,end_column:1}), range:w.parsePackingRange('AA2:AB4')}));
    ''')
    assert 'rowspan="2"' in result['html']
    assert 'data-pg-cell="3:1"' not in result['html']
    assert result['text'] == 'SKU\n'
    assert result['range'] == dict(start_row=2,end_row=4,start_column=27,end_column=28)


def test_grid_blocker_requires_coordinate_reviews_without_internal_ids():
    result = _fee_workspace_result('''
    const w=Object.create(Harness.prototype);
    const preview={source_grid:{cells:[[]]},confirmation_groups:[{group_id:'package-9',row_numbers:[4,5]}]};
    const issue=w.validateWikiMaterialAllocations(preview,{group_confirmations:{'package-9':true}});
    console.log(JSON.stringify({issue}));
    ''')
    assert '4' in result['issue'] and '5' in result['issue']
    assert '原表' in result['issue'] and 'package-9' not in result['issue']


def test_grid_dialog_has_scoped_visible_buttons_and_internal_scroll():
    css_path = PARTS / '49-material-import-grid.css'
    css = css_path.read_text() if css_path.exists() else ''
    assert '.pg-dialog .pg-primary' in css
    assert 'background: #087ef5' in css
    assert 'color: #fff' in css
    assert '.pg-footer' in css and 'flex-shrink: 0' in css
    assert '.pg-grid-scroll' in css and 'overflow: auto' in css
    assert 'position: sticky' in css


def test_grid_constrains_real_frappe_anonymous_body_wrapper_and_native_form():
    css = (PARTS / '49-material-import-grid.css').read_text()
    assert '.pg-dialog .modal-body > div' in css
    assert '.pg-dialog .form-column > form' in css
    assert 'min-width: 0' in css and 'min-height: 0' in css


def test_material_toolbar_wraps_without_clipping_actions():
    css = (PARTS / '48-material-fee-workspace.css').read_text()
    actions = css.split('.ocw-mf-material-toolrow {', 1)[1].split('}', 1)[0]
    groups = css.split('.ocw-mf-material-toolgroup {', 1)[1].split('}', 1)[0]
    title = css.split('.ocw-mf-material-title > div:first-child {', 1)[1].split('}', 1)[0]
    responsive = css.split('@media (max-width: 700px) {', 1)[1].split('.ocw-mf-selection-toolbar', 1)[0]
    assert 'flex-wrap: wrap' in actions
    assert 'justify-content: space-between' in actions
    assert 'overflow: hidden' not in actions and 'flex-wrap: nowrap' not in actions
    assert 'flex-wrap: wrap' in groups and 'min-width: 0' in groups
    assert 'min-width: 0' in title
    assert '.ocw-mf-material-toolrow' in responsive and 'flex-direction: column' in responsive
    assert '.ocw-mf-material-toolgroup.is-data' in responsive
    assert 'width: 100%' in responsive and 'justify-content: flex-start' in responsive


def test_uploaded_xlsx_with_true_grid_uses_same_two_tab_dialog():
    result = _fee_workspace_result('''
    const w=Object.create(Harness.prototype);let configured=0,options;
    w.renderWikiMaterialImportPreview=()=>'<div>GRID</div>';
    w.setupPackingSpreadsheetDialog=()=>configured++;
    global.frappe.ui={Dialog:class {constructor(o){options=o;this.$wrapper={addClass(){return this},on(){return this}}}show(){}}};
    w.openMaterialImportPreviewDialog({source:{kind:'manual_xlsx'},source_grid:{cells:[[]]},rows:[]});
    console.log(JSON.stringify({configured,html:options.fields[0].options,nativePrimary:!!options.primary_action}));
    ''')
    assert result == {'configured':1,'html':'<div>GRID</div>','nativePrimary':False}


def test_range_adjustment_replaces_selected_review_and_undo_is_chronological():
    result = _fee_workspace_result('''
    const w=Object.create(Harness.prototype);let range='B2:B4',requests=[];
    const selection={start_row:2,end_row:3,start_column:2,end_column:2,evidence_kind:'manual_confirmed'};
    const old=[{...selection,action:'confirm'},{start_row:10,end_row:11,start_column:4,end_column:4,action:'separate'}];
    delete old[0].evidence_kind;
    const nodes={val:()=>range,prop(){return this},text(){return this}};
    const preview={source:{kind:'wiki_sheet',id:'WB:S',source_hash:'HASH'},rows:[]};
    const dialog={_ocwMaterialPreview:preview,_pg:{reviews:old,selection,reviewHistory:[],busy:false,closed:false},$wrapper:{find:()=>nodes},fields_dict:{preview:{$wrapper:{html(){}}}}};
    w.detailState={batchName:'B'};w.updatePackingPreviewBlocker=()=>{};w.switchPackingPreviewTab=()=>{};w.renderWikiMaterialImportPreview=()=>'';
    w.call=async(method,args)=>{requests.push(JSON.parse(args.merge_reviews_json).ranges);return {ok:true,...preview,merge_reviews:{ranges:requests.at(-1)}}};
    await w.reviewPackingGridRange(dialog,'confirm');await w.reviewPackingGridRange(dialog,'undo');
    console.log(JSON.stringify({requests,old}));
    ''')
    assert len(result['requests'][0]) == 2
    assert any(r['start_row']==2 and r['end_row']==4 for r in result['requests'][0])
    assert result['requests'][1] == result['old']


def test_range_preview_cancel_ignores_late_result_and_suppresses_double_click():
    result = _fee_workspace_result('''
    const w=Object.create(Harness.prototype);let calls=0,renders=0,release;
    const nodes={val:()=>'B2:B3',prop(){return this},text(){return this}};
    const preview={source:{kind:'wiki_sheet',id:'WB:S',source_hash:'HASH'},rows:[]};
    const dialog={_ocwMaterialPreview:preview,_pg:{reviews:[],reviewHistory:[],busy:false,closed:false},$wrapper:{find:()=>nodes},fields_dict:{preview:{$wrapper:{html(){renders++}}}}};
    w.detailState={batchName:'B'};w.updatePackingPreviewBlocker=()=>{};
    w.call=async()=>{calls++;return await new Promise(r=>release=r)};
    const first=w.reviewPackingGridRange(dialog,'confirm');await w.reviewPackingGridRange(dialog,'confirm');
    dialog._pg.closed=true;release({ok:true,...preview});await first;
    console.log(JSON.stringify({calls,renders,reviews:dialog._pg.reviews,busy:dialog._pg.busy}));
    ''')
    assert result == {'calls':1,'renders':0,'reviews':[],'busy':False}


def test_writing_blocks_modal_close_and_duplicate_submit_then_recovers_on_error():
    result = _fee_workspace_result('''
    const w=Object.create(Harness.prototype);let hide,calls=0,release,prevented=false,disabled=[];
    global.document={};global.$=()=>({off(){}});
    const node={prop(name,value){disabled.push(value);return this},text(){return this}};
    const wrapper={addClass(){return this},on(event,...args){if(event==='hide.bs.modal.pg')hide=args[0];return this},find(){return node}};
    const dialog={$wrapper:wrapper};w.switchPackingPreviewTab=()=>{};w.updatePackingPreviewBlocker=()=>{};
    w.setupPackingSpreadsheetDialog(dialog,{rows:[]});
    w.applyMaterialImport=async()=>{calls++;await new Promise((resolve,reject)=>release=reject)};
    const first=w.confirmPackingSpreadsheet(dialog);await w.confirmPackingSpreadsheet(dialog);
    hide({preventDefault(){prevented=true}});const closed=dialog._pg.closed;
    release(new Error('事务失败'));await first;
    console.log(JSON.stringify({calls,prevented,closed,writing:dialog._pg.writing,busy:dialog._pg.busy,disabled}));
    ''')
    assert result['calls'] == 1 and result['prevented'] is True and result['closed'] is False
    assert result['writing'] is False and result['busy'] is False
    assert result['disabled'][0] is True and result['disabled'][-1] is False


def test_conflicting_real_merges_require_source_correction_not_unavailable_checkbox():
    result = _fee_workspace_result('''
    const w=Object.create(Harness.prototype);
    console.log(JSON.stringify({issue:w.validateWikiMaterialAllocations({source_grid:{cells:[[]]},
      confirmation_groups:[{group_id:'hidden',row_numbers:[2,3,4],source_correction_required:true}]},{})}));
    ''')
    assert '修正原表' in result['issue'] and '2、3、4' in result['issue']


def test_project_and_supplier_cells_use_list_pickers_in_the_main_grid():
    result = _fee_workspace_result(r'''
    const w=new Harness();w.escape=value=>String(value??'');w.formatValue=value=>String(value??'--');
    w.detailState={batchName:'B-1',versionName:'V-1',tab:'documents'};
    const state=w.ensureMaterialFeeState();state.materials={items:[]};
    const columns=w.materialFeeGridColumns();
    const project=w.renderMaterialFeeGridCell({name:'I-1',project_collection:'',requirements:{}},columns.find(row=>row.field==='project_collection'),new Set(),1);
    const invalid=w.renderMaterialFeeGridCell({name:'I-2',project_collection:'YW ODM',requirements:{}},columns.find(row=>row.field==='project_collection'),new Set(),1);
    const supplier=w.renderMaterialFeeGridCell({name:'I-3',supplier:'SUP-001',requirements:{}},columns.find(row=>row.field==='supplier'),new Set(),1);
    console.log(JSON.stringify({fields:columns.map(row=>row.field),project,invalid,supplier}));
    ''')
    assert "supplier" in result["fields"]
    assert result["fields"].index("supplier") < result["fields"].index("project_collection")
    assert 'data-action="mf-open-project-picker"' in result["project"]
    assert '选择项目归属' in result["project"]
    assert '修正' in result["invalid"] and 'YW ODM' in result["invalid"]
    assert 'data-action="mf-open-supplier-picker"' in result["supplier"]
    assert 'data-mf-cell-input' not in result["project"] + result["invalid"] + result["supplier"]


def test_project_picker_groups_candidates_filters_and_keeps_invalid_current_read_only():
    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.escape=value=>String(value??'');
    const optionsResult={ok:true,route_revision:'R-1',conflicts:[],options:[
      {project_collection:'LatinGo拉丁购',subsidiary_code:'拉丁购国际电子商务（东莞）有限公司',is_approval_candidate:true},
      {project_collection:'YW MOLDES MX模具',subsidiary_code:'YW MOLDES MX模具',is_approval_candidate:false}
    ]};
    const model=w.buildProjectPickerModel([{name:'I-1',project_collection:'YW ODM'}],optionsResult,'');
    const all=w.renderProjectPickerOptions(model);
    const filtered=w.renderProjectPickerOptions({...model,search:'moldes'});
    console.log(JSON.stringify({model,all,filtered}));
    ''')
    assert result["model"]["invalidCurrent"] == ["YW ODM"]
    assert '当前值：YW ODM' in result["all"]
    assert '无有效 ERP 路由' in result["all"]
    assert '本审批候选' in result["all"] and '其他可选项目' in result["all"]
    assert '拉丁购国际电子商务' in result["all"]
    assert 'value="YW ODM"' not in result["all"]
    assert 'YW MOLDES MX模具' in result["filtered"]
    assert 'LatinGo拉丁购' not in result["filtered"]


def test_project_route_options_are_cached_per_batch_and_refresh_on_revision_or_save():
    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.detailState={batchName:'B-1'};w.ensureMaterialFeeState();
    let calls=0;w.call=async()=>({ok:true,route_revision:`R-${++calls}`,options:[{project_collection:`P-${calls}`,subsidiary_code:'C'}]});
    const first=await w.loadProjectRouteOptions();
    const cached=await w.loadProjectRouteOptions();
    const revised=await w.loadProjectRouteOptions({expectedRevision:'different'});
    w.invalidateProjectRouteOptions();
    const afterSave=await w.loadProjectRouteOptions();
    console.log(JSON.stringify({calls,first:first.route_revision,cached:cached.route_revision,revised:revised.route_revision,afterSave:afterSave.route_revision}));
    ''')
    assert result == {"calls": 3, "first": "R-1", "cached": "R-1", "revised": "R-2", "afterSave": "R-3"}


def test_project_selection_needs_no_reason_keeps_exact_rows_and_ai_draft_non_mutating():
    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.detailState={batchName:'B-1',versionName:'V-1',editToken:'T',expectedModified:'M'};
    const state=w.ensureMaterialFeeState();state.materials={items:[]};
    w.isMaterialAIReadyStatus=status=>status==='READY';w.renderMaterialFeeWorkspacePreservingPosition=()=>{};
    const blank={name:'A',stable_line_key:'L-A',material_code:'A',project_collection:''};
    const existing={name:'B',stable_line_key:'L-B',material_code:'B',project_collection:'YW ODM'};
    state.aiFill={status:'READY',draftVisible:true,review_mode:true,updates:{},manualUpdates:{},proposals:[],selections:new Set()};
    let writes=0;w.call=async()=>{writes++;return {ok:true}};
    await w.applyProjectCollectionSelection([existing],'LatinGo拉丁购',{aiDraft:true,allowedValues:new Set(['LatinGo拉丁购'])});
    const draft={...state.aiFill.manualUpdates['B:project_collection']};
    state.aiFill=null;w.ensureEditSession=async()=>true;w.updateMaterialFeeExpectedModified=()=>{};w.loadMaterialFeeWorkspace=async()=>true;w.loadProjectRouteOptions=async()=>({ok:true});
    let payload=null;w.call=async(endpoint,args)=>{writes++;payload={endpoint,args};return {ok:true,changed_count:2}};
    await w.applyProjectCollectionSelection([blank,existing],'LatinGo拉丁购',{allowedValues:new Set(['LatinGo拉丁购'])});
    console.log(JSON.stringify({writes,draft,updates:JSON.parse(payload.args.updates)}));
    ''')
    assert result["draft"] == {
        "item_name": "B",
        "fieldname": "project_collection",
        "value": "LatinGo拉丁购",
    }
    assert result["writes"] == 1
    assert [row["item_name"] for row in result["updates"]] == ["A", "B"]
    assert all(row["remark"] == "设置 ERP 项目归属" for row in result["updates"])


def test_project_single_bulk_and_correction_entries_share_one_picker():
    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.detailState={batchName:'B'};const state=w.ensureMaterialFeeState();
    state.materials={packing_group_editable:true,packing_groups:[],items:[
      {name:'A',stable_line_key:'L-A',project_collection:''},{name:'B',stable_line_key:'L-B',project_collection:'YW ODM'}
    ]};state.packingGroupSelections=new Set(['L-A']);
    const calls=[];w.openProjectCollectionPicker=async request=>{calls.push({source:request.source,names:request.items.map(row=>row.name),suggestedValue:request.suggestedValue||''})};
    await w.openProjectCollectionForItem('A');await w.openProjectCollectionForItem('B');await w.openProjectCollectionDialog();
    state.aiFill={status:'READY',draftVisible:true,updates:{'A:project_collection':{value:'LatinGo拉丁购'}},manualUpdates:{}};
    await w.openProjectCollectionForItem('A');
    console.log(JSON.stringify({calls}));
    ''')
    assert result["calls"] == [
        {"source": "cell", "names": ["A"], "suggestedValue": ""},
        {"source": "cell", "names": ["B"], "suggestedValue": ""},
        {"source": "bulk", "names": ["A"], "suggestedValue": ""},
        {"source": "cell", "names": ["A"], "suggestedValue": "LatinGo拉丁购"},
    ]


def test_bulk_project_dialog_lists_every_selected_row_for_per_row_assignment():
    """批量归属必须能逐行指定：每行正确归属都不一样，一个共用值套不住。"""

    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.escape=value=>String(value??'');
    const optionsResult={ok:true,route_revision:'R-1',conflicts:[],options:[
      {project_collection:'LatinGo拉丁购',subsidiary_code:'拉丁购国际电子商务（东莞）有限公司',is_approval_candidate:true},
      {project_collection:'YW MOLDES MX模具',subsidiary_code:'YW MOLDES MX模具',is_approval_candidate:false}
    ]};
    const items=[
      {name:'A',material_code:'FL-1',project_collection:'LatinGo拉丁购'},
      {name:'B',material_code:'FL-2',project_collection:'YW ODM'},
      {name:'C',material_code:'FL-3',project_collection:''}
    ];
    const model=w.buildProjectPickerModel(items,optionsResult,'');
    console.log(JSON.stringify({assignments:model.assignments,rows:w.renderProjectAssignRows(model),table:w.renderProjectAssignTable(model),count:w.renderProjectAssignCount(model)}));
    ''')
    # 当前值本身有效就沿用，无效/为空留空等人工指定（不能把产品线当成合法起点）。
    assert result["assignments"] == {"A": "LatinGo拉丁购", "B": "", "C": ""}
    assert result["rows"].count('data-mf-assign-row="') == 3
    assert '<select data-mf-assign-row="B">' in result["rows"]
    assert '<option value="LatinGo拉丁购" selected>' in result["rows"]
    assert '<optgroup label="本审批候选">' in result["rows"] and '<optgroup label="其他可选项目">' in result["rows"]
    assert '拉丁购国际电子商务（东莞）有限公司' in result["rows"]
    assert result["rows"].count("无有效 ERP 路由") == 1
    assert 'class="ocw-mf-assign-current is-invalid"' in result["rows"]
    assert result["count"] == "显示 3 / 共 3 行 · 已指定 1 行 · 当前值无有效路由 1 行"
    assert 'data-mf-assign-all' in result["table"] and 'data-mf-assign-rows' in result["table"]
    assert "逐行指定后一次提交" in result["table"]


def test_bulk_project_assignment_search_narrows_rows_and_keeps_chosen_values():
    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.escape=value=>String(value??'');
    const optionsResult={ok:true,route_revision:'R-1',conflicts:[],options:[
      {project_collection:'LatinGo拉丁购',subsidiary_code:'C1',is_approval_candidate:true},
      {project_collection:'YW MOLDES MX模具',subsidiary_code:'C2',is_approval_candidate:false}
    ]};
    const items=[
      {name:'A',material_code:'FL-100',product_name:'平板壳',project_collection:'YW ODM'},
      {name:'B',material_code:'FL-200',product_name:'模具',project_collection:'YW ODM'}
    ];
    const model={...w.buildProjectPickerModel(items,optionsResult,''),assignments:{A:'LatinGo拉丁购',B:''}};
    console.log(JSON.stringify({
      all:w.renderProjectAssignRows(model),
      byCode:w.renderProjectAssignRows({...model,search:'fl-200'}),
      byCurrent:w.renderProjectAssignRows({...model,search:'yw odm'}),
      empty:w.renderProjectAssignRows({...model,search:'nope'}),
      count:w.renderProjectAssignCount({...model,search:'fl-200'})
    }));
    ''')
    assert result["all"].count('data-mf-assign-row="') == 2
    assert result["byCode"].count('data-mf-assign-row="') == 1
    assert 'data-mf-assign-row="B"' in result["byCode"]
    assert '<option value="" selected>— 未选择 —</option>' in result["byCode"]
    assert result["byCurrent"].count('data-mf-assign-row="') == 2
    assert "没有匹配的物料" in result["empty"]
    assert result["count"].startswith("显示 1 / 共 2 行")


def test_bulk_project_assignment_writes_each_row_value_in_one_call():
    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.detailState={batchName:'B-1',versionName:'V-1',editToken:'T',expectedModified:'M'};
    w.ensureMaterialFeeState().materials={items:[]};
    w.ensureEditSession=async()=>true;w.updateMaterialFeeExpectedModified=()=>{};w.loadMaterialFeeWorkspace=async()=>true;
    w.invalidateProjectRouteOptions=()=>{};w.loadProjectRouteOptions=async()=>({ok:true});
    let calls=0,payload=null;w.call=async(endpoint,args)=>{calls++;payload={endpoint,args};return {ok:true,changed_count:2}};
    const items=[{name:'A',material_code:'FL-1'},{name:'B',material_code:'FL-2'}];
    const applied=await w.applyProjectCollectionSelection(items,'',{
      allowedValues:new Set(['LatinGo拉丁购','YW MOLDES MX模具']),
      perItemValues:{A:'LatinGo拉丁购',B:'YW MOLDES MX模具'}
    });
    console.log(JSON.stringify({calls,ok:applied.ok,endpoint:payload.endpoint,updates:JSON.parse(payload.args.updates)}));
    ''')
    assert result["calls"] == 1
    assert result["ok"] is True
    assert result["endpoint"] == "overseas_costing.api.calculate.batch_update_items"
    assert result["updates"] == [
        {"item_name": "A", "fieldname": "project_collection", "value": "LatinGo拉丁购", "remark": "设置 ERP 项目归属"},
        {"item_name": "B", "fieldname": "project_collection", "value": "YW MOLDES MX模具", "remark": "设置 ERP 项目归属"},
    ]


def test_bulk_project_assignment_refuses_unset_or_outside_values_without_writing():
    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);
    w.ensureMaterialFeeState().materials={items:[]};
    let writes=0;w.call=async()=>{writes++;return {ok:true}};
    const items=[{name:'A',material_code:'FL-1'},{name:'B',material_code:'FL-2'}];
    const allowedValues=new Set(['LatinGo拉丁购']);
    const issues={};
    for(const [label,values] of [['unset',{A:'LatinGo拉丁购',B:''}],['outside',{A:'产品线A',B:'LatinGo拉丁购'}]]){
      try{
        await w.applyProjectCollectionSelection(items,'',{allowedValues,perItemValues:values});
        issues[label]=null;
      }catch(error){
        issues[label]={message:error.message,invalid:Boolean(error.materialReferenceInputInvalid)};
      }
    }
    console.log(JSON.stringify({writes,issues}));
    ''')
    # 半批提交会让人以为全批都写了，所以整批拒掉；列表外的值同样拒绝。
    assert result["writes"] == 0
    assert result["issues"]["unset"]["invalid"] is True
    assert "还有 1 行未指定项目归属（如 FL-2）" in result["issues"]["unset"]["message"]
    assert result["issues"]["outside"]["invalid"] is True
    assert "请从有效列表中选择，不能录入列表外的值。" == result["issues"]["outside"]["message"]


def test_bulk_project_dialog_uses_per_row_selects_and_keeps_the_shared_picker():
    source = (PARTS / "78-material-fee-workspace.js").read_text(encoding="utf-8")
    picker = source.split("async openProjectCollectionPicker", 1)[1].split("async loadSupplierResolution", 1)[0]
    assert "perItemValues:bulk ? model.assignments : null" in picker
    assert "data-mf-assign-rows" in picker and "data-mf-assign-row]" in picker
    assert "data-mf-assign-all" in picker
    # 单行入口仍走“一个共用值 + 预览”的既有交互，没有被批量路径顶掉。
    assert "data-mf-project-options" in picker
    assert "ocw-project-route" in picker
    assert "renderReferenceChangePreview(items, \"project_collection\", model.selectedValue" in picker


def test_project_assignment_rows_lead_with_the_material_name_and_keep_the_code():
    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);
    w.escape=v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
    const optionsResult={ok:true,route_revision:'R-1',conflicts:[],options:[
      {project_collection:'LatinGo拉丁购',subsidiary_code:'C1',is_approval_candidate:true}
    ]};
    const items=[
      {name:'A',material_code:'FL-1',product_name:'平板保护壳',project_collection:''},
      {name:'B',material_code:'FL-2',product_name:'',project_collection:''}
    ];
    const model=w.buildProjectPickerModel(items,optionsResult,'');
    console.log(JSON.stringify({
      rows:w.renderProjectAssignRows(model),
      preview:w.renderReferenceChangePreview(items,'project_collection','LatinGo拉丁购','项目归属变更预览'),
      table:w.renderProjectAssignTable(model)
    }));
    ''')
    # 一屏要扫几十行，主显物料名、编码退成副标题；没有名字的行回落成编码，不留空单元格。
    assert '<span>平板保护壳</span><small>FL-1</small>' in result["rows"]
    assert '<span>FL-2</span>' in result["rows"]
    assert '<small>FL-2</small>' not in result["rows"]
    assert "物料名称</th>" in result["table"]
    assert "搜索物料（名称 / 编码 / 当前归属）" in result["table"]
    assert '<span>平板保护壳</span><small>FL-1</small>' in result["preview"]


def test_material_reference_dialogs_reuse_the_shared_wide_modal():
    source = (PARTS / "78-material-fee-workspace.js").read_text(encoding="utf-8")
    css = (PARTS / "48-material-fee-workspace.css").read_text(encoding="utf-8")

    # 窄弹窗会把逐行下拉表和候选列表挤成一条，所以这些弹窗统一挂到共用的宽弹窗类上，
    # 而不是各写一套宽度。
    for anchor in (
        'title:bulk ? ocTranslate("批量设置项目归属（{0} 行）", [items.length])',
        'title:source === "bulk" ? ocTranslate("批量设置供应商（{0} 行）", [items.length])',
        'title: ocTranslate("已排除物料")',
    ):
        added = source.split(anchor, 1)[1].split("addClass(", 1)[1].split(")", 1)[0]
        assert added.strip() == '"ocw-mf-dialog"', anchor

    # Frappe 的 .modal-dialog 自带固定宽度，只写 max-width 撑不开，必须显式给 width。
    assert ".ocw-mf-dialog .modal-dialog { width: min(1100px" in css
    assert ".ocw-mf-assign-table { width: 100%; min-width: 640px;" in css


def test_supplier_picker_offers_existing_and_create_without_reason_and_targets_exact_rows():
    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.escape=value=>String(value??'');w.detailState={batchName:'B-1',versionName:'V',editToken:'T',expectedModified:'M'};
    const state=w.ensureMaterialFeeState();state.materials={items:[]};
    let queries=[];w.call=async(endpoint,args)=>{queries.push({endpoint,args});return {raw_value:args.raw_value,status:'SUGGESTED',canonical_supplier:'',candidates:[
      {name:'SUP-001',supplier_name:'Alpha Trading',score:0.94,high_confidence:true},
      {name:'SUP-002',supplier_name:'Alpha Tools',score:0.78,high_confidence:false}
    ]}};
    const model=await w.loadSupplierResolution('Alpha Tradng');const html=w.renderSupplierPickerOptions(model,{kind:'create',value:'Alpha Tradng'});
    const rows=[{name:'A',stable_line_key:'L-A',material_code:'A',supplier:''},{name:'C',stable_line_key:'L-C',material_code:'C',supplier:'SUP-OLD'}];
    w.ensureEditSession=async()=>true;w.updateMaterialFeeExpectedModified=()=>{};w.loadMaterialFeeWorkspace=async()=>true;
    let saved=null;w.call=async(endpoint,args)=>{saved={endpoint,args};return {ok:true,changed_count:2}};
    await w.applySupplierSelection(rows,'SUP-001',{allowedValues:new Set(model.options.map(row=>row.name))});
    console.log(JSON.stringify({queries,model,html,updates:JSON.parse(saved.args.updates)}));
    ''')
    assert [row["name"] for row in result["model"]["options"]] == ["SUP-001", "SUP-002"]
    assert '高置信候选' in result["html"] and 'Alpha Trading' in result["html"]
    assert '新建供应商：Alpha Tradng' in result["html"]
    assert '这是不同供应商' in result["html"]
    assert [row["item_name"] for row in result["updates"]] == ["A", "C"]
    assert all(row["fieldname"] == "supplier" for row in result["updates"])
    assert all(row["remark"] == "设置 ERP 供应商" for row in result["updates"])


def test_supplier_picker_always_offers_the_no_supplier_marker_option():
    """「没有供应商就选 /」：无供应商是常驻一等选项，写入走同一条批量更新链。"""

    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.escape=value=>String(value??'');w.detailState={batchName:'B-1',versionName:'V',editToken:'T',expectedModified:'M'};
    const state=w.ensureMaterialFeeState();state.materials={items:[]};
    w.call=async()=>({raw_value:'',status:'EMPTY',canonical_supplier:'',candidates:[]});
    const model=await w.loadSupplierResolution('');
    const html=w.renderSupplierPickerOptions(model,{kind:'',value:''});
    const rows=[{name:'A',stable_line_key:'L-A',material_code:'A',supplier:''}];
    w.ensureEditSession=async()=>true;w.updateMaterialFeeExpectedModified=()=>{};w.loadMaterialFeeWorkspace=async()=>true;
    let saved=null;w.call=async(endpoint,args)=>{saved={endpoint,args};return {ok:true,changed_count:1}};
    await w.applySupplierSelection(rows,'/',{allowedValues:new Set(['/']),auditRemark:'标记为无供应商（/）'});
    console.log(JSON.stringify({html,updates:JSON.parse(saved.args.updates)}));
    ''')
    assert 'data-mf-supplier-kind="none"' in result["html"]
    assert 'data-mf-supplier-value="/"' in result["html"]
    assert '标记为无供应商（/）' in result["html"]
    assert '推送 ERP 时由站点默认供应商兜底' in result["html"]
    assert result["updates"] == [{
        "item_name": "A",
        "fieldname": "supplier",
        "value": "/",
        "remark": "标记为无供应商（/）",
    }]


def test_material_workspace_announces_the_readonly_reason_inline():
    """只读原因原来只在禁用按钮的 title 里；现在要在物料区上方亮出来。

    回归背景：`0-foundation` 的 `.ocw-outline-btn` 没有 `:disabled` 样式，禁用与可用长得一样，
    工具栏里只有批量按钮带紫色禁用态 —— 物料区整体只读时，用户只看到这两个按钮灰着，
    就误判成「没全选所以不能点」。所以原因还要贴到批量按钮旁边，且每个写入按钮都要真带 disabled。
    """

    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.detailState={readOnly:false};w.escape=v=>String(v??'');
const state=w.ensureMaterialFeeState();
state.materials={packing_group_editable:true,reference_editable:true,packing_groups:[],items:[{name:'A',row_no:1}]};
state.packingGroupSelections=new Set(['A']);
const editableHtml=w.materialReadonlyNoteHtml();
const editableToolbar=w.renderMaterialSelectionToolbar();
// 历史 / 归档版本：物料区整体只读。
state.materials={packing_group_editable:false,reference_editable:false,packing_groups:[],items:[{name:'A',row_no:1}]};
const lockedHtml=w.materialReadonlyNoteHtml();
const lockedToolbar=w.renderMaterialSelectionToolbar();
// 已确认 / 已锁定的当前版本：成本字段冻结，但供应商与项目归属仍要能维护。
state.materials={packing_group_editable:false,reference_editable:true,packing_groups:[],items:[{name:'A',row_no:1}]};
const confirmedHtml=w.materialReadonlyNoteHtml();
const confirmedToolbar=w.renderMaterialSelectionToolbar();
w.detailState={readOnly:true};
state.materials={packing_group_editable:true,reference_editable:true,packing_groups:[],items:[{name:'A',row_no:1}]};
const leaseHtml=w.materialReadonlyNoteHtml();
console.log(JSON.stringify({editableHtml,lockedHtml,confirmedHtml,leaseHtml,
  editableToolbar,lockedToolbar,confirmedToolbar}));
""")
    assert result["editableHtml"] == ""
    assert '物料区只读' in result["lockedHtml"]
    assert '历史或归档版本仅供追溯' in result["lockedHtml"]
    assert '勾选与全选本页仅用于查看范围' in result["lockedHtml"]
    # 文档页只读（编辑租约/权限这一层）也要有说明，不能因为参考字段放宽就不提示。
    assert '物料区只读' in result["leaseHtml"]
    # 已确认 / 锁定的当前版本：只读提示必须说清「什么不能改、什么还能补」。
    assert '基础资料可维护' in result["confirmedHtml"]
    assert '供应商与项目归属仍可按行维护' in result["confirmedHtml"]

    def button(action, html):
        head, tail = html.split(f'data-action="{action}"', 1)
        return head.rsplit('<button', 1)[1] + tail.split('>', 1)[0]

    # 只读时每个写入按钮都真带 disabled，并把原因贴在批量按钮旁边（可写时不留这个提示）。
    assert 'ocw-mf-selection-lock' not in result["editableToolbar"]
    assert 'ocw-mf-selection-lock' in result["lockedToolbar"]
    assert '物料区只读 · 批量设置不可用' in result["lockedToolbar"]
    for action in ('mf-add-material', 'mf-set-project', 'mf-set-supplier'):
        assert 'disabled' in button(action, result["lockedToolbar"])
        assert 'disabled' not in button(action, result["editableToolbar"])
    # 已确认 / 已锁定：成本按钮仍禁用，两颗批量设置按钮放行，胶囊改成说真话的那一句。
    assert 'disabled' in button('mf-add-material', result["confirmedToolbar"])
    assert 'disabled' not in button('mf-set-project', result["confirmedToolbar"])
    assert 'disabled' not in button('mf-set-supplier', result["confirmedToolbar"])
    assert '成本字段只读 · 可补供应商/项目归属' in result["confirmedToolbar"]
    # 「全选本页」只用来圈查看范围，只读批次照旧可点，不能被这层提示连坐。
    assert 'disabled' not in button('mf-select-page', result["lockedToolbar"])


def test_row_checkboxes_and_page_selection_cover_identity_rows_even_when_readonly():
    """「全选本页一直可点」配套契约：勾选身份放宽为 stable_line_key||name（与推送路由
    的行身份约定同源），只读批次也能勾选查看；AI 替换草稿行（无身份）仍不可勾选，
    批量写入门槛不在此处放宽。"""

    result = _fee_workspace_result(r"""
const w=Object.create(Harness.prototype);w.escape=v=>String(v??'');w.detailState={readOnly:true};
const state=w.ensureMaterialFeeState();
state.materials={packing_group_editable:false,items:[
  {name:'ROW-A',row_no:1},
  {name:'ROW-B',stable_line_key:'L-B',row_no:2},
  {__aiReplacement:true}],packing_groups:[]};
state.packingGroupSelections=new Set();
w.toggleMaterialPageSelection(true);
const page=w.materialPageSelectionState();
const cellA=w.renderMaterialFeeGridCell(state.materials.items[0],{field:'__group_select'},new Set(),0);
const cellB=w.renderMaterialFeeGridCell(state.materials.items[1],{field:'__group_select'},new Set(),1);
const cellAI=w.renderMaterialFeeGridCell(state.materials.items[2],{field:'__group_select'},new Set(),2);
console.log(JSON.stringify({page,cellA,cellB,cellAI}));
""")
    assert result["page"]["total"] == 2
    assert result["page"]["selected"] == 2
    assert result["page"]["checked"] is True
    # 只有 name 的行也能勾选（回退身份），勾选后复选框渲染为选中且可点。
    assert 'data-mf-packing-group-select="ROW-A"' in result["cellA"]
    assert "checked" in result["cellA"]
    assert "disabled" not in result["cellA"]
    assert 'data-mf-packing-group-select="L-B"' in result["cellB"]
    assert "disabled" not in result["cellB"]
    # AI 替换草稿行没有身份，保持禁用。
    assert "disabled" in result["cellAI"]


def test_supplier_exact_match_becomes_a_selectable_canonical_option():
    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.detailState={batchName:'B-1'};w.ensureMaterialFeeState();
    w.call=async()=>({raw_value:'Alpha Trading',status:'EXACT',canonical_supplier:'SUP-001',candidates:[]});
    const model=await w.loadSupplierResolution('Alpha Trading');
    console.log(JSON.stringify({model}));
    ''')
    assert result["model"]["options"] == [{
        "name": "SUP-001",
        "supplier_name": "SUP-001",
        "score": 1,
        "exact": True,
    }]
    assert result["model"]["canCreate"] is False


def test_supplier_creation_uses_post_then_existing_batch_update_and_invalidates_cache():
    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.detailState={batchName:'B-1',versionName:'V',editToken:'T',expectedModified:'M'};
    const state=w.ensureMaterialFeeState();state.materials={items:[]};state.supplierOptionsCache.set('old',{ok:true});
    w.ensureEditSession=async()=>true;w.updateMaterialFeeExpectedModified=()=>{};w.loadMaterialFeeWorkspace=async()=>true;
    const calls=[];w.call=async(endpoint,args,_freeze,request)=>{calls.push({endpoint,args,request});if(endpoint.endsWith('create_supplier_from_workbench'))return {ok:true,created:true,supplier:'SUP-NEW',supplier_name:'New Supplier'};return {ok:true,changed_count:1};};
    const rows=[{name:'A',stable_line_key:'L-A',supplier:''}];
    const response=await w.createSupplierAndApply(rows,' New Supplier ',false);
    console.log(JSON.stringify({response,calls,cacheSize:state.supplierOptionsCache.size,updates:JSON.parse(calls[1].args.updates)}));
    ''')
    assert result["calls"][0]["endpoint"].endswith("create_supplier_from_workbench")
    assert result["calls"][0]["request"] == {"type": "POST"}
    assert result["calls"][0]["args"]["supplier_name"] == "New Supplier"
    assert result["calls"][1]["endpoint"].endswith("batch_update_items")
    assert result["updates"] == [{
        "item_name": "A",
        "fieldname": "supplier",
        "value": "SUP-NEW",
        "remark": "新建 ERP 供应商并设置物料",
    }]
    assert result["cacheSize"] == 0


def test_ai_final_material_preview_has_supplier_before_project_and_escapes_blank():
    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.escape=value=>String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
    w.materialAIReviewHasStageSnapshots=()=>true;
    w.ensureMaterialAIRowSelection=()=>({previewKey:'KEY',preview:{rows:[
      {material_code:'A',product_name:'One',supplier:'SUP-OK',project_collection:'LatinGo拉丁购'},
      {material_code:'B',product_name:'Two',supplier:'',project_collection:'<script>bad</script>'}
    ],missing_fields:[]},mode:'fill_missing',rows:new Set(),fields:new Set(),fees:new Set(),packingAssignments:new Map(),loading:false,error:''});
    w.materialAIRowSelectionKey=()=> 'KEY';w.materialAIFeeSelectionPolicy=()=>({fees:[],mainFees:[]});w.renderMaterialAIFeeStages=()=>'';w.materialReplacementRows=()=>[];w.renderMaterialAIReviewSources=()=>'';w.canConfirmMaterialAIRowSelection=()=>false;
    const fill={row_review:{fee_stage_snapshots:[],rows:[]},draft:{},proposals:[],updates:{},manualUpdates:{}};
    const html=w.renderMaterialAIRowReview(fill);
    console.log(JSON.stringify({html}));
    ''')
    html = result["html"]
    assert html.index("<th>供应商</th>") < html.index("<th>项目归属</th>")
    assert "SUP-OK" in html and ">—</td>" in html
    assert "<script>" not in html and "&lt;script>bad&lt;/script>" in html


def test_project_and_supplier_picker_dialogs_have_no_reason_field_or_reason_helper():
    source = (PARTS / "78-material-fee-workspace.js").read_text(encoding="utf-8")
    picker_segment = source.split("async openProjectCollectionPicker", 1)[1].split("async openMaterialPackingGroupDialog", 1)[0]
    assert 'fieldname:"reason"' not in picker_segment
    assert "referenceSelectionRequiresReason" not in source


def test_material_toolbar_exposes_supplier_bulk_action():
    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.escape=value=>String(value??'');w.detailState={batchName:'B'};
    const state=w.ensureMaterialFeeState();state.materials={packing_group_editable:true,packing_groups:[],items:[{name:'A',stable_line_key:'L-A'}]};state.packingGroupSelections=new Set(['L-A']);
    console.log(JSON.stringify({html:w.renderMaterialSelectionToolbar(),actions:w.materialSelectionContext().actions}));
    ''')
    assert 'data-action="mf-set-supplier"' in result["html"]
    assert '批量设置供应商' in result["html"]
    assert result["actions"]["supplier"]["enabled"] is True


def test_reference_picker_layout_is_compact_scrollable_and_responsive():
    css = (PARTS / "48-material-fee-workspace.css").read_text(encoding="utf-8")
    assert ".ocw-mf-reference-picker-dialog" in css
    assert ".ocw-mf-reference-group" in css
    assert "max-height:" in css and "overflow-y: auto" in css
    assert ".ocw-mf-reference-option" in css
    responsive = css.split("@media (max-width: 700px)", 1)[1]
    assert ".ocw-mf-reference-option" in responsive
    assert "grid-template-columns: minmax(0, 1fr)" in responsive


def test_reference_picker_saves_route_failures_to_the_shared_reporter():
    """两个选择器点保存不再静默：错误必须交给统一出口，且不能谎报成功。

    回归背景：`primary_action` 里抛出的错没人接，而 Frappe 的 Dialog 不会等它的
    promise —— 乐观锁过期时表现为“点保存毫无反应”。
    """

    source = (PARTS / "78-material-fee-workspace.js").read_text(encoding="utf-8")
    project_dialog = source.split("async openProjectCollectionPicker", 1)[1].split("async loadSupplierResolution", 1)[0]
    supplier_dialog = source.split("async openSupplierPicker", 1)[1].split("async openMaterialPackingGroupDialog", 1)[0]

    assert "catch (error)" in project_dialog
    assert "await this.reportMaterialReferenceWriteFailure(error)" in project_dialog
    assert "if (!result?.ok) return;" in project_dialog
    assert "catch (error)" in supplier_dialog
    assert "await this.reportMaterialReferenceWriteFailure(error)" in supplier_dialog
    assert "if (!applied?.ok) return;" in supplier_dialog
    # 保存失败后必须先恢复再提示，恢复动作只有一处实现。
    assert "recoverMaterialFeeReadonlyState(this.detailState.batchName)" in source


def test_reference_write_failure_clears_the_cause_and_offers_one_next_step():
    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.detailState={batchName:'B-1'};
    w.normalizeErrorMessage=error=>String(error?.message||'操作失败');
    const recovered=[];const shown=[];
    w.recoverMaterialFeeReadonlyState=async name=>{recovered.push(name);return true;};
    w.showError=error=>shown.push(String(error?.message||''));
    await w.reportMaterialReferenceWriteFailure(new Error('RuntimeError: 批次数据已被更新，请刷新后重新确认本次修改。'));
    w.recoverMaterialFeeReadonlyState=async name=>{recovered.push(name);return false;};
    await w.reportMaterialReferenceWriteFailure(new Error('编辑会话已过期，请重新进入编辑。'));
    console.log(JSON.stringify({recovered,shown}));
    ''')
    assert result["recovered"] == ["B-1", "B-1"]
    assert result["shown"][0] == "批次数据已被更新，请刷新后重新确认本次修改。；已同步最新数据，请再点一次保存。"
    assert result["shown"][1] == "编辑会话已过期，请重新进入编辑。；请刷新页面后重试。"


def test_reference_write_failure_keeps_invalid_input_a_hint_without_resync_or_modal():
    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.detailState={batchName:'B-1'};
    w.normalizeErrorMessage=error=>String(error?.message||'操作失败');
    const recovered=[];const shown=[];
    w.recoverMaterialFeeReadonlyState=async name=>{recovered.push(name);return true;};
    w.showError=error=>shown.push(String(error?.message||''));
    const error=new Error('请从有效列表中选择，不能录入列表外的值。');
    error.materialReferenceInputInvalid=true;
    await w.reportMaterialReferenceWriteFailure(error);
    const blocking=new Error('页面版本已失效，请刷新后继续使用。');blocking.workbenchReleaseBlocked=true;
    await w.reportMaterialReferenceWriteFailure(blocking);
    console.log(JSON.stringify({recovered,shown,alerts:global.alerts}));
    ''')
    assert result["recovered"] == []
    assert result["shown"] == []
    assert result["alerts"] == [{"message": "请从有效列表中选择，不能录入列表外的值。", "indicator": "orange"}]


def test_reference_write_failure_names_the_row_and_cause_instead_of_the_generic_rollback():
    """批量接口只回一句「已整体回滚」，真正的原因在 results 里。

    回归背景：用户点保存只看到「批量字段更新存在错误，已整体回滚。；已同步最新数据，
    请再点一次保存。」—— 既不知道哪一行、也不知道为什么，反复点还是同一句。
    逐行原因必须还原出来，并且业务拒绝不再冒充乐观锁过期（不重同步、不劝重试）。
    """

    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.detailState={batchName:'B-1'};
    w.normalizeErrorMessage=error=>String(error?.message||'操作失败');
    const recovered=[];const shown=[];
    w.recoverMaterialFeeReadonlyState=async name=>{recovered.push(name);return true;};
    w.showError=error=>shown.push(String(error?.message||''));
    const items=[{name:'I1',material_code:'GJ000145',product_name:'防滑手套'}];
    const message=w.materialBatchWriteFailureMessage({ok:false,
      message:'批量字段更新存在错误，已整体回滚。',
      results:[{ok:false,item_name:'I1',message:'只能编辑当前未确认的活动版本，历史版本请创建调整草稿。'}]},items,'supplier');
    const fallback=w.materialBatchWriteFailureMessage({ok:false,message:'批量字段更新存在错误，已整体回滚。',results:[]},items,'project_collection');
    await w.reportMaterialReferenceWriteFailure(Object.assign(new Error(message),{materialRowWriteRejected:true}));
    console.log(JSON.stringify({message,fallback,recovered,shown}));
    ''')
    assert result["message"] == ("供应商未保存（已整体回滚）：防滑手套（GJ000145）："
                                 "只能编辑当前未确认的活动版本，历史版本请创建调整草稿。")
    # 拿不到逐行结果时退回服务端原文，不编造原因。
    assert result["fallback"] == "批量字段更新存在错误，已整体回滚。"
    assert result["recovered"] == []
    assert result["shown"] == [result["message"]]


def test_actual_fee_amount_replaces_the_red_evidence_todo_badge():
    """金额已选「实际」的行不再用红色「待补」显示成报错。

    凭证要求、待办与计数一律不变（仍走 fee_status_service），这里只改展示口径。
    """

    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);
    console.log(JSON.stringify({
      actual:w.materialFeeEvidenceLabel('MISSING','ACTUAL'),
      actualLowercase:w.materialFeeEvidenceLabel('missing','actual'),
      estimated:w.materialFeeEvidenceLabel('MISSING','ESTIMATED'),
      missingWithoutStatus:w.materialFeeEvidenceLabel('MISSING'),
      invalid:w.materialFeeEvidenceLabel('INVALID','ACTUAL'),
      pending:w.materialFeeEvidenceLabel('PENDING','ACTUAL'),
      notRequired:w.materialFeeEvidenceLabel('NOT_REQUIRED','ACTUAL')}));
    ''')
    assert result["actual"] == {"label": "凭证可稍后补充", "tone": "neutral"}
    assert result["actualLowercase"] == result["actual"]
    # 只有「实际 + 缺凭证」这一格改口径，其余状态一字不动。
    assert result["estimated"] == {"label": "待补", "tone": "danger"}
    assert result["missingWithoutStatus"] == {"label": "待补", "tone": "danger"}
    assert result["invalid"] == {"label": "需重补", "tone": "danger"}
    assert result["pending"] == {"label": "已关联", "tone": "info"}
    assert result["notRequired"] == {"label": "无需凭证", "tone": "neutral"}


def test_batch_write_revision_is_advanced_through_one_helper_only():
    """两条会刷新写令牌的路径都必须走 acceptBatchWriteRevision（不回退规则只写一处）。"""

    source = (PARTS / "78-material-fee-workspace.js").read_text(encoding="utf-8")
    snapshot_block = source.split("applyMaterialFeeHeaderSnapshot(detail, batchName) {", 1)[1].split("async saveMaterialFeeCell", 1)[0]
    updater_block = source.split("updateMaterialFeeExpectedModified(result) {", 1)[1].split("applyMaterialFeeHeaderSnapshot", 1)[0]

    assert "this.acceptBatchWriteRevision(header.modified)" in snapshot_block
    assert "this.acceptBatchWriteRevision(modified)" in updater_block
    assert "this.detailState.expectedModified = header.modified" not in source
    assert "this.detailState.expectedModified = modified;" not in source


def test_switching_batch_swaps_the_write_revision_instead_of_reusing_it():
    """换批次必须换写令牌：不同批次的 modified 之间没有可比性。

    回归背景：openBatchDetail 只设 detailState.batchName 与 header，从不重置写令牌。
    令牌若按「只前进」跨批次沿用，先看新批次再看旧批次后，旧批次的每次写入都会被
    edit_session 的乐观锁判成「批次数据已被更新」，页面一个字段都改不动。
    """

    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);
    w.detailState={header:{}};
    w.adoptBatchWriteRevision('B-1','2026-09-24 14:13:56.627458');
    const firstBatch=w.detailState.expectedModified;
    w.adoptBatchWriteRevision('B-2','2026-09-10 09:00:00.000001');
    const secondBatch=w.detailState.expectedModified;
    w.adoptBatchWriteRevision('B-2','2026-09-10 08:00:00.000000');
    const olderRefresh=w.detailState.expectedModified;
    w.adoptBatchWriteRevision('B-2','2026-09-25 09:00:00.000001');
    const newerRefresh=w.detailState.expectedModified;
    w.adoptBatchWriteRevision('B-1','2026-09-24 14:13:56.627458');
    const backToFirst=w.detailState.expectedModified;
    const reused=w.adoptBatchWriteRevision('B-1','');
    console.log(JSON.stringify({firstBatch,secondBatch,olderRefresh,newerRefresh,backToFirst,reused}));
    ''')
    assert result["firstBatch"] == "2026-09-24 14:13:56.627458"
    assert result["secondBatch"] == "2026-09-10 09:00:00.000001"
    # 同一批次内，详情刷新拿到的旧值不能把令牌拉回去。
    assert result["olderRefresh"] == "2026-09-10 09:00:00.000001"
    assert result["newerRefresh"] == "2026-09-25 09:00:00.000001"
    # 切回旧批次时以该批次的现值为准；空值不覆盖已有令牌。
    assert result["backToFirst"] == "2026-09-24 14:13:56.627458"
    assert result["reused"] is False


def test_write_token_has_no_bypass_outside_its_two_helpers():
    """写令牌只允许在 acceptBatchWriteRevision / adoptBatchWriteRevision 内部赋值。

    回归背景：12 处页面各自 ``detailState.expectedModified = result.batch_modified``，
    其中详情页直接拿（可能来自缓存快照的）header.modified 覆盖，线上实测被拉回 4 天，
    之后每次写入都被乐观锁 417 拒绝。收口后再加写入点必须改这两个 helper。
    """

    offenders = {}
    for path in sorted(PARTS.glob("*.js")):
        source = path.read_text(encoding="utf-8")
        hits = re.findall(r"detailState\??\.expectedModified\s*=\s*(?!=)", source)
        if hits:
            offenders[path.name] = len(hits)

    assert offenders == {"78-material-fee-workspace.js": 2}
    gate = (PARTS / "78-material-fee-workspace.js").read_text(encoding="utf-8")
    assert "adoptBatchWriteRevision(batchName, modified) {" in gate
    assert "this.detailState.writeRevisionBatch" in gate


def test_older_workspace_snapshot_cannot_rewind_the_write_revision():
    """写令牌只允许前进：缓存快照里的旧 detail.modified 不能把 revision 拉回去。

    回归背景：资料页快照允许「先用缓存的、回头再校验」，它带的 detail 可能落后于数据库
    （线上实测快照里还是 4 天前的 modified）。这份旧值一旦覆盖写令牌，edit_session 的
    乐观锁就会把本页之后每次写入都判成「批次数据已被更新」—— 页面再也保存不了。
    """

    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.batches=[];
    w.detailState={batchName:'B-1',versionName:'V-1',expectedModified:'2026-09-24 14:13:56.627458'};
    const older={ok:true,batch_name:'B-1',header:{modified:'2026-09-20 17:53:04.118289'}};
    w.applyMaterialFeeHeaderSnapshot(older,'B-1');
    const afterOlder=w.detailState.expectedModified;
    w.updateMaterialFeeExpectedModified({batch_modified:'2026-09-20 17:53:04.118289'});
    const afterOlderWrite=w.detailState.expectedModified;
    w.updateMaterialFeeExpectedModified({batch_modified:'2026-09-24 15:01:00.000001'});
    const headerAfterForward=w.detailState.header.modified;
    const newer={ok:true,batch_name:'B-1',header:{modified:'2026-09-24 15:02:00.000001'}};
    w.applyMaterialFeeHeaderSnapshot(newer,'B-1');
    const afterNewer=w.detailState.expectedModified;
    console.log(JSON.stringify({afterOlder,afterOlderWrite,headerAfterForward,afterNewer,version:w.detailState.versionName}));
    ''')
    assert result["afterOlder"] == "2026-09-24 14:13:56.627458"
    assert result["afterOlderWrite"] == "2026-09-24 14:13:56.627458"
    assert result["headerAfterForward"] == "2026-09-24 15:01:00.000001"
    assert result["afterNewer"] == "2026-09-24 15:02:00.000001"
    assert result["version"] == "V-1"



def test_reference_cells_show_pending_state_until_a_value_is_set():
    """项目归属／供应商空格子里的文案是「待设置」（待办），不是「未设置」（状态描述）。

    用户口径：这两个格子等的是业务/采购去指定 ERP 路由与供应商，属于待办；
    写着「未设置」和旁边那个可点的选择入口对不上。有值时仍显示真实值 + 修正，绝不能换成占位文案。
    """

    result = _fee_workspace_result(r'''
    const w=Object.create(Harness.prototype);w.escape=v=>String(v??'');w.formatValue=v=>String(v??'');w.detailState={readOnly:false};
    const state=w.ensureMaterialFeeState();state.materials={packing_group_editable:true,items:[]};
    const columns=w.materialFeeGridColumns();
    const cell=(item,field)=>w.renderMaterialFeeGridCell(item,columns.find(c=>c.field===field),new Set(),0);
    console.log(JSON.stringify({
      blankProject:cell({name:'I1',project_collection:''},'project_collection'),
      blankSupplier:cell({name:'I1',supplier:''},'supplier'),
      filledProject:cell({name:'I2',project_collection:'LatinGo拉丁购'},'project_collection')
    }));
    ''')
    assert '<span>待设置</span>' in result["blankProject"]
    assert '选择项目归属' in result["blankProject"]
    assert '未设置' not in result["blankProject"]
    assert '<span>待设置</span>' in result["blankSupplier"]
    assert '选择供应商' in result["blankSupplier"]
    assert '<span>LatinGo拉丁购</span>' in result["filledProject"]
    assert '待设置' not in result["filledProject"]
    assert '修正' in result["filledProject"]
