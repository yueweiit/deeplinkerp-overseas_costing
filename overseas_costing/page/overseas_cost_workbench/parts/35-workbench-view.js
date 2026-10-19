  renderShell() {
    this.$root = $(`
      <div class="ocw-page ocw-redesign" data-screen="workbench">
        <main class="ocw-workbench-screen" data-area="workbench-screen">
          <header class="ocw-redesign-header">
            <div class="ocw-redesign-title">
              <button class="ocw-icon-btn ocw-nav-toggle" type="button" data-action="toggle-module-sidebar" aria-label="${ocTranslate('展开或收起导航')}" title="${ocTranslate('展开或收起导航')}">☰</button>
              <div>
                <h1>${ocTranslate("海外成本工作台")}</h1>
                <p>${ocTranslate("优先处理异常批次，也可搜索已知批次核对成本。")}</p>
              </div>
            </div>
            <div class="ocw-header-actions">
              <div class="ocw-menu-wrap">
                <button class="ocw-outline-btn" type="button" data-action="toggle-ingest-menu" aria-expanded="false">${ocTranslate("全局工具")}</button>
                <div class="ocw-ingest-menu" data-area="ingest-menu" hidden>
                  <button type="button" data-action="export-current">${ocTranslate("导出当前结果")}</button>
                  <button type="button" data-action="file-parse">${ocTranslate("凭证对比")}</button>
                  <button type="button" data-action="preview-categories">${ocTranslate("商品归类")}</button>
                  <button type="button" data-action="pull-oa-logistics">${ocTranslate("批量钉钉拉取")}</button>
                  <button type="button" data-action="open-import">${ocTranslate("批量 Excel 导入")}</button>
                </div>
              </div>
              <button class="ocw-primary-btn" type="button" data-action="add-batch">${ocTranslate("+ 新增批次")}</button>
            </div>
          </header>
          <nav class="ocw-task-tabs" data-area="task-tabs" aria-label="${ocTranslate('工作任务')}"></nav>
          <section class="ocw-search-panel" data-area="search-panel"></section>
          <section class="ocw-batch-list-panel" data-area="batch-list"></section>
        </main>
        <main class="ocw-detail-screen" data-area="detail-screen" hidden></main>
      </div>
    `);
    $(this.page.main).empty().append(this.$root);
    this.applyDeskLayout();
    this.applyModuleSidebarPreference();
    this.renderWorkbenchLoading();
  }

  moduleSidebarStorageKey() {
    return "overseas-cost-workbench:workspace-sidebar";
  }

  moduleSidebarCollapsed() {
    try {
      const saved = window.localStorage.getItem(this.moduleSidebarStorageKey());
      return saved ? saved === "collapsed" : this.currentModuleSidebarCollapsed();
    } catch (_error) {
      return this.currentModuleSidebarCollapsed();
    }
  }

  nativeModuleSidebar() {
    return $(".body-sidebar-container").first();
  }

  setModuleSidebarCollapsed(collapsed) {
    const $workspaceSidebar = this.nativeModuleSidebar();
    $("body").toggleClass("ocw-workspace-sidebar-collapsed", collapsed);
    if ($workspaceSidebar.length) $workspaceSidebar.css("display", collapsed ? "none" : "");
  }

  currentModuleSidebarCollapsed() {
    const $workspaceSidebar = this.nativeModuleSidebar();
    return $("body").hasClass("ocw-workspace-sidebar-collapsed") || Boolean($workspaceSidebar.length && !$workspaceSidebar.is(":visible"));
  }

  persistModuleSidebarPreference(collapsed) {
    try {
      window.localStorage.setItem(this.moduleSidebarStorageKey(), collapsed ? "collapsed" : "expanded");
    } catch (_error) {
      // 无本地存储权限时仅保留当前页面状态。
    }
  }

  applyModuleSidebarPreference() {
    if (!this._moduleSidebarSnapshot) {
      const $workspaceSidebar = this.nativeModuleSidebar();
      this._moduleSidebarSnapshot = {
        bodyHadClass: $("body").hasClass("ocw-workspace-sidebar-collapsed"),
        display: $workspaceSidebar.length ? $workspaceSidebar.get(0).style.display : null,
      };
    }
    this.setModuleSidebarCollapsed(this.moduleSidebarCollapsed());
  }

  toggleModuleSidebar() {
    const collapsed = !this.currentModuleSidebarCollapsed();
    this.setModuleSidebarCollapsed(collapsed);
    this.persistModuleSidebarPreference(collapsed);
  }

  restoreModuleSidebar() {
    if (!this._moduleSidebarSnapshot) return;
    const $workspaceSidebar = this.nativeModuleSidebar();
    if ($workspaceSidebar.length && this._moduleSidebarSnapshot.display !== null) {
      $workspaceSidebar.get(0).style.display = this._moduleSidebarSnapshot.display;
    }
    $("body").toggleClass("ocw-workspace-sidebar-collapsed", this._moduleSidebarSnapshot.bodyHadClass);
    this._moduleSidebarSnapshot = null;
  }

  closeActionMenus(event) {
    if (event && $(event.target).closest(".ocw-menu-wrap").length) return;
    this.$root
      .find("[data-area='ingest-menu'], [data-area='detail-tools']")
      .prop("hidden", true);
    this.$root
      .find("[data-action='toggle-ingest-menu'], [data-action='toggle-detail-tools']")
      .attr("aria-expanded", "false");
  }

  bindRedesignEvents() {
    $(document)
      .off("click.ocwActionMenus")
      .on("click.ocwActionMenus", (event) => {
        if (!$(this.wrapper).is(":visible")) return;
        this.closeActionMenus(event);
      });
    $(window)
      .off("beforeunload.ocwDetailEdit")
      .on("beforeunload.ocwDetailEdit", (event) => {
        if (!this.detailState?.dirty && !this.hasUnsavedReviewDrafts?.()) return undefined;
        event.preventDefault();
        event.returnValue = "";
        return "";
      });
    this.$root.on("click", "[data-action='toggle-module-sidebar']", () => this.toggleModuleSidebar());
    this.$root.on("click", "[data-action='toggle-ingest-menu']", (event) => {
      const $button = $(event.currentTarget);
      const $menu = this.$root.find("[data-area='ingest-menu']");
      const willOpen = $menu.prop("hidden");
      $menu.prop("hidden", !willOpen);
      $button.attr("aria-expanded", String(willOpen));
    });
    this.$root.on("click", "[data-action='set-task']", (event) => {
      this.viewState.task = $(event.currentTarget).attr("data-task") || "pending";
      this.filters.issue = "";
      this.filters.review_warning = "";
      this.viewState.page = 1;
      this.replaceViewState({ task: this.viewState.task, issue: "", review_warning: "", page: 1 });
      this.loadBatches();
    });
    this.$root.on("input", "[data-workbench-filter='q']", (event) => {
      this.viewState.q = $(event.currentTarget).val() || "";
    });
    this.$root.on("keydown", "[data-workbench-filter='q']", (event) => {
      if (event.key === "Enter") this.applyFilters();
    });
    this.$root.on("change", "[data-workbench-filter]", (event) => {
      const field = $(event.currentTarget).attr("data-workbench-filter");
      if (field === "q") return;
      if (field === "review_status") return this.setReviewFilter($(event.currentTarget).val());
      this.filters[field] = $(event.currentTarget).val() || "";
    });
    this.$root.on("click", "[data-action='workbench-page']", (event) => {
      this.viewState.page = Math.max(1, Number($(event.currentTarget).attr("data-page") || 1));
      this.replaceViewState({ page: this.viewState.page });
      this.loadBatches();
    });
    this.$root.on("click", "[data-action='open-batch-detail']", (event) => {
      this.openBatchDetail($(event.currentTarget).attr("data-batch-name"), "documents");
    });
    this.$root.on("click", "[data-action='toggle-result-preview']", (event) => {
      event.preventDefault();
      event.stopPropagation();
      this.toggleBatchResultPreview($(event.currentTarget).attr("data-batch-name"));
    });
    this.$root.on("click", "[data-action='result-preview-page']", (event) => {
      const $button = $(event.currentTarget);
      const batchName = $button.attr("data-batch-name") || this.resultPreviewState.batchName;
      const page = Math.max(1, Number($button.attr("data-page") || 1));
      this.loadBatchResultPreview(batchName, page);
    });
    this.$root.on("click", "[data-action='retry-result-preview']", () => {
      if (!this.resultPreviewState.batchName) return;
      this.loadBatchResultPreview(this.resultPreviewState.batchName, this.resultPreviewState.page, { force: true });
    });
    this.$root.on("click", "[data-action='workbench-primary']", async (event) => {
      const batchName = $(event.currentTarget).attr("data-batch-name");
      const action = $(event.currentTarget).attr("data-primary-action");
      if (action === "review_remediation") return this.openBatchDetail(batchName, "review");
      if (["supplement", "supplement_fees", "supplement_allocation"].includes(action)) return this.openBatchDetail(batchName, "documents");
      if (action === "review") return this.openBatchDetail(batchName, "documents");
      if (action === "recalculate") return this.recalculate(batchName);
      return this.openBatchDetail(batchName, OverseasCostWorkbenchState.detailTabForAction(action));
    });
    this.$root.on("click", "[data-action='return-workbench']", () => this.returnToWorkbench());
    this.$root.on("click", "[data-action='retry-detail']", () =>
      this.openBatchDetail(this.detailState.batchName, this.detailState.tab, { updateUrl: false })
    );
    this.$root.on("click", "[data-action='retry-detail-tab']", () => this.switchDetailTab(this.detailState.tab, { updateUrl: false }));
    this.$root.on("click", "[data-action='switch-detail-tab']", (event) =>
      this.switchDetailTab($(event.currentTarget).attr("data-tab"))
    );
    this.$root.on("click", "[data-action='view-dingtalk-approval']", () => this.switchDetailTab("dingtalk"));
    this.$root.on("click", "[data-action='repair-dingtalk-approval']", (event) =>
      this.requestDingtalkApprovalRepair($(event.currentTarget)).catch((error) => this.showError(error))
    );
    this.$root.on("click", "[data-action='open-packing-flow']", () =>
      this.openPackingFlowDialog().catch((error) => this.showError(error))
    );
    this.$root.on("click", "[data-action='open-packing-comparison-history']", () =>
      this.openPackingComparisonHistory().catch((error) => this.showError(error))
    );
    this.$root.on("click", "[data-action='download-dingtalk-attachment']", (event) => {
      const $button = $(event.currentTarget);
      this.downloadDingtalkAttachmentFromDetail(
        $button.attr("data-attachment-name"), $button,
        $button.attr("data-process-instance-id"), $button.attr("data-file-id")
      ).catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='preview-dingtalk-attachment']", (event) => {
      const $button = $(event.currentTarget);
      this.previewDingtalkAttachmentFromDetail(
        $button.attr("data-attachment-name"),
        $button.attr("data-file-url"),
        $button.attr("data-file-name"),
        $button,
        $button.attr("data-process-instance-id"),
        $button.attr("data-file-id")
      ).catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='use-dingtalk-packing-source']", (event) => {
      const $button = $(event.currentTarget);
      this.openPackingFlowFromDingtalk(
        $button.attr("data-source-kind"), $button.attr("data-source-id"),
        $button.attr("data-process-instance-id"), $button.attr("data-file-id")
      ).catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='toggle-detail-tools']", (event) => {
      const $button = $(event.currentTarget);
      const $menu = this.$root.find("[data-area='detail-tools']");
      const willOpen = $menu.prop("hidden");
      $menu.prop("hidden", !willOpen);
      $button.attr("aria-expanded", String(willOpen));
    });
    this.$root.on("click", "[data-action='detail-primary']", (event) => {
      const action = $(event.currentTarget).attr("data-primary-action");
      if (action === "supplement") return this.switchDetailTab("documents");
      if (action === "recalculate") return this.startDetailCostTrial().catch((error) => this.showError(error));
      return this.switchDetailTab(OverseasCostWorkbenchState.detailTabForAction(action));
    });
    this.$root.on("click", "[data-action='detail-export']", () => this.exportDrawerBatch().catch((error) => this.showError(error)));
    this.$root.on("click", "[data-action='detail-voucher']", () => this.openFileParseDialog(this.detailState.batchName));
    this.$root.on("click", "[data-action='detail-category']", () => this.openCategoryPreviewDialog(this.detailState.batchName));
    this.$root.on("click", "[data-action='detail-dingtalk']", () => this.openDingtalkOrder(this.detailState.batchName));
    this.$root.on("click", "[data-action='detail-excel']", () => this.openBatchExcelSupplementDialog(this.detailState.batchName));
    this.$root.on("click", "[data-action='open-voucher-record']", (event) =>
      this.openTaxCertificateRecordDialog($(event.currentTarget).attr("data-record-name"))
    );
    this.$root.on("click", "[data-action='ai-review-voucher']", (event) => {
      event.preventDefault();
      return this.startVoucherFeeEvidenceReview($(event.currentTarget), {
        fallbackBatchName: String(this.detailState.batchName || ""),
      });
    });
    this.$root.on("click", "[data-action='manual-fill-gap']", (event) => {
      const $button = $(event.currentTarget);
      this.openManualGapFillDialog(this.getDetailBatch(), {
        fieldname: $button.attr("data-gap-fieldname") || "",
        label: $button.attr("data-gap-label") || $button.attr("data-slot-label") || "",
        slotCode: $button.attr("data-slot-code") || "",
        slotCodes: [$button.attr("data-slot-code") || ""].filter(Boolean),
        slotLabel: $button.attr("data-slot-label") || "",
        attachmentType: $button.attr("data-attachment-type") || "Other",
        required: $button.attr("data-required") === "1",
        logisticsType: $button.attr("data-logistics-type") || this.detectManualDocumentLogisticsType(this.getDetailBatch()),
      }, this.detailDocumentAdapter());
    });
    this.$root.on("click", "[data-action='upload-manual-document']", (event) => {
      const $button = $(event.currentTarget);
      this.openManualDocumentUploader(
        this.getDetailBatch(),
        this.detailDocumentAdapter(),
        $button.attr("data-logistics-type"),
        {
          code: $button.attr("data-slot-code"),
          label: $button.attr("data-slot-label"),
          attachmentType: $button.attr("data-attachment-type"),
          required: $button.attr("data-required") === "1",
        }
      );
    });
    this.$root.on("click", "[data-action='preview-manual-document']", (event) => {
      const $button = $(event.currentTarget);
      this.openOaAttachmentFilePreviewDialog($button.attr("data-file-url"), $button.attr("data-file-name"));
    });
    this.$root.on("click", "[data-action='download-manual-document']", (event) => {
      const $button = $(event.currentTarget);
      this.downloadFileToLocal($button.attr("data-file-url"), $button.attr("data-file-name"));
    });
    this.$root.on("click", "[data-action='delete-manual-document']", (event) => {
      const $button = $(event.currentTarget);
      this.deleteManualDocumentAttachment(
        this.getDetailBatch(),
        this.detailDocumentAdapter(),
        $button.attr("data-attachment-name"),
        $button.attr("data-logistics-type")
      ).catch((error) => this.showError(error));
    });
    this.$root.on("keydown", "[data-role='sku-keyword']", (event) => {
      if (event.key !== "Enter") return;
      this.detailState.sku.keyword = $(event.currentTarget).val() || "";
      this.detailState.sku.page = 1;
      this.loadSkuPage();
    });
    this.$root.on("click", "[data-action='sku-group']", (event) => {
      this.detailState.sku.fieldGroup = $(event.currentTarget).attr("data-field-group") || "basic";
      this.detailState.sku.page = 1;
      this.loadSkuPage();
    });
    this.$root.on("click", "[data-action='sku-page']", (event) => {
      this.detailState.sku.page = Math.max(1, Number($(event.currentTarget).attr("data-page") || 1));
      this.loadSkuPage();
    });
    this.$root.on("click", "[data-action='sku-sort']", (event) => {
      const sortBy = $(event.currentTarget).attr("data-sort-by") || "row_no";
      this.detailState.sku.sortOrder = this.detailState.sku.sortBy === sortBy && this.detailState.sku.sortOrder === "asc" ? "desc" : "asc";
      this.detailState.sku.sortBy = sortBy;
      this.detailState.sku.page = 1;
      this.loadSkuPage();
    });
  }

  async startDetailCostTrial() {
    const batchName = String(this.detailState?.batchName || "");
    if (!batchName || this.viewState?.screen !== "detail") return;
    if (this.detailState.tab !== "documents") await this.switchDetailTab("documents");
    const rootScreen = this.$root?.attr?.("data-screen");
    if (
      this.viewState?.screen !== "detail"
      || (rootScreen && rootScreen !== "detail")
      || this.detailState?.batchName !== batchName
      || (this.viewState?.batch && this.viewState.batch !== batchName)
      || this.detailState?.tab !== "documents"
    ) return;
    return this.refreshMaterialFeeCostPreview(true);
  }

  replaceViewState(values, { push = false } = {}) {
    const nextUrl = OverseasCostWorkbenchState.buildWorkbenchUrl(window.location.href, values);
    const state = { ...(window.history.state || {}), overseasCostWorkbench: true };
    window.history[push ? "pushState" : "replaceState"](state, "", nextUrl);
    this.viewState = OverseasCostWorkbenchState.parseWorkbenchState(window.location.href);
  }

  workbenchFilters() {
    return {
      keyword: this.viewState.q || "",
      issue: this.filters.issue || "",
      business_type: this.filters.business_type || "",
      subsidiary_code: this.filters.subsidiary_code || "",
      start_date: this.filters.start_date || "",
      end_date: this.filters.end_date || "",
      erp_status: this.filters.erp_status || "",
      calculation_status: this.filters.calculation_status || "",
      review_status: this.filters.review_status || "pending",
      review_warning: this.filters.review_warning || "",
      include_history: 1,
    };
  }

  syncWorkbenchFiltersToUrl() {
    this.replaceViewState({
      q: this.viewState.q || "",
      issue: this.filters.issue || "",
      business_type: this.filters.business_type || "",
      subsidiary_code: this.filters.subsidiary_code || "",
      start_date: this.filters.start_date || "",
      end_date: this.filters.end_date || "",
      erp_status: this.filters.erp_status || "",
      review_status: this.filters.review_status || "pending",
      review_warning: this.filters.review_warning || "",
      page: this.viewState.page || 1,
    });
  }

  async applyFilters() {
    this.viewState.page = 1;
    this.syncWorkbenchFiltersToUrl();
    await this.loadBatches();
  }

  async setReviewFilter(key) {
    if (["pending", "confirmed"].includes(key)) {
      this.filters.review_status = key;
      this.filters.review_warning = "";
    } else if (["estimated", "evidence_missing"].includes(key)) {
      this.filters.review_warning = this.filters.review_warning === key ? "" : key;
    } else return;
    await this.applyFilters();
  }

  clearFilters() {
    const range = this.getDefaultPullDateRange();
    Object.assign(this.filters, {
      start_date: range.start_date,
      end_date: range.end_date,
      business_type: "",
      subsidiary_code: "",
      erp_status: "",
      calculation_status: "",
      issue: "",
      review_status: "pending",
      review_warning: "",
    });
    this.viewState.q = "";
    this.viewState.page = 1;
    this.syncWorkbenchFiltersToUrl();
    this.loadBatches();
  }

  async loadBatches() {
    const requestId = this._workbenchRequestId = (this._workbenchRequestId || 0) + 1;
    const task = this.viewState.task;
    const filtersJson = JSON.stringify(this.workbenchFilters());
    this.resetBatchResultPreview({ clearCache: true, render: false });
    this.renderWorkbenchLoading();
    try {
      const [list, summary] = await Promise.all([
        this.call("overseas_costing.api.workbench.get_batches", {
          filters_json: filtersJson,
          task,
          page: this.viewState.page,
          page_length: 30,
        }),
        this.call("overseas_costing.api.workbench.get_summary", {
          filters_json: filtersJson,
          task,
        }),
      ]);
      if (requestId !== this._workbenchRequestId) return;
      if (!list.ok) throw new Error(list.message || "工作台批次加载失败");
      this.batches = list.items || [];
      this.visibleBatches = this.batches.slice();
      this.workbenchTotal = Number(list.total || 0);
      this.exceptionCounts = (summary && summary.counts) || {};
      this.reviewCounts = (summary && summary.review_counts) || {};
      this.viewState.page = Number(list.page || this.viewState.page || 1);
      this.renderWorkbench();
      if (this.viewState.screen === "detail" && this.viewState.batch) {
        await this.openBatchDetail(this.viewState.batch, this.viewState.tab, { updateUrl: false });
      }
    } catch (error) {
      if (requestId !== this._workbenchRequestId) return;
      this.renderWorkbenchError(error);
    }
  }

  renderWorkbenchLoading() {
    if (!this.$root) return;
    this.$root.find("[data-area='batch-list']").html(`
      <div class="ocw-state-panel"><span class="ocw-spinner"></span><strong>${ocTranslate("正在加载工作台")}</strong></div>
    `);
  }

  renderWorkbenchError(error) {
    this.$root.find("[data-area='batch-list']").html(`
      <div class="ocw-state-panel is-error">
        <strong>${ocTranslate("工作台加载失败")}</strong>
        <span>${this.escape(this.normalizeErrorMessage(error))}</span>
        <button class="ocw-outline-btn" type="button" data-action="reload-batches">${ocTranslate("重新加载")}</button>
      </div>
    `);
  }

  renderWorkbench() {
    this.renderTaskTabs();
    this.renderWorkbenchSearch();
    this.renderWorkbenchBatchList();
    this.$root.attr("data-screen", "workbench");
  }

  renderTaskTabs() {
    const tasks = [
      { key: "pending", label: ocTranslate("待处理"), hint: ocTranslate("优先处理资料与计算异常") },
      { key: "cost", label: ocTranslate("成本核对"), hint: ocTranslate("核对当前已完成分摊的结果") },
      { key: "erp", label: ocTranslate("ERP 队列"), hint: ocTranslate("处理待推送与失败记录") },
    ];
    this.$root.find("[data-area='task-tabs']").html(
      tasks.map((task) => `
        <button class="ocw-task-tab ${this.viewState.task === task.key ? "is-active" : ""}" type="button" data-action="set-task" data-task="${task.key}">
          <strong>${task.label}</strong><span>${task.hint}</span>
        </button>
      `).join("")
    );
  }

  renderWorkbenchSearch() {
    const businessOptions = this.businessTypeOptions.length ? this.businessTypeOptions : this.selectOptions.business_type;
    const entityOptions = this.businessEntityOptions || [];
    this.$root.find("[data-area='search-panel']").html(`
      <div class="ocw-search-row">
        <label class="ocw-search-main"><span>${ocTranslate("查找批次或物料")}</span><input class="form-control" type="search" data-workbench-filter="q" value="${this.escape(this.viewState.q)}" placeholder="${ocTranslate('搜索批次号、运单号、物流单号或物料编码')}" /></label>
        <label><span>${ocTranslate("开始日期")}</span><input class="form-control" type="date" data-workbench-filter="start_date" value="${this.escape(this.filters.start_date)}" /></label>
        <label><span>${ocTranslate("结束日期")}</span><input class="form-control" type="date" data-workbench-filter="end_date" value="${this.escape(this.filters.end_date)}" /></label>
        <button class="ocw-primary-btn" type="button" data-action="apply-filters">${ocTranslate("查询")}</button>
        <button class="ocw-outline-btn" type="button" data-action="clear-filters">${ocTranslate("重置")}</button>
      </div>
      <div class="ocw-secondary-filter-row">
        ${this.viewState.task === "cost" ? `<label><span>${ocTranslate("核对状态")}</span><select class="form-control" data-workbench-filter="review_status">
          <option value="pending" ${this.filters.review_status !== "confirmed" ? "selected" : ""}>${ocTranslate("待核对")}</option>
          <option value="confirmed" ${this.filters.review_status === "confirmed" ? "selected" : ""}>${ocTranslate("已核对")}</option>
        </select></label>` : ""}
        <label><span>${ocTranslate("业务类型")}</span><select class="form-control" data-workbench-filter="business_type" data-filter="business_type">
          <option value="">${ocTranslate("全部业务类型")}</option>
          ${businessOptions.map((option) => {
            const value = typeof option === "string" ? option : option.value;
            const label = typeof option === "string" ? this.businessTypeLabel(option) : option.label;
            return `<option value="${this.escape(value)}" ${this.filters.business_type === value ? "selected" : ""}>${this.escape(ocTranslate(label))}</option>`;
          }).join("")}
        </select></label>
        <label><span>${ocTranslate("业务主体")}</span><select class="form-control" data-workbench-filter="subsidiary_code" data-filter="subsidiary_code">
          <option value="">${ocTranslate("全部业务主体")}</option>
          ${entityOptions.map((value) => `<option value="${this.escape(value)}" ${this.filters.subsidiary_code === value ? "selected" : ""}>${this.escape(value)}</option>`).join("")}
        </select></label>
        <label><span>${ocTranslate("ERP 状态")}</span><select class="form-control" data-workbench-filter="erp_status">
          <option value="">${ocTranslate("全部 ERP 状态")}</option>
          <option value="not_started" ${this.filters.erp_status === "not_started" ? "selected" : ""}>${ocTranslate("未推送")}</option>
          <option value="pending" ${this.filters.erp_status === "pending" ? "selected" : ""}>${ocTranslate("待推送")}</option>
          <option value="failed" ${this.filters.erp_status === "failed" ? "selected" : ""}>${ocTranslate("推送失败")}</option>
          <option value="success" ${this.filters.erp_status === "success" ? "selected" : ""}>${ocTranslate("推送成功")}</option>
        </select></label>
        <span class="ocw-result-copy">${ocTranslate("共 {0} 个批次{1}", [this.workbenchTotal, this.viewState.task === "cost" && this.filters.review_warning ? " · 已按核对提示筛选" : this.viewState.task === "pending" && this.filters.issue ? " · 已按异常筛选" : ""])}</span>
      </div>
    `);
  }

  issueLabel(issue) {
    return {
      purchase: ocTranslate("采购资料不完整"),
      logistics: ocTranslate("物流资料不完整"),
      calculation: ocTranslate("成本待计算"),
      erp_failed: ocTranslate("ERP 回写失败"),
      ready: ocTranslate("可核对"),
    }[issue] || ocTranslate("待核对");
  }

  workbenchRowStatusLabel(batch) {
    if (String(batch.remediation_state || "").toLowerCase() === "returned") return ocTranslate("待整改");
    if (String(batch.remediation_state || "").toLowerCase() === "resubmitted") return ocTranslate("待财务复核");
    if (batch.review_state === "confirmed") return ocTranslate("已核对");
    if (batch.review_state === "ready") return ocTranslate("可核对");
    return {
      purchase: ocTranslate("资料待补"),
      logistics: ocTranslate("资料待补"),
      calculation: ocTranslate("待重新计算"),
      erp_failed: ocTranslate("ERP 回写失败"),
    }[batch.primary_issue] || ocTranslate("待核对");
  }

  workbenchReviewTagLabel(code) {
    const labels = {
      MATERIAL_ITEMS_REQUIRED: ocTranslate("采购资料待补"),
      GOODS_VALUE_MISSING: ocTranslate("采购资料待补"),
      PURCHASE_SOURCE_INVALID: ocTranslate("采购资料待补"),
      SOURCE_ADOPTION_PENDING: ocTranslate("采购资料待采用"),
      TRANSPORT_MODE_REQUIRED: ocTranslate("运输方式待确认"),
      AMOUNT_MISSING: ocTranslate("费用金额待补"),
      AMOUNT_STATUS_INVALID: ocTranslate("费用状态待确认"),
      FEE_AMOUNT_INVALID: ocTranslate("费用金额待核对"),
      DUPLICATE_LOGICAL_FEE: ocTranslate("重复费用待处理"),
      FX_RATE_MISSING: ocTranslate("费用汇率待补"),
      CURRENCY_UNSUPPORTED: ocTranslate("费用币种待核对"),
      ESTIMATED_AMOUNT: ocTranslate("实际费用待确认"),
      EVIDENCE_MISSING: ocTranslate("费用凭证未齐"),
      FINAL_FEE_REVIEW_REQUIRED: ocTranslate("费用待核对"),
      ALLOCATION_REQUIRED: ocTranslate("分摊资料待补"),
      SHIPPING_UNIT_REQUIRED: ocTranslate("分摊资料待补"),
      ALLOCATION_BASIS_INCOMPLETE: ocTranslate("分摊资料待补"),
      ALLOCATION_DENOMINATOR_ZERO: ocTranslate("分摊资料待补"),
      FEE_SCOPE_EMPTY: ocTranslate("分摊资料待补"),
      FEE_SCOPE_INVALID: ocTranslate("分摊资料待补"),
      DIRECT_ITEM_SCOPE_INVALID: ocTranslate("分摊资料待补"),
      STABLE_ITEM_KEY_REQUIRED: ocTranslate("分摊资料待补"),
      STABLE_ITEM_KEY_DUPLICATED: ocTranslate("分摊资料待补"),
      TEMPORARY_ALLOCATION_BASIS: ocTranslate("分摊资料待补"),
      RESULT_NOT_SAVED: ocTranslate("待重新计算"),
      RESULT_LEGACY: ocTranslate("待重新计算"),
      RESULT_STALE: ocTranslate("待重新计算"),
      SAVED_RESULT_INVALID: ocTranslate("待重新计算"),
      STALE: ocTranslate("待重新计算"),
      purchase: ocTranslate("采购资料待补"),
      logistics: ocTranslate("分摊资料待补"),
      calculation: ocTranslate("待重新计算"),
      erp_failed: ocTranslate("ERP 回写待处理"),
    };
    return labels[String(code || "")] || ocTranslate("其他问题待处理");
  }

  renderWorkbenchReviewTags(batch) {
    const entries = [
      ...(batch.review_blockers || []).map((row) => ({...row, tone: "danger"})),
      ...(batch.review_warnings || []).map((row) => ({...row, tone: "warn"})),
    ];
    if (!entries.length) {
      entries.push(...(batch.issue_codes || []).map((code) => ({code, tone: "neutral"})));
    }
    const unique = [];
    const seen = new Set();
    entries.forEach((row) => {
      const label = this.workbenchReviewTagLabel(row.code);
      if (seen.has(label)) return;
      seen.add(label);
      unique.push({label, tone: row.tone});
    });
    if (!unique.length) return "";
    const visible = unique.slice(0, 3);
    const remaining = unique.length - visible.length;
    return `<div class="ocw-review-tags" aria-label="${ocTranslate('状态摘要')}">${visible.map((row) => `<span class="ocw-review-tag is-${this.escape(row.tone)}">${this.escape(ocTranslate(row.label))}</span>`).join("")}${remaining ? `<span class="ocw-review-tag is-more">${ocTranslate("另有 {0} 项", [remaining])}</span>` : ""}</div>`;
  }

  resultPreviewCacheKey(batchName, page) {
    const batch = (this.batches || []).find((row) => row.name === batchName);
    const currentVersion = batch?.current_version || "current";
    return `${batchName}:${currentVersion}:${Math.max(1, Number(page || 1))}`;
  }

  cleanupResultPreviewScrollControls() {
    if (this._resultPreviewScrollCleanup) this._resultPreviewScrollCleanup();
    this._resultPreviewScrollCleanup = null;
  }

  resetBatchResultPreview({ clearCache = false, render = true } = {}) {
    const requestId = Number(this.resultPreviewState?.requestId || 0) + 1;
    this.cleanupResultPreviewScrollControls();
    this.resultPreviewState = {
      batchName: "",
      page: 1,
      loading: false,
      error: "",
      data: null,
      requestId,
    };
    if (clearCache) this.resultPreviewCache.clear();
    if (render) this.renderWorkbenchBatchList();
  }

  async toggleBatchResultPreview(batchName) {
    if (!batchName) return;
    if (this.resultPreviewState.batchName === batchName) {
      this.resetBatchResultPreview();
      return;
    }
    this.cleanupResultPreviewScrollControls();
    this.resultPreviewState = {
      ...this.resultPreviewState,
      batchName,
      page: 1,
      loading: false,
      error: "",
      data: null,
    };
    await this.loadBatchResultPreview(batchName, 1);
  }

  async loadBatchResultPreview(batchName, page = 1, { force = false } = {}) {
    if (!batchName) return;
    page = Math.max(1, Number(page || 1));
    const cacheKey = this.resultPreviewCacheKey(batchName, page);
    const cached = force ? null : this.resultPreviewCache.get(cacheKey);
    const requestId = Number(this.resultPreviewState?.requestId || 0) + 1;
    this.resultPreviewState = {
      ...this.resultPreviewState,
      batchName,
      page,
      loading: !cached,
      error: "",
      data: cached || null,
      requestId,
    };
    this.renderWorkbenchBatchList();
    if (cached) return;

    try {
      const result = await this.call("overseas_costing.api.workbench.get_batch_result_preview", {
        batch_name: batchName,
        page,
        page_length: 20,
      });
      if (this.resultPreviewState.requestId !== requestId || this.resultPreviewState.batchName !== batchName) return;
      if (!result?.ok) throw new Error(result?.message || "SKU 核算结果加载失败");
      this.resultPreviewCache.set(cacheKey, result);
      this.resultPreviewState = {
        ...this.resultPreviewState,
        page: Number(result.page || page),
        loading: false,
        error: "",
        data: result,
      };
      this.renderWorkbenchBatchList();
    } catch (error) {
      if (this.resultPreviewState.requestId !== requestId || this.resultPreviewState.batchName !== batchName) return;
      this.resultPreviewState = {
        ...this.resultPreviewState,
        loading: false,
        error: this.normalizeErrorMessage(error),
        data: null,
      };
      this.renderWorkbenchBatchList();
    }
  }

  formatResultPreviewMoney(value, currency = "RMB") {
    if (value === null || value === undefined || value === "") return "未计算";
    const amount = this.formatMoney(value);
    return `${amount}${currency ? ` ${currency}` : ""}`;
  }

  renderBatchResultPreviewState() {
    const state = this.resultPreviewState;
    if (state.loading) {
      return `
        <section class="ocw-result-preview ocw-result-preview-state" aria-live="polite">
          <span class="ocw-spinner"></span><strong>${ocTranslate("正在加载 SKU 核算结果")}</strong>
        </section>
      `;
    }
    if (state.error) {
      return `
        <section class="ocw-result-preview ocw-result-preview-state is-error" aria-live="polite">
          <strong>${ocTranslate("SKU 核算结果加载失败")}</strong>
          <span>${this.escape(state.error)}</span>
          <button class="ocw-outline-btn" type="button" data-action="retry-result-preview">${ocTranslate("重试")}</button>
        </section>
      `;
    }
    return this.renderBatchResultPreview(state.data || {});
  }

  renderBatchResultPreview(data) {
    const summary = data.summary || {};
    const purchaseTotals = Array.isArray(summary.purchase_totals) ? summary.purchase_totals : [];
    const purchaseTotalHtml = purchaseTotals.length
      ? purchaseTotals.map((row) => `<span>${this.escape(this.formatResultPreviewMoney(row.amount, row.currency || ""))}</span>`).join("")
      : `<span>${ocTranslate("未计算")}</span>`;
    const calculationPending = summary.calculation_status === "pending";
    const otherCost = Number(summary.unlisted_other_cost_rmb || 0);
    const items = Array.isArray(data.items) ? data.items : [];
    const page = Math.max(1, Number(data.page || 1));
    const pageCount = Math.max(1, Number(data.page_count || 1));
    const total = Math.max(0, Number(data.total || 0));
    const moneyCell = (value, currency = "RMB") => this.escape(this.formatResultPreviewMoney(value, currency));
    const sourceMoneyCell = (value, currency = "") => {
      return this.escape(this.formatResultPreviewMoney(value, currency));
    };
    const columns = [
      ocTranslate("物料编码"),
      ocTranslate("产品名称"),
      ocTranslate("规格型号"),
      ocTranslate("原始采购单价"),
      ocTranslate("采购数量"),
      ocTranslate("分摊运费"),
      ocTranslate("分摊关税/税费"),
      ocTranslate("分摊清关"),
      ocTranslate("单品综合单价"),
    ];
    const headerClass = (index) => index === 0 ? " is-sticky-code" : index === 1 ? " is-sticky-name" : "";
    const visualHeaders = columns.map((label, index) =>
      `<th class="ocw-result-cell${headerClass(index)}">${this.escape(ocTranslate(label))}</th>`
    ).join("");
    const semanticHeaders = columns.map((label, index) =>
      `<th id="ocw-result-preview-column-${index + 1}" scope="col">${this.escape(ocTranslate(label))}</th>`
    ).join("");
    const columnClasses = [
      "is-code",
      "is-name",
      "is-spec",
      "is-unit-price",
      "is-quantity",
      "is-freight",
      "is-tax",
      "is-clearance",
      "is-total-unit",
    ];
    const columnGroup = `<colgroup>${columnClasses.map((className) => `<col class="ocw-result-col ${className}" />`).join("")}</colgroup>`;
    const cellHeaders = (index) => ` headers="ocw-result-preview-column-${index + 1}"`;
    const rows = items.map((item) => `
      <tr>
        <td class="ocw-result-cell is-sticky-code"${cellHeaders(0)} title="${this.escape(item.material_code || "")}"><span>${this.escape(item.material_code || "—")}</span></td>
        <td class="ocw-result-cell is-sticky-name"${cellHeaders(1)} title="${this.escape(item.product_name || "")}"><span>${this.escape(item.product_name || "—")}</span></td>
        <td class="ocw-result-cell"${cellHeaders(2)} title="${this.escape(item.spec_model || "")}">${this.escape(item.spec_model || "—")}</td>
        <td class="ocw-result-cell is-number"${cellHeaders(3)}>${sourceMoneyCell(item.adopted_price?.value ?? item.unit_price, item.adopted_price?.currency || item.purchase_currency || "")}${item.adopted_price?.source_type === "purchase_total_derived" ? `<small class="ocw-result-source">${ocTranslate("按货值÷采购数量计算")}</small>` : ""}</td>
        <td class="ocw-result-cell is-number"${cellHeaders(4)}>${this.escape(this.formatNumber(item.quantity) || "—")}</td>
        <td class="ocw-result-cell is-number"${cellHeaders(5)}>${moneyCell(item.freight_alloc_rmb)}</td>
        <td class="ocw-result-cell is-number"${cellHeaders(6)}>${moneyCell(item.tax_alloc_rmb)}</td>
        <td class="ocw-result-cell is-number"${cellHeaders(7)}>${moneyCell(item.clearance_alloc_rmb)}</td>
        <td class="ocw-result-cell is-number"${cellHeaders(8)}>${moneyCell(item.total_unit_rmb)}</td>
      </tr>
    `).join("");

    return `
      <section class="ocw-result-preview" aria-label="${ocTranslate('当前批次 SKU 核算结果')}">
        <div class="ocw-result-table-shell">
          <div class="ocw-result-sticky-dock" data-role="result-preview-sticky-dock">
            <div class="ocw-result-summary">
              <div><span>${ocTranslate("批次号")}</span><strong>${this.escape(summary.batch_no || "—")}</strong></div>
              <div><span>${ocTranslate("物流单号")}</span><strong>${this.escape(summary.logistics_no || ocTranslate("未填写"))}</strong></div>
              <div><span>${ocTranslate("批次总货值")}</span><strong class="ocw-result-summary-stack">${purchaseTotalHtml}</strong></div>
              <div><span>${ocTranslate("总运费")}</span><strong>${moneyCell(summary.total_freight_rmb)}</strong></div>
              <div><span>${ocTranslate("总关税/税费")}</span><strong>${moneyCell(summary.total_tax_rmb)}</strong></div>
              <div><span>${ocTranslate("总清关费")}</span><strong>${moneyCell(summary.total_clearance_rmb)}</strong></div>
              <div><span>${ocTranslate("总数量")}</span><strong>${this.escape(this.formatNumber(summary.total_quantity) || "0")}</strong></div>
              <div><span>${ocTranslate("批次加权综合单价")}</span><strong>${moneyCell(summary.weighted_total_unit_rmb)}</strong></div>
            </div>
            ${otherCost > 0 ? `<p class="ocw-result-other-cost">${ocTranslate("另有 {0} 其他费用已计入综合单价", [moneyCell(otherCost)])}</p>` : ""}
            ${calculationPending ? `<p class="ocw-result-pending-note">${ocTranslate("当前版本尚未完成计算，核算结果显示为“未计算”。")}</p>` : ""}
            <div class="ocw-result-scroll-controls">
              <button class="ocw-scroll-arrow" type="button" data-action="result-preview-scroll" data-direction="-1" aria-label="${ocTranslate('向左滚动 SKU 结果')}">‹</button>
              <div class="ocw-horizontal-scrollbar ocw-result-scrollbar" data-role="result-preview-scrollbar" data-ocw-scrollbar tabindex="0" aria-label="${ocTranslate('横向滚动 SKU 结果')}"><div data-role="result-preview-scrollbar-spacer"></div></div>
              <button class="ocw-scroll-arrow" type="button" data-action="result-preview-scroll" data-direction="1" aria-label="${ocTranslate('向右滚动 SKU 结果')}">›</button>
            </div>
            <div class="ocw-result-header-scroll" data-role="result-preview-header-scroll" aria-hidden="true">
              <table class="ocw-result-table ocw-result-header-table" role="presentation">
                ${columnGroup}
                <thead><tr>${visualHeaders}</tr></thead>
              </table>
            </div>
          </div>
          <div class="ocw-result-table-scroll" data-role="result-preview-table-scroll">
            <table class="ocw-result-table ocw-result-data-table" aria-label="${ocTranslate('当前批次 SKU 核算明细')}" aria-colcount="9">
              ${columnGroup}
              <thead class="ocw-result-semantic-head"><tr>${semanticHeaders}</tr></thead>
              <tbody>${rows || `<tr><td class="ocw-result-empty" colspan="9">${ocTranslate("当前批次没有 SKU 明细")}</td></tr>`}</tbody>
            </table>
          </div>
        </div>
        <div class="ocw-result-pagination">
          <span>${ocTranslate("共 {0} 个 SKU", [total])}</span>
          <div>
            <button class="ocw-outline-btn" type="button" data-action="result-preview-page" data-batch-name="${this.escape(data.batch_name || this.resultPreviewState?.batchName || "")}" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>${ocTranslate("上一页")}</button>
            <span>${ocTranslate("第 {0} / {1} 页", [page, pageCount])}</span>
            <button class="ocw-outline-btn" type="button" data-action="result-preview-page" data-batch-name="${this.escape(data.batch_name || this.resultPreviewState?.batchName || "")}" data-page="${page + 1}" ${page >= pageCount ? "disabled" : ""}>${ocTranslate("下一页")}</button>
          </div>
        </div>
      </section>
    `;
  }

  bindResultPreviewScrollControls() {
    this.cleanupResultPreviewScrollControls();
    const tableScroll = this.$root.find("[data-role='result-preview-table-scroll']").get(0);
    const headerScroll = this.$root.find("[data-role='result-preview-header-scroll']").get(0);
    const scrollbar = this.$root.find("[data-role='result-preview-scrollbar']").get(0);
    const spacer = this.$root.find("[data-role='result-preview-scrollbar-spacer']").get(0);
    this._resultPreviewScrollCleanup = this.bindHorizontalScrollController({
      content: tableScroll,
      header: headerScroll,
      scrollbar,
      spacer,
      leftButton: this.$root.find("[data-action='result-preview-scroll'][data-direction='-1']").get(0),
      rightButton: this.$root.find("[data-action='result-preview-scroll'][data-direction='1']").get(0),
    });
  }

  renderWorkbenchBatchList() {
    const pageCount = Math.ceil(this.workbenchTotal / 30);
    const isReview = this.viewState.task === "cost";
    const isHistory = isReview && this.filters.review_status === "confirmed";
    const title = this.viewState.task === "pending" ? ocTranslate("待处理批次") : this.viewState.task === "erp" ? ocTranslate("ERP 处理队列") : isHistory ? ocTranslate("已核对批次") : ocTranslate("待核对批次");
    const rows = this.batches.map((batch) => this.renderWorkbenchBatchRow(batch)).join("");
    const $batchList = this.$root.find("[data-area='batch-list']");
    $batchList.toggleClass("has-expanded-preview", Boolean(this.resultPreviewState?.batchName));
    $batchList.html(`
      <div class="ocw-list-head">
        <div><h2>${title}</h2><span>${ocTranslate("{0} 个结果", [this.workbenchTotal])}</span></div>
        <span>${isReview ? ocTranslate("暂估与待补凭证仅作提示；最终确认仍须通过校验") : ocTranslate("点击批次号查看详情，按下一步处理阻断项")}</span>
      </div>
      <div class="ocw-batch-grid ocw-batch-grid-head" aria-hidden="true">
        <span></span><span>${ocTranslate("批次 / 物流单号")}</span><span>${ocTranslate("业务类型 / SKU")}</span><span>${isReview ? ocTranslate("核对状态") : ocTranslate("当前问题")}</span><span>${ocTranslate("采购货值")}</span><span>${ocTranslate("综合成本")}</span><span>${ocTranslate("流程发起时间")}</span><span>${ocTranslate("下一步")}</span>
      </div>
      <div class="ocw-batch-grid-body">
        ${rows || `<div class="ocw-state-panel"><strong>${ocTranslate("当前条件下没有{0}", [title])}</strong><span>${isReview && !isHistory ? ocTranslate("请先在待处理中补齐计算所需资料并完成试算；旧结果不会进入待核对。") : ocTranslate("可调整日期、清空筛选或切换任务视图。")}</span></div>`}
      </div>
      <div class="ocw-pagination">
        <button class="ocw-outline-btn" type="button" data-action="workbench-page" data-page="${this.viewState.page - 1}" ${this.viewState.page <= 1 ? "disabled" : ""}>${ocTranslate("上一页")}</button>
        <span>${ocTranslate("第 {0} / {1} 页", [this.viewState.page, Math.max(pageCount, 1)])}</span>
        <button class="ocw-outline-btn" type="button" data-action="workbench-page" data-page="${this.viewState.page + 1}" ${this.viewState.page >= pageCount ? "disabled" : ""}>${ocTranslate("下一页")}</button>
      </div>
    `);
    window.requestAnimationFrame(() => this.bindResultPreviewScrollControls());
  }

  renderWorkbenchBatchRow(batch) {
    const reference = batch.batch_no || batch.source_approval_no || batch.name;
    const logisticsNo = batch.waybill_no || batch.customs_no || ocTranslate("未填写物流单号");
    const remediationState = String(batch.remediation_state || "").toLowerCase();
    let action;
    if (remediationState === "returned") {
      action = {action: "review_remediation", label: ocTranslate("待整改 {0}", [Number(batch.unresolved_count || batch.issue_count || 0)])};
    } else if (remediationState === "resubmitted") {
      action = {action: "review_remediation", label: ocTranslate("待财务复核")};
    } else if (this.viewState.task === "cost") {
      action = {action: "review", label: ocTranslate("成本核算")};
    } else if (this.viewState.task === "erp") {
      action = {
        action: "erp_retry",
        label: String(batch.writeback_status || "").toLowerCase().includes("success") ? ocTranslate("查看 ERP") : ocTranslate("处理 ERP"),
      };
    } else {
      action = {action: "supplement", label: ocTranslate("继续处理")};
    }
    const totalCost = batch.summary_snapshot?.calculation_schema === 2 ? batch.summary_snapshot.total_cost_rmb : batch.actual_total_cost_rmb || batch.estimated_total_cost_rmb;
    const rowStatus = this.workbenchRowStatusLabel(batch);
    const reviewTags = this.renderWorkbenchReviewTags(batch);
    const hasSavedResult = Boolean(batch.calculated_at || Number(totalCost || 0) > 0);
    const expanded = this.resultPreviewState?.batchName === batch.name;
    return `
      <div class="ocw-workbench-record ${expanded ? "is-expanded" : ""}" data-batch-name="${this.escape(batch.name)}">
        <article class="ocw-batch-grid ocw-workbench-row">
          <button class="ocw-result-preview-toggle" type="button" data-action="toggle-result-preview" data-batch-name="${this.escape(batch.name)}" aria-expanded="${expanded}" aria-label="${this.escape(ocTranslate("{0} {1} 的 SKU 核算结果", [expanded ? "收起" : "展开", reference]))}">
            <span aria-hidden="true">›</span>
          </button>
          <div class="ocw-batch-identity">
            <button type="button" class="ocw-batch-link" data-action="open-batch-detail" data-batch-name="${this.escape(batch.name)}">${this.escape(reference)}</button>
            <span>${this.escape(logisticsNo)}</span>
          </div>
          <div><strong>${this.escape(this.businessTypeLabel(batch.business_type) || batch.transport_mode || "-")}</strong><span>${ocTranslate("{0} 个 SKU", [Number(batch.item_count || 0)])}</span></div>
          <div><strong class="ocw-issue is-${this.escape(batch.primary_issue)}">${this.escape(rowStatus)}</strong>${reviewTags}</div>
          <div><strong>${this.escape(this.formatMoney(batch.total_goods_value || 0))}</strong><span>RMB</span></div>
          <div><strong>${this.escape(this.formatMoney(totalCost || 0))}</strong><span>RMB${batch.result_is_current === false && hasSavedResult ? ocTranslate(" · 上次结果，待更新") : ""}</span></div>
          <div><strong>${this.escape(this.formatDateTimeMinute(batch.source_created_at) || "—")}</strong><span>${this.escape(batch.review_state === "confirmed" ? ocTranslate("确认版本 {0}", [batch.reviewed_version || batch.current_version || "—"]) : batch.result_is_current === false ? ocTranslate("待重新试算") : ocTranslate(batch.status || ""))}</span></div>
          <div class="ocw-row-actions">
            <button class="ocw-primary-btn" type="button" data-action="workbench-primary" data-primary-action="${action.action}" data-batch-name="${this.escape(batch.name)}">${action.label}</button>
          </div>
        </article>
        ${expanded ? this.renderBatchResultPreviewState() : ""}
      </div>
    `;
  }

  async handleWorkbenchPopState() {
    const previousDetailBatch = this.detailState.batchName;
    const previousViewState = { ...(this.viewState || {}) };
    const nextViewState = OverseasCostWorkbenchState.parseWorkbenchState(window.location.href);
    const leavesCurrentDetail = Boolean(
      previousDetailBatch
      && (
        nextViewState.screen !== "detail"
        || nextViewState.batch !== previousDetailBatch
        || nextViewState.tab !== this.detailState.tab
      )
    );
    if (leavesCurrentDetail && !(await this.confirmDiscardDetailChanges())) {
      const restoredUrl = OverseasCostWorkbenchState.buildWorkbenchUrl(window.location.href, {
        task: previousViewState.task,
        screen: previousViewState.screen,
        batch: previousViewState.batch,
        tab: previousViewState.tab,
        q: previousViewState.q,
        page: previousViewState.page,
        issue: previousViewState.issue,
        business_type: previousViewState.businessType,
        subsidiary_code: previousViewState.subsidiaryCode,
        start_date: previousViewState.startDate,
        end_date: previousViewState.endDate,
        erp_status: previousViewState.erpStatus,
        review_status: previousViewState.reviewStatus,
        review_warning: previousViewState.reviewWarning,
      });
      window.history.pushState({ ...(window.history.state || {}), overseasCostWorkbench: true }, "", restoredUrl);
      this.viewState = previousViewState;
      return;
    }
    this.viewState = nextViewState;
    const defaults = this.getDefaultPullDateRange();
    Object.assign(this.filters, {
      issue: this.viewState.issue, business_type: this.viewState.businessType,
      subsidiary_code: this.viewState.subsidiaryCode, erp_status: this.viewState.erpStatus,
      start_date: this.viewState.hasDateRange ? this.viewState.startDate : defaults.start_date,
      end_date: this.viewState.hasDateRange ? this.viewState.endDate : defaults.end_date,
      review_status: this.viewState.reviewStatus, review_warning: this.viewState.reviewWarning,
    });
    if (this.viewState.screen === "detail" && this.viewState.batch) {
      await this.openBatchDetail(this.viewState.batch, this.viewState.tab, { updateUrl: false });
      return;
    }
    if (previousDetailBatch && this.detailState.editToken) {
      await this.releaseEditSession();
    }
    this.detailState.requestId += 1;
    this.detailState.skuRequestId += 1;
    this.detailState.refreshRequestId += 1;
    this.detailState.batchName = "";
    this.detailState.header = null;
    this.detailState.detail = null;
    this.exportPinnedBatchName = "";
    this.dataCheckBatchName = "";
    this.drawerBatchName = "";
    this.$root.find("[data-area='detail-screen']").prop("hidden", true);
    this.$root.find("[data-area='workbench-screen']").prop("hidden", false);
    await this.loadBatches();
    requestAnimationFrame(() => window.scrollTo({ top: Number(window.history.state?.ocwScrollY || 0), behavior: "auto" }));
  }
