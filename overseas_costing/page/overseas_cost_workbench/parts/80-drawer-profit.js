  async exportCurrentResult() {
    const scope = this.getExportCurrentBatches();
    let batches = scope.batches;
    const label = scope.label;
    const isSingleBatchDetail = this.$root.attr("data-screen") === "detail" && Boolean(this.exportPinnedBatchName);
    if (!isSingleBatchDetail && this.workbenchTotal > batches.length) {
      batches = await this.loadAllWorkbenchBatchesForExport();
    }
    if (!batches.length) {
      frappe.msgprint(ocTranslate("当前没有可导出的批次。"));
      return;
    }
    const result = await this.call(
      "overseas_costing.api.batch.export_current_result_xlsx",
      {
        batch_names_json: JSON.stringify(batches.map((batch) => batch.name)),
        transport_label: label,
      },
      true
    );
    if (!result.ok) throw new Error(result.message || "导出失败");
    this.downloadBase64File(result.content_base64, result.file_name, result.mime_type);
    this.recordUsage("EXPORT", { remark: ocTranslate("导出当前全部批次结果：{0}，{1} 行", [label || "全部", result.total || 0]), extra: { total: result.total || 0 } });
    frappe.show_alert({ message: result.message || ocTranslate("已导出 {0} 行 SKU 明细。", [result.total || 0]), indicator: "green" });
  }

  async loadAllWorkbenchBatchesForExport() {
    const pageLength = 100;
    const batches = [];
    let page = 1;
    let total = Number(this.workbenchTotal || 0);
    do {
      const result = await this.call("overseas_costing.api.workbench.get_batches", {
        filters_json: JSON.stringify(this.workbenchFilters()),
        task: this.viewState.task,
        page,
        page_length: pageLength,
      });
      if (!result || !result.ok) throw new Error((result && result.message) || "导出范围加载失败");
      const pageItems = result.items || [];
      batches.push(...pageItems);
      total = Number(result.total || total || 0);
      if (!pageItems.length) break;
      page += 1;
    } while (batches.length < total);
    return batches;
  }

  async exportDrawerBatch() {
    const batch = this.drawerBatchName ? this.findBatch(this.drawerBatchName) : null;
    if (!batch) {
      frappe.msgprint(ocTranslate("当前没有可导出的批次。"));
      return;
    }
    const result = await this.call(
      "overseas_costing.api.batch.export_current_result_xlsx",
      {
        batch_names_json: JSON.stringify([batch.name]),
        transport_label: this.batchReferenceLabel(batch),
      },
      true
    );
    if (!result.ok) throw new Error(result.message || "导出失败");
    this.downloadBase64File(result.content_base64, result.file_name, result.mime_type);
    this.recordUsage("EXPORT", { batch, remark: ocTranslate("导出当前批次：{0} 行", [result.total || 0]), extra: { total: result.total || 0 } });
    frappe.show_alert({ message: result.message || ocTranslate("已导出当前批次 {0} 行 SKU 明细。", [result.total || 0]), indicator: "green" });
  }

  async openBatchDrawer(batchName = "", options = {}) {
    const batch = batchName ? this.findBatch(batchName) : this.getActiveBatch();
    if (!batch) {
      this.showPendingFeature("当前没有可查看详情的批次。");
      return;
    }
    this.activeBatchName = batch.name;
    this.exportPinnedBatchName = batch.name;
    this.dataCheckBatchName = batch.name;
    this.drawerBatchName = batch.name;
    this.drawerTab = "overview";
    if (options.updateUrl !== false) {
      this.updateBatchUrl(this.batchUrlKey(batch), { view: "drawer" });
    }
    const $drawer = this.$root.find("[data-area='batch-drawer']");
    const $mask = this.$root.find("[data-area='batch-drawer-mask']");
    $drawer.attr("aria-hidden", "false").addClass("is-open");
    $mask.addClass("is-visible");
    this.renderBatchDrawerLoading();
    try {
      await this.loadBatchItems(batch.name, batch.current_version);
      await this.loadAuditLogs(batch.name, batch.current_version);
      await this.loadUsageLogs(batch.name);
      this.recordUsage("BATCH_VIEW", { batch, remark: "打开批次详情抽屉" });
      this.renderBatchDrawer();
    } catch (error) {
      this.renderBatchDrawerError(error);
      this.showError(error);
    }
  }

  closeBatchDrawer(options = {}) {
    const $drawer = this.$root.find("[data-area='batch-drawer']");
    const $mask = this.$root.find("[data-area='batch-drawer-mask']");
    $drawer.attr("aria-hidden", "true").removeClass("is-open");
    $mask.removeClass("is-visible");
    if (options.updateUrl !== false) {
      const batch = this.findBatch(this.focusedBatchName) || this.findBatch(this.drawerBatchName);
      this.updateBatchUrl(this.batchUrlKey(batch), { view: "" });
    }
  }

  switchBatchDrawerTab(tab = "overview") {
    const allowedTabs = new Set(["overview", "items", "audit", "usage", "allocation"]);
    this.drawerTab = allowedTabs.has(tab) ? tab : "overview";
    if (this.drawerTab === "usage" && this.drawerBatchName) {
      this.loadUsageLogs(this.drawerBatchName).catch((error) => this.showError(error));
    }
    this.renderBatchDrawer();
  }

  renderBatchDrawerLoading() {
    this.$root.find("[data-area='batch-drawer-body']").html(`
      <div class="ocw-batch-drawer-loading">
        <span class="spinner-border spinner-border-sm"></span>
        <span>${ocTranslate("正在加载批次详情...")}</span>
      </div>
    `);
  }

  renderBatchDrawerError(error) {
    this.$root.find("[data-area='batch-drawer-body']").html(`
      <div class="ocw-batch-drawer-empty">
        <strong>${ocTranslate("批次详情加载失败")}</strong>
        <span>${this.escape(error && error.message ? error.message : ocTranslate("请刷新后重试"))}</span>
      </div>
    `);
  }

  renderBatchDrawer() {
    const batch = this.findBatch(this.drawerBatchName);
    if (!batch) return;
    const items = this.batchItems[batch.name] || [];
    const $drawer = this.$root.find("[data-area='batch-drawer']");
    const title = batch.waybill_no || batch.customs_no || batch.batch_no || batch.name;
    $drawer.find("[data-area='batch-drawer-title']").text(title);
    $drawer.find("[data-action='switch-batch-drawer-tab']").each((_, node) => {
      $(node).toggleClass("active", $(node).attr("data-tab") === this.drawerTab);
    });
    $drawer.find("[data-area='batch-drawer-body']").html(this.renderBatchDrawerTab(batch, items));
  }

  renderBatchDrawerTab(batch, items) {
    if (this.drawerTab === "audit") {
      const auditHtml = this.auditEvents.length
        ? this.buildAuditSummaryEvents(this.auditEvents).map((event) => this.renderAuditEvent(event)).join("")
        : `<li class="ocw-audit-empty"><span class="ocw-audit-text">${ocTranslate("当前批次暂无修改记录")}</span></li>`;
      return `<ul class="ocw-audit-list ocw-batch-drawer-audit-list">${auditHtml}</ul>`;
    }
    if (this.drawerTab === "usage") {
      return this.renderUsageDrawer(batch);
    }
    if (this.drawerTab === "allocation") {
      return this.renderBatchDrawerAllocation(batch, items);
    }
    if (this.drawerTab === "profit") {
      return this.renderBatchDrawerProfit(batch, items);
    }
    if (this.drawerTab === "items") {
      return this.renderBatchDrawerItems(batch, items);
    }
    return this.renderBatchDrawerOverview(batch, items);
  }

  renderBatchDrawerOverview(batch, items) {
    const model = this.buildCostFlowPresentation(batch, items);
    const sourceStatus = model.sourceStatus;
    const logisticsTextSummary = sourceStatus.logistics_text_summary || {};
    const logisticsTextBrief = this.formatLogisticsTextSummary(logisticsTextSummary);
    const fields = [
      [ocTranslate("报关/来源单号"), batch.customs_no || batch.source_approval_no || batch.batch_no || "--"],
      [ocTranslate("运单/柜号"), batch.waybill_no || "--"],
      [ocTranslate("运输方式"), this.transportLabel(batch.transport_mode)],
      [ocTranslate("业务类型"), this.businessTypeLabel(batch.business_type)],
      [ocTranslate("状态"), model.statusInfo.label],
      [ocTranslate("物料行数"), model.itemCount],
      [ocTranslate("采购货值"), `${this.formatMoney(model.goodsValueRmb)} RMB`],
      [ocTranslate("综合成本"), `${this.formatMoney(model.totalCostRmb)} RMB`],
      [ocTranslate("采购审批"), this.purchaseApprovalStatusLabel(sourceStatus)],
      [ocTranslate("资料情况"), this.sourceStatusLabel(sourceStatus, batch)],
    ];
    const logisticsTextHtml = logisticsTextBrief
      ? `
        <div class="ocw-batch-drawer-note">
          <span>${ocTranslate("审批正文识别")}</span>
          <strong>${this.escape(logisticsTextBrief)}</strong>
        </div>
      `
      : "";
    const erpFlowHtml = this.renderErpFlowPanel(batch, items);
    return `
      <div class="ocw-batch-drawer-section ocw-batch-drawer-overview-brief">
        <div class="ocw-batch-drawer-field-grid">
          ${fields.map(([label, value]) => `<div><span>${this.escape(ocTranslate(label))}</span><strong>${this.escape(this.formatValue(value))}</strong></div>`).join("")}
        </div>
        ${this.renderPurchaseApprovalStatusAlert(sourceStatus)}
        ${logisticsTextHtml}
      </div>
      ${erpFlowHtml}
    `;
  }

  buildCostFlowPresentation(batch = {}, items = []) {
    const summary = batch.summary_snapshot || {};
    const sourceStatus = batch.source_status || {};
    const itemCount = items.length || Number(batch.item_count || 0);
    const goodsValueRmb = summary.calculation_schema === 2
      ? Number(summary.purchase_goods_value_rmb || 0)
      : items.length
        ? this.sumRowsNumber(items, "goods_value")
        : Number(batch.total_goods_value || summary.purchase_goods_value_rmb || 0);
    const totalCostRmb = summary.calculation_schema === 2
      ? Number(summary.total_cost_rmb || 0)
      : items.length
        ? this.sumRowsNumber(items, "total_cost_rmb")
        : Number(batch.actual_total_cost_rmb || batch.estimated_total_cost_rmb || summary.total_cost_rmb || 0);
    const hasFeeTotals = summary.direct_fees_rmb !== undefined || summary.allocated_fees_rmb !== undefined;
    const feeTotalRmb = summary.calculation_schema === 2 && hasFeeTotals
      ? Number(summary.direct_fees_rmb || 0) + Number(summary.allocated_fees_rmb || 0)
      : totalCostRmb - goodsValueRmb;
    const statusInfo = this.batchStatusInfo(batch.status, batch, itemCount);
    const confirmed = this.isCalculationConfirmed(batch);
    const writebackInfo = this.erpWritebackStatusInfo(batch);
    const writebackLower = String(batch.writeback_status || "").toLowerCase();
    const invalidBusiness = Boolean(sourceStatus.invalid_business);
    const hasVersion = this.hasText(batch.current_version);
    const hasCurrentTrial = hasVersion && !statusInfo.needsRecalculate && !batch.calculation_stale
      && (String(batch.status || "").toLowerCase().includes("calculated") || confirmed);
    const isCostReview = this.viewState?.task === "cost";
    const canConfirm = isCostReview && batch.review_state === "ready" && hasVersion && !statusInfo.needsRecalculate && !invalidBusiness;
    const flow = writebackLower.includes("success")
      ? { documents: "done", trial: "done", erp: "done" }
      : confirmed
        ? { documents: "done", trial: "done", erp: writebackLower.includes("fail") ? "error" : "current" }
        : hasCurrentTrial
          ? { documents: "done", trial: "current", erp: "pending" }
          : { documents: "current", trial: "pending", erp: "pending" };
    return {
      summary,
      sourceStatus,
      itemCount,
      goodsValueRmb,
      totalCostRmb,
      feeTotalRmb,
      statusInfo,
      confirmed,
      writebackInfo,
      invalidBusiness,
      hasVersion,
      hasCurrentTrial,
      isCostReview,
      canConfirm,
      canPreview: confirmed && !invalidBusiness,
      feeRows: this.buildCostBreakdownRows(batch),
      documents: this.buildDocumentChecklist(batch),
      flow,
    };
  }

  buildCostBreakdownRows(batch = {}) {
    const comprehensive = (batch.summary_snapshot || {}).comprehensive_cost || {};
    const groups = [
      ["included_fees", "included"],
      ["excluded_fees", "missing"],
      ["ignored_fees", "unused"],
    ];
    const seen = new Set();
    return groups.flatMap(([key, group]) => (Array.isArray(comprehensive[key]) ? comprehensive[key] : []).map((fee) => {
      const feeKey = String(fee.fee_key || fee.logical_fee_key || fee.rule_code || fee.expense_category || "").trim();
      if (!feeKey || seen.has(feeKey)) return null;
      seen.add(feeKey);
      const amountStatus = String(fee.amount_status || fee.status || "").toUpperCase();
      const reasonCode = String(fee.reason_code || "").toUpperCase();
      const state = group === "included"
        ? (amountStatus.includes("ESTIMAT") ? "estimated" : "included")
        : group === "unused" || reasonCode.includes("NOT_APPLICABLE")
          ? "unused"
          : "missing";
      return {
        key: feeKey,
        label: fee.expense_category || fee.fee_label || fee.label || feeKey,
        currency: String(fee.currency || "RMB").toUpperCase(),
        amount: fee.amount,
        amountRmb: fee.amount_rmb,
        state,
        stateLabel: { included: "已计入", estimated: "暂估", missing: "待补", unused: "未发生" }[state],
      };
    }).filter(Boolean));
  }

  buildDocumentChecklist(batch = {}) {
    const sourceStatus = batch.source_status || {};
    const allocationRules = Array.isArray(batch.allocation_rule_snapshot) ? batch.allocation_rule_snapshot : [];
    const reviewWarnings = Array.isArray(batch.review_warnings) ? batch.review_warnings : [];
    const logisticsCount = Number(sourceStatus.has_oa_logistics ? 1 : 0);
    const purchaseCount = Number(sourceStatus.linked_purchase_count || 0);
    const packingCount = Number(sourceStatus.packing_list_count || 0);
    const parsedPackingCount = Number(sourceStatus.parsed_packing_list_count || 0);
    const taxCount = Number(sourceStatus.tax_certificate_count || 0);
    const parsedTaxCount = Number(sourceStatus.parsed_tax_certificate_count || 0);
    const activeRules = allocationRules.filter((rule) => Number(rule.is_active ?? 1) !== 0 && Number(rule.is_enabled ?? 1) !== 0);
    const validEvidenceCount = activeRules.filter((rule) => String(rule.evidence_status || "").toUpperCase() === "VALID").length;
    const pendingEvidenceCount = activeRules.filter((rule) => ["PENDING", "UNLINKED", "INVALID"].includes(String(rule.evidence_status || "").toUpperCase())).length;
    const evidenceMissing = reviewWarnings.some((warning) => String(warning.code || "").toUpperCase() === "EVIDENCE_MISSING");
    const purchaseState = String(sourceStatus.purchase_approval_sync_state || "").toLowerCase();
    const hasTaxFee = this.buildCostBreakdownRows(batch).some((fee) => /import[_ -]?tax|进口税|关税/i.test(`${fee.key} ${fee.label}`) && fee.state !== "unused");
    const parsedDocumentState = (count, parsedCount) => parsedCount ? "ready" : count ? "pending" : "missing";
    const purchaseDocumentState = purchaseState === "valid" && purchaseCount
      ? "ready"
      : ["invalid", "excluded"].includes(purchaseState)
        ? "unused"
        : purchaseCount || purchaseState === "pending" ? "pending" : "missing";
    const feeEvidenceState = evidenceMissing && !activeRules.length
      ? "missing"
      : !activeRules.length
        ? "unused"
        : pendingEvidenceCount || evidenceMissing
          ? "pending"
          : validEvidenceCount === activeRules.length ? "ready" : "missing";
    const taxDocumentState = taxCount ? parsedDocumentState(taxCount, parsedTaxCount) : hasTaxFee ? "missing" : "unused";
    const document = (key, label, tab, state, count, detail) => ({
      key,
      label,
      tab,
      state,
      count,
      detail,
      stateLabel: { ready: "已具备", pending: "待解析或待确认", missing: "待补", unused: "不适用" }[state],
    });
    return [
      document("logistics", "国际物流审批", "dingtalk", logisticsCount ? "ready" : "missing", logisticsCount, logisticsCount ? "已读取国际物流审批" : "尚未关联国际物流审批"),
      document("purchase", "采购审批", "dingtalk", purchaseDocumentState, purchaseCount, this.purchaseApprovalStatusLabel(sourceStatus)),
      document("packing", "装箱资料", "documents", parsedDocumentState(packingCount, parsedPackingCount), packingCount, parsedPackingCount ? ocTranslate("{0} 份已解析", [parsedPackingCount]) : packingCount ? ocTranslate("{0} 份待解析", [packingCount]) : "尚未补充装箱资料"),
      document("fee_evidence", "费用凭证", "documents", feeEvidenceState, validEvidenceCount, evidenceMissing && !activeRules.length ? "费用凭证尚未补齐" : !activeRules.length ? "当前无需凭证的费用" : ocTranslate("{0}/{1} 项已确认", [validEvidenceCount, activeRules.length])),
      document("tax", "完税凭证", "vouchers", taxDocumentState, taxCount, parsedTaxCount ? ocTranslate("{0} 份已解析", [parsedTaxCount]) : taxCount ? ocTranslate("{0} 份待解析", [taxCount]) : hasTaxFee ? "尚未补充完税凭证" : "当前无完税资料要求"),
    ];
  }

  renderBatchDrawerItems(batch, items) {
    const rows = (items || []).map((item, index) => {
      const adoptedPrice = item.adopted_price || {};
      const priceSource = adoptedPrice.source_type === "purchase_total_derived"
        ? `<small class="ocw-result-source">${ocTranslate("按货值÷采购数量计算")}</small>`
        : "";
      return `
      <tr>
        <td>${this.escape(String(index + 1))}</td>
        <td>${this.escape(this.formatValue(item.material_code || "--"))}</td>
        <td>${this.escape(this.formatValue(item.product_name || item.product_name_es || "--"))}</td>
        <td>${this.escape(this.formatValue(item.quantity))}</td>
        <td>${this.escape(this.formatMoney(item.adopted_price?.value ?? item.unit_price))}${priceSource}</td>
        <td>${this.escape(this.formatValue(item.adopted_price?.currency || item.purchase_currency || "--"))}</td>
        <td>${this.escape(this.formatMoney(item.total_unit_rmb || "--"))}</td>
      </tr>
    `;
    }).join("");
    return `
      <div class="ocw-batch-drawer-section">
        <div class="ocw-batch-drawer-section-head"><h4>${ocTranslate("物料明细")}</h4><span>${ocTranslate("{0} 行，完整展示", [items.length])}</span></div>
        ${rows ? `
          <div class="ocw-batch-drawer-table-wrap"><table class="ocw-batch-drawer-table"><thead><tr><th>#</th><th>${ocTranslate("物料编码")}</th><th>${ocTranslate("物料名称")}</th><th>${ocTranslate("采购数量")}</th><th>${ocTranslate("采购单价")}</th><th>${ocTranslate("采购币种")}</th><th>${ocTranslate("综合单价 RMB")}</th></tr></thead><tbody>${rows}</tbody></table></div>
        ` : `<div class="ocw-batch-drawer-empty"><span>${ocTranslate("当前批次暂无物料明细")}</span></div>`}
      </div>
    `;
  }

  profitDefaultFx(currency, item = {}) {
    const code = String(currency || item.sales_currency || "RMB").trim().toUpperCase();
    const saved = Number(item.sales_fx_rate);
    if (Number.isFinite(saved) && saved > 0) return saved;
    return code === "RMB" ? 1 : 0;
  }

  calculateProfitPreviewRow(item, values = {}) {
    const quantity = Number(values.sales_quantity ?? item.sales_quantity ?? 0) || 0;
    const unitPrice = Number(values.sales_unit_price ?? item.sales_unit_price ?? 0) || 0;
    const currency = String(values.sales_currency ?? item.sales_currency ?? "RMB").trim().toUpperCase() || "RMB";
    const fxRate = Number(values.sales_fx_rate ?? this.profitDefaultFx(currency, item)) || 0;
    const otherExpense = Number(values.other_sales_expense_rmb ?? item.other_sales_expense_rmb ?? 0) || 0;
    const costUnit = Number(item.total_unit_rmb) || 0;
    const salesAmount = quantity * unitPrice;
    const salesAmountRmb = salesAmount * fxRate;
    const salesCost = quantity * costUnit;
    const grossProfit = salesAmountRmb - salesCost;
    const profit = grossProfit - otherExpense;
    const margin = salesAmountRmb ? (profit / salesAmountRmb) * 100 : 0;
    const missing = [];
    if (quantity <= 0) missing.push("销售数量");
    if (unitPrice <= 0) missing.push("销售单价");
    if (fxRate <= 0) missing.push("销售汇率");
    if (costUnit <= 0) missing.push("综合成本单价");
    return {
      sales_quantity: quantity,
      sales_unit_price: unitPrice,
      sales_currency: currency,
      sales_fx_rate: fxRate,
      sales_amount: salesAmount,
      sales_amount_rmb: salesAmountRmb,
      sales_cost_rmb: salesCost,
      other_sales_expense_rmb: otherExpense,
      gross_profit_rmb: grossProfit,
      profit_rmb: profit,
      profit_margin: margin,
      profit_status: missing.length ? "PENDING" : "CALCULATED",
      profit_status_label: missing.length ? "待补销售数据" : "已测算",
      profit_missing_fields: missing,
    };
  }

  calculateProfitPreview(items, valuesByItem = {}) {
    const rows = (items || []).map((item) => this.calculateProfitPreviewRow(item, valuesByItem[item.name] || {}));
    const complete = rows.filter((row) => row.profit_status === "CALCULATED");
    const salesAmount = this.sumRowsNumber(complete, "sales_amount_rmb");
    const profit = this.sumRowsNumber(complete, "profit_rmb");
    return {
      item_count: rows.length,
      calculated_count: complete.length,
      pending_count: rows.length - complete.length,
      sales_amount_rmb: salesAmount,
      sales_cost_rmb: this.sumRowsNumber(complete, "sales_cost_rmb"),
      gross_profit_rmb: this.sumRowsNumber(complete, "gross_profit_rmb"),
      other_sales_expense_rmb: this.sumRowsNumber(complete, "other_sales_expense_rmb"),
      profit_rmb: profit,
      profit_margin: salesAmount ? (profit / salesAmount) * 100 : 0,
      rows,
    };
  }

  renderBatchDrawerProfit(batch, items, valuesByItem = {}) {
    const preview = this.calculateProfitPreview(items, valuesByItem);
    const summaryCards = [
      ["销售金额 RMB", this.formatMoney(preview.sales_amount_rmb)],
      ["综合成本 RMB", this.formatMoney(preview.sales_cost_rmb)],
      ["毛利 RMB", this.formatMoney(preview.gross_profit_rmb)],
      ["其他销售费用 RMB", this.formatMoney(preview.other_sales_expense_rmb)],
      ["利润 RMB", this.formatMoney(preview.profit_rmb)],
      ["利润率", preview.calculated_count ? `${this.formatNumber(preview.profit_margin)}%` : "--"],
    ]
      .map(([label, value]) => `<div class="ocw-profit-summary-card"><span>${ocTranslate(label)}</span><strong>${this.escape(value)}</strong></div>`)
      .join("");
    const rows = (items || []).map((item, index) => {
      const values = valuesByItem[item.name] || {};
      const row = this.calculateProfitPreviewRow(item, values);
      const savedCurrency = values.sales_currency ?? item.sales_currency ?? "RMB";
      const savedFx = values.sales_fx_rate ?? item.sales_fx_rate ?? (savedCurrency === "RMB" ? 1 : "");
      const input = (field, value) => `<input class="ocw-profit-input" data-profit-input="${field}" type="number" value="${this.escape(value === null || value === undefined ? "" : String(value))}" />`;
      return `
        <tr data-profit-row data-item-name="${this.escape(item.name || "")}">
          <td>${this.escape(String(index + 1))}</td>
          <td><strong>${this.escape(item.material_code || "--")}</strong><small>${this.escape(item.product_name || item.product_name_es || "")}</small></td>
          <td>${input("sales_quantity", values.sales_quantity ?? item.sales_quantity ?? "")}</td>
          <td>${input("sales_unit_price", values.sales_unit_price ?? item.sales_unit_price ?? "")}</td>
          <td><select class="ocw-profit-input" data-profit-input="sales_currency"><option value="RMB"${savedCurrency === "RMB" ? " selected" : ""}>RMB</option><option value="USD"${savedCurrency === "USD" ? " selected" : ""}>USD</option><option value="MXN"${savedCurrency === "MXN" ? " selected" : ""}>MXN</option></select></td>
          <td>${input("sales_fx_rate", savedFx)}</td>
          <td>${input("other_sales_expense_rmb", values.other_sales_expense_rmb ?? item.other_sales_expense_rmb ?? "")}</td>
          <td data-profit-result="sales_amount_rmb">${row.profit_status === "CALCULATED" ? this.formatMoney(row.sales_amount_rmb) : "--"}</td>
          <td data-profit-result="sales_cost_rmb">${row.profit_status === "CALCULATED" ? this.formatMoney(row.sales_cost_rmb) : "--"}</td>
          <td data-profit-result="gross_profit_rmb">${row.profit_status === "CALCULATED" ? this.formatMoney(row.gross_profit_rmb) : "--"}</td>
          <td data-profit-result="profit_rmb">${row.profit_status === "CALCULATED" ? this.formatMoney(row.profit_rmb) : "--"}</td>
          <td data-profit-result="profit_margin">${row.profit_status === "CALCULATED" ? `${this.formatNumber(row.profit_margin)}%` : "--"}</td>
          <td data-profit-result="profit_status" class="${row.profit_status === "CALCULATED" ? "is-ok" : "is-pending"}">${row.profit_status_label}</td>
        </tr>
      `;
    }).join("");
    return `
      <div class="ocw-batch-drawer-section ocw-profit-panel">
        <div class="ocw-batch-drawer-section-head"><h4>${ocTranslate("利润测算")}</h4><span>${ocTranslate("销售数据人工录入，结果自动计算")}</span></div>
        <div class="ocw-profit-note">${ocTranslate("综合成本取当前批次的综合单价；物流、清关、关税等已包含在综合成本中的费用不会重复扣除。汇率填写“1销售币种 = 多少人民币”。")}</div>
        <div class="ocw-profit-summary">${summaryCards}</div>
        <div class="ocw-profit-status-line">${ocTranslate("已测算 {0} 行，待补销售数据 {1} 行。汇总只统计已完成的明细。", [preview.calculated_count, preview.pending_count])}</div>
        <div class="ocw-batch-drawer-table-wrap ocw-profit-table-wrap">
          <table class="ocw-batch-drawer-table ocw-profit-table"><thead><tr><th>#</th><th>SKU</th><th>${ocTranslate("销售数量")}</th><th>${ocTranslate("销售单价")}</th><th>${ocTranslate("币种")}</th><th>${ocTranslate("汇率")}</th><th>${ocTranslate("其他销售费用 RMB")}</th><th>${ocTranslate("销售金额 RMB")}</th><th>${ocTranslate("综合成本 RMB")}</th><th>${ocTranslate("毛利 RMB")}</th><th>${ocTranslate("利润 RMB")}</th><th>${ocTranslate("利润率")}</th><th>${ocTranslate("状态")}</th></tr></thead><tbody>${rows || `<tr><td colspan="13">${ocTranslate("当前批次暂无物料明细")}</td></tr>`}</tbody></table>
        </div>
        <div class="ocw-profit-actions"><button class="ocw-primary-btn ocw-mini-btn" type="button" data-action="save-profit-inputs"${items.length ? "" : " disabled"}>${ocTranslate("保存并测算")}</button></div>
      </div>
    `;
  }

  readProfitDrawerValues() {
    const values = {};
    this.$root.find("[data-profit-row]").each((_, row) => {
      const $row = $(row);
      const itemName = $row.attr("data-item-name");
      if (!itemName) return;
      values[itemName] = {};
      $row.find("[data-profit-input]").each((__, input) => {
        values[itemName][$(input).attr("data-profit-input")] = $(input).val();
      });
    });
    return values;
  }

  updateProfitDrawerPreview() {
    if (this.drawerTab !== "profit") return;
    const batch = this.findBatch(this.drawerBatchName);
    if (!batch) return;
    const valuesByItem = this.readProfitDrawerValues();
    const preview = this.calculateProfitPreview(this.batchItems[batch.name] || [], valuesByItem);
    preview.rows.forEach((row, index) => {
      const $row = this.$root.find("[data-profit-row]").eq(index);
      ["sales_amount_rmb", "sales_cost_rmb", "gross_profit_rmb", "profit_rmb"].forEach((field) => {
        $row.find(`[data-profit-result="${field}"]`).text(row.profit_status === "CALCULATED" ? this.formatMoney(row[field]) : "--");
      });
      $row.find('[data-profit-result="profit_margin"]').text(row.profit_status === "CALCULATED" ? `${this.formatNumber(row.profit_margin)}%` : "--");
      $row.find('[data-profit-result="profit_status"]').text(row.profit_status_label).toggleClass("is-ok", row.profit_status === "CALCULATED").toggleClass("is-pending", row.profit_status !== "CALCULATED");
    });
    const $summary = this.$root.find(".ocw-profit-summary");
    const values = [preview.sales_amount_rmb, preview.sales_cost_rmb, preview.gross_profit_rmb, preview.other_sales_expense_rmb, preview.profit_rmb, preview.calculated_count ? `${this.formatNumber(preview.profit_margin)}%` : "--"];
    $summary.find(".ocw-profit-summary-card strong").each((index, node) => $(node).text(index < 5 ? this.formatMoney(values[index]) : values[index]));
    this.$root.find(".ocw-profit-status-line").text(ocTranslate("已测算 {0} 行，待补销售数据 {1} 行。汇总只统计已完成的明细。", [preview.calculated_count, preview.pending_count]));
  }

  async saveProfitInputs() {
    const batch = this.findBatch(this.drawerBatchName);
    if (!batch) return;
    const valuesByItem = this.readProfitDrawerValues();
    const rows = Object.entries(valuesByItem).map(([item_name, values]) => ({ item_name, ...values }));
    const $button = this.$root.find("[data-action='save-profit-inputs']").prop("disabled", true).text(ocTranslate("保存中"));
    try {
      const result = await this.call("overseas_costing.api.profit.save_profit_inputs", {
        batch_name: batch.name,
        version_name: batch.current_version || null,
        rows_payload: JSON.stringify(rows),
      }, true);
      if (!result || !result.ok) throw new Error((result && result.message) || "利润测算保存失败");
      await this.loadBatchItems(batch.name, batch.current_version, true);
      await this.loadAuditLogs(batch.name, batch.current_version);
      this.renderTable();
      this.renderBatchDrawer();
      this.recordUsage("EDIT", { batch, remark: "保存利润测算销售数据" });
      frappe.show_alert({ message: result.message || ocTranslate("利润测算已保存"), indicator: "green" });
    } catch (error) {
      this.showError(error);
      $button.prop("disabled", false).text(ocTranslate("保存并测算"));
    }
  }

  isCalculationConfirmed(batch = {}) {
    return String(batch.confirm_status ?? "").trim().toLowerCase() === "confirmed";
  }

  erpFlowBlockReason(result = {}) {
    const message = String(result.message || "").trim();
    if (message) return message;
    const blockingReasons = Array.isArray(result.blocking_reasons)
      ? result.blocking_reasons.map((reason) => String(reason || "").trim()).filter(Boolean)
      : [];
    return blockingReasons.join("；") || "ERP 推送条件未满足。";
  }

  activeErpPushBlock(batch = {}) {
    const state = this.erpPushBlockState;
    const versionName = String(batch.current_version || "");
    if (!state || state.batchName !== batch.name || String(state.versionName || "") !== versionName) return null;
    return state;
  }

  erpPushActionState(batch = {}, itemCount = null) {
    const count = itemCount === null ? Number(batch.item_count || 0) : Number(itemCount || 0);
    const statusInfo = this.batchStatusInfo(batch.status, batch, count);
    const statusLower = String(batch.status || "").toLowerCase();
    const writebackLower = String(batch.writeback_status || "Not Started").toLowerCase();
    const confirmed = this.isCalculationConfirmed(batch);
    const sourceStatus = batch.source_status || {};
    const resultNeedsRecalculate = Boolean(statusInfo.needsRecalculate || batch.calculation_stale);
    const hasTrial = statusLower.includes("calculated") || confirmed;

    if (writebackLower.includes("success")) {
      if (this.erpPushBlockState?.batchName === batch.name) this.erpPushBlockState = null;
      return {
        label: ocTranslate("ERP 已推送"),
        enabled: false,
        reason: batch.writeback_message || "已成功推送到 ERP，无需重复操作。",
      };
    }
    if (sourceStatus.invalid_business) {
      return {
        label: ocTranslate("推送 ERP"),
        enabled: false,
        reason:
          sourceStatus.invalid_business_reason ||
          sourceStatus.purchase_approval_sync_message ||
          "关联采购审批已拒绝、撤销或终止，不允许推送 ERP。",
      };
    }
    if (!this.hasText(batch.current_version) || !hasTrial || resultNeedsRecalculate) {
      return { label: ocTranslate("推送 ERP"), enabled: false, reason: "请先完成试算。" };
    }
    if (!confirmed) {
      return {
        label: ocTranslate("推送 ERP"),
        enabled: false,
        reason: this.viewState?.task === "cost" ? "请先校验计算结果。" : "请先在成本核对中完成人工确认。",
      };
    }
    const pushBlock = this.activeErpPushBlock(batch);
    if (pushBlock) {
      return { label: ocTranslate("重试 ERP"), enabled: true, reason: this.erpFlowBlockReason(pushBlock) };
    }
    if (writebackLower.includes("fail")) {
      return { label: ocTranslate("重试 ERP"), enabled: true, reason: batch.writeback_message || "上次推送失败，可以重试 ERP。" };
    }
    return { label: ocTranslate("推送 ERP"), enabled: true, reason: "已完成试算和人工校验，可以推送 ERP。" };
  }

  renderErpFlowPanel(batch, items) {
    const model = this.buildCostFlowPresentation(batch, items);
    const {
      totalCostRmb: totalCost,
      statusInfo,
      confirmed,
      writebackInfo,
      invalidBusiness,
      isCostReview,
      canConfirm,
      canPreview,
    } = model;
    const note = invalidBusiness
      ? "关联采购审批已拒绝、撤销或终止，当前批次保留用于追溯，但不会进入成本确认或 ERP 推送。"
      : confirmed
      ? "计算结果已通过人工校验，ERP 报文可预览后进入待推送队列。"
      : statusInfo.needsRecalculate
        ? "明细或费用池变更后需要重新试算，再进行人工校验。"
        : "核对费用池、分摊依据和明细结果后，执行人工校验。";
    const checkpoints = [
      { label: ocTranslate("综合成本"), value: this.isPositive(totalCost) ? `${this.formatMoney(totalCost)} RMB` : "待试算", state: this.isPositive(totalCost) ? "is-ok" : "is-warn" },
      { label: ocTranslate("确认状态"), value: confirmed ? "已确认" : "待确认", state: confirmed ? "is-ok" : "is-warn" },
      { label: ocTranslate("回写状态"), value: writebackInfo.label, state: writebackInfo.state },
      { label: ocTranslate("业务主体"), value: batch.subsidiary_code || "--", state: this.hasText(batch.subsidiary_code) ? "is-ok" : "is-warn" },
    ];
    const blockHtml = this.renderErpFlowBlockInline(batch);
    return `
      <div class="ocw-batch-drawer-section">
        <div class="ocw-erp-flow-panel">
          <div class="ocw-erp-flow-head">
            <h4>${isCostReview ? ocTranslate("校验计算结果") : ocTranslate("成本与 ERP 状态")}</h4>
            <span>${this.escape(statusInfo.label || "")}</span>
          </div>
          <div class="ocw-erp-flow-grid">
            ${checkpoints
              .map(
                (item) => `
                  <div class="${this.escape(item.state)}">
                    <span>${this.escape(ocTranslate(item.label))}</span>
                    <strong>${this.escape(this.formatValue(item.value))}</strong>
                  </div>
                `
              )
              .join("")}
          </div>
          <div class="ocw-erp-flow-actions">
            ${isCostReview ? `<button class="ocw-primary-btn ocw-mini-btn" data-action="confirm-calculation-result"${canConfirm ? "" : " disabled"}>${ocTranslate("校验计算结果")}</button>` : ""}
            <button class="ocw-outline-btn ocw-mini-btn" data-action="preview-erp-payload"${canPreview ? "" : " disabled"}>${ocTranslate("预览 ERP 报文")}</button>
          </div>
          <div class="ocw-erp-flow-note">${this.escape(note)}</div>
        </div>
        ${blockHtml}
      </div>
    `;
  }

  renderBatchDrawerAllocation(batch, items) {
    const rows = this.buildAiReviewRows(batch, items);
    return `
      <div class="ocw-batch-drawer-section">
        <div class="ocw-batch-drawer-section-head"><h4>${ocTranslate("AI 分摊填入")}</h4><span>${ocTranslate("基础金额可在明细中人工调整")}</span></div>
        <div class="ocw-diff-table ocw-batch-drawer-diff-table">
          <div class="ocw-diff-head"><span>${ocTranslate("复核项")}</span><span>${ocTranslate("当前状态")}</span><span>${ocTranslate("说明")}</span></div>
          ${rows.map((row) => `<div><span>${this.escape(ocTranslate(row.label))}</span><b class="${this.escape(row.statusClass)}">${this.escape(row.status)}</b><strong>${this.escape(row.suggestion)}</strong></div>`).join("")}
        </div>
      </div>
    `;
  }

  erpWritebackStatusInfo(batch = {}) {
    const writebackValue = String(batch.writeback_status || "Not Started");
    const writebackLower = writebackValue.toLowerCase();
    if (writebackLower.includes("success")) {
      return { label: ocTranslate("已完成"), state: "is-ok", note: batch.writeback_message || ocTranslate("DeepLinkERP 已返回成功。") };
    }
    if (writebackLower.includes("pending")) {
      return { label: ocTranslate("待接口推送"), state: "is-info", note: batch.writeback_message || ocTranslate("已生成报文，当前等待正式接口配置后推送。") };
    }
    if (writebackLower.includes("fail")) {
      return { label: ocTranslate("失败可重试"), state: "is-warn", note: batch.writeback_message || ocTranslate("上次推送失败，后续可重试。") };
    }
    return { label: ocTranslate("未开始"), state: "is-info", note: batch.writeback_message || ocTranslate("当前还没有生成 DeepLinkERP 待推送报文。") };
  }

  sourceStatusLabel(sourceStatus, batch) {
    if (sourceStatus.has_oa_logistics) return ocTranslate("资料来自国际物流审批");
    if (sourceStatus.invalid_business) return ocTranslate("采购审批无效");
    if (Number(sourceStatus.oa_attachment_count || batch.source_attachment_count || 0) > 0) return ocTranslate("已有关联资料");
    if (batch.source_approval_no || batch.source_instance_id || batch.source_dingtalk_url) return ocTranslate("已关联钉钉审批单");
    return ocTranslate("待补资料");
  }

  purchaseApprovalStatusLabel(sourceStatus = {}) {
    const state = String(sourceStatus.purchase_approval_sync_state || "").trim().toLowerCase();
    const count = Number(sourceStatus.linked_purchase_count || 0);
    const statuses = Array.isArray(sourceStatus.linked_purchase_approval_statuses)
      ? sourceStatus.linked_purchase_approval_statuses.filter(Boolean)
      : [];
    if ((state === "invalid" || state === "excluded" || sourceStatus.invalid_business) && sourceStatus.has_oa_logistics) return ocTranslate("关联采购审批已排除");
    if (state === "invalid" || sourceStatus.invalid_business) return ocTranslate("采购审批无效");
    if (state === "pending") return count ? ocTranslate("{0} 条状态未同步", [count]) : ocTranslate("状态未同步");
    if (state === "missing") return ocTranslate("未关联采购审批");
    if (state === "valid") return statuses.length ? ocTranslate("{0} 条：{1}", [count, statuses.join("/")]) : ocTranslate("{0} 条已同步", [count]);
    return "--";
  }

  downloadBase64File(contentBase64, fileName, mimeType) {
    const binary = atob(contentBase64 || "");
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    const blob = new Blob([bytes], { type: mimeType || "application/octet-stream" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName || "海外采购综合成本核算.xlsx";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }
