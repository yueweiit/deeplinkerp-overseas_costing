  requestParameters(args = {}) {
    const parameters = new URLSearchParams();
    Object.entries(args || {}).forEach(([key, value]) => {
      const encoded = value !== null && typeof value === "object" ? JSON.stringify(value) : value;
      parameters.set(key, encoded === null || encoded === undefined ? "" : String(encoded));
    });
    return parameters;
  }

  async requestJson(url, options = {}) {
    const method = String(options.method || "POST").toUpperCase();
    const parameters = this.requestParameters(options.args || {});
    const formData = options.formData || null;
    const headers = {
      "X-Frappe-CSRF-Token": frappe.csrf_token || "",
      "X-Requested-With": "XMLHttpRequest",
      Accept: "application/json",
      ...(options.headers || {}),
    };
    let requestUrl = url;
    const request = { method, credentials: "same-origin", headers };
    if (method === "GET") {
      const query = parameters.toString();
      if (query) requestUrl += `${requestUrl.includes("?") ? "&" : "?"}${query}`;
    } else if (formData) {
      request.body = formData;
    } else {
      request.body = parameters.toString();
      headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8";
    }
    const response = await fetch(requestUrl, request);
    const responseText = await response.text();
    let payload = {};
    try {
      payload = responseText ? JSON.parse(responseText) : {};
    } catch (_error) {
      payload = {};
    }
    if (!response.ok || payload.exc) {
      const serverMessage = payload.exception
        || payload.exc
        || (typeof payload.message === "string" ? payload.message : "")
        || response.statusText
        || `HTTP ${response.status}`;
      const error = new Error(serverMessage);
      error.status = response.status;
      error.statusText = response.statusText;
      error.responseJSON = payload;
      error.responseText = responseText;
      throw error;
    }
    return payload;
  }

  isReadOnlyRequest(method, options = {}) {
    if (options.readOnly === true || String(options.type || "").toUpperCase() === "GET") return true;
    if (options.businessWrite === true) return false;
    const action = String(method || "").split(".").pop().toLowerCase();
    return /^(get|list|search|preview|check|locate|export|find)_/.test(action);
  }

  isDeploymentTransportError(error) {
    return [0, 502, 503, 504].includes(Number(error?.status || 0));
  }

  workbenchReleaseBlockedError(message = "页面版本已失效，请刷新后继续使用。") {
    const error = new Error(message);
    error.workbenchReleaseBlocked = true;
    return error;
  }

  async call(method, args = {}, freeze = false, options = {}) {
    if (this.releaseBlocked && !String(method).endsWith(".get_workbench_release")) {
      throw this.workbenchReleaseBlockedError();
    }
    const type = String(options.type || "POST").toUpperCase();
    const request = () => this.requestJson(`/api/method/${method}`, { method: type, args });
    if (freeze) frappe.dom?.freeze?.();
    try {
      try {
        const payload = await request();
        return payload.message || {};
      } catch (error) {
        if (!this.isDeploymentTransportError(error) || String(method).endsWith(".get_workbench_release")) throw error;
        const releaseState = await this.checkWorkbenchRelease({ deploymentFailure: true });
        const deploymentUnavailable = releaseState?.deployment === true;
        if (this.releaseBlocked || deploymentUnavailable || !this.isReadOnlyRequest(method, options)) {
          if (this.releaseBlocked || deploymentUnavailable) error.workbenchReleaseHandled = true;
          throw error;
        }
        const payload = await request();
        return payload.message || {};
      }
    } finally {
      if (freeze) frappe.dom?.unfreeze?.();
    }
  }

  async uploadFileRequest(formData) {
    if (this.releaseBlocked) throw this.workbenchReleaseBlockedError();
    try {
      const payload = await this.requestJson("/api/method/upload_file", { method: "POST", formData });
      return payload.message || payload || {};
    } catch (error) {
      if (this.isDeploymentTransportError(error)) {
        const releaseState = await this.checkWorkbenchRelease({ deploymentFailure: true });
        if (this.releaseBlocked || releaseState?.deployment === true) error.workbenchReleaseHandled = true;
      }
      throw error;
    }
  }

  async checkWorkbenchRelease(options = {}) {
    if (options.deploymentFailure) this._releaseDeploymentFailureRequested = true;
    if (this._releaseCheckPromise) return this._releaseCheckPromise;
    const generation = Number(this._releaseMonitorGeneration || 0);
    const pending = (async () => {
      try {
        const payload = await this.requestJson(
          "/api/method/overseas_costing.api.workbench.get_workbench_release",
          { method: "GET" }
        );
        if (!this.isWorkbenchReleaseMonitorCurrent(generation)) return { cancelled: true };
        const result = payload.message || payload || {};
        const releaseId = String(result.release_id || "").trim();
        if (!releaseId) throw new Error(ocTranslate("发布标识为空"));
        if (this.adoptWorkbenchRelease(releaseId)) {
          return { changed: true, releaseId, reloading: true };
        }
        if (!this.initialReleaseId) {
          this.initialReleaseId = releaseId;
          return { changed: false, releaseId };
        }
        if (releaseId !== this.initialReleaseId) {
          this.releaseBlocked = true;
          this.releaseUpdating = false;
          this.dropCachedWorkbenchPage();
          this.showWorkbenchReleaseDialog("updated");
          return { changed: true, releaseId };
        }
        const wasUpdating = this.releaseUpdating;
        this.releaseUpdating = false;
        this.releaseBlocked = false;
        if (wasUpdating) this.hideWorkbenchReleaseDialog();
        return { changed: false, releaseId };
      } catch (error) {
        if (!this.isWorkbenchReleaseMonitorCurrent(generation)) return { cancelled: true };
        const deploymentFailure = options.deploymentFailure || this._releaseDeploymentFailureRequested;
        if (!deploymentFailure || !this.isDeploymentTransportError(error)) {
          return { unavailable: true, deployment: false, error };
        }
        this.releaseUpdating = true;
        this.releaseBlocked = true;
        this.showWorkbenchReleaseDialog("updating");
        this.scheduleWorkbenchReleaseCheck(3000);
        return { unavailable: true, deployment: true, error };
      }
    })();
    this._releaseCheckPromise = pending;
    try {
      return await pending;
    } finally {
      if (this._releaseCheckPromise === pending) {
        this._releaseCheckPromise = null;
        this._releaseDeploymentFailureRequested = false;
      }
    }
  }

  // Frappe 会把整段页面脚本缓存在 localStorage（`pageview.with_page` 里的
  // `_page:<name>`），刷新时优先命中它而不是回服务端取。部署换了版本却不失效
  // 这个键，刷新就还是跑旧脚本 —— 也就是“明明更新了，页面却没变化”的来源。
  cachedWorkbenchPageKey() {
    const pageName = String(window.page_name || "").trim() || "overseas-cost-workbench";
    return `_page:${pageName}`;
  }

  workbenchReleaseAnchorKey() {
    return "ocw_workbench_release";
  }

  readStoredWorkbenchRelease() {
    try {
      const value = window.localStorage?.getItem?.(this.workbenchReleaseAnchorKey());
      return value ? String(value).trim() : "";
    } catch (error) {
      return "";
    }
  }

  storeWorkbenchRelease(releaseId) {
    try {
      window.localStorage?.setItem?.(this.workbenchReleaseAnchorKey(), String(releaseId || ""));
    } catch (error) {
      // 存储不可用时忽略：只少了一层“重新打开页面自愈”，不影响本次校验。
    }
  }

  dropCachedWorkbenchPage() {
    try {
      window.localStorage?.removeItem?.(this.cachedWorkbenchPageKey());
    } catch (error) {
      // 同上：存储不可用时本来也命中不到这份缓存。
    }
  }

  reloadWorkbenchPage() {
    try {
      window.location?.reload?.();
    } catch (error) {
      // 受限环境下无法重载：保持页面可用，等下一次校验再试。
    }
  }

  /**
   * 确认当前这份脚本属于哪个发布标识。
   *
   * 页面重新打开时 `pageview.with_page` 会直接命中 localStorage 里的脚本缓存，
   * 根本不再问服务端，于是那份脚本永远认为自己是最新版。持久锚点就是为了识破
   * 这一点：锚点与当前发布标识不一致，说明跑的是上一次部署的脚本 —— 先失效
   * Page 缓存再重载，下一次拿到的才是新脚本。重载前先写锚点，避免来回重载。
   */
  adoptWorkbenchRelease(releaseId) {
    const stored = this.readStoredWorkbenchRelease();
    this.storeWorkbenchRelease(releaseId);
    if (!stored || stored === releaseId) return false;
    this.dropCachedWorkbenchPage();
    this.reloadWorkbenchPage();
    return true;
  }

  isWorkbenchReleaseMonitorCurrent(generation) {
    return this._releaseMonitorActive !== false
      && Number(this._releaseMonitorGeneration || 0) === Number(generation || 0);
  }

  isDeploymentErrorDialog(element) {
    if (!element?.classList?.contains?.("modal")) return false;
    if (element.classList.contains("ocw-release-modal") || element.classList.contains("ocw-error-modal")) return false;
    const text = String(element.textContent || "").replace(/\s+/g, " ").trim();
    return /(内部服务器错误|Internal Server Error|Server Error)/i.test(text);
  }

  dismissDeploymentErrorDialogs() {
    Array.from(document.querySelectorAll?.(".modal.show, .modal.in") || [])
      .filter((element) => this.isDeploymentErrorDialog(element))
      .forEach((element) => {
        const $dialog = $(element);
        if (typeof $dialog.modal === "function") $dialog.modal("hide");
        else $dialog.hide?.();
      });
  }

  async handlePotentialDeploymentDialog(event) {
    if (!this.isDeploymentErrorDialog(event?.target)) return;
    const state = await this.checkWorkbenchRelease({ deploymentFailure: true });
    if (this.releaseBlocked || state?.changed || state?.deployment === true) this.dismissDeploymentErrorDialogs();
  }

  showWorkbenchReleaseDialog(mode) {
    const updated = mode === "updated";
    this.dismissDeploymentErrorDialogs();
    if (!this._releaseDialog) {
      const dialog = new frappe.ui.Dialog({
        title: ocTranslate("系统版本更新"),
        fields: [{ fieldtype: "HTML", fieldname: "release_status" }],
        primary_action_label: ocTranslate("立即刷新"),
        primary_action: () => window.location.reload(),
      });
      dialog.show();
      dialog.$wrapper?.addClass?.("ocw-release-modal");
      dialog.$wrapper?.find?.(".btn-modal-close, .modal-header .close")?.hide?.();
      dialog.$wrapper?.off?.("hide.bs.modal.ocwRelease")?.on?.("hide.bs.modal.ocwRelease", (event) => {
        if (this.releaseBlocked) event.preventDefault();
      });
      this._releaseDialog = dialog;
    }
    const message = updated
      ? "系统已更新，请刷新后继续使用。"
      : "系统正在更新，请稍候。新版本就绪后即可刷新。";
    this._releaseDialog.fields_dict?.release_status?.$wrapper?.html?.(
      `<div class="ocw-release-message">${message}</div>`
    );
    this._releaseDialog.get_primary_btn?.().toggle?.(updated);
    this._releaseDialog.show?.();
  }

  hideWorkbenchReleaseDialog() {
    if (!this._releaseDialog) return;
    const dialog = this._releaseDialog;
    this._releaseDialog = null;
    dialog.hide?.();
  }

  scheduleWorkbenchReleaseCheck(delay = 60000) {
    if (this._releaseMonitorActive === false) return;
    const generation = Number(this._releaseMonitorGeneration || 0);
    window.clearTimeout(this._releaseCheckTimer);
    this._releaseCheckTimer = window.setTimeout(async () => {
      if (!this.isWorkbenchReleaseMonitorCurrent(generation)) return;
      if (document.visibilityState === "visible") await this.checkWorkbenchRelease();
      if (!this.isWorkbenchReleaseMonitorCurrent(generation)) return;
      this.scheduleWorkbenchReleaseCheck(this.releaseUpdating ? 3000 : 60000);
    }, delay);
  }

  async initializeWorkbenchRelease() {
    this.activateWorkbenchReleaseMonitor();
    await this.checkWorkbenchRelease();
    if (this._releaseMonitorActive === false) return;
    this.startWorkbenchReleaseMonitor();
  }

  activateWorkbenchReleaseMonitor() {
    if (this._releaseMonitorActive === true) return;
    this._releaseMonitorActive = true;
    this._releaseMonitorGeneration = Number(this._releaseMonitorGeneration || 0) + 1;
  }

  startWorkbenchReleaseMonitor() {
    this.activateWorkbenchReleaseMonitor();
    if (this._releaseFocusHandler) window.removeEventListener("focus", this._releaseFocusHandler);
    if (this._releaseVisibilityHandler) document.removeEventListener("visibilitychange", this._releaseVisibilityHandler);
    this._releaseFocusHandler = () => this.checkWorkbenchRelease();
    this._releaseVisibilityHandler = () => {
      if (document.visibilityState === "visible") this.checkWorkbenchRelease();
    };
    window.addEventListener("focus", this._releaseFocusHandler);
    document.addEventListener("visibilitychange", this._releaseVisibilityHandler);
    if (this._releaseModalHandler) $(document).off("shown.bs.modal.ocwReleaseGuard", this._releaseModalHandler);
    this._releaseModalHandler = (event) => {
      this.handlePotentialDeploymentDialog(event).catch(() => {});
    };
    $(document).on("shown.bs.modal.ocwReleaseGuard", this._releaseModalHandler);
    this.scheduleWorkbenchReleaseCheck(60000);
  }

  stopWorkbenchReleaseMonitor() {
    this._releaseMonitorActive = false;
    this._releaseMonitorGeneration = Number(this._releaseMonitorGeneration || 0) + 1;
    this._releaseCheckPromise = null;
    this._releaseDeploymentFailureRequested = false;
    window.clearTimeout(this._releaseCheckTimer);
    if (this._releaseFocusHandler) window.removeEventListener("focus", this._releaseFocusHandler);
    if (this._releaseVisibilityHandler) document.removeEventListener("visibilitychange", this._releaseVisibilityHandler);
    if (this._releaseModalHandler) $(document).off("shown.bs.modal.ocwReleaseGuard", this._releaseModalHandler);
  }

  resumeWorkbenchReleaseMonitor() {
    this.activateWorkbenchReleaseMonitor();
    this.startWorkbenchReleaseMonitor();
    return this.checkWorkbenchRelease();
  }
  async loadBatches() {
    this.setTableLoading();
    try {
      const result = await this.call("overseas_costing.api.batch.get_batch_list", this.getBatchListQueryArgs());
      this.batches = this.sortBatchesNewestFirst(result.items || []);
      this.visibleBatches = this.batches.slice();
      this.expandedBatchNames.clear();
      await this.prefetchBatchItems(this.visibleBatches);
      this.visibleBatches = this.filterBatches();
      const urlBatch = this.restoreBatchFocusStateFromUrl();
      this.renderTransportWorkbench();
      if (this.batches.length) {
        this.syncActiveSelectionWithVisible();
        const activeBatch = urlBatch || this.getVisibleActiveBatch();
        if (activeBatch) {
          await this.loadAuditLogs(activeBatch.name, activeBatch.current_version);
        } else {
          this.auditEvents = [];
          this.renderAuditList();
        }
        this.renderTable();
        this.updateSearchResult();
        this.updateRecalculateAction();
        if (urlBatch && this.getBatchViewFromUrl() === "drawer") {
          await this.openBatchDrawer(urlBatch.name, { updateUrl: false });
        }
      } else {
        this.renderEmpty();
      }
    } catch (error) {
      this.showError(error);
    }
  }

  async prefetchBatchItems(batches) {
    const targets = batches.filter((batch) => batch && batch.name && !this.batchItems[batch.name]);
    await Promise.all(targets.map((batch) => this.loadBatchItems(batch.name, batch.current_version)));
  }

  async loadBatchItems(batchName, versionName = null, force = false) {
    if (!batchName) return { columns: this.batchColumns, items: [] };
    if (!force && this.batchItems[batchName]) {
      return { columns: this.batchColumns, items: this.batchItems[batchName] };
    }
    const result = await this.call("overseas_costing.api.batch.get_batch_items", {
      batch_name: batchName,
      version_name: versionName,
      // 报关单号和运单号属于整票筛选条件。命中批次后仍加载完整物料，
      // 避免单号只在批次头时出现父级命中、明细为空。
      customs_no: "",
      waybill_no: "",
      material_code: this.filters.material_code,
      product_name: this.filters.product_name,
      import_name: this.filters.import_name,
      hs_code: this.filters.hs_code,
      category: this.filters.category,
    });
    this.batchColumns = result.columns || this.batchColumns || [];
    this.batchItems[batchName] = result.items || [];
    return { columns: this.batchColumns, items: this.batchItems[batchName] };
  }

  async loadAuditLogs(batchName = null, versionName = null) {
    const batch = batchName ? this.findBatch(batchName) : this.getActiveBatch();
    if (!batch) {
      this.auditEvents = [];
      this.renderAuditList();
      return;
    }
    const result = await this.call("overseas_costing.api.batch.get_audit_logs", {
      batch_name: batch.name,
      version_name: versionName || batch.current_version || null,
      limit: 80,
    });
    if (!result.ok) throw new Error(result.message || "修改记录加载失败");
    this.auditEvents = (result.items || []).map((row) => this.mapAuditRow(row, batch));
    this.renderAuditList();
  }

  recordUsage(actionType, options = {}) {
    const batch = options.batchName ? this.findBatch(options.batchName) : options.batch || null;
    const payload = {
      action_type: actionType || "OTHER",
      batch_name: options.batchName || (batch && batch.name) || "",
      version_name: options.versionName || (batch && batch.current_version) || "",
      status: options.status || "Success",
      remark: options.remark || "",
      route: window.location.hash || window.location.pathname || "",
      extra_json: JSON.stringify(options.extra || {}),
    };
    this.call("overseas_costing.api.usage.record_usage", payload, false, { businessWrite: true })
      .catch((error) => {
        console.warn("[overseas-cost-workbench] 使用记录写入失败", error);
      });
  }

  async loadUsageLogs(batchName = null) {
    const batch = batchName ? this.findBatch(batchName) : this.getActiveBatch();
    if (!batch) {
      this.usageEvents = [];
      this.usageSummary = null;
      if (this.drawerTab === "usage") this.renderBatchDrawer();
      return;
    }
    const [logs, summary] = await Promise.all([
      this.call("overseas_costing.api.usage.get_usage_logs", {
        batch_name: batch.name,
        limit: 80,
      }),
      this.call("overseas_costing.api.usage.get_usage_summary", {
        days: 30,
        limit: 12,
      }),
    ]);
    if (!logs.ok) throw new Error(logs.message || "使用记录加载失败");
    this.usageEvents = (logs.items || []).map((row) => this.mapUsageRow(row, batch));
    this.usageSummary = summary && summary.ok ? summary : null;
    if (this.drawerTab === "usage") this.renderBatchDrawer();
  }

  async applyFilters() {
    this.setTableLoading();
    try {
      this.focusedBatchName = "";
      this.closeBatchDrawer({ updateUrl: false });
      this.updateBatchUrl("", { replace: true });
      this.exportPinnedBatchName = "";
      this.batchItems = {};
      await this.reloadBatchesForServerSearch(true);
      if (!this.batches.length) {
        this.renderEmpty();
        return;
      }
      await this.prefetchBatchItems(this.batches);
      this.visibleBatches = this.filterBatches();
      this.renderTransportWorkbench();
      this.syncActiveSelectionWithVisible();
      if (this.hasActiveFilters()) {
        this.expandedBatchNames = new Set(this.visibleBatches.map((batch) => batch.name));
      }
      this.renderTable();
      this.updateSearchResult();
    } catch (error) {
      this.showError(error);
    }
  }

  clearFilters() {
    this.resetFilterValues();
    this.moreFiltersOpen = false;
    this.$root.find("[data-area='more-filters']").prop("hidden", true);
    this.$root.find("[data-action='toggle-more-filters']").attr("aria-expanded", "false").text(ocTranslate("更多筛选 +"));
    this.resetBatchScopeState();
    this.batchItems = {};
    this.loadBatches();
  }

  async loadBusinessEntityOptions() {
    try {
      const result = await this.call("overseas_costing.api.batch.get_batch_filter_options");
      this.businessEntityOptions = Array.isArray(result.items) ? result.items : [];
      this.businessTypeOptions = Array.isArray(result.business_types) && result.business_types.length
        ? result.business_types
        : this.selectOptions.business_type;
      this.renderBusinessEntityFilter();
      this.renderBusinessTypeFilter();
    } catch (error) {
      console.warn("[overseas-cost-workbench] 业务主体筛选选项加载失败", error);
    }
  }

  resetBatchScopeState() {
    this.focusedBatchName = "";
    this.exportPinnedBatchName = "";
    this.dataCheckBatchName = "";
    this.erpFlowBlockState = null;
    this.closeBatchDrawer({ updateUrl: false });
    this.updateBatchUrl("", { replace: true, view: "" });
  }

  async setTransportFilter(mode = "") {
    this.filters.business_type = String(mode || "").trim().toUpperCase();
    this.filters.transport_mode = "";
    this.resetBatchScopeState();
    if (!this.batches.length) {
      await this.loadBatches();
      return;
    }
    await this.prefetchBatchItems(this.batches);
    this.visibleBatches = this.filterBatches();
    this.syncActiveSelectionWithVisible();
    if (!this.visibleBatches.length) {
      this.auditEvents = [];
      this.renderAuditList();
    } else {
      const activeBatch = this.getVisibleActiveBatch();
      if (activeBatch) await this.loadAuditLogs(activeBatch.name, activeBatch.current_version);
    }
    this.renderTransportWorkbench();
    this.renderTable();
    this.updateSearchResult();
  }

  syncActiveSelectionWithVisible() {
    if (!this.visibleBatches.length) {
      this.activeBatchName = "";
      this.focusedBatchName = "";
      this.exportPinnedBatchName = "";
      this.dataCheckBatchName = "";
      return;
    }
    if (this.focusedBatchName && !this.visibleBatches.some((batch) => batch.name === this.focusedBatchName)) {
      this.focusedBatchName = "";
    }
    if (!this.visibleBatches.some((batch) => batch.name === this.activeBatchName)) {
      this.activeBatchName = this.visibleBatches[0].name;
    }
    if (this.exportPinnedBatchName && !this.visibleBatches.some((batch) => batch.name === this.exportPinnedBatchName)) {
      this.exportPinnedBatchName = "";
    }
    if (!this.visibleBatches.some((batch) => batch.name === this.dataCheckBatchName)) {
      this.dataCheckBatchName = this.activeBatchName;
    }
  }

  getServerSearchKeyword() {
    return [
      this.filters.customs_no,
      this.filters.waybill_no,
      this.filters.material_code,
      this.filters.product_name,
      this.filters.import_name,
      this.filters.hs_code,
      this.filters.category,
    ]
      .map((value) => String(value || "").trim())
      .find(Boolean) || "";
  }

  async reloadBatchesForServerSearch(force = false) {
    const keyword = this.getServerSearchKeyword();
    if (!keyword && !force) return false;
    const result = await this.call("overseas_costing.api.batch.get_batch_list", this.getBatchListQueryArgs(keyword));
    this.batches = this.sortBatchesNewestFirst(result.items || []);
    this.visibleBatches = this.batches.slice();
    return true;
  }

  resetFilterValues() {
    Object.keys(this.filters).forEach((key) => {
      this.filters[key] = "";
      this.$root.find(`[data-filter='${key}']`).val("");
    });
    Object.assign(this.filters, this.getDefaultPullDateRange());
    this.$root.find("[data-filter='start_date']").val(this.filters.start_date);
    this.$root.find("[data-filter='end_date']").val(this.filters.end_date);
    this.renderFilterChips();
  }

  getBatchListQueryArgs(keyword = "") {
    const dateRangeIsDefault = this.hasDefaultDateRange();
    return {
      transport_mode: "",
      business_type: this.filters.business_type || "",
      recent_days: this.defaultRecentDays,
      keyword,
      include_history: dateRangeIsDefault ? 1 : 0,
      start_date: dateRangeIsDefault ? "" : this.filters.start_date,
      end_date: dateRangeIsDefault ? "" : this.filters.end_date,
    };
  }

  hasDefaultDateRange() {
    const defaults = this.getDefaultPullDateRange();
    return this.filters.start_date === defaults.start_date && this.filters.end_date === defaults.end_date;
  }

  filterFieldLabels() {
    return {
      start_date: "开始日期",
      end_date: "结束日期",
      customs_no: "批次/报关单号/钉钉审批编号",
      material_code: "物料编码",
      product_name: "物料名称",
      import_name: "海关进口名称",
      hs_code: "海关分类编码",
      category: "大类",
      subsidiary_code: "业务主体",
      calculation_status: "核算状态",
      erp_status: "ERP 状态",
      transport_mode: "运输方式",
      business_type: "业务类型",
    };
  }

  filterChipEntries() {
    const labels = this.filterFieldLabels();
    const entries = [];
    const defaults = this.getDefaultPullDateRange();
    if (this.filters.start_date !== defaults.start_date || this.filters.end_date !== defaults.end_date) {
      entries.push({ field: "start_date", label: labels.start_date, value: this.filters.start_date || "未填" });
      entries.push({ field: "end_date", label: labels.end_date, value: this.filters.end_date || "未填" });
    }
    Object.keys(labels)
      .filter((field) => !["start_date", "end_date"].includes(field))
      .forEach((field) => {
        const value = String(this.filters[field] || "").trim();
        if (!value) return;
        entries.push({ field, label: labels[field], value: this.filterChipValue(field, value) });
      });
    return entries;
  }

  filterChipValue(field, value) {
    if (field === "business_type") return this.businessTypeCompactLabel(value);
    if (field === "erp_status") {
      return {
        not_started: "未开始",
        pending: "待接口推送",
        success: "推送成功",
        failed: "推送失败",
      }[value] || value;
    }
    if (field === "transport_mode") return this.transportLabel(value);
    return value;
  }

  renderFilterChips() {
    const $chips = this.$root && this.$root.find("[data-area='filter-chips']");
    if (!$chips || !$chips.length) return;
    const entries = this.filterChipEntries();
    if (!entries.length) {
      $chips.empty().prop("hidden", true);
      return;
    }
    $chips
      .prop("hidden", false)
      .html(entries.map((entry) => `
        <span class="ocw-filter-chip">
          <span class="ocw-filter-chip-label">${this.escape(ocTranslate(entry.label))}：${this.escape(entry.value)}</span>
          <button type="button" class="ocw-filter-chip-remove" data-action="remove-filter" data-filter="${this.escape(entry.field)}" aria-label="${this.escape(ocTranslate("移除{0}", [ocTranslate(entry.label)]))}">×</button>
        </span>
      `).join(""));
  }

  async removeFilter(field) {
    if (!field || !(field in this.filters)) return;
    if (field === "start_date" || field === "end_date") {
      Object.assign(this.filters, this.getDefaultPullDateRange());
      this.$root.find("[data-filter='start_date']").val(this.filters.start_date);
      this.$root.find("[data-filter='end_date']").val(this.filters.end_date);
    } else {
      this.filters[field] = "";
      this.$root.find(`[data-filter='${field}']`).val("");
    }
    this.renderFilterChips();
    await this.applyFilters();
  }

  toggleMoreFilters() {
    this.moreFiltersOpen = !this.moreFiltersOpen;
    const $button = this.$root.find("[data-action='toggle-more-filters']");
    this.$root.find("[data-area='more-filters']").prop("hidden", !this.moreFiltersOpen);
    $button.attr("aria-expanded", this.moreFiltersOpen ? "true" : "false").text(this.moreFiltersOpen ? ocTranslate("收起筛选 -") : ocTranslate("更多筛选 +"));
  }

  childPriorityStorageKey() {
    return "overseas-cost-workbench:sku-priority-fields";
  }

  normalizeChildPriorityFields(fields, columns = this.batchColumns) {
    const selected = Array.isArray(fields) ? fields.filter((field) => typeof field === "string" && field) : [];
    const unique = [...new Set(selected)];
    if (!columns || !columns.length) return unique;
    const allowed = new Set(columns.map((column) => column.fieldname));
    return unique.filter((field) => allowed.has(field));
  }

  loadChildPriorityFields() {
    try {
      return this.normalizeChildPriorityFields(JSON.parse(window.localStorage.getItem(this.childPriorityStorageKey()) || "[]"));
    } catch (error) {
      return [];
    }
  }

  saveChildPriorityFields(fields) {
    this.childPriorityFields = this.normalizeChildPriorityFields(fields);
    window.localStorage.setItem(this.childPriorityStorageKey(), JSON.stringify(this.childPriorityFields));
  }

  getChildDisplayColumns(columns = this.batchColumns) {
    const fixedFields = ["material_code", "product_name"];
    const byField = new Map(columns.map((column) => [column.fieldname, column]));
    const fixed = fixedFields.map((field) => byField.get(field)).filter(Boolean);
    const selected = this.normalizeChildPriorityFields(this.childPriorityFields, columns).filter((field) => !fixedFields.includes(field));
    const prioritized = selected.map((field) => byField.get(field)).filter(Boolean);
    const used = new Set([...fixedFields, ...selected]);
    return [...fixed, ...prioritized, ...columns.filter((column) => !used.has(column.fieldname))];
  }

  openChildColumnPreferenceDialog() {
    const columns = this.batchColumns || [];
    if (!columns.length) {
      frappe.show_alert({ message: ocTranslate("请先展开任意一个批次，再设置 SKU 明细重点字段。"), indicator: "orange" });
      return;
    }

    const fixedFields = new Set(["material_code", "product_name"]);
    const selected = new Set(this.normalizeChildPriorityFields(this.childPriorityFields, columns));
    const options = columns
      .map((column) => {
        const fixed = fixedFields.has(column.fieldname);
        const checked = fixed || selected.has(column.fieldname);
        const description = fixed ? "固定在最左侧" : "勾选后排在左侧";
        return `
          <label class="ocw-sku-priority-option ${fixed ? "is-fixed" : ""}">
            <input type="checkbox" data-sku-priority-field="${this.escape(column.fieldname)}"${checked ? " checked" : ""}${fixed ? " disabled" : ""} />
            <span>
              <strong>[${this.escape(column.excel_col)}] ${this.escape(ocTranslate(column.label))}</strong>
              <small>${description}</small>
            </span>
          </label>
        `;
      })
      .join("");

    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("设置 SKU 明细重点字段"),
      size: "extra-large",
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "sku_priority_fields",
          options: `
            <div class="ocw-sku-priority-note">${ocTranslate("勾选的字段会紧跟在“物料编码、产品名称”后面；未勾选字段不会隐藏，仍按原 Excel 顺序展示。此设置仅保存在当前浏览器。")}</div>
            <div class="ocw-sku-priority-grid">${options}</div>
          `,
        },
      ],
      primary_action_label: ocTranslate("保存"),
      primary_action: () => {
        const fields = dialog.$wrapper
          .find("[data-sku-priority-field]:checked")
          .map((_, input) => $(input).attr("data-sku-priority-field"))
          .get();
        this.saveChildPriorityFields(fields);
        dialog.hide();
        this.renderTable();
        frappe.show_alert({ message: ocTranslate("SKU 明细字段顺序已更新"), indicator: "green" });
      },
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-sku-priority-dialog");
  }
