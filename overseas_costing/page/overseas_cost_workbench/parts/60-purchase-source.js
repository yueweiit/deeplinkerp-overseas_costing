
  isTextFileRef(value) {
    return String(value || "").split("?")[0].toLowerCase().endsWith(".txt");
  }

  openApprovalSourceDialog(batchName = "") {
    const batch = batchName ? this.findBatch(batchName) : this.getActiveBatch();
    if (!batch) {
      this.showPendingFeature("当前没有可查看审批单来源的批次。");
      return;
    }
    this.activeBatchName = batch.name;
    const batchLabel = batch.batch_no || batch.waybill_no || batch.name;
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("审批单与 OA 来源"),
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "approval_source",
          options: `
            <div class="ocw-quick-panel">
              <div class="ocw-quick-context">
                <span>${ocTranslate("当前批次")}</span>
                <strong>${this.escape(batchLabel)}</strong>
              </div>
              <button class="ocw-quick-card" data-action="approval-open-original">
                <strong>${ocTranslate("钉钉原单")}</strong>
                <span>${ocTranslate("打开当前批次对应的钉钉审批表")}</span>
              </button>
            </div>
          `,
        },
      ],
      primary_action_label: ocTranslate("关闭"),
      primary_action: () => dialog.hide(),
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-quick-modal");
    dialog.$wrapper
      .off("click.ocwApprovalSource")
      .on("click.ocwApprovalSource", "[data-action='approval-open-original']", () => {
        this.openDingtalkOrder(batch.name);
      });
  }

  openSourceCenterDialog(batchName = "", gap = {}) {
    const batch = batchName ? this.findBatch(batchName) : this.getActiveBatch();
    if (!batch) {
      this.showPendingFeature("当前没有可查看资料的批次。");
      return;
    }
    this.activeBatchName = batch.name;
    const logisticsType = this.detectManualDocumentLogisticsType(batch);
    const focus = {
      fieldname: String(gap.fieldname || "").trim(),
      label: String(gap.label || gap.fieldname || "").trim(),
    };
    const focusSlotCodes = this.manualDocumentSlotsForGap(batch, focus, logisticsType);
    const dialog = new frappe.ui.Dialog({
      title: focus.label ? ocTranslate("补齐资料：{0}", [focus.label]) : ocTranslate("资料上传与补齐"),
      size: "large",
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "manual_documents",
          options: `<div data-area="manual-documents">${this.renderManualDocumentPanel(batch, logisticsType, [], { ...focus, slotCodes: focusSlotCodes })}</div>`,
        },
      ],
      primary_action_label: ocTranslate("关闭"),
      primary_action: () => dialog.hide(),
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-purchase-modal ocw-manual-document-modal");
    this.addManualDocumentBatchParseButton(batch, dialog);
    dialog.$wrapper
      .off("click.ocwManualDocuments")
      .on("click.ocwManualDocuments", "[data-action='manual-fill-gap']", (event) => {
        const $button = $(event.currentTarget);
        const currentFocusSlotCodes = dialog.$wrapper
          .find(".ocw-manual-doc-card.is-gap-focus")
          .map((index, element) => $(element).attr("data-slot-code"))
          .get()
          .filter(Boolean);
        const clickedSlotCode = $button.attr("data-slot-code") || "";
        this.openManualGapFillDialog(
          batch,
          {
            fieldname: $button.attr("data-gap-fieldname") || focus.fieldname,
            label: $button.attr("data-gap-label") || focus.label,
            slotCode: clickedSlotCode,
            slotCodes: currentFocusSlotCodes.length ? currentFocusSlotCodes : clickedSlotCode ? [clickedSlotCode] : [],
            slotLabel: $button.attr("data-slot-label") || "",
            attachmentType: $button.attr("data-attachment-type") || "Other",
            required: $button.attr("data-required") === "1",
            logisticsType: $button.attr("data-logistics-type") || logisticsType,
          },
          dialog
        );
      })
      .on("click.ocwManualDocuments", "[data-action='upload-manual-document']", (event) => {
        const $button = $(event.currentTarget);
        const slot = {
          code: $button.attr("data-slot-code"),
          label: $button.attr("data-slot-label"),
          attachmentType: $button.attr("data-attachment-type"),
          required: $button.attr("data-required") === "1",
          focusSlotCodes: dialog.$wrapper
            .find(".ocw-manual-doc-card.is-gap-focus")
            .map((index, element) => $(element).attr("data-slot-code"))
            .get()
            .filter(Boolean),
        };
        const activeType = $button.attr("data-logistics-type");
        this.openManualDocumentUploader(batch, dialog, activeType, slot);
      })
      .on("click.ocwManualDocuments", "[data-action='preview-manual-document']", (event) => {
        const $button = $(event.currentTarget);
        this.openOaAttachmentFilePreviewDialog($button.attr("data-file-url"), $button.attr("data-file-name"));
      })
      .on("click.ocwManualDocuments", "[data-action='download-manual-document']", (event) => {
        const $button = $(event.currentTarget);
        this.downloadFileToLocal($button.attr("data-file-url"), $button.attr("data-file-name"));
      })
      .on("click.ocwManualDocuments", "[data-action='delete-manual-document']", (event) => {
        const focusSlotCodes = dialog.$wrapper
          .find(".ocw-manual-doc-card.is-gap-focus")
          .map((index, element) => $(element).attr("data-slot-code"))
          .get()
          .filter(Boolean);
        this.deleteManualDocumentAttachment(
          batch,
          dialog,
          $(event.currentTarget).attr("data-attachment-name"),
          $(event.currentTarget).attr("data-logistics-type"),
          { fieldname: focus.fieldname, label: focus.label, slotCodes: focusSlotCodes }
        ).catch((error) => this.showError(error));
      });
    this.loadManualDocumentAttachments(batch, dialog, logisticsType, { ...focus, slotCodes: focusSlotCodes }).catch((error) => this.showError(error));
  }
