  }

  openFileParseDialog(pinnedBatchName = "") {
    const allSelectableBatches = this.getSelectableBatches();
    const pinnedBatch = pinnedBatchName ? this.getSelectableBatch(pinnedBatchName, allSelectableBatches) || this.getDetailBatch() : null;
    const selectableBatches = pinnedBatch ? [pinnedBatch] : allSelectableBatches;
    const batch = pinnedBatch || this.getSelectableBatch("", selectableBatches);
    const batchName = batch ? batch.name : "";
    const batchHint = this.voucherBatchHint(batch);
    const batchOptions = this.renderSelectableBatchOptions(batchName, selectableBatches);
    const batchSelectDisabled = pinnedBatch || !selectableBatches.length ? " disabled" : "";
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("完税凭证对比"),
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "file_parse",
          options: `
            <div class="ocw-file-parse-box">
              <div class="ocw-voucher-target">
                <label class="ocw-voucher-batch-picker">
                  <span>${ocTranslate("对比批次")}</span>
                  <select class="form-control ocw-batch-select" data-role="voucher-batch-select" aria-label="${ocTranslate('选择凭证对比批次')}"${batchSelectDisabled}>${batchOptions}</select>
                </label>
                <em data-area="voucher-batch-hint">${this.escape(batchHint)}</em>
              </div>
              <label class="ocw-import-file-label">${ocTranslate("上传完税凭证 PDF")}</label>
              <div class="ocw-import-dropzone" data-voucher-dropzone="1" tabindex="0">
                <input class="ocw-voucher-file-input" type="file" accept=".pdf" />
                <div class="ocw-import-drop-icon">PDF</div>
                <div>
                  <strong data-area="voucher-file-name">${ocTranslate("拖放 PDF 到这里，或点击选择")}</strong>
                  <span>${ocTranslate("当前仅做完税凭证识别与系统数据对比，不写入成本表。")}</span>
                </div>
              </div>
              <div class="ocw-import-preview-actions">
                <button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="preview-voucher">${ocTranslate("对比预览")}</button>
                <span data-area="voucher-preview-status">${ocTranslate("选择文件后可先对比凭证字段。")}</span>
              </div>
              <div class="ocw-voucher-preview empty" data-area="voucher-preview">${ocTranslate("尚未对比")}</div>
              <div class="ocw-voucher-records">
                <div class="ocw-voucher-records-head">
                  <strong>${ocTranslate("已保存对比记录")}</strong>
                  <div class="ocw-voucher-records-actions">
                    <button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="refresh-voucher-records">${ocTranslate("刷新")}</button>
                    <button class="ocw-danger-btn ocw-mini-btn" type="button" data-action="delete-voucher-records">${ocTranslate("删除")}</button>
                  </div>
                </div>
                <div class="ocw-voucher-record-list loading" data-area="voucher-records">${ocTranslate("加载中")}</div>
              </div>
            </div>
          `,
        },
      ],
      primary_action_label: ocTranslate("保存对比结果"),
      primary_action: async () => {
        try {
          await this.saveTaxCertificateParseResult(dialog);
        } catch (error) {
          this.showError(error);
        }
      },
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-voucher-modal");
    this.activeVoucherParseDialog = dialog;
    dialog.$wrapper.data("ocw-voucher-batch-name", batchName);
    this.updateVoucherPrimarySaveAction(dialog, false);
    this.bindVoucherDropzone(dialog);
    this.loadTaxCertificateRecords(dialog).catch((error) => this.showError(error));
  }

  bindVoucherDropzone(dialog) {
    const $dropzone = dialog.$wrapper.find("[data-voucher-dropzone='1']");
    const $input = dialog.$wrapper.find(".ocw-voucher-file-input");
    const $fileName = dialog.$wrapper.find("[data-area='voucher-file-name']");
    const setFile = (file) => {
      if (!file) return;
      dialog.$wrapper.data("ocw-voucher-file", file);
      dialog.$wrapper.removeData("ocw-voucher-upload");
      $fileName.text(file.name);
      $dropzone.addClass("has-file");
      this.renderVoucherPreview(dialog, null, "empty");
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
    dialog.$wrapper.on("change", "[data-role='voucher-batch-select']", (event) => {
      const batch = this.findSelectableBatch(String($(event.currentTarget).val() || ""));
      dialog.$wrapper.data("ocw-voucher-batch-name", batch ? batch.name : "");
      dialog.$wrapper.removeData("ocw-voucher-preview");
      this.renderVoucherPreview(dialog, null, "empty");
      this.updateVoucherPrimarySaveAction(dialog, false);
      dialog.$wrapper.find("[data-area='voucher-batch-hint']").text(this.voucherBatchHint(batch));
      this.loadTaxCertificateRecords(dialog).catch((error) => this.showError(error));
    });
    dialog.$wrapper.on("click", "[data-action='preview-voucher']", (event) => {
      event.preventDefault();
      this.previewTaxCertificate(dialog).catch((error) => this.showError(error));
    });
    dialog.$wrapper.on("click", "[data-action='save-voucher-parse']", (event) => {
      event.preventDefault();
      this.saveTaxCertificateParseResult(dialog).catch((error) => this.showError(error));
    });
    dialog.$wrapper.on("click", "[data-action='refresh-voucher-records']", (event) => {
      event.preventDefault();
      this.loadTaxCertificateRecords(dialog).catch((error) => this.showError(error));
    });
    dialog.$wrapper.on("click", "[data-action='delete-voucher-records']", (event) => {
      event.preventDefault();
      this.deleteTaxCertificateRecords(dialog).catch((error) => this.showError(error));
    });
    dialog.$wrapper.on("click", "[data-action='open-voucher-record']", (event) => {
      event.preventDefault();
      const recordName = $(event.currentTarget).attr("data-record-name");
      if (recordName) this.openTaxCertificateRecordDialog(recordName).catch((error) => this.showError(error));
    });
    dialog.$wrapper.on("click", "[data-action='ai-review-voucher']", (event) => {
      event.preventDefault();
      const $button = $(event.currentTarget);
      return this.startVoucherFeeEvidenceReview($button, {
        fallbackBatchName: String(dialog.$wrapper.data("ocw-voucher-batch-name") || ""),
      });
    });
  }

  getVoucherDialogFile(dialog) {
    return dialog.$wrapper.data("ocw-voucher-file") || dialog.$wrapper.find(".ocw-voucher-file-input").get(0)?.files?.[0] || null;
  }

  validateVoucherFile(file) {
    if (!file) {
      frappe.msgprint(ocTranslate("请先上传完税凭证 PDF。"));
      return false;
    }
    if (!this.isPdfFileRef(file.name || "")) {
      frappe.msgprint(ocTranslate("请上传 .pdf 格式的完税凭证。"));
      return false;
    }
    return true;
  }

  async ensureVoucherFileUploaded(dialog, file) {
    const uploaded = dialog.$wrapper.data("ocw-voucher-upload");
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
    dialog.$wrapper.data("ocw-voucher-upload", normalized);
    return normalized;
  }

  async previewTaxCertificate(dialog) {
    const file = this.getVoucherDialogFile(dialog);
    if (!this.validateVoucherFile(file)) return;

    this.renderVoucherPreview(dialog, null, "loading");
    const uploaded = await this.ensureVoucherFileUploaded(dialog, file);
    const result = await this.call(
      "overseas_costing.api.import_api.preview_tax_certificate_pdf",
      {
        source_name: uploaded.file_name || file.name,
        file_url: uploaded.file_url,
        batch_name: dialog.$wrapper.data("ocw-voucher-batch-name") || "",
      },
      true
    );
    dialog.$wrapper.data("ocw-voucher-preview", result);
    this.renderVoucherPreview(dialog, result, "ready");
  }

  async saveTaxCertificateParseResult(dialog) {
    const file = this.getVoucherDialogFile(dialog);
    if (!this.validateVoucherFile(file)) return;

    const preview = dialog.$wrapper.data("ocw-voucher-preview") || {};
    if (!preview.ok) {
      frappe.msgprint(ocTranslate("请先点击「对比预览」，确认凭证已匹配到系统批次后再保存。"));
      return;
    }
    const canSave = Boolean(preview.reconciliation && preview.reconciliation.batch && preview.reconciliation.batch.name);
    if (!canSave) {
      frappe.msgprint(ocTranslate("当前凭证还没有匹配到系统批次，暂不能保存对比结果。"));
      return;
    }

    this.updateVoucherPrimarySaveAction(dialog, false, "保存中");
    let result;
    try {
      const uploaded = await this.ensureVoucherFileUploaded(dialog, file);
      result = await this.call(
        "overseas_costing.api.import_api.save_tax_certificate_parse_result",
        {
          source_name: uploaded.file_name || file.name,
          file_url: uploaded.file_url,
          batch_name: dialog.$wrapper.data("ocw-voucher-batch-name") || "",
        },
        true
      );
    } catch (error) {
      this.updateVoucherPrimarySaveAction(dialog, canSave);
      throw error;
    }
    if (!result || !result.ok) {
      frappe.msgprint((result && result.message) || "保存对比结果失败。");
      this.updateVoucherPrimarySaveAction(dialog, canSave);
      return;
    }
    frappe.show_alert({
      message: result.message || ocTranslate("保存完成。"),
      indicator: result.fx_sync && result.fx_sync.action === "updated" ? "orange" : "green",
    });
    if (result.batch_name) {
      await this.refreshBatch(result.batch_name).catch((error) => this.showError(error));
    }
    try {
      await this.loadTaxCertificateRecords(dialog);
    } catch (error) {
      this.showError(error);
    }
    this.resetVoucherDialogAfterSave(dialog);
  }

  async loadTaxCertificateRecords(dialog) {
    const $records = dialog.$wrapper.find("[data-area='voucher-records']");
    $records.removeClass("empty ready").addClass("loading").text(ocTranslate("加载中"));
    const result = await this.call(
      "overseas_costing.api.import_api.list_tax_certificate_parse_records",
      {
        batch_name: dialog.$wrapper.data("ocw-voucher-batch-name") || "",
        limit: 10,
      },
      false
    );
    this.renderTaxCertificateRecords(dialog, result);
  }

  renderTaxCertificateRecords(dialog, result) {
    const $records = dialog.$wrapper.find("[data-area='voucher-records']");
    const items = (result && result.items) || [];
    const recordNames = items.map((row) => String((row && row.name) || "").trim()).filter(Boolean);
    dialog.$wrapper.data("ocw-voucher-record-names", recordNames);
    dialog.$wrapper.data("ocw-voucher-record-count", recordNames.length);
    dialog.$wrapper.find("[data-action='delete-voucher-records']").prop("disabled", false);
    $records.removeClass("loading ready empty");
    if (!items.length) {
      $records.addClass("empty").text(ocTranslate("暂无保存记录"));
      return;
    }
    const fallbackTip = result.fallback_recent ? `<div class="ocw-voucher-record-tip">${ocTranslate("当前批次暂无凭证记录，以下为最近保存记录。")}</div>` : "";
    $records.addClass("ready").html(`
      ${fallbackTip}
      ${items.map((row) => this.renderTaxCertificateRecord(row)).join("")}
    `);
  }

  renderTaxCertificateRecord(row) {
    const batch = row.batch || {};
    const resolution = row.manual_resolution || {};
    const statusClass = this.voucherValidationPreviewClass(row.reconciliation_status || row.validation_status || "review");
    const batchLabel = batch.customs_no || batch.waybill_no || batch.batch_no || batch.name || "--";
    const itemCount = `${row.item_count || 0} / ${row.declared_item_count ?? "--"}`;
    const resolutionLabel = resolution.status_label || row.manual_resolution_status_label || "未处理";
    return `
      <div class="ocw-voucher-record ${this.escape(statusClass)}">
        <div class="ocw-voucher-record-main">
          <strong>${this.escape(row.source_doc_no || row.customs_no || "--")}</strong>
          <span>${this.escape(batchLabel)} · ${this.escape(row.file_name || "--")}</span>
        </div>
        <div class="ocw-voucher-record-grid">
          <div><span>${ocTranslate("状态")}</span><b>${this.escape(ocTranslate(row.reconciliation_status_label || row.validation_status_label || row.parse_status || "--"))}</b></div>
          <div><span>${ocTranslate("凭证税费 MXN")}</span><b>${this.escape(this.formatMoney(row.paid_total_mxn) || "--")}</b></div>
          <div><span>${ocTranslate("系统税费 MXN")}</span><b>${this.escape(this.formatMoney(row.system_tax_total_mxn) || "--")}</b></div>
          <div><span>${ocTranslate("差额 MXN")}</span><b>${this.escape(this.formatMoney(row.tax_total_diff_mxn) || "--")}</b></div>
          <div><span>${ocTranslate("差额方向")}</span><b>${this.escape(ocTranslate(row.direction_label || "--"))}</b></div>
          <div><span>${ocTranslate("人工处理")}</span><b>${this.escape(ocTranslate(resolutionLabel))}</b></div>
          <div><span>${ocTranslate("行数")}</span><b>${this.escape(itemCount)}</b></div>
          <div><span>${ocTranslate("保存时间")}</span><b>${this.escape(this.formatValue(row.modified || row.creation || "--"))}</b></div>
          <div class="ocw-voucher-record-action">
            <button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="open-voucher-record" data-record-name="${this.escape(row.name)}">${ocTranslate("查看记录")}</button>
            <button class="ocw-primary-btn ocw-mini-btn" type="button" data-action="ai-review-voucher" data-attachment-name="${this.escape(row.name)}" data-batch-name="${this.escape(batch.name || row.batch_name || "")}" data-version-name="${this.escape(row.version || "")}">${ocTranslate("AI 解析并分摊到 SKU")}</button>
          </div>
        </div>
      </div>
    `;
  }

  async deleteTaxCertificateRecords(dialog) {
    const batchName = dialog.$wrapper.data("ocw-voucher-batch-name") || "";
    const recordNames = dialog.$wrapper.data("ocw-voucher-record-names") || [];
    const targetNames = Array.isArray(recordNames) ? recordNames.filter(Boolean) : [];
    const recordCount = targetNames.length;
    if (!recordCount) {
      frappe.msgprint(ocTranslate("当前列表没有可删除的对比记录。"));
      return;
    }
    frappe.confirm(
      ocTranslate("确认删除当前列表显示的 {0} 条完税凭证对比记录？删除后只移除对比记录，不会删除批次和物料明细。", [recordCount]),
      async () => {
        const result = await this.call(
          "overseas_costing.api.import_api.delete_tax_certificate_parse_records",
          { record_names_json: JSON.stringify(targetNames) },
          true
        );
        if (!result || !result.ok) {
          frappe.msgprint((result && result.message) || "删除对比记录失败。");
          return;
        }
        frappe.show_alert({ message: result.message || ocTranslate("对比记录已删除。"), indicator: "green" });
        await this.loadTaxCertificateRecords(dialog);
        await this.refreshBatch(batchName).catch((error) => this.showError(error));
      }
    );
  }

  async openTaxCertificateRecordDialog(recordName) {
    const result = await this.call(
      "overseas_costing.api.import_api.get_tax_certificate_parse_record",
      { record_name: recordName },
      true
    );
    if (!result || !result.ok) {
      frappe.msgprint((result && result.message) || "未能读取完税凭证对比记录。");
      return;
    }

    const detailDialog = new frappe.ui.Dialog({
      title: ocTranslate("完税凭证对比记录"),
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "voucher_record_detail",
          options: this.renderTaxCertificateRecordDetail(result),
        },
      ],
      primary_action_label: ocTranslate("关闭"),
      primary_action: () => detailDialog.hide(),
    });
    detailDialog.show();
    detailDialog.$wrapper.addClass("ocw-voucher-modal ocw-voucher-record-modal");
    detailDialog.$wrapper.on("click", "[data-action='resolve-voucher-record']", (event) => {
      event.preventDefault();
      this.submitVoucherManualResolution(detailDialog, recordName).catch((error) => this.showError(error));
    });
    detailDialog.$wrapper.on("change", "[data-field='resolution_confirm']", (event) => {
      const checked = $(event.currentTarget).is(":checked");
      detailDialog.$wrapper.find("[data-action='resolve-voucher-record']").prop("disabled", !checked);
    });
    detailDialog.$wrapper.on("click", "[data-action='ai-review-voucher']", (event) => {
      event.preventDefault();
      const $button = $(event.currentTarget);
      return this.startVoucherFeeEvidenceReview($button, {
        fallbackAttachment: String(recordName || ""),
      }).then((started) => {
        if (started) detailDialog.hide();
        return started;
      });
    });
  }

  renderTaxCertificateRecordDetail(result) {
    const record = result.record_summary || {};
    const parseResult = result.parse_result || {};
    const mappedResult = result.mapped_result || {};
    const header = parseResult.header || {};
    const summary = parseResult.summary || {};
    const taxes = parseResult.tax_totals || {};
    const items = parseResult.line_items || [];
    const validation = parseResult.validation || {};
    const validationStatus = validation.status || record.validation_status || "review";
    const validationText = validation.status_label || record.validation_status_label || "--";
    const batch = record.batch || mappedResult.batch || {};
    const batchLabel = batch.customs_no || batch.waybill_no || batch.batch_no || batch.container_no || batch.name || "--";
    const itemCount = `${summary.item_count || items.length || record.item_count || 0} / ${summary.declared_item_count ?? record.declared_item_count ?? "--"}`;
    const rows = items.map((row) => this.renderVoucherItemRow(row)).join("");
    const fxSync = parseResult.fx_sync || {};
    const rawPaymentDate = header.payment_date || record.payment_date || "";
    const paymentDateText = this.formatVoucherPaymentDate(rawPaymentDate, fxSync.normalized_payment_date || fxSync.payment_date || "");
    const systemFxInfo = this.formatSystemPaymentFx(fxSync, rawPaymentDate);
    const batchName = String(batch.name || record.batch_name || this.detailState.batchName || "");
    const versionName = String(record.version || (batchName === this.detailState.batchName ? this.detailState.versionName : "") || "");

    return `
      <div class="ocw-voucher-record-detail">
        <div class="ocw-voucher-validation-head ${this.escape(validationStatus)}">
          <div>
            <span>${ocTranslate("解析校验")}</span>
            <strong>${this.escape(ocTranslate(validationText))}</strong>
          </div>
          <p>${ocTranslate("这是已保存的解析快照，只用于复看和复核，不会重新解析文件或写入成本字段。")}</p>
        </div>
        <div class="ocw-voucher-summary">
          <div><span>${ocTranslate("报关单号")}</span><strong>${this.escape(record.customs_no || header.pedimento_no || "--")}</strong></div>
          <div><span>${ocTranslate("凭证参考号")}</span><strong>${this.escape(header.pedimento_ref || header.pedimento_short_no || record.pedimento_ref || "--")}</strong></div>
          <div><span>${ocTranslate("柜号")}</span><strong>${this.escape(record.container_no || header.container_no || "--")}</strong></div>
          <div><span>${ocTranslate("匹配批次")}</span><strong>${this.escape(batchLabel)}</strong></div>
          <div><span>${ocTranslate("文件名")}</span><strong title="${this.escape(record.file_name || parseResult.source_name || "")}">${this.escape(record.file_name || parseResult.source_name || "--")}</strong></div>
          <div><span>${ocTranslate("支付日期")}</span><strong title="${this.escape(rawPaymentDate)}">${this.escape(paymentDateText)}</strong></div>
          <div><span>${ocTranslate("凭证原始汇率")}</span><strong>${this.escape(this.formatValidationValue(header.exchange_rate))}</strong></div>
          <div class="ocw-voucher-summary-wide">
            <span>${this.escape(systemFxInfo.heading || ocTranslate("系统采用汇率"))}</span>
            <div class="ocw-system-fx-lines" title="${this.escape(ocTranslate(systemFxInfo.title))}">
              ${systemFxInfo.lines.map((line) => `<strong>${this.escape(line)}</strong>`).join("")}
            </div>
          </div>
          <div><span>${ocTranslate("毛重 KG")}</span><strong>${this.escape(this.formatValidationValue(header.gross_weight_kg))}</strong></div>
          <div><span>${ocTranslate("支付总额 MXN")}</span><strong>${this.escape(this.formatMoney(summary.paid_total_mxn ?? record.paid_total_mxn) || "--")}</strong></div>
          <div><span>${ocTranslate("税费合计 MXN")}</span><strong>${this.escape(this.formatMoney(summary.tax_total_sum_mxn ?? record.tax_total_sum_mxn) || "--")}</strong></div>
          <div><span>${ocTranslate("保存时间")}</span><strong>${this.escape(this.formatValue(record.modified || record.creation || "--"))}</strong></div>
          <div><span>${ocTranslate("商品分项")}</span><strong>${this.escape(itemCount)}</strong></div>
        </div>
        <div class="ocw-voucher-tax-chips">${this.renderVoucherTaxChips(taxes)}</div>
        <div class="ocw-voucher-record-ai-action"><button class="ocw-primary-btn" type="button" data-action="ai-review-voucher" data-attachment-name="${this.escape(record.name || result.record_name || "")}" data-batch-name="${this.escape(batchName)}" data-version-name="${this.escape(versionName)}">${ocTranslate("AI 解析并分摊到 SKU")}</button><span>${ocTranslate("生成费用、税种和 SKU 对应关系草稿，确认前不会写入成本。")}</span></div>
        ${this.renderVoucherReconciliation(mappedResult, { showSaveButton: false })}
        ${this.renderVoucherManualResolution(record, mappedResult)}
        ${this.renderVoucherValidation(validation)}
        <div class="ocw-voucher-table-wrap">
          <table class="ocw-voucher-table">
            <thead>
              <tr>
                <th>${ocTranslate("序号")}</th>
                <th>${ocTranslate("HS 编码")}</th>
                <th>${ocTranslate("海关进口名称")}</th>
                <th>${ocTranslate("数量")}</th>
                <th>${ocTranslate("IGI 税率")}</th>
                <th>${ocTranslate("IGI 税额")}</th>
                <th>${ocTranslate("IVA 税率")}</th>
                <th>${ocTranslate("IVA 税额")}</th>
              </tr>
            </thead>
            <tbody>${rows || `<tr><td colspan="8">${ocTranslate("未识别到商品分项")}</td></tr>`}</tbody>
          </table>
        </div>
        <div class="ocw-voucher-more">${ocTranslate("共 {0} 条分项。", [this.escape(String(items.length || 0))])}</div>
      </div>
    `;
  }

  async openVoucherFeeEvidenceReview({ batchName = "", versionName = "", attachment = "" } = {}) {
    const targetBatch = String(batchName || this.detailState.batchName || "");
    if (!targetBatch || !attachment) throw new Error(ocTranslate("未找到完税凭证对应的批次或附件记录。"));
    if (String(this.detailState.batchName || "") !== targetBatch) {
      await this.openBatchDetail(targetBatch, "vouchers");
    }
    const batch = this.getDetailBatch() || this.getSelectableBatch(targetBatch, this.getSelectableBatches()) || {};
    const targetVersion = String(versionName || this.detailState.versionName || batch.current_version || "");
    if (!targetVersion) throw new Error(ocTranslate("当前批次没有可编辑的成本版本。"));
    return Boolean(await this.openFeeEvidenceReviewDialog("import_tax", attachment, {
      batchName: targetBatch,
      versionName: targetVersion,
      evidenceRole: "tax_certificate",
      force: false,
    }));
  }

  async startVoucherFeeEvidenceReview($button, { fallbackBatchName = "", fallbackAttachment = "" } = {}) {
    if (!$button || $button.prop("disabled")) return false;
    const originalLabel = String($button.text() || "AI 解析并分摊到 SKU");
    const payload = {
      batchName: String($button.attr("data-batch-name") || fallbackBatchName || ""),
      versionName: String($button.attr("data-version-name") || ""),
      attachment: String($button.attr("data-attachment-name") || fallbackAttachment || ""),
    };
    $button.prop("disabled", true).text(ocTranslate("正在启动 AI…"));
    try {
      return (await this.openVoucherFeeEvidenceReview(payload)) === true;
    } catch (error) {
      this.showError(error);
      return false;
    } finally {
      $button.prop("disabled", false).text(originalLabel);
    }
  }

  renderVoucherManualResolution(record, mappedResult = {}) {
    const resolution = mappedResult.manual_resolution || record.manual_resolution || {};
    const voucher = mappedResult.voucher || {};
    const system = mappedResult.system || {};
    const diff = mappedResult.difference || {};
    const hasResolution = Boolean(resolution.action);
    const selected = resolution.action || "accept_difference";
    const adjustedValue = resolution.final_source === "manual_adjust" ? resolution.final_tax_total_mxn : "";
    const rawHistory = Array.isArray(mappedResult.manual_resolution_history) ? mappedResult.manual_resolution_history : [];
    const history = rawHistory.slice();
    if (hasResolution) {
      const currentKey = `${resolution.resolved_at || ""}|${resolution.action || ""}|${resolution.final_tax_total_mxn ?? ""}`;
      const hasCurrent = history.some((item) => {
        const row = item || {};
        return `${row.resolved_at || ""}|${row.action || ""}|${row.final_tax_total_mxn ?? ""}` === currentKey;
      });
      if (!hasCurrent) {
        history.push(resolution);
      }
    }
    const options = [
      ["accept_difference", "确认差异可接受"],
      ["mark_exception", "备注异常"],
      ["use_voucher", "按凭证金额为准"],
      ["keep_system", "保留系统金额"],
      ["manual_adjust", "手工调整金额"],
    ]
      .map(([value, label]) => `<option value="${this.escape(value)}" ${selected === value ? "selected" : ""}>${this.escape(ocTranslate(label))}</option>`)
      .join("");
    const savedHtml = hasResolution
      ? `
        <div class="ocw-voucher-resolution-saved">
          <div><span>${ocTranslate("当前处理")}</span><strong>${this.escape(ocTranslate(resolution.status_label || resolution.action_label || "--"))}</strong></div>
          <div><span>${ocTranslate("采用金额 MXN")}</span><strong>${this.escape(this.formatMoney(resolution.final_tax_total_mxn) || "--")}</strong></div>
          <div><span>${ocTranslate("采用依据")}</span><strong>${this.escape(resolution.final_source_label || "--")}</strong></div>
          <div><span>${ocTranslate("处理人")}</span><strong>${this.escape(resolution.resolved_by || "--")}</strong></div>
          <div><span>${ocTranslate("处理时间")}</span><strong>${this.escape(resolution.resolved_at || "--")}</strong></div>
          <p>${this.escape(resolution.message || resolution.remark || "")}</p>
        </div>
      `
      : `<div class="ocw-voucher-resolution-empty">${ocTranslate("当前差异尚未人工处理。")}</div>`;
    const historyRows = history
      .slice()
      .reverse()
      .slice(0, 8)
      .map((item) => {
        const row = item || {};
        return `
          <div>
            <strong>${this.escape(ocTranslate(row.action_label || row.status_label || "--"))}</strong>
            <em>${this.escape(row.resolved_at || "--")} / ${this.escape(row.resolved_by || "--")}</em>
            <small>${this.escape(row.message || row.remark || ocTranslate("未填写备注"))}</small>
          </div>
        `;
      })
      .join("");
    const historyHtml = `
      <div class="ocw-voucher-resolution-history">
        <span>${ocTranslate("处理记录")}</span>
        ${historyRows || `<p>${ocTranslate("暂无处理记录，提交后会显示在这里。")}</p>`}
      </div>
    `;
    return `
      <div class="ocw-voucher-resolution">
        <div class="ocw-voucher-resolution-head">
          <div>
            <span>${ocTranslate("人工处理差异")}</span>
            <strong>${this.escape(hasResolution ? ocTranslate("已处理，可重新调整") : ocTranslate("待处理"))}</strong>
          </div>
          <p>${ocTranslate("可选择凭证金额、系统金额或手工调整金额为准；这里只保存处理记录，不会自动改成本字段。")}</p>
        </div>
        <div class="ocw-voucher-resolution-grid">
          <div><span>${ocTranslate("凭证税费 MXN")}</span><strong>${this.escape(this.formatMoney(voucher.paid_total_mxn) || "--")}</strong></div>
          <div><span>${ocTranslate("系统税费 MXN")}</span><strong>${this.escape(this.formatMoney(system.system_import_tax_total_mxn) || "--")}</strong></div>
          <div><span>${ocTranslate("原差额 MXN")}</span><strong>${this.escape(this.formatMoney(diff.tax_total_diff_mxn) || "--")}</strong></div>
          <div><span>${ocTranslate("方向")}</span><strong>${this.escape(ocTranslate(diff.direction_label || "--"))}</strong></div>
        </div>
        <div class="ocw-voucher-resolution-form">
          <label>
            <span>${ocTranslate("处理方式")}</span>
            <select data-field="resolution_action">${options}</select>
          </label>
          <label>
            <span>${ocTranslate("调整后税费 MXN")}</span>
            <input type="number" step="0.01" data-field="adjusted_tax_total_mxn" value="${this.escape(adjustedValue ?? "")}" placeholder="${ocTranslate('选择手工调整时填写')}" />
          </label>
          <label class="ocw-voucher-resolution-remark">
            <span>${ocTranslate("处理备注")}</span>
            <input type="text" data-field="resolution_remark" value="${this.escape(resolution.remark || "")}" placeholder="${ocTranslate('例如：差额 303 为尾差，财务确认可接受')}" />
          </label>
          <label class="ocw-voucher-resolution-confirm">
            <input type="checkbox" data-field="resolution_confirm" />
            <span>${ocTranslate("我确认按当前处理方式保存本次差异处理记录")}</span>
          </label>
          <div class="ocw-voucher-resolution-actions">
            <button class="ocw-primary-btn ocw-mini-btn" type="button" data-action="resolve-voucher-record" disabled>${ocTranslate("提交处理结果")}</button>
          </div>
        </div>
        ${savedHtml}
        ${historyHtml}
      </div>
    `;
  }

  async submitVoucherManualResolution(detailDialog, recordName) {
    const $wrapper = detailDialog.$wrapper;
    const action = $wrapper.find("[data-field='resolution_action']").val();
    const adjusted = String($wrapper.find("[data-field='adjusted_tax_total_mxn']").val() || "").trim();
    const remark = String($wrapper.find("[data-field='resolution_remark']").val() || "").trim();
    const confirmed = $wrapper.find("[data-field='resolution_confirm']").is(":checked");
    if (!confirmed) {
      frappe.msgprint(ocTranslate("请先勾选确认后再提交处理结果。"));
      return;
    }
    const doSave = async () => {
      const result = await this.call(
        "overseas_costing.api.import_api.resolve_tax_certificate_reconciliation",
        {
          record_name: recordName,
          resolution_action: action,
          adjusted_tax_total_mxn: adjusted,
          remark,
        },
        true
      );
      if (!result || !result.ok) {
        frappe.msgprint((result && result.message) || "保存人工处理结果失败。");
        return;
      }
      const $detail = $wrapper.find(".ocw-voucher-record-detail");
      $detail.replaceWith(this.renderTaxCertificateRecordDetail(result));
      if (this.activeVoucherParseDialog && this.activeVoucherParseDialog.$wrapper && this.activeVoucherParseDialog.$wrapper.is(":visible")) {
        this.loadTaxCertificateRecords(this.activeVoucherParseDialog).catch((error) => this.showError(error));
      }
      frappe.show_alert({ message: ocTranslate("处理结果已保存。"), indicator: "green" });
    };
    frappe.confirm(ocTranslate("确认保存这次完税凭证差异处理结果？"), doSave);
  }

  renderVoucherPreview(dialog, result, state = "empty") {
    const $preview = dialog.$wrapper.find("[data-area='voucher-preview']");
    const $status = dialog.$wrapper.find("[data-area='voucher-preview-status']");
    $preview.removeClass("empty loading ready warn failed");
    this.updateVoucherPrimarySaveAction(dialog, false);
    if (state === "loading") {
      $status.text(ocTranslate("正在上传并解析 PDF..."));
      $preview.addClass("loading").text(ocTranslate("解析中"));
      return;
    }
    if (!result) {
      $status.text(ocTranslate("选择文件后可先对比凭证字段。"));
      $preview.addClass("empty").text(ocTranslate("尚未对比"));
      return;
    }

    const summary = result.summary || {};
    const header = result.header || {};
    const taxes = result.tax_totals || {};
    const items = result.line_items || [];
    const validation = result.validation || {};
    const validationStatus = validation.status || (summary.tax_total_matches_paid_total ? "passed" : "review");
    const statusText = validation.status_label || (summary.tax_total_matches_paid_total ? "通过" : "需复核");
    const statusClass = this.voucherValidationPreviewClass(validationStatus);
    const taxChips = this.renderVoucherTaxChips(taxes);
    const rows = items.slice(0, 30).map((row) => this.renderVoucherItemRow(row)).join("");
    const moreText = items.length > 30 ? `<div class="ocw-voucher-more">${ocTranslate("仅展示前 30 条，共 {0} 条。", [this.escape(String(items.length))])}</div>` : "";
    const declaredCount = summary.declared_item_count ?? "--";
    const validationHtml = this.renderVoucherValidation(validation);
    const reconciliation = result.reconciliation || {};
    if (result.saved_attachment_name) reconciliation.saved_attachment_name = result.saved_attachment_name;
    const reconciliationHtml = this.renderVoucherReconciliation(reconciliation, { showSaveButton: false });
    const reconciliationText = reconciliation.status_label ? ocTranslate("，对比{0}", [ocTranslate(reconciliation.status_label)]) : "";
    const canSave = Boolean(reconciliation.batch && reconciliation.batch.name);
    const paymentDateText = this.formatVoucherPaymentDate(header.payment_date || "");
    this.updateVoucherPrimarySaveAction(dialog, canSave);

    $status.text(ocTranslate("预览完成：校验{0}{1}。", [statusText, reconciliationText]));
    $preview.addClass(statusClass).html(`
      <div class="ocw-voucher-validation-head ${this.escape(validationStatus)}">
        <div>
          <span>${ocTranslate("解析校验")}</span>
          <strong>${this.escape(ocTranslate(statusText))}</strong>
        </div>
        <p>${this.escape(this.voucherValidationMessage(validationStatus))}</p>
      </div>
      <div class="ocw-voucher-summary">
        <div><span>${ocTranslate("报关单号")}</span><strong>${this.escape(header.pedimento_no || "--")}</strong></div>
        <div><span>${ocTranslate("凭证参考号")}</span><strong>${this.escape(header.pedimento_ref || header.pedimento_short_no || "--")}</strong></div>
        <div><span>${ocTranslate("柜号")}</span><strong>${this.escape(header.container_no || "--")}</strong></div>
        <div><span>${ocTranslate("支付日期")}</span><strong title="${this.escape(header.payment_date || "")}">${this.escape(paymentDateText)}</strong></div>
        <div><span>${ocTranslate("凭证原始汇率")}</span><strong>${this.escape(this.formatValue(header.exchange_rate || "--"))}</strong></div>
        <div><span>${ocTranslate("毛重 KG")}</span><strong>${this.escape(this.formatValue(header.gross_weight_kg || "--"))}</strong></div>
        <div><span>${ocTranslate("支付总额 MXN")}</span><strong>${this.escape(this.formatMoney(summary.paid_total_mxn || 0))}</strong></div>
        <div><span>${ocTranslate("商品分项")}</span><strong>${this.escape(String(summary.item_count || items.length || 0))} / ${this.escape(String(declaredCount))}</strong></div>
      </div>
      <div class="ocw-voucher-tax-chips">${taxChips}</div>
      ${reconciliationHtml}
      ${validationHtml}
      <div class="ocw-voucher-note">${ocTranslate("当前只做字段摘取预览；确认规则后再用于生成实际核算和多退少补对比。")}</div>
      <div class="ocw-voucher-table-wrap">
        <table class="ocw-voucher-table">
          <thead>
            <tr>
              <th>${ocTranslate("序号")}</th>
              <th>${ocTranslate("HS 编码")}</th>
              <th>${ocTranslate("海关进口名称")}</th>
              <th>${ocTranslate("数量")}</th>
              <th>${ocTranslate("IGI 税率")}</th>
              <th>${ocTranslate("IGI 税额")}</th>
              <th>${ocTranslate("IVA 税率")}</th>
              <th>${ocTranslate("IVA 税额")}</th>
            </tr>
          </thead>
          <tbody>${rows || `<tr><td colspan="8">${ocTranslate("未识别到商品分项")}</td></tr>`}</tbody>
        </table>
      </div>
      ${moreText}
    `);
  }

  updateVoucherPrimarySaveAction(dialog, canSave, label = "保存对比结果") {
    if (!dialog || !dialog.$wrapper) return;
    const $primary = dialog.get_primary_btn ? dialog.get_primary_btn() : dialog.$wrapper.find(".modal-footer .btn-primary");
    if (!$primary || !$primary.length) return;
    $primary.text(label).prop("disabled", !canSave).attr("title", canSave ? "保存解析快照到附件记录" : "请先解析并匹配到系统批次");
    if (canSave) {
      $primary.show();
    } else {
      $primary.hide();
    }
  }

  resetVoucherDialogAfterSave(dialog) {
    if (!dialog || !dialog.$wrapper) return;
    const $dropzone = dialog.$wrapper.find("[data-voucher-dropzone='1']");
    const $input = dialog.$wrapper.find(".ocw-voucher-file-input");
    dialog.$wrapper.removeData("ocw-voucher-file");
    dialog.$wrapper.removeData("ocw-voucher-upload");
    dialog.$wrapper.removeData("ocw-voucher-preview");
    $dropzone.removeClass("has-file is-dragover");
    dialog.$wrapper.find("[data-area='voucher-file-name']").text(ocTranslate("拖放 PDF 到这里，或点击选择"));
    if ($input.length) $input.val("");
    this.renderVoucherPreview(dialog, null, "empty");
    dialog.$wrapper.find("[data-area='voucher-preview-status']").text(ocTranslate("保存完成，可继续上传下一份凭证。"));
  }

  renderVoucherTaxChips(taxes = {}) {
    const rows = [
      ["海关手续费（DTA）", taxes.dta_mxn],
      ["预验证费（PRV）", taxes.prv_mxn],
      ["预验证增值税（PRV IVA）", taxes.prv_iva_mxn],
      ["进口关税（IGI/IGE）", taxes.igi_mxn],
      ["进口增值税（IVA）", taxes.iva_mxn],
    ];
    return `
      <div class="ocw-voucher-tax-title">${ocTranslate("税费构成 MXN")}</div>
      <div class="ocw-voucher-tax-grid">
        ${rows
          .map(
            ([label, value]) => `
              <div class="ocw-voucher-tax-item">
                <span>${this.escape(ocTranslate(label))}</span>
                <strong>${this.escape(this.formatMoney(value || 0))}</strong>
              </div>
            `
          )
          .join("")}
      </div>
    `;
  }

  renderVoucherReconciliation(reconciliation, options = {}) {
    if (!reconciliation || !Object.keys(reconciliation).length) return "";
    const showSaveButton = options.showSaveButton !== false;
    const batch = reconciliation.batch || {};
    const voucher = reconciliation.voucher || {};
    const system = reconciliation.system || {};
    const diff = reconciliation.difference || {};
    const checks = reconciliation.checks || [];
    const status = reconciliation.status || "pending";
    const batchLabel = batch.customs_no || batch.waybill_no || batch.batch_no || batch.container_no || batch.name || "未匹配";
    const declaredCount = voucher.declared_item_count ?? voucher.item_count ?? "--";
    const checkRows = checks.map((check) => this.renderVoucherValidationRow(check)).join("");
    const saveButton = !showSaveButton
      ? ""
      : batch.name
        ? `<button class="ocw-primary-btn ocw-mini-btn" type="button" data-action="save-voucher-parse">${reconciliation.saved_attachment_name ? ocTranslate("已保存对比结果") : ocTranslate("保存对比结果")}</button>`
        : `<button class="ocw-primary-btn ocw-mini-btn" type="button" data-action="save-voucher-parse" disabled>${ocTranslate("保存对比结果")}</button>`;

    return `
      <div class="ocw-voucher-reconciliation ${this.escape(status)}">
        <div class="ocw-voucher-reconciliation-head">
          <div>
            <span>${ocTranslate("多退少补对比预览")}</span>
            <strong>${this.escape(ocTranslate(reconciliation.status_label || "--"))}</strong>
          </div>
          <div class="ocw-voucher-reconciliation-action">
            <p>${this.escape(reconciliation.message || ocTranslate("对比结果仅用于复核，不会自动写入成本。"))}</p>
            ${saveButton}
          </div>
        </div>
        <div class="ocw-voucher-reconciliation-grid">
          <div><span>${ocTranslate("匹配批次")}</span><strong>${this.escape(batchLabel)}</strong></div>
          <div><span>${ocTranslate("凭证税费 MXN")}</span><strong>${this.escape(this.formatMoney(voucher.paid_total_mxn) || "--")}</strong></div>
          <div><span>${ocTranslate("系统税费 MXN")}</span><strong>${this.escape(this.formatMoney(system.system_import_tax_total_mxn) || "--")}</strong></div>
          <div><span>${ocTranslate("差额 MXN")}</span><strong>${this.escape(this.formatMoney(diff.tax_total_diff_mxn) || "--")}</strong></div>
          <div><span>${ocTranslate("差额方向")}</span><strong>${this.escape(ocTranslate(diff.direction_label || "--"))}</strong></div>
          <div><span>${ocTranslate("行数对比")}</span><strong>${this.escape(String(system.item_count || 0))} / ${this.escape(String(declaredCount))}</strong></div>
          <div><span>${ocTranslate("税费来源")}</span><strong>${this.escape(system.tax_source || "--")}</strong></div>
          <div><span>${ocTranslate("有税费行")}</span><strong>${this.escape(String(system.rows_with_tax_count || 0))}</strong></div>
        </div>
        ${
          checkRows
            ? `<table class="ocw-voucher-validation-table ocw-voucher-reconciliation-table">
                <thead>
                  <tr>
                    <th>${ocTranslate("项目")}</th>
                    <th>${ocTranslate("状态")}</th>
                    <th>${ocTranslate("说明")}</th>
                    <th>${ocTranslate("期望值")}</th>
                    <th>${ocTranslate("识别值")}</th>
                  </tr>
                </thead>
                <tbody>${checkRows}</tbody>
              </table>`
            : ""
        }
      </div>
    `;
  }

  renderVoucherValidation(validation) {
    const checks = (validation && validation.checks) || [];
    if (!checks.length) return "";
    const rows = checks.map((check) => this.renderVoucherValidationRow(check)).join("");
    return `
      <div class="ocw-voucher-validation-wrap">
        <div class="ocw-voucher-validation-title">
          <strong>${ocTranslate("校验明细")}</strong>
          <span>${ocTranslate("通过 {0} · 需复核 {1} · 失败 {2}", [this.escape(String(validation.passed_count || 0)), this.escape(String(validation.review_count || 0)), this.escape(String(validation.failed_count || 0))])}</span>
        </div>
        <table class="ocw-voucher-validation-table">
          <thead>
            <tr>
              <th>${ocTranslate("项目")}</th>
              <th>${ocTranslate("状态")}</th>
              <th>${ocTranslate("说明")}</th>
              <th>${ocTranslate("期望值")}</th>
              <th>${ocTranslate("识别值")}</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  renderVoucherValidationRow(check) {
    const status = check.status || "review";
    return `
      <tr class="${this.escape(status)}">
        <td>${this.escape(ocTranslate(check.label || "--"))}</td>
        <td><span class="ocw-voucher-check-status ${this.escape(status)}">${this.escape(ocTranslate(check.status_label || "--"))}</span></td>
        <td>${this.escape(ocTranslate(check.message || "--"))}</td>
        <td>${this.escape(this.formatValidationValue(check.expected, check.code))}</td>
        <td>${this.escape(this.formatValidationValue(check.actual, check.code))}</td>
      </tr>
    `;
  }

  voucherValidationPreviewClass(status) {
    if (["failed", "unmatched", "exception"].includes(status)) return "failed";
    if (["review", "pending"].includes(status)) return "warn";
    return "ready";
  }

  voucherValidationMessage(status) {
    if (status === "failed") return "关键字段或金额不一致，不能直接用于最终核算，需要人工复核。";
    if (status === "review") return "基础解析已完成，但仍有字段需要人工确认。";
    return "基础字段、金额合计和分项数量已通过当前规则校验。";
  }

  formatValidationValue(value, fieldname = "") {
    if (value === null || value === undefined || value === "") return "--";
    if (typeof value === "number") {
      return OverseasCostWorkbenchState.formatVoucherValidationNumber(value, fieldname);
    }
    if (Array.isArray(value)) return value.join("、");
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  normalizeVoucherPaymentDate(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    const dateOnly = text.split(/\s+/)[0].replace(/[年月.]/g, "-").replace(/日/g, "");
    let match = dateOnly.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$/);
    if (match) {
      const [, year, month, day] = match;
      return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    }
    match = dateOnly.match(/^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$/);
    if (match) {
      const [, day, month, year] = match;
      return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    }
    return text;
  }

  formatVoucherPaymentDate(rawDate, preferredDate = "") {
    const raw = String(rawDate || "").trim();
    const normalized = this.normalizeVoucherPaymentDate(preferredDate || raw);
    if (!normalized && !raw) return "--";
    if (!raw || raw === normalized) return normalized || raw;
    return ocTranslate("{0}（原始：{1}）", [normalized || raw, raw]);
  }

  formatSystemPaymentFx(fxSync = {}, rawPaymentDate = "") {
    const hasFx = fxSync && (fxSync.action === "updated" || fxSync.action === "unchanged" || fxSync.usd_to_rmb || fxSync.rmb_to_mxn);
    if (!hasFx) {
      const message = fxSync.reason || fxSync.message || "未保存系统汇率；保存后优先按真实付款日查询，缺失时按付款审批完成日暂估。";
      return { heading: "系统采用汇率", title: message, lines: [message] };
    }

    const rateDate = this.normalizeVoucherPaymentDate(
      fxSync.normalized_fx_rate_date || fxSync.fx_rate_date || fxSync.normalized_payment_date || fxSync.payment_date || rawPaymentDate || ""
    );
    const sourceLabel = fxSync.fx_date_source_label || (fxSync.is_estimated_rate ? "付款审批完成日（暂估）" : "真实付款日");
    const heading = fxSync.is_estimated_rate ? "系统采用汇率（暂估）" : "系统采用汇率";
    const usdToRmb = this.numericOrNull(fxSync.usd_to_rmb);
    const rmbToMxn = this.numericOrNull(fxSync.rmb_to_mxn);
    const snapshotMxnRate = fxSync.rate_snapshots && fxSync.rate_snapshots.MXN
      ? this.numericOrNull(fxSync.rate_snapshots.MXN.cny_per_unit)
      : null;
    const mxnToRmb = this.numericOrNull(fxSync.fx_mxn_to_rmb) || snapshotMxnRate || (rmbToMxn ? 1 / rmbToMxn : null);
    const isFallbackRate = Boolean(fxSync.fallback_rate_source);
    const parts = [`${sourceLabel}：${rateDate || "未识别"}`];
    if (isFallbackRate) {
      parts.push(fxSync.fallback_message || "汇率库缺少付款日汇率，当前成本暂用版本汇率");
      parts.push(ocTranslate("汇率来源：{0}", [fxSync.fallback_rate_source_label || "当前版本汇率（暂用）"]));
    } else if (fxSync.is_estimated_rate) {
      parts.push("后续拿到真实付款日后需重算确认");
    } else if (!fxSync.normalized_fx_rate_date && !fxSync.normalized_payment_date && !fxSync.rate_snapshots) {
      parts.push("历史保存汇率");
    }
    parts.push(`1 USD = ${usdToRmb ? this.formatNumber(usdToRmb) : "--"} RMB`);
    if (mxnToRmb && rmbToMxn) {
      parts.push(ocTranslate("1 MXN = {0} RMB（按 1 RMB = {1} MXN 换算）", [this.formatNumber(mxnToRmb), this.formatNumber(rmbToMxn)]));
    } else {
      parts.push(`1 MXN = ${mxnToRmb ? this.formatNumber(mxnToRmb) : "--"} RMB`);
    }
    return { heading, title: parts.join("；"), lines: parts };
  }

  numericOrNull(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(String(value).replace(/,/g, ""));
    return Number.isFinite(number) ? number : null;
  }

  renderVoucherItemRow(row) {
    const taxes = row.taxes || {};
    return `
      <tr>
        <td>${this.escape(row.row_no || "--")}</td>
        <td>${this.escape(row.hs_code || "--")}</td>
        <td title="${this.escape(row.import_name || "")}">${this.escape(row.import_name || "--")}</td>
        <td>${this.escape(this.formatValue(row.quantity_umc || "--"))}</td>
        <td>${this.escape(this.formatValue(taxes.igi_rate ?? "--"))}</td>
        <td>${this.escape(this.formatMoney(taxes.igi_amount_mxn || 0))}</td>
        <td>${this.escape(this.formatValue(taxes.iva_rate ?? "--"))}</td>
        <td>${this.escape(this.formatMoney(taxes.iva_amount_mxn || 0))}</td>
      </tr>
    `;
  }
