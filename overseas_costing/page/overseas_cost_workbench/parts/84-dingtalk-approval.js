  async loadDingtalkApprovalDetail() {
    const batch = this.getDetailBatch();
    const result = await this.call("overseas_costing.api.workbench.get_batch_dingtalk_approval_detail", {
      batch_name: batch.name,
    });
    if (!result) throw new Error(ocTranslate("钉钉审批读取失败"));
    this.syncPurchaseApprovalStatusFromDingtalk(result);
    this.detailState.dingtalkApproval = result;
    return result;
  }

  syncPurchaseApprovalStatusFromDingtalk(result = {}) {
    const approvals = Array.isArray(result.linked_purchase_approvals) ? result.linked_purchase_approvals : [];
    const excluded = Array.isArray(result.excluded_linked_purchase_approvals) ? result.excluded_linked_purchase_approvals : [];
    const batch = this.getDetailBatch();
    const sourceStatus = { ...(batch.source_status || {}) };
    if (sourceStatus.invalid_business && sourceStatus.invalid_business_scope === "source_approval") return;
    const linkState = String((result.source_state || {}).purchase_link_state || "").toLowerCase();
    if (!approvals.length && !excluded.length) {
      if (!linkState) return;
      sourceStatus.linked_purchase_count = 0;
      if (linkState === "none") {
        sourceStatus.purchase_approval_sync_state = "missing";
        sourceStatus.purchase_approval_sync_message = "物流审批已读取，其中确实没有关联采购审批。";
      } else {
        sourceStatus.purchase_approval_sync_state = linkState === "repairing" ? "pending" : "unknown";
        sourceStatus.purchase_approval_sync_message = linkState === "repairing"
          ? "已发现关联采购审批，相关正文正在补同步。"
          : "物流审批尚未同步，暂无法判断关联采购审批。";
      }
      batch.source_status = sourceStatus;
      if (this.detailState.header && this.detailState.header.name === batch.name) this.detailState.header.source_status = sourceStatus;
      return;
    }
    sourceStatus.linked_purchase_count = approvals.length + excluded.length;
    sourceStatus.excluded_purchase_count = excluded.length;
    sourceStatus.linked_purchase_approval_statuses = [...approvals, ...excluded]
      .map((approval) => approval.effective_status || approval.status || approval.result)
      .filter(Boolean);
    sourceStatus.purchase_approval_sync_state = excluded.length ? (approvals.length ? "partial" : "excluded") : "valid";
    sourceStatus.purchase_approval_sync_message = excluded.length
      ? ocTranslate("已排除 {0} 条拒绝、撤销或终止的关联采购审批；其数据不参与成本写入。", [excluded.length])
      : ocTranslate("已关联 {0} 条有效采购审批，状态已同步。", [approvals.length]);
    if (excluded.length && !approvals.length) {
      sourceStatus.invalid_business = true;
      sourceStatus.invalid_business_scope = "linked_purchase_approval";
      sourceStatus.invalid_business_reason = "关联采购审批均已拒绝、撤销或终止，当前批次不能确认成本或推送 ERP。";
    } else if (sourceStatus.invalid_business_scope === "linked_purchase_approval") {
      sourceStatus.invalid_business = false;
      sourceStatus.invalid_business_scope = "";
      sourceStatus.invalid_business_reason = "";
    }
    batch.source_status = sourceStatus;
    if (this.detailState.header && this.detailState.header.name === batch.name) {
      this.detailState.header.source_status = sourceStatus;
    }
  }

  async renderDingtalkApprovalTab() {
    const requestId = Number(this.detailState.dingtalkRequestId || 0) + 1;
    this.detailState.dingtalkRequestId = requestId;
    const requestedBatch = this.detailState.batchName;
    this.renderDetailTabLoading("正在读取钉钉审批");
    try {
      const result = await this.loadDingtalkApprovalDetail();
      if (this.detailState.dingtalkRequestId !== requestId || this.detailState.batchName !== requestedBatch || this.detailState.tab !== "dingtalk") return;
      if (!result.ok) {
        this.renderDingtalkSourceState(result);
        return;
      }
      const health = result.archive_health || {};
      this.$root.find("[data-area='detail-content']").html(`
        <div class="ocw-detail-section-head">
          <div><span>${ocTranslate("数据源·{0}", [this.escape(result.data_source || "postgres")])}</span><h2>${ocTranslate("钉钉审批")}</h2></div>
          <button class="ocw-outline-btn" type="button" data-action="detail-dingtalk">${ocTranslate("在钉钉中打开")}</button>
        </div>
        <div class="ocw-dingtalk-health">
          <span>${ocTranslate("最近同步：")}<strong>${this.escape(this.formatDateTimeMinute(result.source_updated_at) || result.source_updated_at || "--")}</strong></span>
          <span>${ocTranslate("延迟：")}<strong>${this.escape(result.source_lag_seconds == null ? "--" : ocTranslate("{0} 秒", [result.source_lag_seconds]))}</strong></span>
          <span>${ocTranslate("附件：")}<strong>${this.escape(ocTranslate("{0}/{1} 已归档", [health.archived || 0, health.total || 0]))}</strong></span>
          ${Number(health.manual_required || 0) ? `<span class="is-warning">${ocTranslate("需人工：")}<strong>${this.escape(String(health.manual_required))}</strong></span>` : ""}
          ${Number(health.preview_only || 0) ? `<span class="is-warning">${ocTranslate("仅预览图：")}<strong>${this.escape(String(health.preview_only))}</strong></span>` : ""}
        </div>
        ${this.renderDingtalkApprovalCard(result.main_approval || {}, "国际物流主审批", false)}
        <section class="ocw-dingtalk-linked">
          <div class="ocw-detail-section-head"><div><span>${ocTranslate("关联流程")}</span><h3>${ocTranslate("采购审批")}</h3></div><span>${ocTranslate("{0} 条", [this.escape(String((result.linked_purchase_approvals || []).length))])}</span></div>
          ${(result.linked_purchase_approvals || []).length
            ? result.linked_purchase_approvals.map((approval) => this.renderDingtalkApprovalCard(approval, "关联采购审批", true)).join("")
            : this.renderDingtalkPurchaseLinkEmpty(result)}
        </section>
        ${(result.excluded_linked_purchase_approvals || []).length ? `
          <section class="ocw-dingtalk-linked is-excluded">
            <div class="ocw-detail-section-head"><div><span>${ocTranslate("仅供审计查看")}</span><h3>${ocTranslate("已排除审批")}</h3></div><span>${ocTranslate("{0} 条", [this.escape(String(result.excluded_linked_purchase_approvals.length))])}</span></div>
            ${result.excluded_linked_purchase_approvals.map((approval) => this.renderDingtalkApprovalCard(approval, "已排除的关联采购审批", true)).join("")}
          </section>` : ""}
      `);
    } catch (error) {
      if (this.detailState.dingtalkRequestId !== requestId || this.detailState.batchName !== requestedBatch || this.detailState.tab !== "dingtalk") return;
      this.renderDetailTabError("钉钉审批", error);
    }
  }

  renderDingtalkPurchaseLinkEmpty(result = {}) {
    const state = String((result.source_state || {}).purchase_link_state || "unknown");
    if (state === "none") return `<div class="ocw-detail-empty"><strong>${ocTranslate("未关联采购审批")}</strong><span>${ocTranslate("物流审批正文中没有关联控件或有效关联项。")}</span></div>`;
    if (state === "repairing") return `<div class="ocw-detail-empty is-warning"><strong>${ocTranslate("已关联，采购审批待补同步")}</strong><span>${this.escape((result.missing_linked_instance_ids || []).join("、") || ocTranslate("后台将继续补齐关联流程。"))}</span></div>`;
    return `<div class="ocw-detail-empty is-warning"><strong>${ocTranslate("暂无法判断关联采购审批")}</strong><span>${ocTranslate("需先完成物流审批同步。")}</span></div>`;
  }

  renderDingtalkSourceState(result = {}) {
    const state = result.source_state || {};
    const code = String(state.code || "data_source_unavailable");
    const repairing = code === "repairing";
    const manual = code === "manual_required" || code === "source_mismatch" || code === "missing_instance_id";
    const title = repairing
      ? "钉钉审批正在补同步"
      : (manual ? "钉钉审批需要人工核对" : "物流审批尚未同步，暂无法判断关联采购审批");
    const failure = [state.failure_code, state.failure_reason].filter(Boolean).join("：");
    const canRepair = Boolean(state.repairable) && !repairing;
    this.$root.find("[data-area='detail-content']").html(`
      <div class="ocw-detail-section-head">
        <div><span>${ocTranslate("数据源·{0}", [this.escape(result.data_source || "postgres")])}</span><h2>${ocTranslate("钉钉审批")}</h2></div>
        <button class="ocw-outline-btn" type="button" data-action="detail-dingtalk">${ocTranslate("在钉钉中打开")}</button>
      </div>
      <div class="ocw-dingtalk-source-state ${manual ? "is-manual" : ""}">
        <strong>${this.escape(ocTranslate(title))}</strong>
        <span>${this.escape(failure || result.message || ocTranslate("阿里云同步库暂无该审批正文。"))}</span>
        <small>${ocTranslate("当前不会把“尚未同步”误判为“未关联采购审批”。")}</small>
        <div class="ocw-detail-section-actions">
          ${canRepair ? `<button class="ocw-primary-btn" type="button" data-action='repair-dingtalk-approval'>${manual ? ocTranslate("重试补同步") : ocTranslate("补同步")}</button>` : ""}
          <button class="ocw-outline-btn" type="button" data-action="retry-detail-tab">${ocTranslate("刷新状态")}</button>
        </div>
      </div>
    `);
  }

  async requestDingtalkApprovalRepair($button = null) {
    const batch = this.getDetailBatch();
    if ($button) $button.prop("disabled", true).text(ocTranslate("提交中…"));
    try {
      const result = await this.call("overseas_costing.api.workbench.request_batch_dingtalk_approval_repair", {
        batch_name: batch.name,
      }, true);
      if (!result || !result.ok) throw new Error((result && result.message) || "补同步任务提交失败");
      frappe.show_alert({ message: result.message || ocTranslate("已加入补同步队列"), indicator: "green" });
      this.detailState.dingtalkApproval = {
        ok: false,
        batch_name: batch.name,
        data_source: "postgres",
        message: result.message,
        source_state: { code: "repairing", repairable: true, purchase_link_state: "unknown" },
      };
      this.renderDingtalkSourceState(this.detailState.dingtalkApproval);
    } finally {
      if ($button) $button.prop("disabled", false).text(ocTranslate("补同步"));
    }
  }

  renderDingtalkApprovalCard(approval = {}, label = "审批", collapsible = false) {
    const content = `
      ${approval.excluded ? `<div class="ocw-dingtalk-excluded-note"><strong>${ocTranslate("已排除审批")}</strong><span>${this.escape(approval.exclusion_reason || ocTranslate("撤销、终止或拒绝的审批不参与成本计算。"))}</span></div>` : ""}
      <div class="ocw-dingtalk-summary-grid">
        ${this.renderDingtalkSummaryField("审批编号", approval.business_id)}
        ${this.renderDingtalkSummaryField("状态 / 结果", [approval.status, approval.result].filter(Boolean).join(" / "))}
        ${this.renderDingtalkActorField("发起人", approval.originator_user_name, approval.originator_user_id, approval.originator_name_source, approval.originator_name_unresolved)}
        ${this.renderDingtalkSummaryField("部门", approval.originator_dept_name)}
        ${this.renderDingtalkSummaryField("发起时间", this.formatDateTimeMinute(approval.create_time) || approval.create_time)}
        ${this.renderDingtalkSummaryField("完成时间", this.formatDateTimeMinute(approval.finish_time) || approval.finish_time)}
      </div>
      <section class="ocw-dingtalk-subsection"><h4>${ocTranslate("表单字段")}</h4>${this.renderDingtalkFormFields(approval.form_fields || [])}</section>
      <section class="ocw-dingtalk-subsection"><h4>${ocTranslate("操作与评论")}</h4>${this.renderDingtalkTimeline(approval.timeline || [], !approval.excluded)}</section>
      <section class="ocw-dingtalk-subsection"><h4>${ocTranslate("审批附件")}</h4>${this.renderDingtalkAttachments(approval.attachments || [], !approval.excluded)}</section>
    `;
    if (collapsible) {
      return `<details class="ocw-dingtalk-approval-card${approval.excluded ? " is-excluded" : ""}"><summary><strong>${this.escape(approval.title || label)}</strong><span>${this.escape(approval.business_id || approval.instance_id || "--")}</span></summary><div class="ocw-dingtalk-card-body">${content}</div></details>`;
    }
    return `<article class="ocw-dingtalk-approval-card is-main"><header><div><span>${this.escape(ocTranslate(label))}</span><h3>${this.escape(approval.title || approval.business_id || approval.instance_id || "--")}</h3></div><span>${this.escape(approval.status || "--")}</span></header><div class="ocw-dingtalk-card-body">${content}</div></article>`;
  }

  renderDingtalkSummaryField(label, value) {
    return `<div><span>${this.escape(ocTranslate(label))}</span><strong>${this.escape(value || "--")}</strong></div>`;
  }

  renderDingtalkActor(userName, userId, nameSource = "", unresolved = false) {
    const name = String(userName || "").trim();
    const id = String(userId || "").trim();
    const primary = name || id || "系统";
    let secondary = "";
    if (id && name && id !== name) secondary = `ID：${id}`;
    else if (unresolved || nameSource === "unresolved") secondary = "姓名未同步";
    return `<span class="ocw-dingtalk-actor"><strong>${this.escape(primary)}</strong>${secondary ? `<small class="ocw-dingtalk-actor-id">${this.escape(secondary)}</small>` : ""}</span>`;
  }

  renderDingtalkActorField(label, userName, userId, nameSource = "", unresolved = false) {
    return `<div><span>${this.escape(ocTranslate(label))}</span>${this.renderDingtalkActor(userName, userId, nameSource, unresolved)}</div>`;
  }

  renderDingtalkFormFields(fields = []) {
    if (!fields.length) return `<div class="ocw-purchase-empty-line">${ocTranslate("无可展示表单字段")}</div>`;
    const scalarFields = [];
    const tableFields = [];
    const emptyFields = [];
    fields.forEach((field) => {
      const table = field.table || {};
      const isTable = field.display_kind === "table"
        && Array.isArray(table.columns)
        && Array.isArray(table.rows);
      const isEmpty = Boolean(field.is_empty) || (!isTable && !String(field.value == null ? "" : field.value).trim());
      if (isEmpty) emptyFields.push(field);
      else if (isTable) tableFields.push(field);
      else scalarFields.push(field);
    });
    const scalars = scalarFields.length ? `<dl class="ocw-dingtalk-form-grid">${scalarFields.map((field) => {
      const value = String(field.value == null ? "" : field.value);
      const componentType = String(field.component_type || "").toLowerCase();
      const isLong = componentType.includes("textarea") || value.includes("\n") || value.length > 80;
      return `<div class="ocw-dingtalk-field${isLong ? " is-long" : ""}"><dt>${this.escape(field.label || ocTranslate("未命名字段"))}</dt><dd>${this.escape(value)}</dd></div>`;
    }).join("")}</dl>` : "";
    const tables = tableFields.map((field) => this.renderDingtalkFormTable(field)).join("");
    const empty = emptyFields.length ? `<details class="ocw-dingtalk-empty-fields"><summary>${ocTranslate("查看未填写字段（{0}）", [this.escape(String(emptyFields.length))])}</summary><dl>${emptyFields.map((field) => `<div><dt>${this.escape(field.label || ocTranslate("未命名字段"))}</dt><dd>${ocTranslate("未填写")}</dd></div>`).join("")}</dl></details>` : "";
    return `<div class="ocw-dingtalk-form-layout">${scalars}${tables}${empty}</div>`;
  }

  renderDingtalkFormTable(field = {}) {
    const table = field.table || {};
    const columns = Array.isArray(table.columns) ? table.columns : [];
    const rows = Array.isArray(table.rows) ? table.rows : [];
    const displayCell = (row, index) => {
      const value = Array.isArray(row) ? row[index] : "";
      return value == null || value === "" ? "--" : value;
    };
    const desktop = `<div class="ocw-dingtalk-table-scroll"><table class="ocw-dingtalk-goods-table"><thead><tr>${columns.map((column, index) => `<th scope="col"${index === 0 ? ` class="ocw-dingtalk-goods-code"` : ""}>${this.escape(column || "--")}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${columns.map((_, index) => `<td${index === 0 ? ` class="ocw-dingtalk-goods-code"` : ""}>${this.escape(displayCell(row, index))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    const mobile = `<div class="ocw-dingtalk-goods-cards">${rows.map((row, rowIndex) => `<article><strong>${ocTranslate("物料 {0}", [this.escape(String(rowIndex + 1))])}</strong><dl>${columns.map((column, index) => `<div><dt>${this.escape(column || "--")}</dt><dd>${this.escape(displayCell(row, index))}</dd></div>`).join("")}</dl></article>`).join("")}</div>`;
    return `<section class="ocw-dingtalk-table-field"><h5>${this.escape(field.label || ocTranslate("未命名表格"))}</h5>${desktop}${mobile}</section>`;
  }

  renderDingtalkTimeline(items = [], allowCostSource = true) {
    if (!items.length) return `<div class="ocw-purchase-empty-line">${ocTranslate("无操作或评论记录")}</div>`;
    const sorted = items.map((item, index) => ({ item, index })).sort((left, right) => {
      const leftTime = Date.parse(left.item.operation_time || "") || 0;
      const rightTime = Date.parse(right.item.operation_time || "") || 0;
      return rightTime - leftTime || left.index - right.index;
    }).map((entry) => entry.item);
    const primary = sorted.filter((item) => item.event_kind === "comment" || item.event_kind === "decision");
    const system = sorted.filter((item) => item.event_kind !== "comment" && item.event_kind !== "decision");
    const primaryHtml = primary.length
      ? `<ol class="ocw-dingtalk-timeline">${primary.map((item) => this.renderDingtalkTimelineItem(item, allowCostSource)).join("")}</ol>`
      : `<div class="ocw-purchase-empty-line">${ocTranslate("无评论或审批处理记录")}</div>`;
    const systemHtml = system.length
      ? `<details class="ocw-dingtalk-system-records"><summary>${ocTranslate("展开 {0} 条系统记录", [this.escape(String(system.length))])}</summary><ol class="ocw-dingtalk-timeline is-system">${system.map((item) => this.renderDingtalkTimelineItem(item, false)).join("")}</ol></details>`
      : "";
    return `<div class="ocw-dingtalk-timeline-layout">${primaryHtml}${systemHtml}</div>`;
  }

  renderDingtalkTimelineItem(item = {}, allowCostSource = true) {
    const actor = String(item.user_name || "").trim() || (item.user_name_unresolved ? "姓名未同步" : "系统");
    const segments = Array.isArray(item.remark_segments) && item.remark_segments.length
      ? item.remark_segments
      : (item.remark ? [{ kind: "text", text: item.remark }] : []);
    const remark = segments.length
      ? segments.map((segment) => segment.kind === "mention"
        ? `<span class="ocw-dingtalk-mention">${this.escape(segment.text || "")}</span>`
        : this.escape(segment.text || "")).join("")
      : `<span class="ocw-dingtalk-no-remark">${ocTranslate("无补充说明")}</span>`;
    const auditRows = [
      ["操作原始码", item.operation_type],
      ["原始结果", item.result],
      ["用户 ID", item.user_id],
    ];
    return `<li class="ocw-dingtalk-timeline-item">
      <div class="ocw-dingtalk-timeline-head"><span><strong>${this.escape(actor)}</strong><em>${this.escape(item.display_label || ocTranslate("其他流程记录"))}</em></span><time>${this.escape(this.formatDateTimeMinute(item.operation_time) || item.operation_time || "--")}</time></div>
      <p>${remark}</p>
      ${allowCostSource && item.packing_candidate && item.source_id ? `<button class="ocw-link-btn" type="button" data-action="use-dingtalk-packing-source" data-source-kind="comment" data-source-id="${this.escape(item.source_id)}">${ocTranslate("作为装箱信息预览")}</button>` : ""}
      <details class="ocw-dingtalk-timeline-audit"><summary>${ocTranslate("审计详情")}</summary><dl>${auditRows.map(([label, value]) => `<div><dt>${this.escape(ocTranslate(label))}</dt><dd>${this.escape(value || "--")}</dd></div>`).join("")}</dl></details>
    </li>`;
  }

  dingtalkArchiveStatus(item = {}) {
    const labels = {
      archived: ocTranslate("已归档"),
      pending: ocTranslate("待归档"),
      archiving: ocTranslate("归档中"),
      retry: ocTranslate("等待重试"),
      manual_required: ocTranslate("需人工处理"),
    };
    const status = item.archive_status || "pending";
    const quality = item.content_quality === "preview" ? "·仅预览图" : "";
    return `${labels[status] || status}${quality}`;
  }

  renderDingtalkAttachments(items = [], allowCostSource = true) {
    if (!items.length) return `<div class="ocw-purchase-empty-line">${ocTranslate("无附件")}</div>`;
    return `<div class="ocw-dingtalk-attachments">${items.map((item) => {
      const previewUrl = this.dingtalkAttachmentPreviewUrl(item);
      const isImage = /\.(?:avif|gif|jpe?g|png|webp)$/i.test(String(item.file_name || ""));
      const canPreview = Boolean(item.previewable || item.downloadable);
      const canDownload = Boolean(item.downloadable);
      const actions = [];
      if (canPreview) {
        actions.push(`<button class="ocw-link-btn" type="button" data-action="preview-dingtalk-attachment" data-attachment-name="${this.escape(item.attachment_name || "")}" data-process-instance-id="${this.escape(item.process_instance_id || "")}" data-file-id="${this.escape(item.file_id || "")}" data-file-url="${this.escape(previewUrl)}" data-file-name="${this.escape(item.file_name || "")}">${ocTranslate("预览")}</button>`);
      }
      if (canDownload) {
        actions.push(`<button class="ocw-link-btn" type="button" data-action="download-dingtalk-attachment" data-attachment-name="${this.escape(item.attachment_name || "")}" data-process-instance-id="${this.escape(item.process_instance_id || "")}" data-file-id="${this.escape(item.file_id || "")}">${ocTranslate("下载")}</button>`);
      }
      if (allowCostSource && item.packing_candidate && item.downloadable) {
        actions.push(`<button class="ocw-link-btn" type="button" data-action="use-dingtalk-packing-source" data-source-kind="attachment" data-source-id="${this.escape(item.attachment_name || "")}" data-process-instance-id="${this.escape(item.process_instance_id || "")}" data-file-id="${this.escape(item.file_id || "")}">${ocTranslate("作为装箱单使用")}</button>`);
      }
      const suffix = String(item.file_name || "").split(".").pop().slice(0, 4).toUpperCase() || "FILE";
      const preview = isImage && previewUrl
        ? `<img class="ocw-dingtalk-attachment-thumb" loading="lazy" src="${this.escape(previewUrl)}" alt="">`
        : `<span class="ocw-dingtalk-file-icon" aria-hidden="true">${this.escape(suffix)}</span>`;
      return `<article class="ocw-dingtalk-attachment-card">
        ${preview}
        <div class="ocw-dingtalk-attachment-copy"><strong>${this.escape(item.file_name || item.file_id || "--")}</strong><span>${this.escape(item.origin === "Comment" ? ocTranslate("评论附件") : ocTranslate("表单附件"))} · ${this.escape(this.dingtalkArchiveStatus(item))}</span>${item.origin === "Comment" && (item.comment_user_name || item.comment_user_id) ? this.renderDingtalkActor(item.comment_user_name, item.comment_user_id, item.comment_user_name_source, item.comment_user_name_unresolved) : ""}${item.comment_remark ? `<em>${this.escape(item.comment_remark)}</em>` : ""}${item.failure_reason ? `<em class="is-error">${this.escape(item.failure_reason)}</em>` : ""}</div>
        <div class="ocw-attachment-actions">${actions.join("") || `<span class="ocw-purchase-source-disabled">${ocTranslate("暂不可下载")}</span>`}</div>
      </article>`;
    }).join("")}</div>`;
  }

  dingtalkAttachmentPreviewUrl(item = {}) {
    const value = String(item.preview_url || item.file_url || "");
    if (!value || value !== value.trim() || /[\\\u0000-\u001f\u007f]/.test(value)) return "";
    let resolved;
    try {
      resolved = new URL(value, "https://ocw.local");
    } catch (_error) {
      return "";
    }
    const path = resolved.pathname;
    if (resolved.origin !== "https://ocw.local" || resolved.search || resolved.hash) return "";
    if (!(path.startsWith("/files/") || path.startsWith("/private/files/"))) return "";
    for (const segment of value.split("/")) {
      let decoded = segment;
      let settled = false;
      for (let pass = 0; pass < 4; pass += 1) {
        let next;
        try {
          next = decodeURIComponent(decoded);
        } catch (_error) {
          return "";
        }
        if (next === decoded) {
          settled = true;
          break;
        }
        decoded = next;
      }
      if (!settled) {
        let next;
        try {
          next = decodeURIComponent(decoded);
        } catch (_error) {
          return "";
        }
        if (next !== decoded) return "";
      }
      if (decoded === "." || decoded === ".." || /[\/\\\u0000-\u001f\u007f]/.test(decoded)) return "";
    }
    return value;
  }

  async ensureDingtalkLocalAttachment(attachmentName, processInstanceId, fileId) {
    if (attachmentName && !(processInstanceId && fileId)) return attachmentName;
    const batch = this.getDetailBatch();
    const result = await this.call("overseas_costing.api.import_api.prepare_dingtalk_archive_attachment", {
      batch_name: batch.name,
      process_instance_id: processInstanceId,
      file_id: fileId,
    }, true);
    if (!result || !result.ok || !result.attachment_name) throw new Error((result && result.message) || "无法准备钉钉归档附件");
    return result.attachment_name;
  }

  async downloadDingtalkAttachmentFromDetail(attachmentName, $button = null, processInstanceId = "", fileId = "") {
    const batch = this.getDetailBatch();
    attachmentName = await this.ensureDingtalkLocalAttachment(attachmentName, processInstanceId, fileId);
    await this.downloadOaFormAttachment(batch, null, attachmentName, $button, false);
    if (this.detailState.tab === "dingtalk") await this.renderDingtalkApprovalTab();
  }

  async previewDingtalkAttachmentFromDetail(attachmentName, fileUrl, fileName, $button = null, processInstanceId = "", fileId = "") {
    const batch = this.getDetailBatch();
    attachmentName = await this.ensureDingtalkLocalAttachment(attachmentName, processInstanceId, fileId);
    await this.openOaAttachmentFilePreview(batch, null, attachmentName, fileUrl, fileName, $button);
    if (!fileUrl && this.detailState.tab === "dingtalk") await this.renderDingtalkApprovalTab();
  }

  dingtalkPackingCandidates(detail = {}) {
    const approvals = [detail.main_approval, ...(detail.linked_purchase_approvals || [])]
      .filter((approval) => approval && !approval.excluded);
    const candidates = [];
    approvals.forEach((approval) => {
      (approval.attachments || []).forEach((item) => {
        if (!item.packing_candidate || !item.downloadable) return;
        candidates.push({ kind: "attachment", id: item.attachment_name || "", instanceId: item.process_instance_id || approval.instance_id, fileId: item.file_id, label: item.file_name, meta: `${item.origin === "Comment" ? "评论附件" : "表单附件"} · ${approval.business_id || approval.instance_id}` });
      });
      (approval.timeline || []).forEach((item) => {
        if (!item.packing_candidate || !item.source_id) return;
        candidates.push({ kind: "comment", id: item.source_id, label: item.remark, meta: ocTranslate("纯评论 · {0} · {1}", [item.user_name || item.user_id || "--", this.formatDateTimeMinute(item.operation_time) || item.operation_time || "--"]) });
      });
    });
    return candidates;
  }

  async openDingtalkPackingPreview(sourceKind, sourceId, processInstanceId = "", fileId = "") {
    const batch = this.getDetailBatch();
    if (sourceKind === "attachment") sourceId = await this.ensureDingtalkLocalAttachment(sourceId, processInstanceId, fileId);
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("钉钉装箱资料预览"),
      size: "large",
      fields: [{ fieldtype: "HTML", fieldname: "preview", options: `<div class="ocw-purchase-loading" data-area="dingtalk-packing-preview">${ocTranslate("正在解析，当前不会写入数据")}</div>` }],
      primary_action_label: ocTranslate("关闭"),
      primary_action: () => dialog.hide(),
    });
    dialog.show();
    const load = async () => {
      let result = await this.call("overseas_costing.api.import_api.preview_packing_source", {
        batch_name: batch.name,
        version_name: batch.current_version || null,
        source_kind: sourceKind,
        source_id: sourceId,
      }, true);
      if (result && result.download_required && result.attachment_name) {
        const saved = await this.call("overseas_costing.api.import_api.download_oa_form_attachment", { attachment_name: result.attachment_name }, true);
        if (!saved || !saved.ok) throw new Error((saved && saved.message) || "附件尚未归档");
        result = await this.call("overseas_costing.api.import_api.preview_packing_source", {
          batch_name: batch.name,
          version_name: batch.current_version || null,
          source_kind: sourceKind,
          source_id: sourceId,
        }, true);
      }
      this.renderDingtalkPackingPreview(dialog, result, batch);
    };
    try { await load(); } catch (error) {
      dialog.$wrapper.find("[data-area='dingtalk-packing-preview']").html(`<div class="ocw-detail-empty is-error"><strong>${ocTranslate("装箱资料预览失败")}</strong><span>${this.escape(this.normalizeErrorMessage(error))}</span></div>`);
    }
  }

  renderDingtalkPackingPreview(dialog, result, batch) {
    const $target = dialog.$wrapper.find("[data-area='dingtalk-packing-preview']");
    if (!result || !result.ok) {
      $target.html(`<div class="ocw-detail-empty is-error"><strong>${ocTranslate("无法生成装箱预览")}</strong><span>${this.escape((result && result.message) || ocTranslate("请检查来源内容。"))}</span></div>`);
      return;
    }
    const preview = result.writeback_preview || {};
    const matched = preview.matched_rows || [];
    const fillable = matched.filter((row) => row.has_fillable);
    const conflicts = matched.filter((row) => row.has_conflict);
    $target.html(`
      <div class="ocw-purchase-target"><span>${ocTranslate("来源")}</span><strong>${this.escape(result.source_kind === "comment" ? ocTranslate("钉钉评论") : ocTranslate("钉钉附件"))}</strong><em>${this.escape(result.message || ocTranslate("预览已生成，尚未写入。"))}</em></div>
      <div class="ocw-purchase-summary"><div><span>${ocTranslate("匹配行")}</span><strong>${this.escape(String(preview.matched_count || 0))}</strong></div><div><span>${ocTranslate("可写入")}</span><strong>${this.escape(String(preview.fillable_row_count || 0))}</strong></div><div><span>${ocTranslate("冲突")}</span><strong>${this.escape(String(preview.conflict_row_count || 0))}</strong></div><div><span>${ocTranslate("未匹配")}</span><strong>${this.escape(String(preview.unmatched_count || 0))}</strong></div></div>
      <div class="ocw-purchase-note">${ocTranslate("只自动填充空值或 0；已有值差异须选择处理方式，多物料歧义不会自动写入。")}</div>
      ${this.renderDingtalkPackingApplyActions(preview)}
      ${this.renderPurchasePreviewSection("可写入装箱字段", fillable, "fillable")}
      ${this.renderPackingConflictSection(conflicts)}
      ${this.renderPackingUnmatchedSection(preview.unmatched_rows || [], preview.ambiguous_rows || [])}
    `);
    dialog.$wrapper.off("click.ocwDingtalkPackingPreview")
      .on("click.ocwDingtalkPackingPreview", "[data-action='apply-dingtalk-packing']", (event) => {
        const createUnmatched = $(event.currentTarget).attr("data-create-unmatched") === "1";
        this.applyDingtalkPackingSource(dialog, result, batch, { create_unmatched_items: createUnmatched }).catch((error) => this.showError(error));
      })
      .on("click.ocwDingtalkPackingPreview", "[data-action='resolve-packing-conflict']", (event) => {
        const $button = $(event.currentTarget);
        this.applyDingtalkPackingSource(dialog, result, batch, { conflicts: [{ target_item_name: $button.attr("data-target-item-name"), action: $button.attr("data-resolution-action") }] }).catch((error) => this.showError(error));
      });
  }

  renderDingtalkPackingApplyActions(preview = {}) {
    const fillable = Number(preview.fillable_row_count || 0);
    const unmatched = Number(preview.unmatched_count || 0);
    if (!fillable && !unmatched) return "";
    return `<div class="ocw-purchase-apply"><div><strong>${ocTranslate("确认后才写入")}</strong><span>${ocTranslate("写入后将重新计算批次并记录钉钉来源。")}</span></div><div class="ocw-attachment-actions">${fillable ? `<button class="ocw-primary-btn ocw-mini-btn" type="button" data-action="apply-dingtalk-packing">${ocTranslate("写入可补字段")}</button>` : ""}${unmatched ? `<button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="apply-dingtalk-packing" data-create-unmatched="1">${ocTranslate("确认并新建未匹配物料")}</button>` : ""}</div></div>`;
  }

  async applyDingtalkPackingSource(dialog, previewResult, batch, resolutions = {}) {
    const confirmed = await new Promise((resolve) => frappe.confirm(
      ocTranslate("确认按预览结果写入当前批次并重新计算？"),
      () => resolve(true),
      () => resolve(false)
    ));
    if (!confirmed) return;
    const result = await this.call("overseas_costing.api.import_api.apply_packing_source", {
      batch_name: batch.name,
      version_name: batch.current_version || null,
      source_revision: previewResult.source_revision,
      resolutions_json: JSON.stringify(resolutions),
    }, true);
    if (!result || !result.ok) throw new Error((result && result.message) || "装箱资料写入失败");
    frappe.show_alert({ message: result.message || ocTranslate("装箱资料已写入"), indicator: "green" });
    dialog.hide();
    await this.refreshDetailSummary();
  }
