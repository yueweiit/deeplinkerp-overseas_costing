  packingColumnName(column) {
    let result = "";
    for (let n = Number(column); n > 0; n = Math.floor((n - 1) / 26)) result = String.fromCharCode(65 + (n - 1) % 26) + result;
    return result;
  }

  packingRangeAddress(range) {
    return `${this.packingColumnName(range.start_column)}${range.start_row}:${this.packingColumnName(range.end_column)}${range.end_row}`;
  }

  parsePackingRange(value) {
    const m = String(value || "").trim().toUpperCase().match(/^([A-Z]{1,3})([1-9]\d{0,6})(?::([A-Z]{1,3})([1-9]\d{0,6}))?$/);
    if (!m) throw new Error(ocTranslate("请输入有效范围，例如 B9:B18 或 W4:W5。"));
    const col = (letters) => [...letters].reduce((n, c) => n * 26 + c.charCodeAt(0) - 64, 0);
    const range = { start_row: Number(m[2]), end_row: Number(m[4] || m[2]), start_column: col(m[1]), end_column: col(m[3] || m[1]) };
    if (range.end_row < range.start_row || range.end_column < range.start_column) throw new Error(ocTranslate("范围终点必须在起点之后。"));
    return range;
  }

  packingCellText(cell) {
    return String(cell?.display_value ?? cell?.raw_value ?? "");
  }

  packingSourceLink(row = {}) {
    const status = row.match_status || row.state;
    if (["unmatched", "outside"].includes(status)) return {status: "unmatched", label: row.match_label || ocTranslate("未匹配本批采购明细"), reason: row.match_reason || "当前行未匹配到本批采购明细，请核对采购来源和物料编码。"};
    if (status === "choice_required") return {status: "ambiguous", label: row.match_label || ocTranslate("采购明细匹配不唯一"), reason: row.match_reason || "存在多个候选采购明细，请在导入结果中选择。"};
    return null;
  }

  renderPackingApprovalMarkers(row = {}) {
    const match = this.packingSourceLink(row);
    const approval = row.approval_link || row.target?.approval_link;
    return this.renderApprovalLinkMarker(match) + (match?.status === "ambiguous" && approval?.status === "ambiguous" ? "" : this.renderApprovalLinkMarker(approval));
  }

  renderPackingSourceGrid(grid) {
    const e = (v) => this.escape(v ?? "");
    const cells = grid.cells || [];
    const columns = cells.reduce((n, row) => Math.max(n, row.length), 0);
    const mergeMap = new Map(), candidateMap = new Map();
    for (const [regions, map] of [[grid.merge_ranges || [], mergeMap], [grid.candidate_regions || [], candidateMap]]) {
      for (const range of regions) for (let r = range.start_row; r <= range.end_row; r++) for (let c = range.start_column; c <= range.end_column; c++) map.set(`${r}:${c}`, range);
    }
    const states = new Map((grid.row_states || []).map((row) => [Number(row.source_row), row]));
    return `<table class="pg-source-grid"><colgroup><col style="width:48px"><col style="width:190px">${Array.from({length:columns}, (_, i) => `<col data-pg-col="${i+1}" style="width:${i<2?180:125}px">`).join("")}</colgroup><thead><tr><th></th><th>${ocTranslate("来源状态")}</th>${Array.from({length:columns}, (_, i) => `<th>${this.packingColumnName(i+1)}<span class="pg-col-resize" data-pg-resize="${i+1}" role="separator" aria-label="${this.escape(ocTranslate("调整 {0} 列宽", [this.packingColumnName(i+1)]))}"></span></th>`).join("")}</tr></thead><tbody>${cells.map((row, i) => `<tr data-pg-source-row="${i+1}" class="${i+1===Number(grid.header_row||1)?"is-source-header":""} ${this.renderPackingApprovalMarkers(states.get(i+1)) ? "ocw-approval-row" : ""}"><th scope="row">${i+1}</th><td class="pg-approval-status">${this.renderPackingApprovalMarkers(states.get(i+1))}</td>${Array.from({length:columns}, (_, j) => {
      const key = `${i+1}:${j+1}`, merge = mergeMap.get(key), candidate = candidateMap.get(key);
      if (merge && (merge.start_row !== i+1 || merge.start_column !== j+1)) return "";
      return `<td tabindex="0" data-pg-cell="${key}" class="${merge?"is-merged":""} ${candidate?"is-candidate":""}" ${merge?`rowspan="${merge.end_row-merge.start_row+1}" colspan="${merge.end_column-merge.start_column+1}"`:""} title="${e(this.packingColumnName(j+1)+(i+1))}"><span>${e(this.packingCellText(row[j]))}</span></td>`;
    }).join("")}</tr>`).join("")}</tbody></table>`;
  }

  renderPackingResultCell(preview, row, field) {
    const e = (v) => this.escape(v ?? "");
    const change = (row.changes || []).find((item) => item.field === field);
    const sourceConflict = (row.source_conflicts || []).find((item) => item.field === field);
    const pending = ["net_weight_kg","gross_weight_kg","volume_m3"].includes(field) && row.physical_status !== "complete";
    const value = change?.conflict ? change.old : row.incoming?.[field] ?? row.target?.[field];
    const state = change?.conflict || sourceConflict ? "is-conflict" : pending || value === undefined || value === null || value === "" ? "is-missing" : change ? "is-incoming" : "";
    const attr = preview.is_merged_preview ? "data-mf-merged-source-field" : "data-mf-source-field";
    return `<td class="pg-value ${state}">${change?.conflict ? `<details class="pg-conflict"><summary>${e(value ?? "--")} <small>${ocTranslate("有差异")}</small></summary><div><span>${ocTranslate("当前：{0}", [e(change.old)])}</span><span>${ocTranslate("装箱计划：{0}", [e(change.new)])}</span><select aria-label="${this.escape(ocTranslate("{0}差异处理", [e(this.materialImportFieldLabel(field))]))}" data-mf-conflict-row="${e(row.source_row)}" data-fieldname="${field}"><option value="keep_current">${ocTranslate("保留当前值")}</option><option value="use_source">${ocTranslate("采用装箱计划")}</option></select></div></details>` : `<span>${e(value ?? "--")}</span>`}${pending?`<small>${ocTranslate("待核对")}</small>`:""}${sourceConflict?`<select ${attr}="1" data-source-row="${e(row.source_row)}" data-fieldname="${field}" aria-label="${this.escape(ocTranslate("选择{0}", [e(this.materialImportFieldLabel(field))]))}"><option value="">${ocTranslate("选择来源值")}</option>${(sourceConflict.options||[]).map(v=>`<option value="${e(v)}">${e(v)}</option>`).join("")}</select>`:""}${pending&&!change?.conflict?`<select class="pg-pending-choice" data-mf-conflict-row="${e(row.source_row)}" data-fieldname="${field}" aria-label="${this.escape(ocTranslate("{0}已有值处理", [e(this.materialImportFieldLabel(field))]))}"><option value="keep_current">${ocTranslate("有值时保留")}</option><option value="use_source">${ocTranslate("有值时采用计划")}</option></select>`:""}</td>`;
  }

  renderPackingResultGrid(preview) {
    const e = (v) => this.escape(v ?? "");
    const fields = ["actual_shipped_qty","shipped_uom","net_weight_kg","gross_weight_kg","volume_m3","chargeable_weight_kg","project_collection"];
    const labels = {complete:ocTranslate("可导入"),incomplete:ocTranslate("物理量待补"),allocation_required:ocTranslate("共箱待分配"),group_confirmation_required:ocTranslate("合并待核对")};
    const rows = (preview.rows || []).map(row => {
      const sourceRows = row.source_rows || [row.source_row];
      return `<tr class="${this.renderPackingApprovalMarkers(row) ? "ocw-approval-row" : ""}"><td><button type="button" class="pg-link" data-pg-locate="${e(sourceRows[0])}">${e(sourceRows.join("、"))}</button></td><td><strong>${e(row.incoming?.material_code)}</strong></td><td class="pg-name">${e(row.material_name || row.target?.material_name || row.incoming?.product_name || "")}</td><td>${e(row.target?.source_doc_no || row.incoming?.source_doc_no || "")}${this.renderPackingApprovalMarkers(row)}${row.match_status==="choice_required"?`<select data-mf-match-row="${e(row.source_row)}" aria-label="${ocTranslate('选择采购明细')}"><option value="">${ocTranslate("选择采购明细")}</option>${(row.candidates||[]).map(c=>`<option value="${e(c.stable_line_key)}">${ocTranslate("行 {0} · {1}", [e(c.row_no), e(c.source_doc_no || "未写审批号")])}</option>`).join("")}</select>`:""}</td>${fields.map(field=>this.renderPackingResultCell(preview,row,field)).join("")}<td>${e(labels[row.physical_status] || "待核对")}${row.physical_missing_rows?.length?`<small>${ocTranslate("原表第 {0} 行", [e(row.physical_missing_rows.join("、"))])}</small>`:""}</td></tr>`;
    }).join("");
    const allocations = (preview.shared_groups || []).filter(g=>g.allocation_required!==false).map(g=>`<section class="pg-allocation"><strong>${ocTranslate("原表第 {0} 行共箱 · 填写各物料实际值", [e((g.row_numbers||[]).join("、"))])}</strong><table><thead><tr><th>${ocTranslate("物料")}</th><th>${ocTranslate("净重 kg")}</th><th>${ocTranslate("毛重 kg")}</th><th>${ocTranslate("体积 m³")}</th></tr></thead><tbody>${(g.participants||[]).map(p=>`<tr><td>${e(p.material_code || "待匹配")}${p.in_batch?"":ocTranslate("（批次外）")}</td>${["net_weight_kg","gross_weight_kg","volume_m3"].map(f=>`<td><input inputmode="decimal" data-mf-allocation="1" data-group-id="${e(g.group_id)}" data-source-key="${e(p.source_key)}" data-fieldname="${f}" aria-label="${e(p.material_code)} ${e(this.materialImportFieldLabel(f))}" placeholder="${ocTranslate('填写实际值')}"></td>`).join("")}</tr>`).join("")}<tr class="pg-total"><td>${ocTranslate("各项合计须等于")}</td>${["net_weight_kg","gross_weight_kg","volume_m3"].map(f=>`<td>${e(g.metrics?.[f]?.value ?? "--")}</td>`).join("")}</tr></tbody></table></section>`).join("");
    const issues = [...(preview.source_validation?.blocking || []), ...(preview.source_validation?.warnings || [])].map(issue=>`<div class="pg-review-line">${issue.confirmation_required?`<label><input type="checkbox" data-mf-source-validation="${e(issue.confirmation_key||issue.code)}">${ocTranslate("已核对：")}</label>`:""}<span>${e(issue.message || "原表数据待核对")}</span>${issue.ranges?.[0]?`<button class="pg-link" type="button" data-pg-locate="${e(issue.ranges[0].start_row)}">${ocTranslate("定位原表")}</button>`:""}</div>`).join("");
    return `<table class="pg-result-grid"><thead><tr><th>${ocTranslate("原表行")}</th><th>${ocTranslate("物料编码")}</th><th>${ocTranslate("物料名称")}</th><th>${ocTranslate("采购审批号")}</th>${fields.map(f=>`<th>${e(this.materialImportFieldLabel(f))}</th>`).join("")}<th>${ocTranslate("状态")}</th></tr></thead><tbody>${rows}</tbody></table>${allocations}${issues}`;
  }

  renderPackingSpreadsheetPreview(preview) {
    const e = (v) => this.escape(v ?? "");
    return `<div class="pg-preview"><header class="pg-heading"><div><strong>${e(preview.source?.label || "装箱计划表")}</strong><span>${ocTranslate("更新于 {0}", [e(this.formatDateTimeMinute(preview.source?.source_updated_at)||"未提供")])}</span></div><div class="pg-tabs" role="tablist"><button type="button" role="tab" data-pg-tab="source" aria-selected="true">${ocTranslate("原表")}</button><button type="button" role="tab" data-pg-tab="result" aria-selected="false">${ocTranslate("导入结果")} <b>${preview.rows?.length||0}</b></button></div></header><section data-pg-panel="source" class="pg-source-panel"><div class="pg-tools"><label>${ocTranslate("查看")} <select data-pg-filter><option value="all">${ocTranslate("全部原表行")}</option><option value="batch">${ocTranslate("本批次相关行")}</option><option value="outside">${ocTranslate("批次外与未匹配行")}</option></select></label><span>${ocTranslate("黄色边框：待核对 · 蓝色角标：已确认合并")}</span><button type="button" class="pg-secondary" data-pg-copy>${ocTranslate("复制选中区域")}</button></div><div class="pg-formula"><b data-pg-address>${ocTranslate("选择单元格")}</b><span data-pg-full-value>${ocTranslate("点击查看完整内容；Shift 点击选择区域")}</span><code data-pg-formula></code></div><div class="pg-grid-scroll" tabindex="0">${this.renderPackingSourceGrid(preview.source_grid||{})}</div><div class="pg-range-editor"><label>${ocTranslate("核对范围")} <input data-pg-range placeholder="${ocTranslate('例如 B9:B18')}" aria-label="${ocTranslate('合并单元格范围')}"></label><button type="button" class="pg-secondary" data-pg-review="confirm">${ocTranslate("确认此范围合并")}</button><button type="button" class="pg-secondary" data-pg-review="separate">${ocTranslate("此范围未合并")}</button><button type="button" class="pg-link" data-pg-undo>${ocTranslate("撤销上次核对")}</button><span data-pg-review-status>${ocTranslate("点击待核对格查看范围与原因")}</span></div></section><section data-pg-panel="result" class="pg-result-panel" hidden>${this.renderCurrentSourceReviewControls(preview.source_context || {}, preview.cargo_review)}<div class="pg-tools"><span>${ocTranslate("蓝色：将写入 · 黄色：差异可选择 · 红色：待补")}</span><span>${ocTranslate("匹配 {0} · 物理量待补 {1}", [preview.summary?.matched||0, preview.summary?.physical_incomplete||0])}</span><button type="button" class="pg-link" data-pg-outside>${ocTranslate("查看批次外原表行")}</button></div><div class="pg-result-scroll">${this.renderPackingResultGrid(preview)}</div></section><footer class="pg-footer"><span data-pg-error role="status"></span><div><button type="button" class="pg-secondary" data-pg-back hidden>${ocTranslate("返回原表")}</button><button type="button" class="pg-secondary" data-action="mf-wiki-import-cancel">${ocTranslate("取消")}</button><button type="button" class="pg-primary" data-pg-next>${ocTranslate("查看导入结果")}</button><button type="button" class="pg-primary" data-action="mf-wiki-import-confirm" hidden>${ocTranslate("确认写入物料表")}</button></div></footer></div>`;
  }

  setupPackingSpreadsheetDialog(dialog, preview) {
    dialog._ocwMaterialPreview = preview;
    dialog._pg = {tab:"source",reviews:preview.merge_reviews?.ranges||[],reviewHistory:[],busy:false,closed:false,selection:null};
    const w = dialog.$wrapper.addClass("pg-dialog");
    w.on("hide.bs.modal.pg",event=>{
      if(dialog._pg.writing&&!dialog._pg.allowClose){event.preventDefault();return;}
      dialog._pg.closed=true;$(document).off(".pgResize");
    });
    w.on("click","[data-pg-tab], [data-pg-next], [data-pg-back]",event=>this.switchPackingPreviewTab(dialog,event.currentTarget.hasAttribute("data-pg-next")?"result":event.currentTarget.getAttribute("data-pg-tab")||"source"));
    w.on("click","[data-pg-cell]",event=>this.selectPackingGridCell(dialog,event));
    w.on("keydown","[data-pg-cell]",event=>{
      if (["Enter"," "].includes(event.key)) {event.preventDefault();this.selectPackingGridCell(dialog,event);}
      if ((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="c") {event.preventDefault();this.copyPackingGridSelection(dialog);}
    });
    w.on("click","[data-pg-copy]",()=>this.copyPackingGridSelection(dialog));
    w.on("click","[data-pg-review], [data-pg-undo]",event=>this.reviewPackingGridRange(dialog,event.currentTarget.getAttribute("data-pg-review")||"undo"));
    w.on("click","[data-pg-locate]",event=>{
      this.switchPackingPreviewTab(dialog,"source");w.find("[data-pg-filter]").val("all").trigger("change");
      const target=w.find(`[data-pg-source-row='${Number(event.currentTarget.getAttribute("data-pg-locate"))}']`)[0];
      target?.scrollIntoView({block:"center",inline:"nearest"});w.find(".pg-located").removeClass("pg-located");$(target).addClass("pg-located");
    });
    w.on("click","[data-pg-outside]",()=>{this.switchPackingPreviewTab(dialog,"source");w.find("[data-pg-filter]").val("outside").trigger("change");});
    w.on("change","[data-pg-filter]",()=>this.filterPackingGridRows(dialog));
    w.on("input change","[data-mf-allocation], [data-mf-match-row], [data-mf-source-field], [data-mf-merged-source-field], [data-mf-source-validation]",()=>this.updatePackingPreviewBlocker(dialog));
    w.on("change","[data-mf-conflict-row]",event=>{
      const node=event.currentTarget;
      const row=(dialog._ocwMaterialPreview.rows||[]).find(row=>String(row.source_row)===node.getAttribute("data-mf-conflict-row"));
      const change=row?.changes?.find(c=>c.field===node.getAttribute("data-fieldname"));
      if(change) $(node).closest("details").find("summary").text(String(node.value==="use_source"?change.new:change.old));
    });
    w.on("pointerdown","[data-pg-resize]",event=>{
      event.preventDefault();const col=w.find(`col[data-pg-col='${Number(event.currentTarget.getAttribute("data-pg-resize"))}']`);
      const start=event.clientX,width=parseFloat(col[0]?.style.width)||125;
      $(document).off(".pgResize").on("pointermove.pgResize",move=>col.css("width",`${Math.max(64,width+move.clientX-start)}px`)).one("pointerup.pgResize",()=>$(document).off(".pgResize"));
    });
    this.switchPackingPreviewTab(dialog,"source");
  }

  switchPackingPreviewTab(dialog, tab) {
    dialog._pg.tab=tab;
    const w=dialog.$wrapper;
    w.find("[data-pg-panel]").each((_,node)=>{node.hidden=node.getAttribute("data-pg-panel")!==tab;});
    w.find("[data-pg-tab]").each((_,node)=>node.setAttribute("aria-selected",String(node.getAttribute("data-pg-tab")===tab)));
    w.find("[data-pg-next]").prop("hidden",tab!=="source");
    w.find("[data-pg-back], [data-action='mf-wiki-import-confirm']").prop("hidden",tab!=="result");
    this.updatePackingPreviewBlocker(dialog);
  }

  selectPackingGridCell(dialog, event) {
    const [r,c]=event.currentTarget.getAttribute("data-pg-cell").split(":").map(Number), grid=dialog._ocwMaterialPreview.source_grid||{};
    const contains=range=>r>=range.start_row&&r<=range.end_row&&c>=range.start_column&&c<=range.end_column;
    const known=(grid.merge_ranges||[]).find(contains), candidate=(grid.candidate_regions||[]).find(contains), prev=dialog._pg.selection;
    const range=event.shiftKey&&prev?{start_row:Math.min(r,prev.start_row),end_row:Math.max(r,prev.start_row),start_column:Math.min(c,prev.start_column),end_column:Math.max(c,prev.start_column)}:{...(known||candidate||{start_row:r,end_row:r,start_column:c,end_column:c})};
    dialog._pg.selection=range;const w=dialog.$wrapper,cell=grid.cells?.[r-1]?.[c-1];
    w.find("[data-pg-range]").val(this.packingRangeAddress(range));w.find("[data-pg-address]").text(`${this.packingColumnName(c)}${r}`);
    w.find("[data-pg-full-value]").text(this.packingCellText(cell));w.find("[data-pg-formula]").text(cell?.formula?ocTranslate("公式：{0}", [cell.formula]):"");
    w.find("[data-pg-review-status]").text(known?ocTranslate("已确认合并；原始值保持只读"):candidate?.reason||ocTranslate("可调整范围后核对合并关系"));
    w.find("[data-pg-cell]").each((_,node)=>{const [row,col]=node.getAttribute("data-pg-cell").split(":").map(Number);node.classList.toggle("is-selected",row>=range.start_row&&row<=range.end_row&&col>=range.start_column&&col<=range.end_column);});
  }

  packingSelectionText(grid, range) {
    const rows=[];
    for(let r=range.start_row;r<=range.end_row;r++){const cells=[];for(let c=range.start_column;c<=range.end_column;c++) cells.push(this.packingCellText(grid.cells?.[r-1]?.[c-1]).replace(/\t/g," "));rows.push(cells.join("\t"));}
    return rows.join("\n");
  }

  async copyPackingGridSelection(dialog) {
    try {if(!dialog._pg.selection)throw new Error(ocTranslate("请先点击单元格，或按住 Shift 选择区域。"));await navigator.clipboard.writeText(this.packingSelectionText(dialog._ocwMaterialPreview.source_grid,dialog._pg.selection));dialog.$wrapper.find("[data-pg-review-status]").text(ocTranslate("已复制选中区域"));}
    catch(error){dialog.$wrapper.find("[data-pg-error]").text(error.message||ocTranslate("复制失败"));}
  }

  async reviewPackingGridRange(dialog, action) {
    if(dialog._pg.busy||dialog._pg.closed)return;
    const preview=dialog._ocwMaterialPreview;
    try {
      const previousReviews = [...dialog._pg.reviews];
      const history = dialog._pg.reviewHistory || [];
      let reviews = [...previousReviews];
      if (action === "undo") {
        if (!history.length) throw new Error(ocTranslate("本次会话还没有可撤销的核对。"));
        reviews = [...history[history.length - 1]];
      } else {
        const range = this.parsePackingRange(dialog.$wrapper.find("[data-pg-range]").val());
        const selected = dialog._pg.selection;
        const same = (a, b) => b && ["start_row", "end_row", "start_column", "end_column"].every(key => a[key] === b[key]);
        // Editing a selected manual range replaces it; unrelated ranges remain
        // and the server still rejects accidental overlap with other reviews.
        reviews = reviews.filter(item => !same(item, range) && !(selected?.evidence_kind === "manual_confirmed" && same(item, selected)));
        reviews.push({ ...range, action });
      }
      dialog._pg.busy=true;this.updatePackingPreviewBlocker(dialog);
      dialog.$wrapper.find("[data-pg-review], [data-pg-undo]").prop("disabled",true);
      const result=await this.call("overseas_costing.api.materials.preview_material_import",{batch_name:preview.batch_name||this.detailState.batchName,source_kind:preview.source.kind,source_id:preview.source.id,sheet_name:preview.sheet?.selected||preview.source.sheet||null,merge_reviews_json:JSON.stringify({source_hash:preview.source.source_hash,ranges:reviews})},true);
      if(dialog._pg.closed)return;
      if(!result?.ok)throw new Error(result?.message||"核对失败，请重新预览。");
      dialog._ocwMaterialPreview=result;dialog._pg.reviews=result.merge_reviews?.ranges||reviews;dialog._pg.selection=null;
      dialog._pg.reviewHistory = action === "undo" ? history.slice(0, -1) : [...history, previousReviews];
      // A reviewed range changes aggregation identities; require fresh field/allocation choices.
      dialog._ocwMaterialImportBaseChoices={};
      dialog.fields_dict.preview.$wrapper.html(this.renderWikiMaterialImportPreview(result));dialog._pg.busy=false;
      this.switchPackingPreviewTab(dialog,"source");dialog.$wrapper.find("[data-pg-review-status]").text(ocTranslate("核对完成，导入结果已重新计算"));
    }catch(error){dialog._pg.busy=false;if(!dialog._pg.closed){this.updatePackingPreviewBlocker(dialog);dialog.$wrapper.find("[data-pg-error]").text(error.message||ocTranslate("核对失败"));}}
    finally {dialog._pg.busy=false;if(!dialog._pg.closed)dialog.$wrapper.find("[data-pg-review], [data-pg-undo]").prop("disabled",false);}
  }

  filterPackingGridRows(dialog) {
    const preview=dialog._ocwMaterialPreview,filter=dialog.$wrapper.find("[data-pg-filter]").val();
    const grid=preview.source_grid||{};
    const selected=new Set(filter==="batch"?(preview.rows||[]).flatMap(row=>row.source_rows||[row.source_row]).map(Number):(grid.row_states||[]).filter(row=>["outside","unmatched"].includes(row.state)).map(row=>Number(row.source_row)));
    let size;
    do {size=selected.size;for(const range of grid.merge_ranges||[]){let intersects=false;for(let r=range.start_row;r<=range.end_row;r++)if(selected.has(r))intersects=true;if(intersects)for(let r=range.start_row;r<=range.end_row;r++)selected.add(r);}}while(size!==selected.size);
    dialog.$wrapper.find("[data-pg-source-row]").each((_,node)=>{node.hidden=filter!=="all"&&!node.classList.contains("is-source-header")&&!selected.has(Number(node.getAttribute("data-pg-source-row")));});
  }

  updatePackingPreviewBlocker(dialog) {
    if(!dialog._pg||dialog._pg.closed)return;
    const issue=this.validateWikiMaterialAllocations(dialog._ocwMaterialPreview,this.collectMaterialImportChoices(dialog,dialog._ocwMaterialPreview));
    dialog.$wrapper.find("[data-action='mf-wiki-import-confirm']").prop("disabled",Boolean(issue)||dialog._pg.busy);
    dialog.$wrapper.find("[data-pg-error]").text(dialog._pg.writing?ocTranslate("正在确认写入，请稍候；此时不能取消。"):dialog._pg.busy?ocTranslate("正在处理，请稍候…"):dialog._pg.tab==="result"?issue||ocTranslate("核对结果后，点击确认写入"):"");
  }

  async confirmPackingSpreadsheet(dialog) {
    if(dialog._pg.busy||dialog._pg.closed)return;
    dialog._pg.busy=true;dialog._pg.writing=true;dialog._pg.allowClose=false;
    dialog.$wrapper.find("[data-action='mf-wiki-import-cancel'], .modal-header button").prop("disabled",true);
    this.updatePackingPreviewBlocker(dialog);
    try {await this.applyMaterialImport(dialog,dialog._ocwMaterialPreview);}
    catch(error){dialog._pg.busy=false;dialog._pg.writing=false;if(!dialog._pg.closed){this.updatePackingPreviewBlocker(dialog);dialog.$wrapper.find("[data-pg-error]").text(error.message||ocTranslate("导入失败，未完成写入"));}return;}
    finally {dialog._pg.busy=false;dialog._pg.writing=false;dialog.$wrapper.find("[data-action='mf-wiki-import-cancel'], .modal-header button").prop("disabled",false);}
    this.updatePackingPreviewBlocker(dialog);
  }
