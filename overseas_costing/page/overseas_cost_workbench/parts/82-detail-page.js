  getDetailBatch() {
    const header = this.detailState.header || {};
    return this.findBatch(this.detailState.batchName) || header;
  }

  async openBatchDetail(batchName = "", tab = "documents", options = {}) {
    const normalizedName = String(batchName || "").trim();
    if (!normalizedName) {
      this.showPendingFeature("缺少批次号，无法打开详情。");
      return;
    }
    // Detail navigation supersedes any outstanding list/summary response,
    // including browser history navigation while an edit is already open.
    this._workbenchRequestId = (this._workbenchRequestId || 0) + 1;
    if (this.detailState.editToken && this.detailState.batchName && this.detailState.batchName !== normalizedName) {
      await this.releaseEditSession();
    }
    if (this.detailState.batchName !== normalizedName) this.detailState.dingtalkApproval = null;
    const allowedTab = OverseasCostWorkbenchState.parseWorkbenchState(
      `${window.location.pathname}?screen=detail&batch=${encodeURIComponent(normalizedName)}&tab=${encodeURIComponent(tab || "items")}`
    ).tab;
    if (options.updateUrl !== false) {
      window.history.replaceState(
        { ...(window.history.state || {}), ocwScrollY: window.scrollY, overseasCostWorkbench: true },
        "",
        window.location.href
      );
      this.replaceViewState(
        { screen: "detail", batch: normalizedName, tab: allowedTab },
        { push: true }
      );
    }
    this.detailState.batchName = normalizedName;
    const requestId = ++this.detailState.requestId;
    this.detailState.refreshRequestId += 1;
    this.detailState.tab = allowedTab;
    this.activeBatchName = normalizedName;
    this.exportPinnedBatchName = normalizedName;
    this.dataCheckBatchName = normalizedName;
    this.drawerBatchName = normalizedName;
    this.$root.attr("data-screen", "detail");
    this.$root.find("[data-area='workbench-screen']").prop("hidden", true);
    this.$root.find("[data-area='detail-screen']").prop("hidden", false);
    this.renderDetailLoading();
    try {
      const result = await this.call("overseas_costing.api.batch.get_batch_detail", {
        batch_name: normalizedName,
      });
      if (requestId !== this.detailState.requestId || this.detailState.batchName !== normalizedName) return;
      if (!result || !result.ok) throw new Error((result && result.message) || "批次详情加载失败");
      const merged = {
        ...(this.findBatch(result.batch_name || normalizedName) || {}),
        ...(result.header || {}),
        name: result.batch_name || (result.header || {}).name || normalizedName,
        current_version: result.version_name || (result.header || {}).current_version || "",
        summary_snapshot: result.summary || {},
        allocation_rule_snapshot: result.allocation_rules || [],
      };
      const index = this.batches.findIndex((row) => row.name === merged.name);
      if (index >= 0) this.batches[index] = merged;
      else this.batches.push(merged);
      this.detailState.batchName = merged.name;
      this.detailState.versionName = result.version_name || merged.current_version || "";
      this.detailState.header = merged;
      this.detailState.detail = result;
      this.adoptBatchWriteRevision?.(merged.name, merged.modified);
      this.detailState.dirty = false;
      this.activeBatchName = merged.name;
      this.drawerBatchName = merged.name;
      this.renderDetailShell();
      await this.switchDetailTab(allowedTab, { updateUrl: false });
    } catch (error) {
      if (requestId !== this.detailState.requestId || this.detailState.batchName !== normalizedName) return;
      this.renderDetailError(error);
      if (options.propagateError) throw error;
    }
  }

  async returnToWorkbench() {
    if (!(await this.confirmDiscardDetailChanges())) return;
    await this.releaseEditSession();
    this.cleanupSkuScrollControls();
    this.cleanupMaterialGridScrollControls?.();
    this.detailState.requestId += 1;
    this.detailState.skuRequestId += 1;
    this.detailState.refreshRequestId += 1;
    this.replaceViewState({ screen: "", batch: "", tab: "" });
    this.detailState.batchName = "";
    this.detailState.header = null;
    this.detailState.detail = null;
    this.detailState.dingtalkApproval = null;
    this.exportPinnedBatchName = "";
    this.dataCheckBatchName = "";
    this.drawerBatchName = "";
    this.$root.attr("data-screen", "workbench");
    this.$root.find("[data-area='detail-screen']").prop("hidden", true).empty();
    this.$root.find("[data-area='workbench-screen']").prop("hidden", false);
    await this.loadBatches();
    requestAnimationFrame(() => window.scrollTo({ top: Number(window.history.state?.ocwScrollY || 0), behavior: "auto" }));
  }

  async refreshDetailSummary(options = {}) {
    const batchName = String(this.detailState.batchName || "").trim();
    if (!batchName) return;
    const activeTab = this.detailState.tab || "items";
    const requestId = ++this.detailState.refreshRequestId;
    let result;
    try {
      result = await this.call("overseas_costing.api.batch.get_batch_detail", {
        batch_name: batchName,
        version_name: this.detailState.versionName || null,
      });
    } catch (error) {
      if (requestId !== this.detailState.refreshRequestId || this.detailState.batchName !== batchName) return;
      throw error;
    }
    if (requestId !== this.detailState.refreshRequestId || this.detailState.batchName !== batchName) return;
    if (!result || !result.ok) throw new Error((result && result.message) || "批次详情刷新失败");
    const merged = {
      ...(this.findBatch(batchName) || {}),
      ...(this.detailState.header || {}),
      ...(result.header || {}),
      name: result.batch_name || batchName,
      current_version: result.version_name || (result.header || {}).current_version || this.detailState.versionName || "",
      summary_snapshot: result.summary || {},
      allocation_rule_snapshot: result.allocation_rules || [],
    };
    const index = this.batches.findIndex((row) => row.name === batchName);
    if (index >= 0) this.batches[index] = merged;
    this.detailState.header = merged;
    this.detailState.detail = result;
    this.detailState.versionName = merged.current_version;
    this.adoptBatchWriteRevision?.(merged.name, merged.modified);
    this.renderDetailShell();
    if (this.detailState.editToken) this.updateEditLeaseStatus();
    if (options.refreshCurrentTab === false) return;
    await this.switchDetailTab(activeTab, { updateUrl: false });
  }

  renderDetailLoading() {
    this.cleanupSkuScrollControls();
    this.cleanupMaterialGridScrollControls?.();
    this.$root.find("[data-area='detail-screen']").html(`
      <div class="ocw-detail-state"><span class="ocw-spinner"></span><strong>${ocTranslate("正在加载批次详情")}</strong><small>${ocTranslate("不会预加载 SKU 明细")}</small></div>
    `);
  }

  renderDetailError(error) {
    this.cleanupSkuScrollControls();
    this.cleanupMaterialGridScrollControls?.();
    this.$root.find("[data-area='detail-screen']").html(`
      <div class="ocw-detail-state is-error">
        <strong>${ocTranslate("批次详情加载失败")}</strong>
        <span>${this.escape(this.normalizeErrorMessage(error))}</span>
        <div><button class="ocw-outline-btn" type="button" data-action="return-workbench">${ocTranslate("返回工作台")}</button><button class="ocw-primary-btn" type="button" data-action="retry-detail">${ocTranslate("重试")}</button></div>
      </div>
    `);
  }

  detailCalculationAction(batch = {}) {
    const status = String(batch.status || "").toLowerCase();
    const confirmStatus = String(batch.confirm_status || "").toLowerCase();
    const task = this.viewState?.task || "pending";
    if (
      task === "erp"
      || status.includes("confirmed")
      || confirmStatus === "confirmed"
      || Number(batch.is_locked || 0) === 1
    ) return null;
    const summary = batch.summary_snapshot || {};
    const hasSavedResult = Boolean(
      batch.calculated_at
      || summary.calculation_schema
      || status.includes("calculated")
      || status.includes("confirmed")
      || Number(batch.actual_total_cost_rmb || batch.estimated_total_cost_rmb || summary.total_cost_rmb || 0) > 0
    );
    return { action: "recalculate", label: hasSavedResult ? ocTranslate("重新试算") : ocTranslate("开始试算") };
  }

  renderDetailErpAction(batch = {}) {
    const erpAction = this.erpPushActionState(batch, Number(batch.item_count || 0));
    const reasonId = "ocw-detail-erp-action-reason";
    return `
      <span class="ocw-detail-erp-action" data-area="detail-erp-action" title="${this.escape(ocTranslate(erpAction.reason))}">
        <button class="ocw-outline-btn" type="button" data-action="detail-writeback-to-erp" aria-label="${this.escape(`${ocTranslate(erpAction.label)}: ${ocTranslate(erpAction.reason)}`)}" aria-describedby="${reasonId}"${erpAction.enabled ? "" : " disabled"}>${this.escape(ocTranslate(erpAction.label))}</button>
        <small id="${reasonId}">${this.escape(ocTranslate(erpAction.reason))}</small>
      </span>
    `;
  }

  updateDetailErpAction(batch = null) {
    const current = batch || this.getDetailBatch();
    if (!current || this.detailState?.batchName !== current.name) return;
    this.$root.find("[data-area='detail-erp-action']").replaceWith(this.renderDetailErpAction(current));
  }

  renderDetailShell() {
    this.cleanupSkuScrollControls();
    this.cleanupMaterialGridScrollControls?.();
    const batch = this.getDetailBatch();
    const reference = batch.batch_no || batch.source_approval_no || batch.customs_no || batch.name;
    const logistics = batch.waybill_no || batch.container_no || batch.sea_bill_no || ocTranslate("未填写物流单号");
    this.$root.find("[data-area='detail-screen']").html(`
      <div class="ocw-detail-page">
        <header class="ocw-detail-header">
          <div class="ocw-detail-heading">
            <button class="ocw-back-btn" type="button" data-action="return-workbench" aria-label="${ocTranslate('返回工作台')}">← <span>${ocTranslate("返回工作台")}</span></button>
            <div>
              <span class="ocw-detail-eyebrow">${ocTranslate("批次详情")}</span>
              <h1>${this.escape(reference)}</h1>
              <p>${this.escape(logistics)} · ${this.escape(this.businessTypeLabel(batch.business_type) || this.transportLabel(batch.transport_mode) || ocTranslate("未分类"))}</p>
            </div>
          </div>
          <div class="ocw-detail-header-actions">
            ${this.renderReviewReturnAction?.(batch) || ""}
            <button class="ocw-outline-btn" type="button" data-action="detail-dingtalk">${ocTranslate("打开钉钉原单")}</button>
            ${this.renderDetailErpAction(batch)}
            <div class="ocw-menu-wrap">
              <button class="ocw-outline-btn" type="button" data-action="toggle-detail-tools" aria-expanded="false">${ocTranslate("更多操作 ▾")}</button>
              <div class="ocw-detail-tools" data-area="detail-tools" hidden>
                <button type="button" data-action="detail-export">${ocTranslate("导出")}</button>
                <button type="button" data-action="detail-voucher">${ocTranslate("凭证对比")}</button>
                <button type="button" data-action="detail-category">${ocTranslate("商品归类")}</button>
                <button type="button" data-action="detail-excel">${ocTranslate("单批次 Excel 补充")}</button>
              </div>
            </div>
          </div>
        </header>
        <nav class="ocw-detail-tabs" aria-label="${ocTranslate('批次详情分类')}">
          ${[
            ["overview", "总览"],
            ["dingtalk", "钉钉审批"],
            ["documents", "资料与费用"],
            ["items", "SKU 明细"],
            ["vouchers", "凭证核对"],
            ["review", ocTranslate("复核沟通{0}", [Number(batch.unresolved_count || 0) ? ` <span class="ocw-detail-tab-badge">${Number(batch.unresolved_count || 0)}</span>` : ""])],
            ["audit", "操作记录"],
          ].map(([key, label]) => `<button class="${this.detailState.tab === key ? "is-active" : ""}" type="button" data-action="switch-detail-tab" data-tab="${key}">${ocTranslate(label)}</button>`).join("")}
        </nav>
        <section class="ocw-detail-content" data-area="detail-content"></section>
        <div data-area="review-drawer-host"></div>
      </div>
    `);
  }

  async switchDetailTab(tab = "items", options = {}) {
    const allowed = OverseasCostWorkbenchState.parseWorkbenchState(
      `${window.location.pathname}?screen=detail&batch=x&tab=${encodeURIComponent(tab)}`
    ).tab;
    if (allowed !== this.detailState.tab && !(await this.confirmDiscardDetailChanges())) return;
    this.cleanupSkuScrollControls();
    this.cleanupMaterialGridScrollControls?.();
    this.detailState.tab = allowed;
    this.$root.find("[data-action='switch-detail-tab']").each((_, node) => {
      $(node).toggleClass("is-active", $(node).attr("data-tab") === allowed);
    });
    if (options.updateUrl !== false) {
      this.replaceViewState({ screen: "detail", batch: this.detailState.batchName, tab: allowed });
    }
    if (allowed === "items") return this.loadSkuPage();
    if (allowed === "audit") return this.renderAuditDetailTab();
    if (allowed === "vouchers") return this.renderVoucherDetailTab();
    if (allowed === "dingtalk") return this.renderDingtalkApprovalTab();
    if (allowed === "documents") return this.renderDocumentsDetailTab();
    if (allowed === "review") return this.renderReviewCommunicationTab();
    return this.renderOverviewDetailTab();
  }

  renderDetailTabLoading(label) {
    this.cleanupSkuScrollControls();
    this.cleanupMaterialGridScrollControls?.();
    this.$root.find("[data-area='detail-content']").html(`
      <div class="ocw-detail-state"><span class="ocw-spinner"></span><strong>${this.escape(ocTranslate(label))}</strong></div>
    `);
  }

  renderDetailOverviewDashboard(batch = {}) {
    const model = this.buildCostFlowPresentation(batch, []);
    const calculationAction = this.detailCalculationAction(batch);
    const amountText = (value, currency = "RMB") => value === null || value === undefined || value === ""
      ? "--"
      : `${this.formatMoney(value)} ${currency || "RMB"}`;
    const feeRows = model.feeRows.length
      ? model.feeRows.map((fee) => `
          <tr>
            <td><strong>${this.escape(ocTranslate(fee.label))}</strong></td>
            <td>${this.escape(amountText(fee.amount, fee.currency))}</td>
            <td>${this.escape(amountText(fee.amountRmb, "RMB"))}</td>
            <td><span class="ocw-overview-state is-${this.escape(fee.state)}">${this.escape(ocTranslate(fee.stateLabel))}</span></td>
          </tr>
        `).join("")
      : `<tr><td colspan="4"><div class="ocw-detail-empty">${ocTranslate("当前没有费用构成记录")}</div></td></tr>`;
    const flowLabels = {
      done: "已完成",
      current: "当前",
      pending: "未开始",
      error: "失败待重试",
    };
    const documentsReady = model.documents.every((row) => ["ready", "unused"].includes(row.state));
    const flowSteps = [
      { key: "documents", label: ocTranslate("资料补充"), detail: model.flow.documents === "done" ? ocTranslate("本次试算所需资料已采用") : documentsReady ? ocTranslate("物料与费用资料已具备") : ocTranslate("仍有资料需处理") },
      { key: "trial", label: ocTranslate("成本试算"), detail: model.statusInfo.label || (model.hasCurrentTrial ? ocTranslate("已试算") : ocTranslate("待试算")) },
      { key: "erp", label: ocTranslate("推送 ERP"), detail: model.writebackInfo.label || ocTranslate("未开始") },
    ];
    const documents = model.documents.map((document) => `
      <li class="is-${this.escape(document.state)}">
        <span class="ocw-overview-document-mark" aria-hidden="true"></span>
        <div><strong>${this.escape(ocTranslate(document.label))}</strong><small>${this.escape(ocTranslate(document.detail))}</small></div>
        <button type="button" data-action="switch-detail-tab" data-tab="${this.escape(document.tab)}">${document.count ? ocTranslate("{0} 项 · ", [document.count]) : ""}${this.escape(ocTranslate(document.stateLabel))}</button>
      </li>
    `).join("");
    const basicInfo = [
      ["运输方式", this.transportLabel(batch.transport_mode) || "--"],
      ["业务类型", this.businessTypeLabel(batch.business_type) || "--"],
      ["业务主体", batch.subsidiary_code || "--"],
      ["物料行数", ocTranslate("{0} 行", [model.itemCount])],
      ["运单/柜号", batch.waybill_no || batch.container_no || batch.sea_bill_no || "--"],
    ];
    const currentStep = model.flow.erp === "current" || model.flow.erp === "error"
      ? "推送 ERP"
      : model.flow.trial === "current"
        ? "成本试算"
        : "资料补充";
    const actionButtons = [
      calculationAction
        ? `<button class="ocw-primary-btn" type="button" data-action="detail-primary" data-primary-action="${calculationAction.action}">${this.escape(ocTranslate(calculationAction.label))}</button>`
        : "",
      model.isCostReview && !model.confirmed
        ? `<button class="ocw-primary-btn" type="button" data-action="confirm-calculation-result"${model.canConfirm ? "" : " disabled"}>${ocTranslate("校验计算结果")}</button>`
        : "",
      model.confirmed
        ? `<button class="ocw-outline-btn" type="button" data-action="preview-erp-payload"${model.canPreview ? "" : " disabled"}>${ocTranslate("预览 ERP 报文")}</button>`
        : "",
    ].filter(Boolean).join("");
    return `
      <div class="ocw-detail-overview-dashboard">
        <section class="ocw-detail-overview-costs">
          <div class="ocw-overview-panel-title"><div><span>${ocTranslate("成本结果")}</span><h2>${ocTranslate("本次成本")}</h2></div></div>
          <div class="ocw-overview-total-grid">
            <article><span>${ocTranslate("采购货值")}</span><strong>${this.escape(amountText(model.goodsValueRmb, "RMB"))}</strong></article>
            <article class="is-primary"><span>${ocTranslate("综合成本")}</span><strong>${this.escape(amountText(model.totalCostRmb, "RMB"))}</strong></article>
          </div>
          <div class="ocw-overview-fee-head"><h3>${ocTranslate("费用构成")}</h3><strong>${ocTranslate("合计 {0}", [this.escape(amountText(model.feeTotalRmb, "RMB"))])}</strong></div>
          <div class="ocw-overview-fee-scroll">
            <table class="ocw-overview-fee-table">
              <thead><tr><th>${ocTranslate("费用项目")}</th><th>${ocTranslate("原币金额")}</th><th>${ocTranslate("折合 RMB")}</th><th>${ocTranslate("状态")}</th></tr></thead>
              <tbody>${feeRows}</tbody>
            </table>
          </div>
          <dl class="ocw-overview-basic-info">
            ${basicInfo.map(([label, value]) => `<div><dt>${this.escape(ocTranslate(label))}</dt><dd>${this.escape(this.formatValue(value))}</dd></div>`).join("")}
          </dl>
          <div class="ocw-overview-current-action">
            <div><span>${ocTranslate("当前需要")}</span><strong>${this.escape(ocTranslate(currentStep))}</strong><small>${this.escape(model.invalidBusiness ? ocTranslate("当前审批已无效，不可确认或推送。") : model.statusInfo.suggestion || model.statusInfo.label || ocTranslate("按当前流程继续处理。"))}</small></div>
            <div class="ocw-overview-current-buttons">${actionButtons}</div>
          </div>
        </section>
        <aside class="ocw-detail-overview-progress">
          <div class="ocw-overview-panel-title"><div><span>${ocTranslate("业务流程")}</span><h2>${ocTranslate("处理进度")}</h2></div></div>
          <ol class="ocw-overview-flow">
            ${flowSteps.map((step, index) => `<li data-step="${step.key}" data-state="${model.flow[step.key]}"><span>${model.flow[step.key] === "done" ? "✓" : index + 1}</span><div><strong>${this.escape(ocTranslate(step.label))}</strong><small>${this.escape(ocTranslate(step.detail))}</small></div><em>${this.escape(ocTranslate(flowLabels[model.flow[step.key]]))}</em></li>`).join("")}
          </ol>
          <div class="ocw-overview-documents">
            <div><h3>${ocTranslate("资料清单")}</h3><span>${ocTranslate("点击状态查看对应资料")}</span></div>
            <ul>${documents}</ul>
          </div>
        </aside>
      </div>
    `;
  }

  renderOverviewDetailTab() {
    const batch = this.getDetailBatch();
    const approval = this.detailState.dingtalkApproval;
    if (!approval || approval.batch_name !== batch.name) {
      const requestedBatch = batch.name;
      this.loadDingtalkApprovalDetail()
        .then(() => {
          if (this.detailState.tab === "overview" && this.detailState.batchName === requestedBatch) {
            this.renderOverviewDetailTab();
          }
        })
        .catch(() => {});
    }
    this.$root.find("[data-area='detail-content']").html(`
      <div class="ocw-detail-overview">
        ${this.renderDetailOverviewDashboard(batch)}
        ${this.renderDetailErpSites?.(batch) || ""}
      </div>
    `);
    // 站点面板先渲染骨架再拉真实状态：预览要走服务端路由解析，不该挡住整页。
    this.refreshErpSitePanel?.().catch(() => {});
  }

  detailDocumentAdapter() {
    return { $wrapper: this.$root.find("[data-area='detail-screen']") };
  }

  async renderDocumentsDetailTab() {
    return this.loadMaterialFeeWorkspace();
  }

  async renderVoucherDetailTab() {
    const batch = this.getDetailBatch();
    this.renderDetailTabLoading("正在读取凭证核对记录");
    try {
      const result = await this.call("overseas_costing.api.import_api.list_tax_certificate_parse_records", {
        batch_name: batch.name,
        limit: 20,
      });
      const items = (result && result.items) || [];
      this.$root.find("[data-area='detail-content']").html(`
        <div class="ocw-detail-section-head"><div><span>${ocTranslate("最终对账")}</span><h2>${ocTranslate("凭证核对")}</h2></div><button class="ocw-primary-btn" type="button" data-action="detail-voucher">${ocTranslate("+ 新增凭证对比")}</button></div>
        <div class="ocw-detail-voucher-list">
          ${items.length ? items.map((row) => this.renderTaxCertificateRecord(row)).join("") : `<div class="ocw-detail-empty"><strong>${ocTranslate("当前批次暂无凭证记录")}</strong><span>${ocTranslate("可上传完税凭证 PDF 与系统税费进行对比。")}</span></div>`}
        </div>
      `);
    } catch (error) {
      this.renderDetailTabError("凭证记录", error);
    }
  }

  async renderAuditDetailTab() {
    const batch = this.getDetailBatch();
    this.renderDetailTabLoading("正在读取操作记录");
    try {
      const [auditResult, usageResult] = await Promise.all([
        this.call("overseas_costing.api.batch.get_audit_logs", {
          batch_name: batch.name,
          version_name: this.detailState.versionName || batch.current_version || null,
          limit: 80,
        }),
        this.call("overseas_costing.api.usage.get_usage_logs", {
          batch_name: batch.name,
          limit: 80,
        }),
      ]);
      if (!auditResult || !auditResult.ok) throw new Error((auditResult && auditResult.message) || "修改记录加载失败");
      if (!usageResult || !usageResult.ok) throw new Error((usageResult && usageResult.message) || "使用记录加载失败");
      const auditEvents = (auditResult.items || []).map((row) => this.mapAuditRow(row, batch));
      const usageEvents = (usageResult.items || []).map((row) => this.mapUsageRow(row, batch));
      this.auditEvents = [...auditEvents, ...usageEvents].sort((left, right) => String(right.time || "").localeCompare(String(left.time || "")));
      const events = this.buildAuditSummaryEvents(this.auditEvents);
      this.$root.find("[data-area='detail-content']").html(`
        <div class="ocw-detail-section-head"><div><span>${ocTranslate("可追溯")}</span><h2>${ocTranslate("操作记录")}</h2></div><span>${ocTranslate("{0} 条", [events.length])}</span></div>
        <ul class="ocw-audit-list ocw-detail-audit-list">${events.length ? events.map((event) => this.renderAuditEvent(event)).join("") : `<li class="ocw-audit-empty">${ocTranslate("当前批次暂无操作记录")}</li>`}</ul>
      `);
    } catch (error) {
      this.renderDetailTabError("操作记录", error);
    }
  }

  renderDetailTabError(label, error) {
    this.$root.find("[data-area='detail-content']").html(`
      <div class="ocw-detail-empty is-error"><strong>${ocTranslate("{0}加载失败", [this.escape(ocTranslate(label))])}</strong><span>${this.escape(this.normalizeErrorMessage(error))}</span><button class="ocw-outline-btn" type="button" data-action="retry-detail-tab">${ocTranslate("重试")}</button></div>
    `);
  }

  async loadSkuPage() {
    const batch = this.getDetailBatch();
    const sku = this.detailState.sku;
    const requestId = ++this.detailState.skuRequestId;
    this.renderDetailTabLoading("正在读取 SKU 当前页");
    try {
      const result = await this.call("overseas_costing.api.workbench.get_batch_items_page", {
        batch_name: batch.name,
        version_name: this.detailState.versionName || batch.current_version || null,
        keyword: sku.keyword || "",
        page: sku.page,
        page_length: sku.pageLength,
        field_group: sku.fieldGroup,
        sort_by: sku.sortBy,
        sort_order: sku.sortOrder,
      });
      if (requestId !== this.detailState.skuRequestId || this.detailState.batchName !== batch.name) return;
      if (!result || !result.ok) throw new Error((result && result.message) || "SKU 明细加载失败");
      if (!(result.items || []).length && sku.page > 1 && Number(result.total || 0) > 0) {
        sku.page -= 1;
        return this.loadSkuPage();
      }
      this.detailState.versionName = result.version_name || this.detailState.versionName;
      this.detailState.skuResult = result;
      this.renderSkuDetailTab(result);
    } catch (error) {
      if (requestId !== this.detailState.skuRequestId || this.detailState.batchName !== batch.name) return;
      this.renderDetailTabError("SKU 明细", error);
    }
  }

  renderSkuDetailTab(result = {}) {
    const sku = this.detailState.sku;
    const columns = result.columns || [];
    const items = result.items || [];
    const groups = [
      ["basic", "基础信息"], ["purchase", "采购数据"], ["logistics", "物流费用"],
      ["tax", "税费"], ["total", "综合成本"], ["all", "全部字段"],
    ];
    const header = columns.map((column, index) => {
      const sortable = ["material_code", "product_name", "quantity", "goods_value", "total_cost_rmb", "total_unit_rmb"].includes(column.fieldname);
      const sortMark = sku.sortBy === column.fieldname ? (sku.sortOrder === "asc" ? " ↑" : " ↓") : "";
      return `<th class="${index < 2 ? `ocw-sku-sticky ocw-sku-sticky-${index}` : ""}" title="${this.escape(`${column.excel_col} ${column.label}`)}">${sortable ? `<button type="button" data-action="sku-sort" data-sort-by="${this.escape(column.fieldname)}">` : ""}<span>${this.escape(column.excel_col)}</span>${this.escape(ocTranslate(column.label))}${sortMark}${sortable ? "</button>" : ""}</th>`;
    }).join("");
    const body = items.map((row) => `<tr class="${this.approvalLinkNeedsReview(row.approval_link) ? "ocw-approval-row" : ""}">${columns.map((column, index) => this.renderSkuPageCell(row, column, index)).join("")}</tr>`).join("");
    this.$root.find("[data-area='detail-content']").html(`
      <div class="ocw-detail-section-head"><div><span>${ocTranslate("服务端分页")}</span><h2>${ocTranslate("SKU 明细")}</h2>${result.calculation_stale ? `<span>${ocTranslate("结果待更新，请先开始试算")}</span>` : ""}</div><strong>${ocTranslate("共 {0} 行", [Number(result.total || 0)])}</strong></div>
      <div class="ocw-sku-toolbar">
        <label><span>${ocTranslate("搜索当前批次 SKU")}</span><input class="form-control" type="search" data-role="sku-keyword" value="${this.escape(sku.keyword)}" placeholder="${ocTranslate('物料编码或产品名称')}" /></label>
        <div class="ocw-sku-groups" role="group" aria-label="${ocTranslate('SKU 字段分组')}">${groups.map(([key, label]) => `<button class="${sku.fieldGroup === key ? "is-active" : ""}" type="button" data-action="sku-group" data-field-group="${key}">${ocTranslate(label)}</button>`).join("")}</div>
      </div>
      <div class="ocw-sku-table-shell">
        <div class="ocw-sku-table-wrap" data-role="sku-table-scroll">
          <table class="ocw-sku-table"><thead><tr>${header}</tr></thead><tbody>${body || `<tr><td colspan="${Math.max(columns.length, 1)}"><div class="ocw-detail-empty">${ocTranslate("当前条件下没有 SKU")}</div></td></tr>`}</tbody></table>
        </div>
        <div class="ocw-sku-scroll-controls">
          <button type="button" data-action="sku-scroll" data-direction="-1" aria-label="${ocTranslate('向左滚动 SKU 表')}">◀</button>
          <div class="ocw-horizontal-scrollbar ocw-sku-scrollbar" data-role="sku-scrollbar" data-ocw-scrollbar tabindex="0" aria-label="${ocTranslate('SKU 明细水平滚动条')}"><div data-role="sku-scrollbar-spacer"></div></div>
          <button type="button" data-action="sku-scroll" data-direction="1" aria-label="${ocTranslate('向右滚动 SKU 表')}">▶</button>
          <span>${ocTranslate("当前 {0} · 全部 A–BE", [this.escape(ocTranslate(groups.find(([key]) => key === sku.fieldGroup)?.[1] || "基础信息"))])}</span>
        </div>
      </div>
      <div class="ocw-sku-pagination">
        <span>${ocTranslate("每页 {0} 行", [sku.pageLength])}</span>
        <button class="ocw-outline-btn" type="button" data-action="sku-page" data-page="${Number(result.page || 1) - 1}" ${Number(result.page || 1) <= 1 ? "disabled" : ""}>${ocTranslate("上一页")}</button>
        <strong>${ocTranslate("第 {0} / {1} 页", [Number(result.page || 1), Math.max(Number(result.page_count || 0), 1)])}</strong>
        <button class="ocw-outline-btn" type="button" data-action="sku-page" data-page="${Number(result.page || 1) + 1}" ${Number(result.page || 1) >= Number(result.page_count || 0) ? "disabled" : ""}>${ocTranslate("下一页")}</button>
      </div>
    `);
    requestAnimationFrame(() => this.bindSkuScrollControls());
  }

  renderSkuPageCell(row, column, index) {
    if (column.fieldname === "approval_link") {
      return `<td class="ocw-readonly-cell" data-editable-cell="0">${this.renderApprovalLinkMarker(row.approval_link) || this.escape(row.approval_link?.approval_no || (row.approval_link?.status === "linked" ? ocTranslate("已关联") : "--"))}</td>`;
    }
    const editable = column.fieldname !== "transport_mode" && this.isEditableColumn(column);
    const rawValue = this.shouldShowEmptyZeroFee(column.fieldname, row[column.fieldname]) ? "" : this.normalizeEditorValue(row[column.fieldname]);
    const displayValue = this.formatCellValue(row[column.fieldname], column);
    const content = this.renderCell(row[column.fieldname], column);
    return `
      <td class="${index < 2 ? `ocw-sku-sticky ocw-sku-sticky-${index}` : ""} ${editable ? "ocw-editable-cell" : "ocw-readonly-cell"} ${this.escape(this.columnAlignClass(column))}"
        title="${this.escape(displayValue || "")}" data-editable-cell="${editable ? "1" : "0"}"
        data-batch-name="${this.escape(this.detailState.batchName)}" data-item-name="${this.escape(row.name || "")}"
        data-version-name="${this.escape(this.detailState.versionName || "")}" data-fieldname="${this.escape(column.fieldname)}"
        data-field-label="${this.escape(ocTranslate(column.label))}" data-raw-value="${this.escape(rawValue)}"
        data-special-override="${this.specialOverrideFields.has(column.fieldname) ? "1" : "0"}">${index < 2 ? `<span class="ocw-sku-sticky-content">${content}</span>` : content}${column.fieldname === "product_name" ? (this.renderReviewFeedbackButton?.({ target_tab: "items", target_field: column.fieldname, target_item: row.name }, "反馈此行") || "") : ""}</td>
    `;
  }

  cleanupSkuScrollControls() {
    const cleanup = this.skuScrollCleanup;
    this.skuScrollCleanup = null;
    if (typeof cleanup === "function") cleanup();
  }

  bindSkuScrollControls() {
    this.cleanupSkuScrollControls();
    this.cleanupMaterialGridScrollControls?.();
    this.skuScrollCleanup = this.bindHorizontalScrollController({
      content: this.$root.find("[data-role='sku-table-scroll']").get(0),
      scrollbar: this.$root.find("[data-role='sku-scrollbar']").get(0),
      spacer: this.$root.find("[data-role='sku-scrollbar-spacer']").get(0),
      leftButton: this.$root.find("[data-action='sku-scroll'][data-direction='-1']").get(0),
      rightButton: this.$root.find("[data-action='sku-scroll'][data-direction='1']").get(0),
    });
  }

  async ensureEditSession() {
    if (this.detailState.editToken) return true;
    const batch = this.getDetailBatch();
    if (!batch || !batch.name) return false;
    const batchName = String(batch.name);
    const acquireId = Number(this.editSessionAcquireId || 0) + 1;
    this.editSessionAcquireId = acquireId;
    const result = await this.call("overseas_costing.api.edit_session.acquire", { batch_name: batchName }, true);
    if (String(this.detailState.batchName || "") !== batchName || this.editSessionAcquireId !== acquireId) {
      if (result?.ok && result.edit_token) {
        try {
          await this.call("overseas_costing.api.edit_session.release", {
            batch_name: batchName,
            edit_token: result.edit_token,
          });
        } catch (error) {
          console.warn("[overseas-cost-workbench] 旧批次编辑租约释放失败，将在过期后自动释放", error);
        }
      }
      return false;
    }
    if (!result || !result.ok) {
      this.detailState.readonly = true;
      const lockedBy = (result && result.locked_by) || "其他用户";
      const expiresAt = this.formatDateTimeMinute((result && result.expires_at) || "") || "租约过期";
      this.$root.find("[data-area='edit-lease-status']").addClass("is-locked").text(ocTranslate("只读 · 由 {0} 编辑至 {1}", [lockedBy, expiresAt]));
      this.$root.find("[data-area='detail-content'] [data-editable-cell='1']").attr("data-editable-cell", "0").addClass("ocw-readonly-cell");
      frappe.show_alert({ message: (result && result.message) || ocTranslate("当前批次正在被 {0} 编辑。", [lockedBy]), indicator: "orange" });
      return false;
    }
    this.detailState.readonly = false;
    this.detailState.editToken = result.edit_token;
    this.detailState.editExpiresAt = result.expires_at;
    // 保留详情加载时的 modified，以便首次写入仍能发现“加载后、获取锁前”的并发修改。
    this.acceptBatchWriteRevision?.(this.detailState.expectedModified || batch.modified || result.modified);
    this.updateEditLeaseStatus();
    window.clearInterval(this.detailState.renewTimer);
    this.detailState.renewTimer = window.setInterval(() => this.renewEditSession(), 120000);
    return true;
  }

  async renewEditSession() {
    if (!this.detailState.editToken || !this.detailState.batchName) return;
    try {
      const result = await this.call("overseas_costing.api.edit_session.renew", {
        batch_name: this.detailState.batchName,
        edit_token: this.detailState.editToken,
      });
      if (!result || !result.ok) throw new Error((result && result.message) || "续租失败");
      this.detailState.editExpiresAt = result.expires_at;
      this.updateEditLeaseStatus();
    } catch (error) {
      window.clearInterval(this.detailState.renewTimer);
      this.detailState.renewTimer = null;
      this.detailState.editToken = "";
      this.detailState.readonly = true;
      this.$root.find("[data-area='edit-lease-status']").addClass("is-locked").text(ocTranslate("编辑权已失效 · 请重新点击要修改的单元格"));
    }
  }

  updateEditLeaseStatus() {
    const expiresAt = this.formatDateTimeMinute(this.detailState.editExpiresAt || "") || "5 分钟后";
    this.$root.find("[data-area='edit-lease-status']").removeClass("is-locked").addClass("is-editing").text(ocTranslate("已获得编辑权 · 自动续租至 {0}", [expiresAt]));
  }

  async releaseEditSession() {
    window.clearInterval(this.detailState.renewTimer);
    this.detailState.renewTimer = null;
    const token = this.detailState.editToken;
    const batchName = this.detailState.batchName;
    this.detailState.editToken = "";
    this.detailState.editExpiresAt = "";
    this.detailState.dirty = false;
    if (!token || !batchName) return;
    try {
      await this.call("overseas_costing.api.edit_session.release", { batch_name: batchName, edit_token: token });
    } catch (error) {
      console.warn("[overseas-cost-workbench] 编辑租约释放失败，将在过期后自动释放", error);
    }
  }

  async confirmDiscardDetailChanges() {
    const hasCellChanges = Boolean(this.detailState.dirty);
    const hasReviewDrafts = Boolean(this.hasUnsavedReviewDrafts?.());
    if (!hasCellChanges && !hasReviewDrafts) return true;
    return new Promise((resolve) => {
      frappe.confirm(
        hasReviewDrafts ? "当前有未提交的整改问题，确认放弃并离开？" : "当前单元格修改尚未保存，确认放弃并离开？",
        () => {
          this.$root.find(".ocw-cell-editor").each((_, editor) => this.cancelCellEdit($(editor).closest("td")));
          this.detailState.dirty = false;
          if (hasReviewDrafts) this.discardReviewDrafts?.();
          resolve(true);
        },
        () => resolve(false)
      );
    });
  }
