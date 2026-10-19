  ensureReviewCommunicationState() {
    const batchName = String(this.detailState?.batchName || "");
    if (!this.reviewCommunicationState || this.reviewCommunicationState.batchName !== batchName) {
      this.reviewCommunicationState = {
        batchName,
        drawerOpen: false,
        drafts: [],
        data: null,
        loading: false,
        submitting: false,
        replyDrafts: {},
        returnTarget: null,
      };
    }
    return this.reviewCommunicationState;
  }

  isFinanceReviewContext() {
    return String(this.viewState?.task || "") === "cost";
  }

  hasUnsavedReviewDrafts() {
    const state = this.reviewCommunicationState;
    return Boolean(state?.drawerOpen && (state.drafts || []).some((row) =>
      String(row.description || "").trim() || (row.attachments || []).length
    ));
  }

  discardReviewDrafts() {
    const state = this.ensureReviewCommunicationState();
    state.drawerOpen = false;
    state.drafts = [];
    this.renderReviewRemediationDrawer();
  }

  reviewTrialSignature() {
    const batch = this.getDetailBatch?.() || {};
    return String(
      batch.summary_snapshot?.input_hash
      || this.detailState?.detail?.summary?.input_hash
      || ""
    );
  }

  canOpenReviewRemediation(batch = {}) {
    const confirmed = String(batch.confirm_status || "").toLowerCase() === "confirmed";
    const state = String(batch.remediation_state || "none").toLowerCase();
    return this.isFinanceReviewContext() && !confirmed && ["", "none", "resolved"].includes(state);
  }

  renderReviewReturnAction(batch = {}) {
    if (!this.canOpenReviewRemediation(batch)) return "";
    return `<button class="ocw-review-return-btn" type="button" data-action="open-review-remediation">${ocTranslate("提出整改")}</button>`;
  }

  renderReviewFeedbackButton(anchor = {}, label = "反馈此项") {
    if (!this.canOpenReviewRemediation(this.getDetailBatch?.() || {})) return "";
    const value = (key) => this.escape(String(anchor[key] || ""));
    return `<button class="ocw-review-feedback-btn" type="button" data-action="review-feedback-item"
      data-review-target-tab="${value("target_tab")}" data-review-target-field="${value("target_field")}"
      data-review-target-row="${value("target_row")}" data-review-target-item="${value("target_item")}">${this.escape(ocTranslate(label))}</button>`;
  }

  reviewAnchorFromElement($element) {
    return {
      target_tab: String($element.attr("data-review-target-tab") || ""),
      target_field: String($element.attr("data-review-target-field") || ""),
      target_row: String($element.attr("data-review-target-row") || ""),
      target_item: String($element.attr("data-review-target-item") || ""),
    };
  }

  reviewAnchorLabel(anchor = {}) {
    const labels = { documents: ocTranslate("资料与费用"), items: ocTranslate("SKU 明细"), vouchers: ocTranslate("凭证核对"), overview: ocTranslate("总览"), dingtalk: ocTranslate("钉钉审批") };
    const parts = [labels[anchor.target_tab] || anchor.target_tab, anchor.target_field, anchor.target_item || anchor.target_row].filter(Boolean);
    return parts.join(" / ");
  }

  addReviewRemediationDraft(anchor = {}) {
    const state = this.ensureReviewCommunicationState();
    state.drafts.push({
      id: `draft-${Date.now()}-${state.drafts.length}`,
      description: "",
      target_tab: String(anchor.target_tab || ""),
      target_field: String(anchor.target_field || ""),
      target_row: String(anchor.target_row || ""),
      target_item: String(anchor.target_item || ""),
      attachments: [],
    });
    return state.drafts.length - 1;
  }

  openReviewRemediationDrawer(anchor = null) {
    const state = this.ensureReviewCommunicationState();
    if (!this.canOpenReviewRemediation(this.getDetailBatch?.() || {})) return;
    state.drawerOpen = true;
    if (anchor || !state.drafts.length) this.addReviewRemediationDraft(anchor || {});
    this.renderReviewRemediationDrawer();
    window.requestAnimationFrame(() => {
      const index = Math.max(0, state.drafts.length - 1);
      this.$root.find(`[data-review-draft-description="${index}"]`).trigger("focus");
    });
  }

  async closeReviewRemediationDrawer(force = false) {
    if (!force && this.hasUnsavedReviewDrafts()) {
      const confirmed = await new Promise((resolve) => frappe.confirm(
        ocTranslate("尚有未提交的整改问题，确认放弃草稿？"),
        () => resolve(true),
        () => resolve(false)
      ));
      if (!confirmed) return false;
    }
    this.discardReviewDrafts();
    return true;
  }

  renderReviewRemediationDrawer() {
    const state = this.ensureReviewCommunicationState();
    let $host = this.$root.find("[data-area='review-drawer-host']");
    if (!$host.length) {
      this.$root.find("[data-area='detail-screen']").append('<div data-area="review-drawer-host"></div>');
      $host = this.$root.find("[data-area='review-drawer-host']");
    }
    if (!state.drawerOpen) {
      $host.empty();
      return;
    }
    const rows = state.drafts.map((row, index) => {
      const anchorLabel = this.reviewAnchorLabel(row);
      return `<article class="ocw-review-draft-card" data-review-draft="${index}">
        <div class="ocw-review-draft-head"><strong>${ocTranslate("整改问题 {0}", [index + 1])}</strong><button type="button" data-action="delete-review-draft" data-index="${index}" aria-label="${ocTranslate('删除该问题')}">${ocTranslate("删除")}</button></div>
        ${anchorLabel ? `<div class="ocw-review-anchor"><span>${ocTranslate("关联位置")}</span><strong>${this.escape(anchorLabel)}</strong><small>${ocTranslate("提交后不可在本轮中修改")}</small></div>` : ""}
        <label><span>${ocTranslate("问题说明")} <em>*</em></span><textarea data-review-draft-description="${index}" rows="4" placeholder="${ocTranslate('请直接说明需要改什么、正确值或核对要求')}">${this.escape(row.description || "")}</textarea></label>
        <div class="ocw-review-attachments">
          ${(row.attachments || []).map((file, fileIndex) => `<span><a href="${this.escape(file.file_url || "#")}" target="_blank" rel="noopener">${this.escape(file.file_name || file.name)}</a><button type="button" data-action="remove-review-attachment" data-index="${index}" data-file-index="${fileIndex}">${ocTranslate("移除")}</button></span>`).join("")}
          <label class="ocw-review-upload"><input type="file" data-review-draft-attachment="${index}" hidden />${ocTranslate("+上传附件（可选）")}</label>
        </div>
      </article>`;
    }).join("");
    const completeCount = state.drafts.filter((row) => String(row.description || "").trim()).length;
    $host.html(`<div class="ocw-review-drawer-mask" data-action="close-review-remediation"></div>
      <aside class="ocw-review-drawer" role="dialog" aria-modal="true" aria-label="${ocTranslate('提出整改')}">
        <header><div><small>${ocTranslate("成本核对")}</small><h2>${ocTranslate("提出整改")}</h2><p>${ocTranslate("先在本地编辑，只有点击“退回整改”才会写入并改变批次状态。")}</p></div><button type="button" data-action="close-review-remediation" aria-label="${ocTranslate('关闭')}">×</button></header>
        <div class="ocw-review-drawer__body">${rows}<button class="ocw-outline-btn" type="button" data-action="add-review-draft">${ocTranslate("+添加一项整改问题")}</button></div>
        <footer class="ocw-review-drawer__footer"><button class="ocw-outline-btn" type="button" data-action="close-review-remediation" ${state.submitting ? "disabled" : ""}>${ocTranslate("取消")}</button><button class="ocw-primary-btn" type="button" data-action="submit-review-remediation" ${!completeCount || completeCount !== state.drafts.length || state.submitting ? "disabled" : ""}>${state.submitting ? ocTranslate("正在退回…") : ocTranslate("退回整改 ({0})", [completeCount])}</button></footer>
      </aside>`);
  }

  async uploadReviewDraftAttachment(index, file) {
    if (!file) return;
    const state = this.ensureReviewCommunicationState();
    const draft = state.drafts[index];
    if (!draft) return;
    try {
      const uploaded = await this.uploadImportFile(file);
      draft.attachments.push({ name: uploaded.name, file_name: uploaded.file_name || file.name, file_url: uploaded.file_url });
      this.renderReviewRemediationDrawer();
    } catch (error) {
      this.showError(error);
    }
  }

  async submitReviewRemediation() {
    const state = this.ensureReviewCommunicationState();
    if (state.submitting) return;
    const issues = state.drafts.map((row) => ({
      description: String(row.description || "").trim(),
      target_tab: row.target_tab || "",
      target_field: row.target_field || "",
      target_row: row.target_row || "",
      target_item: row.target_item || "",
      attachments: (row.attachments || []).map((file) => file.name),
    }));
    if (!issues.length || issues.some((row) => !row.description)) {
      frappe.show_alert({ message: ocTranslate("请填写每一项整改问题。"), indicator: "orange" });
      return;
    }
    state.submitting = true;
    this.renderReviewRemediationDrawer();
    try {
      const result = await this.call("overseas_costing.api.review.return_for_remediation", {
        batch_name: state.batchName,
        version_name: this.detailState.versionName,
        trial_signature: this.reviewTrialSignature(),
        issues_json: JSON.stringify(issues),
      });
      if (!result?.ok) throw new Error(result?.message || "整改退回失败。");
      state.drafts = [];
      state.drawerOpen = false;
      state.data = null;
      this.renderReviewRemediationDrawer();
      await this.refreshDetailSummary({ refreshCurrentTab: false });
      await this.switchDetailTab("review");
      frappe.show_alert({ message: ocTranslate("已退回待整改，采购和墨西哥同事可在复核沟通中回复。"), indicator: "green" });
    } catch (error) {
      this.showError(error);
    } finally {
      state.submitting = false;
      if (state.drawerOpen) this.renderReviewRemediationDrawer();
    }
  }

  reviewStatusLabel(value) {
    return ({ Returned: ocTranslate("待整改"), Resubmitted: ocTranslate("待财务复核"), Resolved: ocTranslate("已完成"), Open: ocTranslate("待处理"), Addressed: ocTranslate("已处理待复核") })[value] || value || "--";
  }

  async loadReviewCommunication(force = false) {
    const state = this.ensureReviewCommunicationState();
    if (state.loading) return state.data;
    if (state.data && !force) return state.data;
    state.loading = true;
    try {
      const result = await this.call("overseas_costing.api.review.get_review_communication", { batch_name: state.batchName });
      if (!result?.ok) throw new Error(result?.message || "复核沟通读取失败。");
      state.data = result;
      return result;
    } finally {
      state.loading = false;
    }
  }

  renderReviewIssue(issue = {}, index = 0, round = {}) {
    const state = this.ensureReviewCommunicationState();
    const isFinance = this.isFinanceReviewContext();
    const reply = Object.prototype.hasOwnProperty.call(state.replyDrafts, issue.name) ? state.replyDrafts[issue.name] : (issue.reply || "");
    const canReply = !isFinance && round.status === "Returned" && issue.status === "Open";
    const canResolve = isFinance && round.status === "Resubmitted" && issue.status === "Addressed";
    const anchor = this.reviewAnchorLabel(issue);
    return `<article class="ocw-review-issue is-${String(issue.status || "open").toLowerCase()}" data-review-issue="${this.escape(issue.name || "")}">
      <header><div><h3>①${index ? `-${index + 1}` : ""} ${this.escape(issue.description || "")}</h3>${anchor ? `<small>${ocTranslate("关联：{0}", [this.escape(anchor)])}</small>` : ""}</div><span>${this.escape(this.reviewStatusLabel(issue.status))}</span></header>
      ${(issue.attachments || []).length ? `<div class="ocw-review-issue-files">${issue.attachments.map((file) => `<a href="${this.escape(file.file_url || "#")}" target="_blank" rel="noopener">📎 ${this.escape(file.file_name || file.name || ocTranslate("附件"))}</a>`).join("")}</div>` : ""}
      ${issue.reply ? `<div class="ocw-review-reply"><strong>${ocTranslate("处理回复")}</strong><p>${this.escape(issue.reply)}</p><small>${this.escape(issue.addressed_by || "--")} · ${this.escape(this.formatDateTimeMinute(issue.addressed_at) || issue.addressed_at || "--")}</small></div>` : ""}
      <footer>
        ${issue.target_tab ? `<button class="ocw-outline-btn" type="button" data-action="review-go-target" data-issue-name="${this.escape(issue.name || "")}">${ocTranslate("去修改")}</button>` : ""}
        ${canReply ? `<textarea data-review-reply="${this.escape(issue.name || "")}" rows="2" placeholder="${ocTranslate('填写处理说明…')}">${this.escape(reply)}</textarea><button class="ocw-primary-btn" type="button" data-action="address-review-issue" data-issue-name="${this.escape(issue.name || "")}">${ocTranslate("回复并标记已处理")}</button>` : ""}
        ${canResolve ? `<button class="ocw-primary-btn" type="button" data-action="resolve-review-issue" data-issue-name="${this.escape(issue.name || "")}">${ocTranslate("确认整改完成")}</button>` : ""}
      </footer>
    </article>`;
  }

  renderReviewRound(round = {}, current = false) {
    const issues = round.issues || [];
    const allAddressed = issues.length && issues.every((row) => row.status === "Addressed");
    const isProcurement = !this.isFinanceReviewContext();
    const content = `<div class="ocw-review-round-head"><div><h2>${current ? ocTranslate("本轮整改") : ocTranslate("第 {0} 轮", [Number(round.round_no || 0)])}</h2><p>${ocTranslate("财务于 {0} 退回 · 共 {1} 项", [this.escape(this.formatDateTimeMinute(round.returned_at) || round.returned_at || "--"), issues.length])}</p></div><span>${this.escape(this.reviewStatusLabel(round.status))}</span></div>
      <div class="ocw-review-issue-list">${issues.map((issue, index) => this.renderReviewIssue(issue, index, round)).join("")}</div>
      ${current && isProcurement && round.status === "Returned" ? `<div class="ocw-review-resubmit"><p>${allAddressed ? ocTranslate("全部问题已处理，提交时服务器会再次校验当前试算。") : ocTranslate("请先逐项回复并标记已处理。")}</p><button class="ocw-primary-btn" type="button" data-action="resubmit-review-round" ${allAddressed ? "" : "disabled"}>${ocTranslate("提交财务复核")}</button></div>` : ""}`;
    return current ? `<section class="ocw-review-current">${content}</section>` : `<details class="ocw-review-history-round"><summary>${ocTranslate("第 {0} 轮 · {1}", [Number(round.round_no || 0), this.escape(this.reviewStatusLabel(round.status))])}</summary>${content}</details>`;
  }

  async renderReviewCommunicationTab() {
    const batchName = this.detailState.batchName;
    this.renderDetailTabLoading("正在读取复核沟通记录");
    try {
      const data = await this.loadReviewCommunication(true);
      if (this.detailState.batchName !== batchName || this.detailState.tab !== "review") return;
      const current = data.current_round;
      const history = data.history || [];
      this.$root.find("[data-area='detail-content']").html(`<div class="ocw-review-workspace">
        <div class="ocw-detail-section-head"><div><span>${ocTranslate("可追溯协作")}</span><h2>${ocTranslate("复核沟通")}</h2></div>${this.renderReviewReturnAction(this.getDetailBatch?.() || {})}</div>
        ${current ? this.renderReviewRound(current, true) : `<div class="ocw-detail-empty"><strong>${ocTranslate("当前没有待处理的整改问题")}</strong><span>${ocTranslate("财务提交后，问题、附件、回复和操作人将保留在这里。")}</span></div>`}
        ${history.length ? `<section class="ocw-review-history"><h3>${ocTranslate("历史整改轮次（{0}）", [history.length])}</h3>${history.map((round) => this.renderReviewRound(round, false)).join("")}</section>` : ""}
      </div>`);
      this.restoreReviewTargetFocus();
    } catch (error) {
      this.renderDetailTabError("复核沟通", error);
    }
  }

  reviewCurrentIssue(issueName) {
    const current = this.ensureReviewCommunicationState().data?.current_round;
    return (current?.issues || []).find((row) => String(row.name) === String(issueName));
  }

  async addressReviewIssue(issueName) {
    const state = this.ensureReviewCommunicationState();
    const issue = this.reviewCurrentIssue(issueName);
    const reply = String(state.replyDrafts[issueName] ?? issue?.reply ?? "").trim();
    if (!reply) return frappe.show_alert({ message: ocTranslate("请先填写处理回复。"), indicator: "orange" });
    try {
      await this.call("overseas_costing.api.review.address_review_issue", { batch_name: state.batchName, issue_name: issueName, reply, expected_modified: issue?.modified || "" });
      delete state.replyDrafts[issueName];
      state.data = null;
      await this.renderReviewCommunicationTab();
    } catch (error) { this.showError(error); }
  }

  async resubmitReviewRound() {
    const state = this.ensureReviewCommunicationState();
    const round = state.data?.current_round;
    if (!round) return;
    try {
      await this.call("overseas_costing.api.review.resubmit_review_round", { batch_name: state.batchName, round_name: round.name, expected_modified: round.modified || "" });
      state.data = null;
      await this.refreshDetailSummary({ refreshCurrentTab: false });
      await this.switchDetailTab("review", { updateUrl: false });
    } catch (error) { this.showError(error); }
  }

  async resolveReviewIssue(issueName) {
    const state = this.ensureReviewCommunicationState();
    const issue = this.reviewCurrentIssue(issueName);
    try {
      await this.call("overseas_costing.api.review.resolve_review_issue", { batch_name: state.batchName, issue_name: issueName, expected_modified: issue?.modified || "" });
      state.data = null;
      await this.refreshDetailSummary({ refreshCurrentTab: false });
      await this.switchDetailTab("review", { updateUrl: false });
    } catch (error) { this.showError(error); }
  }

  async goToReviewTarget(issueName) {
    const state = this.ensureReviewCommunicationState();
    const issue = this.reviewCurrentIssue(issueName) || (state.data?.history || []).flatMap((round) => round.issues || []).find((row) => row.name === issueName);
    if (!issue?.target_tab) return;
    state.returnTarget = { issueName, orderNo: issue.order_no, anchor: issue };
    await this.switchDetailTab(issue.target_tab);
    window.requestAnimationFrame(() => this.focusReviewTarget(issue));
  }

  reviewTargetElements(issue = {}) {
    const matches = [];
    this.$root.find("[data-item-name], [data-fee-key], [data-mf-grid-field], [data-fieldname]").each((_, element) => {
      const $element = $(element);
      const itemMatch = !issue.target_item || String($element.attr("data-item-name") || "") === String(issue.target_item);
      const rowMatch = !issue.target_row || ["data-item-name", "data-fee-key", "data-evidence-name"].some((name) => String($element.attr(name) || "") === String(issue.target_row));
      const fieldMatch = !issue.target_field || ["data-fieldname", "data-mf-grid-field", "data-fee-key"].some((name) => String($element.attr(name) || "") === String(issue.target_field));
      if (itemMatch && rowMatch && fieldMatch) matches.push(element);
    });
    return $(matches);
  }

  focusReviewTarget(issue = {}) {
    const $matches = this.reviewTargetElements(issue);
    this.renderReviewReturnBanner(issue);
    const element = $matches.get(0);
    if (!element) {
      frappe.show_alert({ message: ocTranslate("原关联位置已变化，已打开原页签，请手工核对。"), indicator: "orange" });
      return;
    }
    const $target = $(element).closest("td, tr, .ocw-mf-fee-row").first().length ? $(element).closest("td, tr, .ocw-mf-fee-row").first() : $(element);
    element.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
    $target.addClass("ocw-review-target-highlight");
    window.setTimeout(() => $target.removeClass("ocw-review-target-highlight"), 2600);
    const $input = $(element).is("input, select, textarea") ? $(element) : $(element).find("input:not(:disabled), select:not(:disabled), textarea:not(:disabled)").first();
    if ($input.length) $input.trigger("focus");
  }

  renderReviewReturnBanner(issue = {}) {
    this.$root.find("[data-review-return-banner]").remove();
    this.$root.find("[data-area='detail-content']").prepend(`<div class="ocw-review-return-banner" data-review-return-banner="1"><span>${ocTranslate("正在查看整改问题 {0}的关联位置", [this.escape(issue.order_no || "")])}</span><button type="button" data-action="return-review-issue" data-issue-name="${this.escape(issue.name || "")}">${ocTranslate("返回整改问题 {0}", [this.escape(issue.order_no || "")])}</button></div>`);
  }

  restoreReviewTargetFocus() {
    const state = this.ensureReviewCommunicationState();
    const issueName = state.returnTarget?.issueName;
    if (!issueName) return;
    window.requestAnimationFrame(() => {
      const element = this.$root.find(`[data-review-issue="${issueName}"]`).get(0);
      if (element) element.scrollIntoView({ behavior: "smooth", block: "center" });
      state.returnTarget = null;
    });
  }

  async returnToReviewIssue() {
    await this.switchDetailTab("review");
  }

  bindReviewCommunicationEvents() {
    this.$root.on("click", "[data-action='open-review-remediation']", () => this.openReviewRemediationDrawer());
    this.$root.on("click", "[data-action='review-feedback-item']", (event) => this.openReviewRemediationDrawer(this.reviewAnchorFromElement($(event.currentTarget))));
    this.$root.on("click", "[data-action='add-review-draft']", () => { this.addReviewRemediationDraft(); this.renderReviewRemediationDrawer(); });
    this.$root.on("input", "[data-review-draft-description]", (event) => {
      const index = Number($(event.currentTarget).attr("data-review-draft-description"));
      const draft = this.ensureReviewCommunicationState().drafts[index];
      if (draft) draft.description = String($(event.currentTarget).val() || "");
      const drafts = this.ensureReviewCommunicationState().drafts;
      const completeCount = drafts.filter((row) => String(row.description || "").trim()).length;
      this.$root.find("[data-action='submit-review-remediation']")
        .prop("disabled", completeCount !== drafts.length)
        .text(ocTranslate("退回整改 ({0})", [completeCount]));
    });
    this.$root.on("click", "[data-action='delete-review-draft']", (event) => {
      this.ensureReviewCommunicationState().drafts.splice(Number($(event.currentTarget).attr("data-index")), 1);
      if (!this.ensureReviewCommunicationState().drafts.length) this.addReviewRemediationDraft();
      this.renderReviewRemediationDrawer();
    });
    this.$root.on("change", "[data-review-draft-attachment]", (event) => this.uploadReviewDraftAttachment(Number($(event.currentTarget).attr("data-review-draft-attachment")), event.currentTarget.files?.[0]));
    this.$root.on("click", "[data-action='remove-review-attachment']", (event) => {
      const state = this.ensureReviewCommunicationState();
      state.drafts[Number($(event.currentTarget).attr("data-index"))]?.attachments.splice(Number($(event.currentTarget).attr("data-file-index")), 1);
      this.renderReviewRemediationDrawer();
    });
    this.$root.on("click", "[data-action='close-review-remediation']", () => this.closeReviewRemediationDrawer());
    this.$root.on("click", "[data-action='submit-review-remediation']", () => this.submitReviewRemediation());
    this.$root.on("input", "[data-review-reply]", (event) => { this.ensureReviewCommunicationState().replyDrafts[$(event.currentTarget).attr("data-review-reply")] = String($(event.currentTarget).val() || ""); });
    this.$root.on("click", "[data-action='address-review-issue']", (event) => this.addressReviewIssue($(event.currentTarget).attr("data-issue-name")));
    this.$root.on("click", "[data-action='resubmit-review-round']", () => this.resubmitReviewRound());
    this.$root.on("click", "[data-action='resolve-review-issue']", (event) => this.resolveReviewIssue($(event.currentTarget).attr("data-issue-name")));
    this.$root.on("click", "[data-action='review-go-target']", (event) => this.goToReviewTarget($(event.currentTarget).attr("data-issue-name")));
    this.$root.on("click", "[data-action='return-review-issue']", () => this.returnToReviewIssue());
  }
