  addAudit(actor, type, textOrChange) {
    this.auditEvents.unshift({
      time: this.nowText(),
      actor,
      type,
      ...(typeof textOrChange === "object" && textOrChange !== null
        ? { text: textOrChange.text || "", change: textOrChange.change || textOrChange }
        : { text: textOrChange }),
    });
    this.renderAuditList();
  }
  async toggleBatch(batchName) {
    if (!batchName) return;
    this.activeBatchName = batchName;
    this.exportPinnedBatchName = batchName;
    this.dataCheckBatchName = batchName;
    if (this.expandedBatchNames.has(batchName)) {
      this.expandedBatchNames.delete(batchName);
    } else {
      const batch = this.findBatch(batchName);
      await this.loadBatchItems(batchName, batch ? batch.current_version : null);
      this.expandedBatchNames.add(batchName);
    }
    this.renderTable();
    this.renderDiffPanel();
  }

  async selectBatch(batchName) {
    if (!batchName) return;
    const batch = this.findBatch(batchName);
    if (!batch) return;
    this.activeBatchName = batch.name;
    this.exportPinnedBatchName = batch.name;
    this.dataCheckBatchName = batch.name;
    await this.loadAuditLogs(batch.name, batch.current_version);
    this.renderTable();
    this.renderDiffPanel();
    this.updateBatchUrl(this.batchUrlKey(batch), { view: "" });
  }

  async focusBatch(batchName, options = {}) {
    if (!batchName) return;
    const batch = this.findBatch(batchName);
    if (!batch) return;
    this.focusedBatchName = batch.name;
    this.activeBatchName = batch.name;
    this.exportPinnedBatchName = batch.name;
    this.dataCheckBatchName = batch.name;
    await Promise.all([
      this.loadBatchItems(batch.name, batch.current_version),
      this.loadAuditLogs(batch.name, batch.current_version),
    ]);
    this.expandedBatchNames.add(batch.name);
    this.renderTable();
    this.renderDiffPanel();
    if (options.updateUrl !== false) this.updateBatchUrl(this.batchUrlKey(batch), { view: "" });
  }

  clearBatchFocus(options = {}) {
    this.focusedBatchName = "";
    this.closeBatchDrawer({ updateUrl: false });
    this.renderTable();
    this.updateSearchResult();
    if (options.updateUrl !== false) this.updateBatchUrl("", { view: "" });
  }

  getBatchNameFromUrl() {
    try {
      return String(new URL(window.location.href).searchParams.get("batch") || "").trim();
    } catch (_error) {
      return "";
    }
  }

  getBatchViewFromUrl() {
    try {
      return String(new URL(window.location.href).searchParams.get("view") || "").trim();
    } catch (_error) {
      return "";
    }
  }

  updateBatchUrl(batchKey = "", options = {}) {
    const replace = Boolean(options && options.replace);
    const view = batchKey && options && options.view === "drawer" ? "drawer" : "";
    const url = new URL(window.location.href);
    const normalizedBatchKey = String(batchKey || "").trim();
    if (normalizedBatchKey) {
      url.searchParams.set("batch", normalizedBatchKey);
    } else {
      url.searchParams.delete("batch");
    }
    if (view) {
      url.searchParams.set("view", view);
    } else {
      url.searchParams.delete("view");
    }
    const nextUrl = `${url.pathname}${url.search}${url.hash}`;
    const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (nextUrl === currentUrl) return;
    const state = {
      ...(window.history.state || {}),
      overseasCostBatch: normalizedBatchKey,
      overseasCostView: view,
    };
    if (replace) {
      window.history.replaceState(state, "", nextUrl);
    } else {
      window.history.pushState(state, "", nextUrl);
    }
  }

  restoreBatchFocusStateFromUrl() {
    const batchKey = this.getBatchNameFromUrl();
    if (!batchKey) return null;
    const batch = this.findBatchByUrlKey(batchKey);
    if (!batch) {
      this.updateBatchUrl("", { replace: true });
      frappe.show_alert({ message: ocTranslate("链接中的批次不存在或已删除：{0}", [batchKey]), indicator: "orange" });
      return null;
    }
    if (batchKey !== this.batchUrlKey(batch)) {
      this.updateBatchUrl(this.batchUrlKey(batch), { replace: true, view: this.getBatchViewFromUrl() });
    }
    if (!this.visibleBatches.some((row) => row.name === batch.name)) {
      this.resetFilterValues();
      this.visibleBatches = this.batches.slice();
    }
    this.activeBatchName = batch.name;
    this.exportPinnedBatchName = batch.name;
    this.dataCheckBatchName = batch.name;
    this.focusedBatchName = batch.name;
    this.expandedBatchNames = new Set([batch.name]);
    return batch;
  }

  async applyBatchFocusFromUrl() {
    const batchKey = this.getBatchNameFromUrl();
    const view = this.getBatchViewFromUrl();
    if (!batchKey) {
      this.clearBatchFocus({ updateUrl: false });
      return;
    }
    const batch = this.findBatchByUrlKey(batchKey);
    if (!batch) {
      frappe.show_alert({ message: ocTranslate("链接中的批次不存在或已删除：{0}", [batchKey]), indicator: "orange" });
      return;
    }
    if (!this.visibleBatches.some((row) => row.name === batch.name)) {
      this.resetFilterValues();
      this.visibleBatches = this.batches.slice();
      this.renderTransportWorkbench();
    }
    await this.focusBatch(batch.name, { updateUrl: false });
    if (view === "drawer") {
      await this.openBatchDrawer(batch.name, { updateUrl: false });
    } else {
      this.closeBatchDrawer({ updateUrl: false });
    }
  }
