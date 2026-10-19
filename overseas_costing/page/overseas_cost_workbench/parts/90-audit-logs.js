  splitHeaderLabel(label) {
    const text = String(label || "");
    const match = text.match(/[A-Za-z][A-Za-z .()/%-]*/);
    if (!match || match.index === 0) return { primary: text, secondary: "" };
    return {
      primary: text.slice(0, match.index).trim(),
      secondary: text.slice(match.index).trim(),
    };
  }

  filterTermsForColumn(fieldname) {
    const value = this.filters[fieldname];
    return value ? [value] : [];
  }

  highlightText(value, terms) {
    let html = this.escape(value);
    terms
      .filter(Boolean)
      .map((term) => this.escapeRegExp(String(term)))
      .forEach((term) => {
        if (!term) return;
        html = html.replace(new RegExp(`(${term})`, "gi"), `<mark class="ocw-query-hit">$1</mark>`);
      });
    return html;
  }

  escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  updateSearchResult() {
    const $result = this.$root.find("[data-area='search-result']");
    this.renderFilterChips();
    $result.removeClass("imported calculated");
    if (!this.hasActiveFilters()) {
      const role = this.workRoleInfo();
      $result.removeClass("active empty").text(ocTranslate("{0}视图：{1}；查询后会锁定当前结果，回到全部批次请点击“重置”", [role.label, role.description]));
      return;
    }
    const modeLabel = this.filters.transport_mode ? `${this.transportLabel(this.filters.transport_mode)} · ` : "";
    if (this.visibleBatches.length) {
      $result
        .removeClass("empty")
        .addClass("active")
        .text(ocTranslate("{0}筛出 {1} 个报关/运单块 · 共 {2} 行 SKU（已自动展开）；当前结果已锁定，回到全部批次请点击“重置”", [modeLabel, this.visibleBatches.length, this.countVisibleItems()]));
    } else {
      $result.removeClass("active").addClass("empty").text(ocTranslate("{0}未找到匹配的报关/运单块；回到全部批次请点击“重置”", [modeLabel]));
    }
  }

  getExportCurrentBatches() {
    const pinnedBatch = this.exportPinnedBatchName ? this.findBatch(this.exportPinnedBatchName) : null;
    const pinnedInVisible = pinnedBatch && this.visibleBatches.some((batch) => batch.name === pinnedBatch.name);
    const mode = this.normalizeTransportMode(this.filters.transport_mode);
    const modeLabel = mode ? this.transportLabel(mode) : "全部";
    if (pinnedInVisible) {
      return {
        batches: [pinnedBatch],
        label: this.batchReferenceLabel(pinnedBatch),
      };
    }
    const batches = this.visibleBatches.length ? this.visibleBatches : this.getSelectableBatches();
    const label = batches.length === 1 ? this.batchReferenceLabel(batches[0]) : this.hasActiveFilters() ? ocTranslate("{0}筛选结果", [modeLabel]) : modeLabel;
    return { batches, label };
  }

  updateRecalculateAction() {
    if (!this.$root) return;
    const $buttons = this.$root.find("[data-action='recalculate']");
    if (!$buttons.length) return;

    $buttons.each((_, node) => {
      const $button = $(node);
      const batchName = $button.attr("data-batch-name");
      const batch = batchName ? this.findBatch(batchName) : this.getVisibleActiveBatch();

      $button.removeClass("needs-recalculate is-calculated").text(ocTranslate("重新试算")).attr("title", "");
      if (!batch) {
        $button.prop("disabled", true).attr("title", "暂无可试算批次");
        return;
      }

      const sourceStatus = batch.source_status || {};
      if (sourceStatus.invalid_business) {
        $button
          .prop("disabled", true)
          .attr("title", sourceStatus.invalid_business_reason || "当前批次包含已拒绝、撤销或终止的审批，不能重新计算");
        return;
      }

      const statusInfo = this.batchStatusInfo(batch.status);
      $button.prop("disabled", false).attr("title", "对当前批次重新计算分摊和综合成本");
      if (statusInfo.needsRecalculate) {
        $button.addClass("needs-recalculate").attr("title", statusInfo.suggestion);
        return;
      }

      if (String(batch.status || "").toLowerCase().includes("calculated")) {
        $button.addClass("is-calculated").attr("title", "当前批次已试算，可按需重新计算");
      }
    });
  }

  updateHierarchySummary() {
    if (this.erpQueueMode) {
      const stats = this.erpQueueStats();
      const statusText = this.erpQueueStatus === "all" ? "全部状态" : this.erpQueueStatusLabel(this.erpQueueStatus);
      this.$root
        .find("[data-area='hierarchy-summary']")
        .text(ocTranslate("ERP 队列 · {0} {1} 个批次 · 待推送 {2} 个", [statusText, this.getErpQueueBatches().length, stats.pending || 0]));
      return;
    }
    const displayBatches = this.getDisplayedBatches();
    const batchCount = displayBatches.length;
    const role = this.workRoleInfo();
    const modeLabel = this.filters.transport_mode ? `${this.transportLabel(this.filters.transport_mode)} · ` : "";
    const label = this.hasActiveFilters()
      ? ocTranslate("{0} · {1}筛出 {2} 个报关/运单块 · 点击 + 展开 SKU 明细", [role.label, modeLabel, batchCount])
      : ocTranslate("{0} · {1} 个报关/运单块 · 点击 + 展开 SKU 明细", [role.label, batchCount]);
    this.$root.find("[data-area='hierarchy-summary']").text(label);
  }

  getDisplayedBatches() {
    if (this.focusedBatchName) {
      const focusedBatch = this.findBatch(this.focusedBatchName);
      return focusedBatch ? [focusedBatch] : [];
    }
    return this.visibleBatches;
  }

  renderBatchFocusControls() {
    const inFocusedView = Boolean(this.focusedBatchName);
    this.$root.find("[data-action='clear-batch-focus']").prop("hidden", !inFocusedView);
    this.$root.find("[data-action='expand-current'], [data-action='collapse-current']").prop("hidden", inFocusedView || this.erpQueueMode);
  }

  erpQueueStatusLabel(status = "all") {
    const labels = {
      all: ocTranslate("全部"),
      not_started: ocTranslate("未开始"),
      pending: ocTranslate("待推送"),
      success: ocTranslate("成功"),
      failed: ocTranslate("失败"),
    };
    return labels[status] || status || ocTranslate("全部");
  }

  renderAuditList() {
    if (!this.auditEvents.length) {
      this.$root.find("[data-area='audit-list']").html(`
        <li class="ocw-audit-empty">
          <span class="ocw-audit-text">${ocTranslate("当前批次暂无修改记录")}</span>
        </li>
      `);
      return;
    }
    const displayEvents = this.buildAuditSummaryEvents(this.auditEvents);
    const html = displayEvents.map((event) => this.renderAuditEvent(event)).join("");
    this.$root.find("[data-area='audit-list']").html(html);
  }

  buildAuditSummaryEvents(events = []) {
    const orderedGroups = [];
    const groupMap = new Map();
    events.forEach((event) => {
      const key = this.auditSummaryKey(event);
      if (!key) {
        orderedGroups.push(event);
        return;
      }
      let group = groupMap.get(key);
      if (!group) {
        group = {
          summary: true,
          time: event.time || "",
          actor: event.actor || "",
          type: event.type || "",
          sourceLabel: this.auditSummarySourceLabel(event),
          added: 0,
          deleted: 0,
          modified: 0,
          uploaded: 0,
          actions: new Set(),
        };
        groupMap.set(key, group);
        orderedGroups.push(group);
      }
      this.collectAuditSummaryCount(group, event);
    });
    return orderedGroups.map((group) => (group.summary ? this.finalizeAuditSummaryEvent(group) : group));
  }

  auditSummaryKey(event) {
    if (!event || event.actionType === "RECALCULATE") return "";
    if (event.change) return "";
    if (!event.change && !["IMPORT", "EDIT", "BATCH_EDIT", "WRITEBACK", "UPLOAD_ATTACHMENT"].includes(event.actionType || "")) {
      return "";
    }
    const source = this.auditSummarySourceLabel(event);
    const remarkKey = this.normalizeAuditRemark(event.remark);
    return [event.type || "", event.actor || "", source, event.actionType || "", remarkKey].join("|");
  }

  auditSummarySourceLabel(event) {
    const remark = event.remark || "";
    const attachmentMatch = remark.match(/附件[:：]\s*([^；;，,\s]+)/);
    const fileMatch = remark.match(/(?:文件|资料)[:：]\s*([^；;，,\s]+)/);
    if (/采购支出/.test(remark)) return ocTranslate("采购支出 OA");
    if (/国际物流|钉钉|审批/.test(remark)) return ocTranslate("钉钉审批单");
    if (/补传资料|手动上传|人工上传/.test(remark)) return ocTranslate("补传资料");
    if (fileMatch && fileMatch[1]) return fileMatch[1];
    if (attachmentMatch && attachmentMatch[1]) return ocTranslate("附件 {0}", [attachmentMatch[1]]);
    return event.batchLabel || ocTranslate("当前表单");
  }

  normalizeAuditRemark(remark = "") {
    return String(remark || "")
      .replace(/附件[:：]\s*[^；;，,\s]+/g, "附件")
      .replace(/\s+/g, " ")
      .trim();
  }

  collectAuditSummaryCount(group, event) {
    if (event.change && event.fieldName === "item") {
      if (event.rawOldValue && !event.rawNewValue) {
        group.deleted += 1;
      } else if (!event.rawOldValue && event.rawNewValue) {
        group.added += 1;
      } else {
        group.modified += 1;
      }
      return;
    }
    if (event.change) {
      group.modified += 1;
      return;
    }
    if (event.actionType === "UPLOAD_ATTACHMENT") {
      group.uploaded += 1;
      return;
    }
    if (event.text || event.actionType) {
      group.actions.add(event.text || this.auditActionLabel(event.actionType));
    }
  }

  finalizeAuditSummaryEvent(group) {
    const pieces = [];
    if (group.added) pieces.push(ocTranslate("新增 {0} 条物料", [group.added]));
    if (group.deleted) pieces.push(ocTranslate("删除 {0} 条物料", [group.deleted]));
    if (group.modified) pieces.push(ocTranslate("修改 {0} 项字段", [group.modified]));
    if (group.uploaded) pieces.push(ocTranslate("上传 {0} 个附件", [group.uploaded]));
    if (!pieces.length && group.actions.size) pieces.push(Array.from(group.actions).join("，"));
    return {
      summary: true,
      time: group.time,
      actor: group.actor,
      type: group.type,
      sourceLabel: group.sourceLabel,
      text: pieces.length ? `${group.sourceLabel}：${pieces.join("，")}` : ocTranslate("{0}：已处理", [group.sourceLabel]),
    };
  }

  mapAuditRow(row, batch) {
    const fieldName = row.field_name || "";
    const oldValue = row.old_value || "";
    const newValue = row.new_value || "";
    const hasChangeValue = oldValue !== "" || newValue !== "";
    const action = this.auditActionLabel(row.action_type, fieldName, oldValue, newValue);
    const field = this.auditFieldLabel(fieldName);
    const targetParts = [];
    if (row.row_no) targetParts.push(ocTranslate("第 {0} 行", [row.row_no]));
    if (batch && (batch.batch_no || batch.waybill_no)) targetParts.push(batch.batch_no || batch.waybill_no);
    const remark = row.action_remark || "";
    return {
      time: row.creation || "",
      actor: row.operator_name || (row.action_type === "EDIT" || row.action_type === "BATCH_EDIT" ? "人工" : "系统"),
      type: row.action_type === "EDIT" || row.action_type === "BATCH_EDIT" ? "manual" : "system",
      actionType: row.action_type || "",
      fieldName,
      rawOldValue: oldValue,
      rawNewValue: newValue,
      remark,
      batchLabel:
        (batch && (batch.source_title || batch.source_approval_no || batch.batch_no || batch.waybill_no || batch.customs_no || batch.name)) ||
        "",
      text: hasChangeValue ? "" : remark || action,
      change: hasChangeValue
        ? {
            action,
            field,
            target: targetParts.join(" · "),
            oldValue: this.formatAuditChangeValue(fieldName, oldValue),
            newValue: this.formatAuditChangeValue(fieldName, newValue),
          }
        : null,
    };
  }

  auditActionLabel(actionType, fieldName, oldValue, newValue) {
    if (fieldName === "item") {
      if (oldValue && !newValue) return ocTranslate("删除");
      if (!oldValue && newValue) return ocTranslate("新增");
    }
    if (fieldName === "erp_payload") return ocTranslate("生成待推送报文");
    if (fieldName === "confirm_status") return ocTranslate("校验通过");
    const labels = {
      IMPORT: ocTranslate("导入"),
      EDIT: ocTranslate("修改"),
      BATCH_EDIT: ocTranslate("修改"),
      RECALCULATE: ocTranslate("重算"),
      CREATE_VERSION: ocTranslate("创建版本"),
      SWITCH_VERSION: ocTranslate("切换版本"),
      UPLOAD_ATTACHMENT: ocTranslate("上传附件"),
      WRITEBACK: ocTranslate("回写"),
      REVIEW_RETURN: ocTranslate("退回整改"),
      REVIEW_REPLY: ocTranslate("整改回复"),
      REVIEW_RESUBMIT: ocTranslate("提交财务复核"),
      REVIEW_RESOLVE: ocTranslate("确认整改"),
    };
    return labels[actionType] || actionType || ocTranslate("操作");
  }

  auditFieldLabel(fieldName) {
    if (!fieldName) return "";
    if (fieldName === "item") return ocTranslate("物料");
    if (fieldName === "allocation_rule") return ocTranslate("费用分摊规则");
    if (fieldName === "confirm_status") return ocTranslate("校验状态");
    if (fieldName === "erp_payload") return ocTranslate("ERP 报文");
    const column = (this.batchColumns || []).find((item) => item.fieldname === fieldName);
    return column ? column.label : fieldName;
  }

  formatAuditChangeValue(fieldName, value) {
    if (value === null || value === undefined || value === "") return "";
    const parsed = this.tryParseJson(value);
    if (fieldName === "allocation_rule") return this.formatAuditAllocationRule(parsed, value);
    if (fieldName === "confirm_status") return this.formatAuditConfirmStatus(parsed, value);
    if (fieldName === "erp_payload") return this.formatAuditErpPayload(parsed, value);
    if (fieldName !== "item") return this.formatAuditJsonValue(parsed, value);
    if (!parsed || typeof parsed !== "object") return value;
    const code = parsed.material_code || parsed.name || "";
    const name = parsed.product_name || "";
    const quantity = parsed.quantity !== undefined && parsed.quantity !== "" ? ocTranslate("，数量 {0}", [parsed.quantity]) : "";
    const goodsValue = parsed.goods_value !== undefined && parsed.goods_value !== "" ? ocTranslate("，货值 {0}", [this.formatMoney(parsed.goods_value)]) : "";
    const label = [code, name].filter(Boolean).join(" / ");
    return `${label || "未命名物料"}${quantity}${goodsValue}`;
  }

  formatAuditAllocationRule(parsed, fallback) {
    if (!parsed || typeof parsed !== "object") return fallback;
    const category = parsed.expense_category || parsed.rule_code || "费用";
    const amount = parsed.amount !== undefined && parsed.amount !== "" ? this.formatMoney(parsed.amount) : "未填金额";
    const currency = parsed.currency || "";
    const basis = this.allocationBasisLabel(parsed.allocation_basis || parsed.basis);
    const source = parsed.source || "";
    const remark = parsed.remark || "";
    const pieces = [`${category}：${amount}${currency ? ` ${currency}` : ""}`];
    if (basis) pieces.push(ocTranslate("按{0}分摊", [basis]));
    if (source) pieces.push(ocTranslate("来源 {0}", [source]));
    if (remark) pieces.push(remark);
    return pieces.join("，");
  }

  formatAuditConfirmStatus(parsed, fallback) {
    if (!parsed || typeof parsed !== "object") return fallback;
    const status = String(parsed.confirm_status || parsed.status || "").toLowerCase();
    const label = status.includes("confirmed") ? "已人工校验通过" : parsed.confirm_status || parsed.status || "已更新";
    return parsed.remark ? ocTranslate("{0}，备注：{1}", [label, parsed.remark]) : label;
  }

  formatAuditErpPayload(parsed, fallback) {
    if (!parsed || typeof parsed !== "object") return fallback;
    const target = parsed.target_system || "DeepLinkERP";
    const itemCount = parsed.item_count !== undefined ? ocTranslate("{0} 条物料", [parsed.item_count]) : "";
    const subsidiary = parsed.subsidiary_code ? ocTranslate("业务主体 {0}", [parsed.subsidiary_code]) : "";
    return [ocTranslate("目标 {0}", [target]), subsidiary, itemCount].filter(Boolean).join("，");
  }

  formatAuditJsonValue(parsed, fallback) {
    if (!parsed || typeof parsed !== "object") return fallback;
    const entries = Object.entries(parsed)
      .filter(([, value]) => value !== null && value !== undefined && value !== "")
      .slice(0, 4)
      .map(([key, value]) => `${this.auditFieldLabel(key) || key} ${typeof value === "object" ? "已记录" : this.formatDisplayNumber(value, key)}`);
    return entries.length ? entries.join("，") : fallback;
  }

  allocationBasisLabel(value) {
    const labels = {
      quantity: ocTranslate("数量"),
      qty: ocTranslate("数量"),
      gross_weight: ocTranslate("毛重"),
      gross_weight_kg: ocTranslate("毛重"),
      volume: ocTranslate("体积"),
      volume_m3: ocTranslate("体积"),
      goods_value: ocTranslate("货值"),
      amount: ocTranslate("金额"),
      equal: ocTranslate("平均"),
    };
    return labels[value] || value || "";
  }

  mapUsageRow(row, batch) {
    const actor = row.operator_full_name || row.operator_name || "未知用户";
    const actionType = row.action_type || "OTHER";
    const actionLabel = this.usageActionLabel(actionType);
    const status = row.status || "Success";
    const batchLabel =
      (batch && (batch.source_title || batch.source_approval_no || batch.batch_no || batch.waybill_no || batch.customs_no || batch.name)) ||
      row.batch ||
      "";
    return {
      time: row.creation || "",
      actor,
      type: status === "Failed" ? "system" : "manual",
      actionType,
      status,
      text: `${actionLabel}${batchLabel ? ` · ${batchLabel}` : ""}${row.action_remark ? `：${row.action_remark}` : ""}`,
    };
  }

  usageActionLabel(actionType) {
    const labels = {
      PAGE_VIEW: ocTranslate("进入工作台"),
      BATCH_VIEW: ocTranslate("查看批次"),
      DINGTALK_PULL: ocTranslate("钉钉拉取"),
      EXCEL_IMPORT: ocTranslate("Excel 导入"),
      FILE_PARSE: ocTranslate("凭证对比"),
      RECALCULATE: ocTranslate("重新试算"),
      CONFIRM_RESULT: ocTranslate("校验结果"),
      PREVIEW_ERP: ocTranslate("预览 ERP"),
      PUSH_ERP: ocTranslate("推送 ERP"),
      EXPORT: ocTranslate("导出"),
      DATA_CHECK: ocTranslate("数据检查"),
      ATTACHMENT_VIEW: ocTranslate("查看附件"),
      OTHER: ocTranslate("其他操作"),
    };
    return labels[actionType] || actionType || ocTranslate("操作");
  }

  renderUsageDrawer(batch = {}) {
    const summary = this.usageSummary || {};
    const users = summary.users || [];
    const actions = summary.actions || [];
    const writebackInfo = this.erpWritebackStatusInfo(batch);
    const userRows = users.length
      ? users
          .slice(0, 6)
          .map(
            (row) => `
              <div class="ocw-usage-chip">
                <strong>${this.escape(row.operator_full_name || row.operator_name || ocTranslate("未知用户"))}</strong>
                <span>${ocTranslate("{0} 次 · {1}", [this.escape(String(row.action_count || 0)), this.escape(row.last_seen || "--")])}</span>
              </div>
            `
          )
          .join("")
      : `<div class="ocw-usage-empty">${ocTranslate("近 30 天暂无使用汇总")}</div>`;
    const actionRows = actions.length
      ? actions
          .slice(0, 6)
          .map(
            (row) => `
              <div class="ocw-usage-chip">
                <strong>${this.escape(this.usageActionLabel(row.action_type))}</strong>
                <span>${ocTranslate("{0} 次", [this.escape(String(row.action_count || 0))])}</span>
              </div>
            `
          )
          .join("")
      : `<div class="ocw-usage-empty">${ocTranslate("暂无动作统计")}</div>`;
    const events = this.usageEvents.length
      ? this.usageEvents.map((event) => this.renderAuditEvent(event)).join("")
      : `<li class="ocw-audit-empty"><span class="ocw-audit-text">${ocTranslate("当前批次暂无使用记录")}</span></li>`;
    return `
      <div class="ocw-usage-panel">
        <div class="ocw-usage-status ${this.escape(writebackInfo.state)}">
          <div>
            <span>${ocTranslate("当前回写状态")}</span>
            <strong>${this.escape(ocTranslate(writebackInfo.label))}</strong>
          </div>
          <p>${this.escape(writebackInfo.note)}</p>
        </div>
        <div class="ocw-usage-summary">
          <div>
            <h4>${ocTranslate("近 30 天活跃用户")}</h4>
            <div class="ocw-usage-chip-grid">${userRows}</div>
          </div>
          <div>
            <h4>${ocTranslate("近 30 天操作分布")}</h4>
            <div class="ocw-usage-chip-grid">${actionRows}</div>
          </div>
        </div>
        <ul class="ocw-audit-list ocw-batch-drawer-audit-list">${events}</ul>
      </div>
    `;
  }

  tryParseJson(value) {
    if (typeof value !== "string") return value;
    try {
      return JSON.parse(value);
    } catch (_error) {
      return null;
    }
  }

  renderAuditEvent(event) {
    const actor = `<b class="ocw-actor ${this.escape(event.type)}">${this.escape(event.actor)}</b>`;
    if (event.summary) {
      return `
        <li class="ocw-audit-summary-row">
          <span class="ocw-audit-time">${this.escape(event.time)}</span>
          ${actor}
          <span class="ocw-audit-text">${this.escape(event.text)}</span>
        </li>
      `;
    }
    if (!event.change) {
      return `
        <li>
          <span class="ocw-audit-time">${this.escape(event.time)}</span>
          ${actor}
          <span class="ocw-audit-text">${this.escape(event.text)}</span>
        </li>
      `;
    }

    const change = event.change;
    const title = [
      change.action || "修改",
      change.field ? `「${change.field}」` : "",
      change.target ? `· ${change.target}` : "",
    ]
      .filter(Boolean)
      .join(" ");
    const oldValue = this.escape(this.formatAuditValue(change.oldValue, event.fieldName));
    const newValue = this.escape(this.formatAuditValue(change.newValue, event.fieldName));
    return `
      <li class="ocw-audit-change-row">
        <span class="ocw-audit-time">${this.escape(event.time)}</span>
        ${actor}
        <div class="ocw-audit-change">
          <strong>${this.escape(ocTranslate(title))}</strong>
          <div class="ocw-audit-values">
            <span>${ocTranslate("“{0}”改成“{1}”", [oldValue, newValue])}</span>
          </div>
        </div>
      </li>
    `;
  }

  renderDiffPanel() {
    const batch = this.getDataCheckBatch();
    this.renderDataCheckBatchSelector(batch);
    if (!batch) {
      this.$root.find("[data-area='diff-panel']").html(`
        <div class="ocw-diff-table">
          <div class="ocw-diff-head"><span>${ocTranslate("复核项")}</span><span>${ocTranslate("当前状态")}</span><span>${ocTranslate("说明")}</span></div>
          <div><span>${ocTranslate("当前批次")}</span><b class="ocw-check-warn">${ocTranslate("未选择")}</b><strong>${ocTranslate("先选择一条批次查看 AI 分摊状态")}</strong></div>
        </div>
      `);
      return;
    }

    const items = this.batchItems[batch.name] || [];
    const rows = this.buildAiReviewRows(batch, items);
    const sourceStatus = batch.source_status || {};
    const batchLabel = batch.waybill_no || batch.customs_no || batch.batch_no || batch.name;
    const sourceNo = sourceStatus.source_no || batch.source_approval_no || batch.source_instance_id || "";
    const targetLabel =
      sourceNo && sourceNo !== batchLabel ? ocTranslate("{0} / 来源 {1}", [batchLabel || batch.name, sourceNo]) : batchLabel || batch.name;
    const displayRows = [
      {
        label: ocTranslate("检查对象"),
        status: "当前批次",
        statusClass: "ocw-check-info",
        suggestion: targetLabel,
      },
      {
        label: ocTranslate("取数优先级"),
        status: "已确认",
        statusClass: "ocw-check-ok",
        suggestion: this.getSourcePrioritySummary(batch),
      },
      ...rows,
    ];
    this.$root.find("[data-area='diff-panel']").html(`
      <div class="ocw-diff-table">
        <div class="ocw-diff-head"><span>${ocTranslate("复核项")}</span><span>${ocTranslate("当前状态")}</span><span>${ocTranslate("说明")}</span></div>
        ${displayRows
          .map(
            (row) => `
              <div>
                <span>${this.escape(ocTranslate(row.label))}</span>
                <b class="${this.escape(row.statusClass)}">${this.escape(row.status)}</b>
                <strong>${this.escape(row.suggestion)}</strong>
              </div>
            `
          )
          .join("")}
      </div>
    `);
  }

  buildAiReviewRows(batch, items = []) {
    const hasLoadedItems = Object.prototype.hasOwnProperty.call(this.batchItems, batch.name);
    const loadedItems = hasLoadedItems ? items : [];
    const itemCount = hasLoadedItems ? loadedItems.length : Number(batch.item_count || 0);
    const summary = batch.summary_snapshot || {};
    const ai = batch.ai_allocation || summary.ai_allocation || {};
    const ruleSnapshot = Array.isArray(batch.allocation_rule_snapshot) ? batch.allocation_rule_snapshot : [];
    const overviewRows = this.buildAllocationOverviewRows(batch, loadedItems);
    const positiveRules = ruleSnapshot.filter((rule) => this.isPositive(rule.amount) || this.isPositive(rule.amount_rmb));
    const aiRules = positiveRules.filter((rule) => rule.is_ai_suggestion || String(rule.remark || "").includes("AI"));
    const hasAi = Boolean(ai.ok || aiRules.length);
    const sourceStatus = batch.source_status || {};
    const confirmedQuote = sourceStatus.confirmed_logistics_quote || {};
    const freightValue =
      this.sumRowsNumber(loadedItems, "china_to_mexico_freight_rmb") ||
      this.sumRowsNumber(loadedItems, "china_ocean_usd") ||
      Number(confirmedQuote.amount || 0);
    const taxValue =
      this.sumRowsNumber(loadedItems, "import_tax_total") ||
      this.sumRowsNumber(loadedItems, "mexico_customs_mxn") ||
      this.sumRowsNumber(loadedItems, "mexico_customs_rmb");
    const miscValue =
      this.sumRowsNumber(loadedItems, "china_misc_rmb") ||
      this.sumRowsNumber(loadedItems, "mexico_inland_mxn") ||
      this.sumRowsNumber(loadedItems, "mexico_misc_mxn") ||
      this.sumRowsNumber(loadedItems, "mexico_inland_misc_rmb");
    const missingPrice = hasLoadedItems ? this.countRows(loadedItems, (row) => !this.isPositive(this.purchasePriceValue(row))) : 0;
    const missingGoods = hasLoadedItems ? this.countRows(loadedItems, (row) => !this.isPositive(row.goods_value)) : 0;
    const missingWeight = hasLoadedItems ? this.countRows(loadedItems, (row) => !this.isPositive(row.gross_weight_kg)) : 0;
    const missingCost = hasLoadedItems ? this.countRows(loadedItems, (row) => !this.isPositive(row.total_unit_rmb)) : 0;
    const feeHints = [];
    if (this.isPositive(freightValue)) feeHints.push("物流费");
    if (this.isPositive(taxValue)) feeHints.push("税费/清关费");
    if (this.isPositive(miscValue)) feeHints.push("杂费");
    const ruleBasisText = this.summarizeAllocationRuleBasis(positiveRules);
    const resultText = overviewRows.length
      ? overviewRows.slice(0, 2).map((row) => `${row.amountTitle}：${row.basis}，${row.result}`).join("；")
      : "暂无分摊结果，请先确认费用池并点击重新试算";
    const reviewIssues = [];
    if (missingPrice) reviewIssues.push(ocTranslate("单价缺 {0} 行", [missingPrice]));
    if (missingGoods) reviewIssues.push(ocTranslate("货值缺 {0} 行", [missingGoods]));
    if (missingWeight) reviewIssues.push(ocTranslate("毛重缺 {0} 行", [missingWeight]));
    if (missingCost) reviewIssues.push(ocTranslate("综合单价缺 {0} 行", [missingCost]));
    const calculationReview = summary.calculation_review || batch.calculation_review || {};
    const reviewReasons = Array.isArray(calculationReview.reasons)
      ? calculationReview.reasons.filter((reason) => this.hasText(reason))
      : [];
    const reviewStatus = calculationReview.status || "";
    const reviewLabel =
      calculationReview.label ||
      (!itemCount ? "待补数据" : reviewIssues.length || (!positiveRules.length && !overviewRows.length) ? "需人工复核" : "可先采用");
    const reviewStatusClass =
      reviewStatus === "usable" || reviewLabel === "可先采用"
        ? "ocw-check-ok"
        : reviewStatus === "blocked" || reviewLabel === "待补数据"
        ? "ocw-check-warn"
        : "ocw-check-info";
    const reviewSuggestion =
      calculationReview.reason ||
      reviewReasons.slice(0, 3).join("；") ||
      (reviewLabel === "可先采用"
        ? "核心采购金额、费用池和分摊结果已生成，可作为当前试算结果"
        : reviewIssues.length
        ? ocTranslate("{0}；请在明细区点击可编辑字段补齐后重新试算", [reviewIssues.join("，")])
        : "先补齐物料、采购价格和费用池，再重新试算");

    return [
      {
        label: ocTranslate("核算结论"),
        status: reviewLabel,
        statusClass: reviewStatusClass,
        suggestion: reviewSuggestion,
      },
      {
        label: ocTranslate("分摊填入"),
        status: hasAi ? "AI已填" : positiveRules.length ? "系统已填" : "待费用",
        statusClass: hasAi ? "ocw-check-ok" : positiveRules.length ? "ocw-check-info" : "ocw-check-warn",
        suggestion: hasAi
          ? ocTranslate("AI 已选择基础分摊口径，系统已把分摊金额写入每行明细；模型 {0}", [ai.model || "deepseek-v4-flash"])
          : positiveRules.length
          ? ai.message || ocTranslate("当前使用系统基础规则，已按规则填入每行分摊金额")
          : ocTranslate("没有物流费、税费或杂费费用池时，AI 不会凭空生成费用分摊金额"),
      },
      {
        label: ocTranslate("费用池"),
        status: positiveRules.length ? ocTranslate("{0} 个", [positiveRules.length]) : feeHints.length ? ocTranslate("{0} 类", [feeHints.length]) : "0 个",
        statusClass: positiveRules.length || feeHints.length ? "ocw-check-ok" : "ocw-check-warn",
        suggestion: positiveRules.length
          ? this.summarizeAllocationRuleAmounts(positiveRules)
          : feeHints.length
          ? ocTranslate("已检测到{0}，点击重新试算后形成规则快照", [feeHints.join("、")])
          : ocTranslate("先确认物流费、清关费、税费或杂费；否则只能填入货值/重量比例，费用分摊金额为 0"),
      },
      {
        label: ocTranslate("分摊依据"),
        status: ruleBasisText ? "已确定" : "待确定",
        statusClass: ruleBasisText ? "ocw-check-ok" : "ocw-check-warn",
        suggestion: ruleBasisText || ocTranslate("默认先按毛重分摊；抛货或特殊费用可人工改为体积/计费重后重新试算"),
      },
      {
        label: ocTranslate("分摊结果"),
        status: overviewRows.length ? ocTranslate("{0} 项", [overviewRows.length]) : itemCount ? "待重算" : "待物料",
        statusClass: overviewRows.length ? "ocw-check-ok" : "ocw-check-warn",
        suggestion: resultText,
      },
      {
        label: ocTranslate("人工调整"),
        status: reviewIssues.length ? "需核对" : "可复核",
        statusClass: reviewIssues.length ? "ocw-check-warn" : "ocw-check-info",
        suggestion: reviewIssues.length
          ? ocTranslate("{0}；请在明细区点击可编辑字段补齐后重新试算", [reviewIssues.join("，")])
          : ocTranslate("基础分摊金额已填入明细；如需人工调整，请在明细区点击可编辑字段，税费最终以完税凭证对账为准"),
      },
    ];
  }

  summarizeAllocationRuleAmounts(rules = []) {
    return rules
      .slice(0, 3)
      .map((rule) => {
        const amount = this.numericOrNull(rule.amount);
        const currency = this.normalizeCurrencyCode(rule.currency || "RMB");
        return `${this.allocationFeeLabel(rule)} ${amount === null ? "--" : this.formatMoney(amount)} ${currency}`;
      })
      .join("；");
  }

  summarizeAllocationRuleBasis(rules = []) {
    if (!rules.length) return "";
    return rules
      .slice(0, 3)
      .map((rule) => `${this.allocationFeeLabel(rule)}：${this.allocationBasisLabel(rule.allocation_basis || rule.basis_field)}`)
      .join("；");
  }

  buildDataCheckRows(batch, items) {
    const hasLoadedItems = Object.prototype.hasOwnProperty.call(this.batchItems, batch.name);
    const loadedItems = hasLoadedItems ? items : [];
    const customsNo = batch.customs_no || this.firstLoadedValue(items, "customs_no");
    const waybillNo = batch.waybill_no || this.firstLoadedValue(items, "waybill_no");
    const sourceNo = batch.source_approval_no || batch.source_instance_id || batch.batch_no || this.firstLoadedValue(items, "source_doc_no");
    const itemCount = hasLoadedItems ? loadedItems.length : Number(batch.item_count || 0);
    const goodsValue = hasLoadedItems ? this.sumRowsNumber(loadedItems, "goods_value") : Number(batch.total_goods_value || 0);
    const grossWeight = hasLoadedItems ? this.sumRowsNumber(loadedItems, "gross_weight_kg") : Number(batch.total_gross_weight_kg || 0);
    const freightAlloc = hasLoadedItems ? this.sumRowsNumber(loadedItems, "freight_alloc_rmb") : 0;
    const totalCost = hasLoadedItems
      ? this.sumRowsNumber(loadedItems, "total_cost_rmb")
      : Number((batch.summary_snapshot?.calculation_schema === 2 ? batch.summary_snapshot.total_cost_rmb : batch.actual_total_cost_rmb || batch.estimated_total_cost_rmb) || 0);
    const taxTotal = hasLoadedItems
      ? this.sumRowsNumber(loadedItems, "import_tax_total") ||
        this.sumRowsNumber(loadedItems, "mexico_customs_mxn") ||
        this.sumRowsNumber(loadedItems, "igi_amount") + this.sumRowsNumber(loadedItems, "iva_amount")
      : 0;
    const batchStatus = this.batchStatusInfo(batch.status, batch, itemCount);
    const sourceStatus = batch.source_status || {};

    const hasOaSource =
      Boolean(sourceStatus.has_oa_logistics) ||
      String(batch.source_type || "").trim() === "oa_logistics" ||
      this.hasText(batch.source_approval_no) ||
      this.hasText(batch.source_instance_id) ||
      this.hasText(batch.source_dingtalk_url) ||
      this.countRows(loadedItems, (row) => this.hasText(row.dingtalk_instance_id) || this.hasText(row.dingtalk_official_url)) > 0;
    const missingCode = hasLoadedItems ? this.countRows(loadedItems, (row) => !this.hasText(row.material_code)) : 0;
    const missingName = hasLoadedItems ? this.countRows(loadedItems, (row) => !this.hasText(row.product_name)) : 0;
    const badQuantity = hasLoadedItems ? this.countRows(loadedItems, (row) => !this.isPositive(row.quantity)) : 0;
    const badPrice = hasLoadedItems ? this.countRows(loadedItems, (row) => !this.isPositive(this.purchasePriceValue(row))) : 0;
    const badCurrency = hasLoadedItems ? this.countRows(loadedItems, (row) => !this.hasText(this.purchasePriceCurrency(row))) : 0;
    const badGoods = hasLoadedItems ? this.countRows(loadedItems, (row) => !this.isPositive(row.goods_value)) : 0;
    const badActualQty = hasLoadedItems ? this.countRows(loadedItems, (row) => !this.isPositive(row.actual_shipped_qty)) : 0;
    const badWeight = hasLoadedItems ? this.countRows(loadedItems, (row) => !this.isPositive(row.gross_weight_kg)) : 0;
    const rowsWithTax = hasLoadedItems
      ? this.countRows(
          loadedItems,
          (row) =>
            this.isPositive(row.import_tax_total) ||
            this.isPositive(row.mexico_customs_mxn) ||
            this.isPositive(row.igi_amount) ||
            this.isPositive(row.iva_amount)
        )
      : 0;
    const badUnitCost = hasLoadedItems ? this.countRows(loadedItems, (row) => !this.isPositive(row.total_unit_rmb)) : 0;
    const needsRecalculate = batchStatus.needsRecalculate || badUnitCost > 0;
    const missingMaterialDetail = this.describeProblemRows(
      loadedItems,
      (row) => !this.hasText(row.material_code) || !this.hasText(row.product_name) || !this.isPositive(row.quantity)
    );
    const missingPurchaseDetail = this.describeProblemRows(
      loadedItems,
      (row) => !this.isPositive(this.purchasePriceValue(row)) || !this.hasText(this.purchasePriceCurrency(row)) || !this.isPositive(row.goods_value)
    );
    const missingPackingDetail = this.describeProblemRows(
      loadedItems,
      (row) => !this.isPositive(row.actual_shipped_qty) || !this.isPositive(row.gross_weight_kg)
    );
    const missingCostDetail = this.describeProblemRows(loadedItems, (row) => !this.isPositive(row.total_unit_rmb));
    const attachmentCount = Number(sourceStatus.oa_attachment_count || batch.source_attachment_count || 0);
    const packingListCount = Number(sourceStatus.packing_list_count || 0);
    const taxCertificateCount = Number(sourceStatus.tax_certificate_count || 0);
    const parsedTaxCertificateCount = Number(sourceStatus.parsed_tax_certificate_count || 0);
    const quoteCandidateCount = Number(sourceStatus.logistics_quote_candidate_count || 0);
    const confirmedQuote = sourceStatus.confirmed_logistics_quote || {};
    const logisticsTextSummary = sourceStatus.logistics_text_summary || {};
    const logisticsTextBrief = this.formatLogisticsTextSummary(logisticsTextSummary);
    const sourceStatusNo = sourceStatus.source_no || sourceNo;
    const transportMode = this.normalizeTransportMode(batch.transport_mode || this.firstLoadedValue(loadedItems, "transport_mode"));
    const transportCopy = this.dataCheckTransportCopy(transportMode);

    return [
      {
        label: ocTranslate("核算状态"),
        status: batchStatus.label,
        statusClass: batchStatus.statusClass,
        suggestion: batchStatus.suggestion,
      },
      {
        label: ocTranslate("国际物流 OA"),
        status: hasOaSource ? "已关联" : "未关联",
        statusClass: hasOaSource ? "ocw-check-ok" : "ocw-check-warn",
        suggestion: hasOaSource ? ocTranslate("{0}，可跳转原审批单", [sourceStatusNo || "--"]) : ocTranslate("先从钉钉国际物流流程拉取或补审批链接"),
      },
      {
        label: ocTranslate("物料明细"),
        status: !itemCount ? "待生成" : !hasLoadedItems ? "待展开" : missingCode || missingName || badQuantity ? "需核对" : ocTranslate("{0} 行", [itemCount]),
        statusClass: !itemCount || missingCode || missingName || badQuantity ? "ocw-check-warn" : "ocw-check-ok",
        suggestion: !itemCount
          ? ocTranslate("国际物流 OA 或附件解析后生成物料行")
          : !hasLoadedItems
          ? ocTranslate("展开批次后检查行级字段")
          : missingCode || missingName || badQuantity
          ? ocTranslate("编码缺 {0} 行，名称缺 {1} 行，数量异常 {2} 行；{3}", [missingCode, missingName, badQuantity, missingMaterialDetail])
          : `${customsNo || sourceNo || "--"} / ${waybillNo || "--"}`,
      },
      {
        label: ocTranslate("采购价格"),
        status: !itemCount ? "待物料" : !hasLoadedItems ? "待展开" : badPrice || badCurrency || badGoods ? "待同步" : this.formatMoney(goodsValue),
        statusClass: !itemCount || badPrice || badCurrency || badGoods ? "ocw-check-warn" : "ocw-check-ok",
        suggestion: !itemCount
          ? ocTranslate("先有物料行，再从采购支出 OA 补价格")
          : !hasLoadedItems
          ? ocTranslate("展开批次后检查采购单价和币种")
          : badPrice || badCurrency || badGoods
          ? ocTranslate("单价缺 {0} 行，币种缺 {1} 行，货值缺 {2} 行；{3}", [badPrice, badCurrency, badGoods, missingPurchaseDetail])
          : ocTranslate("已具备单价、币种和总货值"),
      },
      {
        label: ocTranslate("物流费用"),
        status: this.isPositive(confirmedQuote.amount)
          ? `${this.formatMoney(confirmedQuote.amount)} ${confirmedQuote.currency || "RMB"}`
          : quoteCandidateCount
          ? ocTranslate("{0} 份待确认", [quoteCandidateCount])
          : "待费用",
        statusClass: this.isPositive(confirmedQuote.amount) ? "ocw-check-ok" : "ocw-check-warn",
        suggestion: this.isPositive(confirmedQuote.amount)
          ? ocTranslate("{0}已参与整票物流费用分摊", [confirmedQuote.carrier || "已确认报价"])
          : quoteCandidateCount
          ? ocTranslate("在相关资料中确认最终物流报价后再参与试算")
          : transportCopy.missingFreightSuggestion,
      },
      {
        label: ocTranslate("审批正文识别"),
        status: logisticsTextBrief ? "已识别" : "待识别",
        statusClass: logisticsTextBrief ? "ocw-check-ok" : "ocw-check-warn",
        suggestion: logisticsTextBrief || ocTranslate("可从钉钉正文识别物流方式、报价、重量、预计发货日期和目的地"),
      },
      {
        label: ocTranslate("钉钉审批附件"),
        status: attachmentCount ? ocTranslate("{0} 个", [attachmentCount]) : "待拉取",
        statusClass: attachmentCount ? "ocw-check-ok" : "ocw-check-warn",
        suggestion: attachmentCount ? ocTranslate("资料里可查看{0}", [transportCopy.attachmentExamples]) : ocTranslate("从国际物流 OA 读取表单和评论附件"),
      },
      {
        label: transportCopy.packingLabel,
        status: !itemCount ? "待物料" : !hasLoadedItems ? "待展开" : badActualQty || badWeight ? "待补齐" : `${this.formatNumber(grossWeight)} KG`,
        statusClass: !itemCount || badActualQty || badWeight ? "ocw-check-warn" : "ocw-check-ok",
        suggestion: !itemCount
          ? ocTranslate("先生成物料行")
          : !hasLoadedItems
          ? ocTranslate("展开批次后检查实际数量和毛重")
          : packingListCount && (badActualQty || badWeight)
          ? ocTranslate("已登记装箱单 {0} 个；实际数量缺 {1} 行，毛重缺 {2} 行；{3}", [packingListCount, badActualQty, badWeight, missingPackingDetail])
          : badActualQty || badWeight
          ? ocTranslate("实际数量缺 {0} 行，毛重缺 {1} 行；{2}", [badActualQty, badWeight, missingPackingDetail])
          : transportCopy.packingReadySuggestion,
      },
      {
        label: ocTranslate("税费凭证"),
        status: taxCertificateCount ? ocTranslate("{0} 份", [taxCertificateCount]) : rowsWithTax ? ocTranslate("{0} 行", [rowsWithTax]) : "待凭证",
        statusClass: taxCertificateCount || rowsWithTax ? "ocw-check-ok" : "ocw-check-warn",
        suggestion: taxCertificateCount
          ? ocTranslate("已登记 {0} 份凭证{1}，用于最终多退少补对账", [taxCertificateCount, parsedTaxCertificateCount ? ocTranslate("，其中 {0} 份已有记录", [parsedTaxCertificateCount]) : ""])
          : rowsWithTax
          ? ocTranslate("系统已有税费 {0} MXN，后续仍需凭证对账", [this.formatMoney(taxTotal)])
          : ocTranslate("完税凭证或清关资料补齐后再做最终对账"),
      },
      {
        label: ocTranslate("试算结果"),
        status: !itemCount ? "待物料" : needsRecalculate || !this.isPositive(totalCost) ? "待重算" : this.formatMoney(totalCost),
        statusClass: !itemCount || needsRecalculate || !this.isPositive(totalCost) ? "ocw-check-warn" : "ocw-check-ok",
        suggestion: !itemCount
          ? ocTranslate("先形成批次物料明细")
          : batchStatus.needsRecalculate
          ? ocTranslate("明细已修改，请点击重新试算")
          : badUnitCost
          ? ocTranslate("综合单价缺 {0} 行：{1}；点击重新试算", [badUnitCost, missingCostDetail])
          : ocTranslate("已生成综合成本，国际运费分摊 {0} RMB", [this.formatMoney(freightAlloc)]),
      },
    ];
  }

  dataCheckTransportCopy(mode = "") {
    const normalized = this.normalizeTransportMode(mode);
    const copies = {
      SEA: {
        attachmentExamples: "装箱单、提单、发票等原始附件",
        packingLabel: "装箱单数据",
        packingReadySuggestion: "可用于海运重量分摊，体积按后续口径复核",
        missingFreightSuggestion: "等待物流 OA 填写海运费，或补充货代账单/报价资料",
      },
      AIR: {
        attachmentExamples: "空运运单、装箱单、发票等原始附件",
        packingLabel: "空运基础数据",
        packingReadySuggestion: "已具备实际数量和毛重；空运按现有口径以发货数量计算平均成本",
        missingFreightSuggestion: "等待物流 OA 填写空运费，或补充空运账单/报价资料",
      },
      EXPRESS: {
        attachmentExamples: "快递面单、货品明细、发票/账单等原始附件",
        packingLabel: "快递基础数据",
        packingReadySuggestion: "已具备实际数量和重量；后续按快递账单或双清费用核对",
        missingFreightSuggestion: "等待物流 OA 填写快递/双清费用，或补充快递账单",
      },
    };
    return (
      copies[normalized] || {
        attachmentExamples: "装箱单、运单、发票等原始附件",
        packingLabel: "发货基础数据",
        packingReadySuggestion: "已具备实际数量和重量，可继续核对费用分摊",
        missingFreightSuggestion: "等待物流 OA 填写明确费用，或补充物流账单/报价资料",
      }
    );
  }
