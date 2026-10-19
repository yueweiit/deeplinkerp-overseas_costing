
  openOaLogisticsPullDialog() {
    const defaults = this.getDefaultPullDateRange();
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("拉取钉钉国际物流审批"),
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "settlement_history",
          options: `<div class="ocw-pull-note"><strong>${ocTranslate("物流类采购支出结算")}</strong><span>${ocTranslate("匹配全部已归档历史采购支出，不受下方日期、运输方式和条数限制。")}</span><div class="ocw-settlement-toolbar"><button type="button" class="ocw-outline-btn" data-action="settlement-history">${ocTranslate("一键匹配历史采购支出")}</button><button type="button" class="ocw-outline-btn" data-action="settlement-history-status">${ocTranslate("查看匹配进度与结果")}</button></div></div>`,
        },
        {
          fieldtype: "HTML",
          fieldname: "pull_note",
          options: `
            <div class="ocw-pull-note">
              <strong>${ocTranslate("只补拉钉钉国际物流审批单")}</strong>
              <span>${ocTranslate("新单会创建批次，已有单据只更新追溯、附件、采购字段和明确费用，不会清空或删除已有批次。")}</span>
            </div>
          `,
        },
        { fieldtype: "Date", fieldname: "start", label: ocTranslate("开始日期"), default: defaults.start_date, reqd: 1 },
        { fieldtype: "Date", fieldname: "end", label: ocTranslate("结束日期"), default: defaults.end_date, reqd: 1 },
        {
          fieldtype: "Select",
          fieldname: "transport_modes",
          label: ocTranslate("运输方式"),
          options: "全部\n海运\n空运\n快递",
          default: "全部",
        },
        {
          fieldtype: "Int",
          fieldname: "limit",
          label: ocTranslate("最多读取审批单数"),
          default: 80,
          description: ocTranslate("默认读取近一个月内最多 80 条审批详情，避免一次拉取过多导致等待太久。"),
        },
        {
          fieldtype: "HTML",
          fieldname: "pull_result",
          options: `<div class="ocw-pull-result empty" data-area="oa-pull-result">${ocTranslate("尚未开始拉取")}</div>`,
        },
      ],
      primary_action_label: ocTranslate("开始拉取"),
      primary_action: async (values) => {
        await this.pullOaLogisticsApprovals(dialog, values || {});
      },
    });
    dialog.show();
    dialog.$wrapper.on("click.ocwSettlementHistory", "[data-action='settlement-history'],[data-action='settlement-history-status']", (event) => {
      const start = $(event.currentTarget).attr("data-action") === "settlement-history";
      this.openSettlementHistory(start).catch((error) => this.showError(error));
    });
    this.setOaPullPrimaryState(dialog, "ready");
  }

  async pullOaLogisticsApprovals(dialog, values) {
    if (dialog.$wrapper.data("ocw-pull-completed") && !values.force) {
      frappe.confirm(ocTranslate("刚刚已经完成一次拉取，确认要按当前日期范围重新拉取吗？"), () => {
        this.pullOaLogisticsApprovals(dialog, { ...values, force: true }).catch((error) => this.showError(error));
      });
      return;
    }
    const start = String(values.start || "").trim();
    const end = String(values.end || "").trim();
    if (!start || !end) {
      frappe.msgprint(ocTranslate("请先选择开始日期和结束日期。"));
      return;
    }
    dialog.$wrapper.data("ocw-pull-completed", false);
    this.setOaPullPrimaryState(dialog, "running");
    try {
      this.renderOaPullResult(dialog, null, "loading");
      const result = await this.call(
        "overseas_costing.api.import_api.pull_latest_oa_logistics_approvals",
        {
          start,
          end,
          transport_modes: this.normalizePullTransportMode(values.transport_modes),
          limit: Number(values.limit || 80) || 80,
        },
        true
      );
      this.renderOaPullResult(dialog, result, result.skipped || !result?.ok ? "warn" : "ready");
      if (result.skipped) {
        this.setOaPullPrimaryState(dialog, "ready");
        frappe.show_alert({ message: result.reason || ocTranslate("钉钉拉取已跳过"), indicator: "orange" });
        return;
      }
      if (!result?.ok) {
        const failedCount = Number(result.save?.failed_count || 0);
        throw new Error(
          result.save?.message || ocTranslate("钉钉审批已读取，但有 {0} 个批次保存失败，请查看失败明细。", [failedCount || "部分"])
        );
      }
      const save = result.save || {};
      const message = ocTranslate("钉钉拉取完成：新增 {0}，更新 {1}，已存在 {2}，跳过 {3}", [save.created_count || 0, save.updated_count || 0, save.unchanged_count || 0, save.skipped_count || 0]);
      this.recordUsage("DINGTALK_PULL", {
        remark: message,
        extra: { start, end, transport_modes: values.transport_modes || "ALL", limit: Number(values.limit || 80) || 80, save },
      });
      frappe.show_alert({ message, indicator: "green" });
      this.resetFilterValues();
      this.resetBatchScopeState();
      await this.loadBatches();
      dialog.$wrapper.data("ocw-pull-completed", true);
      this.setOaPullPrimaryState(dialog, "completed");
    } catch (error) {
      dialog.$wrapper.data("ocw-pull-completed", false);
      this.setOaPullPrimaryState(dialog, "ready");
      this.recordUsage("DINGTALK_PULL", {
        status: "Failed",
        remark: error.message || "钉钉拉取失败",
        extra: { start, end, transport_modes: values.transport_modes || "ALL" },
      });
      this.showError(error);
    }
  }

  async repullGapDingtalk(batchName = "") {
    const batch = this.findBatch(batchName || this.drawerBatchName || this.activeBatchName);
    if (!batch) return;
    const confirmed = await new Promise((resolve) => {
      frappe.confirm(ocTranslate("确认重新拉取这票货的钉钉原单并刷新页面数据？"), () => resolve(true), () => resolve(false));
    });
    if (!confirmed) return;
    try {
      const result = await this.call(
        "overseas_costing.api.import_api.refresh_oa_logistics_detail",
        {
          target: batch.name,
          limit: 50,
          include_non_sea: 1,
        },
        true
      );
      if (!result || result.ok === false) {
        throw new Error((result && result.message) || "钉钉原单刷新失败");
      }
      await this.refreshBatch(batch.name);
      if (this.$root.attr("data-screen") !== "detail") {
        await this.loadBatches();
        this.renderTable();
      }
      frappe.show_alert({ message: result.message || ocTranslate("钉钉原单已重新拉取"), indicator: "green" });
    } catch (error) {
      this.showError(error);
    }
  }

  async openGapItemEdit(batchName = "", itemName = "", fieldname = "") {
    const batch = this.findBatch(batchName || this.drawerBatchName || this.activeBatchName);
    if (!batch) return;
    if (this.$root.attr("data-screen") === "detail") {
      const result = await this.call("overseas_costing.api.workbench.locate_batch_item", {
        batch_name: batch.name,
        version_name: this.detailState.versionName || batch.current_version || null,
        item_name: itemName,
        page_length: this.detailState.sku.pageLength,
      });
      if (!result || !result.ok) {
        this.showPendingFeature((result && result.message) || "没有找到可补录的明细行。");
        return;
      }
      const targetField = this.getEditableFieldForGapRow(result.item || {}, fieldname);
      if (!targetField) {
        this.showPendingFeature("当前明细没有可补录的字段。");
        return;
      }
      this.detailState.sku.page = Number(result.page || 1);
      this.detailState.sku.keyword = "";
      this.detailState.sku.fieldGroup = "all";
      this.detailState.sku.sortBy = "row_no";
      this.detailState.sku.sortOrder = "asc";
      await this.switchDetailTab("items");
      return this.focusGapEditableCell(batch.name, itemName, targetField);
    } else {
      const row = (this.batchItems[batch.name] || []).find((item) => item.name === itemName);
      const targetField = this.getEditableFieldForGapRow(row || {}, fieldname);
      if (!row || !targetField) {
        this.showPendingFeature("没有找到可补录的明细行。");
        return;
      }
      this.closeBatchDrawer({ updateUrl: false });
      await this.focusBatch(batch.name, { updateUrl: false });
      return this.focusGapEditableCell(batch.name, itemName, targetField);
    }
  }

  focusGapEditableCell(batchName, itemName, targetField) {
    // focusBatch() rerenders the table, so locate the cell after rendering.
    const $target = this.$root
      .find("[data-editable-cell='1']")
      .filter((_, element) =>
        $(element).attr("data-batch-name") === batchName &&
        $(element).attr("data-item-name") === itemName &&
        $(element).attr("data-fieldname") === targetField
      )
      .first();
    if (!$target.length) {
      this.showPendingFeature("没有找到可编辑的明细单元格，请先展开该批次。");
      return;
    }
    this.startCellEdit($target, null, true);
    const targetElement = $target.get(0);
    if (targetElement && typeof targetElement.scrollIntoView === "function") {
      targetElement.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
    }
  }

  getEditableFieldForGapRow(row = {}, preferredFieldname = "") {
    if (!row || !Object.keys(row).length) return "";
    if (preferredFieldname && this.isEditableColumn({ fieldname: preferredFieldname }) && Object.prototype.hasOwnProperty.call(row, preferredFieldname)) {
      return preferredFieldname;
    }
    const priorities = [
      "material_code", "product_name", "quantity", "actual_shipped_qty", "unit_price",
      "purchase_currency", "goods_value", "gross_weight_kg", "volume_m3", "chargeable_weight_kg",
      "china_to_mexico_freight_rmb", "mexico_customs_mxn", "mexico_customs_rmb", "mexico_customs_usd",
      "import_tax_total", "igi_amount", "iva_amount", "total_cost_rmb", "total_unit_rmb",
    ];
    return priorities.find((name) => this.isEditableColumn({ fieldname: name }) && Object.prototype.hasOwnProperty.call(row, name)) || "";
  }

  getItemEditableFieldForGap(batchName = "", itemName = "", preferredFieldname = "") {
    const items = this.batchItems[batchName] || [];
    const row = items.find((item) => item.name === itemName);
    return this.getEditableFieldForGapRow(row || {}, preferredFieldname);
  }

  setOaPullPrimaryState(dialog, state = "ready") {
    const $button = dialog.get_primary_btn ? dialog.get_primary_btn() : dialog.$wrapper.find(".modal-footer .btn-primary");
    if (!$button || !$button.length) return;
    if (state === "running") {
      $button.prop("disabled", true).text(ocTranslate("拉取中..."));
      return;
    }
    $button.prop("disabled", false).text(state === "completed" ? ocTranslate("重新拉取") : ocTranslate("开始拉取"));
  }

  renderOaPullResult(dialog, result, state = "empty") {
    const $target = dialog.$wrapper.find("[data-area='oa-pull-result']");
    $target.removeClass("empty loading ready warn");
    if (state === "loading") {
      $target.addClass("loading").text(ocTranslate("正在拉取钉钉审批单并写入成本批次..."));
      return;
    }
    if (!result) {
      $target.addClass("empty").text(ocTranslate("尚未开始拉取"));
      return;
    }
    if (result.skipped) {
      $target.addClass("warn").html(`<strong>${ocTranslate("本次未执行拉取")}</strong><span>${this.escape(result.reason || ocTranslate("缺少钉钉配置"))}</span>`);
      return;
    }
    const pull = result.pull || {};
    const save = result.save || {};
    const counts = pull.transport_counts || {};
    if (!result.ok) {
      const failedCount = Number(save.failed_count || 0);
      $target.addClass("warn").html(
        `<strong>${ocTranslate("拉取未完全成功")}</strong><span>${this.escape(
          save.message || ocTranslate("有 {0} 个批次保存失败，未标记为完成。", [failedCount || "部分"])
        )}</span>`
      );
      return;
    }
    const modeText = (result.transport_modes || []).map((mode) => this.transportLabel(mode)).join("、") || "全部";
    const writeCount = Number(save.created_count || 0) + Number(save.updated_count || 0);
    const completionText = writeCount
      ? "已写入最新变化，页面列表已刷新。"
      : "当前范围内审批单已在系统中，页面列表已刷新。";
    $target.addClass("ready").html(`
      <div class="ocw-pull-success-head">
        <strong>${ocTranslate("拉取完成")}</strong>
        <span>${ocTranslate("{0}需要再次执行时，请点击底部“重新拉取”。", [this.escape(completionText)])}</span>
      </div>
      <div class="ocw-pull-result-grid">
        <div><span>${ocTranslate("日期范围")}</span><strong>${ocTranslate("{0} 至 {1}", [this.escape(result.start || "--"), this.escape(result.end || "--")])}</strong></div>
        <div><span>${ocTranslate("运输方式")}</span><strong>${this.escape(modeText)}</strong></div>
        <div><span>${ocTranslate("读取详情")}</span><strong>${this.escape(String(pull.detail_count || 0))}</strong></div>
        <div><span>${ocTranslate("命中国际物流")}</span><strong>${this.escape(String(pull.filtered_count || 0))}</strong></div>
        <div><span>${ocTranslate("新增批次")}</span><strong>${this.escape(String(save.created_count || 0))}</strong></div>
        <div><span>${ocTranslate("更新批次")}</span><strong>${this.escape(String(save.updated_count || 0))}</strong></div>
        <div><span>${ocTranslate("已存在")}</span><strong>${this.escape(String(save.unchanged_count || 0))}</strong></div>
        <div><span>${ocTranslate("跳过")}</span><strong>${this.escape(String(save.skipped_count || 0))}</strong></div>
      </div>
      <div class="ocw-pull-mode-counts">
        <span>${ocTranslate("海运 {0}", [this.escape(String(counts.SEA || 0))])}</span>
        <span>${ocTranslate("空运 {0}", [this.escape(String(counts.AIR || 0))])}</span>
        <span>${ocTranslate("快递 {0}", [this.escape(String(counts.EXPRESS || 0))])}</span>
      </div>
    `);
  }

  normalizePullTransportMode(value) {
    const text = String(value || "").trim();
    if (text === "海运") return "SEA";
    if (text === "空运") return "AIR";
    if (text === "快递") return "EXPRESS";
    return "ALL";
  }

  openImportDialog() {
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("批量 Excel 导入/解析"),
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "file_picker",
          options: `
            <div class="ocw-import-file-box">
              <label class="ocw-import-file-label">${ocTranslate("上传 Excel 文件")}</label>
              <div class="ocw-import-dropzone" data-import-dropzone="1" tabindex="0">
                <input class="ocw-import-file-input" type="file" accept=".xlsx,.xlsm" />
                <div class="ocw-import-drop-icon">XLSX</div>
                <div>
                  <strong data-area="import-file-name">${ocTranslate("拖放文件到这里，或点击选择")}</strong>
                  <span>${ocTranslate("支持 .xlsx / .xlsm")}</span>
                </div>
              </div>
              <div class="ocw-import-preview-actions">
                <button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="preview-import">${ocTranslate("解析预览")}</button>
                <span data-area="import-preview-status">${ocTranslate("选择文件后可先预览，不会写入数据。")}</span>
              </div>
              <div class="ocw-import-preview empty" data-area="import-preview">${ocTranslate("尚未解析")}</div>
            </div>
          `,
        },
        {
          fieldtype: "Data",
          fieldname: "source_sheet",
          label: ocTranslate("工作表名称"),
          default: "",
          description: ocTranslate("可留空自动识别。只有需要指定某个工作表时再填写 Excel 底部标签名。"),
        },
        {
          fieldtype: "Check",
          fieldname: "include_double_clear",
          label: ocTranslate("包含双清/包税数据"),
          default: 1,
        },
      ],
      primary_action_label: ocTranslate("导入"),
      primary_action: async (values) => {
        const file = this.getImportDialogFile(dialog);
        if (!this.validateImportFile(file)) return;
        const uploaded = dialog.$wrapper.data("ocw-import-upload") || {};
        const imported = await this.importExcel({
          ...values,
          source_sheet: String(values.source_sheet || "").trim(),
          file: uploaded.file_url ? null : file,
          file_url: uploaded.file_url || null,
          source_name: uploaded.file_name || file.name,
        });
        if (imported) dialog.hide();
      },
    });
    dialog.show();
    this.bindImportDropzone(dialog);
  }

  openBatchExcelSupplementDialog(batchName = "") {
    const batch = this.findBatch(batchName || this.detailState?.batchName || this.activeBatchName);
    if (!batch) {
      this.showPendingFeature("当前没有可补充的批次。");
      return;
    }
    const batchLabel = batch.batch_no || batch.source_approval_no || batch.customs_no || batch.name;
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("单批次 Excel 补充 · {0}", [batchLabel]),
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "batch_supplement_file",
          options: `
            <div class="ocw-import-file-box ocw-batch-supplement-box">
              <div class="ocw-scope-warning"><strong>${ocTranslate("仅写入当前批次")}</strong><span>${this.escape(batchLabel)}</span><small>${ocTranslate("文件中只要出现其他批次，整次操作就会被拒绝。")}</small></div>
              <label class="ocw-import-file-label">${ocTranslate("选择补充文件")}</label>
              <div class="ocw-import-dropzone" data-batch-supplement-dropzone="1" tabindex="0">
                <input class="ocw-import-file-input" type="file" accept=".xlsx,.xlsm" />
                <div class="ocw-import-drop-icon">XLSX</div>
                <div><strong data-area="import-file-name">${ocTranslate("拖放文件到这里，或点击选择")}</strong><span>${ocTranslate("上传后必须先预览作用范围")}</span></div>
              </div>
              <div class="ocw-import-preview-actions"><button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="preview-batch-supplement">${ocTranslate("预览作用范围")}</button><span data-area="import-preview-status">${ocTranslate("尚未预览，不会写入数据。")}</span></div>
              <div class="ocw-import-preview empty" data-area="import-preview">${ocTranslate("尚未预览")}</div>
            </div>
          `,
        },
        { fieldtype: "Data", fieldname: "source_sheet", label: ocTranslate("工作表名称"), description: ocTranslate("可留空自动识别。") },
      ],
      primary_action_label: ocTranslate("补充当前批次 {0}", [batchLabel]),
      primary_action: async (values) => {
        const preview = dialog.$wrapper.data("ocw-batch-supplement-preview");
        const uploaded = dialog.$wrapper.data("ocw-import-upload") || {};
        if (!preview || !preview.ok || !uploaded.file_url) {
          frappe.msgprint(ocTranslate("请先预览并确认文件只属于当前批次。"));
          return;
        }
        if (!(await this.ensureEditSession())) return;
        const result = await this.call(
          "overseas_costing.api.import_api.apply_batch_excel_supplement",
          {
            batch_name: batch.name,
            file_url: uploaded.file_url,
            source_sheet: String(values.source_sheet || "").trim() || null,
            expected_modified: batch.modified || null,
            edit_token: this.detailState?.editToken || null,
          },
          true
        );
        if (!result || !result.ok) {
          this.renderBatchSupplementPreview(dialog, result || { ok: false, message: ocTranslate("补充失败。") });
          return;
        }
        this.acceptBatchWriteRevision?.(result.batch_modified);
        dialog.hide();
        frappe.show_alert({ message: result.message || ocTranslate("当前批次已补充"), indicator: "green" });
        await this.openBatchDetail(batch.name, "items", { updateUrl: false });
      },
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-batch-supplement-modal");
    dialog.get_primary_btn().prop("disabled", true);
    const $dropzone = dialog.$wrapper.find("[data-batch-supplement-dropzone='1']");
    const $input = dialog.$wrapper.find(".ocw-import-file-input");
    const setFile = (file) => {
      if (!file) return;
      dialog.$wrapper.data("ocw-import-file", file).removeData("ocw-import-upload ocw-batch-supplement-preview");
      dialog.$wrapper.find("[data-area='import-file-name']").text(file.name);
      dialog.get_primary_btn().prop("disabled", true);
      this.renderBatchSupplementPreview(dialog, null);
    };
    $dropzone.on("click keydown", (event) => {
      if (event.type === "keydown" && !["Enter", " "].includes(event.key)) return;
      event.preventDefault();
      $input.trigger("click");
    });
    $input.on("click", (event) => event.stopPropagation());
    $input.on("change", (event) => setFile(event.currentTarget.files?.[0]));
    $dropzone.on("dragenter dragover", (event) => { event.preventDefault(); $dropzone.addClass("is-dragover"); });
    $dropzone.on("dragleave dragend drop", (event) => { event.preventDefault(); $dropzone.removeClass("is-dragover"); });
    $dropzone.on("drop", (event) => setFile(event.originalEvent?.dataTransfer?.files?.[0]));
    dialog.$wrapper.on("click", "[data-action='preview-batch-supplement']", async (event) => {
      event.preventDefault();
      const file = this.getImportDialogFile(dialog);
      if (!this.validateImportFile(file)) return;
      this.renderBatchSupplementPreview(dialog, { loading: true });
      try {
        const uploaded = await this.ensureImportFileUploaded(dialog, file);
        const values = dialog.get_values() || {};
        const result = await this.call(
          "overseas_costing.api.import_api.preview_batch_excel_supplement",
          { batch_name: batch.name, file_url: uploaded.file_url, source_sheet: String(values.source_sheet || "").trim() || null },
          true
        );
        dialog.$wrapper.data("ocw-batch-supplement-preview", result);
        dialog.get_primary_btn().prop("disabled", !result.ok);
        this.renderBatchSupplementPreview(dialog, result);
      } catch (error) {
        dialog.get_primary_btn().prop("disabled", true);
        this.renderBatchSupplementPreview(dialog, { ok: false, message: this.normalizeErrorMessage(error) });
      }
    });
  }

  renderBatchSupplementPreview(dialog, result) {
    const $preview = dialog.$wrapper.find("[data-area='import-preview']");
    const $status = dialog.$wrapper.find("[data-area='import-preview-status']");
    $preview.removeClass("empty loading ready warn");
    if (!result) {
      $status.text(ocTranslate("尚未预览，不会写入数据。"));
      $preview.addClass("empty").text(ocTranslate("尚未预览"));
      return;
    }
    if (result.loading) {
      $status.text(ocTranslate("正在上传并校验批次范围…"));
      $preview.addClass("loading").text(ocTranslate("校验中"));
      return;
    }
    if (!result.ok) {
      const foreign = (result.foreign_batches || []).map((value) => `<span>${this.escape(value)}</span>`).join("");
      $status.text(ocTranslate("文件未通过作用范围校验。"));
      $preview.addClass("warn").html(`<strong>${ocTranslate("文件包含其他批次，未写入任何数据")}</strong><p>${this.escape(result.message || "")}</p><div class="ocw-import-preview-batches">${foreign}</div>`);
      return;
    }
    const fields = (result.changed_fields || []).slice(0, 12);
    $status.text(ocTranslate("作用范围校验通过，可补充当前批次。"));
    $preview.addClass("ready").html(`
      <div class="ocw-import-preview-grid">
        <div><span>${ocTranslate("目标批次")}</span><strong>${this.escape(result.batch_name || result.batch_doc_name || "--")}</strong></div>
        <div><span>${ocTranslate("SKU 行数")}</span><strong>${Number(result.sku_count || 0)}</strong></div>
        <div><span>${ocTranslate("将补充字段")}</span><strong>${Number((result.changed_fields || []).length)}</strong></div>
        <div><span>${ocTranslate("冲突")}</span><strong>${Number((result.conflicts || []).length)}</strong></div>
        <div><span>${ocTranslate("跳过")}</span><strong>${Number((result.skipped_items || []).length)}</strong></div>
      </div>
      <div class="ocw-import-preview-batches">${fields.map((field) => `<span>${this.escape(field)}</span>`).join("") || `<span>${ocTranslate("无可补充字段")}</span>`}</div>
    `);
  }

  bindImportDropzone(dialog) {
    const $dropzone = dialog.$wrapper.find("[data-import-dropzone='1']");
    const $input = dialog.$wrapper.find(".ocw-import-file-input");
    const $fileName = dialog.$wrapper.find("[data-area='import-file-name']");
    const setFile = (file) => {
      if (!file) return;
      dialog.$wrapper.data("ocw-import-file", file);
      dialog.$wrapper.removeData("ocw-import-upload");
      $fileName.text(file.name);
      $dropzone.addClass("has-file");
      this.renderImportPreview(dialog, null, "empty");
    };

    $dropzone.on("click keydown", (event) => {
      if (event.type === "keydown" && !["Enter", " "].includes(event.key)) return;
      event.preventDefault();
      $input.trigger("click");
    });
    $input.on("click", (event) => event.stopPropagation());
    $input.on("change", (event) => setFile(event.currentTarget.files?.[0]));
    $dropzone.on("dragenter dragover", (event) => {
      event.preventDefault();
      event.stopPropagation();
      $dropzone.addClass("is-dragover");
    });
    $dropzone.on("dragleave dragend drop", (event) => {
      event.preventDefault();
      event.stopPropagation();
      $dropzone.removeClass("is-dragover");
    });
    $dropzone.on("drop", (event) => {
      setFile(event.originalEvent?.dataTransfer?.files?.[0]);
    });
    dialog.$wrapper.on("click", "[data-action='preview-import']", (event) => {
      event.preventDefault();
      this.previewImportExcel(dialog).catch((error) => this.showError(error));
    });
  }

  getImportDialogFile(dialog) {
    return dialog.$wrapper.data("ocw-import-file") || dialog.$wrapper.find(".ocw-import-file-input").get(0)?.files?.[0] || null;
  }

  validateImportFile(file) {
    if (!file) {
      frappe.msgprint(ocTranslate("请先上传 Excel 文件。"));
      return false;
    }
    if (!this.isExcelFileRef(file.name || "")) {
      frappe.msgprint(ocTranslate("请上传 .xlsx / .xlsm 格式的 Excel 文件。"));
      return false;
    }
    return true;
  }

  async ensureImportFileUploaded(dialog, file) {
    const uploaded = dialog.$wrapper.data("ocw-import-upload");
    const sameFile =
      uploaded &&
      uploaded.file_url &&
      uploaded.source_file_name === file.name &&
      uploaded.source_file_size === file.size &&
      uploaded.source_file_modified === file.lastModified;
    if (sameFile) return uploaded;

    const uploadResult = await this.uploadImportFile(file);
    const normalized = {
      ...uploadResult,
      source_file_name: file.name,
      source_file_size: file.size,
      source_file_modified: file.lastModified,
    };
    dialog.$wrapper.data("ocw-import-upload", normalized);
    return normalized;
  }

  async previewImportExcel(dialog) {
    const file = this.getImportDialogFile(dialog);
    if (!this.validateImportFile(file)) return;

    const values = dialog.get_values() || {};
    this.renderImportPreview(dialog, null, "loading");
    const uploaded = await this.ensureImportFileUploaded(dialog, file);
    const result = await this.call(
      "overseas_costing.api.import_api.preview_yuewei_excel_file",
      {
        source_name: uploaded.file_name || file.name,
        file_url: uploaded.file_url,
        source_sheet: String(values.source_sheet || "").trim() || null,
        transport_keyword: "",
        include_double_clear: values.include_double_clear ? 1 : 0,
      },
      true
    );
    dialog.$wrapper.data("ocw-import-preview", result);
    this.renderImportPreview(dialog, result, "ready");
  }

  renderImportPreview(dialog, result, state = "empty") {
    const $preview = dialog.$wrapper.find("[data-area='import-preview']");
    const $status = dialog.$wrapper.find("[data-area='import-preview-status']");
    $preview.removeClass("empty loading ready warn");
    if (state === "loading") {
      $status.text(ocTranslate("正在上传并解析文件..."));
      $preview.addClass("loading").text(ocTranslate("解析中"));
      return;
    }
    if (!result) {
      $status.text(ocTranslate("选择文件后可先预览，不会写入数据。"));
      $preview.addClass("empty").text(ocTranslate("尚未解析"));
      return;
    }

    const parser = result.parser_meta || {};
    const selected = result.selected_summary || {};
    const source = result.source_summary || {};
    const batchIds = selected.batch_ids || [];
    const isEmpty = !Number(selected.block_count || 0);
    const visibleBatches = batchIds.slice(0, 5).map((batchId) => `<span>${this.escape(batchId)}</span>`).join("");
    const moreText = batchIds.length > 5 ? `<em>${ocTranslate("等 {0} 个", [this.escape(String(batchIds.length))])}</em>` : "";
    $status.text(isEmpty ? ocTranslate("已解析，但当前筛选未命中可导入批次。") : ocTranslate("预览完成，确认无误后点击导入。"));
    $preview.addClass(isEmpty ? "warn" : "ready").html(`
      <div class="ocw-import-preview-grid">
        <div><span>${ocTranslate("工作表")}</span><strong>${this.escape(parser.sourceSheet || "--")}</strong></div>
        <div><span>${ocTranslate("解析器")}</span><strong>${this.escape(this.parserLabel(parser.parser))}</strong></div>
        <div><span>${ocTranslate("识别批次")}</span><strong>${this.escape(String(source.block_count || 0))}</strong></div>
        <div><span>${ocTranslate("命中批次")}</span><strong>${this.escape(String(selected.block_count || 0))}</strong></div>
        <div><span>${ocTranslate("SKU 行数")}</span><strong>${this.escape(String(selected.item_count || 0))}</strong></div>
      </div>
      <div class="ocw-import-preview-batches">${visibleBatches || `<span>${ocTranslate("无命中批次")}</span>`}${moreText}</div>
    `);
  }

  parserLabel(parserName) {
    const labels = {
      oa_attachment_detail: ocTranslate("国际物流附件"),
      yuewei_cost_workbook: ocTranslate("成本总表"),
    };
    return labels[parserName] || parserName || "--";
  }

  async importExcel(values) {
    try {
      let fileUrl = values.file_url || null;
      let sourceRef = values.source_name || (values.file ? values.file.name : values.file_url || values.file_path || "");
      if (values.file) {
        const uploadResult = await this.uploadImportFile(values.file);
        fileUrl = uploadResult.file_url || fileUrl;
        sourceRef = uploadResult.file_name || values.file.name || fileUrl || sourceRef;
      }
      const result = await this.call(
        "overseas_costing.api.import_api.import_yuewei_excel_file",
        {
          source_name: this.fileNameFromRef(sourceRef) || "Yuewei Excel",
          file_path: values.file_path || null,
          file_url: fileUrl,
          source_sheet: values.source_sheet || null,
          transport_keyword: "",
          include_double_clear: values.include_double_clear ? 1 : 0,
          fx_rmb_to_mxn: 2.6,
        },
        true
      );
      const selected = result.selected_summary || {};
      const importStats = this.summarizeImportResult(result);
      const parser = result.parser_meta || {};
      const parserHint = parser.sourceSheet ? ocTranslate("，工作表：{0}", [parser.sourceSheet]) : "";
      frappe.show_alert({
        message: ocTranslate("导入完成：{0} 个批次，{1} 行{2}；{3}", [selected.block_count || 0, selected.item_count || 0, parserHint, importStats]),
        indicator: "green",
      });
      this.lastImportResult = result;
      this.resetFilterValues();
      await this.loadBatches();
      await this.focusImportedBatches(result, importStats);
      return true;
    } catch (error) {
      this.showError(error);
      return false;
    }
  }

  async uploadImportFile(file) {
    const formData = new FormData();
    formData.append("file", file, file.name);
    formData.append("is_private", "1");
    formData.append("folder", "Home");

    const message = await this.uploadFileRequest(formData);
    if (!message.file_url) {
      throw new Error(ocTranslate("Excel 文件已上传，但没有返回文件地址。"));
    }
    return message;
  }

  extractServerMessage(data) {
    if (!data) return "";
    if (typeof data === "string") {
      try {
        return this.extractServerMessage(JSON.parse(data));
      } catch (_error) {
        return data;
      }
    }
    if (data.message && typeof data.message === "string") return data.message;
    if (data._server_messages) {
      try {
        const messages = JSON.parse(data._server_messages).map((item) => {
          try {
            const parsed = JSON.parse(item);
            return parsed.message || item;
          } catch (_error) {
            return item;
          }
        });
        return messages.join("；");
      } catch (_error) {
        return String(data._server_messages);
      }
    }
    if (data.exception) return String(data.exception);
    if (data.exc) return String(data.exc);
    if (data.statusText) return String(data.statusText);
    return "";
  }

  fileNameFromRef(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    const clean = text.split("?")[0];
    const name = clean.split(/[\\/]/).filter(Boolean).pop() || clean;
    try {
      return decodeURIComponent(name);
    } catch (_error) {
      return name;
    }
  }

  summarizeImportResult(result) {
    const batches = result.created_batches || [];
    if (!batches.length) return "未写入新数据";
    const batchCreated = batches.filter((row) => row.batch_action === "created").length;
    const batchUpdated = batches.filter((row) => row.batch_action === "updated").length;
    const itemCreated = batches.reduce((total, row) => total + Number(row.created_item_count || 0), 0);
    const itemUpdated = batches.reduce((total, row) => total + Number(row.updated_item_count || 0), 0);
    const itemUnchanged = batches.reduce((total, row) => total + Number(row.unchanged_item_count || 0), 0);
    return ocTranslate("批次新增 {0} / 更新 {1}，明细新增 {2} / 更新 {3} / 未变 {4}", [batchCreated, batchUpdated, itemCreated, itemUpdated, itemUnchanged]);
  }

  importedBatchNames(result) {
    return (result.created_batches || []).map((row) => row.batch_name).filter(Boolean);
  }

  importedBatchLabels(result) {
    return (result.created_batches || [])
      .map((row) => row.batch_no || row.batch_name)
      .filter(Boolean);
  }

  async focusImportedBatches(result, statsText = "") {
    const batchNames = this.importedBatchNames(result);
    if (!batchNames.length) {
      this.updateSearchResult();
      return;
    }

    const imported = new Set(batchNames);
    this.lastImportedBatchNames = imported;
    this.visibleBatches = this.batches.slice();
    await this.prefetchBatchItems(this.visibleBatches);
    this.expandedBatchNames = new Set(this.visibleBatches.filter((batch) => imported.has(batch.name)).map((batch) => batch.name));

    const activeBatch = this.visibleBatches.find((batch) => imported.has(batch.name));
    if (activeBatch) {
      this.activeBatchName = activeBatch.name;
      await this.loadAuditLogs(activeBatch.name, activeBatch.current_version);
    }

    this.renderTable();
    this.renderImportResult(result, statsText);
  }

  renderImportResult(result, statsText = "") {
    const labels = this.importedBatchLabels(result);
    const visibleLabels = labels.slice(0, 3).join("、");
    const suffix = labels.length > 3 ? ocTranslate("等 {0} 个批次", [labels.length]) : "";
    const selected = result.selected_summary || {};
    const message = ocTranslate("本次导入：{0} 个批次，{1} 行 SKU；{2}。已在完整列表中展开 {3}{4}", [selected.block_count || labels.length || 0, selected.item_count || 0, statsText || this.summarizeImportResult(result), visibleLabels, suffix]);
    this.$root.find("[data-area='search-result']").removeClass("empty").addClass("active imported").text(message);
  }

  renderRecalculateResult(batchName, summary = {}) {
    const batch = this.findBatch(batchName) || this.getVisibleActiveBatch();
    const label = batch ? batch.waybill_no || batch.batch_no || batch.name : batchName;
    const ai = summary.ai_allocation || {};
    const aiText = ai.ok
      ? ocTranslate("AI基础分摊：已填入（{0}）", [ai.model || "DeepSeek"])
      : ocTranslate("AI基础分摊：{0}", [ai.message || "未生成，已使用系统基础规则"]);
    const parts = [
      ocTranslate("批次 {0}", [label || "--"]),
      ocTranslate("SKU {0} 行", [this.formatValue(summary.item_count || 0)]),
      ocTranslate("总货值 {0} RMB", [this.formatMoney(summary.total_goods_value || 0)]),
      ocTranslate("毛重 {0} KG", [this.formatNumber(summary.total_gross_weight_kg || 0)]),
      ocTranslate("综合成本 {0} RMB", [this.formatMoney(summary.total_cost_rmb || 0)]),
      ocTranslate("规则 {0} 条", [this.formatValue(summary.rule_count || 0)]),
      aiText,
    ];
    this.$root
      .find("[data-area='search-result']")
      .removeClass("empty imported")
      .addClass("active calculated")
      .text(ocTranslate("试算完成：{0}", [parts.join("；")]));
  }

  async openCategoryPreviewDialog(batchName = "") {
    const allSelectableBatches = this.getSelectableBatches();
    const pinnedBatch = batchName ? this.getSelectableBatch(batchName, allSelectableBatches) || this.getDetailBatch() : null;
    const selectableBatches = pinnedBatch ? [pinnedBatch] : allSelectableBatches;
    const batch = pinnedBatch || this.getSelectableBatch(batchName, selectableBatches);
    if (!batch) {
      this.showPendingFeature("当前没有可归类的批次，请先拉取或查询一条数据。");
      return;
    }
    this.activeBatchName = batch.name;
    const batchOptions = this.renderSelectableBatchOptions(batch.name, selectableBatches);
    const batchSelectDisabled = pinnedBatch || !selectableBatches.length ? " disabled" : "";

    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("AI 商品业务归类预览"),
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "category_preview",
          options: `
            <div class="ocw-category-toolbar">
              <label>
                <span>${ocTranslate("当前批次")}</span>
                <select class="form-control ocw-batch-select" data-role="category-batch-select" aria-label="${ocTranslate('选择商品归类批次')}"${batchSelectDisabled}>${batchOptions}</select>
              </label>
              <em>${this.escape(this.scopedBatchHint())}</em>
            </div>
            <div class="ocw-category-preview" data-area="category-preview">
              <div class="ocw-category-loading">${ocTranslate("正在检查当前批次是否存在可建议的业务大类...")}</div>
            </div>
          `,
        },
      ],
      primary_action_label: ocTranslate("关闭"),
      primary_action: () => dialog.hide(),
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-category-modal");
    dialog.$wrapper.data("ocw-category-batch-name", batch.name);
    dialog.$wrapper.on("change", "[data-role='category-batch-select']", (event) => {
      const selectedBatch = this.findSelectableBatch(String($(event.currentTarget).val() || ""));
      if (!selectedBatch) return;
      this.activeBatchName = selectedBatch.name;
      dialog.$wrapper.data("ocw-category-batch-name", selectedBatch.name);
      this.loadCategoryPreview(dialog, selectedBatch).catch((error) => {
        dialog.hide();
        this.showError(error);
      });
    });
    this.loadCategoryPreview(dialog, batch).catch((error) => {
      dialog.hide();
      this.showError(error);
    });
  }

  async loadCategoryPreview(dialog, batch) {
    dialog.$wrapper.find("[data-area='category-preview']").html(`
      <div class="ocw-category-loading">${ocTranslate("正在检查当前批次是否存在可建议的业务大类...")}</div>
    `);
    const result = await this.call(
      "overseas_costing.api.category.preview_batch_categories",
      {
        batch_name: batch.name,
        version_name: batch.current_version,
        limit: 500,
      },
      true
    );
    this.renderCategoryPreview(dialog, result, batch);
  }

  renderCategoryPreview(dialog, result, batch) {
    const $target = dialog.$wrapper.find("[data-area='category-preview']");
    if (!result || !result.ok) {
      $target.html(`
        <div class="ocw-category-empty">
          <strong>${ocTranslate("暂时无法生成商品归类预览")}</strong>
          <span>${this.escape((result && result.message) || ocTranslate("请确认当前批次已有物料明细。"))}</span>
        </div>
      `);
      return;
    }

    const items = result.items || [];
    const summary = result.summary || {};
    const categoryCounts = summary.category_counts || {};
    const countChips = Object.keys(categoryCounts)
      .map((name) => `<span>${this.escape(name)} ${this.escape(String(categoryCounts[name]))}</span>`)
      .join("");
    const rows = items.map((row) => this.renderCategoryPreviewRow(row)).join("");
    const batchLabel = batch.waybill_no || batch.batch_no || batch.name;

    $target.html(`
      <div class="ocw-category-summary">
        <div><span>${ocTranslate("当前批次")}</span><strong>${this.escape(batchLabel || "--")}</strong></div>
        <div><span>${ocTranslate("物料行数")}</span><strong>${this.escape(String(summary.item_count || items.length || 0))}</strong></div>
        <div><span>${ocTranslate("归类候选")}</span><strong>${this.escape(String(summary.business_category_candidate_count || summary.normalization_candidate_count || 0))}</strong></div>
        <div><span>${ocTranslate("AI命中")}</span><strong>${this.escape(String(summary.ai_business_category_count || summary.ai_normalization_count || 0))}</strong></div>
        <div><span>${ocTranslate("无需处理")}</span><strong>${this.escape(String(summary.no_action_count || 0))}</strong></div>
        <div><span>${ocTranslate("影响税费")}</span><strong>${ocTranslate("不影响")}</strong></div>
      </div>
      <div class="ocw-category-note">
        ${ocTranslate("AI 用于把不同语言、不同写法的商品归到统一业务大类，辅助查询、汇总和分摊参考；不会修改海关进口名称、HS 编码、税率和完税金额。")}
      </div>
      <div class="ocw-category-counts">${countChips || `<span>${ocTranslate("当前批次没有明确的归类候选")}</span>`}</div>
      <div class="ocw-category-table-wrap">
        <table class="ocw-category-table">
          <thead>
            <tr>
              <th>${ocTranslate("物料编码")}</th>
              <th>${ocTranslate("中文品名")}</th>
              <th>${ocTranslate("海关进口名称")}</th>
              <th>${ocTranslate("规格型号")}<br>Especificación / Modelo</th>
              <th>${ocTranslate("建议业务大类")}</th>
              <th>${ocTranslate("状态")}</th>
              <th>${ocTranslate("原因")}</th>
            </tr>
          </thead>
          <tbody>${rows || `<tr><td colspan="7">${ocTranslate("当前批次暂无物料明细")}</td></tr>`}</tbody>
        </table>
      </div>
    `);
  }

  renderCategoryPreviewRow(row) {
    const status = this.categoryStatusInfo(row);
    return `
      <tr class="${this.escape(status.rowClass)}">
        <td>${this.escape(row.material_code || "--")}</td>
        <td title="${this.escape(row.product_name || "")}">${this.escape(row.product_name || "--")}</td>
        <td title="${this.escape(row.import_name || "")}">${this.escape(row.import_name || "--")}</td>
        <td title="${this.escape(row.spec_model || "")}">${this.escape(row.spec_model || "--")}</td>
        <td><strong>${this.escape(row.suggested_business_category || row.suggested_name || "--")}</strong></td>
        <td><span class="ocw-category-status ${this.escape(status.className)}">${this.escape(ocTranslate(status.label))}</span></td>
        <td title="${this.escape(row.reason || "")}">${this.escape(row.reason || "--")}</td>
      </tr>
    `;
  }

  categoryStatusInfo(row) {
    if (row.needs_review) {
      if (row.ai_ready || row.match_type === "ai_business_category" || row.match_type === "ai_name_normalization") {
        return { label: ocTranslate("AI候选"), className: "ai", rowClass: "needs-ai" };
      }
      return { label: ocTranslate("待确认"), className: "review", rowClass: "needs-review" };
    }
    return { label: ocTranslate("无需归类"), className: "noop", rowClass: "" };
  }

  formatConfidence(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return "--";
    const normalized = number > 1 ? number / 100 : number;
    return `${Math.round(normalized * 100)}%`;
  }

  isExcelFileRef(value) {
    const text = String(value || "").split("?")[0].toLowerCase();
    return text.endsWith(".xlsx") || text.endsWith(".xlsm");
  }

  isPdfFileRef(value) {
    const text = String(value || "").split("?")[0].toLowerCase();
    return text.endsWith(".pdf");
  }

  isImageFileRef(value) {
    const text = String(value || "").split("?")[0].toLowerCase();
    return [".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"].some((suffix) => text.endsWith(suffix));
  }
