  async refreshAll() {
    await this.loadBatches();
  }

  async recalculate(batchName = "") {
    const batch = batchName ? this.findBatch(batchName) : this.getActiveBatch();
    if (!batch) return;
    const sourceStatus = batch.source_status || {};
    if (sourceStatus.invalid_business) {
      frappe.show_alert({
        message: sourceStatus.invalid_business_reason || ocTranslate("当前批次包含已拒绝、撤销或终止的审批，不能重新计算。"),
        indicator: "red",
      });
      return;
    }
    this.activeBatchName = batch.name;
    this.exportPinnedBatchName = batch.name;
    let acquired = null;
    const inDetail = this.detailState?.batchName === batch.name;
    const feeState = inDetail && this.materialFeeState?.batchName === batch.name ? this.materialFeeState : null;
    if (feeState && this.isMaterialFeeCalculationBusy(feeState)) return;
    if (feeState) {
      feeState.previewRunning = true;
      this.updateMaterialFeeWriteControls(feeState);
    }
    try {
      if (feeState && !(await this.flushMaterialFeeInputs(feeState))) return;
      if (inDetail) {
        if (!(await this.ensureEditSession())) return;
      } else {
        acquired = await this.call("overseas_costing.api.edit_session.acquire", { batch_name: batch.name });
        if (!acquired?.ok) throw new Error(acquired?.message || "无法获取编辑权。");
      }
      const result = await this.call(
        "overseas_costing.api.calculate.recalculate_batch",
        {
          batch_name: batch.name,
          version_name: batch.current_version,
          edit_token: inDetail ? this.detailState.editToken : acquired.edit_token,
          expected_modified: inDetail ? this.detailState.expectedModified : acquired.modified,
        },
        true
      );
      if (!result?.ok) {
        throw new Error(result?.message || "重新试算被服务器拒绝，未修改批次数据。");
      }
      const summary = result.summary_snapshot || {};
      if (result.saved) this.acceptSavedComprehensiveCost(result, batch.name);
      this.applyRecalculateSummary(batch.name, summary, result.allocation_rules || []);
      this.lastRecalculateResult = { batch_name: batch.name, summary };
      if (this.resetBatchResultPreview) {
        this.resetBatchResultPreview({ clearCache: true, render: false });
      }
      let refreshWarning = "";
      try {
        if (result.saved && this.detailState?.batchName === batch.name && this.$root.attr("data-screen") === "detail") {
          await this.refreshRecalculatedDetailClassification(batch.name);
        } else if (this.detailState?.batchName === batch.name && this.$root.attr("data-screen") === "detail") {
          await this.refreshDetailSummary();
        } else if (this.viewState?.screen === "workbench") {
          await this.loadBatches();
        } else {
          await this.loadBatchItems(batch.name, batch.current_version, true);
          await this.loadAuditLogs(batch.name, batch.current_version);
          this.renderTable();
          this.renderRecalculateResult(batch.name, summary);
          if (this.renderWorkbenchBatchList) this.renderWorkbenchBatchList();
        }
      } catch (refreshError) {
        if (!result.saved) throw refreshError;
        refreshWarning = refreshError?.message || "工作台分类刷新失败";
        if (this.detailState?.batchName === batch.name && this.$root.attr("data-screen") === "detail") {
          try {
            await this.refreshDetailSummary();
          } catch (_fallbackError) {
            // 试算结果已经原子保存；详情回读失败不能把已保存的试算误报为失败。
          }
        }
      }
      this.recordUsage("RECALCULATE", { batch, remark: "重新试算批次成本" });
      frappe.show_alert({
        message: refreshWarning
          ? ocTranslate("试算已保存，但工作台分类刷新失败：{0}。已保留当前详情，请稍后刷新。", [refreshWarning])
          : result.message || ocTranslate("重新试算完成"),
        indicator: refreshWarning ? "orange" : "green",
      });
    } catch (error) {
      this.recordUsage("RECALCULATE", { batch, status: "Failed", remark: error.message || "重新试算失败" });
      this.showError(error);
    } finally {
      if (feeState) {
        feeState.previewRunning = false;
        this.updateMaterialFeeWriteControls(feeState);
      }
      if (acquired?.edit_token) await this.call("overseas_costing.api.edit_session.release", { batch_name: batch.name, edit_token: acquired.edit_token });
    }
  }

  async getAuthoritativeReviewClassification(batchName) {
    const current = this.findBatch(batchName) || this.detailState?.header || {};
    const keyword = current.batch_no || current.source_approval_no || current.customs_no || current.waybill_no || batchName;
    const pageLength = 100;
    const scopes = [
      { task: "cost", reviewStatus: "pending", classification: "cost" },
      { task: "pending", reviewStatus: "pending", classification: "pending" },
      { task: "erp", reviewStatus: "pending", classification: "erp" },
      { task: "cost", reviewStatus: "confirmed", classification: "confirmed" },
    ];
    for (const scope of scopes) {
      const filtersJson = JSON.stringify({ keyword, review_status: scope.reviewStatus, include_history: 1 });
      for (let page = 1; ; page += 1) {
        const result = await this.call("overseas_costing.api.workbench.get_batches", {
          filters_json: filtersJson,
          task: scope.task,
          page,
          page_length: pageLength,
        });
        if (!result?.ok) throw new Error(result?.message || "试算后工作台分类刷新失败");
        const items = result.items || [];
        const batch = items.find((row) => row.name === batchName);
        if (batch) return { task: scope.classification, reviewStatus: scope.reviewStatus, batch };
        const total = Number(result.total || 0);
        if (!items.length || (total > 0 ? page * pageLength >= total : items.length < pageLength)) break;
      }
    }
    return { task: "unknown", reviewStatus: "", batch: null };
  }

  captureReviewNavigationContext(batchName) {
    return {
      batchName,
      task: this.viewState?.task || "pending",
      screen: this.viewState?.screen || this.$root?.attr?.("data-screen") || "workbench",
      viewBatch: this.viewState?.batch || "",
      detailBatch: this.detailState?.batchName || "",
      drawerBatch: this.drawerBatchName || "",
      detailRequestId: this.detailState?.requestId,
    };
  }

  reviewNavigationContextMatches(context) {
    if (!context || this.viewState?.task !== context.task) return false;
    const screen = this.viewState?.screen || this.$root?.attr?.("data-screen") || "workbench";
    if (screen !== context.screen) return false;
    if (context.screen === "detail") {
      if (this.detailState?.batchName !== context.batchName) return false;
      if (this.viewState?.batch && this.viewState.batch !== context.batchName) return false;
      if (this.detailState?.requestId !== context.detailRequestId) return false;
    } else if (context.drawerBatch && this.drawerBatchName !== context.drawerBatch) {
      return false;
    }
    return true;
  }

  reviewNavigationTargetMatches(target) {
    if (!target || this.viewState?.task !== target.task) return false;
    if ((this.filters?.review_status || "pending") !== target.reviewStatus) return false;
    const screen = this.viewState?.screen || this.$root?.attr?.("data-screen") || "workbench";
    if (screen !== target.screen) return false;
    if (target.screen === "detail") {
      if (this.detailState?.batchName !== target.batchName) return false;
      if (this.viewState?.batch !== target.batchName) return false;
      if ((this.detailState?.tab || this.viewState?.tab || "overview") !== target.tab) return false;
    }
    return true;
  }

  async applyAuthoritativeReviewClassification(batchName, authoritative, context, options = {}) {
    if (context && !this.reviewNavigationContextMatches(context)) return false;
    if (!authoritative || authoritative.task === "unknown") return false;
    let task = "pending";
    let reviewStatus = "pending";
    if (authoritative.task === "cost") task = "cost";
    else if (authoritative.task === "erp") task = "erp";
    else if (authoritative.task === "confirmed") {
      task = "cost";
      reviewStatus = "confirmed";
    }
    const keepDetailOpen = context?.screen === "detail" && this.detailState?.batchName === batchName;
    const tab = this.detailState?.tab || this.viewState?.tab || "overview";
    if (authoritative?.batch && keepDetailOpen) {
      this.detailState.header = { ...(this.detailState.header || {}), ...authoritative.batch };
    }
    this.viewState.task = task;
    this.viewState.page = 1;
    this.filters.review_status = reviewStatus;
    this.filters.review_warning = "";
    this.filters.issue = "";
    this.replaceViewState({
      task,
      review_status: reviewStatus,
      review_warning: "",
      issue: "",
      page: 1,
      screen: keepDetailOpen ? "detail" : "workbench",
      batch: keepDetailOpen ? batchName : "",
      tab: keepDetailOpen ? tab : "overview",
    });
    const target = {
      task,
      reviewStatus,
      screen: keepDetailOpen ? "detail" : "workbench",
      batchName: keepDetailOpen ? batchName : "",
      tab: keepDetailOpen ? tab : "overview",
    };
    if (options.reload !== false) {
      await this.loadBatches();
      if (!this.reviewNavigationTargetMatches(target)) return false;
      // loadBatches 会按权威列表重开详情；不再用 await 前的分类快照二次覆盖。
      return true;
    }
    if (authoritative?.batch && keepDetailOpen && this.detailState?.batchName === batchName) {
      const merged = { ...(this.detailState.header || {}), ...authoritative.batch };
      const index = this.batches.findIndex((row) => row.name === batchName);
      if (index >= 0) this.batches[index] = { ...this.batches[index], ...authoritative.batch };
      this.detailState.header = merged;
      if (options.render !== false) {
        this.renderDetailShell?.();
        if (this.switchDetailTab) await this.switchDetailTab(tab, { updateUrl: false });
      }
    }
    return true;
  }

  async refreshRecalculatedDetailClassification(batchName) {
    const context = this.captureReviewNavigationContext(batchName);
    const authoritative = await this.getAuthoritativeReviewClassification(batchName);
    if (authoritative.task === "unknown") {
      if (!this.reviewNavigationContextMatches(context)) return false;
      await this.refreshDetailSummary();
      return this.reviewNavigationContextMatches(context);
    }
    return this.applyAuthoritativeReviewClassification(batchName, authoritative, context);
  }

  async refreshSavedTrialReviewClassification(batchName) {
    return this.refreshRecalculatedDetailClassification(batchName);
  }

  setMainView(view = "cost") {
    this.erpQueueMode = view === "erp_queue";
    if (this.erpQueueMode) {
      this.focusedBatchName = "";
      this.closeBatchDrawer({ updateUrl: false });
    }
    this.updateMainViewSwitch();
    this.renderTable();
  }

  setWorkRole(role = "purchase") {
    const nextRole = role === "finance" ? "finance" : "purchase";
    if (this.workRole === nextRole && !this.erpQueueMode) return;
    this.workRole = nextRole;
    this.erpQueueMode = false;
    this.focusedBatchName = "";
    this.closeBatchDrawer({ updateUrl: false });
    this.syncActiveSelectionWithVisible();
    this.renderTable();
    this.updateSearchResult();
  }

  setErpQueueStatus(status = "all") {
    const allowed = new Set(["all", "not_started", "pending", "success", "failed"]);
    this.erpQueueStatus = allowed.has(status) ? status : "all";
    this.renderTable();
  }

  async refreshBatch(batchName) {
    const batch = this.findBatch(batchName);
    if (!batch) return;
    this.activeBatchName = batch.name;
    this.exportPinnedBatchName = batch.name;
    try {
      if (this.detailState?.batchName === batch.name && this.$root.attr("data-screen") === "detail") {
        await this.refreshDetailSummary();
        return;
      }
      await this.loadBatchItems(batch.name, batch.current_version, true);
      await this.loadAuditLogs(batch.name, batch.current_version);
      this.renderTable();
      if (this.drawerBatchName === batch.name && this.$root.find("[data-area='batch-drawer']").hasClass("is-open")) {
        if (this.erpFlowBlockState && this.erpFlowBlockState.batchName === batch.name) {
          await this.syncErpFlowBlock(batch.name);
        }
        this.renderBatchDrawer();
      }
    } catch (error) {
      this.showError(error);
    }
  }

  async confirmActualQtyFromQuantity(batchName = "") {
    const batch = this.findBatch(batchName || this.drawerBatchName || this.activeBatchName);
    if (!batch) return;
    const confirmed = await new Promise((resolve) => {
      frappe.confirm(
        `
          <div class="ocw-confirm-copy">
            <h4>${ocTranslate("按采购数量确认实际发货数量？")}</h4>
            <p>${ocTranslate("只会处理当前缺实际发货数量、且采购数量大于 0 的物料行；已有实际发货数量的行不会覆盖。")}</p>
            <div class="ocw-confirm-note">${ocTranslate("保存后会留下修改记录，并把批次标记为待重新试算。")}</div>
          </div>
        `,
        () => resolve(true),
        () => resolve(false)
      );
    });
    if (!confirmed) return;
    if (this.detailState.batchName !== batch.name) {
      this.showPendingFeature("请先进入该批次全宽详情，再确认实际发货数量。");
      return;
    }
    if (!(await this.ensureEditSession())) return;

    const result = await this.call(
      "overseas_costing.api.calculate.confirm_actual_shipped_qty_from_quantity",
      {
        batch_name: batch.name,
        version_name: batch.current_version || null,
        remark: "人工确认采购数量等于实际发货数量",
        edit_token: this.detailState.editToken,
        expected_modified: this.detailState.expectedModified || batch.modified || null,
      },
      true
    );
    if (!result.ok) {
      throw new Error(result.message || "实际发货数量确认失败");
    }
    if (Number(result.changed_count || 0) > 0) {
      this.markBatchDirty(batch.name);
    }
    this.acceptBatchWriteRevision?.(result.batch_modified);
    await this.refreshDetailSummary();
    frappe.show_alert({
      message: result.message || ocTranslate("实际发货数量已确认，请重新试算"),
      indicator: Number(result.changed_count || 0) > 0 ? "green" : "blue",
    });
  }

  feeGapConfig(gap = {}) {
    const text = `${gap.fieldname || ""} ${gap.label || ""}`.toLowerCase();
    if (text.includes("清关") || text.includes("clearance") || text.includes("customs")) {
      return {
        feeType: "clearance",
        title: ocTranslate("清关费"),
        uploadLabel: "上传清关资料",
        ruleCode: "manual_clearance_fee",
        expenseCategory: "清关费",
        defaultCurrency: "MXN",
        defaultBasis: "gross_weight",
        zeroRemark: "OCW_ZERO_CONFIRMED | 人工确认本票清关费为0",
      };
    }
    if (text.includes("关税") || text.includes("税费") || text.includes("tariff") || text.includes("duty") || text.includes("tax") || text.includes("igi") || text.includes("iva")) {
      return {
        feeType: "tariff",
        title: ocTranslate("关税/税费"),
        uploadLabel: "上传完税凭证",
        ruleCode: "manual_tariff_tax",
        expenseCategory: "关税税费",
        defaultCurrency: "MXN",
        defaultBasis: "goods_value",
        zeroRemark: "OCW_ZERO_CONFIRMED | 人工确认本票关税税费为0",
      };
    }
    return null;
  }

  openFeePoolGapDialog(batchName = "", gap = {}) {
    const batch = this.findBatch(batchName || this.drawerBatchName || this.activeBatchName);
    const config = this.feeGapConfig(gap);
    if (!batch || !config) return;
    if (!batch.current_version) {
      this.showPendingFeature("当前批次还没有版本，请先生成明细并重新试算。");
      return;
    }
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("录入{0}暂估", [config.title]),
      size: "large",
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "fee_pool_gap",
          options: `
            <div class="ocw-purchase-preview">
              <div class="ocw-purchase-target">
                <span>${ocTranslate("当前批次")}</span>
                <strong>${this.escape(batch.batch_no || batch.waybill_no || batch.name)}</strong>
                <em>${ocTranslate("暂估金额会进入费用池参与综合单价试算；后续拿到正式资料后可再调整。")}</em>
              </div>
              <div class="ocw-manual-quote-box">
                <div class="ocw-manual-quote-head">
                  <strong>${ocTranslate("{0}暂估", [this.escape(ocTranslate(config.title))])}</strong>
                  <span>${ocTranslate("适用于暂时没有完税凭证、但已有清关/税费预计金额的情况。")}</span>
                </div>
                <div class="ocw-manual-quote-grid">
                  <label>
                    <span>${ocTranslate("金额")}</span>
                    <input class="form-control" data-field="amount" type="number" step="0.000001" placeholder="${ocTranslate('请输入大于 0 的金额')}">
                  </label>
                  <label>
                    <span>${ocTranslate("币种")}</span>
                    <select class="form-control" data-field="currency">
                      ${this.renderManualQuoteOption("MXN", "比索 MXN", config.defaultCurrency)}
                      ${this.renderManualQuoteOption("RMB", "人民币 RMB", config.defaultCurrency)}
                      ${this.renderManualQuoteOption("USD", "美元 USD", config.defaultCurrency)}
                    </select>
                  </label>
                  <label>
                    <span>${ocTranslate("分摊依据")}</span>
                    <select class="form-control" data-field="allocation_basis">
                      ${this.renderManualQuoteOption("goods_value", "按货值分摊", config.defaultBasis)}
                      ${this.renderManualQuoteOption("gross_weight", "按毛重分摊", config.defaultBasis)}
                      ${this.renderManualQuoteOption("chargeable_weight", "按计费重分摊", config.defaultBasis)}
                      ${this.renderManualQuoteOption("volume", "按体积分摊", config.defaultBasis)}
                    </select>
                  </label>
                  <label class="ocw-manual-quote-full">
                    <span>${ocTranslate("依据/备注")}</span>
                    <textarea class="form-control" data-field="remark" rows="3" placeholder="${ocTranslate('例如：按货代报价暂估，待完税凭证回来后对账调整')}"></textarea>
                  </label>
                </div>
                <div class="ocw-manual-quote-actions">
                  <button class="ocw-primary-btn" type="button" data-action="save-fee-pool-gap">${ocTranslate("保存暂估并重新试算")}</button>
                </div>
              </div>
            </div>
          `,
        },
      ],
      primary_action_label: ocTranslate("关闭"),
      primary_action: () => dialog.hide(),
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-purchase-modal");
    dialog.$wrapper.off("click.ocwFeePoolGap").on("click.ocwFeePoolGap", "[data-action='save-fee-pool-gap']", () => {
      this.saveFeePoolGap(batch, config, dialog).catch((error) => this.showError(error));
    });
  }

  async saveFeePoolGap(batch, config, dialog) {
    const amount = this.readManualQuoteValue(dialog, "amount");
    const currency = this.readManualQuoteValue(dialog, "currency") || config.defaultCurrency;
    const allocationBasis = this.readManualQuoteValue(dialog, "allocation_basis") || config.defaultBasis;
    const remark = this.readManualQuoteValue(dialog, "remark") || ocTranslate("{0}暂估录入", [config.title]);
    if (!this.isPositive(amount)) {
      frappe.msgprint(ocTranslate("请先填写大于 0 的{0}金额。", [config.title]));
      return;
    }
    const confirmed = await new Promise((resolve) => {
      frappe.confirm(
        `
          <div class="ocw-confirm-copy">
            <h4>${ocTranslate("确认保存{0}暂估？", [this.escape(ocTranslate(config.title))])}</h4>
            <p>${ocTranslate("系统会把 {0} {1} 写入费用池，并立即重新试算。", [this.escape(this.formatMoney(amount)), this.escape(currency)])}</p>
            <div class="ocw-confirm-note">${ocTranslate("后续拿到正式资料后，可以再次调整并保留修改记录。")}</div>
          </div>
        `,
        () => resolve(true),
        () => resolve(false)
      );
    });
    if (!confirmed) return;

    const $button = dialog.$wrapper.find("[data-action='save-fee-pool-gap']");
    $button.prop("disabled", true).text(ocTranslate("保存中..."));
    try {
      const result = await this.call(
        "overseas_costing.api.calculate.update_allocation_rule",
        {
          batch_name: batch.name,
          version_name: batch.current_version,
          rule_payload: JSON.stringify({
            rule_code: config.ruleCode,
            expense_category: config.expenseCategory,
            allocation_basis: allocationBasis,
            basis_field: allocationBasis,
            currency,
            amount: Number(amount),
            is_active: 1,
            is_enabled: 1,
            priority_no: config.feeType === "tariff" ? 70 : 60,
            remark: ocTranslate("{0}暂估 | {1}", [config.title, remark]),
          }),
        },
        true
      );
      if (!result.ok) throw new Error(result.message || ocTranslate("{0}暂估保存失败", [config.title]));
      dialog.hide();
      await this.recalculate(batch.name);
    } finally {
      $button.prop("disabled", false).text(ocTranslate("保存暂估并重新试算"));
    }
  }

  async confirmZeroFeeGap(batchName = "", gap = {}) {
    const batch = this.findBatch(batchName || this.drawerBatchName || this.activeBatchName);
    const config = this.feeGapConfig(gap);
    if (!batch || !config) return;
    if (!batch.current_version) {
      this.showPendingFeature("当前批次还没有版本，请先生成明细并重新试算。");
      return;
    }
    const confirmed = await new Promise((resolve) => {
      frappe.confirm(
        `
          <div class="ocw-confirm-copy">
            <h4>${ocTranslate("确认本票{0}为 0？", [this.escape(ocTranslate(config.title))])}</h4>
            <p>${ocTranslate("系统会写入 0 元确认记录，并重新试算；之后校验结果不会再因该项缺失被阻断。")}</p>
            <div class="ocw-confirm-note">${ocTranslate("这不是暂估金额，表示人工确认当前票据该项无需计入综合成本。")}</div>
          </div>
        `,
        () => resolve(true),
        () => resolve(false)
      );
    });
    if (!confirmed) return;

    const result = await this.call(
      "overseas_costing.api.calculate.update_allocation_rule",
      {
        batch_name: batch.name,
        version_name: batch.current_version,
        rule_payload: JSON.stringify({
          rule_code: config.ruleCode,
          expense_category: config.expenseCategory,
          allocation_basis: config.defaultBasis,
          basis_field: config.defaultBasis,
          currency: config.defaultCurrency,
          amount: 0,
          is_active: 1,
          is_enabled: 1,
          priority_no: config.feeType === "tariff" ? 70 : 60,
          remark: config.zeroRemark,
        }),
      },
      true
    );
    if (!result.ok) throw new Error(result.message || ocTranslate("{0}0 元确认失败", [config.title]));
    await this.recalculate(batch.name);
  }

  async confirmCalculationResult(batchName = "") {
    if (this.viewState?.task !== "cost") {
      this.showPendingFeature("请在“成本核对”任务中人工确认计算结果。");
      return;
    }
    const batch = this.findBatch(batchName || this.drawerBatchName);
    if (!batch) return;
    const operationKey = `${batch.name}::${batch.current_version || ""}`;
    if (!(this._confirmCalculationInFlight instanceof Map)) this._confirmCalculationInFlight = new Map();
    const existing = this._confirmCalculationInFlight.get(operationKey);
    if (existing) return existing;
    const operation = Promise.resolve().then(() => this.confirmCalculationResultOnce(batch));
    this._confirmCalculationInFlight.set(operationKey, operation);
    try {
      return await operation;
    } finally {
      if (this._confirmCalculationInFlight.get(operationKey) === operation) {
        this._confirmCalculationInFlight.delete(operationKey);
      }
    }
  }

  async confirmCalculationResultOnce(batch) {
    const context = this.captureReviewNavigationContext(batch.name);
    if (batch.review_state !== "ready") {
      await this.applyAuthoritativeReviewClassification(
        batch.name,
        { task: "pending", batch },
        context,
        { reload: false, render: false }
      );
      this.showPendingFeature("当前批次仍在待处理，请完成补录或重新计算后再到“成本核对”确认。");
      return;
    }
    try {
      const authoritative = await this.getAuthoritativeReviewClassification(batch.name);
      if (!this.reviewNavigationContextMatches(context)) return;
      if (authoritative.task === "unknown") {
        if (this.detailState?.batchName === batch.name && this.viewState?.screen === "detail") {
          await this.refreshDetailSummary();
        }
        this.showPendingFeature("暂时无法确认批次的服务端分类，已保留当前页面，请刷新后重试。");
        return;
      }
      if (authoritative.task !== "cost" || authoritative.batch?.review_state !== "ready") {
        await this.applyAuthoritativeReviewClassification(batch.name, authoritative, context);
        const destination = authoritative.task === "pending" ? "待处理" : authoritative.task === "erp" ? "ERP 队列" : "已确认记录";
        this.showPendingFeature(ocTranslate("批次权威状态已变化，已切换到{0}。", [destination]));
        return;
      }
      Object.assign(batch, authoritative.batch);
      const result = await this.call(
        "overseas_costing.api.writeback.confirm_calculation_result",
        {
          batch_name: batch.name,
          version_name: batch.current_version || null,
          remark: "前端人工校验计算结果",
        },
        true
      );
      if (!this.reviewNavigationContextMatches(context)) {
        if (result.ok && result.confirmed !== false) {
          this.recordUsage("CONFIRM_RESULT", { batch, remark: "人工校验计算结果通过（页面已切换）" });
          frappe.show_alert({ message: ocTranslate("{0}，当前页面未切换。", [result.message || "计算结果已确认"]), indicator: "green" });
        }
        return;
      }
      if (!result.ok || result.confirmed === false) {
        this.showErpFlowBlock(result, "校验未通过");
        return;
      }
      batch.status = "Confirmed";
      batch.confirm_status = "Confirmed";
      batch.is_locked = 1;
      batch.writeback_status = batch.writeback_status || "Not Started";
      const keepDetailOpen = this.detailState?.batchName === batch.name;
      const tab = this.detailState?.tab || this.viewState?.tab || "overview";
      this.viewState.task = "erp";
      this.viewState.page = 1;
      this.filters.review_status = "pending";
      this.filters.review_warning = "";
      this.filters.issue = "";
      this.replaceViewState({
        task: "erp",
        review_status: "pending",
        review_warning: "",
        issue: "",
        page: 1,
        screen: keepDetailOpen ? "detail" : "workbench",
        batch: keepDetailOpen ? batch.name : "",
        tab: keepDetailOpen ? tab : "overview",
      });
      await this.loadBatches();
      this.recordUsage("CONFIRM_RESULT", { batch, remark: "人工校验计算结果通过" });
      frappe.show_alert({ message: result.message || ocTranslate("计算结果已确认"), indicator: "green" });
    } catch (error) {
      this.recordUsage("CONFIRM_RESULT", { batch, status: "Failed", remark: error.message || "人工校验计算结果失败" });
      this.showError(error);
    }
  }

  async previewErpPayload(batchName = "") {
    const batch = this.findBatch(batchName || this.drawerBatchName);
    if (!batch) return;
    try {
      const result = await this.call(
        "overseas_costing.api.writeback.preview_erp_payload",
        {
          batch_name: batch.name,
          version_name: batch.current_version || null,
        },
        true
      );
      if (!result.ok || !result.ready) {
        this.showErpFlowBlock(result, "暂不能预览 ERP 报文");
        return;
      }
      this.recordUsage("PREVIEW_ERP", { batch, remark: "预览 DeepLinkERP 推送报文" });
      this.openErpPayloadPreviewDialog(result.payload || {}, result);
    } catch (error) {
      this.recordUsage("PREVIEW_ERP", { batch, status: "Failed", remark: error.message || "预览 ERP 报文失败" });
      this.showError(error);
    }
  }

  writebackToErp(batchName = "") {
    const batch = this.findBatch(batchName || this.drawerBatchName);
    if (!batch) return;
    // 原来是一个盲确认框：用户看不到要推到哪些站点/公司、多少钱、哪些行被门槛拦住，
    // 只能凭"确认推送？"点下去。现在先摊开服务端的冻结预览，在预览里确认。
    // 执行入口仍是同一个 queueErpWriteback，不另开一条推送路径。
    this.openErpSiteSyncDialog(batch.name).catch((error) => this.showError(error));
  }

  async queueErpWriteback(batchName = "") {
    const batch = this.findBatch(batchName || this.drawerBatchName);
    if (!batch) return;
    const requestKey = `${batch.name}::${batch.current_version || ""}`;
    this.erpWritebackInFlight ||= new Set();
    if (this.erpWritebackInFlight.has(requestKey)) return;
    this.erpWritebackInFlight.add(requestKey);
    try {
      let result;
      try {
        result = await this.call(
          "overseas_costing.api.writeback.writeback_to_erp",
          {
            batch_name: batch.name,
            version_name: batch.current_version || null,
          },
          true
        );
      } catch (error) {
        const message = String(error?.message || error || "ERP 推送请求失败").trim();
        const failure = {
          batch_name: batch.name,
          version_name: batch.current_version || null,
          message,
          blocking_reasons: [],
        };
        this.recordUsage("PUSH_ERP", { batch, status: "Failed", remark: message });
        this.setErpPushFailureState(batch, failure);
        throw error;
      }
      if (!result.ok) {
        if (result.writeback_status) batch.writeback_status = result.writeback_status;
        if (result.message) batch.writeback_message = result.message;
        this.recordUsage("PUSH_ERP", { batch, status: "Failed", remark: result.message || "ERP 推送未进入队列" });
        try {
          await this.refreshBatch(batch.name);
        } catch (refreshError) {
          console.warn("[overseas-cost-workbench] ERP 失败后刷新批次未完成", refreshError);
        } finally {
          this.setErpPushFailureState(batch, result);
        }
        return;
      }
      batch.writeback_status = result.writeback_status || "Pending";
      batch.writeback_message = result.message || "";
      if (this.erpPushBlockState?.batchName === batch.name) {
        this.erpPushBlockState = null;
        this.updateDetailErpAction?.(batch);
      }
      if (this.erpFlowBlockState?.batchName === batch.name && this.erpFlowBlockState.action === "PUSH_ERP") {
        this.erpFlowBlockState = null;
      }
      await this.refreshBatch(batch.name);
      this.recordUsage("PUSH_ERP", { batch, remark: result.message || "推送 DeepLinkERP" });
      const indicator = String(result.writeback_status || "").toLowerCase().includes("success") ? "green" : "orange";
      frappe.show_alert({ message: result.message || ocTranslate("DeepLinkERP 推送已处理"), indicator });
      // 推送成功只说明"发出去了"，用户并不知道去 ERP 的哪里看；立刻把新建单据摆出来。
      // 这一步失败不能反过来把已成功的推送说成失败。
      try {
        await this.announceErpPushDocuments?.(batch.name);
      } catch (announceError) {
        console.warn("[overseas-cost-workbench] 推送后跳转提示未完成", announceError);
      }
    } finally {
      this.erpWritebackInFlight.delete(requestKey);
    }
  }

  setErpPushFailureState(batch, result = {}) {
    this.erpPushBlockState = {
      batchName: batch.name,
      versionName: String(result.version_name || batch.current_version || ""),
      message: String(result.message || "").trim(),
      blocking_reasons: Array.isArray(result.blocking_reasons) ? [...result.blocking_reasons] : [],
    };
    this.showErpFlowBlock(result, "ERP 推送未进入队列", { action: "PUSH_ERP" });
  }

  showErpFlowBlock(result = {}, title = "流程阻断", options = {}) {
    const batchName = result.batch_name || this.drawerBatchName || this.activeBatchName;
    this.erpFlowBlockState = {
      batchName,
      title,
      action: options.action || "",
      result: {
        ...result,
        batch_name: batchName,
      },
    };
    if (this.drawerBatchName === batchName && this.$root.find("[data-area='batch-drawer']").hasClass("is-open")) {
      this.renderBatchDrawer();
    }
    if (this.detailState?.batchName === batchName) this.updateDetailErpAction?.(this.getDetailBatch());
  }

  async syncErpFlowBlock(batchName = "") {
    const batch = this.findBatch(batchName || this.drawerBatchName || this.activeBatchName);
    if (!batch) return;
    const readiness = await this.call(
      "overseas_costing.api.writeback.check_writeback_ready",
      {
        batch_name: batch.name,
        version_name: batch.current_version || null,
      },
      false
    );
    if (readiness.ok && readiness.ready) {
      if (this.erpFlowBlockState && this.erpFlowBlockState.batchName === batch.name) {
        this.erpFlowBlockState = null;
      }
      if (this.detailState?.batchName === batch.name) this.updateDetailErpAction?.(this.getDetailBatch());
      return readiness;
    }
    this.erpFlowBlockState = {
      batchName: batch.name,
      title: ocTranslate("校验未通过"),
      result: readiness || {},
    };
    if (this.detailState?.batchName === batch.name) this.updateDetailErpAction?.(this.getDetailBatch());
    return readiness;
  }

  renderErpFlowBlockInline(batch) {
    if (!this.erpFlowBlockState || this.erpFlowBlockState.batchName !== batch.name) return "";
    const state = this.erpFlowBlockState;
    return `
      <div class="ocw-erp-block-inline">
        ${this.renderErpFlowBlockContent(state.result || {}, state.title || "流程阻断")}
      </div>
    `;
  }

  renderErpFlowBlockContent(result = {}, title = "流程阻断") {
    const reasons = Array.isArray(result.blocking_reasons) ? result.blocking_reasons : [];
    const fieldGaps = result.field_gaps || {};
    const gapHtml = this.renderErpFieldGapsWithActions(
      fieldGaps,
      result.batch_name || this.drawerBatchName || this.activeBatchName,
      result
    );
    const reasonHtml = reasons.length
      ? `<ul>${reasons.map((reason) => `<li>${this.escape(reason)}</li>`).join("")}</ul>`
      : `<p>${this.escape(result.message || ocTranslate("请补齐基础数据或重新试算后再继续。"))}</p>`;
    return `
      <div class="ocw-erp-block-dialog">
        <div class="ocw-erp-block-head">
          <div>
            <h5>${this.escape(ocTranslate(title))}</h5>
            <span>${this.escape(reasons.length ? ocTranslate("待补信息共 {0} 项", [reasons.length]) : result.message || ocTranslate("请补齐基础数据或重新试算后再继续。"))}</span>
          </div>
        </div>
        ${gapHtml}
        <div class="ocw-erp-block-reason">${reasonHtml}</div>
      </div>
    `;
  }
  renderErpFieldGaps(fieldGaps = {}) {
    const sections = [
      { key: "batch", title: ocTranslate("批次头待补") },
      { key: "rules", title: ocTranslate("费用池待补") },
      { key: "items", title: ocTranslate("明细待补") },
    ];
    const summary = fieldGaps.summary || {};
    const html = sections
      .map((section) => {
        const rows = Array.isArray(fieldGaps[section.key]) ? fieldGaps[section.key] : [];
        if (!rows.length) return "";
        const list = rows
          .map(
            (row) => `
              <li>
                <strong>${this.escape(row.label || row.fieldname || "--")}</strong>
                <span>${this.escape(row.suggestion || row.source_hint || "")}</span>
              </li>
            `
          )
          .join("");
        return `
          <div class="ocw-erp-gap-group">
            <h5>${ocTranslate("{0}（{1} 项）", [this.escape(ocTranslate(section.title)), this.escape(String(rows.length))])}</h5>
            <ul>${list}</ul>
          </div>
        `;
      })
      .filter(Boolean)
      .join("");
    if (!html) return "";
    const total = summary.missing_total ?? sections.reduce((count, section) => count + ((Array.isArray(fieldGaps[section.key]) ? fieldGaps[section.key].length : 0)), 0);
    return `
      <div class="ocw-erp-gap-summary">
        <div class="ocw-erp-gap-total">${ocTranslate("待补信息共 {0} 项", [this.escape(String(total))])}</div>
        ${html}
      </div>
    `;
  }

  renderErpFieldGapsWithActions(fieldGaps = {}, batchName = "", readiness = {}) {
    const sections = [
      { key: "batch", title: ocTranslate("批次头待补") },
      { key: "rules", title: ocTranslate("费用池待补") },
      { key: "items", title: ocTranslate("明细待补") },
    ];
    const batch = batchName ? this.findBatch(batchName) : this.findBatch(this.drawerBatchName || this.activeBatchName);
    const summary = fieldGaps.summary || {};
    const html = sections
      .map((section) => {
        const rows = Array.isArray(fieldGaps[section.key]) ? fieldGaps[section.key] : [];
        if (!rows.length) return "";
        const list = rows.map((row) => this.renderErpGapRow(section.key, row, batch, readiness)).join("");
        return `
          <div class="ocw-erp-gap-group">
            <h5>${ocTranslate("{0}（{1}项）", [this.escape(ocTranslate(section.title)), this.escape(String(rows.length))])}</h5>
            <ul>${list}</ul>
          </div>
        `;
      })
      .filter(Boolean)
      .join("");
    if (!html) return "";
    const total = summary.missing_total ?? sections.reduce((count, section) => count + ((Array.isArray(fieldGaps[section.key]) ? fieldGaps[section.key].length : 0)), 0);
    return `
      <div class="ocw-erp-gap-summary">
        <div class="ocw-erp-gap-total">${ocTranslate("待补信息共 {0} 项", [this.escape(String(total))])}</div>
        ${html}
        ${batch ? `<div class="ocw-erp-flow-note">${ocTranslate("补齐后请使用页头计算入口开始试算或重新计算。")}</div>` : ""}
      </div>
    `;
  }

  renderErpGapRow(scope, row = {}, batch = null, readiness = {}) {
    const rowLabel = row.label || row.fieldname || "--";
    const detailLabel = [row.row_no || row.excel_row_no || "", row.material_code || "", row.product_name || ""].filter(Boolean).join(" / ");
    const examples = Array.isArray(readiness.item_issue_examples) ? readiness.item_issue_examples : [];
    const matchedExample = scope === "items"
      ? examples.find((example) => (example.missing_fieldnames || []).includes(row.fieldname))
      : null;
    const buttons = [];
    if (batch && (scope === "batch" || scope === "rules")) {
      buttons.push(`<button class="ocw-link-btn" type="button" data-action="gap-repull-dingtalk" data-batch-name="${this.escape(batch.name)}">${ocTranslate("重拉钉钉")}</button>`);
    }
    if (batch && scope === "rules") {
      const feeConfig = this.feeGapConfig(row);
      if (feeConfig) {
        buttons.push(`<button class="ocw-link-btn" type="button" data-action="gap-open-source-center" data-batch-name="${this.escape(batch.name)}" data-gap-fieldname="${this.escape(row.fieldname || "")}" data-gap-label="${this.escape(row.label || "")}">${this.escape(feeConfig.uploadLabel)}</button>`);
        buttons.push(`<button class="ocw-link-btn" type="button" data-action="gap-open-fee-pool" data-batch-name="${this.escape(batch.name)}" data-gap-fieldname="${this.escape(row.fieldname || "")}" data-gap-label="${this.escape(row.label || "")}">${ocTranslate("录入暂估")}</button>`);
        buttons.push(`<button class="ocw-link-btn" type="button" data-action="gap-confirm-zero-fee" data-batch-name="${this.escape(batch.name)}" data-gap-fieldname="${this.escape(row.fieldname || "")}" data-gap-label="${this.escape(row.label || "")}">${ocTranslate("确认为0")}</button>`);
      } else {
        buttons.push(`<button class="ocw-link-btn" type="button" data-action="gap-open-source-center" data-batch-name="${this.escape(batch.name)}" data-gap-fieldname="${this.escape(row.fieldname || "")}" data-gap-label="${this.escape(row.label || "")}">${ocTranslate("去补资料")}</button>`);
      }
    }
    if (batch && scope === "items") {
      if (row.fieldname === "actual_shipped_qty") {
        buttons.push(`<button class="ocw-link-btn" type="button" data-action="gap-open-source-center" data-batch-name="${this.escape(batch.name)}" data-gap-fieldname="${this.escape(row.fieldname || "")}" data-gap-label="${this.escape(row.label || "")}">${ocTranslate("上传装箱单")}</button>`);
        buttons.push(`<button class="ocw-link-btn" type="button" data-action="gap-confirm-actual-qty" data-batch-name="${this.escape(batch.name)}">${ocTranslate("按采购数量确认")}</button>`);
      }
      buttons.push(`<button class="ocw-link-btn" type="button" data-action="gap-open-item-edit" data-batch-name="${this.escape(batch.name)}" data-item-name="${this.escape((matchedExample && matchedExample.item_name) || row.item_name || "")}" data-fieldname="${this.escape(row.fieldname || "")}">${ocTranslate("去补录")}</button>`);
    }
    return `
      <li class="ocw-erp-gap-row">
        <div class="ocw-erp-gap-main">
          <strong>${this.escape(rowLabel)}</strong>
          <span>${this.escape(row.suggestion || row.source_hint || "")}</span>
          ${detailLabel ? `<em>${this.escape(detailLabel)}</em>` : ""}
        </div>
        <div class="ocw-erp-gap-actions">${buttons.join("")}</div>
      </li>
    `;
  }

  openErpPayloadPreviewDialog(payload = {}, result = {}) {
    const sites = Array.isArray(payload.sites) ? payload.sites : [];
    const groups = sites.flatMap((site) => (site.groups || []).map((group) => ({...group, site_code:site.site_code || group.site_code})));
    const routedItems = groups.flatMap((group) => (group.items || []).map((item) => ({
      ...item, __company:group.subsidiary_code, __site:group.site_code, __supplier:group.supplier,
    })));
    const items = routedItems.length ? routedItems : (Array.isArray(payload.items) ? payload.items : []);
    const companies = [...new Set(groups.map((group) => group.subsidiary_code).filter(Boolean))];
    const rows = items.slice(0, 12).map((item, index) => {
      const formula = item.cost_formula || {};
      const derivedPrice = item.adopted_price?.source_type === "purchase_total_derived";
      return `
        <tr>
          <td>${this.escape(String(index + 1))}</td>
          <td>${this.escape(this.formatValue(item.material_code || "--"))}</td>
          <td>${this.escape(this.formatValue(item.__company || item.subsidiary_code || "--"))}</td>
          <td>${this.escape(this.formatValue(item.__supplier || item.supplier || "--"))}</td>
          <td>${this.escape(this.formatMoney(item.adopted_price?.value ?? item.original_unit_price ?? formula.original_unit_price ?? "--"))}${derivedPrice ? `<small class="ocw-result-source">${ocTranslate("按货值÷采购数量计算")}</small>` : ""}</td>
          <td>${this.escape(this.formatMoney(item.comprehensive_unit_price ?? formula.comprehensive_unit_price ?? "--"))}</td>
          <td>${this.escape(this.formatValue(item.source_quantity ?? item.outbound_quantity ?? formula.quantity ?? "--"))}</td>
          <td>${this.escape(this.formatMoney(item.total_cost_rmb ?? formula.total_cost ?? "--"))}</td>
          <td>${this.escape(this.formatMoney(item.allocated_fee_rmb ?? formula.allocated_total_cost ?? 0))}</td>
        </tr>
      `;
    }).join("");
    const jsonText = JSON.stringify(payload, null, 2);
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("DeepLinkERP 报文预览"),
      size: "extra-large",
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "erp_payload_preview",
          options: `
            <div class="ocw-erp-preview">
              <div class="ocw-erp-preview-summary">
                <div><span>${ocTranslate("目标系统")}</span><strong>${this.escape(payload.target_system || "DeepLinkERP")}</strong></div>
                <div><span>${ocTranslate("ERP 公司")}</span><strong>${this.escape(companies.length ? ocTranslate("{0} 家", [companies.length]) : payload.subsidiary_code || "--")}</strong></div>
                <div><span>${ocTranslate("批次")}</span><strong>${this.escape(payload.batch_no || payload.batch_name || result.batch_name || "--")}</strong></div>
                <div><span>${ocTranslate("版本")}</span><strong>${this.escape(payload.version_name || result.version_name || "--")}</strong></div>
                <div><span>${ocTranslate("物料行数")}</span><strong>${this.escape(this.formatValue(payload.item_count || items.length || 0))}</strong></div>
                <div><span>${ocTranslate("综合成本")}</span><strong>${this.escape(this.formatMoney(payload.total_cost_rmb || 0))} RMB</strong></div>
              </div>
              <div class="ocw-erp-pool-strip">
                <span>${ocTranslate("站点 {0} 个", [this.escape(this.formatValue(sites.length || 0))])}</span>
                <span>${ocTranslate("Company {0} 家", [this.escape(this.formatValue(companies.length || payload.company_count || 0))])}</span>
                <span>${ocTranslate("采购单分组 {0} 组", [this.escape(this.formatValue(groups.length || payload.group_count || 0))])}</span>
                <span>${ocTranslate("分摊费用 {0} RMB", [this.escape(this.formatMoney(payload.allocated_fee_rmb || 0))])}</span>
              </div>
              <div class="ocw-erp-preview-table-wrap">
                <table class="ocw-erp-preview-table">
                  <thead><tr><th>#</th><th>${ocTranslate("物料编码")}</th><th>${ocTranslate("ERP 公司")}</th><th>${ocTranslate("供应商")}</th><th>${ocTranslate("原始单价")}</th><th>${ocTranslate("综合单价")}</th><th>${ocTranslate("采购数量")}</th><th>${ocTranslate("综合成本")}</th><th>${ocTranslate("分摊费用")}</th></tr></thead>
                  <tbody>${rows || `<tr><td colspan="9">${ocTranslate("暂无物料明细")}</td></tr>`}</tbody>
                </table>
              </div>
              <details class="ocw-erp-json-detail">
                <summary>${ocTranslate("查看技术报文 JSON")}</summary>
                <pre class="ocw-erp-json-preview">${this.escape(jsonText)}</pre>
              </details>
            </div>
          `,
        },
      ],
      primary_action_label: ocTranslate("关闭"),
      primary_action: () => dialog.hide(),
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-erp-preview-modal");
  }

  applyRecalculateSummary(batchName, summary, allocationRules = []) {
    const batch = this.findBatch(batchName);
    if (!batch) return;
    batch.status = "Calculated";
    if (summary.item_count !== undefined) batch.item_count = summary.item_count;
    if (summary.total_goods_value !== undefined) batch.total_goods_value = summary.total_goods_value;
    if (summary.total_gross_weight_kg !== undefined) batch.total_gross_weight_kg = summary.total_gross_weight_kg;
    if (summary.total_cost_rmb !== undefined) batch.estimated_total_cost_rmb = summary.total_cost_rmb;
    batch.summary_snapshot = summary;
    batch.ai_allocation = summary.ai_allocation || {};
    if (Array.isArray(allocationRules)) batch.allocation_rule_snapshot = allocationRules;
