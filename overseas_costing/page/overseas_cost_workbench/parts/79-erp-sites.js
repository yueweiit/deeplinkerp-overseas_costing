  /**
   * 多站点 ERP 同步界面：站点级冻结预览、账本状态、只重试失败站点。
   *
   * 服务端这条链（`preview_site_sync_plan` / `save_site_sync_plan` /
   * `get_site_sync_requests` / `reconcile_erp_request` / `retry_erp_request`）早已就绪，
   * 缺的只是界面。站点、公司、仓库、单据分组、可推送与否全部取自服务端返回值：
   * 站点是 `project_collection` 经服务端路由解析出来的，前端复算一定会漂。
   */
  ensureErpSiteState() {
    const batchName = String(this.detailState?.batchName || this.drawerBatchName || this.activeBatchName || "");
    if (!this.erpSiteState || this.erpSiteState.batchName !== batchName) {
      this.erpSiteState = {
        batchName,
        plan: null,
        ledger: null,
        work: null,
        failure: "",
        loading: false,
        requestId: 0,
        inFlight: new Set(),
      };
    }
    return this.erpSiteState;
  }

  /**
   * 站点同步状态的中文标签与色调。词表只有服务端 `build_erp_work_state` 那五个。
   *
   * 页面不再从账本行自己推一遍站点状态：`erp_work` 是服务端算好的投影，前端复算一定会和
   * 「谁是当前结果哈希」「哪些站点本次会推到」这些服务端才知道的事脱节。认不出的取值一律
   * 落 muted，绝不当成功 —— 远端可能压根没建单。
   */
  erpSiteStateMeta(state) {
    const table = {
      SYNCED: { label: ocTranslate("已同步"), tone: "done" },
      UPDATE_REQUIRED: { label: ocTranslate("同步的是旧成本"), tone: "warn" },
      IN_PROGRESS: { label: ocTranslate("同步中"), tone: "pending" },
      ATTENTION_REQUIRED: { label: ocTranslate("需处理"), tone: "error" },
      NOT_PUSHED: { label: ocTranslate("未推送"), tone: "muted" },
    };
    const key = String(state || "").trim().toUpperCase();
    return table[key] || { label: key || ocTranslate("未知状态"), tone: "muted" };
  }

  erpSiteOverallMeta(overall) {
    const table = {
      EMPTY: { label: ocTranslate("没有可推送的站点"), tone: "muted" },
      NOT_STARTED: { label: ocTranslate("尚未推送"), tone: "muted" },
      IN_PROGRESS: { label: ocTranslate("推送中"), tone: "pending" },
      PARTIAL: { label: ocTranslate("部分站点未推送"), tone: "warn" },
      UPDATE_REQUIRED: { label: ocTranslate("有站点同步的是旧成本"), tone: "warn" },
      ATTENTION_REQUIRED: { label: ocTranslate("有站点需要处理"), tone: "error" },
      SYNCED: { label: ocTranslate("全部站点已同步"), tone: "done" },
    };
    const key = String(overall || "").trim().toUpperCase();
    return table[key] || { label: key || ocTranslate("状态未知"), tone: "muted" };
  }

  /**
   * 账本行（单条同步请求）的状态标签。
   *
   * 与站点级 `erpSiteStateMeta` 分开：站点状态是 `build_erp_work_state` 按站点聚合出来的
   * 结论（SYNCED / UPDATE_REQUIRED…），账本行是 `Overseas Cost ERP Sync Request` 的
   * `status` 原值（PENDING / SUCCESS / FAILED / UNCERTAIN…）。两者词表不同，不能互相套用
   * —— 把 `FAILED` 丢进站点词表只会得到「未知状态」。
   */
  erpSiteRequestStatusMeta(status) {
    const table = {
      PENDING: { label: ocTranslate("待推送"), tone: "muted" },
      RUNNING: { label: ocTranslate("推送中"), tone: "pending" },
      SUCCESS: { label: ocTranslate("已推送"), tone: "done" },
      FAILED: { label: ocTranslate("推送失败"), tone: "error" },
      UNCERTAIN: { label: ocTranslate("结果待核对"), tone: "warn" },
      SUPERSEDED: { label: ocTranslate("已被替代"), tone: "muted" },
      MANUAL_REQUIRED: { label: ocTranslate("需人工处理"), tone: "error" },
    };
    const key = String(status || "").trim().toUpperCase();
    return table[key] || { label: key || ocTranslate("未知状态"), tone: "muted" };
  }

  /** 站点级投影：优先用分站点计划里那份（它才知道当前哈希与本次会推到哪些站点）。 */
  erpSiteWork(plan = null, ledger = null) {
    return plan?.erp_work || ledger?.erp_work || null;
  }

  erpSiteWorkByCode(work = null) {
    const grouped = {};
    (Array.isArray(work?.sites) ? work.sites : []).forEach((site) => {
      const code = String(site.site_code || "");
      if (code) grouped[code] = site;
    });
    return grouped;
  }

  renderErpSiteStateChip(site = null) {
    if (!site) return `<span class="ocw-erp-sites-chip is-muted">${ocTranslate("状态未知")}</span>`;
    const meta = this.erpSiteStateMeta(site.state);
    return `<span class="ocw-erp-sites-chip is-${this.escape(meta.tone)}">${this.escape(ocTranslate(meta.label))}</span>`;
  }

  /**
   * 站点 → 远端单据（服务端投影，见 `list_remote_documents`）。
   *
   * 单号与打开地址都只在服务端算：单号在关联表里，地址要按站点自己的接口配置拼。
   * 页面自己从账本行推一遍迟早会和「哪张单属于这一版」脱节。
   */
  erpSiteRemoteDocsByCode(remoteDocuments = null) {
    const source = Array.isArray(remoteDocuments)
      ? remoteDocuments
      : this.erpSiteState?.ledger?.remote_documents;
    const grouped = {};
    (Array.isArray(source) ? source : []).forEach((entry) => {
      const code = String(entry?.site_code || "");
      if (code) grouped[code] = Array.isArray(entry?.documents) ? entry.documents : [];
    });
    return grouped;
  }

  erpSiteRemoteDocuments(siteCode = "", remoteDocuments = null) {
    const code = String(siteCode || "");
    if (!code) return [];
    return this.erpSiteRemoteDocsByCode(remoteDocuments)[code] || [];
  }

  /**
   * 远端单据投影是否已经读到。
   *
   * 账本读失败时 `remote_documents` 压根没来 —— 这时绝不能报「尚未建单」，
   * 那会把「读不到」说成「没有」，与「认不出的状态一律落 muted」同一套纪律。
   */
  erpSiteRemoteDocsKnown() {
    return Array.isArray(this.erpSiteState?.ledger?.remote_documents);
  }

  /** 站点行上的跳转入口：没有远端单据就不给按钮，绝不给一个点了没反应的东西。 */
  renderErpSiteDocumentsCell(site = {}) {
    if (!this.erpSiteRemoteDocsKnown()) return `<span class="ocw-erp-sites-hint">${ocTranslate("单据未读取")}</span>`;
    const documents = this.erpSiteRemoteDocuments(site.site_code);
    if (!documents.length) return `<span class="ocw-erp-sites-hint">${ocTranslate("尚未在 ERP 建单")}</span>`;
    const title = documents.map((document) => String(document.name || "")).filter(Boolean).join("、");
    return `<button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="erp-site-documents"
      data-site-code="${this.escape(String(site.site_code || ""))}" title="${this.escape(ocTranslate(title))}">${ocTranslate("在 ERP 查看（{0}）", [this.escape(String(documents.length))])}</button>`;
  }

  erpRemoteDocstatusLabel(docstatus) {
    const value = Number(docstatus);
    if (value === 1) return ocTranslate("已提交");
    if (value === 2) return ocTranslate("已作废");
    return ocTranslate("草稿");
  }

  /**
   * 打开某站点在 ERP 的单据：只有一张就直接跳，多张先列出来让人挑
   * （一个站点可能推成好几张采购单）。链接用普通链接渲染，点不动是不可能的。
   */
  openErpSiteRemoteDocuments(siteCode = "") {
    const code = String(siteCode || "");
    if (!this.erpSiteRemoteDocsKnown()) {
      this.showPendingFeature("还没有读到远端单据信息，请先刷新状态。");
      return null;
    }
    const documents = this.erpSiteRemoteDocuments(code);
    const usable = documents.filter((document) => String(document?.url || ""));
    if (!usable.length) {
      this.showPendingFeature(
        documents.length
          ? "远端单据号有，但打开地址拼不出来（站点未配置接口地址）。请在 ERP 里按单号手工查找。"
          : "该站点还没有在 ERP 建立单据。"
      );
      return null;
    }
    if (usable.length === 1) {
      this.openBrowserTab(usable[0].url, String(usable[0].name || "ERP 单据"));
      return null;
    }
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("{0} 在 ERP 的单据（{1} 张）", [code || "站点", usable.length]),
      fields: [{ fieldtype: "HTML", fieldname: "documents", options: this.renderErpRemoteDocumentList(usable) }],
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-erp-site-document-dialog");
    return dialog;
  }

  renderErpRemoteDocumentList(documents = []) {
    const rows = documents.map((document) => `
      <tr>
        <td>${this.escape(String(document.doctype || "单据"))}</td>
        <td><strong>${this.escape(String(document.name || "--"))}</strong></td>
        <td>${this.escape(this.erpRemoteDocstatusLabel(document.docstatus))}</td>
        <td>${this.escape(String(document.line_count ?? 0))}</td>
        <td><a class="ocw-link-btn" href="${this.escape(String(document.url || ""))}" target="_blank" rel="noopener noreferrer">${ocTranslate("打开")}</a></td>
      </tr>`).join("");
    return `
      <div class="ocw-erp-site-documents">
        <p class="ocw-erp-sites-note">${ocTranslate("单号取自本批次已保存的远端单据关联记录；打开的是 ERP 里的单据页，需要该 ERP 的登录状态。")}</p>
        <table class="ocw-erp-sites-table">
          <thead><tr><th>${ocTranslate("单据类型")}</th><th>${ocTranslate("单号")}</th><th>${ocTranslate("状态")}</th><th>${ocTranslate("物料行")}</th><th>${ocTranslate("跳转")}</th></tr></thead>
          <tbody>${rows || `<tr><td colspan="5" class="ocw-erp-sites-empty">${ocTranslate("没有可打开的单据")}</td></tr>`}</tbody>
        </table>
      </div>`;
  }

  /**
   * 推送成功后把「去 ERP 看哪张单」直接摆出来 —— 否则用户只能猜。
   *
   * 读的是同一份服务端投影（账本 + 关联表），没有第二套单号来源；
   * 一条都读不到时静默返回。这是善后动作，任何一步失败都只能安静收场，
   * 绝不把异常甩回调用方 —— 推送已经成功了。
   */
  async announceErpPushDocuments(batchName = "") {
    const batch = this.findBatch(batchName || this.drawerBatchName || this.activeBatchName || "");
    if (!batch) return null;
    try {
      await this.loadErpSiteSync({ batchName: batch.name });
    } catch (error) {
      console.warn("[overseas-cost-workbench] 推送后读取远端单据失败", error);
      return null;
    }
    const groups = Object.entries(this.erpSiteRemoteDocsByCode())
      .map(([code, documents]) => ({
        site_code: code,
        documents: (Array.isArray(documents) ? documents : []).filter((document) => String(document?.url || "")),
      }))
      .filter((group) => group.documents.length);
    if (!groups.length) return null;
    const total = groups.reduce((sum, group) => sum + group.documents.length, 0);
    try {
      const dialog = new frappe.ui.Dialog({
        title: ocTranslate("已在 ERP 建立 {0} 张单据", [total]),
        fields: [
          {
            fieldtype: "HTML",
            fieldname: "documents",
            options: groups
              .map(
                (group) => `<section class="ocw-erp-site-documents">
                  <h4>${this.escape(group.site_code)}</h4>
                  ${this.renderErpRemoteDocumentList(group.documents)}
                </section>`
              )
              .join(""),
          },
        ],
      });
      dialog.show();
      dialog.$wrapper.addClass("ocw-erp-site-document-dialog");
      return dialog;
    } catch (error) {
      console.warn("[overseas-cost-workbench] 推送后的 ERP 单据提示未能弹出", error);
      return null;
    }
  }

  /** 把服务端站点预览归一成渲染用的形状，缺失字段一律兜空，别让模板崩。 */
  erpSitePlanPreview(plan = {}) {
    const preview = plan?.push_state?.preview || {};
    const sites = Array.isArray(preview.sites) ? preview.sites : [];
    return {
      sites: sites.map((site) => {
        const groups = Array.isArray(site.groups) ? site.groups : [];
        return {
          site_code: String(site.site_code || ""),
          groups,
          itemCount: groups.reduce((total, group) => total + (Array.isArray(group.items) ? group.items.length : 0), 0),
          companies: [...new Set(groups.map((group) => String(group.subsidiary_code || "")).filter(Boolean))],
          total_cost_rmb: site.total_cost_rmb || 0,
          allocated_fee_rmb: site.allocated_fee_rmb || 0,
        };
      }),
      blocking: Array.isArray(preview.blocking) ? preview.blocking : [],
      sourceTotalRmb: preview.source_total_cost_rmb || 0,
      previewTotalRmb: preview.preview_total_cost_rmb || 0,
      sourceFeeRmb: preview.source_fee_total_rmb || 0,
      previewFeeRmb: preview.preview_allocated_fee_rmb || 0,
      complete: plan?.complete !== false,
      ready: plan?.ready === true,
      hash: String(plan?.cost_result_hash || ""),
    };
  }

  renderErpSiteHash(hash = "") {
    const text = String(hash || "");
    if (!text) return `<span class="ocw-erp-sites-hash is-muted">${ocTranslate("无结果哈希")}</span>`;
    return `<span class="ocw-erp-sites-hash" title="${this.escape(text)}">${ocTranslate("结果哈希 {0}", [this.escape(text.slice(0, 12))])}</span>`;
  }

  /**
   * 读取站点同步计划与本地账本。
   *
   * 账本读失败（例如成本还没确认，服务端拿不到推送上下文）不算整块失败：
   * 预览仍要能显示，否则用户看不到"为什么推不了"。
   */
  async loadErpSiteSync({ batchName = "", readLedger = true } = {}) {
    const batch = this.findBatch(batchName || this.drawerBatchName || this.activeBatchName || this.detailState?.batchName || "");
    if (!batch) return null;
    const state = this.ensureErpSiteState();
    const requestId = ++state.requestId;
    state.loading = true;
    this.renderErpSitePanelInto();
    try {
      const plan = await this.call("overseas_costing.api.writeback.preview_site_sync_plan", {
        batch_name: batch.name,
        version_name: batch.current_version || null,
      });
      if (requestId !== state.requestId) return null;
      state.plan = plan || null;
      state.failure = "";
      if (readLedger && plan?.batch_name) {
        try {
          const ledger = await this.call("overseas_costing.api.writeback.get_site_sync_requests", {
            batch_name: batch.name,
            version_name: batch.current_version || null,
            limit: 100,
          });
          if (requestId !== state.requestId) return null;
          state.ledger = ledger && ledger.ok !== false ? ledger : null;
        } catch (error) {
          console.warn("[overseas-cost-workbench] ERP 站点账本读取失败", error);
          state.ledger = null;
        }
      }
      state.work = this.erpSiteWork(state.plan, state.ledger);
      return state.plan;
    } catch (error) {
      if (requestId !== state.requestId) return null;
      state.plan = null;
      state.ledger = null;
      state.work = null;
      state.failure = this.normalizeErrorMessage?.(error) || String(error?.message || error || "站点同步状态读取失败");
      return null;
    } finally {
      if (requestId === state.requestId) {
        state.loading = false;
        this.renderErpSitePanelInto();
      }
    }
  }

  /**
   * 详情页概览里的站点面板。只有成本已确认（或正处于 ERP 队列）才出现 ——
   * 146 个未确认批次不该多出一块永远空的区域。
   */
  renderDetailErpSites(batch = {}) {
    if (!this.erpSitePanelVisible(batch)) return "";
    return `<section class="ocw-erp-sites" data-area="detail-erp-sites">${this.renderErpSiteStatusPanel()}</section>`;
  }

  erpSitePanelVisible(batch = {}) {
    const status = [batch.confirm_status, batch.status].map((value) => String(value || "").toLowerCase());
    return status.some((value) => value.includes("confirmed")) || this.viewState?.task === "erp";
  }

  renderErpSitePanelInto() {
    const $area = this.$root.find("[data-area='detail-erp-sites']");
    if (!$area.length) return;
    $area.html(this.renderErpSiteStatusPanel());
  }

  async refreshErpSitePanel() {
    const batch = this.getDetailBatch?.();
    if (!batch || !this.erpSitePanelVisible(batch)) return;
    await this.loadErpSiteSync({ batchName: batch.name });
    this.renderErpSitePanelInto();
  }

  renderErpSiteStatusPanel() {
    const state = this.ensureErpSiteState();
    if (state.loading && !state.plan) {
      return `<div class="ocw-erp-sites-loading">${ocTranslate("正在读取站点同步状态…")}</div>`;
    }
    if (state.failure) {
      return `
        <div class="ocw-erp-sites-head"><div><span>${ocTranslate("ERP 站点推送")}</span><strong>${ocTranslate("状态读取失败")}</strong></div>
          <div class="ocw-erp-sites-actions"><button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="erp-site-refresh">${ocTranslate("重试")}</button></div>
        </div>
        <p class="ocw-erp-sites-note is-error">${this.escape(state.failure)}</p>`;
    }
    const plan = state.plan;
    if (!plan) {
      return `
        <div class="ocw-erp-sites-head"><div><span>${ocTranslate("ERP 站点推送")}</span><strong>${ocTranslate("尚未生成同步草稿")}</strong></div>
          <div class="ocw-erp-sites-actions">
            <button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="erp-site-refresh">${ocTranslate("刷新")}</button>
            <button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="erp-site-preview">${ocTranslate("站点推送预览")}</button>
          </div>
        </div>`;
    }
    const preview = this.erpSitePlanPreview(plan);
    const work = this.erpSiteWork(plan, state.ledger);
    const workBySite = this.erpSiteWorkByCode(work);
    const rows = preview.sites.map((site) => {
      const siteWork = workBySite[site.site_code] || null;
      const companyText = site.companies.length ? site.companies.join(" / ") : "--";
      return `
        <tr>
          <td><strong>${this.escape(site.site_code || "--")}</strong>${site.site_code ? `<small>${this.escape(companyText)}</small>` : ""}</td>
          <td>${ocTranslate("{0} 组 / {1} 行", [this.escape(String(site.groups.length)), this.escape(String(site.itemCount))])}</td>
          <td>${this.escape(this.formatMoney(site.total_cost_rmb))}</td>
          <td>${this.escape(this.formatMoney(site.allocated_fee_rmb))}</td>
          <td>${this.renderErpSiteStateChip(siteWork)}</td>
          <td>${this.renderErpSiteDocumentsCell(site)}</td>
        </tr>`;
    }).join("");
    const overdueSites = (work?.sites || []).filter((site) => site.todo);
    const overall = work ? this.erpSiteOverallMeta(work.overall) : null;
    const blockingNote = preview.complete
      ? ""
      : `<p class="ocw-erp-sites-note is-warn">${ocTranslate("还有 {0} 个物料组未通过路由、供应商或仓库门槛，不会随本次推送发出。", [this.escape(String(preview.blocking.length))])}</p>`;
    return `
      <div class="ocw-erp-sites-head">
        <div>
          <span>${ocTranslate("ERP 站点推送")}</span>
          <strong>${ocTranslate("{0} 个站点 · {1} 个单据组 · {2} RMB", [this.escape(String(preview.sites.length)), this.escape(String(preview.sites.reduce((total, site) => total + site.groups.length, 0))), this.escape(this.formatMoney(preview.previewTotalRmb))])}</strong>
          <small>${preview.ready ? ocTranslate("已通过推送门槛") : ocTranslate("存在阻断项，暂不能推送")}${preview.complete ? "" : ocTranslate(" · 仅部分可推送")}${overall ? ` · ${this.escape(ocTranslate(overall.label))}` : ""}</small>
        </div>
        <div class="ocw-erp-sites-actions">
          <button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="erp-site-refresh">${ocTranslate("刷新状态")}</button>
          <button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="erp-site-preview">${ocTranslate("站点推送预览")}</button>
        </div>
      </div>
      <div class="ocw-erp-sites-scroll">
        <table class="ocw-erp-sites-table">
          <thead><tr><th>${ocTranslate("站点 / ERP 公司")}</th><th>${ocTranslate("目标单据")}</th><th>${ocTranslate("综合成本")}</th><th>${ocTranslate("分摊费用")}</th><th>${ocTranslate("同步状态")}</th><th>${ocTranslate("ERP 单据")}</th></tr></thead>
          <tbody>${rows || `<tr><td colspan="6" class="ocw-erp-sites-empty">${ocTranslate("当前没有可推送的站点分组")}</td></tr>`}</tbody>
        </table>
      </div>
      ${blockingNote}
      ${overdueSites.length ? `
        <div class="ocw-erp-sites-ledger">
          <h4>${ocTranslate("还欠处理的站点（{0} 个）", [this.escape(String(overdueSites.length))])}</h4>
          <ul>${overdueSites.map((site) => this.renderErpSiteTodoRow(site)).join("")}</ul>
        </div>` : ""}
      ${this.renderErpSiteQueueBlock()}
    `;
  }

  /**
   * 一个"还欠处理站点"的条目。
   *
   * 核对/重试入口只按服务端 `todo.action === "reconcile_erp"` 给：服务端
   * `RECONCILABLE_STATUSES` 只认 FAILED/UNCERTAIN，别的状态调过去会被判成 SKIP。
   * 给一个必然被拒的按钮，比写清"要人去 ERP 侧处理"更误导。
   */
  renderErpSiteTodoRow(site = {}) {
    const meta = this.erpSiteStateMeta(site.state);
    const todo = site.todo || {};
    const requestId = String(site.request_id || "");
    const reason = String(site.error_message || site.error_code || "").trim();
    const retries = Number(site.attempt_count || 0);
    const actions = todo.action === "reconcile_erp"
      ? `
          <button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="erp-site-reconcile" data-request-id="${this.escape(requestId)}">${ocTranslate("核对远端")}</button>
          <button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="erp-site-retry" data-request-id="${this.escape(requestId)}">${ocTranslate("核对后重试")}</button>`
      : `<span class="ocw-erp-sites-hint">${this.escape(todo.label || ocTranslate("请在 ERP 侧处理后刷新状态"))}</span>`;
    return `
      <li class="ocw-erp-sites-ledger-row">
        <div>
          <strong>${this.escape(String(site.site_code || "--"))}</strong>
          <span class="ocw-erp-sites-state is-${this.escape(meta.tone)}">${this.escape(todo.label || meta.label)}</span>
          ${retries ? `<em>${ocTranslate("已尝试 {0} 次", [this.escape(String(retries))])}</em>` : ""}
          <small>${this.renderErpSiteHash(site.cost_result_hash)}</small>
          ${reason ? `<p>${this.escape(reason)}</p>` : ""}
        </div>
        <div class="ocw-erp-sites-ledger-actions">${actions}</div>
      </li>`;
  }

  /**
   * 本地同步队列：账本里逐条请求。
   *
   * 「还欠处理的站点」是按站点聚合的结论，看不出这一票到底欠了哪几条；这里把账本行
   * 原样摊开，站点、操作、状态、尝试次数、失败原因一条一条对得上，重试/核对按钮也能
   * 照 `request_id` 精确落到某一条。
   *
   * 账本没读到时返回 `null`（而不是空数组），此时整块不渲染：把「读不到」画成
   * 「没有队列」等于告诉用户"没欠东西"，与远端单据那条同一条纪律。
   */
  erpSiteLedgerQueue() {
    const items = this.erpSiteState?.ledger?.items;
    return Array.isArray(items) ? items : null;
  }

  renderErpSiteQueueRow(item = {}) {
    const meta = this.erpSiteRequestStatusMeta(item.status);
    const requestId = String(item.request_id || "");
    const reason = String(item.error_message || item.error_code || "").trim();
    const retries = Number(item.attempt_count || 0);
    const operation = String(item.operation || "CREATE").trim() || "CREATE";
    return `
      <li class="ocw-erp-sites-ledger-row" data-request-id="${this.escape(requestId)}">
        <div>
          <strong>${this.escape(String(item.site_code || "--"))}</strong>
          <span class="ocw-erp-sites-state is-${this.escape(meta.tone)}">${this.escape(ocTranslate(meta.label))}</span>
          ${retries ? `<em>${ocTranslate("已尝试 {0} 次", [this.escape(String(retries))])}</em>` : ""}
          <small>${this.escape(operation)} · ${this.renderErpSiteHash(item.cost_result_hash)}</small>
          ${requestId ? `<small>${ocTranslate("请求 {0}", [this.escape(requestId.slice(0, 12))])}</small>` : ""}
          ${reason ? `<p>${this.escape(reason)}</p>` : ""}
        </div>
      </li>`;
  }

  renderErpSiteQueueBlock() {
    const items = this.erpSiteLedgerQueue();
    if (items === null) return "";
    const head = `<h4>${ocTranslate("本地同步队列（{0} 条）", [this.escape(String(items.length))])}</h4>`;
    if (!items.length) {
      return `<div class="ocw-erp-sites-ledger" data-area="erp-sync-queue">${head}<p class="ocw-erp-sites-note">${ocTranslate("尚未生成同步请求。")}</p></div>`;
    }
    return `
      <div class="ocw-erp-sites-ledger" data-area="erp-sync-queue">
        ${head}
        <ul>${items.map((item) => this.renderErpSiteQueueRow(item)).join("")}</ul>
      </div>`;
  }

  /** 冻结预览：推送前把要发到哪些站点/公司、多少钱、哪些行被拦，一次摊开。 */
  renderErpSiteSyncPreview(plan = {}) {
    const preview = this.erpSitePlanPreview(plan);
    const estimated = this.erpEstimatedFeeCount();
    const cards = preview.sites.map((site) => {
      const groups = site.groups.map((group) => `
        <tr>
          <td>${this.escape(String(group.subsidiary_code || "--"))}</td>
          <td>${this.escape(String(group.supplier || "未设置"))}</td>
          <td>${this.escape(String(group.warehouse || "--"))}</td>
          <td>${this.escape(String(group.purchase_currency || "CNY"))} / ${this.escape(String(group.erp_stock_uom || "--"))}</td>
          <td>${ocTranslate("{0} 行", [this.escape(String((group.items || []).length))])}</td>
          <td>${this.escape(this.formatMoney(group.total_cost_rmb))}</td>
        </tr>`).join("");
      return `
        <article class="ocw-erp-site-card">
          <header>
            <div><span>${ocTranslate("站点")}</span><strong>${this.escape(site.site_code || "--")}</strong></div>
            <div><span>${ocTranslate("ERP 公司")}</span><strong>${this.escape(site.companies.join(" / ") || "--")}</strong></div>
            <div><span>${ocTranslate("单据组")}</span><strong>${this.escape(String(site.groups.length))}</strong></div>
            <div><span>${ocTranslate("物料行")}</span><strong>${this.escape(String(site.itemCount))}</strong></div>
            <div><span>${ocTranslate("综合成本")}</span><strong>${this.escape(this.formatMoney(site.total_cost_rmb))}</strong></div>
          </header>
          <table class="ocw-erp-site-groups">
            <thead><tr><th>${ocTranslate("ERP 公司")}</th><th>${ocTranslate("供应商")}</th><th>${ocTranslate("收货仓库")}</th><th>${ocTranslate("币种 / 单位")}</th><th>${ocTranslate("行数")}</th><th>${ocTranslate("综合成本")}</th></tr></thead>
            <tbody>${groups || `<tr><td colspan="6" class="ocw-erp-sites-empty">${ocTranslate("该站点没有可推送的分组")}</td></tr>`}</tbody>
          </table>
        </article>`;
    }).join("");
    return `
      <div class="ocw-erp-sites-preview">
        <div class="ocw-erp-sites-preview-summary">
          <div><span>${ocTranslate("站点")}</span><strong>${this.escape(String(preview.sites.length))}</strong></div>
          <div><span>${ocTranslate("单据组")}</span><strong>${this.escape(String(preview.sites.reduce((total, site) => total + site.groups.length, 0)))}</strong></div>
          <div><span>${ocTranslate("物料行")}</span><strong>${this.escape(String(preview.sites.reduce((total, site) => total + site.itemCount, 0)))}</strong></div>
          <div><span>${ocTranslate("综合成本")}</span><strong>${this.escape(this.formatMoney(preview.previewTotalRmb))} RMB</strong></div>
          <div><span>${ocTranslate("分摊费用")}</span><strong>${this.escape(this.formatMoney(preview.previewFeeRmb))} RMB</strong></div>
        </div>
        <p class="ocw-erp-sites-note">${ocTranslate("{0} —— 确认后按这个哈希生成幂等请求，重复点击不会在远端多建一张采购单。", [this.renderErpSiteHash(preview.hash)])}</p>
        ${this.renderErpSiteOverdueNote(plan)}
        ${estimated ? `<p class="ocw-erp-sites-note is-warn">${ocTranslate("本次含 {0} 项暂估费用；实际费用出来后需要另行更新远端成本。", [this.escape(String(estimated))])}</p>` : ""}
        ${preview.complete ? "" : `<p class="ocw-erp-sites-note is-warn">${ocTranslate("另有 {0} 个物料组未通过路由、供应商或仓库门槛，本次不会发出；补齐后可再次推送。", [this.escape(String(preview.blocking.length))])}</p>`}
        ${cards || `<p class="ocw-erp-sites-note is-warn">${ocTranslate("当前没有可推送的站点分组。")}</p>`}
      </div>`;
  }

  /** 推送前先说明这次会补上/更新哪些站点，别让人以为点一下就把所有站点都对齐了。 */
  renderErpSiteOverdueNote(plan = {}) {
    const work = this.erpSiteWork(plan);
    const behind = (work?.sites || []).filter((site) => site.todo);
    if (!behind.length) return "";
    const text = behind
      .map((site) => `${site.todo.label}（${String(site.site_code || "--")}）`)
      .join("；");
    return `<p class="ocw-erp-sites-note is-warn">${ocTranslate("本站点推送会处理：{0}。", [this.escape(text)])}</p>`;
  }

  /** 暂估费用计数只读已加载的费用状态，不为它额外发一次请求。 */
  erpEstimatedFeeCount() {
    const fees = this.materialFeeState?.fees?.fees;
    if (!Array.isArray(fees)) return 0;
    return fees.filter((fee) => String(fee.amount_state || fee.amount_status || "").toUpperCase() === "ESTIMATED").length;
  }

  async openErpSiteSyncDialog(batchName = "") {
    const batch = this.findBatch(batchName || this.drawerBatchName || this.activeBatchName || "");
    if (!batch) return null;
    const plan = await this.loadErpSiteSync({ batchName: batch.name });
    if (!plan) return null;
    if (!plan.ready) {
      // 阻断原因与待补资料继续走既有弹窗，不在这里另造一套文案。
      this.showErpFlowBlock({ ...plan, batch_name: batch.name }, "暂不能推送 ERP");
      return null;
    }
    const sizes = this.erpSitePlanPreview(plan).sites.length;
    let pushing = false;
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("ERP 站点推送预览（{0} 个站点）", [sizes]),
      fields: [{ fieldtype: "HTML", fieldname: "plan", options: this.renderErpSiteSyncPreview(plan) }],
      primary_action_label: ocTranslate("确认并推送"),
      primary_action: async () => {
        // 双击不该变成第二次业务：第一条推送没回来之前直接吞掉后续点击。
        if (pushing) return;
        pushing = true;
        dialog.hide();
        try {
          await this.queueErpWriteback(batch.name);
        } catch (error) {
          this.showError(error);
        } finally {
          pushing = false;
        }
      },
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-erp-site-dialog");
    return dialog;
  }

  async reconcileErpSiteRequest(requestId = "") {
    return this.runErpSiteRequestAction({
      requestId,
      action: "reconcile",
      busyLabel: "正在核对远端单据",
      method: "overseas_costing.api.writeback.reconcile_erp_request",
      done: (result) => {
        // 核对结论由服务端给（远端到底有没有单、是草稿还是已提交），前端不复述一遍。
        return ocTranslate("远端核对结果：{0}", [String(result?.message || result?.status || "已完成")]);
      },
    });
  }

  async retryErpSiteRequest(requestId = "") {
    return this.runErpSiteRequestAction({
      requestId,
      action: "retry",
      busyLabel: "正在核对远端并重试",
      method: "overseas_costing.api.writeback.retry_erp_request",
      done: (result) => String(result?.message || "重试请求已处理。"),
    });
  }

  /**
   * 核对/重试共用的入口：同一个按钮不可能连点两次发出两次请求。
   *
   * 重试本身会先在服务端核对远端，这里不做任何"跳过核对直接重发"的快捷路径 ——
   * 远端已经建单的情况下重发会在 ERP 里多出一张采购单。
   */
  async runErpSiteRequestAction({ requestId = "", action = "", busyLabel = "", method = "", done = null } = {}) {
    const id = String(requestId || "");
    if (!id) return null;
    const state = this.ensureErpSiteState();
    const key = `${action}:${id}`;
    if (state.inFlight.has(key)) return null;
    state.inFlight.add(key);
    frappe.show_alert?.({ message: busyLabel || ocTranslate("处理中"), indicator: "blue" });
    try {
      const result = await this.call(method, { batch_name: state.batchName, request_id: id }, true);
      const ok = result?.ok !== false;
      frappe.show_alert?.({
        message: ok ? (done?.(result) || ocTranslate("已处理")) : String(result?.message || "请求未处理成功"),
        indicator: ok ? "green" : "orange",
      });
      if (ok) {
        this.recordUsage?.("PUSH_ERP", { batch: this.findBatch(state.batchName), remark: ocTranslate("ERP 请求{0}：{1}", [action === "retry" ? "重试" : "核对", id]) });
      }
      await this.loadErpSiteSync({ batchName: state.batchName });
      return result;
    } catch (error) {
      this.showError(error);
      return null;
    } finally {
      state.inFlight.delete(key);
      this.renderErpSitePanelInto();
    }
  }
