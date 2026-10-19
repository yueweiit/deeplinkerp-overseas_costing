  async openDingtalkOrder(batchName = "") {
    const batch = batchName ? this.findBatch(batchName) : this.getActiveBatch();
    if (!batch) {
      this.showPendingFeature("当前没有可打开的批次。");
      return;
    }
    this.activeBatchName = batch.name;
    if (this.isOpeningDingtalk) return;
    this.isOpeningDingtalk = true;
    let preopenedWindow = null;
    try {
      preopenedWindow = window.open("about:blank", "_blank");
      if (preopenedWindow) {
        preopenedWindow.opener = null;
      }
      const result = await this.call("overseas_costing.api.batch.get_dingtalk_order_link", {
        batch_name: batch.name,
      });
      const order = result.dingtalk_order || {};
      if (!order.can_open || !order.open_url) {
        this.closePreopenedWindow(preopenedWindow);
        this.showPendingFeature("当前批次还没有关联钉钉审批链接，请先从国际物流单或采购支出单补来源信息。");
        return;
      }

      if (order.open_mode === "desktop_protocol") {
        this.closePreopenedWindow(preopenedWindow);
        window.location.href = order.open_url;
        frappe.show_alert({ message: ocTranslate("正在唤起钉钉客户端"), indicator: "blue" });
        return;
      }

      if (!this.openBrowserTab(order.open_url, "钉钉订单", preopenedWindow)) {
        frappe.msgprint({
          title: ocTranslate("钉钉订单"),
          message: `${ocTranslate("浏览器拦截了新窗口，请点击链接打开：")}<br><a href="${this.escape(order.open_url)}" target="_blank" rel="noopener noreferrer">${this.escape(order.open_url)}</a>`,
          indicator: "orange",
        });
      }
    } catch (error) {
      this.closePreopenedWindow(preopenedWindow);
      this.showError(error);
    } finally {
      this.isOpeningDingtalk = false;
    }
  }

  showPendingFeature(message) {
    frappe.show_alert({ message, indicator: "orange" });
  }

  setBatchLoading() {
    this.setTableLoading();
  }

  setTableLoading() {
    this.$root.find("[data-area='table']").html(`<div class="ocw-muted ocw-table-empty">${ocTranslate("加载中")}</div>`);
    this.$root.find("[data-area='table-count']").text("");
  }

  renderEmpty() {
    this.visibleBatches = [];
    this.batchItems = {};
    this.auditEvents = [];
    this.$root.find("[data-area='hierarchy-summary']").text(ocTranslate("暂无批次"));
    this.$root.find("[data-area='table']").html(`<div class="ocw-muted ocw-table-empty">${ocTranslate("暂无明细")}</div>`);
    this.$root.find("[data-area='table-title']").text(ocTranslate("报关/运单层级列表"));
    this.$root.find("[data-area='table-count']").text("");
    this.renderTransportWorkbench();
    this.renderAuditList();
    this.updateSearchResult();
    this.updateRecalculateAction();
  }

  renderBatchList() {
    this.renderTable();
  }

  renderTransportWorkbench() {
    if (!this.$root) return;
    const modes = this.transportWorkbenchModes();
    const scopeBatches = this.filterBatches({ ignoreTransport: true, ignoreBusinessType: true });
    const stats = this.transportWorkbenchStats(scopeBatches);
    const activeMode = String(this.filters.business_type || "").trim().toUpperCase();
    const totalCount = scopeBatches.length;
    this.$root
      .find("[data-action='set-transport-filter'][data-transport-mode='']")
      .toggleClass("active", !activeMode)
      .text(activeMode ? ocTranslate("全部") : ocTranslate("全部 {0}", [totalCount]));
    const html = modes
      .map((mode) => {
        const row = stats[mode.value] || { batchCount: 0, itemCount: 0 };
        const isActive = activeMode === mode.value;
        return `
          <button class="ocw-logistics-card ${isActive ? "active" : ""}" type="button" data-action="set-transport-filter" data-transport-mode="${this.escape(mode.value)}">
            <span>
              <strong>${this.escape(ocTranslate(mode.label))}</strong>
              <em>${this.escape(ocTranslate(mode.tip))}</em>
            </span>
            <b>${this.escape(String(row.batchCount))}</b>
            <small>${ocTranslate("{0} 行物料", [this.escape(String(row.itemCount))])}</small>
          </button>
        `;
      })
      .join("");
    this.$root.find("[data-area='transport-workbench']").html(html);
  }

  transportWorkbenchModes() {
    return [
      { value: "SEA_STANDARD", label: ocTranslate("海运正报正清"), tip: ocTranslate("主线核算") },
      { value: "SEA_DDP", label: ocTranslate("海运 DDP（双清包税）"), tip: ocTranslate("包税双清") },
      { value: "AIR_DDP", label: ocTranslate("空运 DDP（双清包税）"), tip: ocTranslate("包税双清") },
      { value: "AIR_STANDARD", label: ocTranslate("正常空运"), tip: ocTranslate("运单与计费重量") },
      { value: "EXPRESS", label: ocTranslate("快递"), tip: ocTranslate("面单与费用补齐") },
    ];
  }

  transportWorkbenchStats(batches = []) {
    const stats = this.transportWorkbenchModes().reduce((map, mode) => {
      map[mode.value] = { batchCount: 0, itemCount: 0 };
      return map;
    }, {});
    (batches || []).forEach((batch) => {
      const mode = this.batchBusinessType(batch, this.batchItems[batch.name] || []);
      if (!stats[mode]) return;
      stats[mode].batchCount += 1;
      stats[mode].itemCount += Number(batch.item_count || (this.batchItems[batch.name] || []).length || 0);
    });
    return stats;
  }

  renderBusinessEntityFilter() {
    const $select = this.$root.find("[data-filter='subsidiary_code']");
    if (!$select.length) return;
    const selected = String(this.filters.subsidiary_code || "");
    const values = this.businessEntityOptions.length
      ? this.businessEntityOptions.slice()
      : [...new Set(
          (this.batches || [])
            .map((batch) => String(batch.subsidiary_code || "").trim())
            .filter(Boolean)
        )].sort((left, right) => left.localeCompare(right, "zh-Hans-CN"));
    if (selected && !values.includes(selected)) values.push(selected);
    const signature = `${selected}\u0000${values.join("\u0000")}`;
    $select.prop("multiple", false).prop("size", 1).attr("size", "1");
    if (signature === this.businessEntityOptionsSignature) {
      $select.val(selected);
      return;
    }
    const options = [
      `<option value="">${ocTranslate("全部业务主体")}</option>`,
      ...values.map((value) => `<option value="${this.escape(value)}">${this.escape(value)}</option>`),
    ];
    $select.html(options.join("")).val(selected);
    this.businessEntityOptionsSignature = signature;
  }

  renderBusinessTypeFilter() {
    const $select = this.$root.find("[data-filter='business_type']");
    if (!$select.length) return;
    const selected = String(this.filters.business_type || "");
    const options = [
      `<option value="">${ocTranslate("全部业务类型")}</option>`,
      ...(this.businessTypeOptions || []).map((option) => {
        const value = typeof option === "string" ? option : option.value;
        const label = typeof option === "string" ? this.businessTypeLabel(option) : option.label;
        return `<option value="${this.escape(value)}">${this.escape(ocTranslate(label))}</option>`;
      }),
    ];
    $select.html(options.join("")).val(selected);
  }

  renderSummary(detail) {
    const header = detail.header || {};
    const summary = detail.summary || {};
    const version = detail.version || {};
    const cells = [
      ["批次", header.batch_no, ""],
      ["报关单号", header.customs_no, ""],
      ["运单号", header.waybill_no, ""],
      ["版本", this.versionLabel(version), ""],
      ["总货值", summary.total_goods_value || header.total_goods_value, "total_goods_value"],
      ["毛重 KG", summary.total_gross_weight_kg || header.total_gross_weight_kg, "gross_weight_kg"],
      ["综合成本 RMB", summary.total_cost_rmb || header.actual_total_cost_rmb || header.estimated_total_cost_rmb, "total_cost_rmb"],
      ["行数", summary.item_count || header.item_count, "quantity"],
    ];
    this.$root.find("[data-area='summary']").html(`
      ${cells
        .map(
          ([label, value, fieldname]) => `
            <div class="ocw-summary-cell">
              <span>${this.escape(ocTranslate(label))}</span>
              <strong>${this.escape(fieldname ? this.formatDisplayNumber(value, fieldname) : this.formatValue(value))}</strong>
            </div>
          `
        )
        .join("")}
    `);
  }

  renderTable() {
    this.renderTransportWorkbench();
    this.updateWorkRoleSwitch();
    this.updateMainViewSwitch();
    if (this.erpQueueMode) {
      this.renderErpQueueTable();
      return;
    }
    const labels = this.parentTableLabels();
    const displayBatches = this.getDisplayedBatches();
    this.$root.find("[data-area='table-title']").text(labels.title);
    this.$root.find("[data-area='table-count']").text(ocTranslate("{0} 个{1}", [displayBatches.length, labels.blockName]));
    this.renderBatchFocusControls();
    this.updateHierarchySummary();

    if (!displayBatches.length) {
      this.$root.find("[data-area='table']").html(`<div class="ocw-muted ocw-table-empty">${this.escape(ocTranslate(this.workRoleInfo().empty))}</div>`);
      return;
    }

    const rows = displayBatches.map((batch) => this.renderBatchRows(batch)).join("");
    this.$root.find("[data-area='table']").html(`
      <table class="ocw-hierarchy-table">
        <colgroup>
        <col class="ocw-col-toggle" />
        <col class="ocw-col-waybill" />
        <col class="ocw-col-purchase-approval" />
        <col class="ocw-col-count" />
        <col class="ocw-col-state" />
        <col class="ocw-col-value" />
          <col class="ocw-col-money" />
          <col class="ocw-col-value" />
          <col class="ocw-col-voucher" />
          <col class="ocw-col-action" />
        </colgroup>
        <thead>
          <tr>
            <th></th>
            <th>${this.escape(labels.logisticsNo)}</th>
            <th>${ocTranslate("采购审批")}</th>
            <th>${ocTranslate("SKU数")}</th>
            <th>${ocTranslate("资料状态")}</th>
            <th>${ocTranslate("采购货值")}</th>
            <th>${ocTranslate("已识别费用")}</th>
            <th>${ocTranslate("综合成本")}</th>
            <th>${ocTranslate("凭证差异")}</th>
            <th>${ocTranslate("操作")}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="ocw-hierarchy-x-scroll" data-role="hierarchy-x-scroll" aria-label="${ocTranslate('层级列表横向滚动条')}">
        <div class="ocw-hierarchy-x-scroll-spacer" data-role="hierarchy-x-scroll-spacer"></div>
      </div>
    `);
    this.bindHierarchyScrollbars();
    this.renderDiffPanel();
    this.updateRecalculateAction();
  }

  updateMainViewSwitch() {
    if (!this.$root) return;
    this.$root.find("[data-action='set-main-view']").each((_, node) => {
      const $button = $(node);
      const view = $button.attr("data-view");
      $button.toggleClass("active", this.erpQueueMode ? view === "erp_queue" : view === "cost");
    });
  }

  updateWorkRoleSwitch() {
    if (!this.$root) return;
    const role = this.workRoleInfo();
    this.$root.find("[data-area='role-description']").first().text(role.description);
    this.$root.find("[data-area='table-title']").attr("title", role.description);
    this.$root.find("[data-action='set-work-role']").each((_, node) => {
      $(node).toggleClass("active", $(node).attr("data-role") === role.key);
    });
  }

  renderErpQueueTable() {
    const batches = this.getErpQueueBatches();
    this.$root.find("[data-area='table-title']").text(ocTranslate("DeepLinkERP 待推送队列"));
    this.$root.find("[data-area='table-count']").text(ocTranslate("{0} 个批次", [batches.length]));
    this.renderBatchFocusControls();
    this.updateHierarchySummary();

    const filters = this.renderErpQueueFilters();
    if (!batches.length) {
      this.$root.find("[data-area='table']").html(`
        ${filters}
        <div class="ocw-muted ocw-table-empty">${ocTranslate("当前筛选下暂无 ERP 队列记录")}</div>
      `);
      this.renderDiffPanel();
      this.updateRecalculateAction();
      return;
    }

    const rows = batches.map((batch) => this.renderErpQueueRow(batch)).join("");
    this.$root.find("[data-area='table']").html(`
      ${filters}
      <table class="ocw-erp-queue-table">
        <thead>
          <tr>
            <th>${ocTranslate("批次")}</th>
            <th>${ocTranslate("业务主体")}</th>
            <th>${ocTranslate("版本")}</th>
            <th>${ocTranslate("物料数")}</th>
            <th>${ocTranslate("综合成本")}</th>
            <th>${ocTranslate("流程阶段")}</th>
            <th>${ocTranslate("回写状态")}</th>
            <th>${ocTranslate("生成时间")}</th>
            <th>${ocTranslate("操作")}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `);
    this.renderDiffPanel();
    this.updateRecalculateAction();
  }

  renderErpQueueFilters() {
    const options = [
      ["all", "全部"],
      ["not_started", "未开始"],
      ["pending", "待推送"],
      ["success", "成功"],
      ["failed", "失败"],
    ];
    const stats = this.erpQueueStats();
    return `
      <div class="ocw-erp-queue-toolbar">
        <div class="ocw-erp-queue-tabs">
          ${options
            .map(
              ([value, label]) => `
                <button class="${this.erpQueueStatus === value ? "active" : ""}" type="button" data-action="set-erp-queue-status" data-status="${this.escape(value)}">
                  ${this.escape(ocTranslate(label))} <span>${this.escape(String(stats[value] || 0))}</span>
                </button>
              `
            )
            .join("")}
        </div>
        <p>${ocTranslate("这里展示本系统已试算、已校验、已生成报文和待正式接口推送的批次。")}</p>
      </div>
    `;
  }

  erpQueueStats() {
    const stats = { all: 0, not_started: 0, pending: 0, success: 0, failed: 0 };
    (this.visibleBatches || []).forEach((batch) => {
      const key = this.erpWritebackQueueKey(batch);
      stats.all += 1;
      stats[key] = (stats[key] || 0) + 1;
    });
    return stats;
  }

  getErpQueueBatches() {
    const source = this.visibleBatches || [];
    if (this.erpQueueStatus === "all") return source;
    return source.filter((batch) => this.erpWritebackQueueKey(batch) === this.erpQueueStatus);
  }

  erpWritebackQueueKey(batch = {}) {
    const value = String(batch.writeback_status || "Not Started").toLowerCase();
    if (value.includes("success")) return "success";
    if (value.includes("fail")) return "failed";
    if (value.includes("pending")) return "pending";
    return "not_started";
  }

  renderErpQueueRow(batch) {
    const items = this.batchItems[batch.name] || [];
    const hasLoadedItems = Object.prototype.hasOwnProperty.call(this.batchItems, batch.name);
    const itemCount = hasLoadedItems ? items.length : Number(batch.item_count || 0);
    const totalCost = this.batchTotalCostNumber(batch, items, hasLoadedItems);
    const writebackInfo = this.erpWritebackStatusInfo(batch);
    const stage = this.erpQueueStageInfo(batch, itemCount, totalCost);
    const batchLabel = batch.batch_no || batch.customs_no || batch.waybill_no || batch.name;
    const canPreview = this.isCalculationConfirmed(batch);
    const canPush = canPreview && !String(batch.writeback_status || "").toLowerCase().includes("success");
    const previewTip = canPreview
      ? "点击预览 ERP 报文"
      : `${stage.label}：${stage.note || "先完成人工校验，再预览 ERP 报文"}`;
    return `
      <tr class="ocw-erp-queue-row" data-batch-name="${this.escape(batch.name)}">
        <td>
          <strong>${this.escape(batchLabel)}</strong>
          <span class="ocw-business-badge">${this.escape(this.businessTypeCompactLabel(batch.business_type))}</span>
        </td>
        <td>${this.escape(batch.subsidiary_code || "--")}</td>
        <td>${this.escape(batch.current_version || "--")}</td>
        <td class="ocw-num-cell">${this.escape(String(itemCount || 0))}</td>
        <td>${this.escape(this.isPositive(totalCost) ? `${this.formatMoney(totalCost)} RMB` : "--")}</td>
        <td><span class="ocw-queue-stage ${this.escape(stage.className)}">${this.escape(ocTranslate(stage.label))}</span><small>${this.escape(stage.note)}</small></td>
        <td><span class="ocw-queue-status ${this.escape(writebackInfo.state)}">${this.escape(ocTranslate(writebackInfo.label))}</span><small>${this.escape(batch.writeback_message || writebackInfo.note)}</small></td>
        <td>${this.escape(this.formatDateTimeMinute(batch.writeback_time) || "--")}</td>
        <td>
          <div class="ocw-row-action-group">
            <button class="ocw-outline-btn ocw-mini-btn" data-action="queue-open-batch" data-batch-name="${this.escape(batch.name)}">${ocTranslate("详情")}</button>
            <span class="ocw-tooltip-wrap" data-tooltip="${this.escape(previewTip)}" title="${this.escape(previewTip)}">
              ${
                canPreview
                  ? `<button class="ocw-outline-btn ocw-mini-btn" data-action="queue-preview-erp" data-batch-name="${this.escape(batch.name)}">${ocTranslate("预览")}</button>`
                  : `<button class="ocw-outline-btn ocw-mini-btn ocw-disabled-btn" type="button" disabled>${ocTranslate("预览")}</button>`
              }
            </span>
            <button class="ocw-outline-btn ocw-mini-btn" data-action="queue-writeback-erp" data-batch-name="${this.escape(batch.name)}"${canPush ? "" : " disabled"}>${this.erpWritebackQueueKey(batch) === "failed" ? ocTranslate("重试") : ocTranslate("生成")}</button>
          </div>
        </td>
      </tr>
    `;
  }

  erpQueueStageInfo(batch = {}, itemCount = 0, totalCost = 0) {
    const statusInfo = this.batchStatusInfo(batch.status, batch, itemCount);
    const confirmed = this.isCalculationConfirmed(batch);
    const queueKey = this.erpWritebackQueueKey(batch);
    if (queueKey === "success") return { label: ocTranslate("推送成功"), note: ocTranslate("ERP 已返回成功"), className: "is-ok" };
    if (queueKey === "failed") return { label: ocTranslate("推送失败"), note: ocTranslate("可查看原因后重试"), className: "is-warn" };
    if (queueKey === "pending") return { label: ocTranslate("已生成报文"), note: ocTranslate("等待正式接口推送"), className: "is-info" };
    if (confirmed) return { label: ocTranslate("已校验"), note: ocTranslate("可生成 ERP 报文"), className: "is-ok" };
    if (statusInfo.needsRecalculate) return { label: ocTranslate("待重算"), note: ocTranslate("先重新试算"), className: "is-warn" };
    if (this.isPositive(totalCost)) return { label: ocTranslate("已试算"), note: ocTranslate("等待人工校验"), className: "is-info" };
    return { label: ocTranslate("待试算"), note: itemCount ? ocTranslate("先补齐费用并试算") : ocTranslate("先导入物料"), className: "is-muted" };
  }

  parentTableLabels() {
    const mode = this.normalizeTransportMode(this.filters.transport_mode);
    const role = this.workRoleInfo();
    const defaults = {
      title: role.title,
      logisticsNo: "批次/物流单号",
      blockName: role.blockName,
    };
    const byMode = {
      SEA: {
        title: ocTranslate("海运批次列表"),
        logisticsNo: "批次/柜号",
        blockName: ocTranslate("海运单块"),
      },
      AIR: {
        title: ocTranslate("空运批次列表"),
        logisticsNo: "批次/空运单号",
        blockName: ocTranslate("空运单块"),
      },
      EXPRESS: {
        title: ocTranslate("快递批次列表"),
        logisticsNo: "批次/快递单号",
        blockName: ocTranslate("快递单块"),
      },
    };
    return byMode[mode] || defaults;
  }

  batchListLabel(batch = {}, firstItem = {}) {
    return (
      batch.waybill_no ||
      firstItem.waybill_no ||
      batch.customs_no ||
      firstItem.customs_no ||
      batch.batch_no ||
      firstItem.batch_no ||
      batch.source_approval_no ||
      firstItem.source_approval_no ||
      batch.name ||
      "--"
    );
  }

  renderBatchRows(batch) {
    const isExpanded = this.expandedBatchNames.has(batch.name);
    return this.renderParentRow(batch, isExpanded) + (isExpanded ? this.renderChildRow(batch) : "");
  }

  renderParentRow(batch, isExpanded) {
    const items = this.batchItems[batch.name] || [];
    const hasLoadedItems = Object.prototype.hasOwnProperty.call(this.batchItems, batch.name);
    const firstItem = items[0] || {};
    const waybillNo = this.batchListLabel(batch, firstItem);
    const itemCount = hasLoadedItems ? items.length : batch.item_count || 0;
    const statusInfo = this.batchStatusInfo(batch.status, batch, itemCount);
    const documentStatus = this.batchDataCompleteness(batch, items, hasLoadedItems);
    const goodsValueDisplay = this.batchGoodsValueDisplay(batch, items, hasLoadedItems);
    const recognizedFeeDisplay = this.batchRecognizedFeeDisplay(batch, items, hasLoadedItems);
    const totalCostDisplay = this.batchTotalCostDisplay(batch, items, hasLoadedItems);
    const voucherDiffDisplay = this.batchVoucherDiffDisplay(batch);
    const importedClass = this.lastImportedBatchNames.has(batch.name) ? "imported" : "";
    const submittedAt = this.formatDateTimeMinute(batch.source_created_at);
    const sampleBadge = batch.is_classic_sample
      ? `<span class="ocw-sample-badge">${this.escape(batch.sample_note || ocTranslate("历史样本"))}</span>`
      : "";
    const recalculateDisabled = (batch.source_status || {}).invalid_business
      ? `disabled title="${this.escape((batch.source_status || {}).invalid_business_reason || ocTranslate("已排除审批不能重算"))}"`
      : "";
    this.activeBatchName = this.activeBatchName || batch.name;
    return `
      <tr class="ocw-parent-row ${isExpanded ? "expanded" : ""} ${importedClass}" data-batch-name="${this.escape(batch.name)}" title="${ocTranslate('单击批次可选中并锁定；双击打开详情侧边栏；字段修改请在明细区点击可编辑字段')}">
        <td>
          <button class="ocw-tree-toggle" data-action="toggle-batch" data-batch-name="${this.escape(batch.name)}" aria-expanded="${isExpanded ? "true" : "false"}">
            ${isExpanded ? "-" : "+"}
          </button>
        </td>
        <td title="${this.escape(this.formatValue(waybillNo || ""))}">
          <strong>${this.renderParentValue(waybillNo, "waybill_no")}</strong>
          <span class="ocw-batch-label-group">
            <span class="ocw-business-badge">${this.escape(this.businessTypeCompactLabel(batch.business_type || firstItem.business_type))}</span>
            <span class="ocw-status ${this.escape(this.statusClass(batch.status))}">${this.escape(ocTranslate(statusInfo.label))}</span>
          </span>
          ${submittedAt ? `<small>${ocTranslate("提交时间 {0}", [this.escape(submittedAt)])}</small>` : ""}
          ${sampleBadge}
        </td>
        <td>${this.renderPurchaseApprovalMetric(batch.source_status || {})}</td>
        <td class="ocw-num-cell">${this.escape(String(itemCount))}</td>
        <td>${this.renderParentMetric(documentStatus)}</td>
        <td>${this.renderParentMetric(goodsValueDisplay)}</td>
        <td>${this.renderParentMetric(recognizedFeeDisplay)}</td>
        <td>${this.renderParentMetric(totalCostDisplay)}</td>
        <td>${this.renderParentMetric(voucherDiffDisplay)}</td>
        <td class="ocw-row-actions">
          <div class="ocw-row-action-group">
            ${this.workRole === "purchase"
              ? `<button class="ocw-outline-btn ocw-mini-btn" data-action="source-center" data-batch-name="${this.escape(batch.name)}">${ocTranslate("补资料")}</button>
                 <button class="ocw-outline-btn ocw-mini-btn" data-action="recalculate" data-batch-name="${this.escape(batch.name)}" ${recalculateDisabled}>${ocTranslate("试算")}</button>`
              : `<button class="ocw-outline-btn ocw-mini-btn" data-action="recalculate" data-batch-name="${this.escape(batch.name)}" ${recalculateDisabled}>${ocTranslate("重新试算")}</button>
                 <button class="ocw-outline-btn ocw-mini-btn" data-action="source-center" data-batch-name="${this.escape(batch.name)}">${ocTranslate("资料")}</button>`}
          </div>
        </td>
      </tr>
    `;
  }

  renderParentMetric(metric = {}) {
    const value = metric.value || "--";
    const hint = metric.hint ? `<small>${this.escape(ocTranslate(metric.hint))}</small>` : "";
    return `
      <div class="ocw-parent-metric ${this.escape(metric.className || "muted")}">
        <strong>${this.escape(value)}</strong>
        ${hint}
      </div>
    `;
  }

  renderPurchaseApprovalMetric(sourceStatus = {}) {
    const state = String(sourceStatus.purchase_approval_sync_state || "").trim().toLowerCase();
    const count = Number(sourceStatus.linked_purchase_count || 0);
    const reason = sourceStatus.invalid_business_reason || sourceStatus.purchase_approval_sync_message || "";
    if ((state === "invalid" || state === "excluded" || sourceStatus.invalid_business) && sourceStatus.has_oa_logistics) {
      return `
        <div class="ocw-parent-metric ocw-purchase-approval-metric is-missing" title="${this.escape(reason)}">
          <strong>${ocTranslate("资料来自国际物流审批")}</strong>
          <small>${ocTranslate("关联采购审批已排除")}</small>
        </div>
      `;
    }
    if (state === "invalid" || sourceStatus.invalid_business) {
      return `
        <div class="ocw-parent-metric ocw-purchase-approval-metric is-invalid" title="${this.escape(reason)}">
          <strong>${ocTranslate("采购审批无效")}</strong>
          <small>${ocTranslate("拒绝/撤销/终止")}</small>
        </div>
      `;
    }
    if (state === "pending") {
      return `
        <div class="ocw-parent-metric ocw-purchase-approval-metric is-pending" title="${this.escape(reason)}">
          <strong>${ocTranslate("状态未同步")}</strong>
          <small>${this.escape(ocTranslate("{0} 条已关联", [count]))}</small>
        </div>
      `;
    }
    if (state === "missing" && sourceStatus.has_oa_logistics) {
      return `
        <div class="ocw-parent-metric ocw-purchase-approval-metric is-missing" title="${this.escape(reason)}">
          <strong>${ocTranslate("资料来自国际物流审批")}</strong>
          <small>${ocTranslate("采购审批待关联")}</small>
        </div>
      `;
    }
    if (state === "valid") {
      return `
        <div class="ocw-parent-metric ocw-purchase-approval-metric is-valid" title="${this.escape(reason)}">
          <strong>${ocTranslate("已关联")}</strong>
          <small>${this.escape(ocTranslate("{0} 条已同步", [count]))}</small>
        </div>
      `;
    }
    return `<div class="ocw-parent-metric ocw-purchase-approval-metric is-muted"><strong>--</strong></div>`;
  }

  renderInvalidBusinessAlert(sourceStatus = {}) {
    const state = String(sourceStatus.purchase_approval_sync_state || "").trim().toLowerCase();
    if (!sourceStatus.invalid_business && state !== "invalid") return "";
    const reason = sourceStatus.invalid_business_reason || sourceStatus.purchase_approval_sync_message || "关联采购审批已拒绝/撤销/终止，不进入核算和 ERP 推送。";
    const linkedPurchaseExcluded = sourceStatus.invalid_business_scope === "linked_purchase_approval" && sourceStatus.has_oa_logistics;
    return `
      <div class="ocw-invalid-business-alert">
        <strong>${linkedPurchaseExcluded ? ocTranslate("关联采购审批已排除") : ocTranslate("采购审批无效")}</strong>
        <span>${this.escape(reason)}</span>
      </div>
    `;
  }

  renderPurchaseApprovalStatusAlert(sourceStatus = {}) {
    const state = String(sourceStatus.purchase_approval_sync_state || "").trim().toLowerCase();
    if (state === "invalid") return this.renderInvalidBusinessAlert(sourceStatus);
    if (state !== "pending" && state !== "missing") return "";
    const title = state === "pending" ? "采购审批状态未同步" : "采购审批待关联";
    return `
      <div class="ocw-invalid-business-alert is-info">
        <strong>${title}</strong>
        <span>${this.escape(sourceStatus.purchase_approval_sync_message || ocTranslate("采购审批状态尚未同步。"))}</span>
      </div>
    `;
  }

  isInvalidApprovalStatusText(value) {
    const text = String(value || "").trim().toLowerCase();
    if (!text) return false;
    return [
      "rejected",
      "refused",
      "denied",
      "canceled",
      "cancelled",
      "terminated",
      "aborted",
      "revoked",
      "拒绝",
      "驳回",
      "已拒",
      "撤销",
      "取消",
      "终止",
      "作废",
      "不做",
    ].some((word) => text.includes(word.toLowerCase()));
  }

  batchDocumentStatus(batch, items, hasLoadedItems) {
    const sourceStatus = batch.source_status || {};
    if (sourceStatus.invalid_business) {
      return {
        value: "审批无效",
        hint: sourceStatus.invalid_business_reason || ocTranslate("已拒绝/撤销/终止"),
        className: "danger",
      };
    }
    const itemCount = hasLoadedItems ? items.length : Number(batch.item_count || 0);
    const attachmentCount = Number(sourceStatus.oa_attachment_count || batch.source_attachment_count || 0);
    const packingListCount = Number(sourceStatus.packing_list_count || 0);
    const taxCertificateCount = Number(sourceStatus.tax_certificate_count || 0);
    const hasOaSource =
      Boolean(sourceStatus.has_oa_logistics) ||
      String(batch.source_type || "").trim() === "oa_logistics" ||
      this.hasText(batch.source_approval_no) ||
      this.hasText(batch.source_instance_id) ||
      this.hasText(batch.source_dingtalk_url);
    const parts = [];
    if (hasOaSource) parts.push("OA");
    if (attachmentCount) parts.push(ocTranslate("{0}附件", [attachmentCount]));
    if (packingListCount) parts.push("装箱单");
    if (itemCount) parts.push(`${itemCount}SKU`);
    if (taxCertificateCount) parts.push("凭证");

    if (!parts.length) {
      return { value: "待资料", hint: ocTranslate("未关联 OA/附件"), className: "warn" };
    }
    if (!itemCount) {
      return { value: "待解析", hint: parts.join(" / "), className: "warn" };
    }
    return {
      value: taxCertificateCount ? "资料较全" : "资料已读取",
      hint: parts.join(" / "),
      className: "ok",
    };
  }

  batchGoodsValueNumber(batch, items, hasLoadedItems) {
    const loadedValue = hasLoadedItems ? this.sumRowsNumber(items, "goods_value") : 0;
    const batchValue = Number(batch.total_goods_value || 0);
    return this.isPositive(loadedValue) ? loadedValue : batchValue;
  }

  batchTotalCostNumber(batch, items, hasLoadedItems) {
    if (batch.summary_snapshot?.calculation_schema === 2) return Number(batch.summary_snapshot.total_cost_rmb || 0);
    const loadedValue = hasLoadedItems ? this.sumRowsNumber(items, "total_cost_rmb") : 0;
    const batchValue = Number((batch.summary_snapshot?.calculation_schema === 2 ? batch.summary_snapshot.total_cost_rmb : batch.actual_total_cost_rmb || batch.estimated_total_cost_rmb) || 0);
    return this.isPositive(loadedValue) ? loadedValue : batchValue;
  }

  batchGoodsValueDisplay(batch, items, hasLoadedItems) {
    const itemCount = hasLoadedItems ? items.length : Number(batch.item_count || 0);
    const goodsValue = this.batchGoodsValueNumber(batch, items, hasLoadedItems);
    if (this.isPositive(goodsValue)) {
      return { value: `${this.formatMoney(goodsValue)} RMB`, hint: ocTranslate("来自采购/OA/明细"), className: "ok" };
    }
    if (itemCount) {
      return { value: "待补采购金额", hint: ocTranslate("缺少采购单价或采购金额"), className: "warn" };
    }
    return { value: "待物料", hint: ocTranslate("先生成 SKU"), className: "muted" };
  }

  batchRecognizedFeeDisplay(batch, items, hasLoadedItems) {
    const itemCount = hasLoadedItems ? items.length : Number(batch.item_count || 0);
    const goodsValue = this.batchGoodsValueNumber(batch, items, hasLoadedItems);
    const totalCost = this.batchTotalCostNumber(batch, items, hasLoadedItems);
    const feeFromTotal = this.isPositive(totalCost) && totalCost > goodsValue ? totalCost - goodsValue : 0;
    const fallbackFeeFields = [
      "freight_alloc_rmb",
      "mexico_customs_rmb",
      "mexico_inland_misc_rmb",
      "china_misc_rmb",
      "china_to_mexico_freight_rmb",
    ];
    const fallbackFee = hasLoadedItems
      ? fallbackFeeFields.reduce((total, fieldname) => total + this.sumRowsNumber(items, fieldname), 0)
      : 0;
    const recognizedFee = this.isPositive(feeFromTotal) ? feeFromTotal : fallbackFee;
    if (this.isPositive(recognizedFee)) {
      return { value: `${this.formatMoney(recognizedFee)} RMB`, hint: ocTranslate("已计入试算"), className: "ok" };
    }
    if (itemCount) {
      return { value: "待费用", hint: ocTranslate("等运费/税费/杂费"), className: "warn" };
    }
    return { value: "--", hint: ocTranslate("暂无明细"), className: "muted" };
  }

  batchTotalCostDisplay(batch, items, hasLoadedItems) {
    const itemCount = hasLoadedItems ? items.length : Number(batch.item_count || 0);
    const totalCost = this.batchTotalCostNumber(batch, items, hasLoadedItems);
    const statusInfo = this.batchStatusInfo(batch.status, batch, itemCount);
    if (statusInfo.needsRecalculate) {
      const hint = this.isPositive(totalCost) ? ocTranslate("当前 {0} RMB", [this.formatMoney(totalCost)]) : "明细已修改";
      return { value: "待重算", hint, className: "warn" };
    }
    if (this.isPositive(totalCost)) {
      return { value: `${this.formatMoney(totalCost)} RMB`, hint: ocTranslate("已生成"), className: "ok" };
    }
    if (itemCount) {
      return { value: "待试算", hint: ocTranslate("请先确认运费/税费/杂费"), className: "warn" };
    }
    return { value: "待物料", hint: ocTranslate("暂无 SKU"), className: "muted" };
  }

  batchVoucherDiffDisplay(batch) {
    const sourceStatus = batch.source_status || {};
    const reconciliation = sourceStatus.latest_tax_certificate_reconciliation || {};
    const taxCertificateCount = Number(sourceStatus.tax_certificate_count || 0);
    if (Object.keys(reconciliation).length) {
      const diff = Number(reconciliation.tax_total_diff_mxn);
      const statusLabel =
        reconciliation.manual_resolution_status_label || reconciliation.status_label || reconciliation.status || "已对账";
      if (Number.isFinite(diff)) {
        if (Math.abs(diff) <= 0.01) {
          return { value: "一致", hint: ocTranslate("0 MXN"), className: "ok" };
        }
        return {
          value: statusLabel,
          hint: `${this.formatMoney(diff)} MXN`,
          className: "review",
        };
      }
      return {
        value: statusLabel,
        hint: reconciliation.file_name || ocTranslate("已登记凭证"),
        className: String(statusLabel).includes("复核") ? "review" : "ok",
      };
    }
    if (taxCertificateCount) {
      return { value: "待对账", hint: ocTranslate("{0} 份凭证", [taxCertificateCount]), className: "warn" };
    }
    return { value: "待凭证", hint: ocTranslate("最终对账"), className: "muted" };
  }
