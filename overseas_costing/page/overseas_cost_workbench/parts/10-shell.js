class OverseasCostWorkbench {
  constructor(wrapper) {
    this.wrapper = wrapper;
    this.page = null;
    this.$root = null;
    this.batches = [];
    this.visibleBatches = [];
    this.batchItems = {};
    this.batchColumns = [];
    this.expandedBatchNames = new Set();
    this.activeBatchName = "";
    this.drawerBatchName = "";
    this.drawerTab = "overview";
    this.focusedBatchName = "";
    this.batchClickTimer = null;
    this.exportPinnedBatchName = "";
    this.dataCheckBatchName = "";
    this.filters = {
      start_date: "",
      end_date: "",
      customs_no: "",
      waybill_no: "",
      material_code: "",
      product_name: "",
      import_name: "",
      hs_code: "",
      category: "",
      subsidiary_code: "",
      calculation_status: "",
      erp_status: "",
      transport_mode: "",
      business_type: "",
      issue: "",
    };
    this.readonlyCalcFields = new Set(["goods_value_ratio", "freight_alloc_rmb", "freight_alloc_mxn", "total_logistics_mxn"]);
    this.specialOverrideFields = new Set(["weight_ratio", "alloc_price_mxn", "total_cost_rmb", "total_unit_rmb"]);
    this.selectOptions = {
      transport_mode: ["SEA", "AIR", "EXPRESS"],
      business_type: [
        { value: "SEA_STANDARD", label: ocTranslate("海运正报正清") },
        { value: "SEA_DDP", label: ocTranslate("海运 DDP（双清包税）") },
        { value: "AIR_DDP", label: ocTranslate("空运 DDP（双清包税）") },
        { value: "AIR_STANDARD", label: ocTranslate("正常空运") },
        { value: "EXPRESS", label: ocTranslate("快递") },
      ],
      purchase_currency: ["RMB", "USD", "MXN"],
    };
    this.auditEvents = [];
    this.usageEvents = [];
    this.usageSummary = null;
    this.businessEntityOptions = [];
    this.businessEntityOptionsSignature = "";
    this.businessTypeOptions = [];
    this.erpQueueMode = false;
    this.workRole = "purchase";
    this.erpQueueStatus = "all";
    this.lastImportResult = null;
    this.lastRecalculateResult = null;
    this.lastImportedBatchNames = new Set();
    this.isOpeningDingtalk = false;
    this.isParsingManualDocuments = false;
    this.defaultRecentDays = 30;
    Object.assign(this.filters, this.getDefaultPullDateRange());
    this.moreFiltersOpen = false;
    this.erpFlowBlockState = null;
    this.erpPushBlockState = null;
    this.erpWritebackInFlight = new Set();
    this.childPriorityFields = this.loadChildPriorityFields();
    this.transportSidebarCollapsed = this.loadTransportSidebarState();
    this.viewState = OverseasCostWorkbenchState.parseWorkbenchState(window.location.href);
    this.filters.issue = this.viewState.issue;
    this.filters.business_type = this.viewState.businessType;
    this.filters.subsidiary_code = this.viewState.subsidiaryCode;
    if (this.viewState.hasDateRange) {
      this.filters.start_date = this.viewState.startDate;
      this.filters.end_date = this.viewState.endDate;
    }
    this.filters.erp_status = this.viewState.erpStatus;
    this.filters.review_status = this.viewState.reviewStatus;
    this.filters.review_warning = this.viewState.reviewWarning;
    this.workbenchTotal = 0;
    this.exceptionCounts = {};
    this.reviewCounts = {};
    this.resultPreviewState = {
      batchName: "",
      page: 1,
      loading: false,
      error: "",
      data: null,
      requestId: 0,
    };
    this.resultPreviewCache = new Map();
    this._resultPreviewScrollCleanup = null;
    this._releaseMonitorActive = false;
    this._releaseMonitorGeneration = 0;
    this.detailState = {
      batchName: this.viewState.batch,
      versionName: "",
      tab: this.viewState.tab,
      header: null,
      sku: { page: 1, pageLength: 50, keyword: "", fieldGroup: "basic", sortBy: "row_no", sortOrder: "asc" },
      requestId: 0,
      skuRequestId: 0,
      refreshRequestId: 0,
      dingtalkRequestId: 0,
      dingtalkApproval: null,
    };
  }

  async init() {
    this.resetDeskLayoutClasses();
    this.prepareWorkbenchContainer();
    this.page = frappe.ui.make_app_page({
      parent: this.wrapper,
      title: ocTranslate("海外采购综合成本核算"),
      single_column: true,
    });
    this.applyDeskLayout();
    this.addActions();
    this.renderShell();
    this.bindEvents();
    await this.initializeWorkbenchRelease();
    if (this.releaseBlocked) return;
    this.loadBusinessEntityOptions();
    this.loadBatches();
    this.recordUsage("PAGE_VIEW", { remark: "进入海外采购综合成本核算工作台" });
  }

  prepareWorkbenchContainer() {
    // 清空 wrapper，再由 make_app_page 重建页面骨架。
    $(this.wrapper).empty();
  }

  // 保留 ERP 模块栏和工作区侧栏，只隐藏会遮挡工作台的顶部标签栏。
  hideDeskChrome() {
    const targets = [$("#custom-filters-desk-tabs-bar")];
    this._hiddenChrome = this._hiddenChrome || [];
    const trackedElements = this._hiddenChrome.map((item) => item.el[0]);
    const newlyVisibleTargets = targets
      .filter(($el) => $el.length && $el.is(":visible") && !trackedElements.includes($el[0]));
    this._hiddenChrome.push(
      ...newlyVisibleTargets
        .map(($el) => ({ el: $el, display: $el[0].style.display }))
    );
    this._hiddenChrome.forEach((item) => {
      item.el[0].style.display = "none";
    });
    console.info("[overseas-cost-workbench] 隐藏桌面外壳", {
      hidden: this._hiddenChrome.map((item) => item.el.attr("id") || item.el.attr("class")),
    });
  }

  // 离开工作台时恢复上面隐藏的元素，避免影响其它页面。
  restoreDeskChrome() {
    this.stopWorkbenchReleaseMonitor();
    $(window).off("beforeunload.ocwDetailEdit");
    if (this.releaseEditSession && this.detailState?.editToken) {
      this.releaseEditSession();
    }
    if (this._hiddenChrome && this._hiddenChrome.length) {
      this._hiddenChrome.forEach((item) => {
        item.el[0].style.display = item.display;
      });
      this._hiddenChrome = null;
    }
    if (this.restoreModuleSidebar) this.restoreModuleSidebar();
    if (this._erpModuleSidebarSnapshot) {
      const snapshot = this._erpModuleSidebarSnapshot;
      snapshot.element.style.display = snapshot.display;
      snapshot.element.style.visibility = snapshot.visibility;
      snapshot.element.style.opacity = snapshot.opacity;
      this._erpModuleSidebarSnapshot = null;
    }
    if (this._workspaceSidebarIconSnapshot) {
      const snapshot = this._workspaceSidebarIconSnapshot;
      if (snapshot.element && snapshot.element.isConnected) {
        $(snapshot.element).html(snapshot.html);
      }
      this._workspaceSidebarIconSnapshot = null;
    }
    this.resetDeskLayoutClasses();
  }

  resetDeskLayoutClasses() {
    $(".ocw-desk-fullwidth, .ocw-desk-wide-node, .ocw-desk-layout, .ocw-desk-section-wrapper, .ocw-desk-main").removeClass(
      "ocw-desk-fullwidth ocw-desk-wide-node ocw-desk-layout ocw-desk-section-wrapper ocw-desk-main"
    );
  }

  applyDeskLayout() {
    this.resetDeskLayoutClasses();
    const $wrapper = $(this.wrapper);
    const $mainSection = $(this.page.main);

    $wrapper.addClass("ocw-desk-fullwidth");
    $mainSection
      .addClass("ocw-desk-main")
      .parents(
        ".container, .page-body, .page-content, .page-wrapper, .layout-main, .layout-main-section, .layout-main-section-wrapper"
      )
      .addClass("ocw-desk-wide-node");

    $mainSection.closest(".page-body").addClass("full-width");
    $mainSection.closest(".layout-main").addClass("ocw-desk-layout");
    $mainSection.closest(".layout-main-section-wrapper").addClass("ocw-desk-section-wrapper");
  }

  addActions() {
    // 操作入口保留在工作台内部，避免 Frappe 顶部 Actions 与页面按钮重复。
    if (this.page.clear_primary_action) this.page.clear_primary_action();
    if (this.page.clear_actions_menu) this.page.clear_actions_menu();
  }

  renderShell() {
    this.$root = $(`
      <div class="ocw-page">
        <div class="ocw-shell">
          <aside class="ocw-sidebar">
            <div class="ocw-brand">
              <div class="ocw-logo">YW</div>
              <div>
                <p>Mexico Ocean Costing</p>
                <h1>${ocTranslate("海外采购综合成本核算")}</h1>
              </div>
            </div>
            <section class="ocw-logistics-panel">
              <div class="ocw-logistics-panel-head">
                <span>${ocTranslate("业务类型")}</span>
                <button
                  class="ocw-sidebar-toggle"
                  type="button"
                  data-action="toggle-transport-sidebar"
                  aria-expanded="true"
                  aria-label="${ocTranslate('收起业务类型栏')}"
                  title="${ocTranslate('收起业务类型栏')}"
                >
                  <span class="ocw-sidebar-toggle-icon" aria-hidden="true"></span>
                </button>
                <button class="ocw-text-btn" type="button" data-action="set-transport-filter" data-transport-mode="">${ocTranslate("全部")}</button>
              </div>
              <div class="ocw-logistics-list" data-area="transport-workbench"></div>
              <div class="ocw-sidebar-scope-note">
                <span>${ocTranslate("默认最近 30 天")}</span>
                <span>${ocTranslate("可按审批发起时间筛选")}</span>
              </div>
            </section>
          </aside>

          <main class="ocw-main">
            <section class="ocw-workbench-card">
              <div class="ocw-workbench-head">
                <div>
                  <h3>${ocTranslate("SKU 成本分摊明细 / 物料详情")}</h3>
                </div>
                <div class="ocw-head-actions">
                  <button class="ocw-success-btn ocw-mini-btn" data-action="export-current">${ocTranslate("导出当前全部批次结果")}</button>
                  <button class="ocw-warning-btn ocw-mini-btn" data-action="file-parse">${ocTranslate("凭证对比")}</button>
                  <button class="ocw-outline-btn ocw-mini-btn" data-action="preview-categories">${ocTranslate("商品归类")}</button>
                  <button class="ocw-outline-btn ocw-mini-btn" data-action="pull-oa-logistics">${ocTranslate("钉钉拉取")}</button>
                  <button class="ocw-outline-btn ocw-mini-btn" data-action="open-import">${ocTranslate("Excel 导入")}</button>
                  <button class="ocw-primary-btn ocw-mini-btn" data-action="add-batch">${ocTranslate("+ 添加报关运单")}</button>
                </div>
              </div>

              <div class="ocw-query-toolbar">
                <div class="ocw-filter-grid">
                  <label class="ocw-toolbar-field ocw-toolbar-field-date">
                    <span>${ocTranslate("开始日期")}</span>
                    <input data-filter="start_date" class="form-control" type="date" value="${this.escape(this.filters.start_date)}" />
                  </label>
                  <label class="ocw-toolbar-field ocw-toolbar-field-date">
                    <span>${ocTranslate("结束日期")}</span>
                    <input data-filter="end_date" class="form-control" type="date" value="${this.escape(this.filters.end_date)}" />
                  </label>
                  <label class="ocw-toolbar-field ocw-toolbar-field-entity">
                    <span>${ocTranslate("业务主体")}</span>
                    <select data-filter="subsidiary_code" class="form-control">
                      <option value="">${ocTranslate("全部业务主体")}</option>
                    </select>
                  </label>
                  <label class="ocw-toolbar-field ocw-toolbar-field-erp">
                    <span>${ocTranslate("ERP 状态")}</span>
                    <select data-filter="erp_status" class="form-control">
                      <option value="">${ocTranslate("全部")}</option>
                      <option value="not_started">${ocTranslate("未开始")}</option>
                      <option value="pending">${ocTranslate("待接口推送")}</option>
                      <option value="success">${ocTranslate("推送成功")}</option>
                      <option value="failed">${ocTranslate("推送失败")}</option>
                    </select>
                  </label>
                  <div class="ocw-filter-actions">
                    <button class="ocw-primary-btn ocw-mini-btn" data-action="apply-filters">${ocTranslate("查询")}</button>
                    <button class="ocw-outline-btn ocw-mini-btn" data-action="clear-filters">${ocTranslate("重置")}</button>
                  </div>
                </div>
                <div class="ocw-query-footer">
                  <div class="ocw-search-result" data-area="search-result">${ocTranslate("查询后会锁定当前结果；回到全部批次请点击“重置”")}</div>
                  <button class="ocw-text-btn" type="button" data-action="toggle-more-filters" aria-expanded="false">${ocTranslate("更多筛选 +")}</button>
                </div>
                <div class="ocw-filter-chips" data-area="filter-chips" aria-live="polite"></div>
                <div class="ocw-more-filter-grid" data-area="more-filters" hidden>
                  <label class="ocw-toolbar-field">
                    <span>${ocTranslate("核算状态")}</span>
                    <select data-filter="calculation_status" class="form-control">
                      <option value="">${ocTranslate("全部")}</option>
                      <option value="草稿">${ocTranslate("草稿")}</option>
                      <option value="已导入">${ocTranslate("已导入")}</option>
                      <option value="待重算">${ocTranslate("待重算")}</option>
                      <option value="已试算">${ocTranslate("已试算")}</option>
                      <option value="已确认">${ocTranslate("已确认")}</option>
                    </select>
                  </label>
                  <label class="ocw-toolbar-field">
                    <span>${ocTranslate("批次/报关单号/钉钉审批编号")}</span>
                    <input data-filter="customs_no" class="form-control" type="search" placeholder="${ocTranslate('请输入批次号、报关单号或钉钉审批编号')}" />
                  </label>
                  <label class="ocw-toolbar-field">
                    <span>${ocTranslate("物料编码")}</span>
                    <input data-filter="material_code" class="form-control" type="search" placeholder="${ocTranslate('请输入物料编码')}" />
                  </label>
                  <label class="ocw-toolbar-field">
                    <span>${ocTranslate("物料名称")}</span>
                    <input data-filter="product_name" class="form-control" type="search" placeholder="${ocTranslate('请输入物料名称')}" />
                  </label>
                  <label class="ocw-toolbar-field">
                    <span>${ocTranslate("海关进口名称")}</span>
                    <input data-filter="import_name" class="form-control" type="search" placeholder="${ocTranslate('请输入海关进口名称')}" />
                  </label>
                  <label class="ocw-toolbar-field">
                    <span>${ocTranslate("海关分类编码")}</span>
                    <input data-filter="hs_code" class="form-control" type="search" placeholder="${ocTranslate('请输入海关分类编码')}" />
                  </label>
                  <label class="ocw-toolbar-field">
                    <span>${ocTranslate("大类")}</span>
                    <input data-filter="category" class="form-control" type="search" placeholder="${ocTranslate('请输入大类')}" />
                  </label>
                </div>
              </div>

                <div class="ocw-table-toolbar">
                  <div class="ocw-table-toolbar-left">
                    <button class="ocw-outline-btn ocw-mini-btn" data-action="clear-batch-focus" hidden>${ocTranslate("返回全部批次")}</button>
                    <button class="ocw-outline-btn ocw-mini-btn" data-action="expand-current">${ocTranslate("+ 全部展开")}</button>
                    <button class="ocw-outline-btn ocw-mini-btn" data-action="collapse-current">${ocTranslate("- 全部收起")}</button>
                    <div class="ocw-role-switch" aria-label="${ocTranslate('岗位工作视图')}">
                      <button class="active" type="button" data-action="set-work-role" data-role="purchase">${ocTranslate("采购")}</button>
                      <button type="button" data-action="set-work-role" data-role="finance">${ocTranslate("财务")}</button>
                    </div>
                    <strong class="ocw-role-title" data-area="table-title">${ocTranslate("采购待补资料")}</strong>
                    <span data-area="table-count"></span>
                  </div>
                  <div class="ocw-table-hint">
                    <span data-area="role-description">${ocTranslate("采购视图：处理数据不完整批次，补齐资料")}</span>
                    <span>${ocTranslate("单击批次可选中并锁定；双击批次打开详情侧边栏")}</span>
                  </div>
                  <div class="ocw-table-actions">
                    <button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="configure-sku-columns">${ocTranslate("SKU重点字段")}</button>
                    <div class="ocw-view-switch">
                      <button class="active" type="button" data-action="set-main-view" data-view="cost">${ocTranslate("成本列表")}</button>
                      <button type="button" data-action="set-main-view" data-view="erp_queue">${ocTranslate("ERP 队列")}</button>
                  </div>
                </div>
              </div>
              <div class="ocw-hierarchy-wrap" data-area="table"></div>
            </section>

            <div class="ocw-batch-drawer-mask" data-area="batch-drawer-mask" data-action="close-batch-drawer"></div>
            <aside class="ocw-batch-drawer" data-area="batch-drawer" aria-hidden="true">
              <div class="ocw-batch-drawer-head">
                <div>
                  <span>${ocTranslate("批次详情")}</span>
                  <strong data-area="batch-drawer-title">${ocTranslate("批次详情（字段修改请在明细区操作）")}</strong>
                </div>
                <div class="ocw-batch-drawer-head-actions">
                  <button class="ocw-outline-btn ocw-mini-btn" data-action="export-drawer-batch">${ocTranslate("导出当前批次")}</button>
                  <button class="ocw-outline-btn ocw-mini-btn" data-action="open-batch-drawer-dingtalk">${ocTranslate("钉钉原单")}</button>
                  <button class="ocw-outline-btn ocw-mini-btn" data-action="open-batch-drawer-recalculate">${ocTranslate("重新试算")}</button>
                  <button class="ocw-icon-btn" data-action="close-batch-drawer" aria-label="${ocTranslate('关闭')}">×</button>
                </div>
              </div>
              <div class="ocw-batch-drawer-tabs">
                <button class="ocw-batch-drawer-tab active" data-action="switch-batch-drawer-tab" data-tab="overview">${ocTranslate("概览")}</button>
                <button class="ocw-batch-drawer-tab" data-action="switch-batch-drawer-tab" data-tab="items">${ocTranslate("物料明细")}</button>
                <button class="ocw-batch-drawer-tab" data-action="switch-batch-drawer-tab" data-tab="allocation">${ocTranslate("AI 分摊")}</button>
                <button class="ocw-batch-drawer-tab" data-action="switch-batch-drawer-tab" data-tab="audit">${ocTranslate("修改记录")}</button>
                <button class="ocw-batch-drawer-tab" data-action="switch-batch-drawer-tab" data-tab="usage">${ocTranslate("使用记录")}</button>
              </div>
              <div class="ocw-batch-drawer-body" data-area="batch-drawer-body"></div>
            </aside>

          </main>
        </div>
      </div>
    `);
    $(this.page.main).empty().append(this.$root);
    this.applyDeskLayout();
    this.setTransportSidebarCollapsed(this.transportSidebarCollapsed, false);
    this.renderEmpty();
    this.renderAuditList();
    this.renderDiffPanel();
  }

  loadTransportSidebarState() {
    try {
      return window.localStorage.getItem("overseas-costing:transport-sidebar-collapsed") === "1";
    } catch (error) {
      return false;
    }
  }

  setTransportSidebarCollapsed(collapsed, persist = true) {
    this.transportSidebarCollapsed = Boolean(collapsed);
    if (persist) {
      try {
        window.localStorage.setItem(
          "overseas-costing:transport-sidebar-collapsed",
          this.transportSidebarCollapsed ? "1" : "0"
        );
      } catch (error) {
        // 浏览器禁用本地存储时仍保留本次页面状态。
      }
    }
    if (!this.$root) return;
    const $shell = this.$root.find(".ocw-shell");
    const $toggle = this.$root.find("[data-action='toggle-transport-sidebar']");
    $shell.toggleClass("is-transport-sidebar-collapsed", this.transportSidebarCollapsed);
    $toggle.attr({
      "aria-expanded": String(!this.transportSidebarCollapsed),
      "aria-label": this.transportSidebarCollapsed ? "展开业务类型栏" : "收起业务类型栏",
      title: this.transportSidebarCollapsed ? ocTranslate("展开业务类型栏") : ocTranslate("收起业务类型栏"),
    });
  }

  toggleTransportSidebar() {
    this.setTransportSidebarCollapsed(!this.transportSidebarCollapsed);
  }

  bindEvents() {
    if (this.bindRedesignEvents) this.bindRedesignEvents();
    if (this.bindMaterialFeeWorkspaceEvents) this.bindMaterialFeeWorkspaceEvents();
    if (this.bindReviewCommunicationEvents) this.bindReviewCommunicationEvents();
    this.$root.on("click", "[data-batch-name]", (event) => {
      // 新工作台由显式的详情导航设置批次上下文；这里仅保留旧壳层的隐式选中兼容。
      if (this.$root.hasClass("ocw-redesign")) return;
      if ($(event.currentTarget).hasClass("ocw-parent-row")) return;
      const batchName = $(event.currentTarget).attr("data-batch-name");
      if (batchName) {
        this.activeBatchName = batchName;
        this.exportPinnedBatchName = batchName;
        this.renderDiffPanel();
        this.updateRecalculateAction();
        const batch = this.findBatch(batchName);
        this.loadAuditLogs(batchName, batch ? batch.current_version : null).catch((error) => this.showError(error));
      }
    });

    this.$root.on("click", "[data-action='reload-batches']", () => this.loadBatches());
    this.$root.on("click", "[data-action='set-work-role']", (event) => this.setWorkRole($(event.currentTarget).attr("data-role")));
    this.$root.on("click", "[data-action='set-main-view']", (event) => this.setMainView($(event.currentTarget).attr("data-view")));
    this.$root.on("click", "[data-action='set-erp-queue-status']", (event) => this.setErpQueueStatus($(event.currentTarget).attr("data-status")));
    this.$root.on("click", "[data-action='apply-filters']", () => this.applyFilters());
    this.$root.on("click", "[data-action='clear-filters']", () => this.clearFilters());
    this.$root.on("click", "[data-action='toggle-more-filters']", () => this.toggleMoreFilters());
    this.$root.on("click", "[data-action='toggle-transport-sidebar']", () => this.toggleTransportSidebar());
    this.$root.on("click", "[data-action='remove-filter']", (event) => {
      this.removeFilter($(event.currentTarget).attr("data-filter"));
    });
    this.$root.on("click", "[data-action='configure-sku-columns']", () => this.openChildColumnPreferenceDialog());
    this.$root.on("click", "[data-action='set-transport-filter']", (event) =>
      this.setTransportFilter($(event.currentTarget).attr("data-transport-mode")).catch((error) => this.showError(error))
    );
    this.$root.on("click", "[data-action='recalculate']", (event) => this.recalculate($(event.currentTarget).attr("data-batch-name")));
    this.$root.on("click", "[data-action='open-import']", () => this.openImportDialog());
    this.$root.on("click", "[data-action='pull-oa-logistics']", () => this.openOaLogisticsPullDialog());
    this.$root.on("change", "[data-role='data-check-batch-select']", (event) => {
      const batchName = String($(event.currentTarget).val() || "");
      const batch = this.findBatch(batchName);
      if (!batch) return;
      this.dataCheckBatchName = batch.name;
      this.loadBatchItems(batch.name, batch.current_version)
        .then(() => this.renderDiffPanel())
        .catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='preview-categories']", () => this.openCategoryPreviewDialog());
    this.$root.on("click", "[data-action='file-parse']", () => this.openFileParseDialog());
    this.$root.on("click", "[data-action='export-current']", () => this.exportCurrentResult().catch((error) => this.showError(error)));
    this.$root.on("click", "[data-action='manual-logistics-quote']", (event) => this.openLogisticsQuoteDialog($(event.currentTarget).attr("data-batch-name")));
    this.$root.on("click", "[data-action='source-center']", (event) => this.openSourceCenterDialog($(event.currentTarget).attr("data-batch-name")));
    this.$root.on("click", ".ocw-parent-row", (event) => {
      if ($(event.target).closest("button, input, select, textarea, a").length) return;
      const batchName = $(event.currentTarget).attr("data-batch-name");
      window.clearTimeout(this.batchClickTimer);
      this.batchClickTimer = window.setTimeout(() => {
        this.focusBatch(batchName).catch((error) => this.showError(error));
      }, 220);
    });
    this.$root.on("click", "[data-action='clear-batch-focus']", () => this.clearBatchFocus());
    this.$root.on("click", "[data-action='close-batch-drawer']", () => this.closeBatchDrawer());
    this.$root.on("click", "[data-action='switch-batch-drawer-tab']", (event) =>
      this.switchBatchDrawerTab($(event.currentTarget).attr("data-tab"))
    );
    this.$root.on("click", "[data-action='save-profit-inputs']", () => this.saveProfitInputs());
    this.$root.on("input change", "[data-profit-input]", () => this.updateProfitDrawerPreview());
    this.$root.on("click", "[data-action='export-drawer-batch']", () => this.exportDrawerBatch().catch((error) => this.showError(error)));
    this.$root.on("click", "[data-action='open-batch-drawer-dingtalk']", () => this.openDingtalkOrder(this.drawerBatchName));
    this.$root.on("click", "[data-action='open-batch-drawer-recalculate']", () => this.recalculate(this.drawerBatchName));
    this.$root.on("click", "[data-action='confirm-calculation-result']", () => this.confirmCalculationResult(this.drawerBatchName));
    this.$root.on("click", "[data-action='preview-erp-payload']", () => this.previewErpPayload(this.drawerBatchName));
    this.$root.on("click", "[data-action='detail-writeback-to-erp']", () => this.writebackToErp(this.detailState.batchName));
    this.$root.on("click", "[data-action='erp-site-preview']", () =>
      this.openErpSiteSyncDialog(this.detailState?.batchName || "").catch((error) => this.showError(error))
    );
    this.$root.on("click", "[data-action='erp-site-refresh']", () =>
      this.refreshErpSitePanel().catch((error) => this.showError(error))
    );
    this.$root.on("click", "[data-action='erp-site-reconcile']", (event) =>
      this.reconcileErpSiteRequest($(event.currentTarget).attr("data-request-id"))
    );
    this.$root.on("click", "[data-action='erp-site-documents']", (event) =>
      this.openErpSiteRemoteDocuments($(event.currentTarget).attr("data-site-code"))
    );
    this.$root.on("click", "[data-action='erp-site-retry']", (event) =>
      this.retryErpSiteRequest($(event.currentTarget).attr("data-request-id"))
    );
    this.$root.on("click", "[data-action='queue-preview-erp']", (event) => {
      const batchName = $(event.currentTarget).attr("data-batch-name");
      const batch = this.findBatch(batchName);
      if (!batch) return;
      const openPreview = async () => {
        if (!this.$root.find("[data-area='batch-drawer']").hasClass("is-open")) {
          await this.openBatchDrawer(batch.name, { updateUrl: false });
        }
        await this.previewErpPayload(batch.name);
      };
      openPreview().catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='queue-writeback-erp']", (event) => this.writebackToErp($(event.currentTarget).attr("data-batch-name")));
    this.$root.on("click", "[data-action='queue-open-batch']", (event) => this.openBatchDrawer($(event.currentTarget).attr("data-batch-name")));
    this.$root.on("click", "[data-action='gap-repull-dingtalk']", (event) => this.repullGapDingtalk($(event.currentTarget).attr("data-batch-name")));
    this.$root.on("click", "[data-action='gap-confirm-actual-qty']", (event) =>
      this.confirmActualQtyFromQuantity($(event.currentTarget).attr("data-batch-name")).catch((error) => this.showError(error))
    );
    this.$root.on("click", "[data-action='gap-open-fee-pool']", (event) => {
      const $button = $(event.currentTarget);
      this.openFeePoolGapDialog($button.attr("data-batch-name"), {
        fieldname: $button.attr("data-gap-fieldname"),
        label: $button.attr("data-gap-label"),
      });
    });
    this.$root.on("click", "[data-action='gap-open-source-center']", (event) => {
      const $button = $(event.currentTarget);
      this.openSourceCenterDialog($button.attr("data-batch-name"), {
        fieldname: $button.attr("data-gap-fieldname"),
        label: $button.attr("data-gap-label"),
      });
    });
    this.$root.on("click", "[data-action='gap-confirm-zero-fee']", (event) => {
      const $button = $(event.currentTarget);
      this.confirmZeroFeeGap($button.attr("data-batch-name"), {
        fieldname: $button.attr("data-gap-fieldname"),
        label: $button.attr("data-gap-label"),
      }).catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='gap-open-item-edit']", (event) =>
      this.openGapItemEdit(
        $(event.currentTarget).attr("data-batch-name"),
        $(event.currentTarget).attr("data-item-name"),
        $(event.currentTarget).attr("data-fieldname")
      ).catch((error) => this.showError(error))
    );
    this.$root.on("click", "[data-action='add-batch']", () => this.openAddBatchDialog());
    this.$root.on("click", "[data-action='toggle-batch']", (event) => this.toggleBatch($(event.currentTarget).attr("data-batch-name")));
    this.$root.on("click", "[data-action='expand-current']", () => this.setAllExpanded(true));
    this.$root.on("click", "[data-action='collapse-current']", () => this.setAllExpanded(false));
    this.$root.on("click", "[data-action='add-material']", (event) => this.openAddMaterialDialog($(event.currentTarget).attr("data-batch-name")));
    this.$root.on("click", "[data-action='delete-material']", (event) => {
      this.confirmDeleteMaterial(
        $(event.currentTarget).attr("data-batch-name"),
        $(event.currentTarget).attr("data-item-name"),
        $(event.currentTarget).attr("data-item-label")
      );
    });
    this.$root.on("click", "[data-editable-cell='1']", (event) => {
      if ($(event.target).closest(".ocw-cell-editor").length) return;
      const $cell = $(event.currentTarget);
      const autoOpenSelect = Boolean(this.selectOptions[$cell.attr("data-fieldname")]);
      this.startCellEdit($cell, event, autoOpenSelect);
    });
    this.$root.on("keydown", ".ocw-cell-editor", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        event.stopImmediatePropagation();
        this.commitCellEdit($(event.currentTarget).closest("td"));
      }
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        this.cancelCellEdit($(event.currentTarget).closest("td"));
      }
    });
    this.$root.on("blur", ".ocw-cell-editor", (event) => {
      const $cell = $(event.currentTarget).closest("td");
      window.setTimeout(() => this.commitCellEdit($cell), 0);
    });
    this.$root.on("change", "select.ocw-cell-editor", (event) => this.commitCellEdit($(event.currentTarget).closest("td")));

    this.$root.on("keydown", "input", (event) => {
      if ($(event.currentTarget).hasClass("ocw-cell-editor")) return;
      if (event.key === "Enter") this.applyFilters();
    });

    this.$root.on("input change", "[data-filter]", (event) => {
      const field = $(event.currentTarget).attr("data-filter");
      this.filters[field] = $(event.currentTarget).val();
      this.renderFilterChips();
    });
    $(window)
      .off("popstate.ocwBatchFocus")
      .on("popstate.ocwBatchFocus", () => {
        if (this.handleWorkbenchPopState) {
          this.handleWorkbenchPopState().catch((error) => this.showError(error));
          return;
        }
        this.applyBatchFocusFromUrl().catch((error) => this.showError(error));
      });
  }
