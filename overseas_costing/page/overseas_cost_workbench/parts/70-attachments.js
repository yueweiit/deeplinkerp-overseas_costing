  async loadOaFormAttachments(batch, dialog) {
    const result = await this.call(
      "overseas_costing.api.import_api.list_oa_form_attachments",
      {
        batch_name: batch.name,
        limit: 80,
      },
      true
    );
    const $target = dialog.$wrapper.find("[data-area='oa-attachment-list']");
    if (!result || !result.ok) {
      $target.html(`
        <div class="ocw-purchase-empty">
          <strong>${ocTranslate("暂时无法读取钉钉审批附件")}</strong>
          <span>${this.escape((result && result.message) || ocTranslate("当前批次没有可读取的钉钉审批附件。"))}</span>
        </div>
      `);
      return;
    }
    $target.removeClass("ocw-purchase-loading").html(this.renderOaAttachmentList(result.items || []));
  }

  async downloadOaFormAttachment(batch, dialog, attachmentName = "", $button = null, openParseAfterDownload = false) {
    if (!attachmentName) {
      this.showPendingFeature("缺少附件记录，无法下载。");
      return;
    }
    if ($button && $button.length) {
      $button.prop("disabled", true).text(ocTranslate("下载中"));
    }
    const result = await this.call(
      "overseas_costing.api.import_api.download_oa_form_attachment",
      {
        attachment_name: attachmentName,
      },
      true
    );
    if (!result || !result.ok) {
      if ($button && $button.length) {
        $button.prop("disabled", false).text(result && result.error_type ? ocTranslate("重试下载") : ocTranslate("下载到本地"));
      }
      this.showPendingFeature((result && result.message) || "钉钉附件下载失败。");
      if (batch && dialog) {
        await this.loadOaFormAttachments(batch, dialog);
      }
      return;
    }
    if (result.file_url) {
      this.downloadFileToLocal(result.file_url, result.file_name || "");
    }
    frappe.show_alert({
      message: ocTranslate("附件已开始下载到本地"),
      indicator: "green",
    });
    if (batch && dialog) await this.loadOaFormAttachments(batch, dialog);
    if (openParseAfterDownload && result.file_url) {
      this.openPackingListPreviewDialog(batch.name, result.attachment_name || attachmentName, result.file_url);
    }
  }

  downloadFileToLocal(fileUrl, fileName = "") {
    if (!fileUrl) return;
    const link = document.createElement("a");
    link.href = fileUrl;
    if (fileName) link.download = fileName;
    link.target = "_self";
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    window.setTimeout(() => link.remove(), 0);
  }

  async openOaAttachmentFilePreview(batch, parentDialog, attachmentName = "", fileUrl = "", fileName = "", $button = null) {
    let previewUrl = fileUrl || "";
    let previewName = fileName || "";
    if (!previewUrl && attachmentName) {
      if ($button && $button.length) {
        $button.prop("disabled", true).text(ocTranslate("准备预览"));
      }
      const result = await this.call(
        "overseas_costing.api.import_api.download_oa_form_attachment",
        {
          attachment_name: attachmentName,
        },
        true
      );
      if (!result || !result.ok) {
        if ($button && $button.length) {
          $button.prop("disabled", false).text(result && result.error_type ? ocTranslate("重试预览") : ocTranslate("附件预览"));
        }
        this.showPendingFeature((result && result.message) || "钉钉附件下载失败，暂时无法预览。");
        if (batch && parentDialog) {
          await this.loadOaFormAttachments(batch, parentDialog);
        }
        return;
      }
      previewUrl = result.file_url || "";
      previewName = result.file_name || previewName;
      frappe.show_alert({
        message: ocTranslate("附件已保存，可预览"),
        indicator: "green",
      });
      if (batch && parentDialog) {
        await this.loadOaFormAttachments(batch, parentDialog);
      }
    }
    if (!previewUrl) {
      this.showPendingFeature("当前附件还没有可预览的文件。");
      return;
    }
    this.openOaAttachmentFilePreviewDialog(previewUrl, previewName || attachmentName || "附件");
  }

  openOaAttachmentFilePreviewDialog(fileUrl = "", fileName = "") {
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("附件预览"),
      size: "large",
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "oa_attachment_file_preview",
          options: this.renderOaAttachmentFilePreview(fileUrl, fileName),
        },
      ],
      primary_action_label: ocTranslate("关闭"),
      primary_action: () => dialog.hide(),
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-purchase-modal ocw-attachment-file-preview-modal");
  }

  renderOaAttachmentFilePreview(fileUrl = "", fileName = "") {
    const fileRef = fileName || fileUrl;
    const downloadLink = `
      <a class="ocw-link-btn" href="${this.escape(fileUrl)}" download="${this.escape(fileName || "")}" target="_self">${ocTranslate("下载到本地")}</a>
    `;
    let previewBody = "";
    if (this.isImageFileRef(fileRef)) {
      previewBody = `<img class="ocw-attachment-file-preview-image" src="${this.escape(fileUrl)}" alt="${this.escape(fileName || ocTranslate("附件预览"))}">`;
    } else if (this.isPdfFileRef(fileRef) || this.isTextFileRef(fileRef)) {
      previewBody = `<iframe class="ocw-attachment-file-preview-frame" src="${this.escape(fileUrl)}" title="${this.escape(fileName || ocTranslate("附件预览"))}"></iframe>`;
    } else {
      previewBody = `
        <div class="ocw-purchase-empty">
          <strong>${ocTranslate("当前格式暂不支持页面内预览")}</strong>
          <span>${ocTranslate("请下载到本地查看原文件，系统仍会保留附件记录用于回溯。")}</span>
        </div>
      `;
    }
    return `
      <div class="ocw-attachment-file-preview">
        <div class="ocw-purchase-target ocw-source-document-target">
          <span>${ocTranslate("原始附件")}</span>
          <strong>${this.escape(fileName || "--")}</strong>
          <em>${ocTranslate("这里显示的是钉钉审批附件原件，供人工复核使用，不代表系统已完整解析。")}</em>
        </div>
        <div class="ocw-attachment-file-preview-actions">${downloadLink}</div>
        <div class="ocw-attachment-file-preview-body">${previewBody}</div>
      </div>
    `;
  }

  openOaSourceAttachmentPreview(attachmentName = "", batch = null, parentDialog = null) {
    if (!attachmentName) {
      this.showPendingFeature("缺少附件记录，无法查看内容。");
      return;
    }
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("附件内容识别预览"),
      size: "large",
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "source_attachment_preview",
          options: `<div class="ocw-purchase-loading" data-area="source-attachment-preview">${ocTranslate("正在识别附件内容")}</div>`,
        },
      ],
      primary_action_label: ocTranslate("关闭"),
      primary_action: () => dialog.hide(),
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-purchase-modal ocw-source-attachment-modal");
    dialog.$wrapper
      .off("click.ocwSourceAttachmentReview")
      .on("click.ocwSourceAttachmentReview", "[data-action='confirm-source-attachment-type']", (event) => {
        this.confirmOaSourceAttachmentType(
          dialog,
          attachmentName,
          batch,
          parentDialog,
          $(event.currentTarget)
        ).catch((error) => this.showError(error));
      })
      .on("click.ocwSourceAttachmentReview", "[data-action='preview-purchase-order-match']", () => {
        this.openPurchaseOrderMatchDialog(attachmentName, batch, parentDialog, dialog);
    });
    this.call("overseas_costing.api.import_api.preview_oa_source_attachment", { attachment_name: attachmentName }, true)
      .then(async (result) => {
        this.renderOaSourceAttachmentPreview(dialog, result, batch, parentDialog);
        if (result && result.ok && batch && parentDialog) {
          await this.loadOaFormAttachments(batch, parentDialog);
        }
      })
      .catch((error) => this.renderOaSourceAttachmentPreview(dialog, { ok: false, message: this.normalizeErrorMessage(error) }, batch, parentDialog));
  }

  renderOaSourceAttachmentPreview(dialog, result, batch = null, parentDialog = null) {
    const $target = dialog.$wrapper.find("[data-area='source-attachment-preview']");
    if (!result || !result.ok) {
      $target.html(`
        <div class="ocw-purchase-empty">
          <strong>${ocTranslate("附件内容暂时无法识别")}</strong>
          <span>${this.escape((result && result.message) || ocTranslate("请先确认附件已下载到系统。"))}</span>
        </div>
      `);
      return;
    }
    const classification = result.classification || {};
    const fields = result.field_candidates || {};
    const manualReview = result.manual_review || {};
    const purchaseOrder = result.purchase_order || {};
    const isPurchaseOrder = classification.code === "purchase_order" || manualReview.confirmed_type === "purchase_order";
    const selectedType = manualReview.confirmed_type || this.sourceAttachmentTypeFromClassification(classification.code);
    const typeOptions = [
      ["purchase_order", "采购订单"],
      ["purchase_price_document", "采购价格资料"],
      ["customs_declaration", "报关资料"],
      ["tax_certificate", "完税凭证"],
      ["logistics_quote", "物流报价"],
      ["other", "其他资料"],
    ]
      .map(([value, label]) => `<option value="${value}" ${value === selectedType ? "selected" : ""}>${ocTranslate(label)}</option>`)
      .join("");
    const candidateRows = [
      ["物料编码候选", (fields.material_codes || []).join("、") || "--"],
      ["海关编码候选", (fields.hs_codes || []).join("、") || "--"],
      ["币种候选", (fields.currencies || []).join("、") || "--"],
      ["单价候选", this.isPositive(fields.unit_price_candidate) ? this.formatMoney(fields.unit_price_candidate) : "--"],
      ["金额候选", this.isPositive(fields.goods_value_candidate) ? this.formatMoney(fields.goods_value_candidate) : "--"],
      ["报关单号候选", fields.pedimento_no_candidate || "--"],
      ["实缴税费候选 MXN", this.isPositive(fields.paid_total_mxn_candidate) ? this.formatMoney(fields.paid_total_mxn_candidate) : "--"],
      ["税费合计候选 MXN", this.isPositive(fields.tax_total_mxn_candidate) ? this.formatMoney(fields.tax_total_mxn_candidate) : "--"],
    ]
      .map(([label, value]) => `<tr><th>${this.escape(ocTranslate(label))}</th><td>${this.escape(String(value))}</td></tr>`)
      .join("");
    const methodLabels = {
      pdf_layout_text: "PDF 版面文本",
      pdf_text: "PDF 文字层",
      ocr_pdf: "扫描 PDF OCR",
      ocr_image: "图片 OCR",
      word_docx: "Word 文本",
      word_doc: "Word 文本",
      txt_text: "TXT 文本",
    };
    const methodLabel = methodLabels[result.extraction_method] || "文档文本";
    $target.html(`
      <div class="ocw-purchase-target ocw-source-document-target">
        <span>${ocTranslate("附件")}</span>
        <strong>${this.escape(result.source_name || "--")}</strong>
        <em>${ocTranslate("{0}；识别结果仅用于资料分类和人工核对，当前不会写入单价、货值。", [this.escape(methodLabel)])}</em>
      </div>
      <div class="ocw-purchase-apply ocw-source-document-classification">
        <div>
          <strong>${this.escape(classification.label || ocTranslate("待人工识别"))}</strong>
          <span>${this.escape(classification.reason || "")}</span>
        </div>
      </div>
      ${isPurchaseOrder ? `
        <section class="ocw-purchase-section ocw-purchase-order-entry ocw-source-purchase-order-entry">
          <div class="ocw-purchase-order-summary">
            <span>${ocTranslate("订单号")}</span><strong>${this.escape(purchaseOrder.purchase_order_no || "--")}</strong>
            <span>${ocTranslate("供应商")}</span><strong>${this.escape(purchaseOrder.supplier || "--")}</strong>
            <span>${ocTranslate("币种")}</span><strong>${this.escape(purchaseOrder.currency || "--")}</strong>
          </div>
          <button class="btn btn-primary btn-sm" type="button" data-action="preview-purchase-order-match">${ocTranslate("查看物料匹配")}</button>
        </section>
      ` : ""}
      <section class="ocw-purchase-section ocw-source-document-review">
        <h4>${ocTranslate("人工确认")}</h4>
        <div class="ocw-source-document-review-controls">
          <select class="form-control" data-field="source-attachment-type">${typeOptions}</select>
          <input class="form-control" data-field="source-attachment-remark" type="text" maxlength="240" placeholder="${ocTranslate('备注（可选）')}" value="${this.escape(manualReview.remark || "")}">
          <button class="btn btn-primary btn-sm" type="button" data-action="confirm-source-attachment-type">${ocTranslate("确认资料类型")}</button>
        </div>
        ${manualReview.status === "confirmed" ? `<div class="ocw-confirm-note">${ocTranslate("已确认：{0}{1}{2}", [this.escape(manualReview.confirmed_type_label || "--"), manualReview.confirmed_by ? `，${this.escape(manualReview.confirmed_by)}` : "", manualReview.confirmed_at ? `，${this.escape(manualReview.confirmed_at)}` : ""])}</div>` : ""}
      </section>
      <section class="ocw-purchase-section">
        <h4>${ocTranslate("字段候选")}</h4>
        <div class="ocw-purchase-table-wrap ocw-field-candidate-wrap">
          <table class="ocw-purchase-table ocw-field-candidate-table"><tbody>${candidateRows}</tbody></table>
        </div>
      </section>
      <section class="ocw-purchase-section">
        <h4>${ocTranslate("识别文本")}</h4>
        <pre class="ocw-attachment-ocr-text">${this.escape(result.text_excerpt || ocTranslate("未识别到可用文字"))}</pre>
      </section>
    `);
  }

  sourceAttachmentTypeFromClassification(code = "") {
    const supported = ["purchase_order", "purchase_price_document", "customs_declaration", "tax_certificate", "logistics_quote"];
    return supported.includes(code) ? code : "other";
  }

  async confirmOaSourceAttachmentType(dialog, attachmentName = "", batch = null, parentDialog = null, $button = null) {
    if (!attachmentName) {
      this.showPendingFeature("缺少附件记录，无法保存确认结果。");
      return;
    }
    const confirmedType = dialog.$wrapper.find("[data-field='source-attachment-type']").val();
    const remark = dialog.$wrapper.find("[data-field='source-attachment-remark']").val();
    if ($button && $button.length) {
      $button.prop("disabled", true).text(ocTranslate("保存中"));
    }
    try {
      const result = await this.call(
        "overseas_costing.api.import_api.confirm_oa_source_attachment_type",
        {
          attachment_name: attachmentName,
          confirmed_type: confirmedType,
          remark: remark || "",
        },
        true
      );
      if (!result || !result.ok) {
        this.showPendingFeature((result && result.message) || "附件类型确认失败。");
        return;
      }
      frappe.show_alert({ message: result.message || ocTranslate("附件资料类型已确认"), indicator: "green" });
      if (batch && parentDialog) {
        await this.loadOaFormAttachments(batch, parentDialog);
        await this.refreshBatch(batch.name);
      }
      dialog.hide();
    } finally {
      if ($button && $button.length && dialog.$wrapper.is(":visible")) {
        $button.prop("disabled", false).text(ocTranslate("确认资料类型"));
      }
    }
  }

  openPurchaseOrderMatchDialog(attachmentName = "", batch = null, attachmentListDialog = null, sourcePreviewDialog = null) {
    if (!attachmentName) {
      this.showPendingFeature("缺少采购订单附件，无法生成物料匹配预览。");
      return;
    }
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("采购订单匹配预览"),
      size: "large",
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "purchase_order_match_preview",
          options: `<div class="ocw-purchase-loading" data-area="purchase-order-match-preview">${ocTranslate("正在生成采购订单匹配预览")}</div>`,
        },
      ],
      primary_action_label: ocTranslate("关闭"),
      primary_action: () => dialog.hide(),
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-purchase-modal");
    dialog.$wrapper
      .off("click.ocwPurchaseOrderMatch")
      .on("click.ocwPurchaseOrderMatch", "[data-action='apply-purchase-order-match']", (event) => {
        this.applyPurchaseOrderMatch(
          dialog,
          attachmentName,
          batch,
          attachmentListDialog,
          sourcePreviewDialog,
          $(event.currentTarget)
        ).catch((error) => this.showError(error));
      });
    this.call(
      "overseas_costing.api.import_api.preview_oa_purchase_order_match",
      { attachment_name: attachmentName },
      true
    )
      .then((result) => this.renderPurchaseOrderMatchPreview(dialog, result))
      .catch((error) => this.renderPurchaseOrderMatchPreview(dialog, { ok: false, message: this.normalizeErrorMessage(error) }));
  }

  renderPurchaseOrderMatchPreview(dialog, result) {
    const $target = dialog.$wrapper.find("[data-area='purchase-order-match-preview']");
    if (!result || !result.ok) {
      $target.html(`
        <div class="ocw-purchase-empty">
          <strong>${ocTranslate("暂时无法生成采购订单匹配")}</strong>
          <span>${this.escape((result && result.message) || ocTranslate("请确认附件已下载且识别出完整价格明细。"))}</span>
        </div>
      `);
      return;
    }
    const purchaseOrder = result.purchase_order || {};
    const writeback = result.writeback_preview || {};
    const rows = this.renderPurchaseOrderMatchRows(result.source_rows || [], writeback);
    const hasFillable = Number(writeback.fillable_row_count || 0) > 0;
    $target.html(`
      <div class="ocw-purchase-target">
        <span>${ocTranslate("采购订单")}</span>
        <strong>${this.escape(purchaseOrder.purchase_order_no || "--")}</strong>
        <em>${this.escape(purchaseOrder.supplier || "--")} / ${this.escape(purchaseOrder.currency || "--")}</em>
      </div>
      <div class="ocw-purchase-order-stats">
        <span>${ocTranslate("识别 {0} 条", [this.escape(String(purchaseOrder.recognized_line_count || 0))])}</span>
        <span>${ocTranslate("匹配 {0} 条", [this.escape(String(writeback.matched_count || 0))])}</span>
        <span>${ocTranslate("可补 {0} 条", [this.escape(String(writeback.fillable_row_count || 0))])}</span>
        <span>${ocTranslate("冲突 {0} 条", [this.escape(String(writeback.conflict_row_count || 0))])}</span>
        <span>${ocTranslate("未匹配 {0} 条", [this.escape(String(writeback.unmatched_count || 0))])}</span>
      </div>
      <section class="ocw-purchase-section">
        <div class="ocw-purchase-table-wrap">
          <table class="ocw-purchase-table ocw-purchase-order-table">
            <thead><tr><th>${ocTranslate("物料编码")}</th><th>${ocTranslate("物料名称")}</th><th>${ocTranslate("数量")}</th><th>${ocTranslate("采购单价")}</th><th>${ocTranslate("币种")}</th><th>${ocTranslate("采购货值")}</th><th>${ocTranslate("匹配结果")}</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </section>
      <div class="ocw-purchase-order-footer">
        <span>${this.escape(writeback.message || result.message || "")}</span>
        ${hasFillable ? `<button class="btn btn-primary btn-sm" type="button" data-action="apply-purchase-order-match">${ocTranslate("确认补入空值")}</button>` : ""}
      </div>
    `);
  }

  renderPurchaseOrderMatchRows(sourceRows, writeback) {
    if (!sourceRows.length) {
      return `<tr><td colspan="7" class="ocw-purchase-empty-line">${ocTranslate("未识别到包含物料编码、数量、单价和货值的完整明细")}</td></tr>`;
    }
    const matchedRows = writeback.matched_rows || [];
    const unmatchedRows = writeback.unmatched_rows || [];
    return sourceRows
      .map((source) => {
        const materialCode = String(source.material_code || "");
        const matched = matchedRows.find((row) => String((row.mapped_row || {}).material_code || "") === materialCode);
        const unmatched = unmatchedRows.some((row) => String(row.material_code || "") === materialCode);
        let status = "未匹配";
        let statusClass = "ocw-match-missing";
        if (matched && matched.has_conflict) {
          status = "已有不同采购值";
          statusClass = "ocw-match-conflict";
        } else if (matched && matched.has_fillable) {
          status = "可补入空值";
          statusClass = "ocw-match-ready";
        } else if (matched && matched.all_business_same) {
          status = "系统已有一致数据";
          statusClass = "ocw-match-same";
        } else if (matched) {
          status = "无需补入";
          statusClass = "ocw-match-same";
        } else if (!unmatched) {
          status = "待核对";
        }
        return `
          <tr>
            <td>${this.escape(materialCode || "--")}</td>
            <td>${this.escape(source.product_name || "--")}</td>
            <td>${this.escape(this.formatNumber(source.quantity))}</td>
            <td>${this.escape(this.formatMoney(source.unit_price))}</td>
            <td>${this.escape(source.purchase_currency || "--")}</td>
            <td>${this.escape(this.formatMoney(source.goods_value))}</td>
            <td><span class="ocw-match-status ${statusClass}">${this.escape(status)}</span></td>
          </tr>
        `;
      })
      .join("");
  }

  async applyPurchaseOrderMatch(dialog, attachmentName, batch = null, attachmentListDialog = null, sourcePreviewDialog = null, $button = null) {
    if ($button && $button.length) {
      $button.prop("disabled", true).text(ocTranslate("提交中"));
    }
    try {
      const confirmed = await new Promise((resolve) => {
        frappe.confirm(
          ocTranslate("将只补入总表中为空的采购单价、币种和货值；系统已有不同值不会覆盖。"),
          () => resolve(true),
          () => resolve(false)
        );
      });
      if (!confirmed) return;
      const result = await this.call(
        "overseas_costing.api.import_api.apply_oa_purchase_order_fillable_fields",
        { attachment_name: attachmentName, recalculate_after_writeback: 1 },
        true
      );
      if (!result || !result.ok) {
        this.showPendingFeature((result && result.message) || "采购订单字段写入失败。");
        return;
      }
      frappe.show_alert({ message: result.message || ocTranslate("采购订单空值已补入"), indicator: "green" });
      if (batch && attachmentListDialog) {
        await this.loadOaFormAttachments(batch, attachmentListDialog);
        await this.refreshBatch(batch.name);
      }
      if (sourcePreviewDialog) sourcePreviewDialog.hide();
      dialog.hide();
    } finally {
      if ($button && $button.length && dialog.$wrapper.is(":visible")) {
        $button.prop("disabled", false).text(ocTranslate("确认补入空值"));
      }
    }
  }

  async parseCurrentOaAttachments(batch, dialog, $button = null) {
    if (!batch || this.isParsingOaAttachments) return;
    this.isParsingOaAttachments = true;
    if ($button && $button.length) {
      $button.prop("disabled", true).text(ocTranslate("解析中"));
    }
    try {
      const result = await this.call(
        "overseas_costing.api.import_api.parse_oa_source_attachments",
        {
          batch_name: batch.name,
          limit: 80,
          skip_parsed: 1,
          recalculate: 1,
        },
        true
      );
      if (!result || !result.ok) {
        this.showOaAttachmentParseResult(result || { ok: false, message: ocTranslate("当前批次附件解析失败。") });
      } else {
        frappe.show_alert({ message: result.message || ocTranslate("当前批次可处理附件已解析"), indicator: "green" });
      }
      await this.loadOaFormAttachments(batch, dialog);
      await this.refreshBatch(batch.name);
    } finally {
      this.isParsingOaAttachments = false;
      if ($button && $button.length) {
        $button.prop("disabled", false).text(ocTranslate("附件处理"));
      }
    }
  }

  showOaAttachmentParseResult(result = {}) {
    const items = Array.isArray(result.items) ? result.items : [];
    const failedItems = items.filter((item) => item.action === "failed" || item.action === "blocked");
    const skippedItems = items.filter((item) => item.action === "skipped");
    const summaryCards = [
      ["扫描附件", result.scanned_count || 0],
      ["已下载", result.downloaded_count || 0],
      ["已解析", result.parsed_count || 0],
      ["装箱单", result.packing_parsed_count || 0],
      ["内容识别", result.source_recognized_count || 0],
      ["新增物料", result.created_count || 0],
      ["写入字段", result.changed_field_count || 0],
      ["失败", result.failed_count || 0],
      ["跳过", result.skipped_count || 0],
    ]
      .map(
        ([label, value]) => `
          <div>
            <span>${this.escape(ocTranslate(label))}</span>
            <strong>${this.escape(String(value))}</strong>
          </div>
        `
      )
      .join("");
    const failedRows = failedItems.length
      ? failedItems
          .map(
            (item) => `
              <tr>
                <td>${this.escape(item.file_name || item.attachment_name || "--")}</td>
                <td>${this.escape(this.attachmentTypeLabel(item.attachment_type))}</td>
                <td>${this.escape(this.oaAttachmentParseActionLabel(item))}</td>
                <td>${this.escape(item.reason || (item.download && item.download.message) || "--")}</td>
              </tr>
            `
          )
          .join("")
      : `<tr><td colspan="4" class="ocw-parse-result-empty">${ocTranslate("暂无失败附件")}</td></tr>`;
    const skippedText = skippedItems.length
      ? ocTranslate("另有 {0} 个附件暂未处理，通常是非装箱单 Excel 或暂不支持格式。", [skippedItems.length])
      : "";
    const fileAccessNote = result.file_access_blocked_count
      ? "当前账号没有部分钉钉附件的文件级访问权限。请换成能在钉钉原单打开附件的账号，或手动下载后拖放上传。"
      : "";
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("附件解析结果"),
      size: "large",
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "parse_result",
          options: `
            <div class="ocw-parse-result">
              <div class="ocw-purchase-summary">${summaryCards}</div>
              <div class="ocw-purchase-note">${this.escape(result.message || ocTranslate("当前批次附件解析未完成。"))}</div>
              ${fileAccessNote ? `<div class="ocw-confirm-note">${this.escape(fileAccessNote)}</div>` : ""}
              ${skippedText ? `<div class="ocw-parse-result-muted">${this.escape(skippedText)}</div>` : ""}
              <div class="ocw-purchase-table-wrap">
                <table class="ocw-purchase-table ocw-parse-result-table">
                  <thead>
                    <tr>
                      <th>${ocTranslate("附件")}</th>
                      <th>${ocTranslate("类型")}</th>
                      <th>${ocTranslate("状态")}</th>
                      <th>${ocTranslate("原因")}</th>
                    </tr>
                  </thead>
                  <tbody>${failedRows}</tbody>
                </table>
              </div>
            </div>
          `,
        },
      ],
      primary_action_label: ocTranslate("我知道了"),
      primary_action: () => dialog.hide(),
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-purchase-modal ocw-oa-parse-result-modal");
  }

  oaAttachmentParseActionLabel(item = {}) {
    if (item.error_type === "dingtalk_attachment_file_access") return ocTranslate("无附件访问权");
    if (item.error_type === "dingtalk_permission") return ocTranslate("缺少钉钉权限");
    if (item.action === "blocked") return ocTranslate("已暂停");
    if (item.action === "failed") return ocTranslate("失败");
    if (item.action === "skipped") return ocTranslate("已跳过");
    if (item.action === "parsed") return ocTranslate("已解析");
    return item.action || "--";
  }

  renderOaAttachmentList(items) {
    if (!items.length) {
      return `
        <section class="ocw-purchase-section">
          <h4>${ocTranslate("钉钉审批附件清单")}</h4>
          <div class="ocw-purchase-empty-line">${ocTranslate("暂无附件记录")}</div>
        </section>
      `;
    }
    const downloadableCount = items.filter((row) => {
      const error = row.last_download_error && row.last_download_error.error_type;
      return !row.file_url && !error && (row.can_download || row.file_id);
    }).length;
    const downloadedCount = items.filter((row) => row.file_url).length;
    const downloadFailedCount = items.filter((row) => row.last_download_error && row.last_download_error.error_type).length;
    const rows = items
      .map((row, index) => {
        const recognizedType = String(row.recognized_type || "").trim();
        const recognizedTypeLabel = recognizedType && recognizedType !== "unclassified" ? row.recognized_type_label : "";
        const typeLabel = row.confirmed_type_label || recognizedTypeLabel || this.attachmentTypeLabel(row.attachment_type);
        const downloadError = row.last_download_error && row.last_download_error.error_type ? row.last_download_error : null;
        const savedFileRef = row.file_url
          ? "已保存，可预览或下载原件"
          : downloadError
            ? this.attachmentDownloadErrorLabel(downloadError)
            : "";
        const actions = [];
        if (row.file_url) {
          actions.push(`
            <a
              class="ocw-link-btn"
              href="${this.escape(row.file_url)}"
              download="${this.escape(row.file_name || "")}"
              target="_self"
            >${ocTranslate("下载到本地")}</a>
          `);
          actions.push(`
            <button
              class="ocw-link-btn"
              data-action="preview-oa-attachment-file"
              data-attachment-name="${this.escape(row.name || "")}"
              data-file-url="${this.escape(row.file_url || "")}"
              data-file-name="${this.escape(row.file_name || "")}"
            >${ocTranslate("附件预览")}</button>
          `);
        } else if (row.can_download || row.file_id) {
          actions.push(`
            <button
              class="ocw-link-btn"
              data-action="download-oa-attachment"
              data-attachment-name="${this.escape(row.name || "")}"
              data-open-parse-after-download="0"
            >${downloadError ? ocTranslate("重试下载") : ocTranslate("下载到本地")}</button>
          `);
          actions.push(`
            <button
              class="ocw-link-btn"
              data-action="preview-oa-attachment-file"
              data-attachment-name="${this.escape(row.name || "")}"
              data-file-name="${this.escape(row.file_name || "")}"
            >${ocTranslate("附件预览")}</button>
          `);
          if (downloadError) {
            actions.push(`<span class="ocw-purchase-source-disabled">${this.escape(this.attachmentDownloadActionHint(downloadError))}</span>`);
          }
        } else {
          actions.push(`<span class="ocw-purchase-source-disabled">${ocTranslate("待下载")}</span>`);
        }
        return `
          <tr>
            <td>${this.escape(String(index + 1))}</td>
            <td title="${this.escape(row.file_name || "")}">
              <div class="ocw-attachment-file-name">${this.escape(row.file_name || "--")}</div>
              ${savedFileRef ? `<div class="ocw-attachment-file-url">${this.escape(savedFileRef)}</div>` : ""}
            </td>
            <td>${this.escape(typeLabel)}</td>
            <td>${this.escape(this.attachmentStatusLabel(row.parse_status, row))}</td>
            <td>${this.escape(this.attachmentPurposeLabel(row))}</td>
            <td><div class="ocw-attachment-actions">${actions.join("")}</div></td>
          </tr>
        `;
      })
      .join("");
    return `
      <section class="ocw-purchase-section">
        <h4>${ocTranslate("钉钉审批附件清单")}</h4>
        <div class="ocw-confirm-note">${ocTranslate("这里同时显示表单附件和评论附件，两者均可用于复核与装箱资料提取。")}</div>
        <div class="ocw-confirm-note">${ocTranslate("已登记 {0} 个附件，{1} 个待归档或保存，{2} 个已保存，{3} 个需处理。", [this.escape(String(items.length)), this.escape(String(downloadableCount)), this.escape(String(downloadedCount)), this.escape(String(downloadFailedCount))])}</div>
        <div class="ocw-purchase-table-wrap">
          <table class="ocw-purchase-table ocw-attachment-table">
            <colgroup>
              <col class="ocw-col-index">
              <col class="ocw-col-file">
              <col class="ocw-col-type">
              <col class="ocw-col-status">
              <col class="ocw-col-purpose">
              <col class="ocw-col-action">
            </colgroup>
            <thead>
              <tr>
                <th>#</th>
                <th>${ocTranslate("文件名")}</th>
                <th>${ocTranslate("类型")}</th>
                <th>${ocTranslate("状态")}</th>
                <th>${ocTranslate("用途")}</th>
                <th>${ocTranslate("操作")}</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </section>
    `;
  }

  attachmentDownloadErrorLabel(error = {}) {
    if (error.error_type === "dingtalk_attachment_file_access") return ocTranslate("下载失败：当前账号无附件访问权");
    if (error.error_type === "dingtalk_attachment_permission") return ocTranslate("下载失败：应用权限不足");
    if (error.error_type === "dingtalk_attachment_user") return ocTranslate("下载失败：下载账号不可用");
    return ocTranslate("下载失败：请查看原因后重试");
  }

  attachmentDownloadActionHint(error = {}) {
    if (error.error_type === "dingtalk_attachment_file_access") return ocTranslate("换可访问账号后重试");
    if (error.error_type === "dingtalk_attachment_permission") return ocTranslate("开通权限后重试");
    if (error.error_type === "dingtalk_attachment_user") return ocTranslate("配置账号后重试");
    return ocTranslate("处理后重试");
  }

  attachmentTypeLabel(type) {
    const labels = {
      "Packing List": ocTranslate("装箱单"),
      "Tax Certificate": ocTranslate("完税凭证"),
      "Logistics Bill": ocTranslate("物流账单"),
      "Commercial Invoice": ocTranslate("商业发票"),
      "Purchase Order": ocTranslate("采购订单"),
      "Customs Declaration": ocTranslate("报关资料"),
      Other: ocTranslate("待识别"),
    };
    return labels[type] || type || ocTranslate("待识别");
  }

  attachmentPurposeLabel(row = {}) {
    const confirmedType = String((row.manual_review && row.manual_review.confirmed_type) || "").trim();
    const recognizedType = String(row.recognized_type || "").trim();
    const type = confirmedType || (recognizedType && recognizedType !== "unclassified" ? recognizedType : "") || String(row.attachment_type || "").trim();
    const purposes = {
      "Packing List": "核对实际数量、重量和体积",
      "Purchase Order": "核对采购单价、币种和货值",
      purchase_order: "核对采购单价、币种和货值",
      purchase_price_document: "核对采购价格信息",
      "Customs Declaration": "核对报关单号和海关信息",
      customs_declaration: "核对报关单号和海关信息",
      "Logistics Bill": "核对物流费用",
      logistics_quote: "核对物流费用",
      "Tax Certificate": "核对最终税费",
      "Commercial Invoice": "核对发票号、货值和币种",
      "Excel Main Table": "核对货物明细",
      other: "保留原件备查",
      Other: "保留原件备查",
    };
    return purposes[type] || ocTranslate("待人工判断用途");
  }

  attachmentStatusLabel(status, row = {}) {
    if (row.manual_review && row.manual_review.status === "confirmed") return ocTranslate("已人工确认");
    if (row.last_download_error && row.last_download_error.error_type) {
      if (row.last_download_error.error_type === "dingtalk_attachment_file_access") return ocTranslate("下载失败（无访问权）");
      return ocTranslate("下载失败");
    }
    const normalized = String(status || "").trim().toLowerCase();
    if (row.file_url) return ocTranslate("已保存");
    if (normalized === "failed" || normalized === "error") return ocTranslate("待复核");
    if (normalized === "queued") return ocTranslate("待下载");
    if (!normalized) return ocTranslate("待下载");
    return status;
  }

  attachmentParseTargetLabel(target) {
    const labels = {
      actual_shipped_qty: ocTranslate("实际发货数量"),
      gross_weight_kg: ocTranslate("毛重KG"),
      volume_m3: ocTranslate("体积m3"),
      volume_weight_kg: ocTranslate("体积重KG"),
      chargeable_weight_kg: ocTranslate("计费重KG"),
      pedimento_no: ocTranslate("报关单号"),
      tax_totals: ocTranslate("税费合计"),
      paid_total_mxn: ocTranslate("实缴金额MXN"),
      line_items: ocTranslate("明细行"),
      logistics_fee: ocTranslate("物流费用"),
      fuel_surcharge: ocTranslate("燃油附加费"),
      currency: ocTranslate("币种"),
      bill_total: ocTranslate("账单总额"),
      invoice_no: ocTranslate("发票号"),
      goods_value: ocTranslate("货值"),
      unit_price: ocTranslate("采购单价"),
      purchase_currency: ocTranslate("采购币种"),
      customs_declaration: ocTranslate("报关资料"),
    };
    return labels[target] || target || "";
  }

  openPackingListPreviewDialog(batchName = "", attachmentName = "", fileUrl = "") {
    const batch = batchName ? this.findBatch(batchName) : this.getActiveBatch();
    if (!batch) {
      this.showPendingFeature("当前没有可解析装箱单的批次。");
      return;
    }
    if (!fileUrl) {
      this.showPendingFeature("当前附件只有钉钉文件标识，还没有下载到系统文件地址，暂不能解析。");
      return;
    }
    this.activeBatchName = batch.name;
    const batchLabel = batch.batch_no || batch.waybill_no || batch.name;
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("装箱单解析预览"),
      size: "large",
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "packing_preview",
          options: `
            <div class="ocw-purchase-preview" data-area="packing-preview">
              <div class="ocw-purchase-target">
                <span>${ocTranslate("当前批次")}</span>
                <strong>${this.escape(batchLabel)}</strong>
                <em>${ocTranslate("先预览匹配结果，不会直接写入实际数量、毛重和体积。")}</em>
              </div>
              <div class="ocw-purchase-loading">${ocTranslate("正在解析装箱单")}</div>
            </div>
          `,
        },
      ],
      primary_action_label: ocTranslate("关闭"),
      primary_action: () => dialog.hide(),
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-purchase-modal ocw-packing-preview-modal");
    this.loadPackingListPreview(batch, dialog, attachmentName, fileUrl).catch((error) => {
      dialog.$wrapper.find("[data-area='packing-preview']").html(`
        <div class="ocw-purchase-empty">
          <strong>${ocTranslate("装箱单解析预览失败")}</strong>
          <span>${this.escape(this.normalizeErrorMessage(error))}</span>
        </div>
      `);
    });
  }

  async loadPackingListPreview(batch, dialog, attachmentName, fileUrl) {
    const result = await this.call(
      "overseas_costing.api.import_api.preview_packing_list_attachment",
      {
        batch_name: batch.name,
        version_name: batch.current_version || null,
        attachment_name: attachmentName || null,
        file_url: fileUrl || null,
      },
      true
    );
    this.renderPackingListPreview(dialog, result, batch, attachmentName, fileUrl);
  }

  renderPackingListPreview(dialog, result, batch, attachmentName, fileUrl) {
    const $target = dialog.$wrapper.find("[data-area='packing-preview']");
    if (!result || !result.ok) {
      $target.html(`
        <div class="ocw-purchase-empty">
          <strong>${ocTranslate("暂时无法解析装箱单")}</strong>
          <span>${this.escape((result && result.message) || ocTranslate("请确认附件已经下载为 Excel 文件。"))}</span>
        </div>
      `);
      return;
    }

    const preview = result.writeback_preview || {};
    const matchedRows = preview.matched_rows || [];
    const fillableRows = matchedRows.filter((row) => row.has_fillable);
    const conflictRows = matchedRows.filter((row) => row.has_conflict);
    const unmatchedRows = preview.unmatched_rows || [];
    const ambiguousRows = preview.ambiguous_rows || [];
    const parser = result.parser_meta || {};

    $target.html(`
      <div class="ocw-purchase-target">
        <span>${ocTranslate("当前批次")}</span>
        <strong>${this.escape(batch.batch_no || batch.waybill_no || batch.name)}</strong>
        <em>${this.escape(result.message || ocTranslate("装箱单预览已完成，当前未写入数据。"))}</em>
      </div>
      <div class="ocw-purchase-summary">
        <div><span>${ocTranslate("解析行数")}</span><strong>${this.escape(String(result.mapped_preview_count || 0))}</strong></div>
        <div><span>${ocTranslate("匹配行")}</span><strong>${this.escape(String(preview.matched_count || 0))}</strong></div>
        <div><span>${ocTranslate("可写入")}</span><strong>${this.escape(String(preview.fillable_row_count || 0))}</strong></div>
        <div><span>${ocTranslate("未匹配")}</span><strong>${this.escape(String(preview.unmatched_count || 0))}</strong></div>
      </div>
      <div class="ocw-purchase-note">
        ${ocTranslate("来源附件已与当前批次物料核对；只补当前为空或为 0 的实际发货数量、毛重、体积、计费重。")}
      </div>
      ${this.renderPackingApplyAction(preview)}
      ${this.renderPurchasePreviewSection("可写入装箱单字段", fillableRows, "fillable")}
      ${this.renderPackingConflictSection(conflictRows)}
      ${this.renderPackingUnmatchedSection(unmatchedRows, ambiguousRows)}
    `);
    dialog.$wrapper
      .off("click.ocwPackingPreview")
      .on("click.ocwPackingPreview", "[data-action='apply-packing-fillable']", () => {
        this.applyPackingListFillableFields(batch, dialog, result, attachmentName, fileUrl).catch((error) => this.showError(error));
      })
      .on("click.ocwPackingPreview", "[data-action='resolve-packing-conflict']", (event) => {
        this.resolvePackingConflictRow(
          batch,
          dialog,
          attachmentName,
          fileUrl,
          $(event.currentTarget).attr("data-target-item-name"),
          $(event.currentTarget).attr("data-resolution-action"),
          $(event.currentTarget)
        ).catch((error) => this.showError(error));
      });
  }

  renderPackingApplyAction(preview = {}) {
    const fillableCount = Number(preview.fillable_row_count || 0);
    if (!fillableCount) return "";
    return `
      <div class="ocw-purchase-apply">
        <div>
          <strong>${ocTranslate("写入可补装箱单字段")}</strong>
          <span>${ocTranslate("本次可写入 {0} 行，只补空值或 0 值；已有差异的行不自动覆盖。", [this.escape(String(fillableCount))])}</span>
        </div>
        <button class="ocw-primary-btn ocw-mini-btn" data-action="apply-packing-fillable">${ocTranslate("写入可补字段")}</button>
      </div>
    `;
  }

  renderPackingConflictSection(conflictRows = []) {
    if (!conflictRows.length) {
      return `
        <section class="ocw-purchase-section">
          <h4>${ocTranslate("待处理差异")}</h4>
          <div class="ocw-purchase-empty-line">${ocTranslate("暂无需要人工处理的差异")}</div>
        </section>
      `;
    }
    const body = conflictRows
      .map((row) => {
        const conflicts = (row.business_changes || []).filter((change) => change.status === "conflict");
        const values = conflicts
          .map(
            (change) => `
              <div class="ocw-packing-conflict-value">
                <strong>${this.escape(change.field_label || change.field_name || ocTranslate("字段"))}</strong>
                <span>${ocTranslate("系统：{0}", [this.escape(this.formatDisplayNumber(change.old_value, change.field_name) || ocTranslate("空"))])}</span>
                <span>${ocTranslate("附件：{0}", [this.escape(this.formatDisplayNumber(change.new_value, change.field_name) || ocTranslate("空"))])}</span>
              </div>
            `
          )
          .join("");
        const resolution = row.conflict_resolution || {};
        const resolutionText = resolution.action_label
          ? `<div class="ocw-packing-resolution-status">${ocTranslate("当前处理：{0}", [this.escape(resolution.action_label)])}</div>`
          : "";
        return `
          <tr>
            <td>${this.escape(String(row.target_row_no || "--"))}</td>
            <td>${this.escape(row.target_material_code || "--")}</td>
            <td title="${this.escape(row.target_product_name || "")}">${this.escape(row.target_product_name || "--")}</td>
            <td>${values || "--"}${resolutionText}</td>
            <td>
              <div class="ocw-packing-conflict-actions">
                <button class="ocw-link-btn" type="button" data-action="resolve-packing-conflict" data-target-item-name="${this.escape(row.target_item_name || "")}" data-resolution-action="use_attachment">${ocTranslate("采用附件值")}</button>
                <button class="ocw-link-btn" type="button" data-action="resolve-packing-conflict" data-target-item-name="${this.escape(row.target_item_name || "")}" data-resolution-action="keep_system">${ocTranslate("保留系统值")}</button>
                <button class="ocw-link-btn" type="button" data-action="resolve-packing-conflict" data-target-item-name="${this.escape(row.target_item_name || "")}" data-resolution-action="pending_review">${ocTranslate("待核对")}</button>
              </div>
            </td>
          </tr>
        `;
      })
      .join("");
    return `
      <section class="ocw-purchase-section">
        <h4>${ocTranslate("待处理差异")}</h4>
        <div class="ocw-purchase-table-wrap">
          <table class="ocw-purchase-table ocw-packing-conflict-table">
            <thead><tr><th>${ocTranslate("行号")}</th><th>${ocTranslate("物料编码")}</th><th>${ocTranslate("系统品名")}</th><th>${ocTranslate("系统值与附件值")}</th><th>${ocTranslate("处理")}</th></tr></thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      </section>
    `;
  }

  async resolvePackingConflictRow(batch, dialog, attachmentName, fileUrl, targetItemName, resolutionAction, $button = null) {
    const actionLabels = {
      use_attachment: "采用附件值",
      keep_system: "保留系统值",
      pending_review: "待核对",
    };
    if (!targetItemName || !actionLabels[resolutionAction]) {
      this.showPendingFeature("缺少差异处理信息。");
      return;
    }
    if (resolutionAction === "use_attachment") {
      const confirmed = await new Promise((resolve) => {
        frappe.confirm(
          ocTranslate("采用附件值将覆盖当前物料行的系统值，并触发重新试算。"),
          () => resolve(true),
          () => resolve(false)
        );
      });
      if (!confirmed) return;
    }
    if ($button && $button.length) {
      $button.prop("disabled", true).text(ocTranslate("处理中"));
    }
    try {
      const result = await this.call(
        "overseas_costing.api.import_api.resolve_packing_list_conflict_row",
        {
          batch_name: batch.name,
          version_name: batch.current_version || null,
          attachment_name: attachmentName || null,
          file_url: fileUrl || null,
          target_item_name: targetItemName,
          resolution_action: resolutionAction,
          recalculate_after_writeback: 1,
        },
        true
      );
      if (!result || !result.ok) {
        throw new Error((result && result.message) || "保存差异处理结果失败");
      }
      await this.loadBatchItems(batch.name, batch.current_version, true);
      await this.loadAuditLogs(batch.name, batch.current_version);
      this.expandedBatchNames.add(batch.name);
      this.renderTable();
      this.renderDiffPanel();
      frappe.show_alert({ message: result.message || ocTranslate("{0}已保存", [actionLabels[resolutionAction]]), indicator: "green" });
      await this.loadPackingListPreview(batch, dialog, attachmentName, fileUrl);
    } finally {
      if ($button && $button.length && dialog.$wrapper.is(":visible")) {
        $button.prop("disabled", false).text(actionLabels[resolutionAction]);
      }
    }
  }

  renderPackingUnmatchedSection(unmatchedRows = [], ambiguousRows = []) {
    const rows = [
      ...ambiguousRows.map((row) => ({
        ...(row.mapped_row || {}),
        source_row_no: row.source_row_no,
        reason: row.reason || ocTranslate("匹配到多行：{0}", [(row.candidate_row_nos || []).join("、")]),
        suggestion: row.suggestion || ocTranslate("补齐规格、数量或物料编码后重新解析。"),
      })),
      ...unmatchedRows.map((row) => ({
        ...row,
        reason: row.reason || "当前批次没有匹配物料，未写入",
        suggestion: row.suggestion || ocTranslate("确认属于本批次后，可先新增物料或修正编码再解析。"),
      })),
    ];
    if (!rows.length) {
      return `
        <section class="ocw-purchase-section">
          <h4>${ocTranslate("未匹配装箱单行")}</h4>
          <div class="ocw-purchase-empty-line">${ocTranslate("暂无未匹配行")}</div>
        </section>
      `;
    }
    const body = rows
      .map(
        (row) => `
          <tr>
            <td>${this.escape(row.source_row_no || row.excel_row_no || "--")}</td>
            <td>${this.escape(row.material_code || "--")}</td>
            <td title="${this.escape(row.product_name || "")}">${this.escape(row.product_name || "--")}</td>
            <td title="${this.escape(row.spec_model || "")}">${this.escape(row.spec_model || "--")}</td>
            <td>${this.escape(this.formatValue(row.actual_shipped_qty) || "--")}</td>
            <td>${this.escape(this.formatValue(row.gross_weight_kg) || "--")}</td>
            <td>${this.escape(this.formatValue(row.volume_m3) || "--")}</td>
            <td>${this.renderReasonCell(row)}</td>
          </tr>
        `
      )
      .join("");
    return `
      <section class="ocw-purchase-section">
        <h4>${ocTranslate("未匹配装箱单行")}</h4>
        <div class="ocw-purchase-table-wrap">
          <table class="ocw-purchase-table">
            <thead>
              <tr>
                <th>${ocTranslate("来源行")}</th>
                <th>${ocTranslate("物料编码")}</th>
                <th>${ocTranslate("品名")}</th>
                <th>${ocTranslate("规格")}</th>
                <th>${ocTranslate("实际数量")}</th>
                <th>${ocTranslate("毛重 KG")}</th>
                <th>${ocTranslate("体积 m3")}</th>
                <th>${ocTranslate("原因")}</th>
              </tr>
            </thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      </section>
    `;
  }

  async applyPackingListFillableFields(batch, dialog, previewResult, attachmentName, fileUrl) {
    const preview = (previewResult && previewResult.writeback_preview) || {};
    const fillableCount = Number(preview.fillable_row_count || 0);
    if (!fillableCount) {
      frappe.show_alert({ message: ocTranslate("当前没有可写入的装箱单字段"), indicator: "blue" });
      return;
    }
    const confirmed = await new Promise((resolve) => {
      frappe.confirm(
        `
          <div class="ocw-confirm-copy">
            <h4>${ocTranslate("确认写入装箱单字段？")}</h4>
            <p>${ocTranslate("本次最多写入 {0} 行实际发货数量、毛重、体积或计费重。", [this.escape(String(fillableCount))])}</p>
            <div class="ocw-confirm-note">${ocTranslate("只补空值或 0 值；已有差异的行不会自动覆盖。")}</div>
          </div>
        `,
        () => resolve(true),
        () => resolve(false)
      );
    });
    if (!confirmed) return;

    const $button = dialog.$wrapper.find("[data-action='apply-packing-fillable']");
    $button.prop("disabled", true).text(ocTranslate("写入中"));
    try {
      const result = await this.call(
        "overseas_costing.api.import_api.apply_packing_list_fillable_fields",
        {
          batch_name: batch.name,
          version_name: batch.current_version || null,
          attachment_name: attachmentName || null,
          file_url: fileUrl || null,
        },
        true
      );
      if (!result.ok) {
        throw new Error(result.message || "装箱单字段写入失败");
      }
      if (Number(result.updated_count || 0) > 0) {
        this.markBatchDirty(batch.name);
      }
      await this.loadBatchItems(batch.name, batch.current_version, true);
      await this.loadAuditLogs(batch.name, batch.current_version);
      this.expandedBatchNames.add(batch.name);
      this.renderTable();
      this.renderDiffPanel();
      frappe.show_alert({ message: result.message || ocTranslate("装箱单字段已写入"), indicator: result.updated_count ? "green" : "blue" });
      dialog.hide();
    } finally {
      $button.prop("disabled", false).text(ocTranslate("写入可补字段"));
    }
  }

  openDingtalkLink(openUrl) {
    const url = String(openUrl || "").trim();
    if (!url) {
      this.showPendingFeature("当前审批单没有可打开的钉钉链接。");
      return;
    }
    if (url.startsWith("dingtalk://")) {
      window.location.href = url;
      frappe.show_alert({ message: ocTranslate("正在唤起钉钉客户端"), indicator: "blue" });
      return;
    }
    this.openBrowserTab(url, "钉钉审批单");
  }

  openBrowserTab(url, title = "打开链接", preopenedWindow = null) {
    const targetUrl = String(url || "").trim();
    if (!targetUrl) return false;
    if (preopenedWindow && !preopenedWindow.closed) {
      preopenedWindow.location.href = targetUrl;
      return true;
    }
    const link = document.createElement("a");
    link.href = targetUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    return true;
  }

  closePreopenedWindow(preopenedWindow) {
    if (preopenedWindow && !preopenedWindow.closed) {
      preopenedWindow.close();
    }
  }

  renderPurchasePreviewSection(title, rows, type) {
    if (!rows.length) {
      return `
        <section class="ocw-purchase-section">
          <h4>${this.escape(ocTranslate(title))}</h4>
          <div class="ocw-purchase-empty-line">${ocTranslate("暂无{0}行", [type === "fillable" ? "可写入" : "差异"])}</div>
        </section>
      `;
    }
    const body = rows
      .map((row) => {
        const changes = (row.business_changes || [])
          .map((change) => {
            const status = this.purchaseChangeLabel(change.status);
            return `
              <span class="ocw-purchase-change ${this.escape(change.status || "")}">
                ${this.escape(change.field_label || change.field_name)}：${this.escape(this.formatDisplayNumber(change.old_value, change.field_name) || ocTranslate("空"))} -> ${this.escape(this.formatDisplayNumber(change.new_value, change.field_name) || ocTranslate("空"))}（${this.escape(status)}）
              </span>
            `;
          })
          .join("");
        return `
          <tr>
            <td>${this.escape(String(row.target_row_no || "--"))}</td>
            <td>${this.escape(row.target_material_code || "--")}</td>
            <td title="${this.escape(row.target_product_name || "")}">${this.escape(row.target_product_name || "--")}</td>
            <td title="${this.escape(row.target_spec_model || "")}">${this.escape(row.target_spec_model || "--")}</td>
            <td>${changes || "--"}</td>
          </tr>
        `;
      })
      .join("");
    return `
      <section class="ocw-purchase-section">
        <h4>${this.escape(ocTranslate(title))}</h4>
        <div class="ocw-purchase-table-wrap">
          <table class="ocw-purchase-table">
            <thead>
              <tr>
                <th>${ocTranslate("行号")}</th>
                <th>${ocTranslate("物料编码")}</th>
                <th>${ocTranslate("系统品名")}</th>
                <th>${ocTranslate("系统规格")}</th>
                <th>${ocTranslate("预览变化")}</th>
              </tr>
            </thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      </section>
    `;
  }

  renderPurchaseUnmatchedSection(unmatchedRows, ambiguousRows) {
    const rows = [
      ...ambiguousRows.map((row) => ({
        ...row.mapped_row,
        source_row_no: row.source_row_no,
        reason: row.reason || ocTranslate("匹配到多行：{0}", [(row.candidate_row_nos || []).join("、")]),
        suggestion: row.suggestion || ocTranslate("补齐规格或数量后重新匹配。"),
      })),
      ...unmatchedRows.map((row) => ({
        ...row,
        reason: row.reason || "当前批次没有该物料编码，未写入",
        suggestion: row.suggestion || ocTranslate("确认属于本批次后，可先新增物料或修正编码再解析。"),
      })),
    ];
    if (!rows.length) {
      return `
        <section class="ocw-purchase-section">
          <h4>${ocTranslate("未匹配采购行")}</h4>
          <div class="ocw-purchase-empty-line">${ocTranslate("暂无未匹配行")}</div>
        </section>
      `;
    }
    const body = rows
      .map(
        (row) => `
          <tr>
            <td>${this.escape(row.source_row_no || row.excel_row_no || "--")}</td>
            <td>${this.escape(row.material_code || "--")}</td>
            <td title="${this.escape(row.product_name || "")}">${this.escape(row.product_name || "--")}</td>
            <td title="${this.escape(row.spec_model || "")}">${this.escape(row.spec_model || "--")}</td>
            <td>${this.escape(this.formatMoney(row.unit_price) || "--")}</td>
            <td>${this.escape(row.purchase_currency || "--")}</td>
            <td>${this.escape(row.source_approval_no || "--")}</td>
            <td>${this.renderReasonCell(row)}</td>
          </tr>
        `
      )
      .join("");
    return `
      <section class="ocw-purchase-section">
        <h4>${ocTranslate("未匹配采购行")}</h4>
        <div class="ocw-purchase-table-wrap">
          <table class="ocw-purchase-table">
            <thead>
              <tr>
                <th>${ocTranslate("来源行")}</th>
                <th>${ocTranslate("物料编码")}</th>
                <th>${ocTranslate("采购品名")}</th>
                <th>${ocTranslate("采购规格")}</th>
                <th>${ocTranslate("单价")}</th>
                <th>${ocTranslate("币种")}</th>
                <th>${ocTranslate("采购审批")}</th>
                <th>${ocTranslate("原因")}</th>
              </tr>
            </thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      </section>
    `;
  }

  renderReasonCell(row = {}) {
    const reason = row.reason || "--";
    const suggestion = row.suggestion || "";
    return `
      <div class="ocw-match-reason">
        <strong>${this.escape(reason)}</strong>
        ${suggestion ? `<small>${this.escape(suggestion)}</small>` : ""}
      </div>
    `;
  }

  purchaseChangeLabel(status) {
    const labels = {
      fillable: ocTranslate("可写入"),
      conflict: ocTranslate("有差异"),
      same: ocTranslate("一致"),
      empty_source: ocTranslate("来源为空"),
    };
    return labels[status] || status || ocTranslate("未知");
  }
