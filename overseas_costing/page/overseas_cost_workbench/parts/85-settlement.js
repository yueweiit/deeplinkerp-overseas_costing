  async settlementApi(action, args = {}) {
    const type = ["get_batch_settlement", "get_matching_status", "find_expenses"].includes(action) ? "GET" : "POST";
    return this.call(`overseas_costing.api.logistics_settlement.${action}`, args, false, { type });
  }

  settlementAmount(source = {}) {
    if (Array.isArray(source.fees) && source.fees.length) {
      return source.fees.map((fee) => `${fee.label || "费用"} ${fee.amount ?? "未识别"} ${fee.currency || source.currency || "币种待核对"}`).join("；");
    }
    return source.amount !== null && source.amount !== undefined && source.amount !== ""
      ? `${source.amount} ${source.currency || "币种待核对"}` : "金额未识别";
  }

  settlementAdoption(data = {}) {
    if (data.historical) return data.binding ? "历史采用来源 · 仅供追溯" : "历史版本 · 尚未采用物流采购支出";
    if (!data.binding) return "未关联采购支出";
    if (data.expense?.invalid) return "来源已失效 · 暂停采用";
    if (!data.expense?.approved) return "已关联 · 待审批通过";
    if (data.source_context?.fingerprint && data.application?.source_context?.fingerprint !== data.source_context.fingerprint) return "已关联 · 当前资料待采用";
    return ({ applied: "费用已采用 · 以当前试算结果为准", applied_pending: "费用已采用 · 资料／汇率待核对",
      queued: "已关联 · 编辑结束后重试采用", invalid: "来源已失效 · 暂停采用", pending: "已关联 · 费用待采用" })[data.binding.application_status] || "已关联 · 费用待采用";
  }

  settlementSelection(candidate, choices = {}) {
    if (!["pending", "conflict"].includes(candidate.status)) throw new Error(ocTranslate("候选状态已变化，请刷新后复核。"));
    const reason = String(choices.reason || "").trim();
    if (candidate.status === "conflict" && !reason) throw new Error(ocTranslate("冲突关联须逐条填写确认依据。"));
    return { id: candidate.id, revision: candidate.revision, resolve: candidate.status === "conflict", reason,
      ...(choices.coverage?.length ? { coverage: choices.coverage } : {}), negative_confirmed: choices.negative_confirmed === true };
  }

  settlementDialog(title, extraFields = [], fieldsFirst = false, closeLabel = "关闭") {
    const body = { fieldtype: "HTML", fieldname: "settlement_body", options: "正在读取本地资料…" };
    const dialog = new frappe.ui.Dialog({ title, size: "extra-large", fields: [
      ...(fieldsFirst ? [...extraFields, body] : [body, ...extraFields]),
      { fieldtype: "HTML", fieldname: "settlement_actions", options: "" },
    ], primary_action_label: closeLabel, primary_action: () => dialog.hide() });
    const state = { dialog, open: true, request: 0, timer: null, busy: false };
    dialog.onhide = () => this.stopSettlementDialog(state);
    dialog.$wrapper.on("hide.bs.modal.ocwSettlement", (event) => {
      if (event.target === dialog.$wrapper.get(0)) this.stopSettlementDialog(state);
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-settlement-modal");
    return state;
  }

  stopSettlementDialog(state) {
    state.open = false;
    state.request += 1;
    clearTimeout(state.timer);
    state.timer = null;
  }

  settlementBody(state, html) {
    if (state.open) state.dialog.fields_dict.settlement_body.$wrapper.html(html);
  }

  settlementActions(state, html) {
    if (state.open) state.dialog.fields_dict.settlement_actions.$wrapper.html(html);
  }

  settlementNotice(state, message, error = false) {
    if (!state.open) return;
    const $body = state.dialog.fields_dict.settlement_body.$wrapper;
    $body.find("[data-settlement-notice]").remove();
    $body.prepend(`<div class="ocw-settlement-notice ${error ? "is-error" : ""}" data-settlement-notice role="status">${this.escape(message)}</div>`);
  }

  settlementEvents(state, handler) {
    state.dialog.$wrapper.off("click.ocwSettlement").on("click.ocwSettlement", "[data-settlement-action]", async (event) => {
      event.preventDefault();
      if (!state.open || state.busy) return;
      const $button = $(event.currentTarget);
      try { await handler($button.attr("data-settlement-action"), $button); }
      catch (error) { this.settlementNotice(state, error.message || "操作失败，请刷新后重试。", true); }
    });
  }

  async settlementWrite(state, action) {
    if (!state.open || state.busy) return;
    state.busy = true;
    clearTimeout(state.timer);
    state.request += 1;
    state.dialog.$wrapper.find("button,input,select,textarea").prop("disabled", true);
    try { return await action(); }
    finally {
      state.busy = false;
      if (state.open) state.dialog.$wrapper.find("button,input,select,textarea").prop("disabled", false);
    }
  }

  async openSettlementHistory(start = false) {
    if (this.settlementHistoryState?.open) {
      this.settlementHistoryState.dialog.show();
      return;
    }
    const state = this.settlementDialog("历史采购支出匹配");
    Object.assign(state, { after: null, pages: [], status: "pending", selected: new Set(), data: {} });
    this.settlementHistoryState = state;
    this.settlementEvents(state, async (action, $button) => {
      if (action === "start") return this.startSettlementHistory(state);
      if (action === "ai-match") {
        const result = await this.settlementWrite(state, () => this.settlementApi("start_ai_matching", {}));
        await this.loadSettlementHistory(state);
        if (result.message) this.settlementNotice(state, result.message);
        return;
      }
      if (action === "refresh") return this.loadSettlementHistory(state);
      if (action === "pause" || action === "retry") {
        await this.settlementWrite(state, () => this.settlementApi("control_job", { job_id: state.jobId, action }));
        return this.loadSettlementHistory(state);
      }
      if (action === "filter") {
        state.status = $button.attr("data-status"); state.after = null; state.pages = []; state.selected.clear();
        return this.loadSettlementHistory(state);
      }
      if (action === "next" || action === "previous") {
        if (action === "next") { state.pages.push(state.after); state.after = state.data.next_cursor; }
        else state.after = state.pages.pop() ?? null;
        state.selected.clear();
        return this.loadSettlementHistory(state);
      }
      if (action === "review") {
        const candidate = (state.data.candidates || []).find((row) => row.id === $button.attr("data-id"));
        if (candidate) this.openSettlementCandidateReview([candidate], { history: state });
      }
      if (action === "review-selected") {
        const rows = (state.data.candidates || []).filter((row) => row.status === "pending" && state.selected.has(`${row.id}:${row.revision}`));
        if (!rows.length) throw new Error(ocTranslate("请先勾选本页待确认候选。冲突需逐条复核。"));
        this.openSettlementCandidateReview(rows, { history: state });
      }
    });
    state.dialog.fields_dict.settlement_body.$wrapper.on("change.ocwSettlement", "[data-settlement-select]", (event) => {
      const key = $(event.currentTarget).attr("data-settlement-select");
      if (event.currentTarget.checked) state.selected.add(key); else state.selected.delete(key);
    });
    if (start) await this.startSettlementHistory(state);
    else await this.loadSettlementHistory(state);
  }

  async startSettlementHistory(state) {
    try {
      this.settlementBody(state, "正在启动历史匹配… 此操作不使用近期拉取的日期、运输方式或条数限制。");
      const result = await this.settlementWrite(state, () => this.settlementApi("start_history_matching", {}));
      if (!state.open) return;
      if (!result?.ok) throw new Error(result?.message || "历史匹配启动失败");
      state.jobId = result.job?.id;
      state.after = null; state.pages = []; state.selected.clear();
      await this.loadSettlementHistory(state);
    } catch (error) {
      this.settlementBody(state, `<button class="ocw-outline-btn" data-settlement-action="start">${ocTranslate("重试历史匹配")}</button>`);
      this.settlementNotice(state, error.message || "启动失败", true);
    }
  }

  async loadSettlementHistory(state) {
    if (!state.open || state.busy) return;
    clearTimeout(state.timer);
    const request = ++state.request;
    try {
      const result = await this.settlementApi("get_matching_status", { job_id: state.jobId || null, after: state.after || null, status: state.status || "pending" });
      if (!state.open || request !== state.request) return;
      if (!result?.ok) throw new Error(result?.message || "读取历史匹配失败");
      state.data = result;
      state.jobId = result.job?.id || state.jobId;
      this.renderSettlementHistory(state, result);
      if (["queued", "running"].includes(result.job?.status) || ["queued", "running"].includes(result.ai_job?.status)) {
        state.timer = setTimeout(() => this.loadSettlementHistory(state), 3000);
      }
    } catch (error) {
      if (!state.open || request !== state.request) return;
      this.settlementBody(state, `<button class="ocw-outline-btn" data-settlement-action="refresh">${ocTranslate("重新读取")}</button>`);
      this.settlementNotice(state, error.message || "读取失败", true);
    }
  }

  renderSettlementHistory(state, data) {
    if (data.freight_mode) return this.renderFreightHistory(state,data);
    const job = data.job || {};
    const labels = { queued: ocTranslate("排队中"), running: ocTranslate("进行中"), completed: ocTranslate("本轮完成"), partial: ocTranslate("部分失败"), paused: ocTranslate("已暂停"), failed: ocTranslate("失败") };
    const phases = { inventory: "扫描历史来源", load: "整理资料", match: "生成候选", finished: "已结束" };
    const candidates = data.candidates || [];
    const scope = data.health?.scope_counts;
    const ai = data.ai_job || {};
    const aiLabels = {queued:"排队中",running:"分析中",completed:"分析完成",partial:"部分结果待重试",failed:"分析失败",stale:"资料已变化，请重试"};
    this.settlementBody(state, `
      <p class="ocw-settlement-hint">${ocTranslate("覆盖所有名称含「采购支出」的流程，明确属于「物流及运输服务」才纳入；「服务类采购 → 物流及运输服务」等父级别名可兼容，不限定海运、空运、快递等运输方式。排除已拒绝、已撤销、已删除单据，再与历史国际物流匹配。忽略近期拉取的日期及条数，候选需确认后才建立关联。")}</p>
      ${scope ? `<p class="ocw-settlement-hint">${ocTranslate("上次范围核对：国际物流 {0} · 物流类采购支出 {1}（审批通过 {2}）· 不符合分类 {3} · 失效单据 {4}。审批中的单据保留待处理，未通过审批的不作为最终核算依据。", [this.escape(scope.logistics), this.escape(scope.expense), this.escape(scope.approved_expense), this.escape(scope.excluded), this.escape(scope.invalid ?? 0)])}</p>` : ''}
      ${this.renderSettlementHealth(data)}
      <div class="ocw-settlement-toolbar"><strong>${this.escape(labels[job.status] || ocTranslate("尚未启动"))} · ${this.escape(phases[job.phase] || ocTranslate("等待任务"))}</strong>
        <span>${ocTranslate("已处理 {0} / 本轮读取来源 {1}{2} · 失败 {3}", [this.escape(job.processed_count ?? 0), this.escape(job.item_count ?? 0), job.excluded_count ? ocTranslate(" · 旧清单已排除 {0}", [this.escape(job.excluded_count)]) : '', this.escape(job.failed_count ?? data.failures?.length ?? 0)])}</span>
        ${!job.id || ["completed"].includes(job.status) ? `<button class="ocw-primary-btn" data-settlement-action="start">${ocTranslate("一键匹配历史采购支出")}</button>` : ""}
        ${["running", "queued"].includes(job.status) ? `<button class="ocw-outline-btn" data-settlement-action="pause">${ocTranslate("暂停")}</button>` : ""}
        ${["paused", "partial", "failed"].includes(job.status) ? `<button class="ocw-outline-btn" data-settlement-action="retry">${ocTranslate("继续／重试失败项")}</button>` : ""}
        <button class="ocw-outline-btn" data-settlement-action="refresh">${ocTranslate("刷新")}</button>
      </div>
      <div class="ocw-settlement-toolbar">${["completed", "partial"].includes(job.status) ? `<button class="ocw-outline-btn" data-settlement-action="ai-match">${ocTranslate("DeepSeek 补充匹配")}</button>` : ''}
        <span>${ocTranslate("第二轮仅分析未匹配和冲突单据；已有明确结果不重复分析，AI 不会自动建立关联。")}</span></div>
      ${ai.id ? `<p class="ocw-settlement-hint">${ocTranslate("DeepSeek：{0} · 待分析 {1} · 已处理 {2} · 推荐 {3} · 证据不足 {4} · 失败 {5}{6}", [this.escape(aiLabels[ai.status] || ai.status), this.escape(ai.total ?? 0), this.escape(ai.processed ?? 0), this.escape(ai.recommended ?? 0), this.escape(ai.no_match ?? 0), this.escape(ai.failed ?? 0), ai.error ? ` · ${this.escape(ai.error)}` : ''])}</p>` : ''}
      <p class="ocw-settlement-hint">${ocTranslate("关闭窗口后停止页面轮询，后台任务继续。暂停会在当前小批处理后生效。")}</p>
      <div class="ocw-settlement-toolbar">${[["pending", "待确认"], ["conflict", "冲突"], ["confirmed", "已关联"], ["rejected", "已忽略"]].map(([key, label]) => `<button class="${state.status === key ? "ocw-primary-btn" : "ocw-outline-btn"}" data-settlement-action="filter" data-status="${key}">${ocTranslate(label)} ${this.escape(data.counts?.[key] ?? 0)}</button>`).join("")}<span>${ocTranslate("未匹配 {0}", [this.escape(data.counts?.unmatched ?? 0)])}</span></div>
      ${job.error ? `<p class="ocw-settlement-notice is-error">${this.escape(job.error)}</p>` : ""}
      ${data.failures?.length ? `<details><summary>${ocTranslate("失败明细（{0}，最多显示 50 条）", [data.failures.length])}</summary>${data.failures.map((row) => `<p>${this.escape(row.source_id || row.id)}：${this.escape(row.error || ocTranslate("整理失败"))}</p>`).join("")}</details>` : ""}
      <div class="ocw-settlement-table-wrap"><table class="ocw-settlement-table"><thead><tr><th>${ocTranslate("选择")}</th><th>${ocTranslate("国际物流审批")}</th><th>${ocTranslate("采购支出审批")}</th><th>${ocTranslate("费用")}</th><th>${ocTranslate("匹配依据")}</th><th>${ocTranslate("操作")}</th></tr></thead><tbody>${candidates.map((row) => `<tr>
        <td>${row.status === "pending" ? `<input type="checkbox" aria-label="${ocTranslate('选择候选')}" data-settlement-select="${this.escape(`${row.id}:${row.revision}`)}" ${state.selected.has(`${row.id}:${row.revision}`) ? "checked" : ""}>` : "—"}</td>
        <td>${this.escape(row.logistics?.approval_no || row.logistics?.instance)}</td><td>${this.escape(row.expense?.approval_no || row.expense?.instance)}<small>${this.escape(row.expense?.status || ocTranslate("审批状态待核对"))}</small></td>
        <td>${this.escape(this.settlementAmount(row.expense || {}))}</td><td>${this.escape(row.reason || row.method || ocTranslate("待复核"))}</td><td><button class="ocw-outline-btn" data-settlement-action="review" data-id="${this.escape(row.id)}">${row.status === "conflict" ? ocTranslate("逐条处理冲突") : ocTranslate("查看复核")}</button></td></tr>`).join("") || `<tr><td colspan="6">${ocTranslate("本页没有候选。未匹配来源可在对应批次中搜索关联。")}</td></tr>`}</tbody></table></div>
      <div class="ocw-settlement-toolbar">${state.status === "pending" ? `<button class="ocw-primary-btn" data-settlement-action="review-selected">${ocTranslate("复核所选并批量确认")}</button>` : ""}
        ${state.pages?.length ? `<button class="ocw-outline-btn" data-settlement-action="previous">${ocTranslate("上一页")}</button>` : ""}${data.has_more ? `<button class="ocw-outline-btn" data-settlement-action="next">${ocTranslate("下一页")}</button>` : ""}<span>${ocTranslate("每页最多 50 条")}</span></div>`);
  }

  renderSettlementHealth(data = {}) {
    const upstream = data.health?.health || {};
    const error = upstream.last_error || data.health?.last_error || data.health?.error;
    return `<div class="ocw-settlement-hint">${ocTranslate("本地同步：{0} · 上游归档最近成功：{1}", [this.escape(data.sync?.last_success || ocTranslate("未知／未同步")), this.escape(upstream.last_success_at || ocTranslate("未知／未同步"))])}
      <br>${ocTranslate("待归档 {0} · 待重试 {1} · 需人工处理 {2}", [this.escape(upstream.pending_count ?? ocTranslate("未知")), this.escape(upstream.retry_count ?? ocTranslate("未知")), this.escape(upstream.manual_required_count ?? ocTranslate("未知"))])}
      ${data.pending_failure_count !== undefined ? ocTranslate(" · 未解决任务失败 {0}", [this.escape(data.pending_failure_count)]) : ""}${data.control?.enabled === false ? ocTranslate(" · 后台同步已暂停") : ""}
      ${error ? `<p class="ocw-settlement-notice is-error">${ocTranslate("最近异常：{0}", [this.escape(error)])}</p>` : ""}</div>`;
  }

  renderSettlementSource(source = {}, heading = "采购支出") {
    return `<section class="ocw-settlement-source"><h4>${this.escape(heading)}</h4><strong>${this.escape(source.approval_no || source.instance || ocTranslate("尚未关联"))}</strong>
      <p>${this.escape(source.status || ocTranslate("状态待核对"))} · ${source.invalid ? ocTranslate("来源失效") : source.approved ? ocTranslate("审批已通过") : ocTranslate("尚未审批通过")}</p>
      <p>${this.escape(this.settlementAmount(source))}</p>
      <small>${ocTranslate("{0} 更新：{1}", [source.fees?.length ? "采用费用明细，不叠加审批总额。" : source.amount !== null && source.amount !== undefined && source.amount !== "" ? "未识别费用明细，使用总额；范围需核对。" : "尚未识别明确费用或总额，请核对原单。", this.escape(source.source_updated_at || ocTranslate("未提供"))])}</small>
      ${(source.issues || []).map((issue) => `<p class="ocw-settlement-notice">${this.escape(typeof issue === "string" ? issue : JSON.stringify(issue))}</p>`).join("")}</section>`;
  }

  renderSettlementComparison(logistics = {}, expense = {}, labels = {}) {
    const goods = (source) => (source.goods || []).map((row) => `<tr>${[row.material_code, row.product_name, row.spec_model, row.quantity, row.unit].map((value) => `<td>${this.escape(value ?? "—")}</td>`).join("")}</tr>`).join("");
    const signature = (source) => JSON.stringify((source.goods || []).map((row) => [row.material_code, row.product_name, row.spec_model, String(row.quantity ?? ""), row.unit]));
    return `<details class="ocw-settlement-comparison" open><summary>${ocTranslate("货物与来源核对 · {0}", [!logistics.goods?.length || !expense.goods?.length ? "一方或双方货物尚未识别，请核对原单" : signature(logistics) === signature(expense) ? "识别字段一致" : "存在差异，采用前请核对"])}</summary>
      <div class="ocw-settlement-source-grid">${[[logistics, labels.left || "国际物流货物"], [expense, labels.right || "采购支出货物"]].map(([source, title]) => `<div><h4>${this.escape(ocTranslate(title))}</h4><div class="ocw-settlement-table-wrap"><table class="ocw-settlement-table"><thead><tr><th>${ocTranslate("物料")}</th><th>${ocTranslate("品名")}</th><th>${ocTranslate("规格")}</th><th>${ocTranslate("数量")}</th><th>${ocTranslate("单位")}</th></tr></thead><tbody>${goods(source) || `<tr><td colspan="5">${ocTranslate("未识别货物行，需核对原单资料")}</td></tr>`}</tbody></table></div></div>`).join("")}</div>
      <p class="ocw-settlement-hint">${ocTranslate("确认关联后，物料、数量、装箱资料和费用统一采用采购支出。商品单价缺失时才补充原商品采购价格。")}</p></details>`;
  }

  settlementChoiceFields(source = {}, currentCoverage = []) {
    const fields = [{ fieldtype: "Small Text", fieldname: "settlement_reason", label: ocTranslate("确认／更正依据"), description: ocTranslate("冲突、更正和忽略须填写；普通候选可填写核对说明。") }];
    if (source.coverage === "unknown" || currentCoverage.length) {
      fields.push({ fieldtype: "HTML", fieldname: "coverage_note", options: `<p class="ocw-settlement-notice">${ocTranslate("{0}当前费用统一采用采购支出；勾选项说明它的覆盖范围，未确认时保留关联并等待采用。", [currentCoverage.length ? "下方保留了原关联的覆盖范围；请对照当前原单重新核对，必要时取消旧范围。" : "费用覆盖范围不明确：请按原单选择。"])}</p>` });
      for (const [key, label] of this.settlementCoverageOptions()) fields.push({ fieldtype: "Check", fieldname: `coverage_${key}`, label, default: currentCoverage.includes(key) ? 1 : 0 });
    }
    const negative = (source.fees?.length ? source.fees : [{ amount: source.amount }]).some((row) => Number(row.amount) < 0);
    if (negative) fields.push({ fieldtype: "Check", fieldname: "negative_confirmed", label: ocTranslate("已核对负数为退款／冲销，并确认采用") });
    return fields;
  }

  settlementCoverageOptions() {
    return [["freight", "国际运输费"], ["customs", "清关费"], ["tax", "税费"], ["mexico_inland", "墨西哥内陆费"]];
  }

  settlementChoices(state) {
    return { reason: String(state.dialog.get_value("settlement_reason") || "").trim(),
      coverage: this.settlementCoverageOptions().filter(([key]) => state.dialog.get_value(`coverage_${key}`)).map(([key]) => key),
      negative_confirmed: Boolean(state.dialog.get_value("negative_confirmed")) };
  }

  openSettlementCandidateReview(candidates, context = {}) {
    const single = candidates.length === 1;
    const candidate = candidates[0];
    const editable = candidates.every((row) => ["pending", "conflict"].includes(row.status));
    const state = this.settlementDialog(context.correction ? "更正关联 · 核对旧单与新单" : "确认采购支出关联", context.correction
      ? [{ fieldtype: "Small Text", fieldname: "settlement_reason", label: ocTranslate("更正依据"), description: ocTranslate("更正后如费用范围或负数待核对，请在批次中继续处理。") }]
      : single ? this.settlementChoiceFields(candidate.expense) : []);
    this.settlementBody(state, `<p class="ocw-settlement-hint">${ocTranslate("请核对审批、费用和货物。确认后资料来源切为采购支出；正文、评论、附件及 AI 分析均使用该单。资料不全或审批未通过时保持待处理，旧物流资料仅供历史查看。")}</p>
      ${context.correction ? `<div class="ocw-settlement-notice">${ocTranslate("将替换当前关联，保留更正记录和已确认版本。请说明更正原因。")}</div>${this.renderSettlementSource(context.correction.expense, "当前关联（旧单）")}` : ""}
      ${candidates.map((row) => `<article class="ocw-settlement-review"><div class="ocw-settlement-source-grid">${this.renderSettlementSource(row.logistics, "国际物流审批")}${this.renderSettlementSource(row.expense, context.correction ? "拟关联（新单）" : "采购支出审批")}</div>
        <p>${ocTranslate("匹配依据：{0} {1}", [this.escape(row.reason || row.method || ocTranslate("人工复核")), this.escape(JSON.stringify(row.evidence || []))])}</p>
        ${row.status === "conflict" ? `<p class="ocw-settlement-notice is-error">${ocTranslate("存在关联冲突，须逐条填写依据。已被其他有效关联占用的来源需要先处理原关联。")}</p>` : ""}
        ${context.correction ? this.renderSettlementComparison(context.correction.expense, row.expense, { left: "当前关联货物（旧单）", right: "拟关联货物（新单）" }) : this.renderSettlementComparison(row.logistics, row.expense)}
        <button class="ocw-outline-btn" data-settlement-action="source" data-id="${this.escape(row.id)}">${ocTranslate("打开采购支出原单")}</button></article>`).join("")}
      ${!single ? `<p class="ocw-settlement-notice">${ocTranslate("本次仅确认所选候选的关联；范围未知或负数未确认的费用会保留为待采用，请随后进入对应批次逐条处理。")}</p>` : ""}`);
    this.settlementActions(state, `<div class="ocw-settlement-toolbar">${editable ? `<button class="ocw-primary-btn" data-settlement-action="confirm">${context.correction ? ocTranslate("确认更正关联") : ocTranslate("确认 {0} 个关联", [candidates.length])}</button>${single && !context.correction ? `<button class="ocw-outline-btn" data-settlement-action="reject">${ocTranslate("忽略此候选")}</button>` : ""}` : `<span>${ocTranslate("此候选已处理，请刷新列表查看最新状态。")}</span>`}</div>`);
    this.settlementEvents(state, async (action, $button) => {
      if (action === "source") return this.openSettlementSource(candidates.find((row) => row.id === $button.attr("data-id"))?.expense);
      const choices = single ? this.settlementChoices(state) : {};
      if ((action === "reject" || context.correction) && !choices.reason) throw new Error(ocTranslate("请填写核对／更正依据。"));
      let result;
      if (action === "confirm") {
        if (context.correction) {
          result = await this.settlementWrite(state, () => this.settlementApi("correct_match", {
            batch_name: context.batchName, binding_id: context.correction.binding.id, expected_revision: context.correction.binding.revision,
            candidate_id: candidate.id, candidate_revision: candidate.revision, reason: choices.reason,
          }));
        } else {
          const selections = candidates.map((row) => this.settlementSelection(row, choices));
          result = await this.settlementWrite(state, () => this.settlementApi("confirm_matches", {
            selections: JSON.stringify(selections),
            ...(context.batchName ? { batch_name: context.batchName, version_name: context.versionName || null } : {}),
          }));
        }
      } else if (action === "reject") {
        result = await this.settlementWrite(state, () => this.settlementApi("reject_match", { candidate_id: candidate.id, revision: candidate.revision, reason: choices.reason }));
      } else return;
      if (!result) return;
      this.settlementActions(state, "");
      this.settlementBody(state, this.renderSettlementWriteResult(result));
      // Refresh behind the receipt without replacing its partial-failure details.
      if (context.history?.open) await this.loadSettlementHistory(context.history);
      if (context.batchName) await this.refreshSettlementBatch(context.batchName);
      if (context.onComplete) await context.onComplete();
    });
  }

  renderSettlementWriteResult(result = {}) {
    const rows = result.results || [];
    return `<div class="ocw-settlement-notice ${result.ok === false ? "is-error" : ""}" role="status">${rows.length ? ocTranslate("关联成功 {0}，失败 {1}", [this.escape(result.confirmed_count ?? 0), this.escape(result.failed_count ?? 0)]) : result.ok ? ocTranslate("操作已保存") : this.escape(result.message || ocTranslate("操作未完成"))}</div>
      <p>${ocTranslate("关联成功不代表费用已采用或计算完成；请在批次「资料与费用」查看采用状态。")}</p>
      ${rows.map((row) => `<p>${this.escape(row.candidate_id)}：${row.ok ? this.escape(this.settlementAdoption({ binding: { application_status: row.application_status }, expense: { approved: true } })) : this.escape(row.message || ocTranslate("处理失败"))} ${this.escape((row.issues || []).join("；"))}</p>`).join("")}
      ${result.application_status ? `<p>${ocTranslate("采用状态：{0}", [this.escape(this.settlementAdoption({ binding: { application_status: result.application_status }, expense: { approved: true } }))])}</p>` : ""}
      ${(result.issues || []).map((issue) => `<p>${this.escape(issue)}</p>`).join("")}
      ${result.ok === false ? `<p>${ocTranslate("成功项已保留。请关闭此窗口，刷新候选或批次状态后重新复核失败项。")}</p>` : ""}`;
  }

  openSettlementSource(source) {
    const url = String(source?.open_url || "");
    if (!/^dingtalk:\/\/dingtalkclient\//i.test(url) && !/^https:\/\/([a-z0-9-]+\.)*dingtalk\.com\//i.test(url)) {
      throw new Error(ocTranslate("原单链接缺失或无效，请刷新本地来源。"));
    }
    this.openDingtalkLink(url);
  }

  async refreshSettlementBatch(batchName, expectedVersion = null) {
    if (this.detailState?.batchName === batchName) {
      // Adoption can create an adjustment version; fetch current header without the old version pin.
      if (expectedVersion) this.detailState.versionName = expectedVersion;
      await this.openBatchDetail(batchName, this.detailState.tab || "documents", { updateUrl: false, propagateError: true });
    } else if (this.markBatchDirty) this.markBatchDirty(batchName);
  }

  async openBatchSettlementDialog(batchName, viewedVersion = null, initialTab = 'freight') {
    if (this.batchSettlementState?.open) this.stopSettlementDialog(this.batchSettlementState);
    const state = this.settlementDialog("支付来源与装箱资料", [], false, "取消");
    this.batchSettlementState = state;
    state.batchName = batchName;
    state.freightTab = ['freight', 'packing', 'audit'].includes(initialTab) ? initialTab : 'freight';
    state.versionName = viewedVersion || (this.detailState?.batchName === batchName ? this.detailState.versionName : null);
    state.detailContext = this.detailState?.batchName === batchName ? this.settlementDetailContext() : null;
    state.autoMatchChecked = false;
    state.autoMatchStarted = false;
    state.paymentRuleMatchStarted = false;
    const load = () => this.loadBatchSettlementDialog(state);
    const afterWrite = (result = {}) => {
      // Adoption may create a new current adjustment version. Only a completed
      // write may deliberately advance this dialog's version and navigation fence.
      if (state.open && !state.data?.historical && this.detailState?.batchName === batchName) {
        state.versionName = result.version || this.detailState.versionName || null;
        if (result.version) this.detailState.versionName = result.version;
        state.detailContext = this.settlementDetailContext();
      }
      return load();
    };
    this.settlementEvents(state, async (action, $button) => {
      const data = state.data || {};
      if (action === "refresh") return load();
      if (action === "source") return this.openSettlementSource(data.expense);
      if (!this.isBatchSettlementCurrent(state)) throw new Error(ocTranslate("当前批次或版本已变化，请重新打开本票资料。"));
      if (data.freight_mode) return this.handleFreightAction(state, action, $button, afterWrite);
      if (data.historical) throw new Error(ocTranslate("历史版本仅供追溯，请返回当前调整草稿处理。"));
      if (action === "retry-matching") return this.startBatchSettlementMatching(state, true);
      if (action === "search" || action === "correct") return this.openSettlementSearch(batchName, action === "correct" ? data : null, afterWrite,
        { versionName: data.viewed_version || state.versionName, logistics: data.logistics });
      if (action === "candidate") {
        const candidate = (data.candidates || []).find((row) => row.id === $button.attr("data-id"));
        if (candidate) this.openSettlementCandidateReview([candidate], { batchName, versionName: data.viewed_version || state.versionName, onComplete: afterWrite });
      }
      if (action === "apply") return this.openSettlementApplicationReview(batchName, data, afterWrite);
      if (action === "items") return this.openSettlementItemReview(batchName, data, afterWrite);
    });
    await load();
    if (this.isBatchSettlementCurrent(state) && state.data?.freight_mode && state.freightTab === 'packing') {
      await this.handleFreightAction(state, 'packing-sources-refresh', null, afterWrite);
    }
  }

  settlementDetailContext() {
    return JSON.stringify([this.detailState?.batchName, this.detailState?.versionName || null, this.detailState?.tab]);
  }

  isBatchSettlementCurrent(state, request = state.request) {
    return state.open && request === state.request && this.batchSettlementState === state
      && (!state.detailContext || state.detailContext === this.settlementDetailContext());
  }

  async loadBatchSettlementDialog(state) {
    if (!this.isBatchSettlementCurrent(state) || state.busy) return;
    clearTimeout(state.timer);
    state.timer = null;
    const request = ++state.request;
    try {
      const data = await this.settlementApi("get_batch_settlement", { batch_name: state.batchName, version_name: state.versionName || null });
      if (!this.isBatchSettlementCurrent(state, request)) return;
      if (!data?.ok) throw new Error(data?.message || "读取本票匹配失败");
      state.data = data;
      this.renderBatchSettlementDialog(state, data);
      const autoMatch = !state.autoMatchChecked;
      state.autoMatchChecked = true;
      if (!data.freight_mode && autoMatch && !data.binding && !data.historical && ["not_started", "stale"].includes(data.matching?.status)) {
        return this.startBatchSettlementMatching(state);
      }
      if (!data.historical && (["queued", "running"].includes(data.payment_matching?.status)
          || (!data.binding && ["queued", "running"].includes(data.matching?.status)))) {
        state.timer = setTimeout(() => this.loadBatchSettlementDialog(state), 3000);
      }
    } catch (error) {
      if (!this.isBatchSettlementCurrent(state, request)) return;
      this.settlementBody(state, `<button class="ocw-outline-btn" data-settlement-action="refresh">${ocTranslate("重新读取")}</button>`);
      this.settlementNotice(state, error.message || "读取失败", true);
    }
  }

  async startBatchSettlementMatching(state, retry = false) {
    const data = state.data || {};
    if (!this.isBatchSettlementCurrent(state) || state.busy || data.binding || data.historical) return;
    const statuses = retry ? ["failed", "partial", "stale", "not_started"] : ["not_started", "stale"];
    if (!statuses.includes(data.matching?.status) || (!retry && state.autoMatchStarted)) return;
    state.autoMatchStarted = true;
    let request;
    try {
      const result = await this.settlementWrite(state, () => {
        request = state.request;
        return this.settlementApi("start_batch_matching", { batch_name: state.batchName, version_name: state.versionName || null });
      });
      if (!this.isBatchSettlementCurrent(state, request)) return;
      if (!result?.ok || !result.matching) throw new Error(result?.message || "本票匹配启动失败，请重试");
      state.data = { ...data, matching: result.matching };
      this.renderBatchSettlementDialog(state, state.data);
      await this.loadBatchSettlementDialog(state);
    } catch (error) {
      if (!this.isBatchSettlementCurrent(state, request)) return;
      state.data = { ...data, matching: { ...data.matching, status: "failed", error: error.message || "本票匹配启动失败" } };
      this.renderBatchSettlementDialog(state, state.data);
    }
  }

  renderBatchSettlementMatching(data) {
    const matching = data.matching || {};
    const labels = { not_started: ocTranslate("准备匹配"), stale: ocTranslate("资料已变化"), queued: ocTranslate("排队中"), running: ocTranslate("匹配中"), completed: ocTranslate("匹配完成"),
      partial: ocTranslate("部分失败"), failed: ocTranslate("匹配失败"), unavailable: ocTranslate("来源待补齐"), bound: ocTranslate("已保存关联"), historical: ocTranslate("历史版本") };
    const stages = { rules: "规则匹配", deepseek: "DeepSeek 补充匹配", saved: "已保存结果" };
    const counters = matching.counters || matching;
    const retry = ["partial", "failed", "stale", "not_started"].includes(matching.status);
    return `<section class="ocw-settlement-source"><div class="ocw-settlement-toolbar"><strong>${this.escape(labels[matching.status] || ocTranslate("状态待读取"))} · ${this.escape(stages[matching.stage] || ocTranslate("本票匹配"))}</strong>
      ${retry ? `<button class="ocw-outline-btn" data-settlement-action="retry-matching">${ocTranslate("本票重试")}</button>` : ""}</div>
      <p>${ocTranslate("已处理 {0} / {1} · 推荐 {2} · 证据不足 {3} · 失败 {4}", [this.escape(counters.processed ?? 0), this.escape(counters.total ?? 0), this.escape(counters.recommended ?? 0), this.escape(counters.no_match ?? 0), this.escape(counters.failed ?? 0)])}</p>
      <p class="ocw-settlement-hint">${ocTranslate("先按本票标识进行规则匹配，仅对未解决的候选使用 DeepSeek；结果保存后可直接读取，候选需复核确认。")}</p>
      ${matching.cached ? `<p class="ocw-settlement-hint">${ocTranslate("已读取本票保存结果。")}</p>` : ""}
      ${matching.finished_at ? `<p class="ocw-settlement-hint">${ocTranslate("保存时间：{0}", [this.escape(matching.finished_at)])}</p>` : ""}
      ${matching.message ? `<p>${this.escape(matching.message)}</p>` : ""}
      ${matching.error ? `<p class="ocw-settlement-notice is-error">${this.escape(matching.error)}</p>` : ""}
      ${matching.status === "completed" && !(data.candidates || []).some((row) => ["pending", "conflict"].includes(row.status)) ? `<p>${ocTranslate("未找到可靠匹配，结果已保存；可输入关键词人工搜索本票采购支出。")}</p>` : ""}</section>`;
  }

  renderBatchSettlementDialog(state, data) {
    if (data.freight_mode) return this.renderFreightWorkspace(state);
    const issues = [...new Set([...(data.binding?.issues || []), ...(data.blocking_reasons || [])])];
    this.settlementBody(state, `<div class="ocw-settlement-toolbar"><strong>${ocTranslate("本票匹配 · 国际物流审批号：{0}", [this.escape(data.logistics?.approval_no || data.matching?.approval_no || data.logistics?.instance || state.batchName || ocTranslate("待读取"))])}</strong><button class="ocw-outline-btn" data-settlement-action="refresh">${ocTranslate("刷新")}</button></div>
      <p>${this.escape(this.settlementAdoption(data))}</p>
      ${this.renderSettlementHealth(data)}
      ${data.message ? `<p>${this.escape(data.message)}</p>` : ""}
      ${data.binding ? `<div class="ocw-settlement-source-grid">${this.renderSettlementSource(data.logistics, "国际物流来源")}${this.renderSettlementSource(data.expense)}</div>
        ${this.renderSettlementComparison(data.logistics, data.expense)}
        <p>${ocTranslate("费用覆盖范围：{0} · 采用版本：{1}", [this.escape((data.binding.coverage || []).map((key) => Object.fromEntries(this.settlementCoverageOptions())[key] || key).join("、") || (data.expense?.coverage === "unknown" ? ocTranslate("待确认") : ocTranslate("国际运输费"))), this.escape(data.binding.version || ocTranslate("尚未采用"))])}</p>
        ${issues.map((issue) => `<p class="ocw-settlement-notice">${this.escape(issue)}</p>`).join("")}
        <p class="ocw-settlement-hint">${data.historical ? ocTranslate("显示此版本采用时的采购支出快照，当前原单后续变化不会改写历史结果。") : ocTranslate("已采用的费用仍需重新试算。装箱、货值或汇率待核对时，请先补充对应资料；已确认版本保留历史。")}</p>
        <div class="ocw-settlement-toolbar"><button class="ocw-outline-btn" data-settlement-action="source">${ocTranslate("打开原单")}</button>${data.historical ? "" : `<button class="ocw-outline-btn" data-settlement-action="correct">${ocTranslate("更正关联")}</button><button class="ocw-primary-btn" data-settlement-action="apply">${ocTranslate("核对范围／重试采用")}</button>`}
          ${!data.historical && (data.item_reviews || []).some((row) => row.packing_pending || row.goods_value_pending) ? `<button class="ocw-outline-btn" data-settlement-action="items">${ocTranslate("核对装箱与货值")}</button>` : ""}</div>`
        : data.historical ? `<p>${ocTranslate("此历史版本没有物流采购支出采用记录。")}</p>` : `${this.renderBatchSettlementMatching(data)}<div class="ocw-settlement-toolbar"><button class="ocw-primary-btn" data-settlement-action="search">${ocTranslate("搜索本票采购支出")}</button></div>
          ${(data.candidates || []).filter((row) => ["pending", "conflict"].includes(row.status)).map((row) => `<p>${this.escape(row.expense?.approval_no || row.expense?.instance)} · ${this.escape(this.settlementAmount(row.expense || {}))} · ${row.status === "conflict" ? ocTranslate("冲突") : ocTranslate("待确认")}<br>${ocTranslate("匹配依据：{0}", [this.escape(row.reason || row.method || ocTranslate("待复核"))])} <button class="ocw-outline-btn" data-settlement-action="candidate" data-id="${this.escape(row.id)}">${ocTranslate("复核关联")}</button></p>`).join("")}`}
      ${data.audit?.length ? `<details><summary>${ocTranslate("关联与采用记录")}</summary>${data.audit.map((row) => `<p>${this.escape(row.created_at || row.at || "")} · ${this.escape(row.action || ocTranslate("记录"))} · ${this.escape(row.actor || "")} ${this.escape(row.reason || "")}</p>`).join("")}</details>` : ""}`);
  }

  async openSettlementSearch(batchName, correction = null, onComplete = null, context = {}) {
    const versionName = context.versionName || correction?.viewed_version || (this.detailState?.batchName === batchName ? this.detailState.versionName : null);
    const logistics = context.logistics || correction?.logistics || {};
    const target = logistics.approval_no || logistics.instance || batchName;
    const state = this.settlementDialog(correction ? "更正关联 · 搜索本票新采购支出" : "本票匹配 · 搜索采购支出", [
      { fieldtype: "Data", fieldname: "settlement_query", label: ocTranslate("审批号、单据号或原单关键词") },
      { fieldtype: "Small Text", fieldname: "settlement_reason", label: ocTranslate("人工关联依据"), description: ocTranslate("请说明该采购支出与本票国际物流的对应关系。") },
    ], true);
    Object.assign(state, { after: null, pages: [], query: "", data: {} });
    const detailContext = this.detailState?.batchName === batchName ? this.settlementDetailContext() : null;
    const current = (request = state.request) => state.open && request === state.request && (!detailContext || detailContext === this.settlementDetailContext());
    const load = async () => {
      if (!current() || state.busy) return;
      const request = ++state.request;
      try {
        const result = state.query ? await this.settlementApi("find_expenses", { batch_name: batchName, query: state.query, after: state.after })
          : { ok: true, items: [], has_more: false, message: ocTranslate("请输入审批号、物流标识或货物关键词；搜索结果仅用于关联本票。") };
        if (!current(request)) return;
        if (!result?.ok) throw new Error(result?.message || "搜索失败");
        state.data = result;
        this.settlementBody(state, `<p><strong>${ocTranslate("固定关联目标 · 本票国际物流审批号：{0}", [this.escape(target)])}</strong></p>${correction ? this.renderSettlementSource(correction.expense, "当前关联（旧单）") : ""}<p class="ocw-settlement-hint">${ocTranslate("仅搜索本地归档、同一企业的物流类采购支出。填写人工关联依据后，选择候选进入资料复核。")}</p>
          <button class="ocw-primary-btn" data-settlement-action="search">${ocTranslate("搜索")}</button>${result.message ? `<p>${this.escape(result.message)}</p>` : ""}
          ${(result.items || []).map((row) => `<div class="ocw-settlement-search-row"><div><strong>${this.escape(row.approval_no || row.instance)}</strong><p>${this.escape(this.settlementAmount(row))} · ${this.escape(row.status || ocTranslate("待核对"))} ${row.occupied ? ocTranslate("· 已有关联") : ""} ${row.invalid ? ocTranslate("· 已失效") : ""}</p></div>
            ${!row.invalid ? `<button class="ocw-outline-btn" data-settlement-action="choose" data-id="${this.escape(row.id)}">${ocTranslate("选择并复核")}</button>` : ""}</div>`).join("") || (state.query ? `<p>${ocTranslate("没有符合关键词的采购支出，请调整关键词。")}</p>` : "")}
          <div class="ocw-settlement-toolbar">${state.pages.length ? `<button class="ocw-outline-btn" data-settlement-action="previous">${ocTranslate("上一页")}</button>` : ""}${result.has_more ? `<button class="ocw-outline-btn" data-settlement-action="next">${ocTranslate("下一页")}</button>` : ""}</div>`);
      } catch (error) {
        if (current(request)) {
          this.settlementBody(state, `<button class="ocw-outline-btn" data-settlement-action="search">${ocTranslate("重新搜索")}</button>`);
          this.settlementNotice(state, error.message || "搜索失败", true);
        }
      }
    };
    this.settlementEvents(state, async (action, $button) => {
      if (!current()) throw new Error(ocTranslate("当前批次或版本已变化，请重新打开本票搜索。"));
      if (action === "search") { state.query = String(state.dialog.get_value("settlement_query") || "").trim(); state.after = null; state.pages = []; return load(); }
      if (action === "next") { state.pages.push(state.after); state.after = state.data.next_cursor; return load(); }
      if (action === "previous") { state.after = state.pages.pop() ?? null; return load(); }
      if (action === "choose") {
        const reason = String(state.dialog.get_value("settlement_reason") || "").trim();
        if (!reason) throw new Error(ocTranslate("请填写人工关联依据，再选择候选。"));
        const result = await this.settlementWrite(state, () => this.settlementApi("prepare_manual_candidate", { batch_name: batchName, expense_id: $button.attr("data-id"), reason }));
        if (!current()) return;
        if (!result?.ok || !result.candidate) throw new Error(result?.message || "候选准备失败，请刷新重试");
        if (result.freight_mode) this.openFreightReview(batchName,versionName,result.candidate,context.freight || {},onComplete || (async()=>{}));
        else this.openSettlementCandidateReview([result.candidate], { batchName, versionName, correction, onComplete });
      }
    });
    await load();
  }

  openSettlementApplicationReview(batchName, data, onComplete) {
    const state = this.settlementDialog("核对费用范围并重试采用", this.settlementChoiceFields(data.expense, data.binding.coverage || []));
    this.settlementBody(state, `${this.renderSettlementSource(data.expense)}<p class="ocw-settlement-hint">${ocTranslate("仅提交范围和核对选择，费用与货物值由服务端读取当前原单。汇率和装箱资料请在原入口补齐。")}</p>`);
    this.settlementActions(state, `<button class="ocw-primary-btn" data-settlement-action="apply">${ocTranslate("重试采用当前原单")}</button>`);
    this.settlementEvents(state, async (action) => {
      if (action !== "apply") return;
      const choices = this.settlementChoices(state);
      const result = await this.settlementWrite(state, () => this.settlementApi("retry_application", { batch_name: batchName, expected_revision: data.binding.revision,
        expected_snapshot: data.expense.snapshot, expected_version: data.viewed_version,
        ...(choices.coverage.length ? { coverage: JSON.stringify(choices.coverage) } : {}), negative_confirmed: choices.negative_confirmed }));
      this.settlementActions(state, "");
      this.settlementBody(state, this.renderSettlementWriteResult(result));
      await this.refreshSettlementBatch(batchName);
      await onComplete();
    });
  }

  renderSettlementItemReviews(items) {
    return `<div class="ocw-settlement-table-wrap"><table class="ocw-settlement-table"><thead><tr><th>${ocTranslate("物料／品名")}</th><th>${ocTranslate("最终数量／单位")}</th><th>${ocTranslate("装箱数量／毛重／体积／计费重")}</th><th>${ocTranslate("采购货值")}</th><th>${ocTranslate("核对选择")}</th></tr></thead><tbody>${items.filter((row) => row.packing_pending || row.goods_value_pending).map((row, index) => `<tr><td>${this.escape(row.material_code)}<small>${this.escape(row.product_name)} ${this.escape(row.spec_model)}</small></td><td>${this.escape(row.quantity ?? "—")} ${this.escape(row.unit)}</td>
      <td>${ocTranslate("装箱数量 {0}", [this.escape(row.packing_quantity ?? "—")])}<small>${ocTranslate("毛重 {0} kg · 体积 {1} m³ · 计费重 {2} kg", [this.escape(row.gross_weight_kg ?? "—"), this.escape(row.volume_m3 ?? "—"), this.escape(row.chargeable_weight_kg ?? "—")])}</small>${this.renderSettlementPackingCandidates(row.packing_candidates || [])}</td><td>${this.escape(row.goods_value ?? "—")}<small>${ocTranslate("单价 {0}", [this.escape(row.unit_price ?? "—")])}</small></td><td>
      ${row.packing_pending ? `<label><input type="checkbox" data-review-index="${index}" data-check="packing_confirmed"> ${ocTranslate("已核对装箱对应关系")}</label>` : ""}${row.goods_value_pending ? `<label><input type="checkbox" data-review-index="${index}" data-check="goods_value_confirmed"> ${ocTranslate("已核对采购货值")}</label>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
  }

  renderSettlementPackingCandidates(candidates) {
    if (!candidates.length) return "";
    const labels = { actual_shipped_qty: ocTranslate("实发数量"), quantity_applicability: ocTranslate("最终数量／装箱数量"), gross_weight_kg: ocTranslate("毛重 kg"), volume_m3: ocTranslate("体积 m³"), chargeable_weight_kg: ocTranslate("计费重 kg"), volume_weight_kg: ocTranslate("体积重 kg"), weight_ratio: ocTranslate("重量比例") };
    return `<details open><summary>${ocTranslate("归档候选与现有值")}</summary>${candidates.map((candidate) => `<p>${this.escape(candidate.reason || ocTranslate("装箱资料待核对"))}<small>${ocTranslate("附件 {0} · 行 {1}", [this.escape(candidate.document_id || ""), this.escape(candidate.line ?? "—")])}</small></p>
      ${(candidate.conflicts || []).map((row) => `<p>${ocTranslate("{0}：现有 {1} → 归档候选 {2}", [this.escape(labels[row.field] || row.field), this.escape(row.existing ?? "—"), this.escape(row.candidate ?? "—")])}</p>`).join("")}
      ${candidate.retired ? `<p>${ocTranslate("原依据已撤销，受影响字段：{0}", [this.escape((candidate.fields || []).map((field) => labels[field] || field).join("、"))])}</p>` : ""}`).join("")}</details>`;
  }

  openSettlementItemReview(batchName, data, onComplete) {
    const items = (data.item_reviews || []).filter((row) => row.packing_pending || row.goods_value_pending);
    const state = this.settlementDialog("核对装箱与采购货值", [{ fieldtype: "Small Text", fieldname: "settlement_reason", label: ocTranslate("核对说明") }]);
    this.settlementBody(state, `<p class="ocw-settlement-hint">${ocTranslate("先在装箱或 SKU 入口修正资料，再刷新并逐项确认；这里只记录核对结果，不修改数量、重量或金额。")}</p>${this.renderSettlementItemReviews(items)}`);
    this.settlementActions(state, `<button class="ocw-primary-btn" data-settlement-action="verify">${ocTranslate("保存已勾选核对结果")}</button>`);
    this.settlementEvents(state, async (action) => {
      if (action !== "verify") return;
      const reason = String(state.dialog.get_value("settlement_reason") || "").trim();
      if (!reason) throw new Error(ocTranslate("请填写核对说明。"));
      const selections = items.map((row, index) => {
        const choices = {};
        state.dialog.fields_dict.settlement_body.$wrapper.find(`[data-review-index='${index}']:checked`).each((_, node) => { choices[$(node).attr("data-check")] = true; });
        return Object.keys(choices).length ? { item_name: row.item_name, expected_item_hash: row.revision, ...choices } : null;
      }).filter(Boolean);
      if (!selections.length) throw new Error(ocTranslate("请逐项勾选已完成核对的资料。"));
      const result = await this.settlementWrite(state, () => this.settlementApi("resolve_item_checks", { batch_name: batchName, expected_revision: data.binding.revision, selections: JSON.stringify(selections), reason }));
      this.settlementActions(state, "");
      this.settlementBody(state, this.renderSettlementWriteResult(result));
      await this.refreshSettlementBatch(batchName);
      await onComplete();
    });
  }
