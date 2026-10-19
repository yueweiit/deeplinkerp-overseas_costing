  ensureMaterialFeeState() {
    const batchName = String(this.detailState?.batchName || "");
    if (!this.materialFeeState || this.materialFeeState.batchName !== batchName) {
      this.materialFeeState = {
        batchName,
        page: 1,
        pageLength: 200,
        onlyMissing: false,
        showAuxiliary: false,
        loading: false,
        requestId: 0,
        feeRequestId: 0,
        feeDrafts: {},
        focusedFeeInput: null,
        materials: null,
        fees: null,
        preview: null,
        cache: null,
        cacheDirty: false,
        cacheChecking: false,
        aiFill: null,
        aiPendingReady: null,
        aiProgressDialog: null,
        aiProgressMinimized: false,
        aiStartPromise: null,
        activeAICandidate: null,
        feeEvidenceReview: null,
        feeEvidenceReviewDialog: null,
        feeEvidenceReviewStartPromise: null,
        feeEvidenceReviewMonitorPromise: null,
        feeEvidenceReviewActiveRequestKey: "",
        costTrialAI: null,
        costTrialDialog: null,
        evidencePreview: null,
        projectRouteOptionsCache: null,
        projectRouteOptionsPromise: null,
        supplierOptionsCache: new Map(),
      };
    }
    if (!Number.isFinite(this.materialFeeState.requestId)) this.materialFeeState.requestId = 0;
    if (!Number.isFinite(this.materialFeeState.feeRequestId)) this.materialFeeState.feeRequestId = 0;
    this.materialFeeState.feeDrafts = this.materialFeeState.feeDrafts || {};
    this.materialFeeState.pendingWrites = this.materialFeeState.pendingWrites || new Set();
    this.materialFeeState.feeCellWrites = this.materialFeeState.feeCellWrites || new Map();
    this.materialFeeState.materialCellWrites = this.materialFeeState.materialCellWrites || new Map();
    this.materialFeeState.materialCellWriteTargets = this.materialFeeState.materialCellWriteTargets || {};
    if (!Number.isFinite(this.materialFeeState.materialCellWriteRevision)) this.materialFeeState.materialCellWriteRevision = 0;
    this.materialFeeState.materialSaveErrors = this.materialFeeState.materialSaveErrors || {};
    this.materialFeeState.materialDrafts = this.materialFeeState.materialDrafts || {};
    this.materialFeeState.packingGroupSelections = this.materialFeeState.packingGroupSelections || new Set();
    this.materialFeeState.supplierOptionsCache = this.materialFeeState.supplierOptionsCache instanceof Map
      ? this.materialFeeState.supplierOptionsCache : new Map();
    this.materialFeeState.aiFill = this.materialFeeState.aiFill || null;
    this.materialFeeState.aiPendingReady = this.materialFeeState.aiPendingReady || null;
    if (this.materialFeeState.aiClarification === undefined) this.materialFeeState.aiClarification = "";
    if (this.materialFeeState.aiClarificationSaved === undefined) this.materialFeeState.aiClarificationSaved = "";
    if (!Number.isFinite(this.materialFeeState.aiClarificationRevision)) this.materialFeeState.aiClarificationRevision = 0;
    if (!this.materialFeeState.aiClarificationStatus) this.materialFeeState.aiClarificationStatus = "saved";
    if (!Number.isFinite(this.materialFeeState.inputRevision)) this.materialFeeState.inputRevision = 0;
    if (this.materialFeeState.focusedFeeInput === undefined) this.materialFeeState.focusedFeeInput = null;
    if (this.materialFeeState.evidencePreview === undefined) this.materialFeeState.evidencePreview = null;
    return this.materialFeeState;
  }

  bindMaterialFeeWorkspaceEvents() {
    this.$root.on("click", "[data-action='mf-reload']", () => {
      this.clearMaterialSelection(false);
      this.invalidateProjectRouteOptions();
      this.loadMaterialFeeWorkspace({ forceRefresh: true });
    });
    this.$root.on("click", "[data-action='mf-jump-status']", (event) => {
      this.jumpToMaterialFeeSection($(event.currentTarget).attr("data-target"));
    });
    this.$root.on("click", "[data-action='mf-view-settlement-source']", () => {
      this.openBatchSettlementDialog(this.detailState.batchName, this.detailState.versionName || null);
    });
    this.$root.on("click", "[data-action='mf-toggle-missing']", () => {
      const state = this.ensureMaterialFeeState();
      state.onlyMissing = !state.onlyMissing;
      this.clearMaterialSelection(false);
      this.renderMaterialFeeWorkspace();
    });
    this.$root.on("click", "[data-action='mf-toggle-aux']", () => {
      const state = this.ensureMaterialFeeState();
      state.showAuxiliary = !state.showAuxiliary;
      this.renderMaterialFeeWorkspace();
    });
    this.$root.on("click", "[data-action='mf-material-page']", (event) => {
      this.closeMaterialAICandidatePopover();
      const state = this.ensureMaterialFeeState();
      state.page = Math.max(1, Number($(event.currentTarget).attr("data-page") || 1));
      this.clearMaterialSelection(false);
      this.loadMaterialFeeWorkspace();
    });
    this.$root.on("click", "[data-action='mf-edit-fee']", (event) => {
      this.openMaterialFeeDialog($(event.currentTarget).attr("data-fee-key"));
    });
    this.$root.on("click", "[data-mf-freight-action]", (event) => {
      const $button = $(event.currentTarget);
      const action = $button.attr("data-mf-freight-action");
      const state = this.ensureMaterialFeeState();
      if (state.freightEditor?.freightWriting) return;
      if (action === "cancel") { state.freightEditor = null; this.renderMaterialFeeFreightSurface(); }
      else if (action === "save") this.saveMaterialFeeFreightEditor();
      else if (action === "refresh") this.refreshMaterialFeeFreightEditor();
      else this.openMaterialFeeFreightEditor($button.attr("data-fee-key"), action);
    });
    this.$root.on("input change", "[data-mf-freight-editor] [data-freight-field],[data-mf-freight-editor] [data-freight-line]", (event) => {
      this.updateMaterialFeeFreightDraft($(event.currentTarget));
    });
    this.$root.on("click", "[data-action='mf-link-evidence']", (event) => {
      this.openMaterialFeeEvidenceDialog($(event.currentTarget).attr("data-fee-key"));
    });
    this.$root.on("click", "[data-action='mf-preview-evidence']", (event) => {
      const $button = $(event.currentTarget);
      const fileUrl = $button.attr("data-file-url");
      // 没有可预览对象时，这个按钮只负责把话说清楚：先选附件、再预览。
      // 提示语与按钮的悬停提示是同一句，不另起说法。
      if (!fileUrl) {
        frappe.show_alert({ message: ocTranslate("需在关联并解析凭证选择附件"), indicator: "orange" });
        return;
      }
      // 预览直接复用附件预览弹窗（图片内嵌、PDF/文本 iframe、其余给下载），
      // 不再为这一处另写一套打开逻辑。
      this.openOaAttachmentFilePreviewDialog?.(fileUrl, $button.attr("data-file-name"));
    });
    this.$root.on("change", "[data-mf-fee-status='1']", (event) => {
      this.changeMaterialFeeStatus($(event.currentTarget)).catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='mf-evidence-status']", (event) => {
      const $button = $(event.currentTarget);
      this.setMaterialFeeEvidenceStatus(
        $button.attr("data-evidence-name"),
        $button.attr("data-status")
      ).catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='mf-adjust-cost']", () => {
      this.openCostTrialAdjustment().catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='mf-import-wiki']", async () => {
      try {
        await this.openWikiMaterialImportDialog();
      } catch (error) { this.showError(error); }
    });
    this.$root.on("click", "[data-action='mf-recover-material-rows']", () => {
      this.previewMaterialRowRecovery().catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='mf-add-material']", () => {
      this.openAddMaterialDialog(this.detailState.batchName);
    });
    this.$root.on("click", "[data-action='mf-excluded-materials']", () => {
      this.openExcludedMaterialsDialog().catch((error) => this.showError(error));
    });
    this.$root.on("change", "[data-mf-packing-group-select]", (event) => {
      const key = String($(event.currentTarget).attr("data-mf-packing-group-select") || "");
      this.toggleMaterialSelection(key, $(event.currentTarget).prop("checked"));
      this.renderMaterialFeeWorkspacePreservingPosition();
    });
    this.$root.on("change", "[data-mf-page-select]", (event) => {
      this.toggleMaterialPageSelection($(event.currentTarget).prop("checked"));
      this.renderMaterialFeeWorkspacePreservingPosition();
    });
    this.$root.on("click", "[data-action='mf-create-packing-group']", () => {
      this.openMaterialPackingGroupDialog("create").catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='mf-edit-selected-packing-group']", () => {
      const context = this.materialSelectionContext();
      if (!context.actions.edit.enabled) return;
      this.openMaterialPackingGroupDialog("update", context.selectedGroupIds[0]).catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='mf-remove-selected-packing-groups']", () => {
      this.removeSelectedPackingGroups().catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='mf-exclude-selected']", () => {
      this.excludeSelectedMaterials().catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='mf-set-project']", () => {
      this.openProjectCollectionDialog().catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='mf-set-supplier']", () => {
      this.openSupplierDialog().catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='mf-open-project-picker']", (event) => {
      const itemName = $(event.currentTarget).attr("data-item-name");
      this.openProjectCollectionForItem(itemName).catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='mf-open-supplier-picker']", (event) => {
      const itemName = $(event.currentTarget).attr("data-item-name");
      this.openSupplierForItem(itemName).catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='mf-select-page']", () => {
      if (!this.materialSelectionContext().actions.selectPage.enabled) return;
      this.toggleMaterialPageSelection(!this.materialPageSelectionState().checked);
      this.renderMaterialFeeWorkspacePreservingPosition();
    });
    this.$root.on("click", "[data-action='mf-clear-selection']", () => {
      this.clearMaterialSelection();
    });
    this.$root.on("click", "[data-action='mf-retry-cell']", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const $input = $(event.currentTarget).closest(".ocw-mf-cell").find("[data-mf-cell-input]").first();
      this.saveMaterialFeeCell($input).catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='mf-ai-fill']", () => {
      const fill = this.ensureMaterialFeeState().aiFill;
      if (["STARTING", "PAYMENT_SELECTION", "QUEUED", "RUNNING", "READY", "READY_WITH_WARNINGS", "FAILED", "STALE"].includes(String(fill?.status || ""))) {
        this.openMaterialAIProgressDialog();
        return;
      }
      this.startMaterialAIFill();
    });
    this.$root.on("click", "[data-action='mf-ai-progress-restore']", () => this.openMaterialAIProgressDialog());
    this.$root.on("click", "[data-action='mf-ai-apply']", () => {
      this.applyMaterialAIFill().catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='mf-ai-discard']", () => {
      this.discardMaterialAIFill().catch((error) => this.showError(error));
    });
    this.$root.on("click", "[data-action='mf-ai-open-candidate']", (event) => {
      event.preventDefault();
      event.stopPropagation();
      this.openMaterialAICandidatePopover($(event.currentTarget));
    });
    this.$root.on("click", "[data-action='mf-ai-adopt-candidate']", (event) => {
      const $button = $(event.currentTarget);
      this.adoptMaterialAICandidate(
        $button.attr("data-item-name"),
        $button.attr("data-fieldname"),
        Number($button.attr("data-candidate-index") || 0)
      );
      this.closeMaterialAICandidatePopover();
    });
    this.$root.on("click", "[data-action='mf-correct-purchase']", (event) => {
      const $button = $(event.currentTarget);
      this.openMaterialPurchaseCorrectionDialog(
        $button.attr("data-item-name"),
        $button.attr("data-fieldname")
      );
    });
    this.$root.on("mousedown", "[data-action='mf-shipment-valuation-adopt']", (event) => {
      event.preventDefault();
    });
    this.$root.on("click", "[data-action='mf-shipment-valuation-adopt']", (event) => {
      this.adoptShipmentValuationCandidate($(event.currentTarget)).catch((error) => this.showError(error));
    });
    this.$root.on("input", "[data-mf-ai-clarification]", (event) => {
      const state = this.ensureMaterialFeeState();
      state.aiClarification = String($(event.currentTarget).val() || "").slice(0, 4000);
      state.aiClarificationDirty = state.aiClarification.trim() !== state.aiClarificationSaved;
      state.aiClarificationStatus = state.aiClarificationSavePromise ? "saving" : (state.aiClarificationDirty ? "unsaved" : "saved");
      state.aiClarificationError = "";
      this.updateMaterialAIClarificationStatus();
    });
    this.$root.on("click", "[data-action='mf-ai-clarification-save']", () =>
      this.saveMaterialAIClarification().catch((error) => this.showError(error)));
    this.$root.on("click", "[data-action='mf-ai-clarification-rerun']", () =>
      this.reanalyzeMaterialAIClarification().catch((error) => this.showError(error)));
    this.$root.on("change", "[data-mf-ai-proposal-select]", (event) => {
      const fill = this.ensureMaterialFeeState().aiFill;
      if (!fill?.selections) return;
      const proposalId = String($(event.currentTarget).attr("data-proposal-id") || "");
      if ($(event.currentTarget).prop("checked")) fill.selections.add(proposalId);
      else fill.selections.delete(proposalId);
      this.renderMaterialFeeWorkspacePreservingPosition();
    });
    this.$root.on("input", "[data-mf-ai-edit]", (event) => {
      this.updateSourceAIReviewEdit($(event.currentTarget));
    });
    this.$root.on("change", "select[data-mf-ai-edit]", (event) => {
      this.updateSourceAIReviewEdit($(event.currentTarget));
    });
    this.$root.on("focus", "[data-mf-fee-input]", (event) => {
      const $input = $(event.currentTarget);
      const state = this.ensureMaterialFeeState();
      state.focusedFeeInput = {
        feeKey: String($input.attr("data-fee-key") || ""),
        field: String($input.attr("data-mf-fee-input") || ""),
      };
    });
    this.$root.on("input", "[data-mf-fee-input]", (event) => {
      this.updateMaterialFeeDraftFromInput($(event.currentTarget));
    });
    this.$root.on("change", "select[data-mf-fee-currency]", (event) => {
      this.updateMaterialFeeDraftFromInput($(event.currentTarget));
      this.saveMaterialFeeInlineAmount($(event.currentTarget)).catch((error) => this.showError(error));
    });
    this.$root.on("input", "[data-mf-cell-input]", (event) => {
      this.updateMaterialDraftFromInput($(event.currentTarget));
    });
    this.$root.on("change", "select[data-mf-cell-input]", (event) => {
      this.updateMaterialDraftFromInput($(event.currentTarget));
      this.saveMaterialFeeCell($(event.currentTarget)).catch((error) => this.showError(error));
    });
    this.$root.on("keydown", "[data-mf-fee-input]", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      event.currentTarget.blur();
    });
    this.$root.on("blur", "[data-mf-fee-input]", (event) => {
      const $input = $(event.currentTarget);
      const feeKey = typeof $input.attr === "function" ? String($input.attr("data-fee-key") || "") : "";
      const $related = event.relatedTarget ? $(event.relatedTarget) : null;
      if (
        $related
        && typeof $related.attr === "function"
        && $related.attr("data-mf-fee-input")
        && String($related.attr("data-fee-key") || "") === feeKey
      ) return;
      const state = this.ensureMaterialFeeState();
      const field = typeof $input.attr === "function" ? String($input.attr("data-mf-fee-input") || "") : "";
      if (state.focusedFeeInput?.feeKey === feeKey && state.focusedFeeInput?.field === field) {
        state.focusedFeeInput = null;
      }
      this.saveMaterialFeeInlineAmount($input).catch((error) => this.showError(error));
    });
    this.$root.on("focus", "[data-mf-cell-input]", (event) => {
      const $input = $(event.currentTarget);
      this.ensureMaterialFeeState().focusedMaterialInput = {
        itemName: $input.attr("data-item-name"),
        fieldname: $input.attr("data-fieldname"),
      };
      $(event.currentTarget).closest(".ocw-mf-cell").removeClass("is-save-error").attr("title", "");
    });
    this.$root.on("keydown", "[data-mf-cell-input]", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      event.currentTarget.blur();
    });
    this.$root.on("blur", "[data-mf-cell-input]", (event) => {
      const $input = $(event.currentTarget);
      const state = this.ensureMaterialFeeState();
      if (state.focusedMaterialInput?.itemName === $input.attr("data-item-name") && state.focusedMaterialInput?.fieldname === $input.attr("data-fieldname")) state.focusedMaterialInput = null;
      this.saveMaterialFeeCell($(event.currentTarget)).catch((error) => this.showError(error));
    });
    this.$root.on("paste", "[data-mf-cell-input]", (event) => {
      const clipboard = event.originalEvent?.clipboardData?.getData("text/plain") || "";
      if (!clipboard.includes("\t") && !clipboard.includes("\n") && !clipboard.includes("\r")) return;
      event.preventDefault();
      this.previewMaterialPaste($(event.currentTarget), clipboard);
    });
    if (typeof document !== "undefined") {
      $(document).off("click.ocwMfCandidate").on("click.ocwMfCandidate", (event) => {
        if ($(event.target).closest("[data-mf-ai-candidate-popover], [data-action='mf-ai-open-candidate']").length) return;
        this.closeMaterialAICandidatePopover();
      });
      $(document).off("keydown.ocwMfCandidate").on("keydown.ocwMfCandidate", (event) => {
        if (event.key === "Escape") this.closeMaterialAICandidatePopover();
      });
    }
  }

  invalidateMaterialAIClarification(note) {
    const state = this.ensureMaterialFeeState();
    if (Number(note.revision || 0) > Number(state.aiFill?.clarification_revision ?? state.aiClarificationRevision ?? 0) && state.aiFill
        && ["STARTING", "PAYMENT_SELECTION", "QUEUED", "RUNNING", "READY", "READY_WITH_WARNINGS"].includes(state.aiFill.status)) {
      state.aiRunGeneration = Number(state.aiRunGeneration || 0) + 1;
      state.aiFill = { ...state.aiFill, status: "STALE", draftVisible: false, stale: true };
      state.aiPendingReady = null;
      state.aiStartPromise = null;
    }
  }

  acceptMaterialAIClarification(note) {
    const state = this.ensureMaterialFeeState();
    if (!note || Number(note.revision || 0) < Number(state.aiClarificationRevision || 0)) return;
    this.invalidateMaterialAIClarification(note);
    if (state.aiClarificationDirty || state.aiClarificationSavePromise) return;
    state.aiClarification = String(note.text || "");
    state.aiClarificationSaved = state.aiClarification;
    state.aiClarificationRevision = Number(note.revision || 0);
    state.aiClarificationLoaded = true;
    state.aiClarificationStatus = "saved";
    state.aiClarificationError = "";
  }

  materialAIClarificationStatusText() {
    const state = this.ensureMaterialFeeState();
    return ocTranslate({ unsaved: "未保存", saving: "保存中…", saved: "已保存", failed: "保存失败" }[state.aiClarificationStatus] || "未保存");
  }

  renderMaterialAIClarification() {
    const state = this.ensureMaterialFeeState();
    const busy = Boolean(state.aiClarificationSavePromise || state.aiClarificationRerunPromise);
    return `<div class="ocw-mf-ai-clarification"><label><span>${ocTranslate("告诉 AI 如何理解")}</span><input data-mf-ai-clarification="1" value="${this.escape(state.aiClarification || "")}" maxlength="4000" placeholder="${ocTranslate('例如：两款是一套，共四套；每种数量相同')}" /></label><div class="ocw-mf-ai-clarification-actions"><span data-mf-ai-clarification-status="${state.aiClarificationStatus}" role="status" title="${this.escape(state.aiClarificationError || "")}">${this.materialAIClarificationStatusText()}</span><button type="button" class="ocw-outline-btn" data-action="mf-ai-clarification-save" ${busy ? "disabled" : ""}>${ocTranslate("保存说明")}</button><button type="button" class="ocw-outline-btn" data-action="mf-ai-clarification-rerun" ${busy ? "disabled" : ""}>${ocTranslate("按说明重新分析")}</button></div></div>`;
  }

  updateMaterialAIClarificationStatus() {
    if (!this.$root?.find) return;
    const state = this.ensureMaterialFeeState();
    this.$root.find("[data-mf-ai-clarification-status]").text(this.materialAIClarificationStatusText())
      .attr("data-mf-ai-clarification-status", state.aiClarificationStatus)
      .attr("title", state.aiClarificationError || "");
    this.$root.find("[data-action='mf-ai-clarification-save'], [data-action='mf-ai-clarification-rerun']")
      .prop("disabled", Boolean(state.aiClarificationSavePromise || state.aiClarificationRerunPromise));
  }

  saveMaterialAIClarification() {
    const state = this.ensureMaterialFeeState();
    if (state.aiClarificationSavePromise) return state.aiClarificationSavePromise;
    const submittedText = String(state.aiClarification || "").trim();
    const expectedRevision = Number(state.aiClarificationRevision || 0);
    state.aiClarificationStatus = "saving";
    state.aiClarificationError = "";
    const savePromise = (async () => {
      try {
        const result = await this.call("overseas_costing.api.materials.save_source_ai_clarification", {
          batch_name: state.batchName, clarification_text: submittedText, expected_revision: expectedRevision,
        }, false);
        if (this.materialFeeState !== state) return false;
        if (!result?.ok) {
          if (result?.conflict && result.clarification) {
            this.invalidateMaterialAIClarification(result.clarification);
            state.aiClarificationRevision = Number(result.clarification.revision || 0);
            state.aiClarificationSaved = String(result.clarification.text || "");
          }
          throw new Error(result?.message || "说明保存失败，请重试。");
        }
        const note = result.clarification;
        this.invalidateMaterialAIClarification(note);
        state.aiClarificationSaved = String(note.text || "");
        state.aiClarificationRevision = Number(note.revision || 0);
        state.aiClarificationLoaded = true;
        state.aiClarificationDirty = String(state.aiClarification || "").trim() !== state.aiClarificationSaved;
        if (!state.aiClarificationDirty) state.aiClarification = state.aiClarificationSaved;
        state.aiClarificationStatus = state.aiClarificationDirty ? "unsaved" : "saved";
        this.updateMaterialAIProgressSurface();
        return result;
      } catch (error) {
        if (this.materialFeeState === state) {
          state.aiClarificationStatus = "failed";
          state.aiClarificationDirty = true;
          state.aiClarificationError = error.message || "说明保存失败，请重试。";
        }
        throw error;
      } finally {
        state.aiClarificationSavePromise = null;
        if (this.materialFeeState === state) this.updateMaterialAIClarificationStatus();
      }
    })();
    state.aiClarificationSavePromise = savePromise;
    this.updateMaterialAIClarificationStatus();
    return savePromise;
  }

  reanalyzeMaterialAIClarification() {
    const state = this.ensureMaterialFeeState();
    if (state.aiClarificationRerunPromise) return state.aiClarificationRerunPromise;
    const rerun = (async () => {
      const saved = await this.saveMaterialAIClarification();
      if (!saved || this.materialFeeState !== state) return;
      if (state.aiClarificationDirty) throw new Error(ocTranslate("说明仍有未保存修改，请保存后重新分析。"));
      // Retire the old polling generation even when its start promise is still pending.
      state.aiRunGeneration = Number(state.aiRunGeneration || 0) + 1;
      state.aiStartPromise = null;
      return this.startMaterialAIFill({ force: false, restart: true });
    })().finally(() => {
      state.aiClarificationRerunPromise = null;
      if (this.materialFeeState === state) this.updateMaterialAIClarificationStatus();
    });
    state.aiClarificationRerunPromise = rerun;
    return rerun;
  }

  async loadMaterialFeeWorkspace(options = {}) {
    const state = this.ensureMaterialFeeState();
    const batch = this.getDetailBatch();
    const batchName = String(batch.name || this.detailState.batchName || "");
    const requestedVersion = this.detailState.versionName;
    const versionName = this.detailState.versionName || batch.current_version || null;
    const requestId = ++state.requestId;
    state.loading = true;
    state.cacheChecking = false;
    if (!options.quiet) this.renderDetailTabLoading("正在读取费用、凭证和物料表");
    try {
      const forceRefresh = Boolean(options.forceRefresh || state.cacheDirty);
      const endpoint = forceRefresh
        ? "overseas_costing.api.material_fee_workspace.refresh_snapshot"
        : "overseas_costing.api.material_fee_workspace.get_snapshot";
      const snapshot = await this.call(endpoint, {
        batch_name: batchName,
        version_name: versionName,
        page: state.page,
        page_length: state.pageLength,
      }, false, forceRefresh ? {} : { type: "GET" });
      if (!this.isMaterialFeeWorkspaceRequestCurrent(state, batchName, requestedVersion, requestId)) return false;
      this.applyMaterialFeeWorkspaceSnapshot(snapshot, state, batchName, batch.current_version || "");
      state.loading = false;
      this.renderMaterialFeeWorkspace();
      const activeVersion = this.detailState.versionName;
      const resolvedVersion = activeVersion || versionName;
      this.restoreMaterialFeeWorkspaceAI(state, batchName, resolvedVersion, requestId).catch((error) => {
        if (this.isMaterialFeeWorkspaceRequestCurrent(state, batchName, activeVersion, requestId)) {
          state.aiStatusError = this.normalizeErrorMessage?.(error) || error?.message || "AI 状态读取失败";
        }
      });
      if (!forceRefresh) this.checkMaterialFeeWorkspaceFreshness(
        state, batchName, resolvedVersion, activeVersion, requestId
      );
      return true;
    } catch (error) {
      if (
        requestId !== state.requestId
        || this.materialFeeState !== state
        || this.detailState.batchName !== batchName
        || this.detailState.versionName !== requestedVersion
        || this.detailState.tab !== "documents"
      ) return false;
      state.loading = false;
      this.renderDetailTabError("资料与费用", error);
      return false;
    }
  }

  isMaterialFeeWorkspaceRequestCurrent(state, batchName, requestedVersion, requestId) {
    return requestId === state.requestId
      && this.materialFeeState === state
      && this.detailState.batchName === batchName
      && this.detailState.versionName === requestedVersion
      && this.detailState.tab === "documents";
  }

  applyMaterialFeeWorkspaceSnapshot(snapshot, state, batchName, fallbackVersion = "") {
    if (!snapshot?.ok || !snapshot.data) throw new Error(snapshot?.message || "资料与费用快照读取失败");
    const data = snapshot.data;
    if (!data.detail || !data.materials || !data.fees || !data.preview) {
      throw new Error(ocTranslate("资料与费用快照不完整，请刷新后重试。"));
    }
    this.applyMaterialFeeHeaderSnapshot(data.detail, batchName);
    const loadedSelectionVersion = String(
      data.materials?.version_name || snapshot.version_name || this.detailState.versionName || fallbackVersion || ""
    );
    if (state.packingGroupSelectionVersion !== undefined
        && state.packingGroupSelectionVersion !== loadedSelectionVersion) {
      state.packingGroupSelections.clear();
    }
    state.packingGroupSelectionVersion = loadedSelectionVersion;
    state.materials = data.materials;
    state.fees = data.fees;
    state.preview = data.preview;
    state.settlementData = data.settlement || null;
    state.cache = { ...(snapshot.cache || {}) };
    state.cacheDirty = false;
    state.cacheChecking = false;
  }

  async restoreMaterialFeeWorkspaceAI(state, batchName, versionName, requestId) {
    const requestedVersion = this.detailState.versionName;
    const shouldRestoreAI = !state.aiFill;
    const restoreGeneration = state.aiRunGeneration || 0;
    state.aiStatusLoading = true;
    try {
      const result = shouldRestoreAI
        ? await this.call("overseas_costing.api.materials.get_source_ai_review_status", {
            batch_name: batchName,
            version_name: versionName,
            run_id: "",
          }, false)
        : await this.call("overseas_costing.api.materials.get_source_ai_clarification", {
            batch_name: batchName,
          }, false);
      if (!this.isMaterialFeeWorkspaceRequestCurrent(state, batchName, requestedVersion, requestId)) return false;
      const latestAI = shouldRestoreAI ? result : null;
      const savedClarification = shouldRestoreAI ? null : result;
      this.acceptMaterialAIClarification(savedClarification?.clarification || latestAI?.clarification);
      if (!state.aiFill && restoreGeneration === (state.aiRunGeneration || 0)
          && latestAI?.ok && latestAI.status && latestAI.status !== "NONE") {
        if (Number(latestAI.clarification_revision || 0) < Number(state.aiClarificationRevision || 0)
            && ["QUEUED", "RUNNING", "READY", "READY_WITH_WARNINGS"].includes(latestAI.status)) latestAI.status = "STALE";
        state.aiFill = { ...latestAI, runId: latestAI.run_id, draftVisible: false };
        state.aiPendingReady = this.isMaterialAIReadyStatus(latestAI.status) ? latestAI : null;
        this.renderMaterialFeeWorkspacePreservingPosition?.();
      }
      if (["QUEUED", "RUNNING"].includes(String(state.aiFill?.status || "")) && !state.aiFill.polling && !state.aiFill.polling_paused) {
        state.aiFill.polling = true;
        this.pollMaterialAIFill(state, batchName, versionName || "", state.aiFill.runId)
          .catch((error) => this.failMaterialAIProgress(error, "AI 分析状态读取失败，正在等待重试。"));
      }
      return true;
    } finally {
      state.aiStatusLoading = false;
    }
  }

  checkMaterialFeeWorkspaceFreshness(state, batchName, versionName, requestedVersion, requestId) {
    const inputRevision = state.inputRevision;
    state.cacheChecking = true;
    this.updateMaterialFeeCacheStatus();
    const isCurrent = () => this.isMaterialFeeWorkspaceRequestCurrent(state, batchName, requestedVersion, requestId)
      && state.inputRevision === inputRevision;
    return this.call("overseas_costing.api.material_fee_workspace.check_freshness", {
      batch_name: batchName,
      version_name: versionName,
      snapshot_fingerprint: state.cache?.input_fingerprint || "",
      page: state.page,
      page_length: state.pageLength,
    }, false, { type: "GET" }).then(async (freshness) => {
      if (!isCurrent()) return false;
      state.cacheChecking = false;
      if (freshness?.unchanged) {
        state.cache = {
          ...(state.cache || {}), status: "ready", refresh_error: "",
          last_checked_at: freshness.checked_at || state.cache?.last_checked_at || "",
          input_fingerprint: freshness.current_fingerprint || state.cache?.input_fingerprint || "",
        };
        this.updateMaterialFeeCacheStatus();
        return true;
      }
      state.cache = { ...(state.cache || {}), status: "stale", refresh_error: "" };
      this.updateMaterialFeeCacheStatus();
      const refreshed = await this.call("overseas_costing.api.material_fee_workspace.refresh_snapshot", {
        batch_name: batchName,
        version_name: versionName,
        page: state.page,
        page_length: state.pageLength,
      }, false);
      if (!isCurrent()) return false;
      this.applyMaterialFeeWorkspaceSnapshot(refreshed, state, batchName, versionName || "");
      this.renderMaterialFeeWorkspacePreservingPosition();
      return true;
    }).catch((error) => {
      if (!isCurrent()) return false;
      state.cacheChecking = false;
      state.cache = {
        ...(state.cache || {}), status: "stale",
        refresh_error: this.normalizeErrorMessage?.(error) || error?.message || "后台核对失败",
      };
      this.updateMaterialFeeCacheStatus();
      return false;
    });
  }

  materialFeeBasisLabel(value) {
    return {
      goods_value: ocTranslate("本次发货货值"),
      project_gross_weight: ocTranslate("按项目毛重及项目内毛重分摊"),
      gross_weight: ocTranslate("毛重"),
      volume: ocTranslate("体积"),
      chargeable_weight: ocTranslate("计费重"),
      direct: ocTranslate("指定物料直接承担"),
      zero_amount: ocTranslate("金额为 0，无需分摊"),
    }[String(value || "")] || String(value || "--");
  }

  isMaterialAIReadyStatus(status) {
    return ["READY", "READY_WITH_WARNINGS"].includes(String(status || ""));
  }

  materialAIReadyCopy(fillOrStatus) {
    const fill = typeof fillOrStatus === "object" && fillOrStatus !== null
      ? fillOrStatus
      : { status: fillOrStatus };
    const incomplete = ["PARTIAL", "UNAVAILABLE"].includes(String(fill.source_completeness || "").toUpperCase());
    if (String(fill.status || "") !== "READY_WITH_WARNINGS" && !incomplete) {
      return { title: ocTranslate("AI 资料草稿已生成"), step: String(fill.progress_step || "草稿已生成") };
    }
    const skipped = (Array.isArray(fill.source_progress) ? fill.source_progress : []).some(source => {
      const status = String(source?.status || "").toUpperCase();
      const readStatus = String(source?.read_status || "").toUpperCase();
      return ["SKIPPED", "FAILED"].includes(status) || ["SKIPPED", "UNREADABLE", "FAILED"].includes(readStatus);
    });
    if (skipped && String(fill.source_completeness || "") === "UNAVAILABLE") {
      return { title: ocTranslate("草稿已生成（未找到有效资料，部分资料已跳过）"), step: "未找到有效资料；部分资料已跳过" };
    }
    if (skipped) return { title: ocTranslate("草稿已生成（部分资料已跳过）"), step: "部分资料已跳过" };
    if (String(fill.source_completeness || "") === "UNAVAILABLE") {
      return { title: ocTranslate("草稿已生成（未找到可采用内容）"), step: "未找到可采用内容" };
    }
    return { title: ocTranslate("草稿已生成（部分资料待核对）"), step: "部分资料待核对" };
  }

  materialAIReadyTitle(fillOrStatus) {
    return this.materialAIReadyCopy(fillOrStatus).title;
  }

  materialAIReadyStep(fill) {
    return this.materialAIReadyCopy(fill).step;
  }

  materialAIReadyChipLabel(fill) {
    const title = this.materialAIReadyTitle(fill);
    return title === "AI 资料草稿已生成"
      ? ocTranslate("AI 草稿待查看")
      : title.replace(/^草稿已生成/, "AI 草稿待查看");
  }

  materialFeeCurrencyOptions() {
    return [
      { value: "RMB", label: ocTranslate("人民币（RMB）") },
      { value: "MXN", label: ocTranslate("比索（MXN）") },
      { value: "USD", label: ocTranslate("美金（USD）") },
    ];
  }

  normalizeMaterialFeeCurrency(value) {
    const currency = String(value ?? "RMB").trim().toUpperCase();
    return currency === "CNY" ? "RMB" : currency;
  }

  materialFeeAmountStatus(value) {
    return {
      MISSING: { label: ocTranslate("待补"), tone: "danger" },
      ESTIMATED: { label: ocTranslate("暂估"), tone: "warn" },
      ACTUAL: { label: ocTranslate("实际"), tone: "ok" },
      NOT_INCURRED: { label: ocTranslate("未发生"), tone: "neutral" },
      INCLUDED: { label: ocTranslate("已包含"), tone: "neutral" },
    }[String(value || "MISSING").toUpperCase()] || { label: String(value || "待补"), tone: "danger" };
  }

  /**
   * 凭证徽标。第二个参数是金额状态：「实际」是已核实的终态，凭证仍然要在最终确认前补齐
   * （凭证要求、待办与计数一律不变），但不再用红色「待补」把已核实的一行显示成报错。
   */
  materialFeeEvidenceLabel(value, amountStatus = "") {
    const state = String(value || "MISSING").toUpperCase();
    if (state === "MISSING" && String(amountStatus || "").toUpperCase() === "ACTUAL") {
      return { label: ocTranslate("凭证可稍后补充"), tone: "neutral" };
    }
    return {
      VALID: { label: ocTranslate("已核对"), tone: "ok" },
      PENDING: { label: ocTranslate("已关联"), tone: "info" },
      INVALID: { label: ocTranslate("需重补"), tone: "danger" },
      MISSING: { label: ocTranslate("待补"), tone: "danger" },
      NOT_REQUIRED: { label: ocTranslate("无需凭证"), tone: "neutral" },
    }[state] || { label: String(value || "待补"), tone: "danger" };
  }

  materialFeeEvidenceFinalLabel(value) {
    return {
      VALID: ocTranslate("已确认有效"),
      PENDING: ocTranslate("待核对"),
      INVALID: ocTranslate("已标记无效"),
    }[String(value || "PENDING").toUpperCase()] || String(value || "待核对");
  }

  renderMaterialFeeWorkspace() {
    const state = this.ensureMaterialFeeState();
    if (!state.materials || !state.fees || !state.preview) return;
    this.closeMaterialAICandidatePopover();
    const materialSummary = state.materials || {};
    const hasSettlementCargo = (materialSummary.items || []).some((item) => item.settlement_cargo);
    const blockingPackingGroups = (materialSummary.packing_groups || []).filter(group => group.blocking || group.status === "needs_reconfirmation");
    const feeSummary = state.fees.summary || {};
    const evidencePending = Number(feeSummary.missing_evidence_fee_count || 0);
    const aiActive = ["STARTING", "PAYMENT_SELECTION", "QUEUED", "RUNNING", "READY", "READY_WITH_WARNINGS"].includes(String(state.aiFill?.status || ""));
    const $content = this.$root.find("[data-area='detail-content']");
    $content.html(`
      <div class="ocw-mf-workspace">
        <div class="ocw-detail-section-head ocw-mf-page-head">
          <div><span>${ocTranslate("资料与费用")}</span><h2>${ocTranslate("综合成本资料工作区")}</h2><p>${ocTranslate("没有装箱计划也可以先用 OA 资料预览；正式推送前再补齐红色缺项和未定费用。")}</p>${this.renderMaterialFeeCacheStatus()}</div>
          <div class="ocw-detail-section-actions">
            <button class="ocw-outline-btn" type="button" data-action="mf-reload">${ocTranslate("刷新")}</button>
          </div>
        </div>
        ${feeSummary.source_pending ? `<p class="ocw-mf-dialog-note">${this.escape(feeSummary.source_message)}</p>` : ""}
        <div class="ocw-mf-alert-strip" aria-label="${ocTranslate('当前待办摘要')}">
          ${this.renderMaterialFeeMetric("基础资料待补", materialSummary.missing_cell_count || 0, "danger", "materials", "装箱单物料信息")}
          ${this.renderMaterialFeeMetric("费用金额待补", feeSummary.missing_amount_fee_count || 0, "danger", "fees", "运费、税费、清关费等运输费用")}
          ${this.renderMaterialFeeMetric("费用凭证未齐", evidencePending, "warn", "fees", "运费、税费、关税等费用凭证")}
          ${this.renderMaterialFeeMetric("实际费用待确认", feeSummary.estimated_fee_count || 0, "warn", "fees", "当前使用暂估金额，等待确认实际金额")}
        </div>
        <section class="ocw-mf-section ocw-mf-material-section" data-mf-status-section="materials" tabindex="-1">
          <div class="ocw-mf-section-title ocw-mf-material-title">
            <div><span>01</span><h3>${ocTranslate("物料与装箱数据")}</h3><p>${ocTranslate("采购标识与数量保持只读；缺失的采购金额、币种和单位可直接补录。{0}", [hasSettlementCargo ? ocTranslate("发货数量按结算采购支出采用，原装箱数量单独保留。") : ocTranslate("蓝色发货数量默认等于采购数量。")])}</p></div>
          </div>
          <div class="ocw-mf-material-toolrow" aria-label="${ocTranslate('物料资料与视图工具')}">
            <div class="ocw-mf-material-toolgroup is-view">
              <span class="ocw-mf-material-tool-label">${ocTranslate("视图")}</span>
              <button class="ocw-outline-btn ocw-mf-tool-quiet ${state.onlyMissing ? "is-active" : ""}" type="button" data-action="mf-toggle-missing">${ocTranslate("只看缺项")}</button>
              <button class="ocw-outline-btn ocw-mf-tool-quiet ${state.showAuxiliary ? "is-active" : ""}" type="button" data-action="mf-toggle-aux">${ocTranslate("展开辅助列")}</button>
              <button class="ocw-outline-btn ocw-mf-tool-quiet" type="button" data-action="mf-excluded-materials">${ocTranslate("已排除物料")}</button>
            </div>
            <div class="ocw-mf-material-toolgroup is-data">
              <span class="ocw-mf-material-tool-label">${ocTranslate("资料")}</span>
              <button class="ocw-outline-btn" type="button" data-action="mf-import-wiki">${ocTranslate("获取装箱资料")}</button>
              <button class="ocw-outline-btn ocw-mf-tool-quiet" type="button" data-action="mf-recover-material-rows">${ocTranslate("恢复误删物料")}</button>
              ${this.renderMaterialAIProgressChip()}
              <button class="ocw-primary-btn" type="button" data-action="mf-ai-fill">${aiActive ? (this.isMaterialAIReadyStatus(state.aiFill?.status) ? ocTranslate("查看填充预览") : ocTranslate("查看填充进度")) : ocTranslate("AI填充资料")}</button>
            </div>
          </div>
          ${blockingPackingGroups.length ? `<div class="ocw-mf-dialog-note"><strong>${ocTranslate("装箱组待重新确认")}</strong><span>${ocTranslate("组内物料曾被删除或恢复，试算已阻止。请勾选完整装箱组后从顶部操作条处理。")}</span></div>` : ""}
          ${this.materialReadonlyNoteHtml()}
          ${this.renderMaterialAIClarification()}
          ${this.renderMaterialSelectionToolbar()}
          ${this.renderMaterialFeeGrid()}
          <div class="ocw-mf-ai-candidate-popover" data-mf-ai-candidate-popover="1" role="dialog" aria-label="${ocTranslate('AI 候选详情')}" hidden></div>
        </section>
        <section class="ocw-mf-section ocw-mf-fee-section" data-mf-status-section="fees" tabindex="-1">
          <div class="ocw-mf-section-title"><div><span>02</span><h3>${ocTranslate("费用与凭证")}</h3><p>${ocTranslate("录入金额后自动保存；凭证可稍后补充，系统会在 SKU 试算时统一分摊。")}</p></div></div>
          <div class="ocw-mf-fee-layout">
            <div class="ocw-mf-fee-table-wrap">${this.renderMaterialFeeTable(state.fees.fees || state.fees.items || [])}</div>
          </div>
        </section>
        ${this.renderMaterialFeeCostTable()}
        ${this.renderMaterialFeeTodos()}
      </div>
    `);
    this.bindMaterialGridScrollControls();
    this.syncMaterialPageCheckboxState();
    this.restoreMaterialFeeInputFocus();
  }

  renderMaterialFeeMetric(label, value, tone, target, description) {
    const number = Number(value || 0);
    const cleared = number === 0;
    const className = cleared ? "is-cleared" : `is-${this.escape(tone)}`;
    const hidden = cleared ? ' aria-hidden="true"' : "";
    const status = cleared ? ocTranslate("已处理") : ocTranslate("点击查看");
    const accessible = `${ocTranslate(label)}: ${cleared ? status : ocTranslate("{0} 项待处理", [number])}; ${ocTranslate(description)}`;
    return `<button class="ocw-mf-metric ${className}" type="button" data-action="mf-jump-status" data-target="${this.escape(target)}" aria-label="${this.escape(accessible)}"><span>${this.escape(ocTranslate(label))}</span><strong${hidden}>${cleared ? "✓" : this.escape(String(number))}</strong><em>${status}</em><small>${this.escape(ocTranslate(description))}</small></button>`;
  }

  jumpToMaterialFeeSection(target) {
    const selector = {
      materials: "[data-mf-status-section='materials']",
      fees: "[data-mf-status-section='fees']",
    }[String(target || "")];
    if (!selector) return;
    const element = this.$root.find(selector)?.[0];
    if (!element) return;
    element.scrollIntoView({ behavior: "smooth", block: "start" });
    element.focus({ preventScroll: true });
  }

  materialFeeCacheStatus() {
    const state = this.ensureMaterialFeeState();
    const cache = state.cache || {};
    if (cache.refresh_error) return {
      status: "error", text: ocTranslate("快照核对失败·正在显示上次可信数据"), title: cache.refresh_error,
    };
    if (cache.status === "stale") return { status: "stale", text: ocTranslate("本地快照可能较旧·正在后台更新"), title: "" };
    if (state.cacheChecking || cache.status === "refreshing") {
      return { status: "checking", text: ocTranslate("本地快照已显示·正在后台核对"), title: "" };
    }
    if (cache.last_checked_at) return { status: "ready", text: ocTranslate("本地快照·已核对"), title: cache.last_checked_at };
    return { status: "ready", text: ocTranslate("本地快照·快速载入"), title: cache.generated_at || "" };
  }

  renderMaterialFeeCacheStatus() {
    const status = this.materialFeeCacheStatus();
    return `<small class="ocw-mf-cache-status" data-mf-cache-status="${this.escape(status.status)}" role="status" title="${this.escape(ocTranslate(status.title))}">${this.escape(status.text)}</small>`;
  }

  updateMaterialFeeCacheStatus() {
    if (!this.$root?.find) return;
    const status = this.materialFeeCacheStatus();
    const $status = this.$root.find("[data-mf-cache-status]");
    $status?.attr?.("data-mf-cache-status", status.status);
    $status?.attr?.("title", status.title);
    $status?.text?.(status.text);
  }

  async openExcludedMaterialsDialog() {
    const result = await this.call("overseas_costing.api.materials.get_excluded_materials", {
      batch_name: this.detailState.batchName,
      version_name: this.detailState.versionName || null,
    }, true);
    if (!result?.ok) throw new Error(result?.message || "已排除物料读取失败");
    const rows = result.items || [];
    const html = rows.length
      ? `<div class="ocw-mf-excluded-list">${rows.map((item) => `<div><span><strong>${this.escape(item.material_code || "--")}</strong> ${this.escape(item.product_name || "")}</span><small>${this.escape(item.exclusion_reason || ocTranslate("未填写原因"))} · ${this.escape(item.excluded_by || "--")}</small><button type="button" class="ocw-outline-btn ocw-mini-btn" data-action="mf-restore-excluded" data-item-name="${this.escape(item.name)}">${ocTranslate("恢复")}</button></div>`).join("")}</div>`
      : `<div class="ocw-detail-empty"><strong>${ocTranslate("当前没有已排除物料")}</strong></div>`;
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("已排除物料"),
      fields: [{ fieldtype: "HTML", fieldname: "rows", options: html }],
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-mf-dialog");
    dialog.$wrapper.on("click", "[data-action='mf-restore-excluded']", async (event) => {
      const $button = $(event.currentTarget);
      $button.prop("disabled", true);
      try {
        if (!(await this.ensureMaterialFeeEditSession())) return;
        const restored = await this.call("overseas_costing.api.calculate.restore_item", {
          item_name: $button.attr("data-item-name"),
          batch_name: this.detailState.batchName,
          version_name: this.detailState.versionName || null,
          remark: "前端恢复已排除物料",
          edit_token: this.detailState.editToken,
          expected_modified: this.detailState.expectedModified,
        }, true);
        if (!restored?.ok) throw new Error(restored?.message || "物料恢复失败");
        this.updateMaterialFeeExpectedModified(restored);
        dialog.hide();
        await this.loadMaterialFeeWorkspace({ quiet: true });
        frappe.show_alert({ message: restored.message || ocTranslate("物料已恢复"), indicator: "green" });
      } catch (error) {
        $button.prop("disabled", false);
        this.showError(error);
      }
    });
  }

  renderMaterialFeeTable(fees) {
    if (!fees.length) return `<div class="ocw-detail-empty"><strong>${ocTranslate("暂无费用清单")}</strong></div>`;
    return `
      <table class="ocw-mf-fee-table">
        <thead><tr><th>${ocTranslate("费用项目")}</th><th>${ocTranslate("状态")}</th><th>${ocTranslate("原币金额")}</th><th>${ocTranslate("凭证")}</th><th>${ocTranslate("操作")}</th></tr></thead>
        <tbody>${fees.map((fee) => this.renderMaterialFeeRow(fee)).join("")}</tbody>
      </table>
    `;
  }

  renderMaterialFeeRow(fee) {
    if (fee.duplicate_rule_names?.length) {
      const evidence = fee.evidence || [];
      return `<tr class="is-review">
        <td><strong>${this.escape(fee.expense_category || fee.logical_fee_key || ocTranslate("费用"))}</strong><small>${ocTranslate("记录：{0}", [this.escape(fee.name || ocTranslate("未命名"))])}</small></td>
        <td><span class="ocw-mf-badge is-danger">${ocTranslate("费用重复 · 未计入")}</span><small>${this.escape(ocTranslate(this.materialFeeAmountStatus(fee.amount_state || fee.amount_status).label))}</small></td>
        <td>${this.escape(fee.currency || "RMB")} ${this.escape(fee.amount ?? ocTranslate("未填写"))}<small>${ocTranslate("请先核对并停用重复记录，再保存或试算。")}</small></td>
        <td>${evidence.map((row) => `<span>${this.escape(row.evidence_role || ocTranslate("凭证"))} · ${this.escape(row.attachment || row.name || "")}</span>`).join("") || ocTranslate("暂无关联凭证")}</td>
        <td><strong>${ocTranslate("冲突记录")}</strong><small>${fee.duplicate_rule_names.map((name) => this.escape(name)).join("、")}</small>${this.renderReviewFeedbackButton?.({ target_tab: "documents", target_field: String(fee.logical_fee_key || fee.fee_key || "") }) || ""}</td>
      </tr>`;
    }
    const amountInfo = this.materialFeeAmountStatus(fee.amount_state || fee.amount_status);
    const scopeLabel = String(fee.scope_type || "ALL_ITEMS") === "ALL_ITEMS" ? "全批物料" : String(fee.scope_type) === "DIRECT_ITEM" ? "指定单行" : "指定物料";
    const amountStatus = String(fee.amount_state || fee.amount_status || "MISSING").toUpperCase();
    // 凭证徽标要连金额状态一起判（「实际」不再用红色待补），所以排在 amountStatus 之后。
    const evidenceInfo = this.materialFeeEvidenceLabel(fee.evidence_state, amountStatus);
    const feeKey = String(fee.logical_fee_key || fee.fee_key || "");
    const sourceOwned = Boolean(fee.source_binding_id);
    const aiFill = this.materialFeeState?.aiFill;
    const aiFeeProposal = !sourceOwned && !aiFill?.row_review && this.isMaterialAIReadyStatus(aiFill?.status) && aiFill.draftVisible
      ? (aiFill.proposals || []).find((proposal) => proposal.proposal_type === "fee_update" && String(proposal.payload?.logical_fee_key || "") === feeKey)
      : null;
    const aiFeeEdit = aiFeeProposal ? aiFill.edits?.[String(aiFeeProposal.proposal_id || "")] : null;
    const aiFeeValues = aiFeeEdit || aiFeeProposal?.payload || null;
    const draft = sourceOwned || aiFeeProposal ? null : (this.materialFeeState?.feeDrafts?.[feeKey] || null);
    const amount = aiFeeValues ? aiFeeValues.amount : draft ? draft.amount : (fee.amount ?? "");
    const currency = this.normalizeMaterialFeeCurrency(aiFeeValues ? aiFeeValues.currency : draft ? draft.currency : fee.currency || "RMB");
    const currencyOptions = this.materialFeeCurrencyOptions();
    const supportedCurrency = currencyOptions.some((option) => option.value === currency);
    const savedPreview = this.materialFeeSavedCostPreview();
    const savedCurrent = savedPreview && this.detailState?.header?.status !== "Dirty"
      && !Object.keys(this.materialFeeState?.feeDrafts || {}).length
      && !Object.keys(this.materialFeeState?.materialDrafts || {}).length
      && !Object.keys(this.materialFeeState?.materialSaveErrors || {}).length;
    const preview = (savedCurrent ? savedPreview : this.materialFeeState?.preview) || {};
    const excludedFee = (preview.excluded_fees || []).find((row) => row.fee_key === feeKey);
    const inclusionLabel = aiFeeProposal ? "AI 草稿 · 未保存" : draft ? "修改待保存" : (preview.included_fees || []).some((row) => row.fee_key === feeKey) ? (savedCurrent ? "已计入试算" : "可计入 · 待试算") : excludedFee ? this.materialFeeExclusionReason(excludedFee) : "";
    const missingSavedAmount = amountStatus === "MISSING" && amount !== "";
    const defaultZero = !draft && fee.virtual && fee.is_default_zero && amountStatus === "ESTIMATED";
    const mexicoEntry = fee.entry_responsibility === "MEXICO";
    const forceActual = Boolean(draft?.forceActual || missingSavedAmount);
    const inlineError = String(draft?.error || "");
    const errorId = this.materialFeeErrorId(feeKey);
    const feeLabel = ocTranslate(String(fee.expense_category || feeKey || "费用"));
    const evidence = fee.evidence || [];
    const evidenceAttachmentCount = new Set(evidence.map((row) => row.attachment).filter(Boolean)).size || evidence.length;
    const evidenceFinancials = fee.evidence_financials || {};
    const settlementRows = Object.entries(evidenceFinancials.settlement_net_by_currency || {}).map(([code, value]) => `${code} ${this.formatMoney(value)}`);
    const finalRows = Object.entries(evidenceFinancials.final_bill_by_currency || {}).map(([code, value]) => `${code} ${this.formatMoney(value)}`);
    if (sourceOwned) {
      const sourceLabel = fee.source_label || "支付来源";
      const editor = this.materialFeeState?.freightEditor;
      const readonly = this.materialFeeState?.settlementData?.historical || this.detailState?.readOnly;
      return `<tr class="is-readonly" data-mf-source-fee="${this.escape(feeKey)}">
        <td><strong>${this.escape(feeLabel)}</strong><small>${ocTranslate("{0} · 最终来源费用", [this.escape(sourceLabel)])}</small></td>
        <td><span class="ocw-mf-badge is-${amountInfo.tone}">${this.escape(ocTranslate(amountInfo.label))}</span>${inclusionLabel ? `<small>${this.escape(ocTranslate(inclusionLabel))}</small>` : ""}</td>
        <td><small>${ocTranslate("当前采用金额")}</small><strong>${this.escape(currency)} ${this.escape(fee.applied_amount ?? amount)}</strong><small>${ocTranslate("原始证据金额：{0} {1}", [this.escape(currency), this.escape(fee.original_amount ?? ocTranslate("待核对"))])}</small></td>
        <td><span>${this.escape(sourceLabel)}</span><small>${this.escape(fee.source_approval_no || ocTranslate("来源与更正记录可追溯"))}</small></td>
        <td><div class="ocw-mf-row-actions">${readonly ? "" : ["amount", "replace", "revoke"].map((action, index) => `<button type="button" data-mf-freight-action="${action}" data-fee-key="${this.escape(feeKey)}" ${editor?.freightWriting ? "disabled" : ""}>${["改金额", "换来源", "撤销采用"][index]}</button>`).join("")}<button type="button" data-action="mf-view-settlement-source">${ocTranslate("查看支付来源与记录")}</button>${this.renderReviewFeedbackButton?.({ target_tab: "documents", target_field: feeKey }) || ""}</div>
          <details class="ocw-mf-row-details"><summary>${ocTranslate("范围与分摊说明")}</summary><div><span>${ocTranslate("适用：{0}", [this.escape(scopeLabel)])}</span><span>${ocTranslate("分摊：{0}", [this.escape(this.materialFeeBasisLabel(fee.allocation?.basis || fee.allocation_basis))])}</span>${inclusionLabel ? `<span>${this.escape(ocTranslate(inclusionLabel))}</span>` : ""}</div></details>
        </td>
      </tr>${this.renderMaterialFeeFreightEditor(feeKey)}`;
    }
    const statusOptions = [
      ["MISSING", "待补"], ["ESTIMATED", "暂估"], ["ACTUAL", "实际"],
      ["NOT_INCURRED", "未发生"], ["INCLUDED", "已包含"],
    ];
    return `
      <tr class="${[fee.legacy_unmapped || fee.requires_review ? "is-review" : "", aiFeeProposal ? "is-ai-draft" : ""].filter(Boolean).join(" ")}">
        <td><strong>${this.escape(ocTranslate(fee.expense_category || fee.logical_fee_key || "--"))}</strong><small>${fee.virtual ? ocTranslate("默认项 · 未入库") : fee.legacy_unmapped ? ocTranslate("历史费用 · 请核对") : ocTranslate("已保存")}</small>${mexicoEntry ? `<small>${ocTranslate("由墨西哥同事补充")}</small>` : ""}</td>
        <td><select data-mf-fee-status="1" data-fee-key="${this.escape(feeKey)}" data-original-value="${this.escape(amountStatus)}" aria-label="${this.escape(ocTranslate("{0}状态", [feeLabel]))}">${statusOptions.map(([value, label]) => `<option value="${value}" ${value === amountStatus ? "selected" : ""}>${ocTranslate(label)}</option>`).join("")}</select>${inclusionLabel ? `<small>${this.escape(ocTranslate(inclusionLabel))}</small>` : ""}</td>
        <td class="ocw-mf-fee-amount-cell ${inlineError ? "is-save-error" : ""}" ${inlineError ? `title="${this.escape(inlineError)}"` : ""}>
          <div class="ocw-mf-fee-inline-fields">
            ${aiFeeProposal
              ? `<select data-mf-ai-edit="1" data-proposal-id="${this.escape(aiFeeProposal.proposal_id || "")}" data-fieldname="currency" aria-label="${this.escape(ocTranslate("AI 草稿 {0}币种", [feeLabel]))}">${currencyOptions.map((option) => `<option value="${option.value}" ${option.value === currency ? "selected" : ""}>${option.label}</option>`).join("")}</select>
                 <input data-mf-ai-edit="1" data-proposal-id="${this.escape(aiFeeProposal.proposal_id || "")}" data-fieldname="amount" value="${this.escape(amount)}" inputmode="decimal" aria-label="${this.escape(ocTranslate("AI 草稿 {0}原币金额", [feeLabel]))}" />`
              : `<select data-mf-fee-input="currency" data-mf-fee-currency="1" data-fee-key="${this.escape(feeKey)}" data-original-value="${this.escape(this.normalizeMaterialFeeCurrency(fee.currency || "RMB"))}" aria-label="${this.escape(ocTranslate("{0}币种", [feeLabel]))}" aria-invalid="${inlineError ? "true" : "false"}" aria-describedby="${this.escape(errorId)}">${supportedCurrency ? "" : `<option value="" selected disabled>${ocTranslate("请选择币种（原 {0}）", [this.escape(currency || ocTranslate("未设置"))])}</option>`}${currencyOptions.map((option) => `<option value="${option.value}" ${option.value === currency ? "selected" : ""}>${option.label}</option>`).join("")}</select>
                 <input data-mf-fee-input="amount" data-mf-fee-amount="1" data-fee-key="${this.escape(feeKey)}" data-original-value="${this.escape(fee.amount ?? "")}" value="${this.escape(amount)}" ${forceActual ? 'data-mf-force-actual="1"' : ""} inputmode="decimal" aria-label="${this.escape(ocTranslate("{0}原币金额", [feeLabel]))}" aria-invalid="${inlineError ? "true" : "false"}" aria-describedby="${this.escape(errorId)}" />`}
          </div>
          <small data-mf-fee-amount-hint="1">${aiFeeProposal ? ocTranslate("AI 草稿 · 确认所选草稿后才会保存") : defaultZero ? ocTranslate("默认暂估 0，待墨西哥确认") : missingSavedAmount ? ocTranslate("首次金额默认暂估") : ocTranslate("Enter 或失焦自动保存；状态可单独调整")}</small>
          <small id="${this.escape(errorId)}" class="ocw-mf-fee-inline-error-text ${inlineError ? "is-visible" : ""}" data-mf-fee-error="1">${this.escape(inlineError)}</small>
        </td>
        <td><span class="ocw-mf-badge is-${evidenceInfo.tone}">${this.escape(ocTranslate(evidenceInfo.label))}</span><small>${evidenceAttachmentCount ? ocTranslate("{0} 份已关联", [evidenceAttachmentCount]) : ocTranslate("可上传或关联已有资料")}</small></td>
        <td><div class="ocw-mf-row-actions"><button type="button" data-action="mf-edit-fee" data-fee-key="${this.escape(fee.logical_fee_key || "")}">${ocTranslate("更多设置")}</button><button type="button" data-action="mf-link-evidence" data-fee-key="${this.escape(fee.logical_fee_key || "")}">${ocTranslate("关联并解析凭证")}</button>${this.renderReviewFeedbackButton?.({ target_tab: "documents", target_field: feeKey }) || ""}${this.renderMaterialFeeEvidencePreview(feeKey)}</div>
          <details class="ocw-mf-row-details"><summary>${ocTranslate("范围与凭证详情")}</summary><div><span>${ocTranslate("适用：{0}", [this.escape(scopeLabel)])}</span>${finalRows.length ? `<span>${ocTranslate("最终账单：{0}", [this.escape(finalRows.join("；"))])}</span>` : ""}${settlementRows.length ? `<span>${ocTranslate("已付款净额：{0}（不与账单重复计费）", [this.escape(settlementRows.join("；"))])}</span>` : ""}${evidence.length ? evidence.map((row) => `<span>${ocTranslate("{0} · {1} · {2} {3}{4} · 终核状态：{5}", [this.escape(row.evidence_role || ocTranslate("凭证")), this.escape(row.evidence_type || ocTranslate("待分类")), this.escape(row.currency || ""), this.escape(row.original_amount ?? ocTranslate("待补金额")), row.related_evidence ? ocTranslate(" · 原付款 {0}", [this.escape(row.related_evidence)]) : "", this.escape(this.materialFeeEvidenceFinalLabel(row.validation_status))])} <button type="button" data-action="mf-evidence-status" data-evidence-name="${this.escape(row.name || "")}" data-status="VALID">${ocTranslate("确认有效")}</button><button type="button" data-action="mf-evidence-status" data-evidence-name="${this.escape(row.name || "")}" data-status="INVALID">${ocTranslate("标记无效")}</button></span>`).join("") : `<span>${ocTranslate("暂无关联凭证")}</span>`}</div></details>
        </td>
      </tr>
    `;
  }

  materialFeeFreightEditorCurrent(editor) {
    return this.materialFeeState?.freightEditor === editor && this.detailState.batchName === editor.batchName
      && this.detailState.versionName === editor.versionName && this.detailState.tab === "documents";
  }

  renderMaterialFeeFreightSurface() {
    this.renderMaterialFeeWorkspacePreservingPosition();
  }

  async openMaterialFeeFreightEditor(feeKey, kind) {
    if (!["amount", "replace", "revoke"].includes(kind)) return;
    const state = this.ensureMaterialFeeState();
    if (state.freightEditor?.freightWriting) return;
    const fee = (state.fees?.fees || state.fees?.items || []).find(row => (row.logical_fee_key || row.fee_key) === feeKey);
    if (!fee?.source_binding_id) return;
    const editor = state.freightEditor = {feeKey, inline: true, batchName: state.batchName, versionName: this.detailState.versionName,
      freightLoading: true, freightWriting: false, freightError: "", data: {}, freightDraft: {}};
    this.renderMaterialFeeFreightSurface();
    try {
      const data = await this.call("overseas_costing.api.logistics_settlement.get_batch_settlement", {batch_name: editor.batchName, version_name: editor.versionName}, false);
      if (!this.materialFeeFreightEditorCurrent(editor)) return;
      if (!data?.ok) throw new Error(data?.message || "费用来源读取失败，请重试。");
      if (data.historical) throw new Error(ocTranslate("历史版本仅供追溯，请返回当前调整草稿更正。"));
      const claimId = String(fee.freight_claim_id || fee.source_binding_id);
      const claim = (data.freight?.claims || []).find(row => String(row.id) === claimId);
      if (!claim) throw new Error(ocTranslate("当前费用来源已变化，请刷新费用清单后重试。"));
      editor.data = data;
      state.settlementData = data;
      editor.freightView = {kind, claim_id: claim.id, expected_revision: data.freight.revision};
      editor.freightDraft = {amount: String(claim.applied_amount ?? claim.amount ?? ""), reason: "", candidate_id: "", line_ids: [], negative_confirmed: false};
    } catch (error) {
      if (this.materialFeeFreightEditorCurrent(editor)) editor.freightError = this.materialAIErrorMessage(error, "费用来源读取失败。");
    } finally {
      editor.freightLoading = false;
      if (this.materialFeeFreightEditorCurrent(editor)) this.renderMaterialFeeFreightSurface();
    }
  }

  renderMaterialFeeFreightEditor(feeKey) {
    const editor = this.materialFeeState?.freightEditor;
    if (!editor || editor.feeKey !== feeKey) return "";
    const busy = editor.freightLoading || editor.freightWriting;
    return `<tr class="ocw-mf-freight-editor-row" data-mf-freight-editor="${this.escape(feeKey)}"><td colspan="5"><div class="ocw-mf-freight-editor"><fieldset ${busy || editor.saved ? "disabled" : ""}>
      ${editor.freightLoading ? `<p role="status">${ocTranslate("正在读取本票费用来源…")}</p>` : editor.saved ? `<p>${ocTranslate("费用更正已保存。")}</p>` : editor.freightView ? this.renderFreightEditor(editor.data, editor) : ""}</fieldset>
      ${editor.freightError ? `<p class="ocw-mf-ai-review-error" role="alert">${this.escape(editor.freightError)}</p>` : ""}
      <div class="ocw-mf-row-actions"><button type="button" data-mf-freight-action="cancel" ${busy ? "disabled" : ""}>${editor.saved ? ocTranslate("关闭") : ocTranslate("取消")}</button>${editor.saved ? `<button type="button" data-mf-freight-action="refresh">${ocTranslate("重新读取已保存费用")}</button>` : editor.freightView ? `<button class="ocw-primary-btn" type="button" data-mf-freight-action="save" ${busy ? "disabled" : ""}>${editor.freightWriting ? ocTranslate("正在保存…") : {amount: "保存金额更正", replace: "保存并更换来源", revoke: "保存并撤销采用"}[editor.freightView.kind]}</button>` : ""}</div></div></td></tr>`;
  }

  updateMaterialFeeFreightDraft($input) {
    const editor = this.ensureMaterialFeeState().freightEditor;
    if (!editor || editor.freightWriting || editor.saved) return;
    const draft = editor.freightDraft;
    const field = $input.attr("data-freight-field");
    const lineId = $input.attr("data-freight-line");
    if (field) draft[field] = field === "negative_confirmed" ? Boolean($input.prop("checked")) : String($input.val() ?? "");
    if (lineId) draft.line_ids = $input.prop("checked") ? [...new Set([...(draft.line_ids || []), lineId])] : (draft.line_ids || []).filter(id => id !== lineId);
    editor.freightError = "";
    if (field === "candidate_id") { draft.line_ids = []; draft.negative_confirmed = false; this.renderMaterialFeeFreightSurface(); }
    else {
      const $host = this.$root.find("[data-mf-freight-editor]");
      if (!this.freightNeedsNegative(editor)) { draft.negative_confirmed = false; $host.find('[data-freight-field="negative_confirmed"]').prop("checked", false); }
      $host.find("[data-freight-negative]").prop("hidden", !this.freightNeedsNegative(editor));
      $host.find("[data-freight-summary]").html(this.renderFreightChangeSummary(editor));
      $host.find('[role="alert"]').text("");
    }
  }

  async refreshMaterialFeeFreightEditor() {
    const editor = this.ensureMaterialFeeState().freightEditor;
    if (!editor?.saved || editor.freightWriting || !this.materialFeeFreightEditorCurrent(editor)) return;
    editor.freightWriting = true;
    try {
      const loaded = await this.loadMaterialFeeWorkspace({quiet: true});
      if (loaded === false) throw new Error(ocTranslate("请刷新重试"));
      if (this.materialFeeState?.freightEditor === editor) this.materialFeeState.freightEditor = null;
    } catch (error) { editor.freightError = ocTranslate("已保存，新版本资料读取失败：{0}", [this.materialAIErrorMessage(error, "请刷新重试")]); }
    finally { editor.freightWriting = false; if (this.detailState.batchName === editor.batchName) this.renderMaterialFeeFreightSurface(); }
  }

  async saveMaterialFeeFreightEditor() {
    const state = this.ensureMaterialFeeState();
    const editor = state.freightEditor;
    if (!editor || editor.freightLoading || editor.freightWriting || editor.saved) return;
    editor.freightError = "";
    const draft = editor.freightDraft;
    const view = editor.freightView;
    try {
      if (!this.materialFeeFreightEditorCurrent(editor)) throw new Error(ocTranslate("当前批次或版本已变化，请重新打开费用更正。"));
      if (editor.data.historical || !view) throw new Error(ocTranslate("当前费用不可更正，请刷新后重试。"));
      const reason = String(draft.reason || "").trim();
      if (!reason) throw new Error(ocTranslate("请填写更正原因。"));
      const args = {batch_name: editor.batchName, version_name: editor.versionName, claim_id: view.claim_id,
        expected_revision: view.expected_revision, action: view.kind, reason};
      if (view.kind === "amount") {
        if (String(draft.amount ?? "").trim() === "" || !Number.isFinite(Number(draft.amount))) throw new Error(ocTranslate("请填写有效金额，可填 0。"));
        args.amount = String(draft.amount).trim();
      } else if (view.kind === "replace") {
        const {candidate, lines} = this.freightSelectedLines(editor);
        if (!candidate || !lines.length) throw new Error(ocTranslate("请选择新的来源及本票费用明细。"));
        if (candidate.expense?.approved === false || lines.some(line => !line.available || line.adopted)) throw new Error(ocTranslate("所选费用不可采用，请重新核对。"));
        Object.assign(args, {candidate_id: candidate.id, candidate_revision: candidate.revision, line_ids: JSON.stringify(lines.map(line => line.id))});
      }
      if (this.freightNeedsNegative(editor) && draft.negative_confirmed !== true) throw new Error(ocTranslate("请先确认已核对负数冲抵／折扣。"));
      if (["amount", "replace"].includes(view.kind)) args.negative_confirmed = this.freightNeedsNegative(editor) && draft.negative_confirmed === true;
      editor.freightWriting = true;
      this.renderMaterialFeeFreightSurface();
      const result = await this.call("overseas_costing.api.logistics_settlement.amend_freight_claim", args, false);
      if (!this.materialFeeFreightEditorCurrent(editor)) return;
      if (!result?.ok) throw new Error(result?.message || "费用更正未保存，请重试。");
      editor.saved = true;
      this.updateMaterialFeeExpectedModified(result);
      const version = result.version || result.version_name;
      if (version) { editor.versionName = version; this.detailState.versionName = version; }
      // Invalidate old detail and fee reads before reading the newly created version.
      state.requestId += 1; state.feeRequestId += 1; state.settlementData = null;
      try {
        const loaded = await this.loadMaterialFeeWorkspace({quiet: true});
        if (loaded === false) throw new Error(ocTranslate("请刷新重试"));
        if (this.materialFeeState?.freightEditor === editor) state.freightEditor = null;
      } catch (error) { editor.freightError = ocTranslate("已保存，新版本资料读取失败：{0}", [this.materialAIErrorMessage(error, "请刷新重试")]); }
    } catch (error) { editor.freightError = this.materialAIErrorMessage(error, "费用更正未保存，请重试。"); }
    finally { editor.freightWriting = false; if (this.detailState.batchName === editor.batchName) this.renderMaterialFeeFreightSurface(); }
  }

  materialFeeGridColumns() {
    const state = this.ensureMaterialFeeState();
    const columns = [
      { field: "__group_select", label: ocTranslate("选择"), readonly: true, width: 48 },
      { field: "row_no", label: ocTranslate("行"), readonly: true, width: 40 },
      { field: "material_code", label: ocTranslate("物料编码"), readonly: true, width: 100 },
      { field: "product_name", label: ocTranslate("物料名称"), readonly: true, width: 180 },
      { field: "quantity", label: ocTranslate("采购数量"), readonly: true, numeric: true, width: 130 },
      { field: "actual_shipped_qty", label: ocTranslate("发货数量"), numeric: true, width: 140 },
      { field: "shipped_uom", label: ocTranslate("发货单位"), width: 130 },
      { field: "shipment_value_rmb", label: ocTranslate("本次发货货值 RMB"), numeric: true, readonly: false, width: 180 },
      { field: "package_count", label: ocTranslate("箱数"), numeric: true, width: 90 },
      { field: "net_weight_kg", label: ocTranslate("净重 kg"), numeric: true, width: 120 },
      { field: "gross_weight_kg", label: ocTranslate("毛重 kg"), numeric: true, width: 130 },
      { field: "volume_m3", label: ocTranslate("体积 m³"), numeric: true, width: 130 },
      { field: "chargeable_weight_kg", label: ocTranslate("计费重 kg"), numeric: true, width: 140 },
      { field: "supplier", label: ocTranslate("供应商"), width: 220 },
      { field: "project_collection", label: ocTranslate("项目归属"), width: 180 },
    ];
    if (state.showAuxiliary) {
      columns.push(
        { field: "goods_value", label: ocTranslate("货值兼容值 RMB"), numeric: true, purchaseField: true, readonly: true, width: 150 },
        { field: "packaging_type", label: ocTranslate("包装类型"), width: 130 },
        { field: "unit_price", label: ocTranslate("原币单价"), numeric: true, purchaseField: true, width: 130 },
        { field: "purchase_uom", label: ocTranslate("采购单位"), purchaseField: true, width: 120 },
        { field: "unit_price_uom", label: ocTranslate("计价单位"), purchaseField: true, width: 120 },
        { field: "purchase_currency", label: ocTranslate("采购币种"), purchaseField: true, options: this.materialFeeCurrencyOptions(), width: 130 },
        { field: "source_file_name", label: ocTranslate("来源文件"), readonly: true, width: 240 }
      );
    }
    columns.push({ field: "source_doc_no", label: ocTranslate("采购审批号"), readonly: true, width: 220 });
    return columns;
  }

  materialPackingGroups() {
    const state = this.ensureMaterialFeeState();
    const groups = new Map();
    (state.materials?.packing_groups || []).forEach((group) => {
      if (group?.group_id && group.status !== "removed") groups.set(String(group.group_id), group);
    });
    (state.materials?.items || []).forEach((item) => {
      const group = item.packing_group;
      if (item.packing_group_id && group?.member_keys && !groups.has(String(item.packing_group_id))) {
        groups.set(String(item.packing_group_id), group);
      }
    });
    return groups;
  }

  /** 物料区「成本字段 + 结构操作」的可写判据：历史、已确认、已回写、锁定版本与详情页只读都算不可写。 */
  materialWriteAllowed() {
    const state = this.ensureMaterialFeeState();
    return state.materials?.packing_group_editable !== false && !this.detailState?.readOnly;
  }

  /**
   * 参考字段（供应商 / 项目归属）的唯一可写判据，比成本字段宽一档。
   *
   * 这两项是 ERP 路由与采购的基础资料、不参与金额计算，采购往往在成本确认**之后**才补齐，
   * 所以已确认 / 已锁定 / 已回写的**当前版本**仍可维护。服务端同一口径：写入门槛
   * `_assert_current_item_version(..., reference_fields=True)`，载荷字段 `reference_editable`；
   * 写入后服务端会把已存结果的输入指纹重新对齐（金额不变），不会把批次钉在「待重新计算」上。
   * 历史与归档版本照旧只读。
   */
  materialReferenceWriteAllowed() {
    const state = this.ensureMaterialFeeState();
    return state.materials?.reference_editable !== false && !this.detailState?.readOnly;
  }

  /** 行级成本字段写入：整块可写，且不是 AI 替换草稿行（草稿行改的是另一份 payload）。 */
  materialRowIsWritable(item) {
    return Boolean(item && !item.__aiReplacement && this.materialWriteAllowed());
  }

  /** 行级参考字段写入：服务端按 item.name 定位，不要求行有 stable_line_key。 */
  materialRowIsReferenceWritable(item) {
    return Boolean(item && !item.__aiReplacement && this.materialReferenceWriteAllowed());
  }

  /** 行结构写入（合并/解除合并/软排除）服务端按 stable_line_key 定位，缺键行一律做不了。 */
  materialRowIsStructurable(item) {
    return this.materialRowIsWritable(item) && Boolean(String(item?.stable_line_key || ""));
  }

  materialRowSelectionKey(item) {
    // 勾选与字段写入共用的行身份：优先 stable_line_key，退回行 name（与 ERP 推送路由的
    // 行身份约定同源）。只读批次也能勾选查看；能不能写由上面两个判据分轨把守。
    return String(item?.stable_line_key || item?.name || "");
  }

  materialPageSelectionRows() {
    return this.materialFeeVisibleItems().filter((item) => this.materialRowSelectionKey(item));
  }

  /** 本页「参考字段可写」的行 —— 勾选门槛用它：已确认批次上批量补归属/供应商也要能算进有效选择。 */
  materialPageReferenceWritableRows() {
    return this.materialFeeVisibleItems().filter((item) => this.materialRowIsReferenceWritable(item));
  }

  materialPageStructurableRows() {
    return this.materialFeeVisibleItems().filter((item) => this.materialRowIsStructurable(item));
  }

  toggleMaterialSelection(stableLineKey, checked) {
    const state = this.ensureMaterialFeeState();
    state.packingGroupSelections = state.packingGroupSelections instanceof Set ? state.packingGroupSelections : new Set();
    const key = String(stableLineKey || "");
    if (!key) return;
    if (checked) state.packingGroupSelections.add(key);
    else state.packingGroupSelections.delete(key);
  }

  materialPageSelectionState() {
    const state = this.ensureMaterialFeeState();
    state.packingGroupSelections = state.packingGroupSelections instanceof Set ? state.packingGroupSelections : new Set();
    const selections = state.packingGroupSelections;
    const keys = [...new Set(this.materialPageSelectionRows().map((item) => this.materialRowSelectionKey(item)).filter(Boolean))];
    const selected = keys.filter((key) => selections.has(key)).length;
    return { total:keys.length, selected, checked:Boolean(keys.length && selected === keys.length),
      indeterminate:Boolean(selected && selected < keys.length) };
  }

  toggleMaterialPageSelection(checked) {
    const keys = [...new Set(this.materialPageSelectionRows().map((item) => this.materialRowSelectionKey(item)).filter(Boolean))];
    keys.forEach((key) => this.toggleMaterialSelection(key, checked));
  }

  syncMaterialPageCheckboxState() {
    const checkbox = this.$root?.find?.("[data-mf-page-select]")?.get?.(0);
    if (!checkbox) return;
    const page = this.materialPageSelectionState();
    checkbox.checked = page.checked;
    checkbox.indeterminate = page.indeterminate;
  }

  clearMaterialSelection(render = true) {
    const state = this.ensureMaterialFeeState();
    state.packingGroupSelections = state.packingGroupSelections instanceof Set ? state.packingGroupSelections : new Set();
    state.packingGroupSelections.clear();
    if (render && state.materials && state.fees && state.preview) this.renderMaterialFeeWorkspacePreservingPosition();
  }

  materialSelectionContext() {
    const state = this.ensureMaterialFeeState();
    state.packingGroupSelections = state.packingGroupSelections instanceof Set ? state.packingGroupSelections : new Set();
    const selectedKeys = new Set([...state.packingGroupSelections].map(String));
    const pageRows = state.materials?.items || [];
    // 三个集合必须用同一个行身份（materialRowSelectionKey）拼装：勾选键放宽到「有身份即可」
    // 之后，只按 stable_line_key 建集合会把 name 键全判成「未知行」，批量设置门槛集体失灵。
    const pageKeys = new Set(pageRows.map((row) => this.materialRowSelectionKey(row)).filter(Boolean));
    const writablePageKeys = new Set(this.materialPageReferenceWritableRows()
      .map((row) => this.materialRowSelectionKey(row)).filter(Boolean));
    const structurablePageKeys = new Set(this.materialPageStructurableRows()
      .map((row) => this.materialRowSelectionKey(row)).filter(Boolean));
    const groups = this.materialPackingGroups();
    const groupByMember = new Map();
    groups.forEach((group, groupId) => (group.member_keys || []).forEach((key) => groupByMember.set(String(key), groupId)));
    const knownKeys = new Set([...pageKeys, ...groupByMember.keys()]);
    const unknownKeys = [...selectedKeys].filter((key) => !knownKeys.has(key));
    const lockedPageKeys = [...selectedKeys].filter((key) => pageKeys.has(key) && !writablePageKeys.has(key));
    // 缺键行（OA 骨架行等）能写字段、不能做结构操作：服务端软排除/装箱组都按 stable_line_key
    // 匹配行。单独一轨，免得「字段可写」把批量设置一起挡掉、或反过来放行必然失败的结构操作。
    const keylessPageKeys = [...selectedKeys].filter((key) => writablePageKeys.has(key) && !structurablePageKeys.has(key));
    const selectedGroupIds = [];
    const incompleteGroupIds = [];
    groups.forEach((group, groupId) => {
      const members = (group.member_keys || []).map(String);
      const selectedCount = members.filter((key) => selectedKeys.has(key)).length;
      if (selectedCount === members.length && members.length) selectedGroupIds.push(groupId);
      else if (selectedCount) incompleteGroupIds.push(groupId);
    });
    const ungroupedKeys = [...selectedKeys].filter((key) => knownKeys.has(key) && !groupByMember.has(key));
    const selectedMemberKeys = [...selectedGroupIds].flatMap((groupId) => (groups.get(groupId)?.member_keys || []).map(String));
    const selectedCount = selectedKeys.size;
    const crossPageCount = [...selectedKeys].filter((key) => !pageKeys.has(key)).length;
    const editable = this.materialWriteAllowed();
    // 参考字段比成本字段宽一档：已确认 / 已锁定的当前版本也要能补供应商与项目归属。
    const referenceEditable = this.materialReferenceWriteAllowed();
    const orderedKeys = pageRows.map((row) => this.materialRowSelectionKey(row));
    const positions = ungroupedKeys.map((key) => orderedKeys.indexOf(key)).sort((a, b) => a - b);
    const contiguous = positions.length >= 2 && positions.every((position, index) =>
      position >= 0 && (index === 0 || position === positions[index - 1] + 1));
    const completeSelection = !unknownKeys.length && !lockedPageKeys.length && !incompleteGroupIds.length;
    const structureReady = completeSelection && !keylessPageKeys.length;
    const readonlyReason = "历史、已确认、已回写或锁定版本不可编辑";
    const referenceReadonlyReason = "历史或归档版本不可维护供应商与项目归属";
    const lockedSelectionReason = "当前选择包含不可操作的 AI 替换草稿行";
    const keylessStructureReason = "所选行缺少物料行标识（stable_line_key），暂不支持合并、解除合并与删除";
    // 结构类动作失败原因的优先级：先讲清「为什么写不了」，再讲「选择不够」，最后才是各自动作的形状要求。
    const structureReason = (shapeReason) => {
      if (!editable) return readonlyReason;
      if (lockedPageKeys.length) return lockedSelectionReason;
      if (keylessPageKeys.length) return keylessStructureReason;
      if (incompleteGroupIds.length) return "删除组员前请先解除合并";
      return shapeReason;
    };
    const mergeEnabled = editable && structureReady && selectedGroupIds.length === 0
      && ungroupedKeys.length === selectedCount && contiguous;
    const editEnabled = editable && structureReady && selectedGroupIds.length === 1
      && !ungroupedKeys.length && selectedMemberKeys.length === selectedCount;
    const unmergeEnabled = editable && structureReady && selectedGroupIds.length >= 1
      && !ungroupedKeys.length && selectedMemberKeys.length === selectedCount;
    const removeEnabled = editable && structureReady && selectedCount > 0;
    const projectEnabled = referenceEditable && selectedCount > 0 && !unknownKeys.length && !lockedPageKeys.length;
    // 表头那个复选框一直是本页全选的唯一入口，但它没有文字、贴在固定列最左边，
    // 用户找不到。这里把同一份状态显式接到工具栏上，复用既有 materialPageSelectionState
    // 与 toggleMaterialPageSelection，不新增选择逻辑。
    // 全选本页永远可点（用户要求）：只看本页有没有可勾选的行，不再受只读门槛限制；
    // 只读批次里勾选仅用于查看范围，批量写入仍被各自的 editable 门槛挡住。
    const pageSelection = this.materialPageSelectionState();
    const pageSelectEnabled = pageSelection.total > 0;
    return {
      selectedKeys, selectedCount, crossPageCount, selectedGroupIds, incompleteGroupIds, ungroupedKeys, lockedPageKeys,
      // 物料区整体只读（历史／已确认／已回写／锁定版本）：工具栏据此把原因贴在批量按钮旁边，
      // 免得只有带紫色 :disabled 样式的批量按钮看得出来「点不了」，被误判成勾选不够。
      readonly: !editable,
      // 只读批次里参考字段仍可能可写（已确认的当前版本要由采购补供应商 / 项目归属）：
      // 工具栏的只读胶囊与那两颗批量按钮据此分开判断，不再一起连坐。
      referenceReadonly: !referenceEditable,
      actions: {
        add:{enabled:editable, reason:editable ? "" : readonlyReason},
        merge:{enabled:mergeEnabled, reason:mergeEnabled ? "" : structureReason("请选择至少两条连续、未分组的物料")},
        edit:{enabled:editEnabled, reason:editEnabled ? "" : structureReason("请只选择一个完整装箱组")},
        unmerge:{enabled:unmergeEnabled, reason:unmergeEnabled ? "" : structureReason("请选择一个或多个完整装箱组")},
        remove:{enabled:removeEnabled, reason:removeEnabled ? "" : structureReason("请先选择物料")},
        project:{enabled:projectEnabled, reason:projectEnabled ? "" : !referenceEditable ? referenceReadonlyReason : "请先选择有效物料行"},
        supplier:{enabled:projectEnabled, reason:projectEnabled ? "" : !referenceEditable ? referenceReadonlyReason : "请先选择有效物料行"},
        selectPage:{enabled:pageSelectEnabled,
          label:pageSelection.checked ? ocTranslate("取消全选本页") : ocTranslate("全选本页"),
          // 只覆盖当前页可勾选的物料行；跨页选择靠逐页累积（已选计数会标出跨页成员数）。
          reason:pageSelectEnabled ? "" : "当前页没有可选择的物料行"},
        clear:{enabled:selectedCount > 0, reason:selectedCount ? "" : "当前没有选中物料"},
      },
    };
  }

  materialReadonlyNoteHtml() {
    const state = this.ensureMaterialFeeState();
    if (!state.materials || this.materialWriteAllowed()) return "";
    // 只读原因原来只在禁用按钮的 title 里，用户点了没反应却看不到为什么；
    // 这里复用 ocw-mf-dialog-note 把原因亮出来。勾选/全选现在只读批次也可用，
    // 但那只是查看范围，真正拦住的是批量写入，所以文案要说清这层区别。
    if (this.materialReferenceWriteAllowed()) {
      // 已确认 / 已锁定的当前版本：成本字段与装箱组结构冻结，但采购仍需补供应商与项目归属
      // （服务端对这两项单独放行，并把已存结果的输入指纹重新对齐，不会变成「待重新计算」）。
      return `<div class="ocw-mf-dialog-note"><strong>${ocTranslate("成本字段只读 · 基础资料可维护")}</strong><span>${ocTranslate("该版本已确认／锁定：数量、金额与装箱组结构不可修改；供应商与项目归属仍可按行维护，不影响已算出的成本结果。")}</span></div>`;
    }
    return `<div class="ocw-mf-dialog-note"><strong>${ocTranslate("物料区只读")}</strong><span>${ocTranslate("历史或归档版本仅供追溯，成本字段、供应商与项目归属都不可修改；勾选与全选本页仅用于查看范围。如需修改数据，请先走解锁/调整流程。")}</span></div>`;
  }

  renderMaterialSelectionToolbar() {
    const context = this.materialSelectionContext();
    const button = (label, action, spec, kind = "ocw-outline-btn") =>
      `<button class="${kind}" type="button" data-action="${action}" ${spec.enabled ? "" : "disabled"} title="${this.escape(spec.reason || label)}">${ocTranslate(label)}</button>`;
    // 左组是逐行／整组的编辑动作；右组这三个一按就成片影响物料行（选中本页、批量改归属、批量改供应商），
    // 靠右单独成组并用紫色区分，免得和左侧混成一条看不出差别的按钮带。
    const batch = (label, action, spec) => button(label, action, spec, "ocw-outline-btn ocw-mf-batch-btn");
    // 只读胶囊只说真话：已确认／锁定的当前版本里，供应商与项目归属其实还能改（服务端专门放行），
    // 写「批量设置不可用」会让采购以为资料补不了；真被禁的是左侧的成本字段与结构操作。
    const lockNote = context.readonly
      ? `<span class="ocw-mf-selection-lock" title="${this.escape(context.referenceReadonly
          ? context.actions.project.reason
          : ocTranslate("数量、金额与装箱组结构不可改；供应商与项目归属仍可维护"))}">${context.referenceReadonly
          ? ocTranslate("物料区只读 · 批量设置不可用")
          : ocTranslate("成本字段只读 · 可补供应商/项目归属")}</span>`
      : "";
    return `<div class="ocw-mf-selection-toolbar" aria-label="${ocTranslate('物料批量操作')}">
      <span class="ocw-mf-selection-count">${ocTranslate("已选 {0} 行{1}", [context.selectedCount, context.crossPageCount ? `<small>${ocTranslate("含 {0} 个跨页成员", [context.crossPageCount])}</small>` : ""])}</span>
      ${button("新增物料", "mf-add-material", context.actions.add)}
      ${button("合并装箱组", "mf-create-packing-group", context.actions.merge)}
      ${button("编辑装箱组", "mf-edit-selected-packing-group", context.actions.edit)}
      ${button("解除合并", "mf-remove-selected-packing-groups", context.actions.unmerge)}
      ${button("删除所选", "mf-exclude-selected", context.actions.remove, "ocw-outline-btn is-danger")}
      ${button("清除选择", "mf-clear-selection", context.actions.clear)}
      <span class="ocw-mf-toolbar-batch" aria-label="${ocTranslate('批量设置')}">
        ${lockNote}
        ${batch(context.actions.selectPage.label, "mf-select-page", context.actions.selectPage)}
        ${batch("批量设置项目归属", "mf-set-project", context.actions.project)}
        ${batch("批量设置供应商", "mf-set-supplier", context.actions.supplier)}
      </span>
    </div>`;
  }

  invalidateProjectRouteOptions() {
    const state = this.ensureMaterialFeeState();
    state.projectRouteOptionsCache = null;
    state.projectRouteOptionsPromise = null;
  }

  projectRouteRevisionHint() {
    const state = this.ensureMaterialFeeState();
    return String(
      state.materials?.route_revision
      || state.aiFill?.route_revision
      || state.aiFill?.project_routing?.route_revision
      || ""
    );
  }

  async loadProjectRouteOptions({ force = false, expectedRevision = "" } = {}) {
    const state = this.ensureMaterialFeeState();
    const batchName = String(this.detailState?.batchName || "");
    const cached = state.projectRouteOptionsCache;
    const revisionChanged = Boolean(expectedRevision && cached?.route_revision !== expectedRevision);
    if (!force && !revisionChanged && cached?.batch_name === batchName) return cached.result;
    if (!force && !revisionChanged && state.projectRouteOptionsPromise) return state.projectRouteOptionsPromise;
    const request = this.call("overseas_costing.api.materials.list_project_route_options", {
      batch_name:batchName,
    }, false).then((result) => {
      if (!result?.ok) throw new Error(result?.message || "项目路由加载失败。");
      if (this.materialFeeState === state && String(this.detailState?.batchName || "") === batchName) {
        state.projectRouteOptionsCache = { batch_name:batchName, route_revision:String(result.route_revision || ""), result };
      }
      return result;
    }).finally(() => {
      if (state.projectRouteOptionsPromise === request) state.projectRouteOptionsPromise = null;
    });
    state.projectRouteOptionsPromise = request;
    return request;
  }

  buildProjectPickerModel(items, optionsResult, search = "", selectedValue = "") {
    const options = Array.isArray(optionsResult?.options) ? optionsResult.options : [];
    const validProjects = new Set(options.map((row) => String(row.project_collection || "").trim()).filter(Boolean));
    const invalidCurrent = [...new Set((items || []).map((row) => String(row.project_collection || "").trim())
      .filter((value) => value && !validProjects.has(value)))];
    return {
      options,
      conflicts: optionsResult?.conflicts || [],
      routeRevision: String(optionsResult?.route_revision || ""),
      invalidCurrent,
      items:(items || []).slice(),
      assignments:this.buildProjectAssignments(items, validProjects),
      selectedValue:String(selectedValue || ""),
      search:String(search || ""),
    };
  }

  /**
   * 批量归属的逐行初值：当前值本身是有效路由就沿用，否则留空等人工指定。
   *
   * 987 行的正确项目归属各不相同（旧口径填的是产品线/客户），一个共用值套不住，
   * 所以批量弹窗以「行名 → 项目」的映射为准；这里只负责给一个不会误导人的起点。
   */
  buildProjectAssignments(items = [], validProjects = new Set()) {
    const assignments = {};
    (items || []).forEach((item) => {
      const current = String(item?.project_collection || "").trim();
      assignments[String(item?.name || "")] = validProjects.has(current) ? current : "";
    });
    return assignments;
  }

  /**
   * 弹窗内的物料行标签：主显物料名，物料编码退成副标题。
   *
   * 逐行归属/供应商弹窗要一屏扫几十行，只给「FL-1」这类编码认不出是哪个东西；
   * 主次顺序沿用物料表的口径（`product_name` 优先，见 76-allocation.js），
   * 编码仍保留在下方，方便与 Excel、ERP 里按编码核对。
   */
  materialDialogRowHtml(row = {}, fallback = "") {
    const name = String(row?.product_name || "").trim();
    const code = String(row?.material_code || "").trim();
    const primary = name || code || String(row?.stable_line_key || "").trim() || String(row?.name || "") || String(fallback || "");
    return `<span>${this.escape(primary)}</span>${name && code ? `<small>${this.escape(code)}</small>` : ""}`;
  }

  renderProjectAssignTable(model = {}) {
    const rows = model.items || [];
    return `<label class="ocw-mf-reference-search"><span>${ocTranslate("搜索物料（名称 / 编码 / 当前归属）")}</span><input type="search" data-mf-project-search autocomplete="off" value="${this.escape(model.search || "")}"></label>`
      + `<div class="ocw-mf-assign-head"><label><span>${ocTranslate("统一设置")}</span><select data-mf-assign-all>${this.renderProjectAssignOptions(model, "", "统一设置为…")}</select></label><small data-mf-assign-count>${this.renderProjectAssignCount(model)}</small></div>`
      + `<div class="ocw-mf-assign-scroll"><table class="ocw-mf-assign-table"><thead><tr><th>${ocTranslate("物料名称")}</th><th>${ocTranslate("当前归属")}</th><th>${ocTranslate("设为")}</th></tr></thead><tbody data-mf-assign-rows>${this.renderProjectAssignRows(model)}</tbody></table></div>`
      + `<p class="ocw-mf-assign-hint">${ocTranslate("逐行指定后一次提交；只修改明确勾选的 {0} 行，不会扩展到装箱组。", [rows.length])}</p>`;
  }

  renderProjectAssignRows(model = {}) {
    const rows = this.filterProjectAssignRows(model);
    if (!rows.length) return `<tr><td class="ocw-mf-assign-empty" colspan="3">${ocTranslate("没有匹配的物料")}</td></tr>`;
    const invalid = new Set(model.invalidCurrent || []);
    const assignments = model.assignments || {};
    return rows.map((row) => {
      const itemName = String(row.name || "");
      const current = String(row.project_collection || "").trim();
      const chosen = String(assignments[itemName] || "");
      const blocked = invalid.has(current);
      const rowClass = chosen ? ` class="is-chosen"` : "";
      const cellClass = blocked ? "ocw-mf-assign-current is-invalid" : "ocw-mf-assign-current";
      const note = blocked ? `<small>${ocTranslate("无有效 ERP 路由")}</small>` : "";
      return `<tr${rowClass}><td class="ocw-mf-assign-item">${this.materialDialogRowHtml(row, itemName)}</td><td class="${cellClass}">${this.escape(current || ocTranslate("未设置"))}${note}</td><td><select data-mf-assign-row="${this.escape(itemName)}">${this.renderProjectAssignOptions(model, chosen)}</select></td></tr>`;
    }).join("");
  }

  renderProjectAssignOptions(model = {}, chosen = "", placeholder = "— 未选择 —") {
    const options = model.options || [];
    const selected = (value) => (value === String(chosen || "") ? ` selected` : "");
    const option = (row) => {
      const value = String(row.project_collection || "");
      const company = row.subsidiary_code ? ` · ${this.escape(row.subsidiary_code)}` : "";
      return `<option value="${this.escape(value)}"${selected(value)}>${this.escape(value)}${company}</option>`;
    };
    const group = (label, rows) => rows.length ? `<optgroup label="${this.escape(ocTranslate(label))}">${rows.map(option).join("")}</optgroup>` : "";
    return `<option value=""${selected("")}>${this.escape(placeholder)}</option>`
      + group("本审批候选", options.filter((row) => row.is_approval_candidate))
      + group("其他可选项目", options.filter((row) => !row.is_approval_candidate));
  }

  renderProjectAssignCount(model = {}) {
    const rows = model.items || [];
    const invalid = new Set(model.invalidCurrent || []);
    const assignments = model.assignments || {};
    const assigned = rows.filter((row) => String(assignments[String(row.name || "")] || "")).length;
    const noRoute = rows.filter((row) => {
      const value = String(row.project_collection || "").trim();
      return Boolean(value) && invalid.has(value);
    }).length;
    return ocTranslate("显示 {0} / 共 {1} 行 · 已指定 {2} 行 · 当前值无有效路由 {3} 行", [this.filterProjectAssignRows(model).length, rows.length, assigned, noRoute]);
  }

  filterProjectAssignRows(model = {}) {
    const rows = model.items || [];
    const needle = String(model.search || "").trim().toLocaleLowerCase();
    if (!needle) return rows;
    return rows.filter((row) => [row.material_code, row.stable_line_key, row.product_name, row.project_collection]
      .some((value) => String(value || "").toLocaleLowerCase().includes(needle)));
  }

  renderProjectPickerOptions(model = {}) {
    const needle = String(model.search || "").trim().toLocaleLowerCase();
    const options = (model.options || []).filter((row) => !needle || [
      row.project_collection, row.subsidiary_code, row.erp_site,
    ].some((value) => String(value || "").toLocaleLowerCase().includes(needle)));
    const renderGroup = (label, rows) => rows.length ? `<section class="ocw-mf-reference-group"><h6>${ocTranslate(label)}</h6>${rows.map((row) => {
      const project = String(row.project_collection || "");
      const selected = project === String(model.selectedValue || "");
      return `<label class="ocw-mf-reference-option ${selected ? "is-selected" : ""}"><input type="radio" name="ocw-project-route" value="${this.escape(project)}" ${selected ? "checked" : ""}><span><strong>${this.escape(project)}</strong><small>${ocTranslate("ERP 公司：{0}", [this.escape(row.subsidiary_code || ocTranslate("未配置"))])}</small></span>${row.is_approval_candidate ? `<em>${ocTranslate("本审批候选")}</em>` : ""}</label>`;
    }).join("")}</section>` : "";
    const invalid = (model.invalidCurrent || []).map((value) => `<p class="ocw-mf-reference-invalid">${ocTranslate("当前值：{0}", [this.escape(value)])}<span>${ocTranslate("无有效 ERP 路由，请改选下方有效项目。")}</span></p>`).join("");
    const candidates = options.filter((row) => row.is_approval_candidate);
    const others = options.filter((row) => !row.is_approval_candidate);
    return `${invalid}${renderGroup("本审批候选", candidates)}${renderGroup("其他可选项目", others)}${options.length ? "" : `<p class="ocw-mf-reference-empty">${ocTranslate("没有匹配的有效项目")}</p>`}`;
  }

  renderReferenceChangePreview(items, fieldname, target, label) {
    const rows = (items || []).map((row) => `<tr><td class="ocw-mf-assign-item">${this.materialDialogRowHtml(row)}</td><td>${this.escape(row[fieldname] || ocTranslate("未设置"))}</td><td>${this.escape(target || ocTranslate("未选择"))}</td></tr>`).join("");
    return `<div class="ocw-mf-reference-preview"><strong>${this.escape(ocTranslate(label))}</strong><span>${ocTranslate("只修改明确勾选的 {0} 行，不会扩展到装箱组。", [items.length])}</span><table><thead><tr><th>${ocTranslate("物料名称")}</th><th>${ocTranslate("原值")}</th><th>${ocTranslate("新值")}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  /**
   * 把「一个共用值」与「逐行不同值」收敛成同一个 行名 → 目标值 映射。
   *
   * 单行/整组入口传一个 `value`，批量入口传 `options.perItemValues`（弹窗里逐行选的）。
   * 校验只留这一处，两条入口的错误文案与 `materialReferenceInputInvalid` 标记保持一致。
   */
  resolveMaterialReferenceTargets(items = [], value, options = {}, allowedValues = new Set()) {
    const perItem = options.perItemValues || null;
    const targets = new Map();
    if (perItem) {
      const unset = [];
      (items || []).forEach((item) => {
        const chosen = String(perItem[String(item?.name || "")] || "").trim();
        if (!chosen) { unset.push(String(item?.material_code || item?.stable_line_key || item?.name || "")); return; }
        targets.set(String(item?.name || ""), chosen);
      });
      if (unset.length) {
        const error = new Error(ocTranslate("还有 {0} 行未指定项目归属（如 {1}），整批都不会保存。", [unset.length, unset.slice(0, 3).join("、")]));
        error.materialReferenceInputInvalid = true;
        throw error;
      }
    } else {
      const shared = String(value || "").trim();
      (items || []).forEach((item) => targets.set(String(item?.name || ""), shared));
    }
    const outside = [...new Set([...targets.values()].filter((target) => !allowedValues.has(target)))];
    if (outside.length) {
      const error = new Error(ocTranslate("请从有效列表中选择，不能录入列表外的值。"));
      error.materialReferenceInputInvalid = true;
      throw error;
    }
    return targets;
  }

  async applyMaterialReferenceSelection(items, fieldname, value, options = {}) {
    const allowedValues = options.allowedValues instanceof Set ? options.allowedValues : new Set(options.allowedValues || []);
    const targets = this.resolveMaterialReferenceTargets(items, value, options, allowedValues);
    const state = this.ensureMaterialFeeState();
    if (options.aiDraft && fieldname === "project_collection") {
      (items || []).forEach((item) => {
        const target = targets.get(String(item.name || ""));
        const meta = item.__aiReplacement;
        if (meta) {
          const proposal = (state.aiFill?.proposals || []).find((row) => String(row.proposal_id || "") === String(meta.proposalId || ""));
          if (!proposal) return;
          state.aiFill.edits = state.aiFill.edits || {};
          if (!state.aiFill.edits[meta.proposalId]) state.aiFill.edits[meta.proposalId] = JSON.parse(JSON.stringify(proposal.payload?.fields || {}));
          const edit = state.aiFill.edits[meta.proposalId];
          edit.replacement_rows = edit.replacement_rows || JSON.parse(JSON.stringify(proposal.payload?.replacement_rows || []));
          if (edit.replacement_rows[meta.rowIndex]) edit.replacement_rows[meta.rowIndex].project_collection = target;
          state.aiFill.selections?.add?.(String(meta.proposalId));
          return;
        }
        this.updateMaterialAIDraftValue(item.name, fieldname, target, item[fieldname] || "");
        const draft = state.aiFill?.manualUpdates?.[`${item.name}:${fieldname}`];
        if (draft) delete draft.reason;
      });
      this.renderMaterialFeeWorkspacePreservingPosition();
      return { ok:true, draft:true, changed_count:items.length };
    }
    if (!(await this.ensureEditSession())) return { ok:false, cancelled:true };
    const fallbackRemark = fieldname === "project_collection" ? "设置 ERP 项目归属" : "设置 ERP 供应商";
    const auditRemark = String(options.auditRemark || fallbackRemark).trim() || fallbackRemark;
    const updates = (items || []).map((item) => ({
      item_name:item.name, fieldname, value:targets.get(String(item.name || "")), remark:auditRemark,
    }));
    const result = await this.call("overseas_costing.api.calculate.batch_update_items", {
      batch_name:this.detailState.batchName,
      version_name:this.detailState.versionName,
      updates:JSON.stringify(updates),
      remark:auditRemark,
      edit_token:this.detailState.editToken,
      expected_modified:this.detailState.expectedModified,
    }, false);
    if (!result?.ok) {
      // 批量接口只会回一句「已整体回滚」，真正的原因在 results 里：这里还原成「哪一行、为什么」，
      // 否则用户反复点保存拿到的都是同一句无从下手的话。标记成业务拒绝，别再走乐观锁那套提示。
      const error = new Error(this.materialBatchWriteFailureMessage(result, items, fieldname));
      error.materialRowWriteRejected = true;
      throw error;
    }
    this.updateMaterialFeeExpectedModified(result);
    state.packingGroupSelections?.clear?.();
    if (fieldname === "project_collection") {
      this.invalidateProjectRouteOptions();
      await this.loadProjectRouteOptions({ force:true });
    }
    await this.loadMaterialFeeWorkspace({ quiet:true });
    return result;
  }

  async applyProjectCollectionSelection(items, value, options = {}) {
    return this.applyMaterialReferenceSelection(items, "project_collection", value, options);
  }

  /**
   * 项目归属 / 供应商两个选择器保存失败时的唯一出口。
   *
   * 里面抛的错以前没人接，而 Frappe 的 Dialog 不会等 `primary_action` 的 promise，
   * 于是「编辑权没拿到」「乐观锁过期」这类失败都表现为点保存毫无反应、连原因都没有。
   * 这里复用单元格保存那条已有的恢复动作（recoverMaterialFeeReadonlyState 会强制重建
   * 资料页快照、把批次 revision 刷成最新），再把失败原因和下一步明确呈现出来。
   */
  /**
   * 批量写入失败时把逐行原因还原成人话。
   *
   * 服务端为了「要么全成功、要么全回滚」只回一句能力有限的总体文案，真正的原因在
   * `results` 里；这里把它和本地行标签拼起来，用户才知道是哪一行、为什么没保存。
   */
  materialBatchWriteFailureMessage(result, items = [], fieldname = "") {
    const label = { supplier: "供应商", project_collection: "项目归属" }[String(fieldname || "")] || "批量字段";
    const byName = new Map((items || []).map((row) => [String(row?.name || ""), row]));
    const failures = (result?.results || []).filter((row) => row && row.ok === false);
    const details = failures.slice(0, 3).map((row) => {
      const rowLabel = this.materialRowPlainLabel(byName.get(String(row.item_name || "")) || {}, row.item_name);
      const reason = String(row.message || "").trim();
      return reason ? `${rowLabel}：${reason}` : rowLabel;
    }).filter(Boolean);
    if (!details.length) return String(result?.message || ocTranslate("{0}未保存。", [label]));
    const rest = failures.length > details.length ? ocTranslate("；另有 {0} 行失败", [failures.length - details.length]) : "";
    return ocTranslate("{0}未保存（已整体回滚）：{1}{2}", [label, details.join("；"), rest]);
  }

  /** 纯文本行标签：错误文案里不能塞给表格渲染用的 HTML。 */
  materialRowPlainLabel(row = {}, fallback = "") {
    const productName = String(row?.product_name || "").trim();
    const materialCode = String(row?.material_code || "").trim();
    if (productName && materialCode) return `${productName}（${materialCode}）`;
    return productName || materialCode || String(fallback || "").trim() || ocTranslate("所选物料行");
  }

  async reportMaterialReferenceWriteFailure(error) {
    if (error?.workbenchReleaseHandled || error?.workbenchReleaseBlocked) return;
    const reason = this.normalizeErrorMessage(error).replace(/^[A-Za-z_]*Error:\s*/, "");
    if (error?.materialReferenceInputInvalid) {
      frappe.show_alert({ message:reason, indicator:"orange" });
      return;
    }
    if (error?.materialRowWriteRejected) {
      // 业务拒绝（行不属于当前版本、缺行标识…）：一个字都没写进去，重同步和
      //「已同步最新数据，请再点一次保存」都是误导 —— 直接把原因摊开。
      this.showError(new Error(reason));
      return;
    }
    let recovered = false;
    try {
      recovered = await this.recoverMaterialFeeReadonlyState(this.detailState.batchName);
    } catch (_recoveryError) {
      recovered = false;
    }
    const nextStep = recovered ? "已同步最新数据，请再点一次保存。" : "请刷新页面后重试。";
    this.showError(new Error(`${reason}；${nextStep}`));
  }

  async openProjectCollectionForItem(itemName, suggestedValue = "") {
    const item = this.findMaterialFeeItem(itemName);
    if (!item) throw new Error(ocTranslate("物料行已变化，请刷新后重试。"));
    const state = this.ensureMaterialFeeState();
    const draftValue = state.aiFill?.manualUpdates?.[`${itemName}:project_collection`]?.value
      ?? state.aiFill?.updates?.[`${itemName}:project_collection`]?.value;
    return this.openProjectCollectionPicker({ items:[item], source:"cell", suggestedValue:suggestedValue || draftValue || "" });
  }

  async openProjectCollectionDialog() {
    const state = this.ensureMaterialFeeState();
    const context = this.materialSelectionContext();
    if (!context.actions.project.enabled) throw new Error(context.actions.project.reason);
    const selected = (state.materials?.items || []).filter((row) => context.selectedKeys.has(this.materialRowSelectionKey(row)));
    if (selected.length !== context.selectedCount || selected.some((row) => !row.name)) throw new Error(ocTranslate("选中物料已变化，请刷新后重试。"));
    return this.openProjectCollectionPicker({ items:selected, source:"bulk" });
  }

  async openProjectCollectionPicker({ items = [], source = "cell", suggestedValue = "" } = {}) {
    const state = this.ensureMaterialFeeState();
    const optionsResult = await this.loadProjectRouteOptions({ expectedRevision:this.projectRouteRevisionHint() });
    if (!(optionsResult.options || []).length) throw new Error(ocTranslate("当前没有可用的项目路由。"));
    const valid = new Set(optionsResult.options.map((row) => String(row.project_collection || "")));
    // 批量入口改成逐行指定：每行归属都不一样，一个共用值套所有选中行不解决问题。
    const bulk = source === "bulk";
    const sharedCurrent = [...new Set(items.map((row) => String(row.project_collection || "").trim()).filter(Boolean))];
    const initial = valid.has(String(suggestedValue || "")) ? String(suggestedValue) : sharedCurrent.length === 1 && valid.has(sharedCurrent[0]) ? sharedCurrent[0] : "";
    let model = this.buildProjectPickerModel(items, optionsResult, "", initial);
    const dialog = new frappe.ui.Dialog({
      title:bulk ? ocTranslate("批量设置项目归属（{0} 行）", [items.length]) : ocTranslate("选择项目归属"),
      fields: bulk
        ? [
            {fieldtype:"HTML", fieldname:"picker", options:`<div class="ocw-mf-reference-picker-dialog ocw-mf-project-assign">${this.renderProjectAssignTable(model)}</div>`},
          ]
        : [
            {fieldtype:"HTML", fieldname:"picker", options:`<div class="ocw-mf-reference-picker-dialog"><label class="ocw-mf-reference-search"><span>${ocTranslate("搜索项目或 ERP 公司")}</span><input type="search" data-mf-project-search autocomplete="off"></label><div data-mf-project-options>${this.renderProjectPickerOptions(model)}</div></div>`},
            {fieldtype:"HTML", fieldname:"preview", options:this.renderReferenceChangePreview(items, "project_collection", initial, "项目归属变更预览")},
          ],
      primary_action_label:bulk ? "确认批量设置" : "保存",
      primary_action:async () => {
        try {
          const aiDraft = Boolean(this.isMaterialAIReadyStatus(state.aiFill?.status) && state.aiFill?.draftVisible);
          const result = await this.applyProjectCollectionSelection(items, String(model.selectedValue || ""), {
            aiDraft,
            allowedValues:valid,
            perItemValues:bulk ? model.assignments : null,
          });
          // 编辑权没拿到时内部返回 ok:false（入口已给出提示），不能当成保存成功。
          if (!result?.ok) return;
          dialog.hide();
          frappe.show_alert({message:aiDraft ? ocTranslate("项目归属已更新到 AI 草稿") : ocTranslate("已更新 {0} 行项目归属", [items.length]), indicator:"green"});
        } catch (error) {
          await this.reportMaterialReferenceWriteFailure(error);
        }
      },
    });
    dialog.show();
    // 逐行归属表 + 候选列表在默认弹窗里会被挤成一条，沿用材料费用域共用的宽弹窗宽度。
    dialog.$wrapper.addClass("ocw-mf-dialog");
    const $picker = dialog.fields_dict.picker.$wrapper;
    if (bulk) {
      const rerenderRows = () => {
        $picker.find("[data-mf-assign-rows]").html(this.renderProjectAssignRows(model));
        $picker.find("[data-mf-assign-count]").html(this.renderProjectAssignCount(model));
      };
      $picker.on("input", "[data-mf-project-search]", (event) => { model = { ...model, search:String($(event.currentTarget).val() || "") }; rerenderRows(); });
      $picker.on("change", "[data-mf-assign-row]", (event) => {
        const itemName = String($(event.currentTarget).attr("data-mf-assign-row") || "");
        model = { ...model, assignments:{ ...model.assignments, [itemName]:String($(event.currentTarget).val() || "") } };
        rerenderRows();
      });
      // 「统一设置」等价于旧行为，但改成作用于当前筛选出的行，且不再是唯一入口。
      $picker.on("change", "[data-mf-assign-all]", (event) => {
        const chosen = String($(event.currentTarget).val() || "");
        if (!chosen) return;
        const assignments = { ...model.assignments };
        this.filterProjectAssignRows(model).forEach((row) => { assignments[String(row.name || "")] = chosen; });
        model = { ...model, assignments };
        $(event.currentTarget).val("");
        rerenderRows();
      });
      return dialog;
    }
    const render = () => {
      $picker.find("[data-mf-project-options]").html(this.renderProjectPickerOptions(model));
      dialog.fields_dict.preview.$wrapper.html(this.renderReferenceChangePreview(items, "project_collection", model.selectedValue, "项目归属变更预览"));
    };
    $picker.on("input", "[data-mf-project-search]", (event) => { model = { ...model, search:String($(event.currentTarget).val() || "") }; render(); });
    $picker.on("change", "input[name='ocw-project-route']", (event) => { model = { ...model, selectedValue:String($(event.currentTarget).val() || "") }; render(); });
    return dialog;
  }

  async loadSupplierResolution(rawValue = "") {
    const state = this.ensureMaterialFeeState();
    const batchName = String(this.detailState?.batchName || "");
    const query = String(rawValue || "").trim();
    const key = `${batchName}:${query.toLocaleLowerCase()}`;
    if (state.supplierOptionsCache.has(key)) return state.supplierOptionsCache.get(key);
    const result = await this.call("overseas_costing.api.materials.resolve_supplier_options", {
      batch_name:batchName, raw_value:query,
    }, false, { type:"GET" });
    const options = [];
    if (result?.canonical_supplier) options.push({
      name:String(result.canonical_supplier), supplier_name:String(result.canonical_supplier), score:1, exact:true,
    });
    (result?.candidates || []).forEach((candidate) => {
      if (!candidate?.name || options.some((row) => row.name === candidate.name)) return;
      options.push({ ...candidate, name:String(candidate.name) });
    });
    const model = {
      query,
      status:String(result?.status || (query ? "UNRESOLVED" : "EMPTY")),
      options,
      canCreate:Boolean(query && !["EXACT", "AMBIGUOUS"].includes(String(result?.status || ""))),
      highConfidence:options.find((row) => row.high_confidence) || null,
    };
    state.supplierOptionsCache.set(key, model);
    return model;
  }

  renderSupplierPickerOptions(model = {}, selection = {}) {
    const options = model.options || [];
    const selectedKind = String(selection.kind || "");
    const selectedValue = String(selection.value || "");
    // 「无供应商」是常驻一等选项：业务口径是"没有供应商就标 /"，与"未设置"（空）区分。
    // 写入仍走同一条 batch_update_items 链，由服务端 validate_canonical_supplier 归一。
    const none = `<section class="ocw-mf-reference-group"><h6>${ocTranslate("无供应商")}</h6><label class="ocw-mf-reference-option is-none ${selectedKind === "none" ? "is-selected" : ""}"><input type="radio" name="ocw-supplier" data-mf-supplier-kind="none" data-mf-supplier-value="/" ${selectedKind === "none" ? "checked" : ""}><span><strong>${ocTranslate("标记为无供应商（/）")}</strong><small>${ocTranslate("该行确认没有供应商；推送 ERP 时由站点默认供应商兜底。")}</small></span></label></section>`;
    const existing = options.length ? `<section class="ocw-mf-reference-group"><h6>${ocTranslate("ERP 供应商")}</h6>${options.map((row) => `<label class="ocw-mf-reference-option ${selectedKind === "existing" && String(row.name) === selectedValue ? "is-selected" : ""}"><input type="radio" name="ocw-supplier" data-mf-supplier-kind="existing" data-mf-supplier-value="${this.escape(row.name)}" ${selectedKind === "existing" && String(row.name) === selectedValue ? "checked" : ""}><span><strong>${this.escape(row.name)}</strong><small>${this.escape(row.supplier_name || row.name)}${row.score ? ` · ${Math.round(Number(row.score) * 100)}%` : ""}</small></span>${row.high_confidence ? `<em>${ocTranslate("高置信候选")}</em>` : ""}</label>`).join("")}</section>` : "";
    const warning = model.highConfidence ? `<small>${ocTranslate("存在高置信近似供应商 {0}；创建前需确认“这是不同供应商”。", [this.escape(model.highConfidence.name)])}</small>` : `<small>${ocTranslate("仅创建基础 Supplier，详细档案可稍后维护。")}</small>`;
    const create = model.canCreate ? `<section class="ocw-mf-reference-group"><h6>${ocTranslate("没有完全同名结果")}</h6><label class="ocw-mf-reference-option is-create ${selectedKind === "create" ? "is-selected" : ""}"><input type="radio" name="ocw-supplier" data-mf-supplier-kind="create" data-mf-supplier-value="${this.escape(model.query)}" ${selectedKind === "create" ? "checked" : ""}><span><strong>${ocTranslate("新建供应商：{0}", [this.escape(model.query)])}</strong>${warning}</span></label></section>` : "";
    return `${none}${existing}${create}`;
  }

  async applySupplierSelection(items, value, options = {}) {
    return this.applyMaterialReferenceSelection(items, "supplier", value, options);
  }

  async createSupplierAndApply(items, supplierName, confirmSimilar = false) {
    const name = String(supplierName || "").trim();
    const result = await this.call("overseas_costing.api.materials.create_supplier_from_workbench", {
      batch_name:String(this.detailState?.batchName || ""),
      supplier_name:name,
      confirm_similar:confirmSimilar ? 1 : 0,
    }, false, { type:"POST" });
    if (!result?.ok) return result || { ok:false, message:ocTranslate("供应商创建失败。") };
    const supplier = String(result.supplier || "").trim();
    if (!supplier) throw new Error(ocTranslate("ERP 未返回规范供应商 ID。"));
    this.ensureMaterialFeeState().supplierOptionsCache.clear();
    const applied = await this.applySupplierSelection(items, supplier, {
      allowedValues:new Set([supplier]),
      auditRemark:result.created ? "新建 ERP 供应商并设置物料" : "设置 ERP 供应商",
    });
    // 供应商建好了但没落到物料行：如实上报，交给调用方按失败处理。
    if (!applied?.ok) return applied;
    return result;
  }

  async openSupplierForItem(itemName, suggestedValue = "") {
    const item = this.findMaterialFeeItem(itemName);
    if (!item) throw new Error(ocTranslate("物料行已变化，请刷新后重试。"));
    return this.openSupplierPicker({ items:[item], source:"cell", suggestedValue });
  }

  async openSupplierDialog() {
    const state = this.ensureMaterialFeeState();
    const context = this.materialSelectionContext();
    if (!context.actions.supplier.enabled) throw new Error(context.actions.supplier.reason);
    const selected = (state.materials?.items || []).filter((row) => context.selectedKeys.has(this.materialRowSelectionKey(row)));
    if (selected.length !== context.selectedCount || selected.some((row) => !row.name)) throw new Error(ocTranslate("选中物料已变化，请刷新后重试。"));
    return this.openSupplierPicker({ items:selected, source:"bulk" });
  }

  async openSupplierPicker({ items = [], source = "cell", suggestedValue = "" } = {}) {
    const sharedCurrent = [...new Set(items.map((row) => String(row.supplier || "").trim()).filter(Boolean))];
    let query = String(suggestedValue || (sharedCurrent.length === 1 ? sharedCurrent[0] : ""));
    let model = await this.loadSupplierResolution(query);
    let selection = model.options.some((row) => row.name === query)
      ? { kind:"existing", value:query }
      : { kind:"", value:"" };
    let requestRevision = 0;
    const dialog = new frappe.ui.Dialog({
      title:source === "bulk" ? ocTranslate("批量设置供应商（{0} 行）", [items.length]) : ocTranslate("选择 ERP 供应商"),
      fields:[
        {fieldtype:"HTML", fieldname:"picker", options:`<div class="ocw-mf-reference-picker-dialog"><label class="ocw-mf-reference-search"><span>${ocTranslate("搜索或输入供应商")}</span><input type="search" data-mf-supplier-search autocomplete="off" value="${this.escape(query)}"></label><div data-mf-supplier-options>${this.renderSupplierPickerOptions(model, selection)}</div></div>`},
        {fieldtype:"HTML", fieldname:"preview", options:this.renderReferenceChangePreview(items, "supplier", selection.value, "供应商变更预览")},
      ],
      primary_action_label:source === "bulk" ? "确认批量设置" : "保存",
      primary_action:async () => {
        try {
          if (!selection.kind || !selection.value) {
            const error = new Error(ocTranslate("请选择已有供应商，或选择新建当前输入名称。"));
            error.materialReferenceInputInvalid = true;
            throw error;
          }
          if (selection.kind === "none") {
            const applied = await this.applySupplierSelection(items, "/", {
              allowedValues:new Set(["/"]),
              auditRemark:"标记为无供应商（/）",
            });
            if (!applied?.ok) return;
          } else if (selection.kind === "existing") {
            const allowedValues = new Set(model.options.map((row) => row.name));
            const applied = await this.applySupplierSelection(items, selection.value, { allowedValues });
            if (!applied?.ok) return;
          } else {
            let result = await this.createSupplierAndApply(items, selection.value, false);
            if (result?.code === "SIMILAR_SUPPLIER_CONFIRMATION_REQUIRED") {
              const candidates = (result.candidates || []).map((row) => row.name).filter(Boolean).join("、");
              const confirmed = await new Promise((resolve) => frappe.confirm(
                ocTranslate("发现近似供应商{0}。确认“这是不同供应商”，并新建 {1}？", [candidates ? `：${this.escape(candidates)}` : "", this.escape(selection.value)]),
                () => resolve(true),
                () => resolve(false)
              ));
              if (!confirmed) return;
              result = await this.createSupplierAndApply(items, selection.value, true);
            }
            if (!result?.ok) throw new Error(result?.message || "供应商创建失败。");
          }
          dialog.hide();
          frappe.show_alert({message:ocTranslate("已更新 {0} 行供应商", [items.length]), indicator:"green"});
        } catch (error) {
          await this.reportMaterialReferenceWriteFailure(error);
        }
      },
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-mf-dialog");
    const $picker = dialog.fields_dict.picker.$wrapper;
    const render = () => {
      $picker.find("[data-mf-supplier-options]").html(this.renderSupplierPickerOptions(model, selection));
      dialog.fields_dict.preview.$wrapper.html(this.renderReferenceChangePreview(items, "supplier", selection.value, "供应商变更预览"));
      dialog.get_primary_btn?.().text(selection.kind === "create" ? "新建并使用" : (source === "bulk" ? "确认批量设置" : "保存"));
    };
    $picker.on("input", "[data-mf-supplier-search]", async (event) => {
      query = String($(event.currentTarget).val() || "").trim();
      const currentRevision = ++requestRevision;
      const next = await this.loadSupplierResolution(query);
      if (currentRevision !== requestRevision) return;
      model = next;
      if (selection.kind === "existing" && !model.options.some((row) => row.name === selection.value)) selection = { kind:"", value:"" };
      if (selection.kind === "create") selection = { kind:"", value:"" };
      render();
    });
    $picker.on("change", "input[name='ocw-supplier']", (event) => {
      const $input = $(event.currentTarget);
      selection = {
        kind:String($input.attr("data-mf-supplier-kind") || ""),
        value:String($input.attr("data-mf-supplier-value") || ""),
      };
      render();
    });
    return dialog;
  }

  async openMaterialPackingGroupDialog(action = "create", groupId = "") {
    const state = this.ensureMaterialFeeState();
    const groups = state.materials?.packing_groups || [];
    const group = groups.find((row) => String(row.group_id || "") === String(groupId || ""));
    const memberKeys = action === "create" ? [...state.packingGroupSelections] : [...(group?.member_keys || [])];
    if (action !== "create" && !group) throw new Error(ocTranslate("装箱组已变化，请刷新后重试。"));
    if (memberKeys.length < 2) throw new Error(ocTranslate("请先勾选至少两行连续物料。"));
    if (action === "remove") {
      const preview = await this.previewMaterialPackingGroup(action, groupId, memberKeys, {}, "人工解除装箱组");
      return this.confirmMaterialPackingGroupPreview(preview, ocTranslate("确认解除这个 {0} 行装箱组？解除后各行恢复独立装箱字段。", [memberKeys.length]));
    }
    const initial = await this.previewMaterialPackingGroup(action, groupId, memberKeys,
      action === "update" ? group : {}, action === "create" ? "人工合并连续物料行" : "人工修改装箱组");
    const defaults = initial.group || {};
    const dialog = new frappe.ui.Dialog({
      title: action === "create" ? ocTranslate("合并装箱组（{0} 行）", [memberKeys.length]) : ocTranslate("编辑装箱组"),
      fields: [
        {fieldtype:"HTML", fieldname:"summary", options:`<p>${ocTranslate("所选物料将共用一组箱数、净重、毛重和体积；货值仍按行保存。")}</p>`},
        {fieldtype:"Float", fieldname:"package_count", label:ocTranslate("箱数"), reqd:1, default:defaults.package_count},
        {fieldtype:"Data", fieldname:"packaging_type", label:ocTranslate("包装类型"), default:defaults.packaging_type},
        {fieldtype:"Float", fieldname:"net_weight_kg", label:ocTranslate("净重 kg"), reqd:1, default:defaults.net_weight_kg},
        {fieldtype:"Float", fieldname:"gross_weight_kg", label:ocTranslate("毛重 kg"), reqd:1, default:defaults.gross_weight_kg},
        {fieldtype:"Float", fieldname:"volume_m3", label:ocTranslate("体积 m³"), reqd:1, default:defaults.volume_m3},
        {fieldtype:"Small Text", fieldname:"reason", label:ocTranslate("修改说明"), default:action === "create" ? "多行共用同一装箱" : "调整装箱组"},
      ],
      primary_action_label: ocTranslate("预览并确认"),
      primary_action: async (values) => {
        try {
          const preview = await this.previewMaterialPackingGroup(action, groupId, memberKeys, values, values.reason);
          const g = preview.group || {};
          const message = ocTranslate("确认后 {0} 行共用：箱数 {1}，净重 {2} kg，毛重 {3} kg，体积 {4} m³。", [memberKeys.length, g.package_count, g.net_weight_kg, g.gross_weight_kg, g.volume_m3]);
          const saved = await this.confirmMaterialPackingGroupPreview(preview, message);
          if (saved) dialog.hide();
        } catch (error) { this.showError(error); }
      },
    });
    dialog.show();
  }

  async previewMaterialPackingGroup(action, groupId, memberKeys, values, reason) {
    const result = await this.call("overseas_costing.api.materials.preview_material_packing_group", {
      batch_name:this.detailState.batchName, version_name:this.detailState.versionName,
      member_keys_json:JSON.stringify(memberKeys), action, group_id:groupId || "",
      values_json:JSON.stringify(values || {}), reason:reason || "",
    }, false);
    if (!result?.ok) throw new Error(result?.message || "装箱组预览失败。");
    return result;
  }

  async confirmMaterialPackingGroupPreview(preview, message) {
    return new Promise((resolve) => frappe.confirm(message, async () => {
      try {
        if (!(await this.ensureEditSession())) return resolve(false);
        const result = await this.call("overseas_costing.api.materials.confirm_material_packing_group", {
          batch_name:this.detailState.batchName, preview_id:preview.preview_id, revision:preview.revision,
          edit_token:this.detailState.editToken, expected_modified:this.detailState.expectedModified,
        }, false);
        if (!result?.ok) throw new Error(result?.message || "装箱组未保存。");
        this.updateMaterialFeeExpectedModified(result);
        this.ensureMaterialFeeState().packingGroupSelections.clear();
        await this.loadMaterialFeeWorkspace({quiet:true});
        frappe.show_alert({message:preview.action === "remove" ? ocTranslate("装箱组已解除") : ocTranslate("装箱组已保存"), indicator:"green"});
        resolve(true);
      } catch (error) { this.showError(error); resolve(false); }
    }, () => resolve(false)));
  }

  async removeSelectedPackingGroups() {
    const context = this.materialSelectionContext();
    if (!context.actions.unmerge.enabled) throw new Error(context.actions.unmerge.reason);
    const preview = await this.call("overseas_costing.api.materials.preview_material_packing_group_batch", {
      batch_name:this.detailState.batchName, version_name:this.detailState.versionName,
      group_ids_json:JSON.stringify(context.selectedGroupIds), reason:"人工批量解除装箱组",
    }, false);
    if (!preview?.ok) throw new Error(preview?.message || "装箱组预览失败。");
    return new Promise((resolve) => frappe.confirm(
      ocTranslate("确认解除 {0} 个装箱组，共影响 {1} 行物料？", [context.selectedGroupIds.length, Number(preview.affected_member_keys?.length || 0)]),
      async () => {
        try {
          if (!(await this.ensureEditSession())) return resolve(false);
          const result = await this.call("overseas_costing.api.materials.confirm_material_packing_group_batch", {
            batch_name:this.detailState.batchName, preview_id:preview.preview_id, revision:preview.revision,
            edit_token:this.detailState.editToken, expected_modified:this.detailState.expectedModified,
          }, false);
          if (!result?.ok) throw new Error(result?.message || "装箱组未解除。");
          this.updateMaterialFeeExpectedModified(result);
          this.clearMaterialSelection(false);
          await this.loadMaterialFeeWorkspace({quiet:true});
          frappe.show_alert({message:ocTranslate("已解除 {0} 个装箱组", [result.removed_group_ids?.length || context.selectedGroupIds.length]), indicator:"green"});
          resolve(true);
        } catch (error) { this.showError(error); resolve(false); }
      }, () => resolve(false),
    ));
  }

  async excludeSelectedMaterials() {
    const context = this.materialSelectionContext();
    if (!context.actions.remove.enabled) throw new Error(context.actions.remove.reason);
    const keys = [...context.selectedKeys];
    return new Promise((resolve) => frappe.confirm(
      ocTranslate("确认软删除所选 {0} 行物料？删除后可在“已排除物料”中恢复。", [keys.length]),
      async () => {
        try {
          if (!(await this.ensureEditSession())) return resolve(false);
          const result = await this.call("overseas_costing.api.materials.exclude_material_items", {
            batch_name:this.detailState.batchName, version_name:this.detailState.versionName,
            stable_line_keys_json:JSON.stringify(keys), reason:`顶部批量操作软排除 ${keys.length} 行物料`,
            edit_token:this.detailState.editToken, expected_modified:this.detailState.expectedModified,
          }, false);
          if (!result?.ok) throw new Error(result?.message || "所选物料未删除。");
          this.updateMaterialFeeExpectedModified(result);
          this.clearMaterialSelection(false);
          await this.loadMaterialFeeWorkspace({quiet:true});
          frappe.show_alert({message:ocTranslate("已软排除 {0} 行物料", [result.excluded_count || keys.length]), indicator:"green"});
          resolve(true);
        } catch (error) { this.showError(error); resolve(false); }
      }, () => resolve(false),
    ));
  }

  materialFeeVisibleItems() {
    const state = this.ensureMaterialFeeState();
    let items = this.materialReplacementRows(state.materials?.items || []);
    if (state.onlyMissing) {
      const missingGroups = new Set(items.filter((row) => (row.requirements?.missing_fields || []).length)
        .map((row) => row.packing_group_id).filter(Boolean));
      items = items.filter((row) => row.__aiReplacement || (row.requirements?.missing_fields || []).length
        || missingGroups.has(row.packing_group_id));
    }
    return items;
  }

  renderMaterialFeeGrid() {
    const state = this.ensureMaterialFeeState();
    const materialData = state.materials || {};
    const columns = this.materialFeeGridColumns();
    const items = this.materialFeeVisibleItems();
    const page = Number(materialData.page || state.page || 1);
    const pageCount = Math.max(1, Number(materialData.page_count || 1));
    const tableWidth = columns.reduce((sum, column) => sum + Number(column.width || 130), 0);
    return `
      <div class="ocw-mf-grid-shell" style="--mf-grid-table-width:${tableWidth}px">
        <div class="ocw-mf-grid-note"><span>${this.isMaterialAIReadyStatus(state.aiFill?.status) && state.aiFill?.draftVisible ? ocTranslate("AI 草稿中，单格修改只更新草稿") : ocTranslate("单格离开或按 Enter 自动保存")}</span><span>${ocTranslate("Tab 可连续操作")}</span><span>${ocTranslate("多格粘贴会先预览再整体确认")}</span><span>${ocTranslate("项目归属缺失不阻断试算")}</span></div>
        <div class="ocw-mf-grid-scroll" data-mf-grid-viewport>
          <div class="ocw-mf-grid-track"><table class="ocw-mf-grid-table">
            <colgroup>${columns.map((column) => `<col style="width:${Number(column.width || 130)}px">`).join("")}</colgroup>
            <thead><tr>${columns.map((column) => column.field === "__group_select"
              ? `<th data-mf-grid-field="__group_select"><input type="checkbox" data-mf-page-select="1" aria-label="${ocTranslate('选择本页物料')}" ${this.materialPageSelectionState().checked ? "checked" : ""} ${this.materialPageSelectionState().total ? "" : "disabled"}></th>`
              : `<th data-mf-grid-field="${column.field}">${this.escape(ocTranslate(column.label))}</th>`).join("")}</tr></thead>
            <tbody>${items.length ? items.map((item, index) => item.__aiReplacement ? this.renderMaterialReplacementGridRow(item, columns, index) : this.renderMaterialFeeGridRow(item, columns, index)).join("") : `<tr><td class="ocw-mf-grid-empty" colspan="${columns.length}">${state.onlyMissing ? ocTranslate("当前页没有缺项") : ocTranslate("当前批次暂无物料行")}</td></tr>`}</tbody>
          </table></div>
        </div>
        <div class="ocw-mf-grid-scroll-controls"><button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="mf-grid-scroll-left" aria-label="${ocTranslate('向左滚动')}">‹</button><div class="ocw-horizontal-scrollbar ocw-mf-grid-scrollbar" data-mf-grid-scrollbar tabindex="0" aria-label="${ocTranslate('物料表水平滚动条')}"><div style="width:${tableWidth}px"></div></div><button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="mf-grid-scroll-right" aria-label="${ocTranslate('向右滚动')}">›</button></div>
        <div class="ocw-mf-grid-footer"><span>${ocTranslate("共 {0} · 当前第 {1}/{2} 页", [items.length !== (materialData.items || []).length ? ocTranslate("{0} 行（含 AI 临时明细）", [items.length]) : ocTranslate("{0} 行", [Number(materialData.total || 0)]), page, pageCount])}</span><div><button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="mf-material-page" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>${ocTranslate("上一页")}</button><button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="mf-material-page" data-page="${page + 1}" ${page >= pageCount ? "disabled" : ""}>${ocTranslate("下一页")}</button></div></div>
      </div>
    `;
  }

  materialReplacementRows(items = []) {
    const fill = this.ensureMaterialFeeState().aiFill;
    if (!this.isMaterialAIReadyStatus(fill?.status) || !fill.draftVisible || !fill.review_mode) return items;
    const replacements = new Map();
    (fill.proposals || []).filter((proposal) => proposal.proposal_type === "material_replace").forEach((proposal) => {
      const proposalId = String(proposal.proposal_id || "");
      const rows = fill.edits?.[proposalId]?.replacement_rows || proposal.payload?.replacement_rows || [];
      if (!proposal.target_item_name || !rows.length) return;
      replacements.set(String(proposal.target_item_name), { proposal, rows });
    });
    if (!replacements.size) return items;
    return items.flatMap((item) => {
      const replacement = replacements.get(String(item.name || ""));
      if (!replacement) return [item];
      return replacement.rows.map((row, rowIndex) => ({
        ...item,
        ...row,
        name: `ai:${replacement.proposal.proposal_id}:${rowIndex}`,
        row_no: `${item.row_no || ""}.${rowIndex + 1}`,
        material_code: "待建档",
        actual_shipped_qty: row.actual_shipped_qty ?? row.quantity,
        shipped_uom: row.shipped_uom || row.purchase_uom || item.shipped_uom,
        effective_shipping_quantity: row.actual_shipped_qty ?? row.quantity,
        effective_shipping_uom: row.shipped_uom || row.purchase_uom || item.shipped_uom,
        requirements: { missing_fields: [] },
        __aiReplacement: {
          proposalId: String(replacement.proposal.proposal_id || ""),
          rowIndex,
          originalItemName: String(item.name || ""),
        },
      }));
    });
  }

  renderMaterialReplacementGridRow(item, columns, rowIndex) {
    return `<tr class="is-ai-replacement" data-mf-row-index="${rowIndex}" data-item-name="${this.escape(item.name || "")}">${columns.map((column, columnIndex) => this.renderMaterialReplacementGridCell(item, column, columnIndex)).join("")}</tr>`;
  }

  renderMaterialReplacementGridCell(item, column, columnIndex) {
    const meta = item.__aiReplacement || {};
    if (column.field === "__group_select") {
      return `<td class="ocw-mf-cell ocw-mf-group-select is-readonly" data-mf-column-index="${columnIndex}" data-mf-grid-field="__group_select"><input type="checkbox" disabled aria-label="${ocTranslate('AI 替换草稿行不可选择')}"></td>`;
    }
    const editable = new Set([
      "product_name", "spec_model", "quantity", "purchase_uom", "unit_price",
      "unit_price_uom", "purchase_currency", "goods_value", "actual_shipped_qty",
      "shipped_uom", "net_weight_kg", "gross_weight_kg", "volume_m3",
      "chargeable_weight_kg", "project_collection",
    ]);
    let fieldname = column.field;
    let value = item[fieldname];
    if (fieldname === "source_doc_no") value = item.source_doc_no || "原审批行";
    if (fieldname === "material_code") value = "待建档";
    if (fieldname === "actual_shipped_qty") value = item.actual_shipped_qty ?? item.quantity;
    if (fieldname === "shipped_uom") value = item.shipped_uom || item.purchase_uom;
    if (!editable.has(fieldname)) {
      return `<td class="ocw-mf-cell is-readonly is-ai-replacement" data-mf-column-index="${columnIndex}" data-mf-grid-field="${column.field}" title="${ocTranslate('AI 临时明细，确认后替换原模糊物料行')}"><span>${this.escape(this.formatValue(value || "--"))}</span></td>`;
    }
    if (fieldname === "project_collection") {
      return this.renderMaterialReferencePickerCell(item, column, value, columnIndex, "is-ai-draft is-ai-replacement");
    }
    return `<td class="ocw-mf-cell is-ai-draft is-ai-replacement" data-mf-column-index="${columnIndex}" data-mf-grid-field="${column.field}"><input data-mf-ai-edit="1" data-proposal-id="${this.escape(meta.proposalId || "")}" data-row-index="${Number(meta.rowIndex || 0)}" data-fieldname="${this.escape(fieldname)}" value="${this.escape(value ?? "")}" ${column.numeric ? 'inputmode="decimal"' : ""} aria-label="${this.escape(ocTranslate("AI 临时明细 {0}", [ocTranslate(column.label)]))}" /><small>${ocTranslate("AI 临时明细 · 可修改")}</small></td>`;
  }

  approvalLinkNeedsReview(link) {
    return Boolean(link?.status && link.status !== "linked");
  }

  renderApprovalLinkMarker(link) {
    if (!this.approvalLinkNeedsReview(link)) return "";
    return `<details class="ocw-approval-marker"><summary>${this.escape(ocTranslate(link.label || "采购审批待核实"))}</summary><div><p>${this.escape(ocTranslate(link.reason || "请核对采购审批来源。"))}</p>${link.approval_no ? `<p>${ocTranslate("审批号：{0}", [this.escape(link.approval_no)])}</p>` : ""}${link.instance_id ? `<p>${ocTranslate("审批实例：{0}", [this.escape(link.instance_id)])}</p>` : ""}</div></details>`;
  }

  renderMaterialFeeGridRow(item, columns, rowIndex) {
    const missingFields = new Set(item.requirements?.missing_fields || []);
    return `<tr class="${this.approvalLinkNeedsReview(item.approval_link) ? "ocw-approval-row" : ""}" data-mf-row-index="${rowIndex}" data-item-name="${this.escape(item.name || "")}">${columns.map((column, columnIndex) => this.renderMaterialFeeGridCell(item, column, missingFields, columnIndex)).join("")}</tr>`;
  }

  materialValueIsPlaceholder(fieldname, value, item = {}) {
    if (value === null || value === undefined) return true;
    const text = String(value).trim().replace(/\s+/g, " ").toLowerCase();
    if (["", "-", "--", "/", "\\", "n/a", "na", "null", "none", "无", "暂无"].includes(text)) return true;
    if (["actual_shipped_qty", "shipped_uom"].includes(String(fieldname || ""))) {
      const mode = String(item.actual_shipped_qty_mode || item.effective_shipping?.mode || "").toUpperCase();
      if (["DEFAULT_PURCHASE", "LEGACY_UNVERIFIED"].includes(mode)) return true;
    }
    if (fieldname === "shipment_value_rmb" && (item.shipment_valuation?.error || item.shipment_valuation?.status === "missing")) return true;
    const zeroMissing = ["goods_value", "unit_price", "net_weight_kg", "gross_weight_kg", "volume_m3", "chargeable_weight_kg"];
    if (zeroMissing.includes(String(fieldname || "")) && Number(text) === 0) return true;
    if (fieldname === "actual_shipped_qty" && Number(text) === 0) {
      const mode = String(item.actual_shipped_qty_mode || item.effective_shipping?.mode || "").toUpperCase();
      return !["MANUAL_CONFIRMED", "EXPLICIT_SOURCE"].includes(mode);
    }
    return false;
  }

  materialPurchaseCorrectionFields() {
    return new Set([
      "goods_value", "shipment_value_rmb", "unit_price", "purchase_currency", "purchase_uom", "unit_price_uom",
      "actual_shipped_qty", "shipped_uom", "net_weight_kg", "gross_weight_kg", "volume_m3",
      "chargeable_weight_kg", "project_collection",
    ]);
  }

  renderMaterialFeeGridCell(item, column, missingFields, columnIndex) {
    if (column.field === "__group_select") {
      const key = this.materialRowSelectionKey(item);
      const checked = this.ensureMaterialFeeState().packingGroupSelections.has(key);
      // 勾选只要求行有身份（stable_line_key 或 name），只读批次也能勾选查看；
      // AI 替换草稿行没有身份，保持禁用。
      return `<td class="ocw-mf-cell ocw-mf-group-select" data-mf-column-index="${columnIndex}" data-mf-grid-field="__group_select"><input type="checkbox" data-mf-packing-group-select="${this.escape(key)}" ${checked ? "checked" : ""} ${item.__aiReplacement || !key ? "disabled" : ""} aria-label="${ocTranslate('选择物料行')}"></td>`;
    }
    const packingFields = new Set(["package_count", "packaging_type", "net_weight_kg", "gross_weight_kg", "volume_m3"]);
    if (item.packing_group_id && packingFields.has(column.field)) {
      if (Number(item.packing_group_position || 0) > 0) return "";
      const group = item.packing_group || {};
      return `<td class="ocw-mf-cell is-packing-group" rowspan="${Number(group.rowspan || item.packing_group_size || 1)}" data-mf-column-index="${columnIndex}" data-mf-grid-field="${this.escape(column.field)}"><span>${this.escape(this.formatValue(group[column.field] ?? "--"))}</span><small>${ocTranslate("装箱组共用 · {0} 行", [Number(item.packing_group_size || 0)])}</small></td>`;
    }
    let value = item[column.field];
    if (item.source_adoption_state === "historical_pending" && ["material_code", "product_name"].includes(column.field)) {
      return `<td class="ocw-mf-cell is-readonly" data-mf-column-index="${columnIndex}" data-mf-grid-field="${column.field}"><span>${this.escape(value ?? "--")}</span><small>${ocTranslate("历史行 · 当前未采用")}</small></td>`;
    }
    if (item.adopted_price && ["unit_price", "purchase_currency", "unit_price_uom"].includes(column.field)) {
      const price = item.adopted_price;
      const current = { unit_price: price.value, purchase_currency: price.currency, unit_price_uom: price.unit }[column.field];
      const sourceLabel = price.error
        ? "商品价待核对"
        : price.source_type === "expense"
          ? "采购支出商品价"
          : price.source_type === "purchase_total_derived"
            ? "按货值÷采购数量计算"
            : "原商品采购价";
      return `<td class="ocw-mf-cell is-readonly" data-mf-column-index="${columnIndex}" data-mf-grid-field="${column.field}"><span>${this.escape(current ?? ocTranslate("待补"))}</span><small>${sourceLabel}</small></td>`;
    }
    const shippingField = ["actual_shipped_qty", "shipped_uom"].includes(column.field);
    if (column.field === "actual_shipped_qty") value = item.effective_shipping_quantity;
    if (column.field === "shipped_uom") value = item.effective_shipping_uom;
    if (shippingField && item.settlement_cargo) {
      const rawPacking = [item.actual_shipped_qty ?? "未识别", item.shipped_uom || "单位待核对"].join(" ");
      return `<td class="ocw-mf-cell is-readonly ocw-mf-settlement-quantity" data-mf-column-index="${columnIndex}" data-mf-grid-field="${column.field}"><span>${this.escape(this.formatValue(value ?? "--"))}</span><small>${item.source_adoption_state === "historical_pending" ? ocTranslate("当前来源待补") : item.source_context?.packing?.selected_source ? ocTranslate("资料来源采用") : ocTranslate("采购支出采用")}</small>${column.field === "actual_shipped_qty" ? `<small>${ocTranslate("装箱原值 {0}", [this.escape(rawPacking)])}</small>` : ""}</td>`;
    }
    const originalValue = value;
    const draft = this.materialFeeState?.materialDrafts?.[`${item.name}:${column.field}`];
    if (draft && !column.readonly) value = draft.value;
    const aiFill = this.materialFeeState?.aiFill;
    const aiVisible = this.isMaterialAIReadyStatus(aiFill?.status) && aiFill.draftVisible;
    const aiCell = this.materialAICell(item.name, column.field);
    const aiUpdate = aiVisible ? aiFill.updates?.[`${item.name}:${column.field}`] : null;
    const manualUpdate = aiVisible ? aiFill.manualUpdates?.[`${item.name}:${column.field}`] : null;
    if (!column.readonly && aiUpdate) value = aiUpdate.value;
    if (!column.readonly && manualUpdate) value = manualUpdate.value;
    const isMissing = missingFields.has(column.field);
    const isDefault = shippingField && item.effective_shipping?.is_default;
    const requiresCorrection = Boolean(
      this.materialPurchaseCorrectionFields().has(column.field)
      && !this.materialValueIsPlaceholder(column.field, originalValue, item)
      && !draft
      && !aiUpdate
      && !manualUpdate
    );
    const valuationStatus = column.field === "shipment_value_rmb" ? this.shipmentValuationStatus(item.shipment_valuation) : "";
    const valuationMeta = column.field === "shipment_value_rmb" ? this.renderShipmentValuationMeta(item.shipment_valuation, item.name) : "";
    const classes = ["ocw-mf-cell", isMissing ? "is-missing" : "", isDefault ? "is-default" : "", column.readonly ? "is-readonly" : "", requiresCorrection ? "is-protected-purchase" : "", draft?.error ? "is-save-error" : "", aiUpdate || manualUpdate ? "is-ai-draft" : "", aiCell && !aiUpdate ? "has-ai-candidate" : "", valuationStatus ? "is-shipment-valuation" : "", valuationStatus ? `is-valuation-${valuationStatus}` : ""].filter(Boolean).join(" ");
    const reason = draft?.error || (column.field === "shipment_value_rmb" ? item.shipment_valuation?.error_detail : "") || (item.requirements?.field_reasons?.[column.field] || []).map((row) => row.message || row.code).join("；");
    if (["project_collection", "supplier"].includes(column.field)) {
      return this.renderMaterialReferencePickerCell(item, column, value, columnIndex, classes);
    }
    if (column.readonly) {
      const fullValue = this.formatValue(value ?? "--");
      return `<td class="${classes}" data-item-name="${this.escape(item.name || "")}" data-mf-column-index="${columnIndex}" data-mf-grid-field="${column.field}" title="${this.escape(column.field === "product_name" || column.field === "source_doc_no" ? fullValue : reason)}"><span>${this.escape(fullValue)}</span>${column.field === "source_doc_no" ? this.renderApprovalLinkMarker(item.approval_link) : ""}${column.field === "product_name" ? (this.renderReviewFeedbackButton?.({ target_tab: "documents", target_field: column.field, target_item: item.name }, "反馈此行") || "") : ""}</td>`;
    }
    if (requiresCorrection) {
      return `<td class="${classes}" data-mf-column-index="${columnIndex}" data-mf-grid-field="${column.field}" title="${ocTranslate('已有有效值；修正时需填写原因')}"><span>${this.escape(this.formatValue(value ?? "--"))}</span>${valuationMeta}<button type="button" class="ocw-mf-purchase-correct" data-action="mf-correct-purchase" data-item-name="${this.escape(item.name || "")}" data-fieldname="${this.escape(column.field)}">${ocTranslate("修正")}</button>${this.renderMaterialAICandidates(item.name, column.field, aiCell, false)}</td>`;
    }
    const editor = Array.isArray(column.options)
      ? `<select data-mf-cell-input="1" data-item-name="${this.escape(item.name || "")}" data-fieldname="${this.escape(column.field)}" data-original-value="${this.escape(originalValue ?? "")}" aria-label="${this.escape(ocTranslate(column.label))}"><option value="">${ocTranslate("请选择")}</option>${column.options.map((option) => `<option value="${this.escape(option.value)}" ${String(value || "") === String(option.value) ? "selected" : ""}>${this.escape(ocTranslate(option.label))}</option>`).join("")}</select>`
      : `<input data-mf-cell-input="1" data-item-name="${this.escape(item.name || "")}" data-fieldname="${this.escape(column.field)}" data-original-value="${this.escape(originalValue ?? "")}" value="${this.escape(value ?? "")}" ${column.numeric ? 'inputmode="decimal"' : ""} aria-label="${this.escape(ocTranslate(column.label))}" />`;
    const retry = draft?.error ? `<button type="button" class="ocw-mf-cell-retry" data-action="mf-retry-cell">${ocTranslate("重试")}</button>` : "";
    return `<td class="${classes}" data-mf-column-index="${columnIndex}" data-mf-grid-field="${column.field}" title="${this.escape(reason)}">${editor}${valuationMeta}${retry}${aiUpdate ? `<small>${ocTranslate("AI 草稿{0}", [aiUpdate.user_edited ? " · 已修改" : ""])}</small>` : manualUpdate ? `<small>${ocTranslate("人工草稿")}</small>` : isDefault && column.field === "actual_shipped_qty" ? `<small>${ocTranslate("默认=采购数")}</small>` : ""}${this.renderMaterialAICandidates(item.name, column.field, aiCell, Boolean(aiUpdate))}</td>`;
  }

  renderMaterialReferencePickerCell(item, column, value, columnIndex, extraClasses = "") {
    const fieldname = String(column.field || "");
    const project = fieldname === "project_collection";
    const action = project ? "mf-open-project-picker" : "mf-open-supplier-picker";
    const label = project ? "项目归属" : "供应商";
    const display = String(value ?? "").trim();
    // 用参考字段自己的可写判据（不是成本字段那个）：已确认 / 已锁定的当前版本服务端对这两项
    // 单独放行。真正被禁掉的只剩历史与归档版本 —— 那时点开也只会被拒，所以按钮直接说明原因。
    const disabled = this.materialReferenceWriteAllowed() ? "" : "disabled";
    // 空格子的文案是「待设置」：这是待办（等采购/业务指定 ERP 路由与供应商），不是缺项报错；
    // 「未设置」读起来像状态描述，和旁边那个可点的选择入口对不上。
    return `<td class="ocw-mf-cell ocw-mf-reference-cell ${extraClasses}" data-mf-column-index="${columnIndex}" data-mf-grid-field="${this.escape(fieldname)}"><span>${this.escape(display || ocTranslate("待设置"))}</span><button type="button" class="ocw-mf-reference-picker" data-action="${action}" data-item-name="${this.escape(item.name || "")}" ${disabled}>${display ? ocTranslate("修正") : ocTranslate("选择{0}", [ocTranslate(label)])}</button></td>`;
  }

  shipmentValuationStatus(valuation = {}) {
    const status = String(valuation?.status || (valuation?.error ? "missing" : "automatic")).toLowerCase();
    return ["automatic", "manual", "conflict", "missing", "stale"].includes(status) ? status : "missing";
  }

  renderShipmentValuationMeta(valuation = {}, itemName = "") {
    const status = this.shipmentValuationStatus(valuation);
    const labels = {
      automatic: ocTranslate("自动估值"),
      manual: ocTranslate("人工确认"),
      conflict: ocTranslate("待确认"),
      missing: ocTranslate("待补"),
      stale: ocTranslate("数量或单位已变化"),
    };
    if (status !== "conflict") return `<small class="ocw-mf-valuation-status">${labels[status]}</small>`;
    const prior = valuation?.prior_amount_rmb ?? "";
    const calculated = valuation?.calculated_amount_rmb ?? "";
    return `<small class="ocw-mf-valuation-status">${ocTranslate("待确认 · 旧值 {0} · 计算值 {1}", [this.escape(prior), this.escape(calculated)])}</small><div class="ocw-mf-valuation-actions"><button type="button" data-action="mf-shipment-valuation-adopt" data-item-name="${this.escape(itemName)}" data-value="${this.escape(prior)}">${ocTranslate("采用旧值")}</button><button type="button" data-action="mf-shipment-valuation-adopt" data-item-name="${this.escape(itemName)}" data-value="${this.escape(calculated)}">${ocTranslate("采用计算值")}</button></div>`;
  }

  async adoptShipmentValuationCandidate($button) {
    if (!$button?.attr) return;
    const $input = $button.closest(".ocw-mf-cell").find("[data-mf-cell-input]").first();
    if (!$input?.length) {
      this.openMaterialPurchaseCorrectionDialog(
        String($button.attr("data-item-name") || ""),
        "shipment_value_rmb",
        String($button.attr("data-value") ?? "")
      );
      return;
    }
    $input.val(String($button.attr("data-value") ?? ""));
    this.updateMaterialDraftFromInput($input);
    return this.saveMaterialFeeCell($input);
  }

  materialAICell(itemName, fieldname) {
    const fill = this.ensureMaterialFeeState().aiFill;
    if (!this.isMaterialAIReadyStatus(fill?.status) || !fill.draftVisible) return null;
    return fill.draft?.rows?.[String(itemName)]?.[String(fieldname)] || null;
  }

  renderMaterialAICandidates(itemName, fieldname, cell, hasDraft) {
    const candidates = cell?.candidates || [];
    if (!candidates.length || (hasDraft && cell.status === "AI_DRAFT")) return "";
    const label = cell.status === "EXISTING_VALUE" ? "已有值 · 查看 AI" : ocTranslate("候选 {0}", [candidates.length]);
    return `<button type="button" class="ocw-mf-ai-candidate-trigger" data-action="mf-ai-open-candidate" data-item-name="${this.escape(itemName)}" data-fieldname="${this.escape(fieldname)}" aria-haspopup="dialog">${ocTranslate(label)}</button>`;
  }

  renderMaterialAICandidatePopoverContent(itemName, fieldname, cell) {
    const candidates = cell?.candidates || [];
    return `<header><strong>${ocTranslate("AI 候选详情")}</strong><button type="button" aria-label="${ocTranslate('关闭')}" data-action="mf-ai-close-candidate">×</button></header><div>${candidates.map((candidate, index) => `<article><span><b>${this.escape(this.formatValue(candidate.suggested_value))}</b><em>${Math.round(Number(candidate.confidence || 0) * 100)}%</em></span><p>${this.escape(candidate.reason || ocTranslate("待人工核对"))}</p><p>${(candidate.source_refs || []).map((ref) => this.escape([ref.file, ref.sheet, ref.page ? ocTranslate("第 {0} 页", [ref.page]) : "", ref.row ? ocTranslate("第 {0} 行", [ref.row]) : "", ref.cell].filter(Boolean).join(" · "))).join("；") || ocTranslate("来源位置未标注")}</p><button type="button" class="ocw-outline-btn ocw-mini-btn" data-action="mf-ai-adopt-candidate" data-item-name="${this.escape(itemName)}" data-fieldname="${this.escape(fieldname)}" data-candidate-index="${index}">${ocTranslate("采用此值")}</button></article>`).join("")}</div>`;
  }

  openMaterialAICandidatePopover($trigger) {
    const itemName = String($trigger.attr("data-item-name") || "");
    const fieldname = String($trigger.attr("data-fieldname") || "");
    const cell = this.materialAICell(itemName, fieldname);
    if (!cell?.candidates?.length) return;
    const state = this.ensureMaterialFeeState();
    state.activeAICandidate = { itemName, fieldname };
    const $popover = this.$root.find("[data-mf-ai-candidate-popover]");
    if (!$popover.length) return;
    $popover.html(this.renderMaterialAICandidatePopoverContent(itemName, fieldname, cell)).prop("hidden", false);
    $popover.off("click.ocwMfCandidateClose").on("click.ocwMfCandidateClose", "[data-action='mf-ai-close-candidate']", () => this.closeMaterialAICandidatePopover());
    const trigger = $trigger.get(0);
    const popover = $popover.get(0);
    if (!trigger || !popover) return;
    const triggerRect = trigger.getBoundingClientRect();
    const viewportWidth = Number(window.innerWidth || document.documentElement.clientWidth || 0);
    const viewportHeight = Number(window.innerHeight || document.documentElement.clientHeight || 0);
    const margin = 10;
    const width = Math.min(340, Math.max(260, viewportWidth - margin * 2));
    $popover.css({ width: `${width}px`, left: `${Math.max(margin, Math.min(triggerRect.right - width, viewportWidth - width - margin))}px`, top: `${Math.min(viewportHeight - margin, triggerRect.bottom + 6)}px` });
    const popoverHeight = popover.getBoundingClientRect().height;
    const belowTop = triggerRect.bottom + 6;
    const aboveTop = triggerRect.top - popoverHeight - 6;
    const top = belowTop + popoverHeight <= viewportHeight - margin || aboveTop < margin ? belowTop : aboveTop;
    $popover.css("top", `${Math.max(margin, Math.min(top, viewportHeight - popoverHeight - margin))}px`);
  }

  closeMaterialAICandidatePopover() {
    const state = this.materialFeeState;
    if (state) state.activeAICandidate = null;
    const $popover = this.$root?.find?.("[data-mf-ai-candidate-popover]");
    if ($popover?.length) $popover.prop("hidden", true).empty();
  }

  renderMaterialAIFillBanner() {
    const fill = this.ensureMaterialFeeState().aiFill;
    if (!fill) return "";
    const steps = ["读取资料", "解析/OCR", "DeepSeek 识别", "合并候选"];
    const progress = Math.max(0, Math.min(100, Number(fill.progress_percent || 0)));
    const warning = fill.ai_warning || fill.error_message || "";
    const ready = this.isMaterialAIReadyStatus(fill.status);
    const title = ready ? this.materialAIReadyTitle(fill) : fill.status === "FAILED" ? "AI 分析失败" : fill.status === "STALE" ? "AI 草稿已过期" : "正在分析当前批次资料";
    const progressStep = ready ? this.materialAIReadyStep(fill) : (fill.progress_step || "读取资料");
    return `<div class="ocw-mf-ai-banner is-${this.escape(String(fill.status || "running").toLowerCase())}"><div><strong>${title}</strong><span>${this.escape(progressStep)}</span></div><div class="ocw-mf-ai-progress" aria-label="${ocTranslate('AI 分析进度')}"><i style="width:${progress}%"></i></div><div class="ocw-mf-ai-steps">${steps.map((step) => `<span class="${step === progressStep ? "active" : ""}">${step}</span>`).join("")}</div>${warning ? `<p>${this.escape(warning)}</p>` : ""}</div>`;
  }

  materialAIProgressStatus(value) {
    return {
      WAITING: { label: ocTranslate("等待"), tone: "waiting" },
      DOWNLOADING: { label: ocTranslate("下载中"), tone: "running" },
      READING: { label: ocTranslate("读取中"), tone: "running" },
      PARSED: { label: ocTranslate("已解析"), tone: "parsed" },
      ANALYZING: { label: ocTranslate("AI 分析中"), tone: "running" },
      COMPLETED: { label: ocTranslate("已完成"), tone: "complete" },
      PARTIAL: { label: ocTranslate("部分读取"), tone: "partial" },
      FAILED: { label: ocTranslate("失败"), tone: "failed" },
      SKIPPED: { label: ocTranslate("跳过"), tone: "skipped" },
      EXCLUDED: { label: ocTranslate("已排除"), tone: "skipped" },
      NO_RESULT: { label: ocTranslate("未产生结果"), tone: "skipped" },
      NEEDS_SELECTION: { label: ocTranslate("待选择 Sheet"), tone: "waiting" },
    }[String(value || "WAITING").toUpperCase()] || { label: String(value || "等待"), tone: "waiting" };
  }

  materialAISourceGroupKey(source, index = 0) {
    const kind = String(source?.source_kind || "unknown");
    const sourceId = String(source?.source_id || source?.source_key || source?.id || "").trim();
    const explicitParent = String(source?.parent_source_id || "").trim();
    const inferredParent = !explicitParent && /:sheet:[0-9a-f]{20,64}$/i.test(sourceId)
      ? sourceId.replace(/:sheet:[0-9a-f]{20,64}$/i, "")
      : "";
    const logicalSourceId = explicitParent || inferredParent || sourceId || `unidentified:${index}`;
    return {
      group_key: JSON.stringify([kind, logicalSourceId]),
      logical_source_id: logicalSourceId,
      source_kind: kind,
    };
  }

  materialAISourceSheetInfo(source) {
    const name = String(source?.sheet_name || source?.sheet || "").trim();
    const sourceId = String(source?.source_id || source?.source_key || source?.id || "").trim();
    return {
      name,
      is_sheet: Boolean(name || source?.parent_source_id || /:sheet:[0-9a-f]{20,64}$/i.test(sourceId)),
    };
  }

  materialAISourceGroups(sources) {
    const grouped = new Map();
    (Array.isArray(sources) ? sources : []).forEach((source, index) => {
      const identity = this.materialAISourceGroupKey(source, index);
      if (!grouped.has(identity.group_key)) grouped.set(identity.group_key, { ...identity, rows: [] });
      grouped.get(identity.group_key).rows.push(source);
    });
    return Array.from(grouped.values()).map((group) => {
      const sheetRows = group.rows.filter((source) => this.materialAISourceSheetInfo(source).is_sheet);
      // 服务端已经给出「能不能用于分析」的结论，前端不再自己按 status/read_status 猜。
      // 早期版本这里自造判据，把"审批进行中"一并算成仅审计，和资料清单页口径不一致。
      const auditRows = sheetRows.length ? group.rows.filter((source) => {
        if (this.materialAISourceSheetInfo(source).is_sheet) return false;
        if (source?.audit_only === true) return true;
        const readStatus = String(source?.read_status || "").toUpperCase();
        const status = String(source?.status || "").toUpperCase();
        return source?.selectable === false
          || source?.analysis_allowed === false
          || ["EXCLUDED", "NO_RESULT", "FAILED"].includes(readStatus)
          || ["EXCLUDED", "SKIPPED", "FAILED"].includes(status);
      }) : [];
      const contentRows = group.rows.filter((source) => !auditRows.includes(source));
      const failedRows = contentRows.filter((source) => String(source?.read_status || "").toUpperCase() === "FAILED"
        || String(source?.status || "").toUpperCase() === "FAILED");
      const partialRows = contentRows.filter((source) => String(source?.read_status || "").toUpperCase() === "PARTIAL"
        || String(source?.status || "").toUpperCase() === "PARTIAL");
      const successfulRows = contentRows.filter((source) => {
        const readStatus = String(source?.read_status || "").toUpperCase();
        const status = String(source?.status || "").toUpperCase();
        return !failedRows.includes(source) && !partialRows.includes(source)
          && (readStatus === "READ" || ["PARSED", "COMPLETED"].includes(status));
      });
      const readRows = successfulRows.filter((source) => {
        const readStatus = String(source?.read_status || "").toUpperCase();
        return readStatus === "READ" || !readStatus;
      });
      const activeRows = contentRows.filter((source) => ["WAITING", "DOWNLOADING", "READING", "ANALYZING"].includes(String(source?.status || "").toUpperCase()));
      const primary = readRows[0] || successfulRows[0] || partialRows[0] || activeRows[0] || failedRows[0] || contentRows[0] || group.rows[0] || {};
      let status = String(primary?.status || "WAITING").toUpperCase();
      let readStatus = String(primary?.read_status || "NO_RESULT").toUpperCase();
      if ((partialRows.length || (successfulRows.length && failedRows.length)) && !activeRows.length) {
        status = "PARTIAL";
        readStatus = "PARTIAL";
      } else if (activeRows.length) {
        status = String(activeRows[0]?.status || "WAITING").toUpperCase();
      } else if (successfulRows.length) {
        status = successfulRows.some((source) => String(source?.status || "").toUpperCase() === "COMPLETED") ? "COMPLETED" : "PARSED";
        readStatus = readRows.length ? "READ" : String(primary?.read_status || "NO_RESULT").toUpperCase();
      } else if (failedRows.length) {
        status = "FAILED";
        readStatus = "FAILED";
      }
      const metricRows = sheetRows.length ? contentRows.filter((source) => this.materialAISourceSheetInfo(source).is_sheet) : contentRows;
      const sum = (name, fallback = "") => metricRows.reduce((total, source) => {
        const value = fallback && source?.[name] == null ? source?.[fallback] : source?.[name];
        const number = Number(value || 0);
        return total + (Number.isFinite(number) ? number : 0);
      }, 0);
      return {
        ...group,
        primary,
        label: String(primary?.label || group.rows[0]?.label || "未命名资料"),
        status,
        read_status: readStatus,
        sheet_names: [...new Set(sheetRows.map((source) => this.materialAISourceSheetInfo(source).name).filter(Boolean))],
        field_count: sum("field_count"),
        candidate_count: sum("candidate_count", "result_count"),
        result_count: sum("result_count", "candidate_count"),
        failed_count: failedRows.length,
        audit_count: auditRows.length,
        audit_rows: auditRows,
      };
    });
  }

  materialAISourceGroupSummary(groups) {
    const rows = Array.isArray(groups) ? groups : [];
    return {
      source_count: rows.length,
      partial_source_count: rows.filter((group) => String(group?.status || "").toUpperCase() === "PARTIAL").length,
      failed_source_count: rows.filter((group) => String(group?.status || "").toUpperCase() === "FAILED").length,
    };
  }

  renderMaterialAIProgressChip() {
    const fill = this.ensureMaterialFeeState().aiFill;
    const active = ["STARTING", "PAYMENT_SELECTION", "QUEUED", "RUNNING", "READY", "READY_WITH_WARNINGS", "FAILED", "STALE"].includes(String(fill?.status || ""));
    const minimized = Boolean(this.ensureMaterialFeeState().aiProgressMinimized);
    const label = this.isMaterialAIReadyStatus(fill?.status)
      ? this.materialAIReadyChipLabel(fill)
      : fill?.status === "FAILED"
        ? "AI 分析失败"
        : fill?.status === "STALE"
          ? "AI 草稿已过期"
          : `AI ${Math.max(0, Math.min(100, Number(fill?.progress_percent || 0)))}%`;
    return `<button class="ocw-mf-ai-progress-chip" type="button" data-action="mf-ai-progress-restore" data-mf-ai-progress-chip="1" ${active && minimized ? "" : "hidden"}><i></i><span>${this.escape(ocTranslate(label))}</span></button>`;
  }

  renderMaterialAIProgressDialogContent() {
    const fill = this.ensureMaterialFeeState().aiFill || {};
    if (fill.status === "PAYMENT_SELECTION") return this.renderMaterialAIPaymentSelection(fill);
    const progress = Math.max(0, Math.min(100, Number(fill.progress_percent || 0)));
    const sources = Array.isArray(fill.source_progress) ? fill.source_progress : [];
    const sourceGroups = this.materialAISourceGroups(sources);
    const sourceSummary = this.materialAISourceGroupSummary(sourceGroups);
    const summary = fill.completion_summary || {};
    const warning = this.materialAIProgressWarning(fill);
    const ready = this.isMaterialAIReadyStatus(fill.status);
    const failed = ["FAILED", "STALE"].includes(String(fill.status || ""));
    const canRetry = failed || Boolean(fill.stalled || fill.is_stalled || fill.connection_error || fill.polling_paused);
    const title = ready ? this.materialAIReadyTitle(fill) : fill.polling_paused ? "AI 状态读取已暂停" : failed ? "AI 分析未完成" : "AI 正在分析当前批次资料";
    return `<div class="ocw-mf-ai-progress-dialog" data-mf-ai-progress-host="1">
      <header><div><strong data-mf-ai-progress-title>${this.escape(ocTranslate(title))}</strong><span data-mf-ai-progress-step>${this.escape(ready ? this.materialAIReadyStep(fill) : (fill.progress_step || ocTranslate("等待读取资料")))}</span></div><b data-mf-ai-progress-percent>${progress}%</b></header>
      <main class="ocw-mf-ai-dialog-body">
      <div class="ocw-mf-ai-progress" aria-label="${ocTranslate('AI 分析进度')}"><i data-mf-ai-progress-bar style="width:${progress}%"></i></div>
      <div class="ocw-mf-ai-progress-summary">
        <span data-mf-ai-summary="source_count">${ocTranslate("资料 {0} 份", [sourceSummary.source_count])}</span>
        <span data-mf-ai-summary="material_proposal_count">${ocTranslate("物料 {0} 项", [Number(summary.material_proposal_count || 0)])}</span>
        <span data-mf-ai-summary="packing_proposal_count">${ocTranslate("装箱 {0} 项", [Number(summary.packing_proposal_count || 0)])}</span>
        <span data-mf-ai-summary="fee_proposal_count">${ocTranslate("费用 {0} 项", [Number(summary.fee_proposal_count || 0)])}</span>
        <span class="is-partial" data-mf-ai-summary="partial_source_count" ${sourceSummary.partial_source_count ? "" : "hidden"}>${ocTranslate("部分读取 {0} 份", [sourceSummary.partial_source_count])}</span>
        <span class="is-failed" data-mf-ai-summary="failed_source_count" ${sourceSummary.failed_source_count ? "" : "hidden"}>${ocTranslate("失败 {0} 份", [sourceSummary.failed_source_count])}</span>
      </div>
      <div class="ocw-mf-ai-source-progress" data-mf-ai-source-progress>${sourceGroups.map((group) => this.renderMaterialAIProgressSourceGroup(group)).join("")}<div class="ocw-mf-ai-progress-empty" data-mf-ai-progress-empty ${sourceGroups.length ? "hidden" : ""}>${ocTranslate("正在建立当前批次的资料清单…")}</div></div>
      <div class="ocw-mf-ai-progress-warning" data-mf-ai-progress-warning ${warning ? "" : "hidden"}>${this.escape(warning)}</div>
      </main>
      <footer class="ocw-mf-ai-dialog-footer">
        <button class="ocw-outline-btn" type="button" data-action="mf-ai-minimize">${ocTranslate("最小化")}</button>
        <div><button class="ocw-outline-btn" type="button" data-action="mf-ai-progress-retry" ${canRetry ? "" : "hidden"}>${ocTranslate("重试")}</button><button class="ocw-outline-btn" type="button" data-action="mf-ai-review-cancel">${ocTranslate("取消")}</button><button class="ocw-outline-btn" type="button" data-action="mf-ai-discard">${ocTranslate("放弃任务")}</button><button class="ocw-primary-btn" type="button" data-action="mf-ai-apply" disabled>${ocTranslate("确认填充")}</button></div>
      </footer>
    </div>`;
  }

  renderMaterialAIPaymentSelection(fill) {
    const scope = fill.paymentPreflight || {};
    const selected = fill.paymentSelection instanceof Set ? fill.paymentSelection : new Set();
    const choices = (scope.candidates || []).map((candidate) => {
      const id = String(candidate.candidate_id || "");
      return `<label class="ocw-mf-ai-payment-choice"><input type="checkbox" data-mf-ai-payment-candidate="${this.escape(id)}" ${selected.has(id) ? "checked" : ""}><span><strong>${this.escape(candidate.title || candidate.workflow_template || ocTranslate("支付流程"))}</strong><small>${this.escape(candidate.approval_no || ocTranslate("审批号待核对"))} · ${candidate.match_strength === "STRONG" ? ocTranslate("强匹配") : ocTranslate("弱匹配")}</small><small>${this.escape(candidate.match_reason || ocTranslate("请核对是否属于本票"))}</small></span></label>`;
    }).join("");
    return `<div class="ocw-mf-ai-progress-dialog is-payment-selection" data-mf-ai-progress-host="1"><header><div><strong>${ocTranslate("选择本次需要解析的支付流程")}</strong><span>${ocTranslate("仅选择资料范围；确认填充前不会修改业务数据。")}</span></div></header><main class="ocw-mf-ai-dialog-body"><p>${ocTranslate("规则已找到以下支付申请。可多选；如果都不属于本票，可跳过支付申请并继续读取国际物流和采购支出。")}</p><div class="ocw-mf-ai-payment-choices">${choices || `<p>${ocTranslate("未找到可用支付流程。")}</p>`}</div></main><footer class="ocw-mf-ai-dialog-footer"><button class="ocw-outline-btn" type="button" data-action="mf-ai-review-cancel">${ocTranslate("取消")}</button><div><button class="ocw-outline-btn" type="button" data-action="mf-ai-payment-skip">${ocTranslate("跳过支付申请")}</button><button class="ocw-primary-btn" type="button" data-action="mf-ai-payment-continue" ${selected.size ? "" : "disabled"}>${ocTranslate("使用所选来源并继续")}</button></div></footer></div>`;
  }

  materialAIProgressSourceKey(source, index) {
    if (source?.group_key) return String(source.group_key);
    return this.materialAISourceGroupKey(source, index).group_key;
  }

  materialAIProgressSourceView(source) {
    const status = this.materialAIProgressStatus(source?.status);
    const location = [source?.sheet ? `Sheet ${source.sheet}` : "", Number(source?.page_count || 0) ? ocTranslate("{0} 页", [Number(source.page_count)]) : "", Number(source?.field_count || 0) ? ocTranslate("{0} 个字段", [Number(source.field_count)]) : ""].filter(Boolean).join(" · ");
    return {
      status,
      label: String(source?.label || "未命名资料"),
      detail: [source?.approval_no ? ocTranslate("审批 {0}", [source.approval_no]) : "", source?.detail, location].filter(Boolean).join(" · ") || status.label,
      error: source?.error ? this.materialAIErrorMessage(source.error, "资料读取失败") : "",
      restriction: this.materialAISourceRestriction(source),
    };
  }

  renderMaterialAIProgressSourceRow(source, index) {
    const key = this.materialAIProgressSourceKey(source, index);
    const view = this.materialAIProgressSourceView(source);
    return `<article class="is-${view.status.tone}" data-mf-ai-source-key="${this.escape(key)}"><i></i><div><strong data-mf-ai-source-label>${this.escape(ocTranslate(view.label))}</strong><span data-mf-ai-source-detail>${this.escape(ocTranslate(view.detail))}</span><small data-mf-ai-source-restriction ${view.restriction ? "" : "hidden"}>${this.escape(view.restriction)}</small><small data-mf-ai-source-error ${view.error ? "" : "hidden"}>${this.escape(view.error)}</small></div><em data-mf-ai-source-status>${view.status.label}</em></article>`;
  }

  renderMaterialAIProgressSourceRecord(source, auditOnly = false) {
    const view = this.materialAIProgressSourceView(source);
    const sheet = this.materialAISourceSheetInfo(source);
    const identity = sheet.name ? `Sheet ${sheet.name}` : sheet.is_sheet ? "工作表记录" : "附件归档";
    return `<div class="ocw-mf-ai-source-record is-${view.status.tone}${auditOnly ? " is-audit" : ""}"><i></i><div><strong>${this.escape(identity)}</strong><span>${this.escape([source?.detail, Number(source?.field_count || 0) ? ocTranslate("{0} 个字段", [Number(source.field_count)]) : "", Number(source?.candidate_count || source?.result_count || 0) ? ocTranslate("{0} 个候选", [Number(source.candidate_count || source.result_count)]) : ""].filter(Boolean).join(" · ") || view.status.label)}</span>${view.error ? `<small>${this.escape(view.error)}</small>` : ""}</div><em>${view.status.label}</em>${auditOnly ? `<b>${ocTranslate("仅审计")}</b>` : ""}</div>`;
  }

  renderMaterialAIProgressSourceGroup(group) {
    const key = this.materialAIProgressSourceKey(group);
    const status = this.materialAIProgressStatus(group?.status);
    const primary = group?.primary || {};
    const sheets = Array.isArray(group?.sheet_names) ? group.sheet_names : [];
    const location = sheets.length === 1 ? `Sheet ${sheets[0]}` : sheets.length > 1 ? ocTranslate("{0} 个 Sheet", [sheets.length]) : "";
    const detail = [primary?.approval_no ? ocTranslate("审批 {0}", [primary.approval_no]) : "", location, primary?.detail,
      Number(group?.field_count || 0) ? ocTranslate("{0} 个字段", [Number(group.field_count)]) : "",
      Number(group?.candidate_count || 0) ? ocTranslate("{0} 个候选", [Number(group.candidate_count)]) : "",
      group?.status === "PARTIAL" ? ocTranslate("{0} 个工作表读取失败", [Number(group.failed_count || 0)]) : "",
    ].filter(Boolean).join(" · ") || status.label;
    const restriction = this.materialAISourceRestriction(primary);
    const error = group?.status === "FAILED" && primary?.error ? this.materialAIErrorMessage(primary.error, "资料读取失败") : "";
    const rows = Array.isArray(group?.rows) ? group.rows : [];
    const auditRows = Array.isArray(group?.audit_rows) ? group.audit_rows : [];
    const records = rows.length > 1 ? `<details class="ocw-mf-ai-source-records" data-mf-ai-source-records><summary>${ocTranslate("同步记录 {0} 条{1}", [rows.length, auditRows.length ? ocTranslate(" · {0} 条仅审计", [auditRows.length]) : ""])}</summary><div>${rows.map((source) => this.renderMaterialAIProgressSourceRecord(source, auditRows.includes(source))).join("")}</div></details>` : "";
    return `<article class="ocw-mf-ai-source-group is-${status.tone}" data-mf-ai-source-key="${this.escape(key)}"><i></i><div><strong data-mf-ai-source-label>${this.escape(group?.label || ocTranslate("未命名资料"))}</strong><span data-mf-ai-source-detail>${this.escape(detail)}</span><small data-mf-ai-source-restriction ${restriction ? "" : "hidden"}>${this.escape(restriction)}</small><small data-mf-ai-source-error ${error ? "" : "hidden"}>${this.escape(error)}</small>${records}</div><em data-mf-ai-source-status>${status.label}</em></article>`;
  }

  updateMaterialAIProgressSources($host, sources) {
    const $list = $host.find("[data-mf-ai-source-progress]");
    if (!$list.length) return;
    const groups = this.materialAISourceGroups(sources);
    const scrollTop = $list.scrollTop();
    const remaining = new Map();
    $list.children("[data-mf-ai-source-key]").each((_index, element) => {
      const $row = $(element);
      remaining.set(String($row.attr("data-mf-ai-source-key") || ""), $row);
    });
    groups.forEach((group) => {
      const key = this.materialAIProgressSourceKey(group);
      let $row = remaining.get(key);
      if (!$row?.length) {
        $list.find("[data-mf-ai-progress-empty]").before(this.renderMaterialAIProgressSourceGroup(group));
        $row = $list.children("[data-mf-ai-source-key]").last();
      } else {
        remaining.delete(key);
        const wasOpen = Boolean($row.find("[data-mf-ai-source-records]").prop("open"));
        const $next = $(this.renderMaterialAIProgressSourceGroup(group));
        $row.replaceWith($next);
        $row = $next;
        if (wasOpen) $row.find("[data-mf-ai-source-records]").prop("open", true);
      }
    });
    remaining.forEach(($row) => $row.remove());
    $list.find("[data-mf-ai-progress-empty]").prop("hidden", Boolean(groups.length));
    $list.scrollTop(scrollTop);
  }

  openMaterialAIReviewEvidence(dialog) {
    const $evidence = dialog?.$wrapper?.find("[data-mf-ai-review-evidence]").first();
    if (!$evidence?.length) return;
    $evidence.prop("open", true);
    const evidence = $evidence.get?.(0);
    evidence?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    evidence?.querySelector?.("summary")?.focus?.({ preventScroll: true });
  }

  openMaterialAIProgressDialog() {
    const state = this.ensureMaterialFeeState();
    if (!state.aiFill) return;
    state.aiProgressMinimized = false;
    if (!state.aiProgressDialog) {
      const dialog = new frappe.ui.Dialog({
        title: ocTranslate("AI填充资料"),
        fields: [{ fieldtype: "HTML", fieldname: "progress_html", options: this.renderMaterialAIProgressDialogContent() }],
      });
      state.aiProgressDialog = dialog;
      dialog.show();
      dialog.$wrapper.addClass("ocw-mf-ai-progress-modal");
      dialog.$wrapper.off("click.ocwAIProgress").on("click.ocwAIProgress", "[data-action]", (event) => {
        const action = String($(event.currentTarget).attr("data-action") || "");
        if (action === "mf-ai-minimize") {
          state.aiProgressMinimized = true;
          dialog.hide();
          this.updateMaterialAIProgressSurface();
        } else if (action === "mf-ai-review-cancel") {
          state.aiProgressMinimized = false;
          if (state.aiFill) state.aiFill.reviewDialogVisible = false;
          dialog.hide();
        } else if (action === "mf-ai-discard") {
          this.discardMaterialAIFill().catch((error) => this.showError(error));
        } else if (action === "mf-ai-change-sources") {
          this.restartMaterialAIWithSources(dialog).catch((error) => this.showError(error));
        } else if (action === "mf-ai-apply") {
          this.applyMaterialAIFill().catch((error) => this.showError(error));
        } else if (action === "mf-ai-row-all" || action === "mf-ai-row-none") {
          this.changeMaterialAIRowSelection("rows", "all", action === "mf-ai-row-all");
        } else if (action === "mf-ai-row-preview") {
          this.restartMaterialAIFromCurrentSources();
        } else if (action === "mf-ai-progress-retry") {
          this.retryMaterialAIProgress();
        } else if (action === "mf-ai-payment-continue") {
          this.continueMaterialAIPaymentSelection(false).catch((error) => this.showError(error));
        } else if (action === "mf-ai-payment-skip") {
          this.continueMaterialAIPaymentSelection(true).catch((error) => this.showError(error));
        } else if (action === "mf-ai-open-source") {
          this.openMaterialAISourceProcess($(event.currentTarget).attr("data-mf-ai-source-open-ref"));
        } else if (action === "mf-ai-adopt-field") {
          const $button = $(event.currentTarget);
          this.changeMaterialAIRowSelection("fields", $button.attr("data-mf-ai-field-key"), $button.attr("data-mf-ai-candidate-id"));
        } else if (action === "mf-ai-show-evidence") {
          this.openMaterialAIReviewEvidence(dialog);
        }
      });
      dialog.$wrapper
        .off("change.ocwAIReview input.ocwAIReview")
        .on("change.ocwAIReview", "[data-mf-ai-row-select],[data-mf-ai-fee-select],[data-mf-ai-field-select],[data-mf-ai-packing-assignment],[data-mf-ai-row-mode]", (event) => {
          const $input = $(event.currentTarget);
          if ($input.attr("data-mf-ai-row-mode") !== undefined) this.changeMaterialAIRowSelection("mode", $input.val());
          else if ($input.attr("data-mf-ai-field-select") !== undefined) this.changeMaterialAIRowSelection("fields", $input.attr("data-mf-ai-field-select"), $input.val());
          else if ($input.attr("data-mf-ai-packing-assignment") !== undefined) this.changeMaterialAIRowSelection("packingAssignments", $input.attr("data-mf-ai-packing-assignment"), $input.val());
          else this.changeMaterialAIRowSelection($input.attr("data-mf-ai-row-select") !== undefined ? "rows" : "fees", $input.attr("data-mf-ai-row-select") ?? $input.attr("data-mf-ai-fee-select"), $input.prop("checked"));
        })
        .on("change.ocwAIReview", "[data-mf-ai-proposal-select]", (event) => {
          const fill = state.aiFill;
          if (!fill?.selections) return;
          const proposalId = String($(event.currentTarget).attr("data-proposal-id") || "");
          const checked = $(event.currentTarget).prop("checked");
          const proposal = (fill.proposals || []).find((row) => String(row.proposal_id || "") === proposalId);
          if (checked && proposal?.conflict_group) {
            (fill.proposals || []).forEach((row) => {
              if (row.conflict_group === proposal.conflict_group) fill.selections.delete(String(row.proposal_id || ""));
            });
          }
          if (checked) fill.selections.add(proposalId);
          else fill.selections.delete(proposalId);
          this.renderMaterialAIReviewDialog();
        })
        .on("change.ocwAIReview", "[data-mf-ai-source-select]", (event) => {
          const fill = state.aiFill;
          const sourceId = String($(event.currentTarget).val() || "");
          const source = (fill?.source_progress || []).find((row) => String(row.source_id || "") === sourceId);
          if (this.materialAICanSelectSource(source)) {
            source.selected = Boolean($(event.currentTarget).prop("checked"));
          }
        })
        .on("change.ocwAIReview", "[data-mf-ai-payment-candidate]", (event) => {
          const fill = state.aiFill;
          if (fill?.status !== "PAYMENT_SELECTION") return;
          if (!(fill.paymentSelection instanceof Set)) fill.paymentSelection = new Set();
          const id = String($(event.currentTarget).attr("data-mf-ai-payment-candidate") || "");
          if ($(event.currentTarget).prop("checked")) fill.paymentSelection.add(id);
          else fill.paymentSelection.delete(id);
          state.aiProgressDialog.fields_dict.progress_html.$wrapper.html(this.renderMaterialAIProgressDialogContent());
        })
        .on("input.ocwAIReview", "[data-mf-ai-edit]", (event) => {
          this.updateSourceAIReviewEdit($(event.currentTarget));
        });
    } else {
      state.aiProgressDialog.show();
      const paymentSelectionChanged = state.aiFill?.status === "PAYMENT_SELECTION"
        && !state.aiProgressDialog.$wrapper.find(".is-payment-selection").length;
      if (paymentSelectionChanged || (!this.isMaterialAIReadyStatus(state.aiFill?.status)
          && !state.aiProgressDialog.$wrapper.find("[data-mf-ai-progress-host]").length)) {
        state.aiProgressDialog.$wrapper.removeClass("is-review");
        state.aiProgressDialog.fields_dict.progress_html.$wrapper.html(this.renderMaterialAIProgressDialogContent());
      }
    }
    this.updateMaterialAIProgressSurface();
    if (this.isMaterialAIReadyStatus(state.aiFill?.status)) this.showMaterialAIReadyDraft();
  }

  updateMaterialAIProgressSurface() {
    const state = this.ensureMaterialFeeState();
    const $chip = this.$root?.find?.("[data-mf-ai-progress-chip]");
    const fill = state.aiFill || {};
    const active = ["STARTING", "PAYMENT_SELECTION", "QUEUED", "RUNNING", "READY", "READY_WITH_WARNINGS", "FAILED", "STALE"].includes(String(fill.status || ""));
    const chipLabel = this.isMaterialAIReadyStatus(fill.status) ? this.materialAIReadyChipLabel(fill) : fill.status === "FAILED" ? "AI 分析失败" : fill.status === "STALE" ? "AI 草稿已过期" : `AI ${Math.max(0, Math.min(100, Number(fill.progress_percent || 0)))}%`;
    if ($chip?.length) {
      $chip.prop("hidden", !(active && state.aiProgressMinimized));
      $chip.find("span").text(chipLabel);
    }
    const $button = this.$root?.find?.("[data-action='mf-ai-fill']");
    if ($button?.length) {
      const status = String(state.aiFill?.status || "");
      $button.text(this.isMaterialAIReadyStatus(status) ? ocTranslate("查看填充预览") : ["STARTING", "PAYMENT_SELECTION", "QUEUED", "RUNNING"].includes(status) ? ocTranslate("查看填充进度") : ocTranslate("AI填充资料"));
    }
    const dialog = state.aiProgressDialog;
    if (dialog?.$wrapper?.length) {
      if (fill.status === "PAYMENT_SELECTION") {
        if (!dialog.$wrapper.find(".is-payment-selection").length) {
          dialog.fields_dict.progress_html.$wrapper.html(this.renderMaterialAIProgressDialogContent());
        }
        return;
      }
      const $host = dialog.$wrapper.find("[data-mf-ai-progress-host]");
      if (!$host.length) return;
      const progress = Math.max(0, Math.min(100, Number(fill.progress_percent || 0)));
      const ready = this.isMaterialAIReadyStatus(fill.status);
      const failed = ["FAILED", "STALE"].includes(String(fill.status || ""));
      const title = ready ? this.materialAIReadyTitle(fill) : fill.polling_paused ? "AI 状态读取已暂停" : failed ? "AI 分析未完成" : "AI 正在分析当前批次资料";
      const sources = Array.isArray(fill.source_progress) ? fill.source_progress : [];
      const sourceGroups = this.materialAISourceGroups(sources);
      const sourceSummary = this.materialAISourceGroupSummary(sourceGroups);
      const summary = fill.completion_summary || {};
      const warning = this.materialAIProgressWarning(fill);
      $host.find("[data-mf-ai-progress-title]").text(title);
      $host.find("[data-mf-ai-progress-step]").text(ready ? this.materialAIReadyStep(fill) : (fill.progress_step || ocTranslate("等待读取资料")));
      $host.find("[data-mf-ai-progress-percent]").text(`${progress}%`);
      $host.find("[data-mf-ai-progress-bar]").css("width", `${progress}%`);
      const labels = {
        source_count: ocTranslate("资料 {0} 份", [sourceSummary.source_count]),
        material_proposal_count: ocTranslate("物料 {0} 项", [Number(summary.material_proposal_count || 0)]),
        packing_proposal_count: ocTranslate("装箱 {0} 项", [Number(summary.packing_proposal_count || 0)]),
        fee_proposal_count: ocTranslate("费用 {0} 项", [Number(summary.fee_proposal_count || 0)]),
        partial_source_count: ocTranslate("部分读取 {0} 份", [sourceSummary.partial_source_count]),
        failed_source_count: ocTranslate("失败 {0} 份", [sourceSummary.failed_source_count]),
      };
      Object.entries(labels).forEach(([key, label]) => $host.find(`[data-mf-ai-summary='${key}']`).text(label));
      $host.find("[data-mf-ai-summary='partial_source_count']").prop("hidden", !sourceSummary.partial_source_count);
      $host.find("[data-mf-ai-summary='failed_source_count']").prop("hidden", !sourceSummary.failed_source_count);
      $host.find("[data-mf-ai-progress-warning]").text(warning).prop("hidden", !warning);
      $host.find("[data-action='mf-ai-progress-retry']").prop("hidden", !(failed || fill.stalled || fill.is_stalled || fill.connection_error || fill.polling_paused));
      this.updateMaterialAIProgressSources($host, sources);
    }
  }

  showMaterialAIReadyDraft() {
    const state = this.ensureMaterialFeeState();
    const ready = state.aiPendingReady || state.aiFill;
    if (!this.isMaterialAIReadyStatus(ready?.status)) return;
    state.aiFill = ready.selections instanceof Set
      ? ready
      : this.initializeMaterialAIDraft({ ...ready, draftVisible: true });
    state.aiFill.draftVisible = false;
    state.aiFill.reviewDialogVisible = true;
    state.aiPendingReady = null;
    state.aiProgressMinimized = false;
    this.renderMaterialAIReviewDialog();
    if (this.materialAIReviewCanUseSelection(state.aiFill) && !state.aiFill.rowSelection?.preview && !state.aiFill.rowSelection?.loading) this.scheduleMaterialAIRowPreview();
  }

  materialAIReadStatusLabel(source) {
    return {
      READ: ocTranslate("已读取"),
      PARTIAL: ocTranslate("部分读取"),
      FAILED: ocTranslate("读取失败"),
      NO_RESULT: ocTranslate("未产生结果"),
      EXCLUDED: ocTranslate("已排除"),
      NEEDS_SELECTION: ocTranslate("待选择 Sheet"),
    }[String(source?.read_status || "NO_RESULT")] || ocTranslate("未产生结果");
  }

  materialAICanSelectSource(source) {
    return Boolean(source) && !source.locked && source.selectable !== false && source.analysis_allowed !== false;
  }

  materialAIAuditSourceRows(sources) {
    return new Set(this.materialAISourceGroups(sources).flatMap((group) => group.audit_rows || []));
  }

  materialAISelectedSourceIds(sources) {
    const rows = Array.isArray(sources) ? sources : [];
    const auditRows = this.materialAIAuditSourceRows(rows);
    return rows.filter((source) => !auditRows.has(source) && source.selected && this.materialAICanSelectSource(source))
      .map((source) => String(source.source_id || "")).filter(Boolean);
  }

  materialAISourceRestriction(source) {
    if (source?.analysis_allowed === false) return ocTranslate("不可分析：{0}", [source.analysis_reason || source.adoption_restriction || "当前资料不可用于分析"]);
    if (source?.adoption_allowed === false) return ocTranslate("仅供分析：{0}", [source.adoption_restriction || "当前资料暂不能采用"]);
    return String(source?.adoption_restriction || source?.analysis_reason || "");
  }

  renderMaterialAIReviewSources(fill) {
    const sources = Array.isArray(fill?.source_progress) ? fill.source_progress : [];
    const groups = this.materialAISourceGroups(sources);
    const order = { READ: 0, PARTIAL: 1, NEEDS_SELECTION: 2, NO_RESULT: 3, FAILED: 4, EXCLUDED: 5 };
    const cards = [...groups].sort((left, right) => (order[left.read_status] ?? 9) - (order[right.read_status] ?? 9)).map((group) => {
      const rows = Array.isArray(group.rows) ? group.rows : [];
      const auditRows = Array.isArray(group.audit_rows) ? group.audit_rows : [];
      const records = rows.map((source) => {
        const auditOnly = auditRows.includes(source);
        const sheet = this.materialAISourceSheetInfo(source);
        const identity = [sheet.name ? `Sheet ${sheet.name}` : sheet.is_sheet ? "工作表记录" : "附件归档", source.actor_name || "", source.occurred_at || ""].filter(Boolean).join(" · ");
        const canToggle = this.materialAICanSelectSource(source);
        const restriction = this.materialAISourceRestriction(source);
        const input = auditOnly ? "" : `<input type="checkbox" data-mf-ai-source-select="1" value="${this.escape(source.source_id || "")}" ${source.selected && source.analysis_allowed !== false ? "checked" : ""} ${canToggle ? "" : "disabled"}>`;
        return `<label class="ocw-mf-ai-review-source-record is-${String(source.read_status || "no_result").toLowerCase()}${auditOnly ? " is-audit" : ""}">${input}<span><strong>${this.escape(identity || this.materialAIReadStatusLabel(source))}</strong>${restriction ? `<small data-mf-ai-source-restriction>${this.escape(restriction)}</small>` : ""}${source.error ? `<em>${this.escape(this.materialAIErrorMessage(source.error, "资料读取失败"))}</em>` : ""}<i>${ocTranslate("{0} 个候选 · {1}", [Number(source.result_count || source.candidate_count || 0), this.escape(source.parse_method || "NONE")])}</i></span>${auditOnly ? `<b>${ocTranslate("仅审计")}</b>` : source.locked ? `<b>${ocTranslate("锁定纳入")}</b>` : ""}</label>`;
      }).join("");
      return `<article class="ocw-mf-ai-review-source-group is-${String(group.read_status || "no_result").toLowerCase()}"><header><div><strong>${this.escape(ocTranslate(group.label))}</strong><small>${this.escape(group.primary?.approval_no ? ocTranslate("审批 {0}", [group.primary.approval_no]) : group.logical_source_id)}</small></div><b>${this.materialAIReadStatusLabel(group)}</b></header><details><summary>${ocTranslate("同步记录 {0} 条{1}", [rows.length, auditRows.length ? ocTranslate(" · {0} 条仅审计", [auditRows.length]) : ""])}</summary><div>${records}</div></details></article>`;
    }).join("");
    const catalog = fill?.row_review || {};
    const skippedStatuses = new Set(["SKIPPED", "UNREADABLE", "FAILED", "TIMEOUT", "FORBIDDEN", "MISSING", "UNSUPPORTED", "CORRUPT"]);
    const stageEvidence = [
      ...(Array.isArray(catalog.stage_snapshots) ? catalog.stage_snapshots.map(stage => ({ ...stage, evidence_type: "装箱字段" })) : []),
      ...(Array.isArray(catalog.fee_stage_snapshots) ? catalog.fee_stage_snapshots.map(stage => ({ ...stage, evidence_type: "费用" })) : []),
    ].map(stage => {
      const processRows = (Array.isArray(stage.processes) ? stage.processes : []).map(process => {
        const evidence = (Array.isArray(process.evidence) ? process.evidence : []).map(record => {
          const status = String(record.read_status || "NO_RESULT").toUpperCase();
          const failed = skippedStatuses.has(status);
          const safeReason = failed ? this.materialAIErrorMessage(record.skip_reason_text, "未能读取该资料") : "";
          return `<li class="is-${this.escape(status.toLowerCase())}"><span><strong>${this.escape(record.source_label || record.evidence_id || ocTranslate("资料"))}</strong><small>${this.escape(record.evidence_kind || ocTranslate("其他资料"))}</small></span><em>${this.escape(status)}</em>${failed ? `<p>${ocTranslate("{0}。已跳过，继续读取下一资料", [this.escape(safeReason)])}</p>` : ""}</li>`;
        }).join("");
        return `<article><header><strong>${this.escape(process.label || process.approval_no || ocTranslate("未命名流程"))}</strong>${process.approval_no ? `<small>${this.escape(process.approval_no)}</small>` : ""}</header>${evidence ? `<ul>${evidence}</ul>` : `<p>${ocTranslate("本流程未记录可展示的资料依据。")}</p>`}</article>`;
      }).join("");
      const warnings = (Array.isArray(stage.warnings) ? stage.warnings : []).filter(Boolean)
        .map(warning => this.materialAIErrorMessage(warning, "部分资料未能读取，已跳过。"));
      if (!processRows && !warnings.length) return "";
      return `<section class="ocw-mf-ai-review-stage-records"><h4>${this.escape(stage.stage_label || stage.stage || ocTranslate("其他阶段"))} · ${stage.evidence_type}</h4>${processRows}${warnings.length ? `<ul class="ocw-mf-ai-stage-warnings">${warnings.map(warning => `<li>${this.escape(warning)}</li>`).join("")}</ul>` : ""}</section>`;
    }).join("");
    const fieldCandidateRows = (Array.isArray(catalog.field_candidates) ? catalog.field_candidates : []).map(candidate => {
      const refs = (candidate.source_refs || []).map(ref => ref.source_id || ref.source_label).filter(Boolean);
      const equivalentIds = candidate.presentation_equivalent_candidate_ids || [];
      return `<tr><td>${this.escape(candidate.candidate_id || "--")}</td><td>${this.escape(candidate.source_label || refs.join(" / ") || ocTranslate("未标注来源"))}</td><td>${this.escape(candidate.fieldname || "--")}</td><td>${this.escape(candidate.suggested_value ?? "--")}</td><td>${this.escape(equivalentIds.join(" / ") || candidate.presentation_representative_candidate_id || candidate.candidate_id || "--")}</td><td>${candidate.can_apply ? ocTranslate("可采用") : ocTranslate("只读 · 不可采用")}</td></tr>`;
    }).join("");
    const feeRows = (Array.isArray(catalog.fees) ? catalog.fees : []).map(fee => {
      const values = fee.payload || fee;
      const role = String(fee.selection_role || "");
      const roleLabel = role === "component" ? "总额分项" : ({ primary_total: "应付总额", approved_quote: "已批准报价", ambiguous: "待核对", alternative: "其他记录" }[role] || "普通候选");
      const source = [values.source_label, values.remark, fee.reason].filter(Boolean).join(" · ");
      const reason = fee.blocked_reason || fee.resolution_reason || "";
      return `<tr data-mf-ai-fee-evidence="${this.escape(fee.proposal_id || "")}"><td>${this.escape(fee.proposal_id || "--")}</td><td>${this.escape(values.expense_category || values.logical_fee_key || "--")}</td><td>${this.escape(values.amount ?? "--")} ${this.escape(values.currency || "")}</td><td>${this.escape(fee.previous_amount ?? values.previous_amount ?? "--")}</td><td>${this.escape(roleLabel)}</td><td>${this.escape(source || "--")}</td><td>${this.escape(reason || "--")}${fee.can_apply ? "" : `<small>${ocTranslate("只读 · 不可采用")}</small>`}</td></tr>`;
    }).join("");
    const scope = catalog.material_scope_reason
      ? `<section class="ocw-mf-ai-review-scope"><h4>${ocTranslate("完整定行依据")}</h4><p>${this.escape(catalog.material_scope_reason)}${catalog.material_scope_fallback ? ocTranslate(" 已按安全顺序回落。") : ""}</p></section>`
      : "";
    return `<div class="ocw-mf-ai-review-evidence-content">${scope}<section class="ocw-mf-ai-review-sources"><h4>${ocTranslate("资料来源")} <span>${ocTranslate("{0} 份", [groups.length])}</span></h4><div>${cards || `<p>${ocTranslate("未找到可展示的资料来源。")}</p>`}</div></section>${stageEvidence}${fieldCandidateRows ? `<section><h4>${ocTranslate("字段候选依据")}</h4><div class="ocw-mf-ai-preview-table"><table><thead><tr><th>${ocTranslate("候选 ID")}</th><th>${ocTranslate("来源")}</th><th>${ocTranslate("字段")}</th><th>${ocTranslate("建议值")}</th><th>${ocTranslate("等价候选")}</th><th>${ocTranslate("状态")}</th></tr></thead><tbody>${fieldCandidateRows}</tbody></table></div></section>` : ""}${feeRows ? `<section><h4>${ocTranslate("费用依据与其他记录")}</h4><div class="ocw-mf-ai-preview-table"><table><thead><tr><th>${ocTranslate("记录 ID")}</th><th>${ocTranslate("费用项目")}</th><th>${ocTranslate("金额")}</th><th>${ocTranslate("原金额")}</th><th>${ocTranslate("类型")}</th><th>${ocTranslate("来源 / 说明")}</th><th>${ocTranslate("核对信息")}</th></tr></thead><tbody>${feeRows}</tbody></table></div></section>` : ""}</div>`;
  }

  renderCurrentSourceReviewControls(context = {}, cargo = null, { fees = false, values = {} } = {}) {
    if (context.root_kind !== "expense") return "";
    const cargoControl = cargo ? (cargo.complete
      ? `<label class="ocw-mf-review-choice"><input type="checkbox" data-source-review-complete ${values.complete_cargo ? "checked" : ""}><span>${ocTranslate("已核对完整货物表（{0} 行），采用为本票最终 SKU 和数量", [Number(cargo.row_count || cargo.rows?.length || 0)])}</span></label>`
      : `<p class="ocw-mf-review-warning">${this.escape(cargo.reason || ocTranslate("货物清单完整性待核对，仅作为候选。"))}</p>`) : "";
    const cargoRows = (cargo?.rows || []).map(row => `<tr>${[
      row.source_row, row.material_code, row.product_name,
      row.quantity ?? row.actual_shipped_qty, row.unit || row.shipped_uom,
      row.gross_weight_kg, row.volume_m3,
    ].map(value => `<td>${this.escape(value ?? "--")}</td>`).join("")}</tr>`).join("");
    const cargoTable = cargoRows ? `<p>${ocTranslate("本次来源货物清单（确认后按稳定行身份更新本票物料）")}</p><div style="max-height:260px;overflow:auto"><table class="table table-bordered"><thead><tr>${["原表行", "SKU", "品名", "数量", "单位", "毛重 kg", "体积 m³"].map(label => `<th>${ocTranslate(label)}</th>`).join("")}</tr></thead><tbody>${cargoRows}</tbody></table></div>` : "";
    const feeControls = fees ? `<p>${ocTranslate("本次费用明细须与采购支出总额核对一致；请选择它实际覆盖的费用。")}</p>${this.settlementCoverageOptions().map(([key, label]) => `<label class="ocw-mf-review-choice"><input type="checkbox" data-source-review-coverage="${key}" ${(values.coverage || []).includes(key) ? "checked" : ""}><span>${this.escape(ocTranslate(label))}</span></label>`).join("")}<label class="ocw-mf-review-choice"><input type="checkbox" data-source-review-negative ${values.negative_confirmed ? "checked" : ""}><span>${ocTranslate("已核对本次负数、折扣或冲抵（如有）")}</span></label>` : "";
    return `<section class="ocw-mf-dialog-note" data-current-source-review><strong>${ocTranslate("采购支出资料采用")}</strong>${cargoTable}${cargoControl}${feeControls}<label>${ocTranslate("核对说明")}<input data-source-review-reason value="${this.escape(values.reason || "")}" placeholder="${ocTranslate('记录本次核对依据')}" maxlength="1000"></label></section>`;
  }

  collectCurrentSourceReviewControls($wrapper) {
    return {
      complete_cargo: $wrapper.find("[data-source-review-complete]").is(":checked"),
      coverage: $wrapper.find("[data-source-review-coverage]:checked").map((_, node) => $(node).attr("data-source-review-coverage")).get(),
      negative_confirmed: $wrapper.find("[data-source-review-negative]").is(":checked"),
      reason: String($wrapper.find("[data-source-review-reason]").val() || "").trim(),
    };
  }

  renderMaterialAIReviewDialogContent() {
    const fill = this.ensureMaterialFeeState().aiFill || {};
    if (fill.row_review) return this.renderMaterialAIRowReview(fill);
    return this.renderMaterialAIReanalysisRequired({ legacy: true });
  }

  materialAIReviewHasStageSnapshots(fill) {
    const catalog = fill?.row_review;
    return Boolean(catalog && Array.isArray(catalog.stage_snapshots) && Array.isArray(catalog.fee_stage_snapshots));
  }

  materialAIReviewCanUseSelection(fill) {
    return this.materialAIReviewHasStageSnapshots(fill);
  }

  renderMaterialAIReanalysisRequired({ legacy = false } = {}) {
    const title = legacy ? "草稿规则已升级" : "填充预览";
    const message = legacy ? "请重新分析资料" : "资料处理规则已更新，请重新分析资料";
    const confirm = legacy ? "" : `<button class="ocw-primary-btn" type="button" data-action="mf-ai-apply" disabled>${ocTranslate("确认填充")}</button>`;
    return `<div class="ocw-mf-ai-review-dialog" data-mf-ai-review-host="1" data-mf-ai-review-stale="1"><header><div><strong>${title}</strong></div></header><main class="ocw-mf-ai-dialog-body"><div class="ocw-mf-ai-progress-warning"><strong>${message}</strong></div></main><footer class="ocw-mf-ai-dialog-footer"><span>${ocTranslate("确认前不会修改已保存数据")}</span><div><button class="ocw-outline-btn" type="button" data-action="mf-ai-review-cancel">${ocTranslate("取消")}</button><button class="ocw-outline-btn" type="button" data-action="mf-ai-discard">${ocTranslate("放弃草稿")}</button><button class="ocw-outline-btn" type="button" data-action="mf-ai-row-preview">${ocTranslate("重新分析")}</button>${confirm}</div></footer></div>`;
  }

  materialAIFeesOverlap(left, right) {
    const leftScopes = this.materialAIFeeScopes(left);
    const rightScopes = this.materialAIFeeScopes(right);
    if ([...leftScopes].some(scope => rightScopes.has(scope))) return true;
    const leftKey = String((left?.payload || left || {}).logical_fee_key || "");
    const rightKey = String((right?.payload || right || {}).logical_fee_key || "");
    if (leftKey && leftKey === rightKey) return true;
    if (left?.conflict_group && left.conflict_group === right?.conflict_group) return true;
    return !leftScopes.size && !rightScopes.size && !leftKey && !rightKey;
  }

  materialAIReviewFeePolicy(fill) {
    const fees = fill?.row_review?.fees || [];
    const eligibleFees = fees.filter(fee => {
      const role = String(fee.selection_role || "");
      if (["", "primary_total", "approved_quote"].includes(role)) return true;
      return role === "ambiguous" && fee.can_apply;
    });
    const mainGroups = this.materialAIPresentationGroups(eligibleFees);
    const mainFees = mainGroups.map(group => group.representative).filter(Boolean);
    const selectableIds = new Set(mainFees.filter(fee => fee.can_apply).map(fee => String(fee.proposal_id)));
    const representativeByCandidateId = new Map();
    mainGroups.forEach(group => group.equivalentIds.forEach(candidateId => {
      representativeByCandidateId.set(String(candidateId), group.representativeId);
    }));
    return { fees, mainFees, mainGroups, selectableIds, representativeByCandidateId };
  }

  materialAIFieldCandidateIndex(fieldCandidates) {
    const entries = (Array.isArray(fieldCandidates) ? fieldCandidates : []).map(candidate => ({
      candidate,
      candidateId: String(candidate.candidate_id || ""),
    })).filter(entry => entry.candidateId);
    const candidatesById = new Map(entries.map(entry => [entry.candidateId, entry.candidate]));
    const equivalentIdsByRepresentativeId = new Map();
    entries.forEach(({ candidate, candidateId }) => {
      if (String(candidate.presentation_representative_candidate_id || "") !== candidateId
        || !Array.isArray(candidate.presentation_equivalent_candidate_ids)) return;
      equivalentIdsByRepresentativeId.set(candidateId, new Set(candidate.presentation_equivalent_candidate_ids.map(String)));
    });
    return { entries, candidatesById, equivalentIdsByRepresentativeId };
  }

  materialAIFieldRepresentativeCandidateId(candidateIndex, fieldKey, candidateId) {
    const selectedId = String(candidateId || "");
    const selected = candidateIndex?.candidatesById?.get(selectedId);
    if (!selected?.can_apply) return "";
    const expectedKey = `${selected.item_name}:${selected.fieldname}`;
    if (expectedKey !== String(fieldKey || "")) return "";
    const groupId = String(selected.presentation_group_id || "");
    const representativeId = String(selected.presentation_representative_candidate_id || "");
    if (!groupId || !representativeId) return "";
    const representative = candidateIndex.candidatesById.get(representativeId);
    if (!representative?.can_apply
      || `${representative.item_name}:${representative.fieldname}` !== expectedKey
      || String(representative.presentation_group_id || "") !== groupId
      || !candidateIndex.equivalentIdsByRepresentativeId.get(representativeId)?.has(selectedId)) return "";
    return representativeId;
  }

  ensureMaterialAIRowSelection(fill, candidateIndex = null) {
    const feePolicy = this.materialAIReviewFeePolicy(fill);
    const fieldCandidates = Array.isArray(fill.row_review.field_candidates) ? fill.row_review.field_candidates : [];
    const fieldCandidateIndex = candidateIndex || this.materialAIFieldCandidateIndex(fieldCandidates);
    const packingCandidates = Array.isArray(fill.draft?.packing_group_candidates) ? fill.draft.packing_group_candidates : [];
    if (!fill.rowSelection) {
      const packingDefaults = new Map();
      packingCandidates.forEach((candidate) => {
        const defaults = (candidate.assignment_options || []).filter((option) => option.can_apply && option.default_selected);
        if (defaults.length === 1) packingDefaults.set(String(candidate.candidate_id), String(defaults[0].assignment_id));
      });
      const fieldDefaults = new Map();
      fieldCandidateIndex.entries.filter(entry => entry.candidate.default_selected).forEach(({ candidate, candidateId }) => {
        const key = `${candidate.item_name}:${candidate.fieldname}`;
        const representativeId = this.materialAIFieldRepresentativeCandidateId(fieldCandidateIndex, key, candidateId);
        if (representativeId) fieldDefaults.set(key, representativeId);
      });
      fill.rowSelection = {
        mode: "update_selected",
        rows: new Set(fieldCandidates.length ? [] : (fill.row_review.rows || []).filter(row => row.can_update && (row.default_update_selected ?? row.default_selected)).map(row => String(row.row_id))),
        fields: fieldDefaults,
        packingAssignments: packingDefaults,
        fees: new Set(feePolicy.mainGroups.filter(group => group.canApply
          && group.candidates.some(fee => fee.default_selected)).map(group => group.representativeId)),
        request: 0, loading: false, preview: null, error: "", timer: null,
      };
    } else {
      if (!(fill.rowSelection.fields instanceof Map)) fill.rowSelection.fields = new Map();
      if (!(fill.rowSelection.packingAssignments instanceof Map)) fill.rowSelection.packingAssignments = new Map();
      fill.rowSelection.fees.forEach(id => {
        const candidateId = String(id);
        const representativeId = feePolicy.representativeByCandidateId.get(candidateId);
        if (representativeId && feePolicy.selectableIds.has(representativeId)) {
          if (representativeId !== candidateId) {
            fill.rowSelection.fees.delete(candidateId);
            fill.rowSelection.fees.add(representativeId);
          }
        } else fill.rowSelection.fees.delete(candidateId);
      });
      fill.rowSelection.fields.forEach((candidateId, key) => {
        const representativeId = this.materialAIFieldRepresentativeCandidateId(fieldCandidateIndex, key, candidateId);
        if (representativeId) fill.rowSelection.fields.set(key, representativeId);
        else fill.rowSelection.fields.delete(key);
      });
      const validPackingAssignments = new Map(packingCandidates.map((candidate) => [
        String(candidate.candidate_id),
        new Set((candidate.assignment_options || []).filter((option) => option.can_apply).map((option) => String(option.assignment_id))),
      ]));
      fill.rowSelection.packingAssignments.forEach((assignmentId, candidateId) => {
        if (!validPackingAssignments.get(String(candidateId))?.has(String(assignmentId))) {
          fill.rowSelection.packingAssignments.delete(candidateId);
        }
      });
    }
    return fill.rowSelection;
  }

  materialAIRowSelectionKey(fill, candidateIndex = null) {
    const selection = this.ensureMaterialAIRowSelection(fill, candidateIndex);
    return JSON.stringify([this.detailState.batchName, this.detailState.versionName, fill.runId || fill.run_id,
      fill.row_review.fingerprint, selection.mode, [...selection.rows].sort(), [...selection.fields.entries()].sort(),
      [...selection.packingAssignments.entries()].sort(), [...selection.fees].sort()]);
  }

  changeMaterialAIRowSelection(kind, id, checked) {
    const fill = this.ensureMaterialFeeState().aiFill;
    if (!this.materialAIReviewCanUseSelection(fill) || fill.applying || fill.discarding) return;
    const candidateIndex = this.materialAIFieldCandidateIndex(fill.row_review.field_candidates);
    const selection = this.ensureMaterialAIRowSelection(fill, candidateIndex);
    const rows = fill.row_review.rows || [];
    const feePolicy = this.materialAIReviewFeePolicy(fill);
    if (kind === "mode") {
      if (!["fill_missing", "update_selected", "add_selected"].includes(id)) return;
      selection.mode = id;
      selection.rows.forEach(rowId => {
        const row = rows.find(item => String(item.row_id) === rowId);
        const allowed = id === "update_selected" ? row?.can_update : id === "add_selected" ? row?.can_add : row?.can_fill;
        if (!row || !allowed) selection.rows.delete(rowId);
      });
      if (id === "add_selected") { selection.fees.clear(); selection.fields.clear(); selection.packingAssignments.clear(); }
      else if (!(selection.fields.size) && (fill.row_review.field_candidates || []).length) {
        candidateIndex.entries.filter(entry => entry.candidate.default_selected).forEach(({ candidate, candidateId }) => {
          const key = `${candidate.item_name}:${candidate.fieldname}`;
          const representativeId = this.materialAIFieldRepresentativeCandidateId(candidateIndex, key, candidateId);
          if (representativeId) selection.fields.set(key, representativeId);
        });
      }
      if (id !== "add_selected" && !selection.packingAssignments.size) {
        (fill.draft?.packing_group_candidates || []).forEach((candidate) => {
          const defaults = (candidate.assignment_options || []).filter((option) => option.can_apply && option.default_selected);
          if (defaults.length === 1) selection.packingAssignments.set(String(candidate.candidate_id), String(defaults[0].assignment_id));
        });
      }
    } else if (kind === "fields") {
      if (!checked) selection.fields.delete(String(id));
      else {
        const representativeId = this.materialAIFieldRepresentativeCandidateId(candidateIndex, id, checked);
        if (representativeId) selection.fields.set(String(id), representativeId);
      }
    } else if (kind === "packingAssignments") {
      const candidate = (fill.draft?.packing_group_candidates || []).find(row => String(row.candidate_id) === String(id));
      const option = (candidate?.assignment_options || []).find(row => String(row.assignment_id) === String(checked));
      if (option?.can_apply && selection.mode !== "add_selected") selection.packingAssignments.set(String(id), String(option.assignment_id));
      else selection.packingAssignments.delete(String(id));
    } else {
      const items = kind === "rows" ? rows : feePolicy.mainFees;
      for (const item of items) {
        const itemId = String(kind === "rows" ? item.row_id : item.proposal_id);
        const allowed = kind === "fees" ? selection.mode !== "add_selected" && feePolicy.selectableIds.has(itemId)
          : selection.mode === "update_selected" ? item.can_update : selection.mode === "add_selected" ? item.can_add : item.can_fill;
        if (id !== "all" && itemId !== id) continue;
        if (checked && allowed) {
          if (kind === "fees") {
            items.forEach(candidate => {
              const candidateId = String(candidate.proposal_id);
              if (candidateId === itemId || !selection.fees.has(candidateId)) return;
              if (this.materialAIFeesOverlap(item, candidate)) {
                selection.fees.delete(candidateId);
              }
            });
          }
          selection[kind].add(itemId);
        }
        else selection[kind].delete(itemId);
      }
    }
    this.scheduleMaterialAIRowPreview(candidateIndex);
  }

  scheduleMaterialAIRowPreview(candidateIndex = null) {
    const fill = this.ensureMaterialFeeState().aiFill;
    if (!this.materialAIReviewCanUseSelection(fill) || fill.applying || fill.discarding) return;
    const selection = this.ensureMaterialAIRowSelection(fill, candidateIndex);
    clearTimeout(selection.timer);
    selection.request += 1;
    selection.preview = null;
    selection.loading = true;
    selection.error = "";
    this.renderMaterialAIReviewDialog();
    selection.timer = setTimeout(() => {
      if (this.ensureMaterialFeeState().aiFill === fill) this.previewMaterialAIRowSelection();
    }, 180);
  }

  async previewMaterialAIRowSelection() {
    const state = this.ensureMaterialFeeState();
    const fill = state.aiFill;
    if (!this.materialAIReviewCanUseSelection(fill) || fill.applying || fill.discarding) return;
    const candidateIndex = this.materialAIFieldCandidateIndex(fill.row_review.field_candidates);
    const selection = this.ensureMaterialAIRowSelection(fill, candidateIndex);
    const fieldChoices = {};
    selection.fields.forEach((candidateId, fieldKey) => {
      const representativeId = this.materialAIFieldRepresentativeCandidateId(candidateIndex, fieldKey, candidateId);
      if (representativeId) fieldChoices[fieldKey] = representativeId;
    });
    clearTimeout(selection.timer);
    const request = ++selection.request;
    const key = this.materialAIRowSelectionKey(fill, candidateIndex);
    const current = () => this.materialFeeState === state && state.aiFill === fill && request === selection.request
      && key === this.materialAIRowSelectionKey(fill, candidateIndex) && this.detailState.tab === "documents";
    const previous = selection.inFlight;
    let release;
    const inFlight = new Promise(resolve => { release = resolve; });
    selection.inFlight = inFlight;
    selection.preview = null; selection.loading = true; selection.error = "";
    this.renderMaterialAIReviewDialog();
    try {
      // Preserve server ordering. Intermediate queued selections exit before making a request.
      if (previous) await previous;
      if (!current()) return;
      const result = await this.call("overseas_costing.api.materials.preview_source_ai_selection", {
        batch_name: state.batchName, run_id: fill.runId || fill.run_id,
        row_ids_json: JSON.stringify([...selection.rows]), fee_ids_json: JSON.stringify([...selection.fees]),
        ...((fill.row_review.field_candidates || []).length ? { field_choices_json: JSON.stringify(fieldChoices) } : {}),
        ...((fill.draft?.packing_group_candidates || []).length ? { packing_assignments_json: JSON.stringify(Object.fromEntries(selection.packingAssignments)) } : {}),
        mode: selection.mode, expected_version: this.detailState.versionName || null,
      }, false);
      if (!current()) return;
      if (!result?.ok || !result.preview) throw new Error(result?.message || "未取得有效预览，请重新预览。");
      if (result.row_review) fill.row_review = result.row_review;
      // The server owns the final rows, counts and eligibility; never merge business values here.
      selection.preview = result.preview;
      selection.previewKey = this.materialAIRowSelectionKey(fill);
    } catch (error) {
      if (current()) selection.error = this.materialAIErrorMessage(error, "服务器预览失败，本次未保存，请稍后重试。");
    } finally {
      release();
      if (selection.inFlight === inFlight) selection.inFlight = null;
      if (this.materialFeeState === state && state.aiFill === fill && request === selection.request) {
        selection.loading = false;
        this.renderMaterialAIReviewDialog();
      }
    }
  }

  canConfirmMaterialAIRowSelection(fill) {
    if (!this.materialAIReviewCanUseSelection(fill)) return false;
    const selection = this.ensureMaterialAIRowSelection(fill);
    return this.isMaterialAIReadyStatus(fill.status) && !this.isMaterialFeeCalculationBusy() && !fill.applying && !fill.discarding && !selection.loading
      && selection.preview?.can_apply === true && selection.previewKey === this.materialAIRowSelectionKey(fill);
  }

  materialAIStageStatusLabel(status) {
    const normalized = String(status || "UNAVAILABLE").toUpperCase();
    if (normalized === "AVAILABLE") return ocTranslate("可用");
    if (normalized === "PARTIAL") return ocTranslate("部分可用");
    return ocTranslate("未找到有效资料");
  }

  materialAIStageSourceActions(stage) {
    const buttonLabels = {
      payment: "打开支付申请原单",
      international_logistics: "打开国际物流原单",
      purchase: "打开采购支出原单",
    };
    const seen = new Set();
    const processes = (Array.isArray(stage?.processes) ? stage.processes : []).filter((process) => {
      const ref = String(process?.source_open_ref || "");
      if (!process?.can_open || !ref || seen.has(ref)) return false;
      seen.add(ref);
      return true;
    });
    if (!processes.length) return "";
    const buttonLabel = buttonLabels[String(stage.stage || "")] || "打开原单";
    return `<div class="ocw-mf-ai-stage-sources"><strong>${ocTranslate("相关原单")}</strong><div>${processes.map((process) => `<span><b>${this.escape(process.label || process.approval_no || ocTranslate("钉钉审批"))}</b>${process.approval_no ? `<small>${this.escape(process.approval_no)}</small>` : ""}<button type="button" class="ocw-outline-btn ocw-mini-btn" data-action="mf-ai-open-source" data-mf-ai-source-open-ref="${this.escape(process.source_open_ref)}">${this.escape(buttonLabel)}</button></span>`).join("")}</div></div>`;
  }

  async openMaterialAISourceProcess(sourceOpenRef) {
    const state = this.ensureMaterialFeeState();
    const fill = state.aiFill;
    try {
      const result = await this.call("overseas_costing.api.materials.get_source_ai_process_open_target", {
        batch_name: this.detailState.batchName,
        run_id: fill?.runId || fill?.run_id || "",
        source_open_ref: String(sourceOpenRef || ""),
      }, false);
      if (!result?.ok || !result.open_url) throw new Error(ocTranslate("source_unavailable"));
      this.openSettlementSource(result);
    } catch (_error) {
      frappe.show_alert({ message: ocTranslate("原单链接已失效，请重新读取资料源"), indicator: "orange" });
    }
  }

  materialAIFirstActionableStageIndex(stages, isActionable) {
    return stages.findIndex(stage => Boolean(isActionable(stage)));
  }

  materialAIStageUnreadableCount(stage) {
    const failed = new Set(["UNREADABLE", "FAILED", "TIMEOUT", "FORBIDDEN", "MISSING", "UNSUPPORTED", "CORRUPT"]);
    const evidenceCount = (stage?.processes || []).reduce((count, process) => count + (process.evidence || [])
      .filter(record => failed.has(String(record.read_status || record.status || "").toUpperCase())).length, 0);
    return evidenceCount;
  }

  renderMaterialAIStageWarning(stage) {
    const count = this.materialAIStageUnreadableCount(stage);
    const needsReview = Array.isArray(stage?.warnings) && stage.warnings.some(Boolean);
    if (!count && !needsReview) return "";
    const labels = [count ? ocTranslate("{0} 份资料未读取", [count]) : "", needsReview ? ocTranslate("需核对") : ""].filter(Boolean).join(" · ");
    return `<p class="ocw-mf-ai-stage-warning"><span>${labels}</span><button type="button" class="ocw-link-btn" data-action="mf-ai-show-evidence">${ocTranslate("查看依据")}</button></p>`;
  }

  materialAIStageMaterialRows(stage, candidatesById) {
    const materialRows = new Map();
    (Array.isArray(stage?.rows) ? stage.rows : []).forEach((row, rowIndex) => {
      const fields = Object.entries(row.field_candidates || {}).map(([fieldname, candidateIds]) => {
        const ids = (Array.isArray(candidateIds) ? candidateIds : [candidateIds]).filter(Boolean).map(String)
          .filter(id => candidatesById.has(id));
        return [fieldname, ids];
      }).filter(([, ids]) => ids.length);
      if (!fields.length) return;
      const firstCandidate = candidatesById.get(fields[0][1][0]) || {};
      const materialKey = String(firstCandidate.item_name || row.item_name || row.material_stable_key || `row:${row.row_id || rowIndex}`);
      if (!materialRows.has(materialKey)) materialRows.set(materialKey, {
        materialKey, item_name: firstCandidate.item_name || row.item_name || "",
        material_code: row.material_code || "", product_name: row.product_name || "", field_candidates: {},
      });
      const aggregate = materialRows.get(materialKey);
      if (!aggregate.material_code && row.material_code) aggregate.material_code = row.material_code;
      if (!aggregate.product_name && row.product_name) aggregate.product_name = row.product_name;
      fields.forEach(([fieldname, ids]) => {
        aggregate.field_candidates[fieldname] = [...new Set([...(aggregate.field_candidates[fieldname] || []), ...ids])];
      });
    });
    return [...materialRows.values()];
  }

  materialAIPresentationGroups(candidates, candidatesById = null) {
    const entries = Array.isArray(candidates) ? candidates : [];
    const idOf = candidate => String(candidate?.candidate_id || candidate?.proposal_id || "");
    const byId = candidatesById instanceof Map
      ? candidatesById
      : new Map(entries.map(candidate => [idOf(candidate), candidate]).filter(([candidateId]) => candidateId));
    const groups = new Map();
    entries.forEach(candidate => {
      const candidateId = idOf(candidate);
      if (!candidateId) return;
      const groupId = String(candidate.presentation_group_id || `candidate:${candidateId}`);
      if (!groups.has(groupId)) groups.set(groupId, { groupId, candidates: [] });
      groups.get(groupId).candidates.push(candidate);
    });
    return [...groups.values()].map(group => {
      const firstId = idOf(group.candidates[0]);
      const representativeId = String(group.candidates[0]?.presentation_representative_candidate_id || firstId);
      const representative = byId.get(representativeId) || group.candidates[0];
      const equivalentIds = new Set([representativeId, ...group.candidates.map(idOf), ...(representative?.presentation_equivalent_candidate_ids || []).map(String)]);
      const canApply = group.candidates.some(candidate => candidate.can_apply) && Boolean(representative?.can_apply);
      return { ...group, representativeId, representative, equivalentIds, canApply };
    });
  }

  renderMaterialAIFieldChoice({ candidates, candidatesById, key, material, label, selection, value, busy }) {
    const groups = this.materialAIPresentationGroups(candidates, candidatesById);
    const applicableGroups = groups.filter(group => group.canApply);
    const readonlyGroups = groups.filter(group => !group.canApply);
    const selectedId = String(selection.fields.get(key) || "");
    const selectedGroup = groups.find(group => group.equivalentIds.has(selectedId));
    const readonly = readonlyGroups.map(group => `<span class="ocw-mf-ai-field-readonly" data-mf-ai-presentation-group="${this.escape(group.groupId)}">${value(group.representative?.suggested_value)}<b>${ocTranslate("只读")}</b></span>`).join("");
    const conflict = groups.length > 1
      ? `<span class="ocw-mf-ai-field-conflict-meta"><em>${ocTranslate("{0} 个值", [groups.length])}</em><button type="button" class="ocw-link-btn" data-action="mf-ai-show-evidence">${ocTranslate("查看依据")}</button></span>`
      : "";
    if (!applicableGroups.length) return `${readonly}${conflict}`;
    if (applicableGroups.length === 1) {
      const group = applicableGroups[0];
      const display = value(group.representative?.suggested_value);
      const control = selectedGroup === group
        ? `<span class="ocw-mf-ai-field-selected" data-mf-ai-field-selected="${this.escape(group.groupId)}">${display}<b aria-label="${ocTranslate('已采用')}">✓</b></span>`
        : `<button type="button" class="ocw-outline-btn ocw-mini-btn ocw-mf-ai-field-adopt" data-action="mf-ai-adopt-field" data-mf-ai-field-key="${this.escape(key)}" data-mf-ai-candidate-id="${this.escape(group.representativeId)}" ${busy}>${ocTranslate("采用 {0}", [display])}</button>`;
      return `${control}${readonly}${conflict}`;
    }
    const options = [`<option value="" ${applicableGroups.includes(selectedGroup) ? "" : "selected"}>${ocTranslate("不采用")}</option>`, ...applicableGroups.map(group => `<option value="${this.escape(group.representativeId)}" ${selectedGroup === group ? "selected" : ""}>${value(group.representative?.suggested_value)}</option>`)].join("");
    return `<select data-mf-ai-field-select="${this.escape(key)}" aria-label="${this.escape(material)} ${this.escape(ocTranslate(label))}" ${busy}>${options}</select>${readonly}${conflict}`;
  }

  renderMaterialAIPackingStages(fill, selection, fieldColumnOrder, fieldLabels, value, busy, itemLabel) {
    const catalog = fill.row_review || {};
    const snapshots = new Map((catalog.stage_snapshots || []).map(stage => [String(stage.stage || ""), stage]));
    const specs = [["payment", 0, "支付申请"], ["international_logistics", 1, "国际物流"], ["purchase", 2, "采购支出"]];
    const candidatesById = new Map((catalog.field_candidates || []).map(candidate => [String(candidate.candidate_id), candidate]));
    const stages = specs.map(([stage, rank, label]) => ({ status: "UNAVAILABLE", processes: [], rows: [], warnings: [], ...(snapshots.get(stage) || {}), stage, stage_rank: rank, stage_label: label }));
    const stageViews = stages.map(stage => ({ stage, rows: this.materialAIStageMaterialRows(stage, candidatesById) }));
    const sharedFields = new Set(stageViews.flatMap(view => view.rows.flatMap(row => Object.keys(row.field_candidates || {}))));
    const sharedColumns = fieldColumnOrder.filter(([fieldname]) => sharedFields.has(fieldname));
    const actionable = view => view.rows.some(row => Object.values(row.field_candidates || {}).some(ids => ids.some(id => candidatesById.get(String(id))?.can_apply)));
    const openIndex = this.materialAIFirstActionableStageIndex(stageViews, actionable);
    const panels = stageViews.map((view, index) => {
      const { stage, rows } = view;
      const stageColumns = sharedColumns;
      const selectedCount = rows.reduce((count, row) => count + Object.entries(row.field_candidates || {}).filter(([fieldname, candidateIds]) => {
        const candidates = candidateIds.map(id => candidatesById.get(String(id))).filter(Boolean);
        const keyItem = candidates[0]?.item_name || row.item_name || "";
        const selectedId = String(selection.fields.get(`${keyItem}:${fieldname}`) || "");
        return this.materialAIPresentationGroups(candidates, candidatesById)
          .some(group => group.equivalentIds.has(selectedId));
      }).length, 0);
      const matrixRows = rows.map(row => {
        const material = [row.material_code, row.product_name && String(row.product_name) !== String(row.material_code) ? row.product_name : ""].filter(Boolean).join(" · ") || itemLabel(row.item_name || row.material_stable_key);
        const fieldCells = stageColumns.map(([fieldname]) => {
          const ids = (Array.isArray(row.field_candidates?.[fieldname]) ? row.field_candidates[fieldname] : [row.field_candidates?.[fieldname]]).filter(Boolean).map(String);
          const candidates = ids.map(id => candidatesById.get(id)).filter(Boolean);
          if (!candidates.length) return '<td class="is-empty">—</td>';
          const keyItem = candidates[0]?.item_name || row.item_name || "";
          const key = `${keyItem}:${fieldname}`;
          const groups = this.materialAIPresentationGroups(candidates, candidatesById);
          return `<td class="${groups.length > 1 ? "is-conflict" : ""}">${this.renderMaterialAIFieldChoice({ candidates, candidatesById, key, material, label: fieldLabels[fieldname] || fieldname, selection, value, busy })}</td>`;
        }).join("");
        return `<tr data-mf-ai-material-row="${this.escape(row.materialKey)}"><th scope="row">${value(material)}</th>${fieldCells}</tr>`;
      }).join("");
      const empty = `<tr><td colspan="${stageColumns.length + 1}" class="is-empty">${ocTranslate("无可用资料")}</td></tr>`;
      const status = String(stage.status || "UNAVAILABLE").toUpperCase();
      const hasActionable = actionable(view);
      return `<details class="ocw-mf-ai-stage-panel is-${this.escape(status.toLowerCase())}" data-mf-ai-packing-stage="${this.escape(stage.stage)}" ${index === openIndex ? "open" : ""}><summary><strong>${this.escape(stage.stage_label)}</strong><span><b>${hasActionable ? this.escape(this.materialAIStageStatusLabel(status)) : ocTranslate("无可用资料")}</b>${ocTranslate("已选 {0} 项", [selectedCount])}</span></summary>${this.materialAIStageSourceActions(stage)}${this.renderMaterialAIStageWarning(stage)}<div class="ocw-mf-ai-preview-table"><table class="ocw-mf-ai-stage-matrix"><thead><tr><th>${ocTranslate("物料")}</th>${stageColumns.map(([, label]) => `<th>${this.escape(ocTranslate(label))}</th>`).join("")}</tr></thead><tbody>${matrixRows || empty}</tbody></table></div></details>`;
    }).join("");
    const assignedIds = new Set(stages.flatMap(stage => (stage.rows || []).flatMap(row => Object.values(row.field_candidates || {}).flat().map(String))));
    const unclassified = (catalog.field_candidates || []).filter(candidate => !assignedIds.has(String(candidate.candidate_id)));
    const unclassifiedGroups = new Map();
    unclassified.forEach(candidate => {
      const key = `${candidate.item_name}:${candidate.fieldname}`;
      if (!unclassifiedGroups.has(key)) unclassifiedGroups.set(key, { key, itemName: candidate.item_name, fieldname: candidate.fieldname, candidates: [] });
      unclassifiedGroups.get(key).candidates.push(candidate);
    });
    const unclassifiedRows = [...unclassifiedGroups.values()].map(({ key, itemName, fieldname, candidates }) => {
      const source = candidates.map(candidate => candidate.source_label || candidate.priority_reason || "未分类资料").filter(Boolean).join(" / ");
      const candidateIds = candidates.map(candidate => String(candidate.candidate_id)).join(",");
      const control = this.renderMaterialAIFieldChoice({ candidates, candidatesById, key, material: itemLabel(itemName), label: fieldLabels[fieldname] || fieldname, selection, value, busy });
      return `<tr data-mf-ai-unclassified-candidates="${this.escape(candidateIds)}"><td>${value(itemLabel(itemName))}</td><td>${this.escape(fieldLabels[fieldname] || fieldname)}</td><td>${control}</td><td>${this.escape(source)}</td></tr>`;
    }).join("");
    const unclassifiedSection = unclassified.length ? `<details class="ocw-mf-ai-unclassified" data-mf-ai-unclassified-packing="1"><summary>${ocTranslate("其他可选字段")} <span>${ocTranslate("{0} 个", [unclassified.length])}</span></summary><div class="ocw-mf-ai-preview-table"><table><thead><tr><th>${ocTranslate("物料")}</th><th>${ocTranslate("字段")}</th><th>${ocTranslate("候选值")}</th><th>${ocTranslate("资料来源")}</th></tr></thead><tbody>${unclassifiedRows}</tbody></table></div></details>` : "";
    return `<section class="ocw-mf-ai-preview-section" data-mf-ai-field-candidates><h4>${ocTranslate("装箱资料")} <span>${ocTranslate("已选 {0}", [selection.fields.size])}</span></h4>${panels}${unclassifiedSection}</section>`;
  }

  materialAIFeeScopes(fee) {
    const values = fee?.payload || fee || {};
    const explicit = values.covered_scopes ?? fee?.covered_scopes;
    if (Array.isArray(explicit)) return new Set(explicit.map(String).map(value => value.trim()).filter(Boolean));
    if (String(explicit || "").trim()) return new Set(String(explicit).split(",").map(value => value.trim()).filter(Boolean));
    const keys = `${values.logical_fee_key || ""} ${values.rule_code || ""}`.toLowerCase();
    if (keys.includes("mexico_inland") || keys.includes("destination_delivery")) return new Set(["mexico_inland"]);
    if (keys.includes("customs") || keys.includes("clearance")) return new Set(["customs"]);
    if (keys.includes("tax")) return new Set(["tax"]);
    if (["freight", "ocean", "international_express_fee", "express_surcharge", "forwarder_surcharge", "port_and_forwarder_charges"].some(token => keys.includes(token))) return new Set(["freight"]);
    return new Set();
  }

  renderMaterialAIFeeChoiceRow(fee, selection, value, disabled = false) {
    const values = fee.payload || fee;
    const readonly = selection.mode === "add_selected" || !fee.can_apply || disabled;
    const originLabels = { SYSTEM: "系统直读", AI: "AI识别" };
    const presentationSources = Array.isArray(fee.presentation_sources) ? fee.presentation_sources : [];
    const labelCounts = presentationSources.reduce((counts, source) => {
      const label = String(source?.label || source?.approval_no || "未标注资料");
      counts.set(label, Number(counts.get(label) || 0) + 1);
      return counts;
    }, new Map());
    const sources = presentationSources.map(source => {
      const label = source?.label || source?.approval_no || "未标注资料";
      const approval = source?.approval_no && labelCounts.get(String(label)) > 1
        ? ocTranslate("审批 {0}", [source.approval_no]) : "";
      const origins = (Array.isArray(source?.origins) ? source.origins : [])
        .map(origin => originLabels[String(origin || "").toUpperCase()] || "其他识别");
      return [this.escape(ocTranslate(label)), approval ? this.escape(approval) : "", origins.map(origin => this.escape(origin)).join(" / ")]
        .filter(Boolean).join(" · ");
    });
    const fallbackSource = values.source_label || fee.source_label || "";
    const source = sources.length ? sources.map(row => `<span>${row}</span>`).join("<br>") : value(fallbackSource);
    return `<tr><td><input type="checkbox" data-mf-ai-fee-select="${this.escape(fee.proposal_id)}" ${readonly ? "disabled" : ""} ${selection.fees.has(String(fee.proposal_id)) ? "checked" : ""} aria-label="${this.escape(ocTranslate("选择费用 {0}", [values.expense_category || values.logical_fee_key || ""]))}"></td><td>${value(values.expense_category || values.logical_fee_key)}</td><td>${value(values.amount)} ${value(values.currency)}</td><td>${source}</td></tr>`;
  }

  renderMaterialAIFeeStages(fill, selection, feePolicy, value) {
    const catalog = fill.row_review || {};
    const snapshots = new Map((catalog.fee_stage_snapshots || []).map(stage => [String(stage.stage || ""), stage]));
    const specs = [["payment", 0, "支付申请"], ["international_logistics", 1, "国际物流"], ["purchase", 2, "采购支出"]];
    const feesById = new Map(feePolicy.fees.map(fee => [String(fee.proposal_id), fee]));
    const mainIds = new Set(feePolicy.mainFees.map(fee => String(fee.proposal_id)));
    const stages = specs.map(([stage, rank, label]) => ({ status: "UNAVAILABLE", processes: [], fees: [], warnings: [], ...(snapshots.get(stage) || {}), stage, stage_rank: rank, stage_label: label }));
    const stageViews = stages.map(stage => {
      const feeIds = new Set((stage.fees || []).map(fee => String(fee?.proposal_id || fee || "")).filter(Boolean));
      const stageFees = [...feeIds].map(id => feesById.get(id)).filter(Boolean);
      const mainFees = stageFees.filter(fee => mainIds.has(String(fee.proposal_id)));
      return { stage, mainFees };
    });
    const actionable = view => view.mainFees.some(fee => fee.can_apply);
    const openIndex = this.materialAIFirstActionableStageIndex(stageViews, actionable);
    const renderMain = fee => this.renderMaterialAIFeeChoiceRow(fee, selection, value, fill.applying);
    const panels = stageViews.map((view, index) => {
      const { stage, mainFees } = view;
      const status = String(stage.status || "UNAVAILABLE").toUpperCase();
      const selectedCount = mainFees.filter(fee => selection.fees.has(String(fee.proposal_id))).length;
      const hasActionable = actionable(view);
      const empty = `<tr><td colspan="4">${ocTranslate("无可用资料")}</td></tr>`;
      return `<details class="ocw-mf-ai-stage-panel is-${this.escape(status.toLowerCase())}" data-mf-ai-fee-stage="${this.escape(stage.stage)}" ${index === openIndex ? "open" : ""}><summary><strong>${this.escape(stage.stage_label)}</strong><span><b>${hasActionable ? this.escape(this.materialAIStageStatusLabel(status)) : ocTranslate("无可用资料")}</b>${ocTranslate("已选 {0} 项", [selectedCount])}</span></summary>${this.materialAIStageSourceActions(stage)}${this.renderMaterialAIStageWarning(stage)}<div class="ocw-mf-ai-preview-table"><table class="ocw-mf-ai-fee-catalog"><thead><tr><th>${ocTranslate("选择")}</th><th>${ocTranslate("费用项目")}</th><th>${ocTranslate("金额")}</th><th>${ocTranslate("来源")}</th></tr></thead><tbody>${mainFees.map(renderMain).join("") || empty}</tbody></table></div></details>`;
    }).join("");
    const assignedIds = new Set(stages.flatMap(stage => (stage.fees || []).map(fee => String(fee?.proposal_id || fee || "")).filter(Boolean)));
    const unclassified = feePolicy.mainFees.filter(fee => !assignedIds.has(String(fee.proposal_id)));
    const unclassifiedSection = unclassified.length ? `<details class="ocw-mf-ai-unclassified" data-mf-ai-unclassified-fees="1"><summary>${ocTranslate("其他可选费用")} <span>${ocTranslate("{0} 条", [unclassified.length])}</span></summary><div class="ocw-mf-ai-preview-table"><table class="ocw-mf-ai-fee-catalog"><thead><tr><th>${ocTranslate("选择")}</th><th>${ocTranslate("费用项目")}</th><th>${ocTranslate("金额")}</th><th>${ocTranslate("来源")}</th></tr></thead><tbody>${unclassified.map(renderMain).join("")}</tbody></table></div></details>` : "";
    return `<section class="ocw-mf-ai-preview-section" data-mf-ai-fee-stages><h4>${ocTranslate("费用")} <span>${ocTranslate("已选 {0}", [selection.fees.size])}</span></h4>${panels}${unclassifiedSection}</section>`;
  }

  renderMaterialAIRowReview(fill) {
    if (!this.materialAIReviewHasStageSnapshots(fill)) return this.renderMaterialAIReanalysisRequired();
    const selection = this.ensureMaterialAIRowSelection(fill);
    const catalog = fill.row_review;
    const preview = selection.previewKey === this.materialAIRowSelectionKey(fill) ? selection.preview : null;
    const value = input => this.escape(input === null || input === undefined || input === "" ? "—" : input);
    const columns = [["material_code", ocTranslate("物料编码")], ["product_name", ocTranslate("物料名称")], ["quantity", ocTranslate("采购数量")], ["actual_shipped_qty", ocTranslate("实发数量")], ["shipped_uom", ocTranslate("单位")], ["unit_price", ocTranslate("采购单价")], ["purchase_currency", ocTranslate("币种")], ["shipment_value_rmb", ocTranslate("本次发货货值 RMB")], ["package_count", ocTranslate("箱数")], ["net_weight_kg", ocTranslate("净重 kg")], ["gross_weight_kg", ocTranslate("毛重 kg")], ["volume_m3", ocTranslate("体积 m³")], ["supplier", ocTranslate("供应商")], ["project_collection", ocTranslate("项目归属")]];
    const displayCell = (row, field) => {
      if (field === "unit_price" && row.adopted_price?.value != null) {
        const price = Number(row.adopted_price.value);
        return `${Number.isFinite(price) ? value(price.toFixed(2)) : "—"}<small>${ocTranslate("按同来源货值÷数量计算")}</small>`;
      }
      if (field === "purchase_currency" && row.adopted_price?.currency) return value(row.adopted_price.currency);
      if (field === "unit_price") return Number(row[field]) > 0 ? value(Number(row[field]).toFixed(2)) : "—";
      if (field === "shipment_value_rmb" && row[field] != null && row[field] !== "") return value(Number(row[field]).toFixed(2));
      return value(row[field]);
    };
    const cells = row => columns.map(([field]) => `<td>${displayCell(row, field)}</td>`).join("");
    const busy = fill.applying || fill.discarding ? "disabled" : "";
    const missing = preview?.missing_fields || [];
    const missingCount = Array.isArray(missing) ? missing.length : Number(missing.count ?? missing) || Object.keys(missing).length;
    const notices = [...(preview?.unresolved || []), ...(Array.isArray(missing) ? missing : [])];
    const mergedAmountGroups = preview?.merged_amount_groups || fill.draft?.merged_amount_groups || [];
    const mergedAmountSummary = mergedAmountGroups.length ? `<section class="ocw-mf-ai-preview-section"><h4>${ocTranslate("合并金额校验")}</h4><ul>${mergedAmountGroups.map((group) => `<li>${ocTranslate("{0} · 第 {1}-{2} 行 · 组总额 {3} · 独立行合计 {4} · {5}", [this.escape(group.sheet_name || group.source_id || ocTranslate("装箱单")), this.escape(group.source_range?.start_row ?? group.source_row ?? "--"), this.escape(group.source_range?.end_row ?? group.source_row ?? "--"), this.escape(group.control_total_rmb ?? "--"), this.escape(group.computed_total_rmb ?? "--"), group.status === "verified" ? "已校验" : "待人工分摊"])}</li>`).join("")}</ul></section>` : "";
    const packingGroupCandidates = (fill.draft?.packing_group_candidates || preview?.packing_group_candidates || [])
      .filter(group => group?.can_apply && (group.assignment_options || []).some(option => option?.can_apply));
    const packingGroupSummary = packingGroupCandidates.length ? `<section class="ocw-mf-ai-preview-section"><h4>${ocTranslate("装箱组候选")}</h4><p>${ocTranslate("每条装箱事实只能选择一种归属：仅归属某个物料，或候选物料共同装为 1 箱。已保存的人工分组不会被覆盖。")}</p><ul>${packingGroupCandidates.map((group) => { const candidateId = String(group.candidate_id || ""); const selectedAssignment = selection.packingAssignments.get(candidateId); return `<li><strong>${this.escape(group.source_label || group.sheet_name || group.source_id || ocTranslate("装箱单"))}</strong> ${ocTranslate("· {0} 行 · 箱数 {1} · 净重 {2} kg · 毛重 {3} kg · 体积 {4} m³{5}", [Number(group.member_keys?.length || 0), this.escape(group.package_count ?? "--"), this.escape(group.net_weight_kg ?? "--"), this.escape(group.gross_weight_kg ?? "--"), this.escape(group.volume_m3 ?? "--"), group.weight_basis === "inferred_unqualified_weight_as_gross" ? " · 未注明口径，按组毛重候选" : ""])}<div class="ocw-mf-ai-packing-assignments">${(group.assignment_options || []).map((option) => `<label><input type="radio" name="packing-assignment-${this.escape(candidateId)}" data-mf-ai-packing-assignment="${this.escape(candidateId)}" value="${this.escape(option.assignment_id)}" ${selectedAssignment === String(option.assignment_id) ? "checked" : ""} ${!option.can_apply || busy ? "disabled" : ""}><span>${this.escape(option.label || ocTranslate("装箱归属"))}</span></label>`).join("") || `<small>${this.escape(group.resolution_reason || ocTranslate("暂无可用归属选项"))}</small>`}</div><small>${this.escape(group.resolution_reason || "")}</small></li>`; }).join("")}</ul></section>` : "";
    const sharedPackingFields = new Set(["package_count", "net_weight_kg", "gross_weight_kg", "volume_m3"]);
    const sharedPackingByMember = new Map();
    packingGroupCandidates.forEach(group => {
      const candidateId = String(group.candidate_id || "");
      const selectedAssignment = String(selection.packingAssignments.get(candidateId) || "");
      const option = (group.assignment_options || []).find(row => String(row.assignment_id || "") === selectedAssignment);
      if (!option?.can_apply || option.mode !== "one_box_group" || (option.member_keys || []).length < 2) return;
      option.member_keys.forEach((memberKey, position) => sharedPackingByMember.set(String(memberKey), {
        group, option, position, size: option.member_keys.length,
      }));
    });
    const finalCells = row => {
      const rowKey = String(row.stable_line_key || (row.name ? `legacy:${row.name}` : ""));
      const shared = sharedPackingByMember.get(rowKey);
      return columns.map(([field]) => {
        if (!shared || !sharedPackingFields.has(field)) return `<td>${displayCell(row, field)}</td>`;
        if (shared.position > 0) return "";
        const total = field === "package_count"
          ? (shared.option.package_count_override ?? shared.group[field])
          : shared.group[field];
        return `<td class="is-packing-group" rowspan="${Number(shared.size)}">${value(total)}<small>${ocTranslate("共享 1 箱总计 · {0} 个物料", [Number(shared.size)])}</small></td>`;
      }).join("");
    };
    const fieldLabels = Object.fromEntries(columns);
    const fieldColumnOrder = [...columns, ["goods_value", "采购货值 RMB"], ["purchase_uom", "采购单位"], ["unit_price_uom", "单价单位"], ["volume_weight_kg", "体积重 kg"], ["chargeable_weight_kg", "计费重 kg"], ["weight_ratio", "重量占比"], ["packaging_type", "包装类型"]];
    fieldColumnOrder.forEach(([fieldname, label]) => { fieldLabels[fieldname] = label; });
    const currentItems = catalog.rows || [];
    const itemLabel = itemName => {
      const row = currentItems.find(candidate => candidate.origin === "current" && String(candidate.target_item_name || candidate.values?.name || "") === String(itemName || ""));
      const code = row?.values?.material_code;
      const name = row?.values?.product_name;
      return [code, name && String(name) !== String(code) ? name : ""].filter(Boolean).join(" · ") || itemName;
    };
    const fieldChoiceSection = this.renderMaterialAIPackingStages(fill, selection, fieldColumnOrder, fieldLabels, value, busy, itemLabel);
    const addCandidateRows = (catalog.rows || []).filter(row => row.can_add).map(row => {
      const reason = row.blocked_reason || row.default_selection_reason || "";
      return `<tr><td><input type="checkbox" data-mf-ai-row-select="${this.escape(row.row_id)}" ${fill.applying ? "disabled" : ""} ${selection.rows.has(String(row.row_id)) ? "checked" : ""} aria-label="${this.escape(ocTranslate("选择 {0}", [row.values?.product_name || row.values?.material_code || row.row_id]))}"></td>${cells(row.values || {})}<td>${value(reason)}</td></tr>`;
    }).join("");
    const addCandidateTable = `<div class="ocw-mf-ai-preview-table"><table class="ocw-mf-ai-row-catalog"><thead><tr><th>${ocTranslate("选择")}</th>${columns.map(([, label]) => `<th>${ocTranslate(label)}</th>`).join("")}<th>${ocTranslate("核对提示")}</th></tr></thead><tbody>${addCandidateRows || `<tr><td colspan="${columns.length + 2}">${ocTranslate("没有可新增的物料")}</td></tr>`}</tbody></table></div>`;
    const hasAddCandidates = (catalog.rows || []).some(row => row.can_add);
    const feePolicy = this.materialAIReviewFeePolicy(fill);
    const feeSection = this.renderMaterialAIFeeStages(fill, selection, feePolicy, value);
    const modeSummary = selection.mode === "update_selected" ? "将更新已选字段，不清空其他内容" : selection.mode === "add_selected" ? "仅新增已选物料，不同时更新费用" : "只补缺失字段，保留已有内容";
    const modeHelp = selection.mode === "update_selected" ? "更新已选字段，未选字段与其他行保留。" : selection.mode === "add_selected" ? "仅新增明确勾选的未匹配物料；本次不同时更新现有行或费用。" : "只补真正缺失的字段；已填金额、数量和 0 值保留。";
    const scopeNotice = `<p class="ocw-mf-ai-scope-summary" data-mf-ai-material-scope>${ocTranslate("定行依据：国际物流/支付申请/采购支出")}</p>`;
    return `<div class="ocw-mf-ai-review-dialog" data-mf-ai-review-host="1"><header><div><strong>${ocTranslate("填充预览")}</strong><span>${modeSummary}</span></div></header>
      <main class="ocw-mf-ai-dialog-body"><details class="ocw-mf-ai-row-controls"><summary>${ocTranslate("更多设置")}</summary><div><label>${ocTranslate("填充方式")} <select data-mf-ai-row-mode ${busy}><option value="update_selected" ${selection.mode === "update_selected" ? "selected" : ""}>${ocTranslate("更新所选行（默认）")}</option><option value="fill_missing" ${selection.mode === "fill_missing" ? "selected" : ""}>${ocTranslate("只补缺失")}</option>${hasAddCandidates ? `<option value="add_selected" ${selection.mode === "add_selected" ? "selected" : ""}>${ocTranslate("单独确认新增")}</option>` : ""}</select></label><p>${modeHelp}</p></div></details>
      ${scopeNotice}${fieldChoiceSection}${selection.mode === "add_selected" ? `<section class="ocw-mf-ai-preview-section"><h4>${ocTranslate("待新增物料")} <span>${ocTranslate("已选 {0} / {1}", [selection.rows.size, addCandidateRows ? (catalog.rows || []).filter(row => row.can_add).length : 0])}</span></h4><div class="ocw-mf-ai-row-toolbar"><button type="button" class="ocw-outline-btn" data-action="mf-ai-row-all" ${busy}>${ocTranslate("全选可用行")}</button><button type="button" class="ocw-outline-btn" data-action="mf-ai-row-none" ${busy}>${ocTranslate("全不选")}</button></div>${addCandidateTable}</section>` : ""}
      ${feeSection}
      ${mergedAmountSummary}${packingGroupSummary}<section class="ocw-mf-ai-preview-section" data-mf-ai-final-preview><h4>${ocTranslate("确认后物料清单")}</h4>${selection.loading ? `<p role="status">${ocTranslate("正在更新服务器预览…")}</p>` : preview ? `<p>${ocTranslate("最终 {0} 行 · 新增 {1} · 移除 {2} · 补充 {3} · 缺项 {4}", [preview.rows?.length || 0, Number(preview.added_count || 0), Number(preview.removed_count || 0), Number(preview.updated_count || 0), missingCount])}</p><div class="ocw-mf-ai-preview-table"><table class="ocw-mf-ai-final-table"><thead><tr>${columns.map(([, label]) => `<th>${ocTranslate(label)}</th>`).join("")}</tr></thead><tbody>${(preview.rows || []).map(row => `<tr>${finalCells(row)}</tr>`).join("")}</tbody></table></div>` : `<p>${ocTranslate("等待服务器预览。")}</p>`}${notices.length ? `<ul>${notices.map(row => `<li>${this.escape(typeof row === "string" ? row : row.message || row.reason || row.fieldname || ocTranslate("待核对"))}</li>`).join("")}</ul>` : ""}</section>
      ${selection.error ? `<p class="ocw-mf-ai-review-error" role="alert">${this.escape(selection.error)}</p>` : ""}
      <details class="ocw-mf-ai-review-advanced" data-mf-ai-review-evidence><summary>${ocTranslate("查看依据与其他记录")}</summary>${this.renderMaterialAIReviewSources(fill)}<button class="ocw-outline-btn" type="button" data-action="mf-ai-change-sources" ${busy}>${ocTranslate("更换来源并重新生成")}</button></details></main>
      <footer class="ocw-mf-ai-dialog-footer"><span>${ocTranslate("确认前不会修改已保存数据")}</span><div><button class="ocw-outline-btn" type="button" data-action="mf-ai-review-cancel" ${busy}>${ocTranslate("取消")}</button><button class="ocw-outline-btn" type="button" data-action="mf-ai-discard" ${busy}>${ocTranslate("放弃草稿")}</button><button class="ocw-outline-btn" type="button" data-action="mf-ai-row-preview" ${selection.loading || fill.applying ? "disabled" : ""}>${ocTranslate("重新读取资料源")}</button><button class="ocw-primary-btn" type="button" data-action="mf-ai-apply" ${this.canConfirmMaterialAIRowSelection(fill) ? "" : "disabled"}>${fill.applying ? ocTranslate("正在填充…") : selection.mode === "add_selected" ? ocTranslate("确认新增") : ocTranslate("确认填充")}</button></div></footer></div>`;
  }

  async confirmMaterialAIRowSelection() {
    const state = this.ensureMaterialFeeState();
    const fill = state.aiFill;
    if (!fill?.row_review || !this.canConfirmMaterialAIRowSelection(fill)) return;
    const selection = fill.rowSelection;
    const key = this.materialAIRowSelectionKey(fill);
    const current = () => this.materialFeeState === state && state.aiFill === fill && key === this.materialAIRowSelectionKey(fill) && this.detailState.tab === "documents";
    fill.applying = true; selection.error = ""; this.updateMaterialFeeWriteControls(state); this.renderMaterialAIReviewDialog();
    try {
      if (!(await this.ensureEditSession()) || !current()) return;
      const result = await this.call("overseas_costing.api.materials.confirm_source_ai_selection", {
        batch_name: state.batchName, run_id: fill.runId || fill.run_id,
        preview_id: selection.preview.id, preview_revision: selection.preview.revision,
        edit_token: this.detailState.editToken, expected_modified: this.detailState.expectedModified,
      }, false);
      if (!current()) return;
      if (!result?.ok) throw new Error(result?.message || "填充未保存，请核对后重试。");
      this.updateMaterialFeeExpectedModified(result);
      if (result.version_name) this.detailState.versionName = result.version_name;
      state.requestId += 1; state.feeRequestId += 1;
      state.aiFill = null; state.aiPendingReady = null;
      state.aiProgressDialog?.hide(); state.aiProgressDialog = null;
      frappe.show_alert({ message: result.message || ocTranslate("所选资料已填充，请重新试算"), indicator: "green" });
      await this.loadMaterialFeeWorkspace({ quiet: true });
    } catch (error) {
      if (current()) selection.error = this.materialAIErrorMessage(error, "填充未保存，请重试。");
    } finally { fill.applying = false; this.updateMaterialFeeWriteControls(state); if (current()) this.renderMaterialAIReviewDialog(); }
  }

  materialAIAutofillPreview(fill) {
    const state = this.ensureMaterialFeeState();
    const preview = fill?.draft?.autofill_preview;
    const proposals = fill?.proposals || [];
    const selected = (proposal) => fill.selections?.has(String(proposal.proposal_id || ""));
    let items = (preview?.items || state.materials?.items || []).map((row) => {
      let metadata = row.extra_json || {};
      if (typeof metadata === "string") {
        try { metadata = JSON.parse(metadata); } catch (_error) { metadata = {}; }
      }
      return { ...row, package_count: row.package_count ?? metadata?.logistics_row?.packing?.package_count };
    });
    let fees = (preview?.fees || []).map((row) => ({ ...row }));
    // The server preview contains default choices. Restore fields before applying changed choices.
    proposals.filter((proposal) => proposal.proposal_type === "item_update" && proposal.default_selected && !selected(proposal)).forEach((proposal) => {
      const row = items.find((item) => String(item.name || "") === String(proposal.target_item_name || ""));
      const original = (state.materials?.items || []).find((item) => String(item.name || "") === String(proposal.target_item_name || ""));
      if (!row) return;
      Object.keys(proposal.payload?.fields || {}).forEach((field) => {
        const change = (preview?.changes || []).find((change) => String(change.item_name || change.name || "") === String(row.name || "") && change.fieldname === field);
        if (change && Object.prototype.hasOwnProperty.call(change, "previous_value")) row[field] = change.previous_value;
        else if (original) row[field] = original[field];
      });
    });
    proposals.filter(selected).forEach((proposal) => {
      const edit = fill.edits?.[String(proposal.proposal_id || "")];
      if (proposal.proposal_type === "item_update") {
        const row = items.find((item) => String(item.name || "") === String(proposal.target_item_name || ""));
        if (row) Object.assign(row, edit || proposal.payload?.fields || {});
      } else if (proposal.proposal_type === "material_replace") {
        const replacementRows = edit?.replacement_rows || proposal.payload?.replacement_rows || [];
        items = items.flatMap((item) => String(item.name || "") === String(proposal.target_item_name || "") && replacementRows.length ? replacementRows.map((row) => ({ ...row })) : [item]);
      } else if (proposal.proposal_type === "fee_update") {
        const row = fees.find((fee) => String(fee.proposal_id || "") === String(proposal.proposal_id || ""));
        const values = { ...proposal.payload, ...edit, proposal_id: proposal.proposal_id };
        if (Object.prototype.hasOwnProperty.call(values, "expense_category")) values.fee_type = values.expense_category;
        if (row) Object.assign(row, values);
        else fees.push(values);
      }
    });
    fees = fees.filter((fee) => {
      const proposal = proposals.find((proposal) => String(proposal.proposal_id || "") === String(fee.proposal_id || ""));
      return !proposal || selected(proposal);
    });
    Object.values(fill.manualUpdates || {}).forEach((update) => {
      const row = items.find((item) => String(item.name || "") === String(update.item_name || ""));
      if (row) row[update.fieldname] = update.value;
    });
    const summaryStale = Object.keys(fill.manualUpdates || {}).length > 0 || Object.keys(fill.edits || {}).length > 0
      || proposals.some(proposal => Boolean(proposal.default_selected) !== Boolean(selected(proposal)));
    return { items, fees, notes: preview?.notes || [], project_summary: summaryStale ? [] : preview?.project_summary || [], summaryStale, unresolved: preview?.unresolved || [] };
  }

  renderMaterialAIAutofillPreview(fill) {
    const preview = this.materialAIAutofillPreview(fill);
    const columns = [["row_no", ocTranslate("行")], ["material_code", ocTranslate("物料编码")], ["product_name", ocTranslate("物料名称")], ["quantity", ocTranslate("采购数量")], ["actual_shipped_qty", ocTranslate("实发数量")], ["shipped_uom", ocTranslate("发运单位")], ["unit_price", ocTranslate("采购单价")], ["purchase_currency", ocTranslate("币种")], ["shipment_value_rmb", ocTranslate("本次发货货值 RMB")], ["package_count", ocTranslate("箱数")], ["net_weight_kg", ocTranslate("净重 kg")], ["gross_weight_kg", ocTranslate("毛重 kg")], ["volume_m3", ocTranslate("体积 m³")], ["project_collection", ocTranslate("项目归属")]];
    const value = (input) => this.escape(input === null || input === undefined || input === "" ? "—" : input);
    const money = (input) => {
      if (input === null || input === undefined || String(input).trim() === "") return "—";
      return Number.isFinite(Number(input)) ? Number(input).toFixed(2) : value(input);
    };
    if (preview.summaryStale) preview.unresolved = [...preview.unresolved, {message: ocTranslate("草稿选择或数据已修改，项目汇总已隐藏；确认填充后请重新试算。")}];
    const notes = preview.notes.length ? `<details class="ocw-mf-ai-review-notes"><summary>${ocTranslate("资料说明（已保留人工值与采购关联状态）")}</summary><ul>${preview.notes.map(note => `<li>${this.escape(note.message || "")}</li>`).join("")}</ul></details>` : "";
    return `${notes}${this.renderShipmentProjectSummary(preview.project_summary)}<section class="ocw-mf-ai-preview-section"><h4>${ocTranslate("物料明细")} <span>${ocTranslate("{0} 条", [preview.items.length])}</span></h4><div class="ocw-mf-ai-preview-table"><table><thead><tr>${columns.map(([, label]) => `<th>${ocTranslate(label)}</th>`).join("")}</tr></thead><tbody>${preview.items.map((row, index) => `<tr data-mf-ai-preview-item="${this.escape(row.name || row.stable_line_key || "")}">${columns.map(([field]) => `<td>${value(field === "row_no" ? row.row_no ?? index + 1 : row[field])}</td>`).join("")}</tr>`).join("") || `<tr><td colspan="${columns.length}">${ocTranslate("暂无可填充的物料明细。")}</td></tr>`}</tbody></table></div></section><section class="ocw-mf-ai-preview-section"><h4>${ocTranslate("费用")}</h4>${preview.fees.length ? `<div class="ocw-mf-ai-preview-table"><table><thead><tr><th>${ocTranslate("费用项目")}</th><th>${ocTranslate("填充金额")}</th><th>${ocTranslate("币种")}</th><th>${ocTranslate("原金额")}</th><th>${ocTranslate("金额变更")}</th><th>${ocTranslate("承运人 / 说明")}</th></tr></thead><tbody>${preview.fees.map((fee) => `<tr><td>${value(fee.fee_type || fee.expense_category || fee.logical_fee_key)}</td><td>${money(fee.amount)}</td><td>${value(fee.currency)}</td><td>${money(fee.previous_amount)}</td><td>${money(fee.previous_amount)} → ${money(fee.amount)} ${value(fee.currency)}</td><td>${value([fee.carrier, fee.remark].filter(Boolean).join(" · "))}</td></tr>`).join("")}</tbody></table></div>` : `<p class="ocw-mf-ai-preview-empty">${ocTranslate("本次没有费用变更。")}</p>`}</section>${preview.unresolved.length ? `<section class="ocw-mf-ai-preview-unresolved"><h4>${ocTranslate("仍需补充 / 核对")}</h4><ul>${preview.unresolved.map((row) => `<li>${this.escape(typeof row === "string" ? row : row.message || row.reason || ocTranslate("待核对"))}</li>`).join("")}</ul></section>` : ""}`;
  }

  renderShipmentProjectSummary(projects = []) {
    if (!projects.length) return "";
    const fields = ["project_collection", "goods_value_rmb", "gross_weight_kg", "allocated_fees_rmb", "total_cost_rmb"];
    return `<section class="ocw-mf-ai-preview-section"><h4>${ocTranslate("项目汇总")}</h4><div class="ocw-mf-ai-preview-table"><table><thead><tr><th>${ocTranslate("项目归属")}</th><th>${ocTranslate("本次发货货值 RMB")}</th><th>${ocTranslate("毛重 kg")}</th><th>${ocTranslate("分摊费用 RMB")}</th><th>${ocTranslate("综合成本 RMB")}</th></tr></thead><tbody>${projects.map(row => `<tr>${fields.map(field => `<td>${this.escape(row[field] ?? "—")}</td>`).join("")}</tr>`).join("")}</tbody></table></div></section>`;
  }

  materialAIPhysicalSummary(fill) {
    const rows = this.materialAIAutofillPreview(fill).items;
    const compact = (value) => {
      const rounded = Math.round(Number(value || 0) * 1000000) / 1000000;
      return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(6);
    };
    return {
      itemCount: rows.length,
      netWeight: compact(rows.reduce((sum, row) => sum + (Number(row.net_weight_kg) || 0), 0)),
      grossWeight: compact(rows.reduce((sum, row) => sum + (Number(row.gross_weight_kg) || 0), 0)),
    };
  }

  renderMaterialAIReviewDialog() {
    const state = this.ensureMaterialFeeState();
    const dialog = state.aiProgressDialog;
    if (!dialog?.$wrapper?.length || !this.isMaterialAIReadyStatus(state.aiFill?.status)) return;
    const scrollTop = dialog.$wrapper.find(".ocw-mf-ai-dialog-body").scrollTop?.() || 0;
    const tableScroll = [];
    dialog.$wrapper.find(".ocw-mf-ai-preview-table").each?.((index, element) => { tableScroll[index] = $(element).scrollLeft(); });
    dialog.$wrapper.addClass("is-review");
    dialog.fields_dict.progress_html.$wrapper.html(this.renderMaterialAIReviewDialogContent());
    dialog.$wrapper.find(".ocw-mf-ai-dialog-body").scrollTop?.(scrollTop);
    dialog.$wrapper.find(".ocw-mf-ai-preview-table").each?.((index, element) => { $(element).scrollLeft(tableScroll[index] || 0); });
  }

  updateMaterialAIReviewSelectionSurface() {
    const state = this.ensureMaterialFeeState();
    const fill = state.aiFill;
    const $wrapper = state.aiProgressDialog?.$wrapper;
    if (!$wrapper?.length || !fill) return;
    $wrapper.find("[data-mf-ai-proposal-select]").each((_index, node) => {
      const proposalId = String($(node).attr("data-proposal-id") || "");
      $(node).prop("checked", fill.selections.has(proposalId));
    });
    $wrapper.find("[data-mf-ai-autofill-preview]").html(this.renderMaterialAIAutofillPreview(fill));
    const physical = this.materialAIPhysicalSummary(fill);
    $wrapper.find(".ocw-mf-ai-physical-summary").html(`<b>${ocTranslate("明细 {0} 条", [physical.itemCount])}</b><b>${ocTranslate("净重 {0} kg", [this.escape(physical.netWeight)])}</b><b>${ocTranslate("毛重 {0} kg", [this.escape(physical.grossWeight)])}</b>`);
    $wrapper.find("[data-action='mf-ai-apply']")
      .prop("disabled", !this.canApplyMaterialAIFill(fill))
      .text(fill.applying ? ocTranslate("正在填充…") : ocTranslate("确认填充"));
  }

  async restartMaterialAIWithSources(dialog) {
    const state = this.ensureMaterialFeeState();
    const sources = state.aiFill?.source_progress || [];
    const selectedSourceIds = this.materialAISelectedSourceIds(sources);
    state.aiPendingReady = null;
    dialog.$wrapper.removeClass("is-review");
    await this.startMaterialAIFill({ force: true, restart: true, selectedSourceIds });
  }

  restartMaterialAIFromCurrentSources() {
    const state = this.ensureMaterialFeeState();
    state.aiPendingReady = null;
    if (state.aiProgressDialog?.$wrapper?.length) state.aiProgressDialog.$wrapper.removeClass("is-review");
    return this.startMaterialAIFill({ force: true, restart: true });
  }

  async previewMaterialRowRecovery() {
    const batchName = this.detailState.batchName;
    const versionName = this.detailState.versionName;
    const preview = await this.call("overseas_costing.api.materials.preview_material_row_recovery", {
      batch_name: batchName, version_name: versionName,
    }, false);
    if (!preview?.ok || !preview.can_confirm) throw new Error(preview?.message || (preview?.issues || []).join("；") || "当前单据没有可安全恢复的物料行。");
    const renderRows = rows => (rows || []).map(row => `<tr><td>${row.action === "restore" ? ocTranslate("恢复") : row.action === "keep_updated" ? ocTranslate("保留已更新") : ocTranslate("当前")}</td><td>${this.escape(row.material_code || "—")}</td><td>${this.escape(row.product_name || "—")}</td><td>${this.escape(row.actual_shipped_qty ?? row.quantity ?? "—")}</td><td>${this.escape(row.shipped_uom || row.unit || "—")}</td><td>${this.escape(row.gross_weight_kg ?? "—")}</td></tr>`).join("");
    const table = rows => `<div class="ocw-mf-ai-preview-table"><table><thead><tr><th>${ocTranslate("操作")}</th><th>${ocTranslate("物料编码")}</th><th>${ocTranslate("物料名称")}</th><th>${ocTranslate("数量")}</th><th>${ocTranslate("单位")}</th><th>${ocTranslate("毛重 kg")}</th></tr></thead><tbody>${renderRows(rows)}</tbody></table></div>`;
    const message = `<div class="ocw-mf-recovery-preview"><p>${ocTranslate("当前 {0} 行；将恢复 {1} 行，并保留已更新行的新值。", [Number(preview.current_count || 0), Number(preview.restored_count || 0)])}</p><h5>${ocTranslate("恢复前")}</h5>${table(preview.before_rows || [])}<h5>${ocTranslate("恢复后")}</h5>${table(preview.after_rows || preview.rows || [])}<p>${ocTranslate("确认后将创建新的可审计版本，当前版本仍保留。")}</p></div>`;
    return new Promise((resolve, reject) => {
      frappe.confirm(message, () => {
        this.confirmMaterialRowRecovery(preview).then(resolve).catch(reject);
      }, () => resolve(false));
    });
  }

  async confirmMaterialRowRecovery(preview) {
    if (!(await this.ensureEditSession())) return false;
    const result = await this.call("overseas_costing.api.materials.confirm_material_row_recovery", {
      batch_name: this.detailState.batchName,
      version_name: preview.current_version,
      preview_id: preview.id,
      revision: preview.revision,
      edit_token: this.detailState.editToken,
      expected_modified: this.detailState.expectedModified,
    }, false);
    if (!result?.ok) throw new Error(result?.message || "物料恢复失败，请重新预览。");
    this.detailState.versionName = result.version_name || this.detailState.versionName;
    this.acceptBatchWriteRevision(result.batch_modified);
    await this.loadMaterialFeeWorkspace({ preservePosition: true });
    frappe.show_alert({ message: ocTranslate("已恢复 {0} 行物料", [Number(result.restored_count || 0)]), indicator: "green" });
    return result;
  }

  sourceAIReviewProposalLabel(proposal) {
    return { logistics_reconcile: ocTranslate("同步物流审批明细"), material_replace: ocTranslate("拆分临时物料"), item_update: ocTranslate("补充物料字段"), fee_update: ocTranslate("补充费用") }[proposal?.proposal_type] || ocTranslate("资料候选");
  }

  renderSourceAIReviewProposals(forDialog = false) {
    const fill = this.ensureMaterialFeeState().aiFill;
    if (!forDialog || !this.isMaterialAIReadyStatus(fill?.status) || !fill.draftVisible || !Array.isArray(fill.proposals)) return "";
    if (!fill.proposals.length) return `<div class="ocw-mf-ai-proposals is-empty">${ocTranslate("当前资料没有形成可保存候选，请补充说明或资料后重新分析。")}</div>`;
    return `<div class="ocw-mf-ai-proposals">${fill.proposals.map((proposal) => {
      const selected = fill.selections?.has(String(proposal.proposal_id || ""));
      const tone = proposal.conflict || Number(proposal.confidence || 0) < 0.9 ? "review" : "ready";
      const sources = (proposal.source_refs || []).map((ref) => {
        const source = (fill.source_progress || []).find((row) => String(row.label || "") === String(ref.file || "") && (!ref.sheet || String(row.sheet_name || row.sheet || "") === String(ref.sheet || ""))) || {};
        return [
          ref.file,
          ref.approval_no ? ocTranslate("审批 {0}", [ref.approval_no]) : source.approval_no || source.source_context?.instance_id ? ocTranslate("审批 {0}", [source.approval_no || source.source_context.instance_id]) : "",
          ref.sheet,
          ref.field,
          ref.row ? ocTranslate("第 {0} 行", [ref.row]) : "",
          ref.cell,
          ref.actor_name || source.actor_name || "",
          ref.occurred_at || source.occurred_at || "",
        ].filter(Boolean).join(" · ");
      }).join("；");
      const origin = proposal.result_origin === "SYSTEM" ? "系统直读" : "AI 识别";
      return `<article class="is-${tone}">
        <header><label>${proposal.proposal_type === "logistics_reconcile" ? "" : `<input type="checkbox" data-mf-ai-proposal-select="1" data-proposal-id="${this.escape(proposal.proposal_id || "")}" ${selected ? "checked" : ""} />`}<strong>${this.escape(this.sourceAIReviewProposalLabel(proposal))}</strong></label><span>${this.escape(origin)} · ${Math.round(Number(proposal.confidence || 0) * 100)}%${proposal.recommended ? ocTranslate(" · 推荐") : ""}</span></header>
        ${this.renderSourceAIReviewProposalFields(proposal)}
        ${this.renderSourceAIReviewAlternativeQuotes(proposal)}
        <details><summary>${ocTranslate("依据与说明")}</summary><p>${this.escape(proposal.reason || ocTranslate("待人工核对"))}</p><p>${this.escape(sources || ocTranslate("来源位置未标注"))}</p></details>
      </article>`;
    }).join("")}</div>`;
  }

  renderSourceAIReviewProposalFields(proposal) {
    if (proposal.proposal_type === "logistics_reconcile") return `<p>${ocTranslate("确认后按物流审批同步整表明细，预览见上方物料表。")}</p>`;
    if (String(proposal.target_item_name || "").startsWith("draft-")) return `<p>${ocTranslate("此行为待新增的物流明细，填充后可在物料表中补充资料。")}</p>`;
    const proposalId = String(proposal.proposal_id || "");
    const edit = this.ensureMaterialFeeState().aiFill?.edits?.[proposalId];
    if (proposal.proposal_type === "material_replace") {
      const rows = edit?.replacement_rows || proposal.payload?.replacement_rows || [];
      const fields = ["product_name", "spec_model", "quantity", "purchase_uom", "unit_price", "unit_price_uom", "purchase_currency", "goods_value"];
      const labels = { product_name: ocTranslate("物料名称"), spec_model: ocTranslate("规格"), quantity: ocTranslate("数量"), purchase_uom: ocTranslate("单位"), unit_price: ocTranslate("原币单价"), unit_price_uom: ocTranslate("计价单位"), purchase_currency: ocTranslate("币种"), goods_value: ocTranslate("人民币货值") };
      return `<div class="ocw-mf-ai-replacement"><p>${ocTranslate("替换原行：{0}", [this.escape(proposal.target_item_name || "--")])}</p>${rows.map((row, rowIndex) => `<div class="ocw-mf-ai-replacement-row">${fields.map((field) => `<label><span>${labels[field]}</span><input data-mf-ai-edit="1" data-proposal-id="${this.escape(proposalId)}" data-row-index="${rowIndex}" data-fieldname="${field}" value="${this.escape(row?.[field] ?? "")}" /></label>`).join("")}</div>`).join("")}</div>`;
    }
    const values = edit || (proposal.proposal_type === "item_update" ? proposal.payload?.fields : proposal.payload) || {};
    const fields = proposal.proposal_type === "fee_update"
      ? ["expense_category", "amount", "currency", "amount_status", "remark"]
      : Object.keys(values);
    const labels = { expense_category: ocTranslate("费用项目"), amount: ocTranslate("原币金额"), currency: ocTranslate("币种"), amount_status: ocTranslate("状态"), remark: ocTranslate("备注") };
    return `<div class="ocw-mf-ai-fields">${fields.map((field) => `<label><span>${this.escape(labels[field] || field)}</span><input data-mf-ai-edit="1" data-proposal-id="${this.escape(proposalId)}" data-fieldname="${this.escape(field)}" value="${this.escape(values[field] ?? "")}" /></label>`).join("")}</div>`;
  }

  renderSourceAIReviewAlternativeQuotes(proposal) {
    const quotes = proposal.proposal_type === "fee_update" && Array.isArray(proposal.alternatives)
      ? proposal.alternatives.filter((quote) => quote && typeof quote === "object" && !Array.isArray(quote)) : [];
    if (!quotes.length) return "";
    const value = (input) => this.escape(input === null || input === undefined || input === "" ? "—" : input);
    return `<section data-mf-ai-alternative-quotes="1"><h4>${ocTranslate("报价记录（只读）")}</h4><div class="ocw-mf-ai-preview-table"><table><thead><tr><th>${ocTranslate("承运人")}</th><th>${ocTranslate("报价金额")}</th><th>${ocTranslate("币种")}</th><th>${ocTranslate("单价")}</th><th>${ocTranslate("计价依据")}</th><th>${ocTranslate("来源与原文")}</th></tr></thead><tbody>${quotes.map((quote) => {
      const basis = { volume: "体积", weight: "重量" }[quote.pricing_basis] || quote.pricing_basis;
      const location = [quote.source_field, quote.evidence_line_no ? ocTranslate("第 {0} 行", [quote.evidence_line_no]) : ""].filter(Boolean).join(" · ");
      return `<tr><td>${value(quote.carrier)}<small>${String(quote.carrier || "") === String(proposal.carrier || "") ? ocTranslate("审批采用") : ocTranslate("其他报价")}</small></td><td>${value(quote.amount)}</td><td>${value(quote.currency)}</td><td>${value(quote.unit_rate)}</td><td>${value(basis)}</td><td>${value(location)}<br>${value(quote.evidence_line || quote.source_value)}</td></tr>`;
    }).join("")}</tbody></table></div></section>`;
  }

  updateSourceAIReviewEdit($input) {
    const fill = this.ensureMaterialFeeState().aiFill;
    if (!this.isMaterialAIReadyStatus(fill?.status)) return;
    const proposalId = String($input.attr("data-proposal-id") || "");
    const fieldname = String($input.attr("data-fieldname") || "");
    const proposal = (fill.proposals || []).find((row) => String(row.proposal_id) === proposalId);
    if (!proposal || !fieldname) return;
    if (proposal.proposal_type === "logistics_reconcile" || String(proposal.target_item_name || "").startsWith("draft-")) return;
    fill.edits = fill.edits || {};
    if (proposal.proposal_type === "material_replace") {
      if (!fill.edits[proposalId]) fill.edits[proposalId] = { replacement_rows: JSON.parse(JSON.stringify(proposal.payload?.replacement_rows || [])) };
      const rowIndex = Number($input.attr("data-row-index") || 0);
      fill.edits[proposalId].replacement_rows[rowIndex][fieldname] = String($input.val() ?? "");
    } else {
      if (!fill.edits[proposalId]) fill.edits[proposalId] = JSON.parse(JSON.stringify(proposal.proposal_type === "item_update" ? proposal.payload?.fields || {} : proposal.payload || {}));
      fill.edits[proposalId][fieldname] = String($input.val() ?? "");
    }
    if (proposal.conflict_group) {
      (fill.proposals || []).forEach((row) => {
        if (row.conflict_group === proposal.conflict_group) fill.selections.delete(String(row.proposal_id || ""));
      });
    }
    fill.selections.add(proposalId);
    this.updateMaterialAIReviewSelectionSurface();
  }

  renderMaterialAIFillFooter() {
    const fill = this.ensureMaterialFeeState().aiFill;
    if (!this.isMaterialAIReadyStatus(fill?.status) || !fill.draftVisible || !fill.row_review) return "";
    const updateCount = (fill.selections?.size ?? 0) + Object.keys(fill.manualUpdates || {}).length;
    const candidateCount = Number(fill.draft?.proposal_count || fill.draft?.candidate_count || 0);
    const mutating = Boolean(fill.applying || fill.discarding);
    return `<div class="ocw-mf-ai-footer"><span>${ocTranslate("AI 草稿 · 已选择 {0} 项{1}", [updateCount, candidateCount ? ocTranslate(" · 共 {0} 个提案", [candidateCount]) : ""])}</span><div><button class="ocw-outline-btn" type="button" data-action="mf-ai-discard" ${mutating ? "disabled" : ""}>${fill.discarding ? ocTranslate("正在放弃…") : ocTranslate("放弃草稿")}</button><button class="ocw-primary-btn" type="button" data-action="mf-ai-apply" ${this.canApplyMaterialAIFill(fill) ? "" : "disabled"}>${fill.applying ? ocTranslate("正在保存…") : ocTranslate("确认所选草稿")}</button></div></div>`;
  }

  cleanupMaterialGridScrollControls() {
    this.materialGridScrollCleanup?.();
    this.materialGridScrollCleanup = null;
    this.refreshMaterialGridScroll = null;
  }

  bindMaterialGridScrollControls() {
    this.cleanupMaterialGridScrollControls();
    const $viewport = this.$root.find("[data-mf-grid-viewport]");
    const $scrollbar = this.$root.find("[data-mf-grid-scrollbar]");
    if (!$viewport.length || !$scrollbar.length) return;
    const viewport = $viewport.get(0);
    const scrollbar = $scrollbar.get(0);
    if (!viewport || !scrollbar) return;
    const shell = viewport.closest(".ocw-mf-grid-shell");
    const spacer = scrollbar.firstElementChild;
    const leftButton = shell.querySelector("[data-action='mf-grid-scroll-left']");
    const rightButton = shell.querySelector("[data-action='mf-grid-scroll-right']");
    this.materialGridScrollCleanup = this.bindHorizontalScrollController({
      content: viewport,
      scrollbar,
      spacer,
      leftButton,
      rightButton,
      onInteraction: () => this.closeMaterialAICandidatePopover(),
    });
    this.refreshMaterialGridScroll = () => viewport.dispatchEvent(new Event("scroll"));
  }

  initializeMaterialAIDraft(status) {
    const fill = {
      ...status,
      runId: status.run_id,
      updates: {},
      manualUpdates: {},
      draft: status.draft || { rows: {} },
      proposals: status.proposals || status.draft?.proposals || [],
      selections: new Set((status.proposals || status.draft?.proposals || []).filter((proposal) => proposal.default_selected).map((proposal) => String(proposal.proposal_id))),
      edits: {},
      draftVisible: status.draftVisible !== false,
    };
    if (fill.row_review) {
      fill.draftVisible = false;
      if (this.materialAIReviewCanUseSelection(fill)) this.ensureMaterialAIRowSelection(fill);
      return fill;
    }
    fill.proposals.filter((proposal) => proposal.proposal_type === "item_update" && proposal.default_selected).forEach((proposal) => {
      Object.entries(proposal.payload?.fields || {}).forEach(([fieldname, value]) => {
        fill.updates[`${proposal.target_item_name}:${fieldname}`] = {
          item_name: proposal.target_item_name,
          fieldname,
          value,
          proposal_id: String(proposal.proposal_id || ""),
          user_edited: false,
        };
      });
    });
    Object.entries(fill.draft.rows || {}).forEach(([itemName, fields]) => {
      Object.entries(fields || {}).forEach(([fieldname, cell]) => {
        if (cell?.status !== "AI_DRAFT" || !cell.can_auto_adopt) return;
        fill.updates[`${itemName}:${fieldname}`] = {
          item_name: itemName,
          fieldname,
          value: cell.value,
          user_edited: false,
        };
      });
    });
    return fill;
  }

  materialAIErrorPayload(error) {
    let payload = error?.responseJSON || error?.responseText || error?.xhr?.responseJSON || error?.xhr?.responseText || error;
    if (typeof payload === "string") {
      try { payload = JSON.parse(payload); } catch (_error) { return payload; }
    }
    if (payload?.message && typeof payload.message === "object") payload = payload.message;
    return payload;
  }

  materialAIErrorMessage(error, fallback = "AI 分析暂时不可用，请稍后重试。") {
    const payload = this.materialAIErrorPayload(error);
    const status = Number(error?.status ?? error?.xhr?.status);
    if (status === 500) return fallback === "AI 分析暂时不可用，请稍后重试。"
      ? "服务器处理失败，请稍后重试。" : fallback;
    if (!payload?.error?.reason && payload?.ok !== false) {
      if (Number.isFinite(status)) {
        const transportMessage = {
          0: "网络连接已中断，请检查连接后重试。",
          401: "登录已失效，请重新登录后重试。",
          403: "当前账号没有访问权限，请确认账号权限后重试。",
          408: "请求超时，请稍后重试。",
          502: "服务暂时不可用，请稍后重试。",
          503: "服务暂时不可用，请稍后重试。",
          504: "服务响应超时，请稍后重试。",
        }[status];
        if (transportMessage) return transportMessage;
      }
    }
    const readable = payload?.error?.reason || payload?.reason || payload;
    const message = String(this.normalizeErrorMessage?.(readable)
      || this.extractReadableError?.(readable)
      || (typeof readable === "string" ? readable : typeof readable?.message === "string" ? readable.message : "")).trim();
    if (/QueryDeadlockError|changed since last read|\(1020\)/i.test(message)) {
      return "任务状态正在同步，请稍后重试。";
    }
    if (/<\s*!doctype(?:\s+[^<>]*?)?\s*>|<\s*(html|body|title|h1|p)\b[^>]*>[\s\S]*<\s*\/\s*\1\s*>|^\s*<\s*(?:html|body|title|h1|p)\b[^>]*>/i.test(message)) return fallback;
    if (!message || message === "操作失败" || /^\s*[\[{]/.test(message) || /\[object Object\]|Traceback|frappe\.exceptions/i.test(message)) return fallback;
    return message.slice(0, 500);
  }

  materialAIProgressWarning(fill) {
    const payload = this.materialAIErrorPayload(fill.request_error || fill);
    const detail = payload?.error || {};
    const message = this.materialAIErrorMessage(fill.connection_error || fill.ai_warning || fill.error_message || fill.request_error, "");
    return [message,
      detail.stage ? ocTranslate("环节：{0}", [detail.stage]) : "",
      detail.scope ? ocTranslate("范围：{0}", [detail.scope]) : "",
      detail.source_label || detail.source_id || detail.approval_no
        ? ocTranslate("来源：{0}", [[detail.source_label || detail.source_id, detail.approval_no ? ocTranslate("审批 {0}", [detail.approval_no]) : ""].filter(Boolean).join(" · ")]) : "",
      detail.next_action ? ocTranslate("下一步：{0}", [detail.next_action]) : "",
      payload?.trace_id ? ocTranslate("参考编号：{0}", [payload.trace_id]) : "",
    ].filter(Boolean).join("\n");
  }

  materialAIRequestRetryable(error) {
    if (error?.workbenchReleaseHandled || error?.workbenchReleaseBlocked) return false;
    const payload = this.materialAIErrorPayload(error);
    if (payload?.ok === false || payload?.retryable === false || payload?.error?.retryable === false) return false;
    const status = error?.status ?? error?.xhr?.status;
    if (status !== undefined) return [0, 408, 502, 503, 504].includes(Number(status));
    const message = this.materialAIErrorMessage(error, "");
    return /network|fetch failed|failed to fetch|load failed|timeout|timed out|connection (?:failed|reset|lost|refused)|网络|连接中断|连接超时/i.test(message);
  }

  failMaterialAIProgress(error, fallback, { resumePolling = false } = {}) {
    const state = this.ensureMaterialFeeState();
    const retrySources = !resumePolling && !state.aiFill?.runId && Array.isArray(state.aiStartOptions?.sourceProgress)
      ? state.aiStartOptions.sourceProgress
      : [];
    state.aiFill = {
      ...(state.aiFill || {}),
      status: resumePolling ? state.aiFill?.status || "RUNNING" : "FAILED",
      progress_step: resumePolling ? state.aiFill?.progress_step || "状态读取已暂停" : "分析未完成",
      error_message: this.materialAIErrorMessage(error, fallback),
      request_error: this.materialAIErrorPayload(error),
      connection_error: "",
      polling: false,
      polling_paused: resumePolling,
      source_progress: state.aiFill?.source_progress?.length ? state.aiFill.source_progress : retrySources,
    };
    this.openMaterialAIProgressDialog();
    this.updateMaterialAIProgressSurface();
  }

  retryMaterialAIProgress() {
    const state = this.ensureMaterialFeeState();
    const fill = state.aiFill;
    const workerStalled = Boolean(fill?.stalled || fill?.is_stalled);
    if (state.aiPollRetryPromise && !workerStalled) return state.aiPollRetryPromise;
    if (fill?.runId && !workerStalled && !["READY", "READY_WITH_WARNINGS", "FAILED", "STALE", "DISCARDED", "APPLIED"].includes(fill.status)) {
      const generation = state.aiRunGeneration = Number(state.aiRunGeneration || 0) + 1;
      fill.polling_paused = false;
      delete fill.connection_error;
      delete fill.error_message;
      delete fill.request_error;
      this.openMaterialAIProgressDialog();
      const polling = this.pollMaterialAIFill(state, this.detailState.batchName, this.detailState.versionName, fill.runId)
        .catch((error) => {
          if (this.materialFeeState === state && state.aiRunGeneration === generation) {
            this.failMaterialAIProgress(error, "AI 分析状态读取失败，请重试。", { resumePolling: true });
          }
        }).finally(() => {
          if (state.aiPollRetryPromise === polling) state.aiPollRetryPromise = null;
        });
      state.aiPollRetryPromise = polling;
      return polling;
    }
    state.aiStartPromise = null;
    state.aiPollRetryPromise = null;
    const options = { ...(state.aiStartOptions || {}) };
    const sourceScopeStale = fill?.status === "STALE"
      || /不属于当前批次|资料来源.*已失效|资料来源选择已变化/.test(
        this.materialAIProgressWarning(fill || {}));
    // A failed start may have reached the server. Keep its key until a run is known.
    if (fill?.runId || sourceScopeStale) {
      options.force = true;
      options.restart = true;
      delete options.request_id;
      delete options.requestPayload;
      if (sourceScopeStale) {
        // A policy/source refresh must rebuild both scopes from current server
        // evidence. Replaying IDs from the obsolete draft can only fail again.
        delete options.selectedSourceIds;
        delete options.paymentPreflightComplete;
        delete options.paymentCandidateRefs;
      } else if (fill?.source_progress?.length) {
        options.selectedSourceIds = this.materialAISelectedSourceIds(fill.source_progress);
      }
    }
    return this.startMaterialAIFill(options);
  }

  materialAINewRequestId() {
    if (globalThis.crypto.randomUUID) return globalThis.crypto.randomUUID();
    return Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  continueMaterialAIPaymentSelection(skip = false) {
    const state = this.ensureMaterialFeeState();
    const fill = state.aiFill || {};
    if (fill.status !== "PAYMENT_SELECTION") return Promise.resolve();
    const selected = skip ? new Set() : (fill.paymentSelection instanceof Set ? fill.paymentSelection : new Set());
    if (!skip && !selected.size) return Promise.reject(new Error(ocTranslate("请选择支付流程，或跳过支付申请。")));
    const refs = (fill.paymentPreflight?.candidates || [])
      .filter((candidate) => selected.has(String(candidate.candidate_id || "")))
      .map((candidate) => ({
        candidate_id: String(candidate.candidate_id || ""),
        revision: String(candidate.revision || ""),
        version: String(fill.paymentPreflight?.version || this.detailState.versionName || ""),
      }));
    state.aiStartPromise = null;
    state.aiFill = null;
    return this.startMaterialAIFill({
      ...(state.aiStartOptions || {}), restart: true,
      request_id: this.materialAINewRequestId(), requestPayload: null,
      paymentPreflightComplete: true, paymentCandidateRefs: refs,
    });
  }

  startMaterialAIFill(options = {}) {
    const state = this.ensureMaterialFeeState();
    if (state.aiStartPromise) {
      this.openMaterialAIProgressDialog();
      return state.aiStartPromise;
    }
    const currentStatus = String(state.aiFill?.status || "");
    if (!options.restart && ["STARTING", "PAYMENT_SELECTION", "QUEUED", "RUNNING", "READY", "READY_WITH_WARNINGS"].includes(currentStatus)) {
      this.openMaterialAIProgressDialog();
      return Promise.resolve();
    }
    const sourceProgress = Array.isArray(state.aiFill?.source_progress)
      ? state.aiFill.source_progress.map((source) => ({ ...source }))
      : [];
    state.aiStartOptions = { ...options, sourceProgress, request_id: options.request_id || this.materialAINewRequestId(), ...(Array.isArray(options.selectedSourceIds) ? { selectedSourceIds: [...options.selectedSourceIds] } : {}) };
    state.aiFill = { status: "STARTING", progress_step: "正在启动分析任务", progress_percent: 0, progress_revision: 0, source_progress: [] };
    state.aiPendingReady = null;
    state.aiProgressMinimized = false;
    const generation = state.aiRunGeneration = Number(state.aiRunGeneration || 0) + 1;
    this.openMaterialAIProgressDialog();
    const startPromise = this.runMaterialAIFillStart(state, state.aiStartOptions)
      .catch((error) => {
        if (this.materialFeeState === state && state.aiRunGeneration === generation) this.failMaterialAIProgress(error, "AI 分析任务启动失败，请稍后重试。");
      })
      .finally(() => {
        if (state.aiStartPromise === startPromise) state.aiStartPromise = null;
      });
    state.aiStartPromise = startPromise;
    return startPromise;
  }

  restartMaterialAIForChangedSources() {
    const state = this.ensureMaterialFeeState();
    if (state.aiProgressDialog) {
      state.aiProgressDialog.hide();
      state.aiProgressDialog = null;
    }
    state.aiFill = null;
    state.aiPendingReady = null;
    return this.startMaterialAIFill({ force: false });
  }

  async runMaterialAIFillStart(state, options = {}) {
    const generation = state.aiRunGeneration;
    const batchName = this.detailState.batchName;
    const versionName = this.detailState.versionName;
    const isCurrent = () => this.materialFeeState === state && state.aiRunGeneration === generation
      && this.detailState.batchName === batchName && this.detailState.versionName === versionName
      && this.detailState.tab === "documents";
    if (state.calculationWrite) await state.calculationWrite;
    while (state.pendingWrites.size) await Promise.all([...state.pendingWrites]);
    if (!isCurrent()) return;
    if (Object.keys(state.materialDrafts || {}).length || Object.keys(state.feeDrafts || {}).length || Object.keys(state.materialSaveErrors || {}).length) {
      throw new Error(ocTranslate("当前页面有未保存修改，请先完成保存再分析资料。"));
    }
    const requestId = options.request_id || this.materialAINewRequestId();
    let payload = options.requestPayload?.request_id === requestId ? { ...options.requestPayload } : null;
    if (!payload) {
      payload = {
        batch_name: batchName,
        version_name: versionName,
        request_id: requestId,
        ...(state.aiClarificationLoaded
          ? { expected_clarification_revision: state.aiClarificationRevision }
          : { clarification_text: state.aiClarification || "" }),
        force: options.force === true ? 1 : 0,
      };
      if (options.paymentPreflightComplete) {
        payload.payment_candidate_refs_json = JSON.stringify((options.paymentCandidateRefs || []).map((row) => ({
          candidate_id: String(row.candidate_id || ''),
          revision: String(row.revision || ''),
          version: String(row.version || ''),
        })));
      }
      if (Array.isArray(options.selectedSourceIds)) {
        const currentSources = options.sourceProgress || state.aiFill?.source_progress || [];
        const sources = new Map(currentSources.map((source) => [String(source.source_id || ""), source]));
        const auditRows = this.materialAIAuditSourceRows(currentSources);
        payload.selected_source_ids_json = JSON.stringify(options.selectedSourceIds.filter((id) => {
          const source = sources.get(String(id));
          return !source || (!auditRows.has(source) && this.materialAICanSelectSource(source));
        }));
      }
    }
    // Keep the exact request for an explicit user retry. Write requests are
    // never replayed automatically because the first response may have been lost.
    state.aiStartOptions = { ...options, request_id: requestId, requestPayload: { ...payload } };
    const started = await this.call("overseas_costing.api.materials.start_source_ai_review", { ...payload });
    if (!isCurrent()) return;
    if (!started?.ok) throw started || new Error(ocTranslate("AI 分析任务启动失败。"));
    if (started.status === "PAYMENT_SELECTION") {
      const scope = started.payment_preflight || {};
      const selectedRefs = Array.isArray(scope.selected_refs) ? scope.selected_refs : [];
      state.aiStartOptions = { ...options, paymentPreflightComplete: false };
      state.aiFill = {
        status: "PAYMENT_SELECTION", progress_step: "等待选择支付来源", progress_percent: 0,
        paymentPreflight: scope,
        paymentSelection: new Set(selectedRefs.map((row) => String(row.candidate_id || ""))),
      };
      state.aiPendingReady = null;
      this.openMaterialAIProgressDialog();
      this.updateMaterialAIProgressSurface();
      return;
    }
    state.aiFill = { ...started, runId: started.run_id, status: started.status, progress_step: "读取资料", progress_percent: 5, progress_revision: Number(started.progress_revision || 0), source_progress: started.source_progress || [], reused: Boolean(started.reused), reuse_reason: started.reuse_reason || "" };
    state.aiPendingReady = null;
    this.openMaterialAIProgressDialog();
    this.updateMaterialAIProgressSurface();
    const polling = this.pollMaterialAIFill(state, batchName, versionName, started.run_id);
    if (options.restart) {
      polling.catch((error) => {
        if (isCurrent()) this.failMaterialAIProgress(error, "AI 分析状态读取失败，请重试。", { resumePolling: true });
      });
      return;
    }
    await polling;
  }

  async pollMaterialAIFill(state, batchName, versionName, runId) {
    let failureCount = 0;
    const generation = state.aiRunGeneration;
    const isCurrent = () => this.materialFeeState === state
      && state.aiRunGeneration === generation
      && this.detailState.batchName === batchName
      && this.detailState.versionName === versionName
      && this.detailState.tab === "documents"
      && (!state.aiFill?.runId || state.aiFill.runId === runId);
    if (!isCurrent()) return;
    state.aiFill = { ...state.aiFill, runId, polling: true, polling_paused: false };
    try {
      for (let attempt = 0; attempt < 300; attempt += 1) {
        if (!isCurrent()) return;
        let status = null;
        try {
          status = await this.call("overseas_costing.api.materials.get_source_ai_review_status", {
            batch_name: batchName,
            run_id: runId,
            after_revision: Number(state.aiFill?.progress_revision || 0),
          }, false);
          if (!isCurrent()) return;
          if (!status?.ok) throw status || new Error(ocTranslate("AI 分析状态读取失败。"));
          failureCount = 0;
        } catch (error) {
          if (!isCurrent()) return;
          failureCount += 1;
          if (!this.materialAIRequestRetryable(error) || failureCount >= 3) {
            this.failMaterialAIProgress(error, "状态读取暂时中断，点击重试继续查看当前任务。", { resumePolling: true });
            return;
          }
          state.aiFill = { ...state.aiFill, connection_error: this.materialAIErrorMessage(error, "状态连接暂时中断，正在自动重试。") };
          this.updateMaterialAIProgressSurface();
          await new Promise((resolve) => window.setTimeout(resolve, Math.min(5000, 800 * failureCount)));
          continue;
        }
        const hadRequestError = Boolean(state.aiFill.connection_error || state.aiFill.request_error || state.aiFill.polling_paused);
        delete state.aiFill.connection_error;
        delete state.aiFill.request_error;
        delete state.aiFill.error_message;
        state.aiFill.polling_paused = false;
        if (status.unchanged) {
          if (hadRequestError) this.updateMaterialAIProgressSurface();
          await new Promise((resolve) => window.setTimeout(resolve, 1200));
          continue;
        }
        state.aiFill = { ...state.aiFill, ...status, runId, polling: true, draftVisible: false };
        if (this.isMaterialAIReadyStatus(status.status)) {
          state.aiPendingReady = status;
          this.showMaterialAIReadyDraft();
        }
        this.updateMaterialAIProgressSurface();
        if (["READY", "READY_WITH_WARNINGS", "FAILED", "STALE", "DISCARDED", "APPLIED"].includes(status.status)) return;
        await new Promise((resolve) => window.setTimeout(resolve, 1200));
      }
      if (isCurrent()) this.failMaterialAIProgress(new Error(ocTranslate("状态读取等待时间较长，点击重试继续查看当前任务。")), undefined, { resumePolling: true });
    } finally {
      if (isCurrent()) state.aiFill.polling = false;
    }
  }

  updateMaterialAIDraftFromInput($input) {
    const itemName = String($input.attr("data-item-name") || "");
    const fieldname = String($input.attr("data-fieldname") || "");
    const value = String($input.val() ?? "").trim();
    const original = String($input.attr("data-original-value") ?? "").trim();
    return this.updateMaterialAIDraftValue(itemName, fieldname, value, original);
  }

  updateMaterialAIDraftValue(itemName, fieldname, value, original = "") {
    const fill = this.ensureMaterialFeeState().aiFill;
    if (!this.isMaterialAIReadyStatus(fill?.status)) return false;
    if (String(itemName || "").startsWith("draft-")) return false;
    const key = `${itemName}:${fieldname}`;
    const proposalUpdate = fill.updates?.[key];
    if (fill.review_mode && proposalUpdate?.proposal_id) {
      const proposalId = String(proposalUpdate.proposal_id);
      const proposal = (fill.proposals || []).find((row) => String(row.proposal_id || "") === proposalId);
      if (!proposal) return false;
      fill.edits = fill.edits || {};
      if (!fill.edits[proposalId]) fill.edits[proposalId] = JSON.parse(JSON.stringify(proposal.payload?.fields || {}));
      fill.edits[proposalId][fieldname] = value;
      fill.selections.add(proposalId);
      fill.updates[key] = { ...proposalUpdate, value, user_edited: true };
      return true;
    }
    if (fill.review_mode) {
      fill.manualUpdates = fill.manualUpdates || {};
      if (value === original) delete fill.manualUpdates[key];
      else {
        const previous = fill.manualUpdates[key] || {};
        fill.manualUpdates[key] = {
          item_name: itemName,
          fieldname,
          value,
          reason: previous.reason || "",
        };
      }
      return true;
    }
    if (value === original) delete fill.updates[key];
    else fill.updates[key] = { item_name: itemName, fieldname, value, user_edited: true };
    return true;
  }

  adoptMaterialAICandidate(itemName, fieldname, candidateIndex) {
    const fill = this.ensureMaterialFeeState().aiFill;
    const cell = fill?.draft?.rows?.[String(itemName)]?.[String(fieldname)];
    const candidate = cell?.candidates?.[candidateIndex];
    if (!candidate) return;
    fill.updates[`${itemName}:${fieldname}`] = {
      item_name: String(itemName),
      fieldname: String(fieldname),
      value: candidate.suggested_value,
      user_edited: true,
    };
    this.renderMaterialFeeWorkspacePreservingPosition();
  }

  async applyMaterialAIFill() {
    const fill = this.ensureMaterialFeeState().aiFill;
    if (fill?.row_review) return this.confirmMaterialAIRowSelection();
    frappe.show_alert?.({ message: ocTranslate("草稿规则已升级，请重新分析资料。"), indicator: "orange" });
    return false;
  }

  async discardMaterialAIFill() {
    const state = this.ensureMaterialFeeState();
    const fill = state.aiFill;
    if (!fill || fill.applying || fill.discarding) return;
    const batchName = String(this.detailState.batchName || "");
    const versionName = String(this.detailState.versionName || "");
    const isCurrent = () => this.materialFeeState === state
      && state.aiFill === fill
      && String(this.detailState.batchName || "") === batchName
      && String(this.detailState.versionName || "") === versionName
      && this.detailState.tab === "documents";
    fill.discarding = true;
    if (fill.row_review) this.renderMaterialAIReviewDialog();
    else this.renderMaterialFeeWorkspace();
    try {
      const result = await this.call("overseas_costing.api.materials.discard_source_ai_review", {
        batch_name: batchName,
        run_id: fill.runId || fill.run_id,
      });
      if (!isCurrent()) return;
      if (!result?.ok) throw new Error(result?.message || "AI 草稿放弃失败。 ");
      state.aiFill = null;
      state.aiProgressDialog?.hide();
      state.aiProgressDialog = null;
      await this.loadMaterialFeeWorkspace({ quiet: true });
    } catch (error) {
      fill.discarding = false;
      if (!isCurrent()) return;
      if (fill.row_review) {
        this.ensureMaterialAIRowSelection(fill).error = this.materialAIErrorMessage(error, "草稿放弃失败，请重试。");
        this.renderMaterialAIReviewDialog();
        return;
      }
      this.renderMaterialFeeWorkspace();
      throw error;
    }
  }

  findMaterialFeeItem(itemName) {
    const items = this.ensureMaterialFeeState().materials?.items || [];
    return items.find((row) => String(row.name) === String(itemName))
      || this.materialReplacementRows(items).find((row) => String(row.name) === String(itemName));
  }

  /**
   * 写令牌（expectedModified）只允许前进，不回退。
   *
   * 值域是服务端 batch 的 modified（Frappe 定宽 DATETIME(6)），因此字典序即时序。
   * 资料页快照允许「先用缓存的、回头再校验」，AI／导入接口也会回带它们各自读到的
   * batch_modified；这些值都可能比本页已经拿到的 revision 更旧。一旦把写令牌回退成
   * 旧值，edit_session 的乐观锁会把本页之后的每次写入都判成「批次数据已被更新」，
   * 页面就再也保存不了任何东西。同一约定见 acceptSavedComprehensiveCost 的 hasNewerRevision。
   *
   * 全部写入点都必须经这里，不要直接给 detailState.expectedModified 赋值。
   */
  acceptBatchWriteRevision(modified) {
    const next = String(modified || "");
    if (!next) return false;
    const current = String(this.detailState?.expectedModified || "");
    if (current && next < current) return false;
    this.detailState.expectedModified = next;
    return true;
  }

  /**
   * 详情加载/刷新读到的写令牌基准值。
   *
   * 换批次时整个换掉：不同批次的 modified 之间没有可比性，沿用上一批的令牌会让新批次的
   * 每次写入都被乐观锁拒绝（openBatchDetail 只设 batchName，不重置令牌）。同一批次内
   * 仍走 acceptBatchWriteRevision 的不回退规则 —— 详情刷新拿到的值可能来自缓存快照。
   */
  adoptBatchWriteRevision(batchName, modified) {
    const name = String(batchName || "").trim();
    const next = String(modified || "");
    if (name && this.detailState.writeRevisionBatch !== name) {
      this.detailState.writeRevisionBatch = name;
      this.detailState.expectedModified = next;
      return Boolean(next);
    }
    return this.acceptBatchWriteRevision(modified);
  }

  updateMaterialFeeExpectedModified(result) {
    const modified = result?.batch_modified;
    if (!modified || !this.acceptBatchWriteRevision(modified)) return;
    if (this.detailState.header) this.detailState.header.modified = modified;
    if (this.materialFeeState?.batchName === this.detailState.batchName) this.materialFeeState.cacheDirty = true;
  }

  applyMaterialFeeHeaderSnapshot(detail, batchName) {
    if (!detail || !detail.ok) throw new Error(detail?.message || "批次最新状态读取失败");
    const header = {
      ...(this.detailState.header || {}),
      ...(detail.header || {}),
      name: detail.batch_name || detail.header?.name || batchName,
      summary_snapshot: detail.summary || this.detailState.header?.summary_snapshot || {},
    };
    this.detailState.header = header;
    const batchIndex = (this.batches || []).findIndex((row) => row.name === batchName);
    if (batchIndex >= 0) this.batches[batchIndex] = { ...this.batches[batchIndex], ...header };
    this.detailState.versionName = detail.version_name || header.current_version || this.detailState.versionName || "";
    if (header.modified) this.acceptBatchWriteRevision(header.modified);
  }

  async saveMaterialFeeCell($input) {
    const state = this.ensureMaterialFeeState();
    if (!$input?.length) return false;
    if (this.isMaterialAIReadyStatus(state.aiFill?.status) && state.aiFill.draftVisible) {
      return this.trackMaterialFeeWrite(() => this.persistMaterialFeeCell($input));
    }
    this.updateMaterialDraftFromInput($input);
    const itemName = String($input.attr("data-item-name") || "");
    const fieldname = String($input.attr("data-fieldname") || "");
    const key = `${itemName}:${fieldname}`;
    state.materialCellWriteTargets[key] = {
      input: $input,
      value: String($input.val() ?? "").trim(),
      revision: ++state.materialCellWriteRevision,
    };
    const currentWrite = state.materialCellWrites.get(key);
    if (currentWrite) return currentWrite;
    let write;
    write = this.trackMaterialFeeWrite(async () => {
      let forceSave = false;
      while (this.materialFeeState === state && state.materialCellWriteTargets[key]) {
        const target = state.materialCellWriteTargets[key];
        const liveInput = this.findMaterialFeeCellInput(itemName, fieldname) || target.input;
        const saved = await this.persistMaterialFeeCell(liveInput, target.value, { forceSave });
        if (!saved || this.materialFeeState !== state) return false;
        const latest = state.materialCellWriteTargets[key];
        if (!latest || latest.revision === target.revision || latest.value === target.value) {
          delete state.materialCellWriteTargets[key];
          if (state.materialDrafts[key]?.value === target.value) delete state.materialDrafts[key];
          delete state.materialSaveErrors[key];
          return true;
        }
        forceSave = true;
      }
      return false;
    });
    state.materialCellWrites.set(key, write);
    try {
      return await write;
    } finally {
      if (state.materialCellWrites.get(key) === write) state.materialCellWrites.delete(key);
      delete state.materialCellWriteTargets[key];
    }
  }

  findMaterialFeeCellInput(itemName, fieldname) {
    let target = null;
    this.$root?.find?.("[data-mf-cell-input]")?.each?.((_, element) => {
      if (target) return;
      const $element = $(element);
      if (
        String($element.attr("data-item-name") || "") === String(itemName || "")
        && String($element.attr("data-fieldname") || "") === String(fieldname || "")
      ) target = $element;
    });
    return target;
  }

  renderMaterialFeeWorkspacePreservingPosition() {
    const viewport = this.$root.find("[data-mf-grid-viewport]").get(0);
    const horizontal = viewport?.scrollLeft || 0;
    const scrollY = Number(globalThis.window?.scrollY || 0);
    this.renderMaterialFeeWorkspace();
    const restore = () => {
      const nextViewport = this.$root.find("[data-mf-grid-viewport]").get(0);
      if (nextViewport) nextViewport.scrollLeft = horizontal;
      this.refreshMaterialGridScroll?.();
      if (globalThis.window?.scrollTo) globalThis.window.scrollTo({ top: scrollY, behavior: "instant" });
    };
    if (globalThis.window?.requestAnimationFrame) globalThis.window.requestAnimationFrame(restore);
    else restore();
  }

  openMaterialPurchaseCorrectionDialog(itemName, fieldname, suggestedValue = undefined) {
    if (fieldname === "project_collection") return this.openProjectCollectionForItem(itemName, suggestedValue);
    if (fieldname === "supplier") return this.openSupplierForItem(itemName, suggestedValue);
    const item = this.findMaterialFeeItem(itemName);
    if (!item) {
      frappe.show_alert({ message: ocTranslate("物料行已变化，请刷新后重试"), indicator: "red" });
      return;
    }
    const column = this.materialFeeGridColumns().find((entry) => entry.field === fieldname) || { label: fieldname };
    const valueField = column.options
      ? { fieldtype: "Select", fieldname: "value", label: column.label, options: column.options.map((option) => option.value).join("\n"), default: suggestedValue ?? item[fieldname] ?? "" }
      : { fieldtype: column.numeric ? "Float" : "Data", fieldname: "value", label: column.label, default: suggestedValue ?? item[fieldname] ?? "", reqd: 1 };
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("修正{0}", [column.label]),
      fields: [
        valueField,
        { fieldtype: "Small Text", fieldname: "reason", label: ocTranslate("修改原因"), reqd: 1, description: ocTranslate("将记录原值、新值、原因和操作者。") },
      ],
      primary_action_label: ocTranslate("保存修正"),
      primary_action: async (values) => {
        const value = String(values.value ?? "").trim();
        const reason = String(values.reason || "").trim();
        if (!reason) return;
        const state = this.ensureMaterialFeeState();
        const key = `${itemName}:${fieldname}`;
        if (this.isMaterialAIReadyStatus(state.aiFill?.status) && state.aiFill.draftVisible) {
          state.aiFill.manualUpdates = state.aiFill.manualUpdates || {};
          state.aiFill.manualUpdates[key] = { item_name: itemName, fieldname, value, reason };
          dialog.hide();
          this.renderMaterialFeeWorkspacePreservingPosition();
          return;
        }
        if (!(await this.ensureMaterialFeeEditSession())) return;
        const result = await this.call("overseas_costing.api.calculate.update_item_field", {
          item_name: itemName,
          fieldname,
          value,
          version_name: this.detailState.versionName || null,
          remark: reason,
          edit_token: this.detailState.editToken,
          expected_modified: this.detailState.expectedModified,
        }, true);
        if (!result?.ok) throw new Error(result?.message || "物料字段修正失败");
        this.updateMaterialFeeExpectedModified(result);
        dialog.hide();
        frappe.show_alert({ message: ocTranslate("物料字段修正已保存"), indicator: "green" });
        await this.loadMaterialFeeWorkspace({ quiet: true });
      },
    });
    dialog.show();
  }

  updateMaterialDraftFromInput($input) {
    const state = this.ensureMaterialFeeState();
    if (this.isMaterialAIReadyStatus(state.aiFill?.status) && state.aiFill.draftVisible) {
      this.updateMaterialAIDraftFromInput($input);
      return;
    }
    const itemName = $input.attr("data-item-name");
    const fieldname = $input.attr("data-fieldname");
    const key = `${itemName}:${fieldname}`;
    const value = String($input.val() ?? "").trim();
    const original = String($input.attr("data-original-value") ?? "").trim();
    if (state.materialDrafts[key]?.value !== value) state.inputRevision += 1;
    if (value === original) {
      delete state.materialDrafts[key];
      delete state.materialSaveErrors[key];
    } else {
      const previous = state.materialDrafts[key];
      state.materialDrafts[key] = { itemName, fieldname, value, error: previous?.value === value ? previous.error : "" };
    }
  }

  async persistMaterialFeeCell($input, queuedValue, { forceSave = false } = {}) {
    if (!$input.length) return false;
    if (this.isMaterialAIReadyStatus(this.ensureMaterialFeeState().aiFill?.status) && this.ensureMaterialFeeState().aiFill.draftVisible) {
      this.updateMaterialAIDraftFromInput($input);
      const $cell = $input.closest(".ocw-mf-cell");
      const key = `${$input.attr("data-item-name")}:${$input.attr("data-fieldname")}`;
      const fill = this.ensureMaterialFeeState().aiFill;
      const isDraft = Boolean(fill?.updates?.[key] || fill?.manualUpdates?.[key]);
      $cell.toggleClass("is-ai-draft", isDraft);
      return true;
    }
    if (queuedValue === undefined) this.updateMaterialDraftFromInput($input);
    const original = String($input.attr("data-original-value") ?? "");
    const value = String(queuedValue === undefined ? $input.val() ?? "" : queuedValue).trim();
    const itemName = $input.attr("data-item-name");
    const fieldname = $input.attr("data-fieldname");
    const saveState = this.ensureMaterialFeeState();
    const errorKey = `${itemName}:${fieldname}`;
    const $cell = $input.closest(".ocw-mf-cell");
    if (!forceSave && value === original) {
      delete saveState.materialSaveErrors[errorKey];
      $cell.removeClass("is-save-error").attr("title", "");
      return true;
    }
    const batchName = this.detailState.batchName;
    const versionName = this.detailState.versionName;
    const isCurrent = () => this.materialFeeState === saveState
      && this.detailState.batchName === batchName
      && this.detailState.versionName === versionName
      && this.detailState.tab === "documents";
    const item = this.findMaterialFeeItem(itemName);
    $input.data("saving", true).prop("disabled", true);
    $cell.addClass("is-saving").removeClass("is-save-error").attr("title", "");
    try {
      if (!item) throw new Error(ocTranslate("物料行已变更，请刷新后重试。"));
      if (!(await this.ensureMaterialFeeEditSession())) throw new Error(ocTranslate("未能获取编辑权，物料未保存。"));
      if (!isCurrent()) return false;
      let result;
      if (["actual_shipped_qty", "shipped_uom"].includes(fieldname)) {
        const editingQuantity = fieldname === "actual_shipped_qty";
        const currentMode = String(item.effective_shipping?.mode || "DEFAULT_PURCHASE");
        const mode = editingQuantity || currentMode !== "DEFAULT_PURCHASE" ? "MANUAL_CONFIRMED" : "DEFAULT_PURCHASE";
        const quantity = editingQuantity ? value : item.effective_shipping_quantity;
        const uom = fieldname === "shipped_uom" ? value : item.effective_shipping_uom;
        result = await this.call("overseas_costing.api.materials.set_shipping_quantity", {
          batch_name: this.detailState.batchName,
          item_name: itemName,
          mode,
          value: quantity,
          uom,
          edit_token: this.detailState.editToken,
          expected_modified: this.detailState.expectedModified,
        });
      } else {
        result = await this.call("overseas_costing.api.calculate.update_item_field", {
          item_name: itemName,
          fieldname,
          value,
          version_name: this.detailState.versionName || null,
          remark: "资料与费用页单格自动保存",
          edit_token: this.detailState.editToken,
          expected_modified: this.detailState.expectedModified,
        });
      }
      if (!result || !result.ok) throw new Error(result?.message || "保存失败");
      if (!isCurrent()) return false;
      delete saveState.materialSaveErrors[errorKey];
      if (saveState.materialDrafts[errorKey]?.value === value) delete saveState.materialDrafts[errorKey];
      this.updateMaterialFeeExpectedModified(result);
      this.detailState.dirty = false;
      frappe.show_alert({ message: ocTranslate("已保存"), indicator: "green" });
      await this.loadMaterialFeeWorkspace({ quiet: true });
      return true;
    } catch (error) {
      if (!isCurrent()) return false;
      saveState.materialSaveErrors[errorKey] = this.normalizeErrorMessage(error);
      if (saveState.materialDrafts[errorKey]) saveState.materialDrafts[errorKey].error = this.normalizeErrorMessage(error);
      $cell.removeClass("is-saving").addClass("is-save-error").attr("title", ocTranslate("{0}；当前输入已保留，请重试。", [this.normalizeErrorMessage(error)]));
      frappe.show_alert({ message: ocTranslate("保存失败，当前值已保留"), indicator: "red" });
      return false;
    } finally {
      if (isCurrent()) {
        $input.data("saving", false).prop("disabled", false);
        $cell.removeClass("is-saving");
      }
    }
  }

  previewMaterialPaste($startInput, text) {
    const rows = String(text || "").replace(/\r/g, "").split("\n").filter((row, index, list) => row || index < list.length - 1).map((row) => row.split("\t"));
    const $table = $startInput.closest("table");
    const $startRow = $startInput.closest("tr");
    const visibleRows = $table.find("tbody tr[data-item-name]").toArray();
    const startRowIndex = visibleRows.indexOf($startRow.get(0));
    const editableColumns = this.materialFeeGridColumns().filter((column) => !column.readonly);
    const startField = $startInput.attr("data-fieldname");
    const startColumnIndex = editableColumns.findIndex((column) => column.field === startField);
    const updates = [];
    const protectedPurchaseCorrections = [];
    const purchaseCorrectionFields = this.materialPurchaseCorrectionFields();
    rows.forEach((values, rowOffset) => {
      const targetRow = visibleRows[startRowIndex + rowOffset];
      if (!targetRow) return;
      const itemName = $(targetRow).attr("data-item-name");
      values.forEach((value, columnOffset) => {
        const column = editableColumns[startColumnIndex + columnOffset];
        if (!column) return;
        const current = this.findMaterialFeeItem(itemName)?.[column.field];
        const effectiveCurrent = column.field === "actual_shipped_qty" ? this.findMaterialFeeItem(itemName)?.effective_shipping_quantity : column.field === "shipped_uom" ? this.findMaterialFeeItem(itemName)?.effective_shipping_uom : current;
        if (String(value).trim() === String(effectiveCurrent ?? "").trim()) return;
        if (
          purchaseCorrectionFields.has(column.field)
          && !this.materialValueIsPlaceholder(column.field, effectiveCurrent, this.findMaterialFeeItem(itemName) || {})
        ) {
          protectedPurchaseCorrections.push({ item_name: itemName, fieldname: column.field });
          return;
        }
        updates.push({ item_name: itemName, fieldname: column.field, value: String(value).trim(), old_value: effectiveCurrent, label: column.label });
      });
    });
    if (protectedPurchaseCorrections.length) {
      frappe.show_alert({ message: ocTranslate("粘贴内容包含已有有效值，请在对应单元格使用“修正”并填写原因。"), indicator: "orange" });
      return;
    }
    if (!updates.length) {
      frappe.show_alert({ message: ocTranslate("粘贴内容没有可更新的单元格"), indicator: "blue" });
      return;
    }
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("预览多格粘贴"),
      fields: [{ fieldtype: "HTML", fieldname: "preview", options: `<div class="ocw-mf-paste-preview"><div class="ocw-mf-dialog-note">${ocTranslate("即将更新 {0} 个单元格；确认后整批提交，任一行失败都会整体回滚。", [updates.length])}</div><table><thead><tr><th>${ocTranslate("物料")}</th><th>${ocTranslate("字段")}</th><th>${ocTranslate("当前值")}</th><th>${ocTranslate("新值")}</th></tr></thead><tbody>${updates.slice(0, 500).map((row) => `<tr><td>${this.escape(this.findMaterialFeeItem(row.item_name)?.material_code || row.item_name)}</td><td>${this.escape(ocTranslate(row.label))}</td><td>${this.escape(this.formatValue(row.old_value ?? "--"))}</td><td>${this.escape(row.value || "--")}</td></tr>`).join("")}</tbody></table></div>` }],
      primary_action_label: ocTranslate("确认整批保存"),
      primary_action: () => this.applyMaterialPaste(dialog, updates),
    });
    dialog._ocwMaterialPreview = preview;
    dialog._ocwMaterialImportBaseChoices = null;
    dialog.show();
    dialog.$wrapper.addClass("ocw-mf-dialog");
  }

  async applyMaterialPaste(dialog, updates) {
    const fill = this.ensureMaterialFeeState().aiFill;
    if (this.isMaterialAIReadyStatus(fill?.status) && fill.draftVisible) {
      updates.forEach(({ item_name, fieldname, value, old_value }) => {
        this.updateMaterialAIDraftValue(
          String(item_name || ""),
          String(fieldname || ""),
          String(value ?? "").trim(),
          String(old_value ?? "").trim()
        );
      });
      dialog.hide();
      this.renderMaterialFeeWorkspace();
      frappe.show_alert({ message: ocTranslate("已更新 {0} 个 AI 草稿单元格", [updates.length]), indicator: "blue" });
      return;
    }
    if (!(await this.ensureEditSession())) return;
    const result = await this.call("overseas_costing.api.calculate.batch_update_items", {
      batch_name: this.detailState.batchName,
      version_name: this.detailState.versionName || null,
      updates: JSON.stringify(updates.map(({ item_name, fieldname, value }) => ({ item_name, fieldname, value, remark: "资料与费用页多格粘贴" }))),
      remark: "资料与费用页多格粘贴",
      edit_token: this.detailState.editToken,
      expected_modified: this.detailState.expectedModified,
    }, true);
    if (!result || !result.ok) throw new Error(result?.message || "多格粘贴保存失败");
    this.updateMaterialFeeExpectedModified(result);
    dialog.hide();
    frappe.show_alert({ message: ocTranslate("已更新 {0} 个单元格", [result.changed_count || 0]), indicator: "green" });
    await this.loadMaterialFeeWorkspace({ quiet: true });
  }

  findMaterialFee(feeKey) {
    const fees = this.ensureMaterialFeeState().fees?.fees || this.ensureMaterialFeeState().fees?.items || [];
    return fees.find((row) => String(row.logical_fee_key || row.fee_key) === String(feeKey));
  }

  materialFeeErrorId(feeKey) {
    const safeKey = String(feeKey || "fee").replace(/[^A-Za-z0-9_-]/g, (character) => `_${character.charCodeAt(0)}_`);
    return `ocw-mf-fee-error-${safeKey}`;
  }

  restoreMaterialFeeInputFocus() {
    const state = this.ensureMaterialFeeState();
    const focused = state.focusedFeeInput;
    const material = state.focusedMaterialInput;
    if ((!focused?.feeKey || !focused?.field) && !material?.itemName) return;
    let target = null;
    this.$root.find(material?.itemName ? "[data-mf-cell-input]" : "[data-mf-fee-input]").each((_, element) => {
      if (target) return;
      const $element = $(element);
      if (material?.itemName) {
        if ($element.attr("data-item-name") === material.itemName && $element.attr("data-fieldname") === material.fieldname) target = element;
      } else if (
        String($element.attr("data-fee-key") || "") === String(focused.feeKey)
        && String($element.attr("data-mf-fee-input") || "") === String(focused.field)
      ) target = element;
    });
    if (!target) return;
    target.focus();
    if (typeof target.setSelectionRange === "function") {
      const position = String(target.value ?? "").length;
      target.setSelectionRange(position, position);
    }
  }

  clearMaterialFeeEditSession() {
    if (!this.detailState) return;
    const clearIntervalFn = globalThis.window?.clearInterval || globalThis.clearInterval;
    if (this.detailState.renewTimer && clearIntervalFn) clearIntervalFn(this.detailState.renewTimer);
    this.detailState.renewTimer = null;
    this.detailState.editToken = "";
    this.detailState.editExpiresAt = "";
  }

  materialFeeEditSessionExpired() {
    if (!this.detailState?.editToken || !this.detailState?.editExpiresAt) return false;
    const expiresAt = Date.parse(this.detailState.editExpiresAt);
    return Number.isFinite(expiresAt) && expiresAt <= Date.now() + 1000;
  }

  async ensureMaterialFeeEditSession() {
    if (this.materialFeeEditSessionExpired()) this.clearMaterialFeeEditSession();
    if (this.detailState?.editToken) return true;
    const state = this.ensureMaterialFeeState();
    if (state.editSessionPromise) return state.editSessionPromise;
    const promise = (async () => Boolean(await this.ensureEditSession()))();
    state.editSessionPromise = promise;
    try {
      return await promise;
    } finally {
      if (state.editSessionPromise === promise) state.editSessionPromise = null;
    }
  }

  materialFeeEditSessionInvalid(error) {
    const message = String(this.normalizeErrorMessage ? this.normalizeErrorMessage(error) : error?.message || error || "");
    return /(?:编辑权|编辑会话|租约|token).*(?:失效|过期|无效|不存在)|(?:失效|过期|无效).*(?:编辑权|编辑会话|租约|token)|当前批次正在被.*编辑/i.test(message);
  }

  updateMaterialFeeDraftFromInput($input) {
    if (!$input?.length) return null;
    const $cell = $input.closest(".ocw-mf-fee-amount-cell");
    const $amount = $cell.find("[data-mf-fee-amount]");
    const $currency = $cell.find("[data-mf-fee-currency]");
    const feeKey = String($input.attr("data-fee-key") || "");
    const state = this.ensureMaterialFeeState();
    state.feeDrafts = state.feeDrafts || {};
    const previous = state.feeDrafts[feeKey] || {};
    const rawCurrency = String($currency.val() ?? "");
    const currency = this.normalizeMaterialFeeCurrency(rawCurrency);
    if (currency !== rawCurrency) $currency.val(currency);
    const draft = {
      ...previous,
      amount: String($amount.val() ?? ""),
      currency,
      forceActual: previous.forceActual || $amount.attr("data-mf-force-actual") === "1",
      touched: true,
    };
    state.feeDrafts[feeKey] = draft;
    if (previous.amount !== draft.amount || previous.currency !== draft.currency) state.inputRevision += 1;
    if (draft.error) {
      const validationError = this.validateMaterialFeeInlineDraft(draft);
      this.setMaterialFeeInlineError($cell, feeKey, validationError);
    }
    return draft;
  }

  validateMaterialFeeInlineDraft(draft) {
    const amountText = String(draft?.amount ?? "").trim();
    if (!amountText) return "费用金额不能为空。";
    const amount = Number(amountText);
    if (!Number.isFinite(amount)) return "费用金额必须是有限数值。";
    if (amount < 0) return "费用金额不能小于 0。";
    const currency = String(draft?.currency ?? "");
    if (!currency) return "费用币种不能为空。";
    if (!this.materialFeeCurrencyOptions().some((option) => option.value === currency)) return "费用币种只能选择人民币、比索或美金。";
    return "";
  }

  setMaterialFeeInlineError($cell, feeKey, message) {
    const errorMessage = String(message || "");
    const state = this.ensureMaterialFeeState();
    state.feeDrafts = state.feeDrafts || {};
    const draft = state.feeDrafts[feeKey] || { amount: "", currency: "", touched: true };
    draft.error = errorMessage;
    state.feeDrafts[feeKey] = draft;
    const errorId = this.materialFeeErrorId(feeKey);
    const $inputs = $cell.find("[data-mf-fee-input]");
    $cell.toggleClass ? $cell.toggleClass("is-save-error", Boolean(errorMessage)) : (errorMessage ? $cell.addClass("is-save-error") : $cell.removeClass("is-save-error"));
    $cell.attr("title", errorMessage);
    $inputs.attr("aria-invalid", errorMessage ? "true" : "false").attr("aria-describedby", errorId);
    $cell.find("[data-mf-fee-error]").text(errorMessage).toggleClass("is-visible", Boolean(errorMessage));
  }

  materialFeeSavePayload(fee, overrides = {}) {
    const feeKey = fee.logical_fee_key || fee.fee_key || "";
    return {
      logical_fee_key: feeKey,
      rule_code: fee.rule_code || feeKey,
      expense_category: fee.expense_category,
      amount_status: overrides.amount_status || fee.amount_state || fee.amount_status || "MISSING",
      amount: overrides.amount !== undefined ? overrides.amount : fee.amount,
      currency: overrides.currency || fee.currency || "RMB",
      allocation_basis: fee.allocation_basis || fee.basis_field || "goods_value",
      scope_type: fee.scope_type || "ALL_ITEMS",
      scope_value_json: fee.scope_value_json || "[]",
      required_evidence_role: fee.required_evidence_role || "",
      included_in_fee_key: fee.included_in_fee_key || "",
      priority_no: fee.priority_no ?? 0,
      remark: fee.remark || "",
      status_change_reason: overrides.status_change_reason || fee.status_change_reason || "",
      is_active: fee.is_active === undefined ? 1 : fee.is_active,
      is_enabled: fee.is_enabled === undefined ? 1 : fee.is_enabled,
    };
  }

  async saveMaterialFeeInlineAmount($input) {
    if (!$input?.length) return false;
    const state = this.ensureMaterialFeeState();
    const feeKey = String($input.attr("data-fee-key") || "");
    const currentWrite = state.feeCellWrites.get(feeKey);
    if (currentWrite) return currentWrite;
    const write = this.trackMaterialFeeWrite(() => this.persistMaterialFeeInlineAmount($input));
    state.feeCellWrites.set(feeKey, write);
    try {
      return await write;
    } finally {
      if (state.feeCellWrites.get(feeKey) === write) state.feeCellWrites.delete(feeKey);
    }
  }

  async trackMaterialFeeWrite(operation) {
    const state = this.ensureMaterialFeeState();
    const batchName = this.detailState.batchName;
    const versionName = this.detailState.versionName;
    const requestId = state.requestId;
    const previous = state.materialFeeWriteQueue;
    const run = async () => {
      const isCurrent = () => this.materialFeeState === state
        && this.detailState.batchName === batchName
        && this.detailState.versionName === versionName
        && (!this.detailState.tab || this.detailState.tab === "documents");
      if (!isCurrent()) return false;
      if (state.calculationWrite) {
        try { await state.calculationWrite; } catch (_error) { /* The trial reports its own failure. */ }
        if (!isCurrent()) return false;
      }
      return operation();
    };
    const pending = previous ? previous.catch(() => false).then(run) : run();
    state.materialFeeWriteQueue = pending;
    state.pendingWrites.add(pending);
    try {
      return await pending;
    } finally {
      state.pendingWrites.delete(pending);
      if (
        state.materialFeeWriteQueue === pending
        && state.pendingWrites.size === 0
        && state.cacheDirty
        && this.materialFeeState === state
        && this.detailState.batchName === batchName
        && this.detailState.versionName === versionName
        && this.detailState.tab === "documents"
      ) {
        const shouldReportRefreshFailure = state.requestId === requestId;
        const refresh = this.loadMaterialFeeWorkspace({ quiet: true });
        const refreshRequestId = state.requestId;
        state.materialFeeWriteQueue = refresh;
        state.pendingWrites.add(refresh);
        try {
          const refreshed = await refresh;
          if (
            refreshed === false
            && this.materialFeeState === state
            && this.detailState.batchName === batchName
            && this.detailState.versionName === versionName
            && this.detailState.tab === "documents"
            && shouldReportRefreshFailure
            && state.requestId === refreshRequestId
          ) {
            frappe.show_alert({ message: ocTranslate("数据已保存，但最新状态读取失败，请点击重试。"), indicator: "orange" });
          }
        } finally {
          state.pendingWrites.delete(refresh);
          if (state.materialFeeWriteQueue === refresh) state.materialFeeWriteQueue = null;
        }
      } else if (state.materialFeeWriteQueue === pending) {
        state.materialFeeWriteQueue = null;
      }
    }
  }

  async persistMaterialFeeInlineAmount($input) {
    if (!$input.length) return;
    const $cell = $input.closest(".ocw-mf-fee-amount-cell");
    if ($cell.data("saving")) return;
    const $amount = $cell.find("[data-mf-fee-amount]");
    const $currency = $cell.find("[data-mf-fee-currency]");
    const feeKey = String($input.attr("data-fee-key") || "");
    const draft = this.updateMaterialFeeDraftFromInput($input);
    const amount = String(draft?.amount ?? "").trim();
    const currency = String(draft?.currency ?? "");
    const originalAmount = String($amount.attr("data-original-value") ?? "").trim();
    const originalCurrency = String($currency.attr("data-original-value") ?? "").trim().toUpperCase();
    const forceActual = Boolean(draft?.forceActual);
    if (!forceActual && amount === originalAmount && currency === originalCurrency) {
      this.setMaterialFeeInlineError($cell, feeKey, "");
      delete this.ensureMaterialFeeState().feeDrafts?.[feeKey];
      return;
    }
    const validationError = this.validateMaterialFeeInlineDraft(draft);
    if (validationError) {
      this.setMaterialFeeInlineError($cell, feeKey, validationError);
      return;
    }
    this.setMaterialFeeInlineError($cell, feeKey, "");
    if (!this.findMaterialFee(feeKey)) return;
    const batchName = String(this.detailState.batchName || "");
    const versionName = this.detailState.versionName;
    const saveState = this.ensureMaterialFeeState();
    const fullRequestId = saveState.requestId;

    const $inputs = $cell.find("[data-mf-fee-input]");
    $cell.data("saving", true).addClass("is-saving").removeClass("is-save-error").attr("title", "");
    $inputs.prop("disabled", true);
    try {
      if (!(await this.ensureMaterialFeeEditSession())) throw new Error(ocTranslate("未能获取编辑权，费用未保存"));
      if (
        this.detailState.batchName !== batchName
        || this.detailState.tab !== "documents"
        || this.materialFeeState !== saveState
        || saveState.requestId !== fullRequestId
      ) return;
      const latestFee = this.findMaterialFee(feeKey);
      if (!latestFee) throw new Error(ocTranslate("费用项已变更，请刷新后重试"));
      const expectedModified = this.detailState.expectedModified;
      const amountStatus = String(latestFee.amount_state || latestFee.amount_status || "MISSING").toUpperCase();
      const payload = this.materialFeeSavePayload(latestFee, {
        amount_status: amountStatus === "MISSING" ? "ESTIMATED" : amountStatus,
        amount,
        currency,
      });
      const result = await this.call("overseas_costing.api.fees.save_fee", {
        batch_name: batchName,
        version_name: versionName,
        fee_payload: JSON.stringify(payload),
        edit_token: this.detailState.editToken,
        expected_modified: expectedModified,
      });
      if (!result || !result.ok) throw new Error(result?.message || "费用保存失败");
      if (this.detailState.batchName !== batchName || this.materialFeeState !== saveState) return;
      this.updateMaterialFeeExpectedModified(result);
      this.detailState.dirty = false;
      delete this.ensureMaterialFeeState().feeDrafts?.[feeKey];
      if (this.detailState.tab !== "documents") return;
      if (saveState.requestId === fullRequestId && !saveState.loading) {
        $amount.attr("data-original-value", amount).attr("data-mf-force-actual", "0");
        $currency.val(currency).attr("data-original-value", currency);
        $cell.data("saving", false).removeClass("is-saving");
        $inputs.prop("disabled", false);
      }
      if (this.detailState.tab === "documents" && saveState.requestId === fullRequestId) {
        frappe.show_alert({ message: result.message || ocTranslate("费用已保存"), indicator: "green" });
      }
      return true;
    } catch (error) {
      const originalMessage = this.normalizeErrorMessage(error);
      if (this.detailState.batchName !== batchName || this.materialFeeState !== saveState) return;
      if (this.materialFeeEditSessionInvalid(error)) this.clearMaterialFeeEditSession();
      const initialErrorMessage = ocTranslate("{0}；当前输入已保留；请再次按 Enter 或失焦重试，必要时使用页面刷新", [originalMessage]);
      if (this.detailState.tab !== "documents" || saveState.requestId !== fullRequestId) {
        saveState.feeDrafts = saveState.feeDrafts || {};
        saveState.feeDrafts[feeKey] = {
          ...(saveState.feeDrafts[feeKey] || draft),
          error: initialErrorMessage,
          touched: true,
        };
        if (this.detailState.tab === "documents") this.renderMaterialFeeWorkspace();
        return;
      }
      const failureState = this.ensureMaterialFeeState();
      failureState.feeDrafts = failureState.feeDrafts || {};
      failureState.feeDrafts[feeKey] = { ...draft, error: initialErrorMessage, touched: true };
      $cell.removeClass("is-saving");
      $inputs.prop("disabled", false);
      this.setMaterialFeeInlineError($cell, feeKey, initialErrorMessage);
      let recovered = false;
      try {
        recovered = await this.recoverMaterialFeeReadonlyState(batchName);
      } catch (_recoveryError) {
        recovered = false;
      }
      if (
        this.detailState.batchName !== batchName
        || this.detailState.tab !== "documents"
        || this.materialFeeState !== saveState
        || saveState.requestId !== fullRequestId
      ) return;
      $cell.data("saving", false).addClass("is-save-error");
      const recoveryMessage = recovered
        ? "已同步最新数据，请再次按 Enter 或失焦重试"
        : "最新状态同步失败，请使用页面刷新后重试";
      const finalErrorMessage = ocTranslate("{0}；当前输入已保留；{1}。", [originalMessage, recoveryMessage]);
      this.setMaterialFeeInlineError($cell, feeKey, finalErrorMessage);
      frappe.show_alert({ message: finalErrorMessage, indicator: "red" });
    }
  }

  async recoverMaterialFeeReadonlyState(batchName) {
    const state = this.ensureMaterialFeeState();
    const expectedBatchName = String(batchName || "");
    const versionName = this.detailState.versionName || null;
    const fullRequestId = state.requestId;
    const requestId = ++state.feeRequestId;
    const snapshot = await this.call("overseas_costing.api.material_fee_workspace.refresh_snapshot", {
      batch_name: expectedBatchName,
      version_name: versionName,
      page: state.page,
      page_length: state.pageLength,
    });
    if (
      this.detailState.batchName !== expectedBatchName
      || this.materialFeeState !== state
      || requestId !== state.feeRequestId
      || fullRequestId !== state.requestId
      || this.detailState.tab !== "documents"
    ) return false;
    const data = snapshot?.data || {};
    if (!snapshot?.ok || !data.detail || !data.fees || !data.preview) {
      throw new Error(snapshot?.message || "最新资料同步失败");
    }
    this.applyMaterialFeeHeaderSnapshot(data.detail, expectedBatchName);
    state.fees = data.fees;
    state.preview = data.preview;
    state.cache = { ...(snapshot.cache || {}) };
    state.cacheDirty = false;
    return true;
  }

  async refreshMaterialFeeData() {
    const state = this.ensureMaterialFeeState();
    const batchName = String(this.detailState.batchName || "");
    const fullRequestId = state.requestId;
    const requestId = ++state.feeRequestId;
    const snapshot = await this.call("overseas_costing.api.material_fee_workspace.refresh_snapshot", {
      batch_name: batchName,
      version_name: this.detailState.versionName || null,
      page: state.page,
      page_length: state.pageLength,
    });
    if (
      this.detailState.batchName !== batchName
      || this.materialFeeState !== state
      || requestId !== state.feeRequestId
      || fullRequestId !== state.requestId
      || this.detailState.tab !== "documents"
    ) return false;
    const data = snapshot?.data || {};
    if (!snapshot?.ok || !data.fees || !data.preview) throw new Error(snapshot?.message || "费用快照刷新失败");
    state.fees = data.fees;
    state.preview = data.preview;
    state.cache = { ...(snapshot.cache || {}) };
    state.cacheDirty = false;
    this.renderMaterialFeeWorkspace();
    return true;
  }

  openMaterialFeeDialog(feeKey, candidate = null) {
    const fee = this.findMaterialFee(feeKey);
    if (!fee) return;
    let selectedKeys = [];
    try { selectedKeys = JSON.parse(fee.scope_value_json || "[]"); } catch (_error) { selectedKeys = []; }
    const materials = this.ensureMaterialFeeState().materials?.items || [];
    const otherFees = (this.ensureMaterialFeeState().fees?.fees || []).filter((row) => row.logical_fee_key !== fee.logical_fee_key);
    const currency = this.normalizeMaterialFeeCurrency(candidate?.currency || fee.currency || "RMB");
    const currencies = this.materialFeeCurrencyOptions();
    const supportedCurrency = currencies.some((option) => option.value === currency);
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("编辑费用：{0}", [fee.expense_category || fee.logical_fee_key]),
      fields: [
        ...(candidate ? [{ fieldtype: "HTML", fieldname: "candidate_note", options: `<div class="ocw-mf-dialog-note">${ocTranslate("已带入凭证识别候选 {0} {1}。请仍由你确认金额状态；系统不会自动认定为实际费用。", [this.escape(candidate.currency || ""), this.escape(candidate.amount || "")])}</div>` }] : []),
        { fieldtype: "Select", fieldname: "amount_status", label: ocTranslate("金额状态"), reqd: 1, options: "MISSING\nESTIMATED\nACTUAL\nNOT_INCURRED\nINCLUDED", default: fee.amount_state || fee.amount_status || "MISSING" },
        { fieldtype: "Data", fieldname: "amount", label: ocTranslate("原币金额"), default: candidate?.amount ?? fee.amount ?? "" },
        { fieldtype: "Select", fieldname: "currency", label: ocTranslate("币种"), reqd: 1, options: supportedCurrency ? currencies : [{ value: "", label: ocTranslate("请选择币种（原 {0}）", [currency]) }, ...currencies], default: supportedCurrency ? currency : "" },
        { fieldtype: "Select", fieldname: "scope_type", label: ocTranslate("适用范围"), reqd: 1, options: "ALL_ITEMS\nITEMS\nDIRECT_ITEM", default: fee.scope_type || "ALL_ITEMS" },
        { fieldtype: "HTML", fieldname: "scope_items", options: `<div class="ocw-mf-scope-picker"><strong>${ocTranslate("限定到指定物料时勾选")}</strong>${materials.map((item) => `<label><input type="checkbox" data-mf-scope-key="${this.escape(item.stable_line_key || "")}" ${selectedKeys.includes(item.stable_line_key) ? "checked" : ""}/><span>${ocTranslate("{0} · {1} · 行 {2}", [this.escape(item.material_code || "--"), this.escape(item.product_name || "--"), this.escape(item.row_no || "--")])}</span></label>`).join("")}</div>` },
        { fieldtype: "Select", fieldname: "included_in_fee_key", label: ocTranslate("已包含于"), options: ["", "purchase_goods_value", ...otherFees.map((row) => row.logical_fee_key)].join("\n"), default: fee.included_in_fee_key || "" },
        { fieldtype: "Small Text", fieldname: "remark", label: ocTranslate("备注 / 未发生或已包含原因"), default: fee.remark || "" },
      ],
      primary_action_label: ocTranslate("保存费用"),
      primary_action: () => this.saveMaterialFeeDialog(dialog, fee),
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-mf-dialog");
  }

  async saveMaterialFeeDialog(dialog, fee) {
    const values = dialog.get_values();
    if (!values) return false;
    const scopeKeys = dialog.$wrapper.find("[data-mf-scope-key]:checked").toArray().map((node) => $(node).attr("data-mf-scope-key"));
    const payload = {
      logical_fee_key: fee.logical_fee_key,
      rule_code: fee.rule_code || fee.logical_fee_key,
      expense_category: fee.expense_category,
      amount_status: values.amount_status,
      amount: values.amount,
      currency: values.currency,
      allocation_basis: fee.allocation_basis || fee.basis_field || "goods_value",
      scope_type: values.scope_type,
      scope_value_json: values.scope_type === "ALL_ITEMS" ? [] : scopeKeys,
      required_evidence_role: fee.required_evidence_role || "",
      included_in_fee_key: values.included_in_fee_key,
      priority_no: fee.priority_no || 0,
      remark: values.remark,
      is_active: 1,
      is_enabled: 1,
    };
    return this.trackMaterialFeeWrite(async () => {
      if (!(await this.ensureMaterialFeeEditSession())) return false;
      const result = await this.call("overseas_costing.api.fees.save_fee", {
        batch_name: this.detailState.batchName,
        version_name: this.detailState.versionName,
        fee_payload: JSON.stringify(payload),
        edit_token: this.detailState.editToken,
        expected_modified: this.detailState.expectedModified,
      }, true);
      if (!result || !result.ok) throw new Error(result?.message || "费用保存失败");
      this.updateMaterialFeeExpectedModified(result);
      dialog.hide();
      frappe.show_alert({ message: result.message || ocTranslate("费用已保存"), indicator: "green" });
      return true;
    });
  }

  materialFeeStatusNeedsReason(fee, nextStatus) {
    const previous = String(fee.amount_state || fee.amount_status || "MISSING").toUpperCase();
    const next = String(nextStatus || "MISSING").toUpperCase();
    if (previous === next) return false;
    if (["NOT_INCURRED", "INCLUDED"].includes(next)) return true;
    if (previous === "ACTUAL" && next === "ESTIMATED") return true;
    if (next === "ACTUAL") {
      return !(fee.evidence || []).some((row) => String(row.validation_status || "") === "VALID" && Number(row.is_final || 0));
    }
    return false;
  }

  requestMaterialFeeStatusReason(fee, nextStatus) {
    return new Promise((resolve) => {
      const dialog = new frappe.ui.Dialog({
        title: ocTranslate("调整费用状态：{0}", [fee.expense_category || fee.logical_fee_key]),
        fields: [
          { fieldtype: "Small Text", fieldname: "reason", label: ocTranslate("修改原因"), reqd: 1, description: ocTranslate("将记录前后状态、操作者和时间。") },
        ],
        primary_action_label: ocTranslate("确认修改"),
        primary_action: (values) => {
          const reason = String(values?.reason || "").trim();
          if (!reason) return;
          dialog.hide();
          resolve(reason);
        },
      });
      dialog.$wrapper.on("hidden.bs.modal", () => resolve(""));
      dialog.show();
    });
  }

  async changeMaterialFeeStatus($select) {
    const feeKey = String($select.attr("data-fee-key") || "");
    const fee = this.findMaterialFee(feeKey);
    if (!fee) return;
    const previous = String($select.attr("data-original-value") || fee.amount_state || fee.amount_status || "MISSING").toUpperCase();
    const nextStatus = String($select.val() || "MISSING").toUpperCase();
    if (previous === nextStatus) return;
    const reason = this.materialFeeStatusNeedsReason(fee, nextStatus)
      ? await this.requestMaterialFeeStatusReason(fee, nextStatus)
      : "";
    if (this.materialFeeStatusNeedsReason(fee, nextStatus) && !reason) {
      $select.val(previous);
      return;
    }
    const currentAmount = String(fee.amount ?? "").trim();
    if (["ESTIMATED", "ACTUAL"].includes(nextStatus) && !currentAmount) {
      $select.val(previous);
      frappe.show_alert({ message: ocTranslate("暂估或实际费用需要金额；也可以先关联并解析凭证。"), indicator: "orange" });
      return;
    }
    return this.trackMaterialFeeWrite(async () => {
      if (!(await this.ensureMaterialFeeEditSession())) {
        $select.val(previous);
        return false;
      }
      $select.prop("disabled", true);
      try {
        const latestFee = this.findMaterialFee(feeKey);
        if (!latestFee) throw new Error(ocTranslate("费用项已变更，请刷新后重试"));
        const payload = this.materialFeeSavePayload(latestFee, {
          amount_status: nextStatus,
          status_change_reason: reason,
        });
        if (["NOT_INCURRED", "INCLUDED"].includes(nextStatus) && reason) payload.remark = reason;
        const result = await this.call("overseas_costing.api.fees.save_fee", {
          batch_name: this.detailState.batchName,
          version_name: this.detailState.versionName,
          fee_payload: JSON.stringify(payload),
          edit_token: this.detailState.editToken,
          expected_modified: this.detailState.expectedModified,
        }, false);
        if (!result?.ok) throw new Error(result?.message || "费用状态保存失败");
        this.updateMaterialFeeExpectedModified(result);
        frappe.show_alert({ message: ocTranslate("费用状态已保存，试算结果待更新"), indicator: "green" });
        return true;
      } catch (error) {
        $select.val(previous).prop("disabled", false);
        throw error;
      }
    });
  }

  openMaterialFeeEvidenceDialog(feeKey) {
    const fee = this.findMaterialFee(feeKey);
    if (!fee) return;
    const candidates = this.ensureMaterialFeeState().fees?.evidence_candidates || [];
    const linked = new Set((fee.evidence || []).map((row) => row.attachment));
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("关联并解析凭证：{0}", [fee.expense_category || fee.logical_fee_key]),
      fields: [{ fieldtype: "HTML", fieldname: "evidence", options: this.renderMaterialFeeEvidencePicker(candidates, linked) }],
      primary_action_label: ocTranslate("开始解析所选凭证"),
      primary_action: () => this.linkSelectedMaterialFeeEvidence(dialog, fee),
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-mf-dialog");
    dialog.$wrapper.on("click", "[data-action='mf-upload-evidence']", () => {
      this.uploadMaterialFeeEvidence(dialog, fee);
    });
    // 预览面板长在操作列上（弹窗外面），所以勾选要把结果同步出去：面板只消费
    // “当前勾的是哪一份”这个事实，候选数据本身仍然来自服务端候选池。
    dialog.$wrapper.on("change", "[data-mf-evidence-attachment]", (event) => {
      this.previewMaterialFeeEvidenceSelection(feeKey, candidates, String($(event.target).attr("data-mf-evidence-attachment") || ""));
    });
  }

  renderMaterialFeeEvidencePicker(candidates, linked) {
    const rows = Array.isArray(candidates) ? candidates : [];
    const groups = this.evidenceSourceGroups(rows);
    const cards = groups.map((group) => {
      // 同一份文件在反复同步里会留下多条副本，被服务端标成“仅审计”的都是被替代、
      // 或不属于当前版本的行。它们默认收进折叠区：列表先呈现可用的，重复行要点开才看；
      // 但一条都不丢，展开后仍用同一套选项渲染、仍可选中解析。
      const available = group.rows.filter((candidate) => !candidate.audit_only);
      const auditOnly = group.rows.filter((candidate) => candidate.audit_only);
      // 该组一条可用资料都没有时折叠区默认展开：否则整组看起来是空的，
      // 用户会以为这条渠道什么也没抓到。
      const openAttribute = available.length ? "" : " open";
      const body = available.map((candidate) => this.materialFeeEvidenceOptionHtml(candidate, linked)).join("")
        || `<div class="ocw-mf-evidence-source-empty">${ocTranslate("该流程暂无可关联资料，可上传新凭证。")}</div>`;
      const collapsed = auditOnly.length
        ? `<details class="ocw-mf-evidence-audit-group"${openAttribute}><summary>${ocTranslate("仅审计 {0} 份", [auditOnly.length])}</summary>${auditOnly.map((candidate) => this.materialFeeEvidenceOptionHtml(candidate, linked)).join("")}</details>`
        : "";
      // 分组计数只算可用份数：折叠区自己带着“仅审计 N 份”，两个数字不重不漏。
      return `<section class="ocw-mf-evidence-source-group"><header><strong>${this.escape(ocTranslate(group.label))}</strong><b>${ocTranslate("{0} 份", [available.length])}</b></header>${body}${collapsed}</section>`;
    }).join("");
    // 只有分组列表滚动，上传按钮和说明留在滚动区之外：早先按钮被卷进滚动容器，
    // 弹窗一窄就看不见，用户找不到“没有就上传”的兜底入口。
    const emptyState = `<div class="ocw-detail-empty"><strong>${ocTranslate("暂无可关联资料")}</strong><span>${ocTranslate("可先上传新凭证。")}</span></div>`;
    return `<div class="ocw-mf-evidence-picker"><div class="ocw-mf-dialog-note">${ocTranslate("可先选凭证，系统再提取金额、币种、费用类别及 SKU／税种关系。采购、费用申请、国际物流三个渠道抓到的资料都按审批归属落到对应分组。所有结果都要在审核草稿中确认。")}</div><div class="ocw-mf-evidence-groups">${cards || emptyState}</div><button class="ocw-outline-btn" type="button" data-action="mf-upload-evidence">${ocTranslate("上传新凭证并解析")}</button></div>`;
  }

  materialFeeEvidenceOptionHtml(candidate, linked) {
    const scopeNote = candidate.in_current_source === false && candidate.stage_selectable
      ? `<em>${ocTranslate("不在当前采购支出范围，解析后需人工确认")}</em>` : "";
    // 服务端标注“仅审计”的候选仍然照常列出、不隐藏，只把原因显示出来：
    // 审批已失效、来源被判定不得作为成本来源，或不属于当前版本。
    const auditNote = candidate.audit_only
      ? `<em class="ocw-mf-evidence-audit-note">${this.escape(candidate.audit_only_reason || ocTranslate("仅审计，不参与核算。"))}</em>` : "";
    // 摘要优先展示：把服务端已解析出的字段放在文件名下方，用户不必打开
    // 凭证就能判断该选哪一份。摘要只做展示，不参与任何业务判定。
    const summary = this.materialFeeEvidenceSummaryHtml(candidate);
    // 文件名含“凭证”的资料高亮，用户上传时常靠命名表达“这就是凭证件”。
    const voucherClass = candidate.is_voucher_name ? " is-voucher" : "";
    const voucherTag = candidate.is_voucher_name
      ? `<span class="ocw-mf-evidence-voucher-tag">${ocTranslate("凭证")}</span>` : "";
    // 附件类型是服务端按文件名推断的既有列，直接展示，用户不必打开文件
    // 就知道这是发票、账单还是装箱单。
    const meta = [candidate.attachment_type, candidate.source_type || "附件", candidate.parse_status || "Draft"]
      .filter((value) => String(value ?? "").trim())
      .map((value) => this.escape(String(value)))
      .join(" · ");
    return `<label class="ocw-mf-evidence-option${voucherClass}"><input type="radio" name="mf-evidence-attachment" data-mf-evidence-attachment="${this.escape(candidate.attachment || "")}"/><span><strong>${this.escape(candidate.file_name || candidate.attachment || "--")}${voucherTag}</strong>${summary}<small>${meta}${linked.has(candidate.attachment) ? ocTranslate(" · 已关联，可重新解析") : ""}</small>${auditNote}${scopeNote}</span></label>`;
  }

  materialFeeEvidenceSummaryHtml(candidate) {
    const summary = candidate?.summary;
    if (!summary || typeof summary !== "object") return "";
    const entries = Object.entries(summary)
      .filter(([, value]) => String(value ?? "").trim())
      .slice(0, 8);
    if (!entries.length) return "";
    const items = entries
      .map(([label, value]) => `<span><i>${this.escape(String(label))}</i><b>${this.escape(String(value))}</b></span>`)
      .join("");
    return `<div class="ocw-mf-evidence-summary">${items}</div>`;
  }

  evidenceSourceGroups(candidates) {
    // 采购、费用申请、国际物流三流程的分组标题恒定展示：某一流程当前没有可关联
    // 资料时给出空态提示，而不是整组消失。否则用户无法判断“这条流程没有资料”
    // 还是“这条流程没被支持”。“其他资料”仅在确有归属不明的候选时才出现。
    //
    // 分组只认服务端给出的 workflow_stage。批次附件表虽然是全批次共用的资料表，
    // 但装箱单、报关单这类资料同样挂在某个审批下被采集：落回它所属的渠道，
    // 用户才看得清“这条渠道到底抓到了什么”。前端不再另立资料分组。
    const order = ["payment", "international_logistics", "purchase", "other"];
    const labels = {
      payment: ocTranslate("费用申请"),
      international_logistics: ocTranslate("国际物流"),
      purchase: ocTranslate("采购"),
      other: ocTranslate("其他资料"),
    };
    const alwaysVisible = ["payment", "international_logistics", "purchase"];
    const grouped = new Map(
      order.map((stage) => [stage, { stage, label: labels[stage], rows: [] }])
    );
    (Array.isArray(candidates) ? candidates : []).forEach((candidate) => {
      const stage = order.includes(String(candidate.workflow_stage || ""))
        ? String(candidate.workflow_stage)
        : "other";
      // 服务端返回的 workflow_label 是文案真源，优先采用。
      const group = grouped.get(stage);
      if (candidate.workflow_label) group.label = candidate.workflow_label;
      group.rows.push(candidate);
    });
    // “其他资料”只在确有归属不明的候选时出现：它是兜底收纳，不应多出一个恒为空的组。
    return order
      .map((stage) => grouped.get(stage))
      .filter((group) => group.rows.length || alwaysVisible.includes(group.stage));
  }

  renderMaterialFeeEvidencePreview(feeKey) {
    // 与「关联并解析凭证」并列的按钮：文案恒为「预览」，外观全部来自既有的
    // .ocw-mf-row-actions button，不自造第二套按钮样式。
    // 没有可预览对象时只用 class 把它调灰，**不用 disabled** —— 原生禁用的按钮
    // 不派发点击、悬停提示也不保证出现，那样它就成了一段点不动的死文字。
    const target = this.materialFeeEvidencePreviewTarget(feeKey);
    const fileUrl = String(target?.file_url || "");
    const className = `ocw-mf-evidence-preview${fileUrl ? "" : " is-muted"}`;
    const shell = `type="button" class="${className}" data-mf-evidence-preview="${this.escape(feeKey)}" data-action="mf-preview-evidence"`;
    if (!fileUrl) {
      return `<button ${shell} title="${ocTranslate('需在关联并解析凭证选择附件')}">${ocTranslate("预览")}</button>`;
    }
    const fileName = target.file_name || target.attachment || "已选附件";
    return `<button ${shell} data-file-url="${this.escape(fileUrl)}" data-file-name="${this.escape(fileName)}" title="${this.escape(ocTranslate("预览 {0}", [fileName]))}">${ocTranslate("预览")}</button>`;
  }

  materialFeeEvidencePreviewTarget(feeKey) {
    // 可预览对象有两个来源，弹窗里刚选中的那份优先；否则退回该行最近关联的那一份
    // （凭证行由服务端按关联时间升序返回，所以从后往前找第一份带文件地址的）。
    // 两者都没有时返回 null，按钮保持灰色。
    const picked = this.materialFeeState?.evidencePreview;
    if (picked && picked.feeKey === String(feeKey || "") && picked.candidate?.file_url) {
      return picked.candidate;
    }
    const linked = this.findMaterialFee(feeKey)?.evidence || [];
    return [...linked].reverse().find((row) => String(row.file_url || "")) || null;
  }

  previewMaterialFeeEvidenceSelection(feeKey, candidates, attachment) {
    const candidate = (candidates || []).find((row) => String(row.attachment || "") === attachment) || null;
    this.ensureMaterialFeeState().evidencePreview = candidate ? { feeKey: String(feeKey || ""), candidate } : null;
    // 只替换这一个按钮，不整表重渲染：重渲染会打断正在打开的弹窗所在行的交互。
    if (!this.$root?.find) return;
    this.$root
      .find(`[data-mf-evidence-preview="${this.escape(feeKey)}"]`)
      .replaceWith(this.renderMaterialFeeEvidencePreview(feeKey));
  }

  async linkSelectedMaterialFeeEvidence(dialog, fee) {
    if (!(await this.ensureEditSession())) return;
    const attachments = dialog.$wrapper.find("[data-mf-evidence-attachment]:checked").toArray().map((node) => $(node).attr("data-mf-evidence-attachment"));
    if (!attachments.length) {
      frappe.show_alert({ message: ocTranslate("请选择一份凭证"), indicator: "orange" });
      return;
    }
    dialog.hide();
    await this.openFeeEvidenceReviewDialog(fee.logical_fee_key, attachments[0]);
  }

  uploadMaterialFeeEvidence(dialog, fee) {
    const batch = this.getDetailBatch();
    this.openManualDocumentUploader(batch, this.detailDocumentAdapter(), this.detectManualDocumentLogisticsType(batch), {
      code: `fee_evidence_${fee.logical_fee_key}`,
      label: ocTranslate("{0}凭证", [fee.expense_category || fee.logical_fee_key]),
      attachmentType: "Other",
      required: false,
    }, async (registered) => {
      const attachment = registered?.attachment?.name;
      if (!attachment || !(await this.ensureEditSession())) return;
      let recognitionOk = false;
      try {
        const recognition = await this.call(
          "overseas_costing.api.import_api.preview_oa_source_attachment",
          { attachment_name: attachment },
          true
        );
        recognitionOk = Boolean(recognition?.ok);
      } catch (_error) {
        // 识别失败不影响凭证关联，用户仍可以手填费用。
      }
      dialog.hide();
      await this.openFeeEvidenceReviewDialog(fee.logical_fee_key, attachment);
      if (!recognitionOk) frappe.show_alert({ message: ocTranslate("规则预解析未完成，AI 审核中仍可关联并人工补录"), indicator: "orange" });
    });
  }

  renderFeeEvidenceReviewProgressShell() {
    return `<div class="ocw-mf-evidence-review-dialog" data-mf-fee-review-host="1">
      <header><div><strong data-mf-fee-review-title>${ocTranslate("正在启动凭证分析")}</strong><span data-mf-fee-review-step>${ocTranslate("建立审核任务")}</span></div><b data-mf-fee-review-percent>0%</b></header>
      <div class="ocw-mf-ai-progress"><i data-mf-fee-review-bar style="width:0%"></i></div>
      <div class="ocw-mf-fee-review-source"><i></i><div><strong data-mf-fee-review-source-name>${ocTranslate("读取凭证资料")}</strong><span data-mf-fee-review-source-detail>${ocTranslate("等待后台任务")}</span></div><em data-mf-fee-review-source-status>${ocTranslate("等待")}</em></div>
      <div class="ocw-mf-ai-progress-warning" data-mf-fee-review-warning hidden></div>
      <div data-mf-fee-review-draft></div>
      <footer><span>${ocTranslate("预览、编辑和关闭均不写入业务数据。")}</span><div><button class="ocw-outline-btn" type="button" data-action="mf-fee-review-discard" hidden>${ocTranslate("放弃草稿")}</button><button class="ocw-primary-btn" type="button" data-action="mf-fee-review-apply" hidden>${ocTranslate("确认应用")}</button></div></footer>
    </div>`;
  }

  feeEvidenceReviewStatusLabel(status) {
    return {
      WAITING: ocTranslate("等待"), DOWNLOADING: ocTranslate("下载中"), READING: ocTranslate("读取中"), PARSED: ocTranslate("已解析"),
      ANALYZING: ocTranslate("AI 分析中"), COMPLETED: ocTranslate("已完成"), FAILED: ocTranslate("失败"), SKIPPED: ocTranslate("跳过"),
    }[String(status || "WAITING").toUpperCase()] || String(status || "等待");
  }

  async openFeeEvidenceReviewDialog(feeKey, attachment, options = {}) {
    const state = this.ensureMaterialFeeState();
    const fee = this.findMaterialFee(feeKey) || {};
    const request = {
      batchName: String(options.batchName || this.detailState.batchName || ""),
      versionName: String(options.versionName || this.detailState.versionName || ""),
      logicalFeeKey: String(feeKey || ""),
      attachment: String(attachment || ""),
      evidenceRole: String(options.evidenceRole || fee.required_evidence_role || "expense_invoice"),
    };
    const requestKey = JSON.stringify(request);
    if (state.feeEvidenceReviewActiveRequestKey) {
      state.feeEvidenceReviewDialog?.show();
      if (state.feeEvidenceReviewActiveRequestKey === requestKey) {
        return state.feeEvidenceReviewStartPromise || true;
      }
      frappe.show_alert({ message: ocTranslate("已有凭证正在启动/分析，请稍候完成后再试。"), indicator: "orange" });
      return false;
    }
    state.feeEvidenceReviewActiveRequestKey = requestKey;
    this._feeEvidenceReviewRequestSequence = Number(this._feeEvidenceReviewRequestSequence || 0) + 1;
    const requestToken = `fee-evidence-review-${this._feeEvidenceReviewRequestSequence}`;
    state.feeEvidenceReviewStartToken = requestToken;
    const isCurrentRequest = () => this.materialFeeState === state &&
      String(state.feeEvidenceReview?.requestToken || "") === requestToken;
    let monitorStarted = false;
    const startPromise = Promise.resolve().then(async () => {
      if (!(await this.ensureMaterialFeeEditSession())) return false;
      if (this.materialFeeState !== state || state.feeEvidenceReviewActiveRequestKey !== requestKey) return false;
      state.feeEvidenceReview = {
        status: "STARTING", logicalFeeKey: request.logicalFeeKey, attachment: request.attachment,
        batchName: request.batchName, versionName: request.versionName, requestKey, requestToken,
        progress_percent: 0, progress_step: "正在启动凭证分析", progress_revision: 0,
      };
      if (!state.feeEvidenceReviewDialog) {
        const dialog = new frappe.ui.Dialog({
          title: ocTranslate("AI 凭证解析与 SKU 分摊审核"),
          fields: [{ fieldtype: "HTML", fieldname: "fee_review", options: this.renderFeeEvidenceReviewProgressShell() }],
        });
        state.feeEvidenceReviewDialog = dialog;
        dialog.$wrapper.addClass("ocw-mf-evidence-review-modal");
        dialog.$wrapper.on("change", "[data-mf-fee-review-select]", (event) => {
          const review = state.feeEvidenceReview;
          const id = String($(event.currentTarget).attr("data-proposal-id") || "");
          if ($(event.currentTarget).prop("checked")) review.selections.add(id);
          else review.selections.delete(id);
          this.invalidateFeeEvidenceMatrixTotals(review);
          const $totals = dialog.$wrapper.find("[data-mf-fee-matrix-totals]");
          if (review.draft && $totals.length) $totals.replaceWith(this.renderFeeEvidenceMatrixFooter(review.draft, review));
        });
        dialog.$wrapper.on("input change", "[data-mf-fee-review-edit]", (event) => {
          const $input = $(event.currentTarget);
          const proposalId = String($input.attr("data-proposal-id") || "");
          const fieldname = String($input.attr("data-fieldname") || "");
          const review = state.feeEvidenceReview;
          review.edits[proposalId] = review.edits[proposalId] || {};
          review.edits[proposalId][fieldname] = $input.attr("type") === "checkbox" ? ($input.prop("checked") ? 1 : 0) : String($input.val() ?? "");
          review.selections.add(proposalId);
          this.invalidateFeeEvidenceMatrixTotals(review);
          dialog.$wrapper.find(`[data-mf-fee-review-select][data-proposal-id='${proposalId.replace(/'/g, "\\'")}']`).prop("checked", true);
          const $totals = dialog.$wrapper.find("[data-mf-fee-matrix-totals]");
          if (review.draft && $totals.length) $totals.replaceWith(this.renderFeeEvidenceMatrixFooter(review.draft, review));
        });
        dialog.$wrapper.on("input", "[data-mf-fee-matrix-input]", (event) => {
          const $input = $(event.currentTarget);
          const review = state.feeEvidenceReview;
          if (!review?.draft) return;
          this.setFeeEvidenceMatrixValue(review, review.draft, String($input.attr("data-item") || ""), String($input.attr("data-column-key") || ""), String($input.val() ?? ""));
          const $totals = dialog.$wrapper.find("[data-mf-fee-matrix-totals]");
          if ($totals.length) $totals.replaceWith(this.renderFeeEvidenceMatrixFooter(review.draft, review));
        });
        dialog.$wrapper.on("change", "[data-mf-fee-review-edit]", () => {
          const review = state.feeEvidenceReview;
          if (!review?.draft) return;
          dialog.$wrapper.find("[data-mf-fee-review-draft]").html(this.renderFeeEvidenceReviewDraft(review.draft, review)).data("rendered", true);
        });
        dialog.$wrapper.on("change", "[data-mf-fee-matrix-input]", () => {
          const review = state.feeEvidenceReview;
          if (!review?.draft) return;
          dialog.$wrapper.find("[data-mf-fee-review-draft]").html(this.renderFeeEvidenceReviewDraft(review.draft, review)).data("rendered", true);
        });
        dialog.$wrapper.on("click", "[data-action='mf-fee-matrix-prev-page'], [data-action='mf-fee-matrix-next-page']", (event) => {
          const review = state.feeEvidenceReview;
          if (!review?.draft) return;
          const direction = String($(event.currentTarget).attr("data-action") || "").includes("next") ? 1 : -1;
          this.setFeeEvidenceMatrixPage(review, review.draft, Number(review.matrixPage || 0) + direction);
          dialog.$wrapper.find("[data-mf-fee-review-draft]").html(this.renderFeeEvidenceReviewDraft(review.draft, review)).data("rendered", true);
        });
        dialog.$wrapper.on("click", "[data-action='mf-fee-source-prev-page'], [data-action='mf-fee-source-next-page']", (event) => {
          const review = state.feeEvidenceReview;
          if (!review?.draft) return;
          const direction = String($(event.currentTarget).attr("data-action") || "").includes("next") ? 1 : -1;
          this.setFeeEvidenceSourcePage(review, review.draft, Number(review.sourcePage || 0) + direction);
          dialog.$wrapper.find("[data-mf-fee-review-draft]").html(this.renderFeeEvidenceReviewDraft(review.draft, review)).data("rendered", true);
        });
        dialog.$wrapper.on("click", "[data-action='mf-fee-warning-prev-page'], [data-action='mf-fee-warning-next-page']", (event) => {
          const review = state.feeEvidenceReview;
          if (!review?.draft) return;
          const direction = String($(event.currentTarget).attr("data-action") || "").includes("next") ? 1 : -1;
          this.setFeeEvidenceWarningPage(review, review.draft, Number(review.warningPage || 0) + direction);
          dialog.$wrapper.find("[data-mf-fee-review-draft]").html(this.renderFeeEvidenceReviewDraft(review.draft, review)).data("rendered", true);
        });
        dialog.$wrapper.on("click", "[data-action='mf-fee-matrix-use-ai']", (event) => {
          const $button = $(event.currentTarget);
          const review = state.feeEvidenceReview;
          if (!review?.draft) return;
          this.adoptFeeEvidenceMatrixSuggestion(review, review.draft, String($button.attr("data-item") || ""), String($button.attr("data-column-key") || ""));
          dialog.$wrapper.find("[data-mf-fee-review-draft]").html(this.renderFeeEvidenceReviewDraft(review.draft, review)).data("rendered", true);
        });
        dialog.$wrapper.on("click", "[data-mf-fee-voucher-details] > summary", (event) => {
          const review = state.feeEvidenceReview;
          if (review) review.matrixDetailsOpen = !Boolean($(event.currentTarget).closest("details").prop("open"));
        });
        dialog.$wrapper.on("click", "[data-action='mf-fee-review-apply']", () => this.applyFeeEvidenceReview().catch((error) => this.showError(error)));
        dialog.$wrapper.on("click", "[data-action='mf-fee-review-discard']", () => this.discardFeeEvidenceReview().catch((error) => this.showError(error)));
      }
      state.feeEvidenceReviewDialog.show();
      state.feeEvidenceReviewDialog.$wrapper.find("[data-mf-fee-review-draft]").empty().removeData("rendered");
      this.updateFeeEvidenceReviewProgress();
      const started = await this.call("overseas_costing.api.fees.start_fee_evidence_review", {
        batch_name: request.batchName,
        version_name: request.versionName,
        logical_fee_key: request.logicalFeeKey,
        attachment: request.attachment,
        evidence_role: request.evidenceRole,
        force: options.force ? 1 : 0,
        edit_token: this.detailState.editToken,
        expected_modified: this.detailState.expectedModified,
      }, false);
      if (!isCurrentRequest()) return;
      if (!started?.ok) throw new Error(started?.message || "凭证分析任务启动失败");
      state.feeEvidenceReview = {
        ...state.feeEvidenceReview, runId: started.run_id, status: started.status,
        progress_revision: Number(started.progress_revision || 0), progress_step: "读取凭证", progress_percent: 5,
      };
      this.updateFeeEvidenceReviewProgress();
      let monitorPromise;
      monitorPromise = Promise.resolve()
        .then(() => this.pollFeeEvidenceReview(state, request.batchName, started.run_id, requestToken))
        .catch((error) => {
          if (!isCurrentRequest()) return;
          state.feeEvidenceReview = { ...state.feeEvidenceReview, status: "FAILED", progress_step: "分析失败", error_message: this.materialAIErrorMessage(error, "凭证分析失败，请重试。") };
          this.updateFeeEvidenceReviewProgress();
        })
        .finally(() => {
          if (state.feeEvidenceReviewMonitorPromise === monitorPromise) state.feeEvidenceReviewMonitorPromise = null;
          if (state.feeEvidenceReviewActiveRequestKey === requestKey) state.feeEvidenceReviewActiveRequestKey = "";
          if (state.feeEvidenceReviewStartToken === requestToken) state.feeEvidenceReviewStartToken = null;
        });
      state.feeEvidenceReviewMonitorPromise = monitorPromise;
      monitorStarted = true;
      return true;
    }).catch((error) => {
      if (isCurrentRequest() && state.feeEvidenceReviewDialog) {
        state.feeEvidenceReview = { ...state.feeEvidenceReview, status: "FAILED", progress_step: "分析失败", error_message: this.materialAIErrorMessage(error, "凭证分析失败，请重试。") };
        this.updateFeeEvidenceReviewProgress();
      } else {
        this.showError(error);
      }
      return false;
    }).finally(() => {
      if (state.feeEvidenceReviewStartPromise === startPromise) state.feeEvidenceReviewStartPromise = null;
      if (!monitorStarted && state.feeEvidenceReviewActiveRequestKey === requestKey) state.feeEvidenceReviewActiveRequestKey = "";
      if (!monitorStarted && state.feeEvidenceReviewStartToken === requestToken) state.feeEvidenceReviewStartToken = null;
    });
    state.feeEvidenceReviewStartPromise = startPromise;
    return startPromise;
  }

  async pollFeeEvidenceReview(state, batchName, runId, requestToken) {
    const isCurrentRequest = () => this.materialFeeState === state &&
      String(state.feeEvidenceReview?.runId || "") === String(runId || "") &&
      String(state.feeEvidenceReview?.requestToken || "") === String(requestToken || "");
    let failures = 0;
    for (let attempt = 0; attempt < 300; attempt += 1) {
      if (!isCurrentRequest()) return;
      let result;
      try {
        result = await this.call("overseas_costing.api.fees.get_fee_evidence_review_status", {
          batch_name: batchName,
          run_id: runId,
          after_revision: Number(state.feeEvidenceReview?.progress_revision || 0),
        }, false);
        if (!isCurrentRequest()) return;
        failures = 0;
      } catch (error) {
        if (!isCurrentRequest()) return;
        failures += 1;
        state.feeEvidenceReview.connection_error = this.materialAIErrorMessage(error, "状态连接暂时中断，正在重试。 ");
        this.updateFeeEvidenceReviewProgress();
        await new Promise((resolve) => window.setTimeout(resolve, Math.min(5000, 800 * failures)));
        if (!isCurrentRequest()) return;
        continue;
      }
      if (!result?.ok) throw new Error(result?.message || "凭证分析状态读取失败");
      if (!result.unchanged) {
        state.feeEvidenceReview = { ...state.feeEvidenceReview, ...result, runId, requestToken };
        delete state.feeEvidenceReview.connection_error;
        if (result.status === "READY") {
          const draft = result.draft || {};
          state.feeEvidenceReview.selections = state.feeEvidenceReview.selections || this.defaultFeeEvidenceReviewSelections(draft);
          state.feeEvidenceReview.edits = state.feeEvidenceReview.edits || {};
          state.feeEvidenceReview.draft = draft;
          this.ensureFeeEvidenceMatrixState(state.feeEvidenceReview, draft);
        }
        this.updateFeeEvidenceReviewProgress();
      }
      if (["READY", "FAILED", "STALE", "APPLIED", "DISCARDED"].includes(String(result.status || ""))) return;
      await new Promise((resolve) => window.setTimeout(resolve, 1200));
      if (!isCurrentRequest()) return;
    }
    throw new Error(ocTranslate("凭证分析超时，请稍后重新分析。 "));
  }

  updateFeeEvidenceReviewProgress() {
    const state = this.ensureMaterialFeeState();
    const review = state.feeEvidenceReview || {};
    const dialog = state.feeEvidenceReviewDialog;
    if (!dialog?.$wrapper?.length) return;
    const $host = dialog.$wrapper.find("[data-mf-fee-review-host]");
    const progress = Math.max(0, Math.min(100, Number(review.progress_percent || 0)));
    const source = (review.source_progress || [])[0] || {};
    const warning = String(review.connection_error || review.ai_warning || review.error_message || "");
    $host.find("[data-mf-fee-review-title]").text(review.status === "READY" ? ocTranslate("凭证审核草稿已生成") : review.status === "FAILED" ? ocTranslate("凭证分析未完成") : ocTranslate("正在解析凭证"));
    $host.find("[data-mf-fee-review-step]").text(review.progress_step || ocTranslate("读取凭证"));
    $host.find("[data-mf-fee-review-percent]").text(`${progress}%`);
    $host.find("[data-mf-fee-review-bar]").css("width", `${progress}%`);
    $host.find("[data-mf-fee-review-source-name]").text(source.display_name || source.label || review.attachment || ocTranslate("凭证资料"));
    $host.find("[data-mf-fee-review-source-detail]").text(source.detail || ocTranslate("后台处理中"));
    $host.find("[data-mf-fee-review-source-status]").text(this.feeEvidenceReviewStatusLabel(source.status));
    $host.find("[data-mf-fee-review-warning]").text(warning).prop("hidden", !warning);
    const ready = review.status === "READY";
    $host.find("[data-action='mf-fee-review-apply'], [data-action='mf-fee-review-discard']").prop("hidden", !ready);
    if (ready && !$host.find("[data-mf-fee-review-draft]").data("rendered")) {
      review.draft = review.draft || {};
      this.ensureFeeEvidenceMatrixState(review, review.draft);
      $host.find("[data-mf-fee-review-draft]").html(this.renderFeeEvidenceReviewDraft(review.draft, review)).data("rendered", true);
    }
  }

  setFeeEvidenceReviewActionBusy(dialog, busy) {
    const $buttons = dialog?.$wrapper?.find("[data-action='mf-fee-review-apply'], [data-action='mf-fee-review-discard']");
    if (typeof $buttons?.prop === "function") $buttons.prop("disabled", Boolean(busy));
  }

  async applyFeeEvidenceReview() {
    const state = this.ensureMaterialFeeState();
    const review = state.feeEvidenceReview;
    if (review?.actionPromise) return review.actionPromise;
    if (review?.status !== "READY") return;
    const dialog = state.feeEvidenceReviewDialog;
    const actionPromise = (async () => {
      if (!(await this.ensureMaterialFeeEditSession())) return;
      if (dialog?.$wrapper?.find("[data-current-source-review]").length) {
        review.edits = { ...(review.edits || {}), _source_review: this.collectCurrentSourceReviewControls(dialog.$wrapper) };
      }
      const componentMatrix = this.validateFeeEvidenceMatrix(review.draft || {}, review);
      const result = await this.call("overseas_costing.api.fees.apply_fee_evidence_review", {
        batch_name: review.batchName || this.detailState.batchName,
        run_id: review.runId,
        selections_json: JSON.stringify([...review.selections]),
        edits_json: JSON.stringify(review.edits || {}),
        component_matrix_json: JSON.stringify(componentMatrix),
        edit_token: this.detailState.editToken,
        expected_modified: this.detailState.expectedModified,
      }, false);
      if (!result?.ok) throw new Error(result?.message || "凭证审核草稿保存失败");
      this.updateMaterialFeeExpectedModified(result);
      dialog?.hide();
      state.feeEvidenceReviewDialog = null;
      state.feeEvidenceReview = null;
      frappe.show_alert({ message: result.message || ocTranslate("凭证草稿已保存"), indicator: "green" });
      if (this.detailState.tab === "documents") await this.loadMaterialFeeWorkspace({ quiet: true });
      return result;
    })();
    review.actionPromise = actionPromise;
    this.setFeeEvidenceReviewActionBusy(dialog, true);
    try {
      return await actionPromise;
    } finally {
      if (review.actionPromise === actionPromise) review.actionPromise = null;
      this.setFeeEvidenceReviewActionBusy(dialog, false);
    }
  }

  async discardFeeEvidenceReview() {
    const state = this.ensureMaterialFeeState();
    const review = state.feeEvidenceReview;
    if (review?.actionPromise) return review.actionPromise;
    if (!review?.runId) return;
    const dialog = state.feeEvidenceReviewDialog;
    const actionPromise = (async () => {
      const result = await this.call("overseas_costing.api.fees.discard_fee_evidence_review", {
        batch_name: review.batchName || this.detailState.batchName,
        run_id: review.runId,
      }, false);
      if (!result?.ok) throw new Error(result?.message || "放弃凭证草稿失败");
      dialog?.hide();
      state.feeEvidenceReviewDialog = null;
      state.feeEvidenceReview = null;
      frappe.show_alert({ message: result.message, indicator: "green" });
      return result;
    })();
    review.actionPromise = actionPromise;
    this.setFeeEvidenceReviewActionBusy(dialog, true);
    try {
      return await actionPromise;
    } finally {
      if (review.actionPromise === actionPromise) review.actionPromise = null;
      this.setFeeEvidenceReviewActionBusy(dialog, false);
    }
  }

  async setMaterialFeeEvidenceStatus(evidenceName, status) {
    if (!evidenceName || !(await this.ensureEditSession())) return;
    const result = await this.call("overseas_costing.api.fees.set_fee_evidence_status", {
      batch_name: this.detailState.batchName,
      evidence_name: evidenceName,
      status,
      remark: status === "VALID" ? "费用页人工确认凭证有效" : "费用页人工标记凭证无效",
      edit_token: this.detailState.editToken,
      expected_modified: this.detailState.expectedModified,
    });
    if (!result || !result.ok) throw new Error(result?.message || "凭证状态更新失败");
    this.updateMaterialFeeExpectedModified(result);
    await this.loadMaterialFeeWorkspace({ quiet: true });
  }

  isMaterialFeeCalculationBusy(state = this.materialFeeState) {
    return Boolean(state?.previewRunning || state?.calculationWrite || state?.costTrialRestarting
      || state?.costTrialAI?.previewing || state?.costTrialAI?.confirming);
  }

  hasBlockingPackingGroups(state = this.materialFeeState) {
    return Boolean((state?.materials?.packing_groups || []).some(
      group => group.blocking || group.status === "needs_reconfirmation"
    ));
  }

  canApplyMaterialAIFill(fill) {
    if (!this.isMaterialAIReadyStatus(fill?.status) || fill.applying || fill.discarding || this.isMaterialFeeCalculationBusy()) return false;
    return Boolean(fill.row_review) && this.canConfirmMaterialAIRowSelection(fill);
  }

  updateMaterialFeeWriteControls(state) {
    if (this.materialFeeState !== state) return;
    const calculating = this.isMaterialFeeCalculationBusy(state);
    this.$root?.find("[data-action='mf-adjust-cost']")?.prop?.("disabled", calculating || Boolean(state.aiFill?.applying));
    for (const root of [this.$root, state.aiProgressDialog?.$wrapper]) {
      root?.find("[data-action='mf-ai-apply']")?.prop?.("disabled", !this.canApplyMaterialAIFill(state.aiFill));
    }
  }

  async flushMaterialFeeInputs(state) {
    // Analysis proposals are independent of saved facts. Only an actual AI write blocks a trial.
    if (state.aiFill?.applying) throw new Error(ocTranslate("AI 资料正在保存，请保存完成后再开始试算。"));
    if (this.hasBlockingPackingGroups(state)) throw new Error(ocTranslate("装箱组成员已变化，请先重新确认装箱组。"));
    if (state.calculationWrite) await state.calculationWrite;
    while (state.pendingWrites.size) await Promise.all([...state.pendingWrites]);
    if (this.materialFeeState !== state || this.detailState.batchName !== state.batchName) return false;
    if (this.detailState.tab !== "documents") {
      if (Object.keys(state.materialDrafts).length || Object.keys(state.feeDrafts).length || Object.keys(state.materialSaveErrors).length) {
        throw new Error(ocTranslate("有资料与费用修改尚未保存，请返回资料与费用完成保存后再试算。"));
      }
      return true;
    }
    for (const key of Object.keys(state.materialDrafts)) {
      const draft = state.materialDrafts[key];
      if (draft.error) throw new Error(ocTranslate("请先处理物料输入：{0}", [draft.error]));
      let input = null;
      this.$root.find("[data-mf-cell-input]").each((_, element) => {
        if ($(element).attr("data-item-name") === draft.itemName && $(element).attr("data-fieldname") === draft.fieldname) input = element;
      });
      if (!input) throw new Error(ocTranslate("有物料修改尚未保存，请返回对应物料行保存后再试算。"));
      await this.saveMaterialFeeCell($(input));
      if (this.materialFeeState !== state || this.detailState.tab !== "documents") return false;
      if (state.materialDrafts[key]) throw new Error(state.materialDrafts[key].error || "物料尚未保存，请重试后再试算。");
    }
    for (const feeKey of Object.keys(state.feeDrafts)) {
      const draft = state.feeDrafts[feeKey];
      const error = draft.error || this.validateMaterialFeeInlineDraft(draft);
      if (error) throw new Error(ocTranslate("请先处理费用输入：{0}", [error]));
      let input = null;
      this.$root.find("[data-mf-fee-amount]").each((_, element) => {
        if (String($(element).attr("data-fee-key")) === feeKey) input = element;
      });
      if (!input) throw new Error(ocTranslate("有费用修改尚未保存，请返回费用区保存后再试算。"));
      await this.saveMaterialFeeInlineAmount($(input));
      if (this.materialFeeState !== state || this.detailState.tab !== "documents") return false;
      if (state.feeDrafts[feeKey]) throw new Error(state.feeDrafts[feeKey].error || "费用尚未保存，请重试后再试算。");
    }
    if (Object.keys(state.materialSaveErrors).length) throw new Error(ocTranslate("物料数据保存失败，请修正红色输入并保存后再试算。"));
    if (state.pendingWrites.size || Object.keys(state.materialDrafts).length || Object.keys(state.feeDrafts).length) {
      throw new Error(ocTranslate("试算准备期间有新的修改尚未保存，请完成录入后再次试算。"));
    }
    return true;
  }

  async refreshMaterialFeeCostPreview(scrollToResult = false) {
    const state = this.ensureMaterialFeeState();
    if (this.isMaterialFeeCalculationBusy(state)) return false;
    const batchName = this.detailState.batchName;
    const versionName = this.detailState.versionName;
    const isCurrent = () => this.materialFeeState === state
      && this.detailState.batchName === batchName
      && this.detailState.versionName === versionName
      && this.detailState.tab === "documents";
    state.costTrialDialog?.hide?.();
    state.costTrialDialog = null;
    state.previewRunning = true;
    state.costTrialAI = { status: "STARTING", actionStage: "reuse" };
    this.updateMaterialFeeWriteControls(state);
    try {
      if (!(await this.flushMaterialFeeInputs(state)) || !isCurrent()) return false;
      if (this.ensureEditSession && !(await this.ensureEditSession())) return false;
      if (!isCurrent()) return false;
      const requestId = state.requestId;
      const feeRequestId = state.feeRequestId;
      const inputRevision = state.inputRevision;
      const inputsUnchanged = () => isCurrent()
        && state.requestId === requestId
        && state.feeRequestId === feeRequestId
        && state.inputRevision === inputRevision;
      const started = await this.call("overseas_costing.api.calculate.start_cost_trial_ai_review", {
        batch_name: batchName,
        version_name: versionName || null,
        edit_token: this.detailState.editToken,
        expected_modified: this.detailState.expectedModified,
        force: 0,
        reuse_only: 0,
      });
      if (!started?.ok) throw new Error(started?.message || "AI 试算任务启动失败。");
      if (!inputsUnchanged()) return false;
      state.costTrialAI = {
        runId: started.run_id,
        status: started.status,
        progressRevision: Number(started.progress_revision || 0),
        scrollToResult: Boolean(scrollToResult),
        requestId,
        feeRequestId,
        inputRevision,
        actionStage: ["QUEUED", "RUNNING"].includes(String(started.status || "")) ? "ai" : "reuse",
        aiCandidateCount: Number(started.ai_candidate_count || 0),
      };
      this.updateMaterialFeeWriteControls(state);
      await this.pollCostTrialAI(state, batchName, started.run_id);
      if (!isCurrent() || state.costTrialAI?.status !== "READY") return false;
      const draft = state.costTrialAI.draft || {};
      const blocked = (draft.fee_suggestions || []).filter((row) => Boolean(row.blocked));
      const fxResolution = draft.fx_resolution || null;
      if (blocked.length || this.costTrialFxIsBlocked(fxResolution)) {
        state.costTrialAI.dialogMode = "repair";
        state.costTrialAI.actionStage = "";
        this.openCostTrialAIReviewDialog({ mode: "repair" });
        throw new Error(blocked.length
          ? this.costTrialBlockingMessage(blocked)
          : this.costTrialFxBlockingMessage(fxResolution));
      }
      state.costTrialAI.selections = (draft.default_selections || []).map((row) => ({ ...row }));
      state.costTrialAI.dialogMode = "adjust";
      state.costTrialAI.actionStage = "preview";
      this.updateMaterialFeeWriteControls(state);
      const preview = await this.previewCostTrialAI();
      if (!preview || !isCurrent()) return false;
      state.costTrialAI.actionStage = "";
      this.openCostTrialAIReviewDialog({ mode: "adjust" });
      return preview;
    } finally {
      state.previewRunning = false;
      this.updateMaterialFeeWriteControls(state);
    }
  }

  async pollCostTrialAI(state, batchName, runId) {
    const isCurrent = () => this.materialFeeState === state
      && this.detailState.batchName === batchName
      && state.costTrialAI?.runId === runId
      && state.requestId === state.costTrialAI?.requestId
      && state.feeRequestId === state.costTrialAI?.feeRequestId
      && state.inputRevision === state.costTrialAI?.inputRevision;
    while (isCurrent()) {
      const current = state.costTrialAI || {};
      const result = await this.call("overseas_costing.api.calculate.get_cost_trial_ai_review_status", {
        batch_name: batchName,
        run_id: runId,
        after_revision: current.progressRevision ?? null,
      });
      if (!isCurrent()) return false;
      state.costTrialAI = {
        ...current,
        ...result,
        runId,
        progressRevision: Number(result.progress_revision || current.progressRevision || 0),
        draft: result.draft || current.draft || null,
      };
      if (["QUEUED", "RUNNING"].includes(String(result.status || ""))) {
        state.costTrialAI.actionStage = "ai";
      } else if (result.status === "CONFIRMING") {
        state.costTrialAI.actionStage = "confirm";
      }
      this.updateMaterialFeeWriteControls(state);
      this.renderCostTrialAIReviewDialog();
      if (!["QUEUED", "RUNNING", "READY", "CONFIRMING", "CONFIRMED", "FAILED", "STALE", "DISCARDED"].includes(String(result.status || ""))) {
        throw new Error(result.message || "AI 试算状态无效，请重试。");
      }
      if (result.status === "READY") return true;
      if (result.status === "CONFIRMED") return false;
      if (["FAILED", "STALE", "DISCARDED"].includes(String(result.status || ""))) {
        throw new Error(result.error_message || (result.status === "STALE"
          ? "试算输入已变化，请重试 AI。"
          : "AI 试算未完成，可重试或放弃本次试算。"));
      }
      const schedule = globalThis.window?.setTimeout || globalThis.setTimeout;
      await new Promise((resolve) => schedule(resolve, 1000));
    }
    return false;
  }

  costTrialBasisLabel(basis) {
    return { goods_value: ocTranslate("货值"), gross_weight: ocTranslate("毛重"), volume: ocTranslate("体积"), chargeable_weight: ocTranslate("计费重") }[basis] || basis || "--";
  }

  costTrialDecisionSourceLabel(source) {
    return {
      REUSED: ocTranslate("沿用上次"),
      AI: ocTranslate("AI 判断"),
      SYSTEM_FALLBACK: ocTranslate("系统兜底"),
      EVIDENCE: ocTranslate("凭证优先"),
      USER_OVERRIDE: ocTranslate("用户调整"),
    }[String(source || "")] || ocTranslate("系统口径");
  }

  costTrialBlockingMessage(rows = []) {
    const details = rows.map((row) => {
      const missing = (row.missing_fields || []).map((field) => ({
        goods_value: "货值",
        gross_weight_kg: "毛重",
        volume_m3: "体积",
        chargeable_weight_kg: "计费重",
      }[field] || field)).join("、");
      if (row.evidence_issue === "SKU_MATCH_INVALID") return ocTranslate("{0}：凭证 SKU 匹配无效", [row.expense_category || row.fee_key]);
      if (row.evidence_issue === "AMOUNT_INVALID") return ocTranslate("{0}：凭证金额无效", [row.expense_category || row.fee_key]);
      return ocTranslate("{0}：缺少 {1}", [row.expense_category || row.fee_key, missing || "可用分摊数据"]);
    });
    return ocTranslate("当前无法试算，请先补充：{0}", [details.join("；")]);
  }

  costTrialFxResolution() {
    const trial = this.ensureMaterialFeeState().costTrialAI || {};
    return trial.preview?.fx_resolution || trial.draft?.fx_resolution || null;
  }

  costTrialFxBlockingMessage(resolution = this.costTrialFxResolution()) {
    const errors = (resolution?.blocking_errors || []).map((error) => String(error?.message || error || "").trim()).filter(Boolean);
    return errors.join("；") || "未取得有效汇率，请刷新后重新试算。";
  }

  costTrialFxIsBlocked(resolution = this.costTrialFxResolution()) {
    return Boolean(resolution && (resolution.ok === false || (resolution.blocking_errors || []).length));
  }

  formatCostTrialFxRate(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return "--";
    return number.toFixed(6).replace(/\.?0+$/, "");
  }

  costTrialFxSourceLabel(rate = {}) {
    return rate.source_label || {
      version_snapshot: ocTranslate("当前版本汇率"),
      currency_exchange: ocTranslate("当日汇率库"),
      fx_api: ocTranslate("当日汇率 API"),
      historical_currency_exchange: ocTranslate("历史汇率暂估"),
    }[String(rate.source || "")] || ocTranslate("已解析汇率");
  }

  renderCostTrialFxResolution(resolution = this.costTrialFxResolution()) {
    if (!resolution) return "";
    if (this.costTrialFxIsBlocked(resolution)) {
      return `<section class="ocw-cost-trial-fx is-blocked"><header><strong>${ocTranslate("汇率未就绪")}</strong><span>${ocTranslate("汇率日期 {0}", [this.escape(resolution.calculation_date || "--")])}</span></header><p>${this.escape(this.costTrialFxBlockingMessage(resolution))}</p></section>`;
    }
    const rates = resolution.rates || {};
    const usd = rates.USD || {};
    const mxn = rates.MXN || {};
    const estimated = Boolean(resolution.is_estimated || usd.is_estimated || mxn.is_estimated);
    const sourceLabels = [...new Set([this.costTrialFxSourceLabel(usd), this.costTrialFxSourceLabel(mxn)].filter(Boolean))].join(" · ");
    const rateDates = [...new Set([usd.rate_date, mxn.rate_date].filter(Boolean))];
    const dateDetail = rateDates.length
      ? ocTranslate("实际采用 {0}", [rateDates.join(" / ")])
      : ocTranslate("实际采用 {0}", [resolution.calculation_date || "--"]);
    return `<section class="ocw-cost-trial-fx ${estimated ? "is-estimated" : ""}">
      <header><strong>${estimated ? ocTranslate("历史汇率暂估") : ocTranslate("计算汇率")}</strong><span>${ocTranslate("汇率日期 {0}", [this.escape(resolution.calculation_date || "--")])}</span></header>
      <div><span>${this.escape(sourceLabels)}</span><span>${this.escape(dateDetail)}</span></div>
      <p><strong>1 USD = ${this.escape(this.formatCostTrialFxRate(resolution.fx_usd_to_rmb))} RMB</strong><strong>1 RMB = ${this.escape(this.formatCostTrialFxRate(resolution.fx_rmb_to_mxn))} MXN</strong></p>
    </section>`;
  }

  renderCostTrialAIReview() {
    const trial = this.ensureMaterialFeeState().costTrialAI || {};
    const draft = trial.draft || {};
    const preview = trial.preview || null;
    const suggestions = draft.fee_suggestions || [];
    const waiting = ["QUEUED", "RUNNING"].includes(String(trial.status || ""));
    const fxResolution = preview?.fx_resolution || draft.fx_resolution || null;
    const selectedById = new Map((trial.selections || []).map((row) => [row.suggestion_id, row]));
    const rows = suggestions.map((row) => {
      const savedChoice = selectedById.get(row.suggestion_id) || {};
      const options = (row.available_alternatives || []).map((option) => {
        const selectedBasis = savedChoice.basis || row.default_basis || (row.recommended_basis_available ? row.recommended_basis : "");
        const selected = option.basis === selectedBasis ? "selected" : "";
        return `<option value="${this.escape(option.basis)}" ${selected}>${ocTranslate("{0}·可用", [this.escape(ocTranslate(option.label))])}</option>`;
      }).join("");
      const basisControl = `<label><span>${ocTranslate("本次分摊口径")}</span><select data-cost-trial-basis data-suggestion-id="${this.escape(row.suggestion_id)}" data-recommended-basis="${this.escape(row.recommended_basis || "")}"><option value="">${ocTranslate("请选择")}</option>${options}</select></label>`;
      const evidence = row.evidence_locked
        ? `<div class="ocw-cost-trial-evidence"><strong>${ocTranslate("凭证优先")}</strong><span>${ocTranslate("{0} 条 SKU 分项·RMB {1}", [Number(row.evidence_component_count || 0), this.escape(row.evidence_amount_rmb || "0.00")])}</span></div>`
        : row.evidence_issue === "SKU_MATCH_INVALID"
          ? `<div class="ocw-cost-trial-warning">${ocTranslate("凭证 SKU 匹配存在歧义，请返回费用凭证完成人工匹配。")}</div>`
          : row.evidence_issue === "AMOUNT_INVALID"
            ? `<div class="ocw-cost-trial-warning">${ocTranslate("凭证分项金额无效，请先修正凭证。")}</div>`
            : row.evidence_issue === "TOTAL_MISMATCH"
              ? `<div class="ocw-cost-trial-warning">${ocTranslate("凭证分项合计 RMB {0} 与费用金额不一致，本次不采用凭证分项。", [this.escape(row.evidence_amount_rmb || "0.00")])}</div>${basisControl}`
              : basisControl;
      const unavailable = row.default_basis ? "" : `<p class="is-warning">${ocTranslate("当前没有完整可用口径，缺少 {0}。请补齐后再试算。", [this.escape((row.missing_fields || []).join("、"))])}</p>`;
      const alternativeDiffs = (row.available_alternatives || []).length > 1
        ? `<div class="ocw-cost-trial-alternatives"><strong>${ocTranslate("可选口径的逐行试算差异")}</strong>${row.available_alternatives.map((option) => `<span>${this.escape(ocTranslate(option.label))}：RMB ${this.escape((option.allocation_preview || []).map((item) => item.amount_rmb).join(" / ") || "--")}</span>`).join("")}</div>`
        : "";
      return `<article class="ocw-cost-trial-fee ${row.evidence_locked ? "is-evidence" : ""}">
        <header><div><strong>${this.escape(row.expense_category || row.fee_key)}</strong><span>${this.escape(row.currency)} ${this.escape(row.amount)}</span></div><em>${this.escape(this.costTrialDecisionSourceLabel(row.decision_source))}·${this.escape(this.costTrialBasisLabel(row.default_basis || row.recommended_basis))}</em></header>
        <p>${this.escape(row.reason || ocTranslate("已选用当前完整可计算的口径，可直接调整后试算。"))}</p>${unavailable}${alternativeDiffs}${evidence}
      </article>`;
    }).join("");
    const previewHtml = preview ? `<section class="ocw-cost-trial-preview ${preview.trial_review?.is_temporary ? "is-temporary" : ""}"><strong>${preview.trial_review?.is_temporary ? ocTranslate("暂行口径试算·非完整成本") : ocTranslate("完整口径预览")}</strong><span>${ocTranslate("综合成本 RMB {0}", [this.escape(preview.summary?.total_cost_rmb || "0.00")])}</span><small>${ocTranslate("服务器已校验逐 SKU 分摊与金额守恒。")}</small></section>` : "";
    const summary = draft.decision_summary || {};
    const sourceSummary = [["reused", "沿用上次"], ["ai", "AI 判断"], ["system_fallback", "系统兜底"], ["evidence", "凭证优先"]]
      .filter(([key]) => Number(summary[key] || 0) > 0)
      .map(([key, label]) => `${label} ${Number(summary[key] || 0)}`).join(" · ");
    return `<div class="ocw-cost-trial-review"><header><div><strong>${trial.dialogMode === "repair" ? ocTranslate("试算所需资料待补") : ocTranslate("调整分摊口径")}</strong><span>${this.escape(sourceSummary || ocTranslate("已自动选择完整可用口径"))}</span></div></header>
      ${draft.ai_warning ? `<div class="ocw-cost-trial-warning">${this.escape(draft.ai_warning)}</div>` : ""}
      <main>${this.renderCostTrialFxResolution(fxResolution)}${waiting ? `<div class="ocw-cost-trial-running"><strong>${this.escape(trial.progress_step || ocTranslate("DeepSeek 正在分析费用口径"))}</strong><span>${Math.max(0, Math.min(100, Number(trial.progress_percent || 0)))}%</span></div>` : rows || `<div class="ocw-detail-empty"><strong>${ocTranslate("当前没有需要分摊的费用")}</strong></div>`}${previewHtml}</main>
    </div>`;
  }

  costTrialFooterState() {
    const trial = this.ensureMaterialFeeState().costTrialAI || {};
    const draft = trial.draft || {};
    const waiting = ["QUEUED", "RUNNING"].includes(String(trial.status || ""));
    const fxResolution = trial.preview?.fx_resolution || draft.fx_resolution || null;
    const fxBlocked = this.costTrialFxIsBlocked(fxResolution);
    const blockedSuggestions = (draft.fee_suggestions || []).filter((row) => row.blocked);
    const hasBlockingHint = waiting || fxBlocked || blockedSuggestions.length > 0;
    const blockingHint = waiting
      ? "AI 正在分析费用口径，请稍候。"
      : fxBlocked
        ? this.costTrialFxBlockingMessage(fxResolution)
        : this.costTrialBlockingMessage(blockedSuggestions);
    return { hasBlockingHint, blockingHint };
  }

  renderCostTrialNativeFooterActions() {
    const { hasBlockingHint, blockingHint } = this.costTrialFooterState();
    return `<div class="ocw-cost-trial-secondary-actions"><button type="button" class="btn btn-default ocw-outline-btn" data-action="cost-trial-retry">${ocTranslate("重新让 AI 判断")}</button><button type="button" class="btn btn-default ocw-outline-btn" data-action="cost-trial-back">${ocTranslate("返回补资料")}</button><button type="button" class="btn btn-default ocw-outline-btn" data-action="cost-trial-discard">${ocTranslate("放弃试算")}</button></div>${hasBlockingHint ? `<span class="ocw-cost-trial-action-hint">${this.escape(blockingHint)}</span>` : ""}`;
  }

  ensureCostTrialDialogFooter() {
    const dialog = this.ensureMaterialFeeState().costTrialDialog;
    const $footer = dialog?.$wrapper?.find?.(".modal-footer");
    if (!$footer?.length || typeof globalThis.$ !== "function") return;
    $footer.find?.(".ocw-cost-trial-secondary-actions, .ocw-cost-trial-action-hint")?.remove?.();
    const $actions = $(this.renderCostTrialNativeFooterActions());
    const $primary = dialog?.get_primary_btn?.() || $footer.find?.(".btn-primary");
    const $standardActions = $footer.find?.(".standard-actions");
    const $anchor = $standardActions?.length ? $standardActions : $primary;
    if ($anchor?.length) $actions.insertBefore?.($anchor);
    else $footer.append?.($actions);
  }

  openCostTrialAIReviewDialog({ mode = "adjust" } = {}) {
    const state = this.ensureMaterialFeeState();
    if (!globalThis.frappe?.ui?.Dialog) return;
    if (state.costTrialAI) state.costTrialAI.dialogMode = mode;
    state.costTrialDialog?.hide?.();
    const dialog = new frappe.ui.Dialog({
      title: mode === "repair" ? ocTranslate("试算资料待补") : ocTranslate("调整试算分摊"),
      size: "extra-large",
      fields: [{ fieldname: "review_html", fieldtype: "HTML" }],
      primary_action_label: mode === "repair" ? "补齐资料后重试" : "应用调整并试算",
      primary_action: () => this.confirmCostTrialAI().catch((error) => this.showError(error)),
    });
    state.costTrialDialog = dialog;
    dialog.$wrapper?.addClass?.("ocw-cost-trial-dialog");
    dialog.$wrapper?.on?.("input change", "[data-cost-trial-basis]", () => {
      this.invalidateCostTrialAIPreview({ render: false });
      this.updateCostTrialAIPrimaryAction();
    });
    dialog.$wrapper?.on?.("click", "[data-action='cost-trial-back']", () => dialog.hide());
    dialog.$wrapper?.on?.("click", "[data-action='cost-trial-discard']", () => this.discardCostTrialAI().catch((error) => this.showError(error)));
    dialog.$wrapper?.on?.("click", "[data-action='cost-trial-retry']", () => this.retryCostTrialAI().catch((error) => this.showError(error)));
    dialog.show();
    this.renderCostTrialAIReviewDialog();
  }

  renderCostTrialAIReviewDialog() {
    const state = this.ensureMaterialFeeState();
    const dialog = state.costTrialDialog;
    const host = dialog?.fields_dict?.review_html?.$wrapper;
    host?.html?.(this.renderCostTrialAIReview());
    this.ensureCostTrialDialogFooter();
    this.updateCostTrialAIPrimaryAction();
  }

  collectCostTrialAISelections({ validate = true } = {}) {
    const state = this.ensureMaterialFeeState();
    const trial = state.costTrialAI || {};
    const wrapper = state.costTrialDialog?.$wrapper;
    const selections = [];
    let renderedInputs = 0;
    wrapper?.find?.("[data-cost-trial-basis]")?.each?.((_index, element) => {
      renderedInputs += 1;
      const $input = $(element);
      const suggestionId = String($input.attr("data-suggestion-id") || "");
      const basis = String($input.val() || "");
      if (validate && !basis) throw new Error(ocTranslate("请为每项费用选择可用的分摊口径。"));
      selections.push({ suggestion_id: suggestionId, basis, reason: "" });
    });
    if (!renderedInputs) {
      const defaults = trial.selections?.length ? trial.selections : (trial.draft?.default_selections || []);
      defaults.forEach((row) => selections.push({
        suggestion_id: String(row.suggestion_id || ""),
        basis: String(row.basis || ""),
        reason: "",
      }));
    }
    if (validate) {
      const fxResolution = trial.preview?.fx_resolution || trial.draft?.fx_resolution || null;
      if (this.costTrialFxIsBlocked(fxResolution)) {
        throw new Error(this.costTrialFxBlockingMessage(fxResolution));
      }
      const blocked = (trial.draft?.fee_suggestions || []).filter((row) => Boolean(row.blocked));
      if (blocked.length) throw new Error(this.costTrialBlockingMessage(blocked));
      if (selections.some((row) => !row.suggestion_id || !row.basis)) {
        throw new Error(ocTranslate("请为每项费用选择可用的分摊口径。"));
      }
    }
    return selections;
  }

  costTrialAIReadyToConfirm() {
    const trial = this.ensureMaterialFeeState().costTrialAI;
    if (!trial || trial.status !== "READY" || trial.confirming || trial.previewing) return false;
    if (this.costTrialFxIsBlocked(trial.preview?.fx_resolution || trial.draft?.fx_resolution || null)) return false;
    if ((trial.draft?.fee_suggestions || []).some((row) => Boolean(row.blocked))) return false;
    try {
      this.collectCostTrialAISelections();
      return true;
    } catch (_error) {
      return false;
    }
  }

  updateCostTrialAIPrimaryAction() {
    const state = this.ensureMaterialFeeState();
    const trial = state.costTrialAI || {};
    const $button = state.costTrialDialog?.get_primary_btn?.();
    const label = trial.actionStage === "preview"
      ? "校验分摊…"
      : trial.actionStage === "confirm"
        ? "保存试算…"
        : trial.dialogMode === "repair" ? "补齐资料后重试" : "应用调整并试算";
    $button?.text?.(label);
    $button?.prop?.("disabled", !this.costTrialAIReadyToConfirm());
    const busy = Boolean(trial.confirming || trial.previewing || state.costTrialRestarting);
    state.costTrialDialog?.$wrapper?.find?.("[data-cost-trial-basis]")?.prop?.("disabled", busy);
    state.costTrialDialog?.$wrapper?.find?.("[data-action^='cost-trial-']")?.prop?.("disabled", busy);
  }

  invalidateCostTrialAIPreview({ render = true } = {}) {
    const state = this.ensureMaterialFeeState();
    const trial = state.costTrialAI;
    if (!trial) return;
    trial.preview = null;
    trial.selections = this.collectCostTrialAISelections({ validate: false });
    state.costTrialDialog?.$wrapper?.find?.(".ocw-cost-trial-preview")?.remove?.();
    if (render) this.renderCostTrialAIReviewDialog();
  }

  async previewCostTrialAI() {
    const state = this.ensureMaterialFeeState();
    const trial = state.costTrialAI;
    if (!trial?.runId || trial.previewing) return false;
    const selections = this.collectCostTrialAISelections();
    trial.selections = selections;
    trial.preview = null;
    trial.previewing = true;
    try {
      const preview = await this.call("overseas_costing.api.calculate.preview_cost_trial", {
        batch_name: this.detailState.batchName,
        run_id: trial.runId,
        selections: JSON.stringify(selections),
      });
      if (!preview?.ok) throw new Error(preview?.message || "试算预览失败。");
      trial.preview = preview;
      trial.selections = selections;
      return preview;
    } finally {
      trial.previewing = false;
      this.renderCostTrialAIReviewDialog();
    }
  }

  async confirmCostTrialAI() {
    const state = this.ensureMaterialFeeState();
    const trial = state.costTrialAI;
    if (!trial?.runId) throw new Error(ocTranslate("本次 AI 试算任务已失效，请重试。"));
    if (trial.confirming) return false;
    const currentSelections = this.collectCostTrialAISelections();
    const batchName = this.detailState.batchName;
    const versionName = this.detailState.versionName;
    const requestId = state.requestId;
    const feeRequestId = state.feeRequestId;
    const inputRevision = state.inputRevision;
    const isUnchangedView = () => this.materialFeeState === state
      && this.detailState.batchName === batchName
      && this.detailState.versionName === versionName
      && this.detailState.tab === "documents"
      && state.requestId === requestId
      && state.feeRequestId === feeRequestId
      && state.inputRevision === inputRevision;
    trial.confirming = true;
    const previewMatches = trial.preview?.preview_token
      && JSON.stringify(currentSelections) === JSON.stringify(trial.selections || []);
    let calculationWrite = null;
    try {
      if (!previewMatches) {
        trial.actionStage = "preview";
        trial.preview = null;
        trial.selections = currentSelections;
        this.updateCostTrialAIPrimaryAction();
        const preview = await this.call("overseas_costing.api.calculate.preview_cost_trial", {
          batch_name: batchName,
          run_id: trial.runId,
          selections: JSON.stringify(currentSelections),
        });
        if (!preview?.ok || !preview.preview_token) throw new Error(preview?.message || "试算预览失败。");
        const latestSelections = this.collectCostTrialAISelections();
        if (JSON.stringify(latestSelections) !== JSON.stringify(currentSelections)) {
          trial.preview = null;
          trial.selections = latestSelections;
          throw new Error(ocTranslate("分摊口径已变化，未保存旧选择，请再次确认。"));
        }
        trial.preview = preview;
      }
      trial.actionStage = "confirm";
      this.updateCostTrialAIPrimaryAction();
      calculationWrite = this.call("overseas_costing.api.calculate.confirm_cost_trial", {
        batch_name: batchName,
        run_id: trial.runId,
        preview_token: trial.preview.preview_token,
        selections: JSON.stringify(currentSelections),
        edit_token: this.detailState.editToken,
        expected_modified: this.detailState.expectedModified,
      });
      state.calculationWrite = calculationWrite;
      const result = await calculationWrite;
      if (!result?.ok || !result?.saved) throw new Error(result?.message || "AI 试算保存失败。");
      this.acceptSavedComprehensiveCost(result, batchName, { versionName, preserveDirty: !isUnchangedView() });
      if (!isUnchangedView()) return false;
      if (result.batch_modified && this.detailState.expectedModified
        && String(this.detailState.expectedModified) > String(result.batch_modified)) return false;
      state.preview = result;
      state.costTrialDialog?.hide?.();
      state.costTrialDialog = null;
      this.renderDetailShell?.();
      if (this.detailState.editToken) this.updateEditLeaseStatus?.();
      this.renderMaterialFeeWorkspace();
      if (trial.scrollToResult) this.$root.find(".ocw-mf-cost-section").get(0)?.scrollIntoView({ behavior: "smooth", block: "start" });
      let refreshWarning = "";
      if (this.viewState?.screen === "detail" && this.refreshSavedTrialReviewClassification) {
        try {
          await this.refreshSavedTrialReviewClassification(batchName);
        } catch (refreshError) {
          // 试算已经事务保存；分类回读失败不应把已保存结果误报为失败。
          refreshWarning = refreshError?.message || "工作台分类刷新失败";
        }
      }
      frappe.show_alert({
        message: refreshWarning
          ? ocTranslate("试算已保存，但工作台分类刷新失败：{0}。请稍后刷新。", [refreshWarning])
          : (result.trial_review?.is_temporary ? ocTranslate("暂行口径试算已保存") : ocTranslate("试算完成")),
        indicator: refreshWarning ? "orange" : "green",
      });
      return result;
    } finally {
      if (state.calculationWrite === calculationWrite) state.calculationWrite = null;
      trial.confirming = false;
      trial.actionStage = "";
      this.updateCostTrialAIPrimaryAction();
      this.updateMaterialFeeWriteControls(state);
    }
  }

  async discardCostTrialAI() {
    const state = this.ensureMaterialFeeState();
    const trial = state.costTrialAI;
    if (trial?.runId) await this.call("overseas_costing.api.calculate.discard_cost_trial_ai_review", {
      batch_name: this.detailState.batchName, run_id: trial.runId,
    });
    state.costTrialDialog?.hide?.();
    state.costTrialDialog = null;
    state.costTrialAI = null;
    this.updateMaterialFeeWriteControls(state);
  }

  async openCostTrialAdjustment() {
    const state = this.ensureMaterialFeeState();
    if (this.isMaterialFeeCalculationBusy(state)) return false;
    const batchName = this.detailState.batchName;
    const versionName = this.detailState.versionName;
    const isCurrent = () => this.materialFeeState === state
      && this.detailState.batchName === batchName
      && this.detailState.versionName === versionName
      && this.detailState.tab === "documents";
    state.costTrialDialog?.hide?.();
    state.costTrialDialog = null;
    state.previewRunning = true;
    state.costTrialAI = { status: "STARTING", actionStage: "reuse", dialogMode: "adjust" };
    this.updateMaterialFeeWriteControls(state);
    try {
      if (!(await this.flushMaterialFeeInputs(state)) || !isCurrent()) return false;
      if (this.ensureEditSession && !(await this.ensureEditSession())) return false;
      if (!isCurrent()) return false;
      const requestId = state.requestId;
      const feeRequestId = state.feeRequestId;
      const inputRevision = state.inputRevision;
      const started = await this.call("overseas_costing.api.calculate.start_cost_trial_ai_review", {
        batch_name: batchName,
        version_name: versionName || null,
        edit_token: this.detailState.editToken,
        expected_modified: this.detailState.expectedModified,
        force: 0,
        reuse_only: 1,
      });
      if (!started?.ok) throw new Error(started?.message || "读取已保存分摊口径失败。");
      if (!isCurrent() || state.requestId !== requestId || state.feeRequestId !== feeRequestId
        || state.inputRevision !== inputRevision) return false;
      state.costTrialAI = {
        runId: started.run_id,
        status: started.status,
        progressRevision: Number(started.progress_revision || 0),
        requestId,
        feeRequestId,
        inputRevision,
        actionStage: "reuse",
        dialogMode: "adjust",
        aiCandidateCount: 0,
      };
      await this.pollCostTrialAI(state, batchName, started.run_id);
      if (!isCurrent() || state.costTrialAI?.status !== "READY") return false;
      state.costTrialAI.actionStage = "";
      const mode = (state.costTrialAI.draft?.fee_suggestions || []).some((row) => row.blocked) ? "repair" : "adjust";
      state.costTrialAI.dialogMode = mode;
      state.costTrialAI.selections = (state.costTrialAI.draft?.default_selections || []).map((row) => ({ ...row }));
      this.openCostTrialAIReviewDialog({ mode });
      return true;
    } finally {
      state.previewRunning = false;
      this.updateMaterialFeeWriteControls(state);
    }
  }

  async retryCostTrialAI() {
    const state = this.ensureMaterialFeeState();
    if (state.costTrialRestarting) return false;
    state.costTrialRestarting = true;
    this.updateMaterialFeeWriteControls(state);
    try {
      const trial = state.costTrialAI;
      const batchName = this.detailState.batchName;
      const versionName = this.detailState.versionName;
      const requestId = state.requestId;
      const feeRequestId = state.feeRequestId;
      const inputRevision = state.inputRevision;
      const isCurrent = () => this.materialFeeState === state
        && this.detailState.batchName === batchName
        && this.detailState.versionName === versionName
        && this.detailState.tab === "documents"
        && state.requestId === requestId
        && state.feeRequestId === feeRequestId
        && state.inputRevision === inputRevision;
      if (trial?.runId) await this.call("overseas_costing.api.calculate.discard_cost_trial_ai_review", {
        batch_name: batchName, run_id: trial.runId,
      });
      state.costTrialDialog?.hide?.();
      state.costTrialDialog = null;
      state.costTrialAI = null;
      if (!isCurrent()) return false;
      const started = await this.call("overseas_costing.api.calculate.start_cost_trial_ai_review", {
        batch_name: batchName,
        version_name: versionName || null,
        edit_token: this.detailState.editToken,
        expected_modified: this.detailState.expectedModified,
        force: 1,
        reuse_only: 0,
      });
      if (!started?.ok) throw new Error(started?.message || "AI 试算任务重试失败。");
      if (!isCurrent()) return false;
      state.costTrialAI = {
        runId: started.run_id,
        status: started.status,
        progressRevision: Number(started.progress_revision || 0),
        requestId,
        feeRequestId,
        inputRevision,
        actionStage: ["QUEUED", "RUNNING"].includes(String(started.status || "")) ? "ai" : "reuse",
        aiCandidateCount: Number(started.ai_candidate_count || 0),
        dialogMode: "adjust",
      };
      await this.pollCostTrialAI(state, batchName, started.run_id);
      if (state.costTrialAI?.status === "READY") {
        state.costTrialAI.actionStage = "";
        state.costTrialAI.selections = (state.costTrialAI.draft?.default_selections || []).map((row) => ({ ...row }));
        this.openCostTrialAIReviewDialog({ mode: "adjust" });
      }
      return state.costTrialAI?.status === "READY";
    } finally {
      state.costTrialRestarting = false;
      this.updateCostTrialAIPrimaryAction();
      this.updateMaterialFeeWriteControls(state);
    }
  }

  acceptSavedComprehensiveCost(result, batchName, { versionName = result.version_name, preserveDirty = false } = {}) {
    const summary = result.summary_snapshot || {};
    const updates = { status: "Calculated", modified: result.batch_modified,
      estimated_total_cost_rmb: result.summary?.total_cost_rmb, summary_snapshot: summary,
      item_count: summary.item_count, total_goods_value: summary.total_goods_value };
    const index = (this.batches || []).findIndex((row) => row.name === batchName);
    const hasNewerRevision = (modified) => Boolean(modified && result.batch_modified && String(modified) > String(result.batch_modified));
    if (index >= 0 && (!versionName || !this.batches[index].current_version || this.batches[index].current_version === versionName)
      && !hasNewerRevision(this.batches[index].modified)) {
      this.batches[index] = { ...this.batches[index], ...updates };
    }
    if (this.detailState.batchName === batchName && (!versionName || this.detailState.versionName === versionName)
      && !hasNewerRevision(this.detailState.expectedModified)) {
      this.detailState.header = { ...(this.detailState.header || {}), ...updates };
      this.acceptBatchWriteRevision(result.batch_modified);
      this.detailState.skuRequestId = (this.detailState.skuRequestId || 0) + 1;
      this.detailState.skuResult = null;
      if (!preserveDirty) this.detailState.dirty = false;
      if (this.materialFeeState?.batchName === batchName) this.materialFeeState.preview = result;
    }
    if (this.resetBatchResultPreview) this.resetBatchResultPreview({ clearCache: true, render: false });
  }

  materialFeeExclusionReason(fee) {
    return {
      AMOUNT_MISSING: "金额尚未填写",
      FX_RATE_MISSING: "缺少该币种的批次汇率，请先补充汇率",
      CURRENCY_UNSUPPORTED: "币种暂不支持，请选择人民币、比索或美金",
      FEE_AMOUNT_INVALID: "金额无效，请填写不小于 0 的有效金额",
      AMOUNT_STATUS_INVALID: "金额状态无效，请重新确认",
      DUPLICATE_LOGICAL_FEE: "费用重复，请核对并停用重复记录",
      ALLOCATION_BASIS_INCOMPLETE: "体积或重量资料不完整，采购货值也未齐全，请补充资料后重试",
      ALLOCATION_DENOMINATOR_ZERO: "体积或重量尚未提供，采购货值也未齐全，请补充资料后重试",
      FEE_SCOPE_EMPTY: "没有可分摊的物料，请检查物料与适用范围",
      DIRECT_ITEM_SCOPE_INVALID: "直接费用需要指定一条有效物料",
      FEE_SCOPE_INVALID: "费用适用范围无效，请重新选择",
      STABLE_ITEM_KEY_REQUIRED: "物料标识缺失，请先核对物料数据",
      STABLE_ITEM_KEY_DUPLICATED: "物料标识重复，请先核对物料数据",
    }[fee.reason_code] || "费用资料尚不完整，请核对后重试";
  }

  renderMaterialFeeTodos() {
    const state = this.ensureMaterialFeeState();
    const todos = [];
    (state.fees?.fees || state.fees?.items || []).forEach((fee) => (fee.todos || []).forEach((todo) => todos.push(`${fee.expense_category || fee.logical_fee_key}：${todo.label}`)));
    (state.materials?.items || []).forEach((item) => (item.requirements?.missing_fields || []).forEach((field) => todos.push(ocTranslate("{0}：缺少 {1}", [item.material_code || item.name, this.materialFeeGridColumns().find((column) => column.field === field)?.label || field]))));
    (state.preview?.incomplete_reasons || []).forEach((row) => {
      const text = row.message || row.reason_code;
      if (text && !todos.includes(text)) todos.push(text);
    });
    return `<details class="ocw-mf-section ocw-mf-todos"><summary><span><strong>${ocTranslate("详细待办")}</strong><em>${ocTranslate("{0} 项", [todos.length])}</em></span><small>${ocTranslate("默认收起，需要时展开查看")}</small></summary><div>${todos.length ? `<ul>${todos.slice(0, 100).map((todo) => `<li>${this.escape(todo)}</li>`).join("")}</ul>` : `<div class="ocw-detail-empty"><strong>${ocTranslate("当前没有待办")}</strong></div>`}</div></details>`;
  }

  materialFeeSavedCostPreview() {
    // Only persisted results can be shared with the overview and SKU details.
    return this.detailState?.header?.summary_snapshot?.comprehensive_cost
      || (this.materialFeeState?.preview?.saved === true && this.materialFeeState.preview.read_only === false ? this.materialFeeState.preview : null);
  }

  materialFeeTrialDecisionSummary(preview = {}) {
    const choices = preview.trial_review?.fee_choices || [];
    const counts = choices.reduce((result, row) => {
      const source = String(row.decision_source || "");
      result[source] = Number(result[source] || 0) + 1;
      return result;
    }, {});
    return [["REUSED", "沿用上次"], ["AI", "AI 判断"], ["SYSTEM_FALLBACK", "系统兜底"], ["EVIDENCE", "凭证优先"], ["USER_OVERRIDE", "用户调整"]]
      .filter(([source]) => Number(counts[source] || 0) > 0)
      .map(([source, label]) => `${ocTranslate(label)} ${Number(counts[source] || 0)}`).join(" · ");
  }

  renderMaterialFeeCostTable() {
    const state = this.ensureMaterialFeeState();
    const header = this.detailState.header || {};
    const preview = this.materialFeeSavedCostPreview();
    const legacyTotal = header.summary_snapshot?.total_cost_rmb;
    const hasLegacyTotal = legacyTotal !== undefined && legacyTotal !== null && legacyTotal !== "" && Number.isFinite(Number(legacyTotal));
    const sourcePending = state.materials?.calculation_stale || state.fees?.summary?.source_pending;
    const staleCost = (header.status === "Dirty" || sourcePending) && Boolean(preview || hasLegacyTotal);
    const decisionSummary = preview ? this.materialFeeTrialDecisionSummary(preview) : "";
    const packingGroupBlocked = this.hasBlockingPackingGroups(state);
    const trialDisabled = this.isMaterialFeeCalculationBusy(state) || state.aiFill?.applying || packingGroupBlocked;
    const trialActionLabel = preview || hasLegacyTotal ? ocTranslate("重新试算") : ocTranslate("开始试算");
    const sectionTitle = `<div class="ocw-mf-section-title">
      <div><span>03</span><h3>${ocTranslate("SKU 综合单价试算")}</h3><p>${ocTranslate("开始试算后保存当前计算结果，并同步总览与 SKU 明细；确认和 ERP 推送需单独操作。")}</p><p>${ocTranslate("系统优先沿用已有口径，仅在必要时请求 AI；逐 SKU 金额由服务端规则引擎计算，可信关税凭证明细优先。")}</p></div>
      <div class="ocw-mf-cost-actions"><span class="ocw-mf-completeness ${!staleCost && preview?.summary?.is_complete ? "is-complete" : "is-partial"}">${staleCost ? ocTranslate("待重新试算") : preview ? (preview.summary?.is_complete ? ocTranslate("完整成本") : ocTranslate("非完整成本")) : (hasLegacyTotal ? ocTranslate("待重新试算") : ocTranslate("尚未试算"))}</span><button class="ocw-primary-btn" type="button" data-action="detail-primary" data-primary-action="recalculate" ${trialDisabled ? "disabled" : ""}>${trialActionLabel}</button>${preview ? `<button class="ocw-outline-btn" type="button" data-action="mf-adjust-cost" ${trialDisabled ? "disabled" : ""}>${ocTranslate("调整分摊")}</button>` : ""}</div>
    </div>`;
    if (!preview) {
      return `<section class="ocw-mf-section ocw-mf-cost-section">${sectionTitle}
        ${packingGroupBlocked ? `<p class="ocw-mf-trial-note">${ocTranslate("装箱组成员已变化，请先重新确认装箱组，再开始试算。")}</p>` : ""}
        ${hasLegacyTotal ? `<div class="ocw-mf-cost-summary"><div class="ocw-mf-cost-total"><span>${ocTranslate("上次已保存成本 · 待重新试算")}</span><strong>RMB ${this.escape(Number(legacyTotal).toFixed(2))}</strong></div></div>` : ""}
        <div class="ocw-detail-empty"><strong>${hasLegacyTotal ? ocTranslate("当前费用尚未汇总到已保存成本") : ocTranslate("尚未保存试算结果")}</strong><p>${ocTranslate("点击“{0}”，按当前物料和费用更新总览、SKU 明细及本区结果。", [trialActionLabel])}</p></div>
      </section>`;
    }
    const summary = preview.summary || {};
    const items = preview.items || [];
    const hasUnsaved = Object.keys(state.feeDrafts).length || Object.keys(state.materialDrafts).length || Object.keys(state.materialSaveErrors).length;
    const allocationNotes = (preview.included_fees || []).map((fee) => {
      const method = this.materialFeeBasisLabel(fee.allocation_basis);
      const explanation = fee.fallback_reason ? ocTranslate("{0}数据不完整，已自动按采购货值分摊", [this.materialFeeBasisLabel(fee.preferred_basis)]) : method;
      return `<li><strong>${this.escape(ocTranslate(fee.expense_category || fee.fee_key || "费用"))}</strong><span>${this.escape(explanation)}</span><em>RMB ${this.escape(fee.amount_rmb || "0.00")}</em></li>`;
    }).join("");
    return `<section class="ocw-mf-section ocw-mf-cost-section">
      ${sectionTitle}
      ${staleCost ? `<p class="ocw-mf-trial-note">${ocTranslate("资料已更新，请点击重新试算。以下为历史结果。")}</p><details class="ocw-mf-cost-history"><summary>${ocTranslate("查看上次试算")}</summary>` : ""}
      ${this.renderShipmentProjectSummary(preview.project_summary || [])}
      <div class="ocw-mf-cost-summary">
        <div class="ocw-mf-cost-total"><span>${hasUnsaved ? ocTranslate("上次试算 · 有修改待保存") : header.status === "Dirty" ? ocTranslate("上次试算 · 结果待更新") : ocTranslate("当前试算总成本")}</span><strong>RMB ${this.escape(summary.total_cost_rmb || "0.00")}</strong></div>
        <dl><div><dt>${ocTranslate("本次发货货值")}</dt><dd>${this.escape(summary.purchase_goods_value_rmb || "0.00")}</dd></div><div><dt>${ocTranslate("直接费用")}</dt><dd>${this.escape(summary.direct_fees_rmb || "0.00")}</dd></div><div><dt>${ocTranslate("分摊费用")}</dt><dd>${this.escape(summary.allocated_fees_rmb || "0.00")}</dd></div><div><dt>${ocTranslate("已计入费用")}</dt><dd>${ocTranslate("{0} 笔", [Number(summary.included_fee_count || 0)])}</dd></div></dl>
      </div>
      ${decisionSummary ? `<p class="ocw-mf-trial-note">${ocTranslate("本次口径：{0}", [this.escape(decisionSummary)])}</p>` : ""}
      <p class="ocw-mf-trial-note">${ocTranslate("试算不生成正式成本版本；凭证待补单独保留，已知金额可先参与计算。{0}", [summary.estimated_fee_count ? ocTranslate("含 {0} 笔暂估费用，需后续核实。", [Number(summary.estimated_fee_count)]) : ""])}</p>
      <div class="ocw-mf-cost-scroll"><table><thead><tr><th>${ocTranslate("物料编码")}</th><th>${ocTranslate("物料名称")}</th><th>${ocTranslate("本次发货货值")}</th><th>${ocTranslate("直接费用")}</th><th>${ocTranslate("分摊费用")}</th><th>${ocTranslate("综合成本")}</th><th>${ocTranslate("单价（RMB）")}</th><th>${ocTranslate("综合单价（RMB）")}</th></tr></thead><tbody>${items.length ? items.map((item) => `<tr><td>${this.escape(item.material_code || "--")}</td><td>${this.escape(item.product_name || "--")}${Number(item.shipping_quantity_difference) < 0 ? `<small class="ocw-mf-warning">${ocTranslate("少发 {0} {1}", [this.escape(String(-Number(item.shipping_quantity_difference))), this.escape(item.shipping_unit_cost?.uom || "")])}</small>` : ""}</td><td>${this.escape(item.goods_value_rmb || "0.00")}</td><td>${this.escape(item.direct_fees_rmb || "0.00")}</td><td>${this.escape(item.allocated_fees_rmb || "0.00")}</td><td><strong>${this.escape(item.total_cost_rmb || "0.00")}</strong></td><td>${item.shipping_unit_price ? `${this.escape(Number(item.shipping_unit_price.amount_rmb).toFixed(2))} / ${this.escape(item.shipping_unit_price.uom)}` : item.shipping_unit_cost ? `<span class="ocw-mf-muted">${item.valuation_source?.error ? ocTranslate("货值待补") : ocTranslate("重新试算后显示")}</span>` : `<span class="ocw-mf-muted">${ocTranslate("待补发货数量/单位")}</span>`}</td><td>${item.shipping_unit_cost ? `<strong>${this.escape(Number(item.shipping_unit_cost.amount_rmb).toFixed(2))} / ${this.escape(item.shipping_unit_cost.uom)}</strong>` : `<span class="ocw-mf-muted">${ocTranslate("待补发货数量/单位")}</span>`}</td></tr>`).join("") : `<tr><td colspan="8">${ocTranslate("暂无可试算物料")}</td></tr>`}</tbody></table></div>
      ${preview.excluded_fees?.length ? `<div class="ocw-mf-excluded"><strong>${ocTranslate("未计入费用")}</strong>${preview.excluded_fees.map((fee) => `<span>${this.escape(ocTranslate(fee.expense_category || fee.fee_key || "费用"))} · ${this.escape(ocTranslate(this.materialFeeExclusionReason(fee)))}</span>`).join("")}</div>` : ""}
      ${(preview.incomplete_reasons || []).filter((reason) => reason.item_key || reason.reason_code === "MATERIAL_ITEMS_REQUIRED").length ? `<div class="ocw-mf-excluded"><strong>${ocTranslate("物料待补")}</strong>${preview.incomplete_reasons.filter((reason) => reason.item_key || reason.reason_code === "MATERIAL_ITEMS_REQUIRED").map((reason) => `<span>${this.escape(items.find((item) => item.stable_line_key === reason.item_key)?.material_code || "")} ${this.escape(reason.message || ocTranslate("请补充物料资料"))}</span>`).join("")}</div>` : ""}
      <details class="ocw-mf-allocation-notes"><summary>${ocTranslate("查看系统分摊说明")}</summary><p>${ocTranslate("已确认的项目规则优先按项目毛重，再按项目内发货行毛重分摊，缺项时不会切换依据。其他费用：海运与港杂优先按体积，空运与快递按计费重，配送按毛重，清关与税费按采购货值。适用物料的体积或重量不齐全时，整笔费用自动按完整的采购货值分摊。")}</p><ul>${allocationNotes || `<li>${ocTranslate("本次暂无已计入费用。")}</li>`}</ul></details>
      ${staleCost ? "</details>" : ""}
    </section>`;
  }

  openMaterialXlsxUploader() {
    const batch = this.getDetailBatch();
    if (!frappe.ui.FileUploader) return this.showPendingFeature("当前无法打开上传器，请刷新后重试。");
    this.ensureEditSession().then((ready) => {
      if (!ready) return;
      new frappe.ui.FileUploader({
        allow_multiple: false,
        restrictions: { allowed_file_types: [".xlsx", ".xlsm", ".xls", ".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".doc", ".docx"], max_file_size: 20 * 1024 * 1024 },
        on_success: (fileDoc) => {
          const uploaded = Array.isArray(fileDoc) ? fileDoc[0] : fileDoc;
          const fileName = String(uploaded?.file_name || uploaded?.name || "");
          if (fileName.toLowerCase().endsWith(".xls")) {
            this.showPendingFeature("暂不支持旧版 .xls，请先在 Excel 中另存为 .xlsx 后上传。");
            return;
          }
          this.registerManualDocumentAttachment(batch, this.detailDocumentAdapter(), this.detectManualDocumentLogisticsType(batch), {
            code: "material_packing_document",
            label: ocTranslate("本地上传装箱单"),
            attachmentType: "Packing List",
            required: false,
          }, uploaded).then((registered) => {
            const attachment = registered?.attachment?.name;
            if (!attachment) return;
            this.openWikiMaterialImportDialog({ sourceTab: "local" }).then((sourceDialog) => {
              if ([".xlsx", ".xlsm"].some((suffix) => fileName.toLowerCase().endsWith(suffix))) {
                this.previewMaterialAttachmentSource(sourceDialog, attachment).catch((error) => this.showWikiMaterialSourceError(sourceDialog, error));
              } else {
                frappe.show_alert({ message: ocTranslate("装箱资料已加入，AI 将在后台分析"), indicator: "green" });
                this.restartMaterialAIForChangedSources();
              }
            }).catch((error) => this.showError(error));
          }).catch((error) => this.showError(error));
        },
      });
      [0, 100, 300].forEach((delay) => window.setTimeout(() => this.localizeFrappeFileUploader("本地上传装箱单"), delay));
    }).catch((error) => this.showError(error));
  }

  async openWikiMaterialImportDialog(options = {}) {
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("获取装箱资料"),
      fields: [{
        fieldtype: "HTML",
        fieldname: "wiki_sources",
        options: `<div class="ocw-mf-wiki-source-shell" data-area="mf-wiki-sources"><div class="ocw-detail-empty"><strong>${ocTranslate("正在读取装箱计划表")}</strong></div></div>`,
      }],
    });
    dialog.wikiMaterialSelectedSource = "";
    dialog.materialBatchName = this.detailState.batchName;
    dialog.materialVersionName = this.detailState.versionName;
    dialog.materialSourceTab = options.sourceTab || "wiki";
    dialog.materialAttachmentSources = [];
    dialog.materialAttachmentsLoaded = false;
    dialog.wikiMaterialWorkbooks = [];
    dialog.wikiMaterialBusy = "";
    dialog.wikiMaterialProgress = "";
    dialog.wikiMaterialOperationError = "";
    dialog.wikiMaterialClosed = false;
    dialog.wikiMaterialOpeningPreview = false;
    dialog.show();
    dialog.$wrapper.addClass("ocw-mf-dialog ocw-mf-wiki-dialog");
    dialog.$wrapper.on("hide.bs.modal.ocwMfWiki", () => {
      if (!dialog.wikiMaterialOpeningPreview) dialog.wikiMaterialClosed = true;
    });
    dialog.$wrapper
      .off(".ocwMfWiki")
      .on("click.ocwMfWiki", "[data-mf-source-tab]", (event) => {
        if (dialog.wikiMaterialBusy) return;
        dialog.materialSourceTab = $(event.currentTarget).attr("data-mf-source-tab");
        this.renderWikiMaterialSources(dialog);
        if (dialog.materialSourceTab !== "wiki" && !dialog.materialAttachmentsLoaded) {
          this.loadMaterialAttachmentSources(dialog)
            .catch((error) => this.showWikiMaterialSourceError(dialog, error));
        }
      })
      .on("click.ocwMfWiki", "[data-mf-attachment-source]", (event) => {
        const $button = $(event.currentTarget);
        this.previewMaterialAttachmentSource(dialog, $button.attr("data-mf-attachment-source"), $button.attr("data-sheet-name") || "")
          .catch((error) => this.showWikiMaterialSourceError(dialog, error));
      })
      .on("click.ocwMfWiki", "[data-action='mf-source-upload']", () => {
        dialog.hide(); this.openMaterialXlsxUploader();
      })
      .on("click.ocwMfWiki", "[data-action='mf-source-reload']", () => {
        this.loadMaterialAttachmentSources(dialog, { force: true })
          .catch((error) => this.showWikiMaterialSourceError(dialog, error));
      })
      .on("click.ocwMfWiki", "[data-mf-wiki-source]", (event) => {
        dialog.wikiMaterialSelectedSource = $(event.currentTarget).attr("data-source-id") || "";
        this.renderWikiMaterialSources(dialog);
      })
      .on("input.ocwMfWiki", "[data-action='mf-wiki-filter']", (event) => {
        const keyword = String($(event.currentTarget).val() || "").trim().toLowerCase();
        dialog.$wrapper.find("[data-mf-wiki-card]").each((_, node) => {
          $(node).toggle(!keyword || String($(node).attr("data-search") || "").includes(keyword));
        });
      })
      .on("click.ocwMfWiki", "[data-action='mf-wiki-refresh-selected']", () => {
        this.refreshSelectedWikiMaterialSource(dialog)
          .catch((error) => this.showWikiMaterialSourceError(dialog, error));
      })
      .on("click.ocwMfWiki", "[data-action='mf-wiki-preview-card']", (event) => {
        const $button = $(event.currentTarget);
        const sourceId = String($button.attr("data-source-id") || "");
        if (!sourceId || dialog.wikiMaterialBusy) return;
        dialog.wikiMaterialSelectedSource = sourceId;
        dialog.wikiMaterialBusy = `preview:${sourceId}`;
        this.renderWikiMaterialSources(dialog);
        this.previewWikiMaterialImport(dialog, sourceId)
          .catch((error) => this.showWikiMaterialSourceError(dialog, error))
          .finally(() => {
            dialog.wikiMaterialBusy = "";
            if (!dialog.wikiMaterialClosed && !dialog.wikiMaterialOpeningPreview) this.renderWikiMaterialSources(dialog);
          });
      })
      .on("click.ocwMfWiki", "[data-action='mf-wiki-cancel']", () => {
        dialog.wikiMaterialClosed = true;
        dialog.hide();
      });
    await this.loadWikiMaterialSources(dialog);
    if (dialog.materialSourceTab !== "wiki") await this.loadMaterialAttachmentSources(dialog);
    return dialog;
  }

  async loadWikiMaterialSources(dialog, { preserveOnError = false } = {}) {
    const result = await this.call("overseas_costing.api.packing_api.list_packing_sheet_catalog", {
      batch_name: this.detailState.batchName,
    }, true);
    const nextWorkbooks = result?.wiki_workbooks || [];
    const previousFingerprint = dialog.sourceContext?.fingerprint || "";
    dialog.sourceContext = result?.source_context || {};
    const sameSource = previousFingerprint === (dialog.sourceContext.fingerprint || "");
    if (!sameSource) {
      dialog.wikiMaterialSelectedSource = "";
      dialog.wikiMaterialSources = [];
      dialog.wikiMaterialWorkbooks = [];
      dialog.wikiMaterialPreview = null;
    }
    if (
      preserveOnError
      && sameSource
      && result?.sync_error
      && !nextWorkbooks.length
      && (dialog.wikiMaterialSources || []).length
    ) {
      dialog.wikiMaterialError = result.sync_error;
      if (!dialog.wikiMaterialClosed) this.renderWikiMaterialSources(dialog);
      return false;
    }
    dialog.wikiMaterialWorkbooks = nextWorkbooks;
    const sheets = dialog.wikiMaterialWorkbooks.flatMap((workbook) =>
      (workbook.sheets || []).map((sheet) => ({ ...sheet, workbook_id: workbook.workbook_id, workbook_label: workbook.label }))
    );
    sheets.sort((left, right) => {
      const leftDate = String(left.business_date || left.source_updated_at || left.snapshot_updated_at || "");
      const rightDate = String(right.business_date || right.source_updated_at || right.snapshot_updated_at || "");
      return rightDate.localeCompare(leftDate) || String(left.source_label || "").localeCompare(String(right.source_label || ""));
    });
    dialog.wikiMaterialSources = sheets;
    dialog.wikiMaterialError = result?.sync_error || "";
    if (!sheets.some((sheet) => String(sheet.source_id) === String(dialog.wikiMaterialSelectedSource))) {
      const recommended = sheets.find((sheet) => sheet.auto_select_recommended) || sheets.find((sheet) => sheet.is_recommended) || sheets[0];
      dialog.wikiMaterialSelectedSource = recommended?.source_id || "";
    }
    if (!dialog.wikiMaterialClosed) this.renderWikiMaterialSources(dialog);
    return true;
  }

  async loadMaterialAttachmentSources(dialog, { force = false } = {}) {
    if (dialog.materialAttachmentsLoaded && !force) return true;
    const result = await this.call("overseas_costing.api.packing_api.list_packing_attachment_sources", {
      batch_name: this.detailState.batchName,
    }, true);
    const nextContext = result?.source_context || {};
    if (
      dialog.sourceContext?.fingerprint
      && nextContext.fingerprint
      && dialog.sourceContext.fingerprint !== nextContext.fingerprint
    ) throw new Error(ocTranslate("当前资料来源已变化，请关闭后重新获取资料。"));
    dialog.sourceContext = nextContext;
    // 「本地上传装箱单」页签同时列出本地手工上传件与国际物流审批里的装箱单附件：
    // 服务端 list_packing_sources 早就两者都给（manual_attachments / approval_sources），
    // 早期只取前者，导致国际物流的装箱单 Excel 在这个弹窗里看不到也选不了。
    // 只保留服务端标为可导入 Excel 的项（supported_for_material_import），
    // 审批正文/评论等非表格来源不进这个页签。
    const materialImportableSources = (rows) => (rows || []).filter((row) =>
      row?.supported_for_material_import !== false
      && ["manual_attachment", "approval_attachment"].includes(String(row?.source_kind || ""))
    );
    dialog.materialAttachmentSources = [
      ...materialImportableSources(result?.manual_attachments),
      ...materialImportableSources(result?.approval_sources),
    ].filter((row, index, rows) => rows.findIndex((candidate) => candidate.source_id === row.source_id) === index);
    dialog.materialAttachmentsLoaded = true;
    dialog.wikiMaterialOperationError = "";
    if (!dialog.wikiMaterialClosed) this.renderWikiMaterialSources(dialog);
    return true;
  }

  renderWikiMaterialSources(dialog) {
    const $target = dialog.$wrapper.find("[data-area='mf-wiki-sources']");
    if (dialog.materialSourceTab && dialog.materialSourceTab !== "wiki") {
      $target.html(this.renderMaterialSourceTabs(dialog) + this.renderMaterialAttachmentSources(dialog));
      return;
    }
    const sheets = dialog.wikiMaterialSources || [];
    const selectedId = String(dialog.wikiMaterialSelectedSource || "");
    const busy = String(dialog.wikiMaterialBusy || "");
    const cards = sheets.map((sheet) => {
      const selected = String(sheet.source_id || "") === selectedId;
      const sheetId = String(sheet.source_id || "").split(":").slice(1).join(":");
      const confidence = { high: "高置信度", medium: "中置信度", low: "低置信度" }[sheet.recommendation_confidence] || "待确认";
      const updatedAt = sheet.last_success_at || sheet.cache_updated_at || sheet.snapshot_updated_at;
      const cacheStatus = String(sheet.cache_status || "missing");
      const status = cacheStatus === "ready"
        ? ocTranslate("已同步 {0}", [this.formatDateTimeMinute(updatedAt) || updatedAt || ""])
        : cacheStatus === "stale" ? ocTranslate("缓存已过期 · 同步于 {0}", [this.formatDateTimeMinute(updatedAt) || updatedAt || "未知"])
        : cacheStatus === "error" ? ocTranslate("同步失败{0}", [sheet.sync_error ? `：${sheet.sync_error}` : ""])
        : cacheStatus === "unavailable" ? "Sheet 已停用" : "尚未缓存";
      const previewDisabled = busy || !sheet.content_hash || ["error", "missing", "unavailable"].includes(cacheStatus);
      return `<article class="ocw-mf-wiki-card ${selected ? "selected" : ""} ${sheet.is_recommended ? "recommended" : ""}" data-mf-wiki-card="1" data-search="${this.escape(String(sheet.source_label || "").toLowerCase())}">
        <button type="button" data-mf-wiki-source="1" data-source-id="${this.escape(sheet.source_id || "")}">
          <span>${sheet.is_recommended ? ocTranslate("系统推荐") : "SHEET"}</span>
          <strong>${this.escape(sheet.source_label || sheet.source_id || ocTranslate("未命名 Sheet"))}</strong>
          <small>${ocTranslate("{0} · 装箱日期 {1}", [this.escape(sheet.workbook_label || ocTranslate("装箱计划表")), this.escape(sheet.business_date || ocTranslate("未识别"))])}</small>
        </button>
        ${sheet.is_recommended ? `<div class="ocw-mf-wiki-reasons"><b>${ocTranslate("系统推荐 · {0}", [this.escape(confidence)])}</b>${(sheet.recommendation_reasons || []).slice(0, 3).map((reason) => `<span>${this.escape(reason)}</span>`).join("")}</div>` : ""}
        <div class="ocw-mf-wiki-card-meta"><span>${this.escape(status)}</span><button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="mf-wiki-preview-card" data-source-id="${this.escape(sheet.source_id || "")}" data-workbook-id="${this.escape(sheet.workbook_id || "")}" data-sheet-id="${this.escape(sheetId)}" ${previewDisabled ? "disabled" : ""}>${busy === `preview:${sheet.source_id}` ? ocTranslate("正在打开…") : ocTranslate("预览")}</button></div>
      </article>`;
    }).join("");
    $target.html(`
      ${this.renderMaterialSourceTabs(dialog)}
      <div class="ocw-mf-wiki-toolbar">
        <label class="ocw-mf-wiki-search"><span>${ocTranslate("查找 Sheet")}</span><input type="search" data-action="mf-wiki-filter" placeholder="${ocTranslate('输入装箱单、日期或品类')}"></label>
        <div class="ocw-mf-wiki-toolbar-actions">
          <button class="ocw-outline-btn" type="button" data-action="mf-wiki-refresh-selected" ${busy || !selectedId ? "disabled" : ""}>${busy === `sheet:${selectedId}` ? ocTranslate("正在刷新…") : ocTranslate("刷新所选 Sheet")}</button>
        </div>
      </div>
      ${dialog.wikiMaterialError ? `<div class="ocw-mf-wiki-error">${this.escape(dialog.wikiMaterialError)}</div>` : ""}
      ${dialog.wikiMaterialOperationError ? `<div class="ocw-mf-wiki-error">${this.escape(dialog.wikiMaterialOperationError)}</div>` : ""}
      <div class="ocw-mf-wiki-list">${cards || `<div class="ocw-detail-empty"><strong>${ocTranslate("暂无可用 Sheet")}</strong><span>${ocTranslate("请先确认服务端装箱计划表同步状态。")}</span></div>`}</div>
      <footer class="ocw-mf-import-sticky-footer"><span>${selectedId ? ocTranslate("已选：{0}", [this.escape((sheets.find((sheet) => String(sheet.source_id) === selectedId) || {}).source_label || selectedId)]) : ocTranslate("请选择一个 Sheet")}</span><div><button class="ocw-outline-btn" type="button" data-action="mf-wiki-cancel">${ocTranslate("取消")}</button></div></footer>
    `);
  }

  renderMaterialSourceTabs(dialog) {
    return `<div class="ocw-packing-source-tabs">${[["wiki", "装箱计划表"], ["local", "本地上传装箱单"]].map(([key,label]) => `<button type="button" data-mf-source-tab="${key}" class="${(dialog.materialSourceTab || "wiki") === key ? "active" : ""}" ${dialog.wikiMaterialBusy ? "disabled" : ""}>${ocTranslate(label)}</button>`).join("")}</div>`;
  }

  renderMaterialAttachmentSources(dialog) {
    const bound = dialog.sourceContext?.root_kind === "expense";
    // 不再按 source_kind 过滤：本地手工上传件与国际物流 OA 附件都在这里列出，
    // 来源已在 loadMaterialAttachmentSources 收敛为「可导入 Excel」。
    const rows = dialog.materialAttachmentSources || [];
    const hasManual = rows.some((row) => row.source_kind === "manual_attachment");
    const hasApproval = rows.some((row) => row.source_kind === "approval_attachment");
    const cards = rows.map((row) => {
      const sheets = row.sheets?.length ? row.sheets : [""];
      const semanticOnly = row.supported_for_material_import === false;
      // 服务端标 excluded 的行（身份不完整/审批已否定的重复登记行）不可选：给出原因而不是
      // 渲染出一个点下去必然报「来源校验失败」的按钮。
      const blocked = Boolean(row.excluded);
      const status = blocked ? (row.exclude_reason || "该来源当前不可用。")
        : semanticOnly ? (row.available ? "可供 AI 识别" : "选择 AI 填充后自动获取并识别")
        : row.available ? "可预览"
        : ({ archived: "已归档，选择后自动获取", pending: "等待归档，可重试", manual_required: "需从钉钉下载后手动上传" }[row.archive_status] || "选择后获取附件");
      // 国际物流附件来自钉钉审批，标注来源便于与本地手工上传件区分。
      const fromApproval = row.source_kind === "approval_attachment";
      const badge = semanticOnly ? `<span>${ocTranslate("AI 资料")}</span>` : fromApproval ? `<span>${ocTranslate("国际物流 OA 附件")}</span>` : `<span>${ocTranslate("本地文件")}</span>`;
      const actions = blocked ? ""
        : sheets.map((sheet) => `<button class="ocw-outline-btn" type="button" data-mf-attachment-source="${this.escape(row.source_id)}" data-sheet-name="${this.escape(sheet)}" ${dialog.wikiMaterialBusy ? "disabled" : ""}>${dialog.wikiMaterialBusy === `attachment:${row.source_id}` ? ocTranslate("正在获取…") : sheet ? ocTranslate("预览 {0}", [this.escape(sheet)]) : row.available ? ocTranslate("预览") : ocTranslate("获取并预览")}</button>`).join("");
      return `<article class="ocw-mf-wiki-card${blocked ? " ocw-mf-wiki-card-blocked" : ""}"><strong>${this.escape(row.source_label || row.source_id)}</strong><small>${this.escape(status)}</small><div class="ocw-mf-wiki-card-meta">${badge}${actions}</div></article>`;
    }).join("");
    const emptyHint = hasManual || hasApproval
      ? "还没有可导入的装箱单，可用上方「本地上传装箱单」补充。"
      : "还没有可导入的装箱单。本地文件可用上方「本地上传装箱单」补充；国际物流的装箱单附件会随审批同步自动出现。";
    return `
      <div class="ocw-mf-wiki-toolbar">
        <span>${bound ? ocTranslate("当前资料来源：采购支出。正文、评论和附件也会纳入“AI 分析资料”。") : ocTranslate("本地装箱单与国际物流 OA 附件都可直接预览导入；正文和评论由 AI 自动收集。")}</span>
        <div class="ocw-mf-wiki-toolbar-actions">
          <button class="ocw-outline-btn" type="button" data-action="mf-source-reload" ${dialog.wikiMaterialBusy ? "disabled" : ""}>${ocTranslate("刷新来源")}</button>
          <button class="ocw-primary-btn" type="button" data-action="mf-source-upload">${ocTranslate("本地上传装箱单")}</button>
        </div>
      </div>
      ${dialog.wikiMaterialOperationError ? `<div class="ocw-mf-wiki-error">${this.escape(dialog.wikiMaterialOperationError)}</div>` : ""}
      <div class="ocw-mf-wiki-list">${cards || `<div class="ocw-detail-empty"><strong>${ocTranslate("暂无可导入的装箱单")}</strong><span>${this.escape(emptyHint)}</span></div>`}</div>
    `;
  }

  async previewMaterialAttachmentSource(dialog, sourceId, sheetName = "") {
    if (dialog.wikiMaterialBusy) return;
    let source = (dialog.materialAttachmentSources || []).find((row) => row.source_id === sourceId);
    if (!source) throw new Error(ocTranslate("资料来源已变化，请刷新来源列表。"));
    const isCurrent = () => !dialog.wikiMaterialClosed && this.detailState.batchName === dialog.materialBatchName && this.detailState.versionName === dialog.materialVersionName;
    if (!isCurrent()) return;
    dialog.wikiMaterialBusy = `attachment:${sourceId}`;
    dialog.wikiMaterialOperationError = "";
    this.renderWikiMaterialSources(dialog);
    try {
      const attachment = source.attachment_name || source.source_id;
      if (!isCurrent()) return;
      let sheets = source.sheets || [];
      if (!sheets.length) {
        const catalog = await this.call("overseas_costing.api.packing_api.list_packing_attachment_sheets", {
          batch_name: dialog.materialBatchName, source_kind: source.source_kind, source_id: attachment,
        });
        if (!isCurrent()) return;
        if (catalog.source_context?.fingerprint !== dialog.sourceContext?.fingerprint) throw new Error(ocTranslate("当前来源已变化，请关闭并重新获取资料。"));
        sheets = catalog.sheets || [];
        source.sheets = sheets;
      }
      const selectedSheet = sheetName || (sheets.length === 1 ? sheets[0] : "");
      if (!selectedSheet) {
        if (!sheets.length) throw new Error(ocTranslate("未读取到工作表，请检查文件或重新上传。"));
        dialog.wikiMaterialOperationError = "文件包含多个工作表，请选择要导入的工作表。";
        return;
      }
      const preview = await this.call("overseas_costing.api.materials.preview_material_import", {
        batch_name: dialog.materialBatchName, source_kind: source.source_kind,
        source_id: attachment || source.source_id, sheet_name: selectedSheet || null,
      });
      if (!isCurrent()) return;
      if (!preview?.ok) throw new Error(preview?.message || "装箱资料预览失败。");
      dialog.wikiMaterialOpeningPreview = true;
      dialog.hide();
      this.openMaterialImportPreviewDialog(preview);
    } finally {
      dialog.wikiMaterialBusy = "";
      if (isCurrent() && !dialog.wikiMaterialOpeningPreview) this.renderWikiMaterialSources(dialog);
    }
  }

  showWikiMaterialSourceError(dialog, error) {
    dialog.wikiMaterialBusy = "";
    dialog.wikiMaterialProgress = "";
    dialog.wikiMaterialOperationError = this.normalizeErrorMessage(error);
    if (!dialog.wikiMaterialClosed) this.renderWikiMaterialSources(dialog);
  }

  async refreshWikiMaterialSource(dialog, workbookId, sheetId, sourceId) {
    if (!workbookId || !sheetId) throw new Error(ocTranslate("无法识别需要刷新的装箱计划 Sheet。"));
    const requestId = this.packingFlowRequestId();
    await this.call("overseas_costing.api.packing_api.request_packing_sheet_refresh", {
      batch_name: this.detailState.batchName,
      workbook_id: workbookId,
      sheet_id: sheetId,
      request_id: requestId,
    }, false);
    await this.waitPackingRefresh(this.getDetailBatch(), requestId);
    const loaded = await this.loadWikiMaterialSources(dialog, { preserveOnError: true });
    if (!loaded) throw new Error(dialog.wikiMaterialError || "刷新后的 Sheet 目录读取失败。");
  }

  async refreshSelectedWikiMaterialSource(dialog) {
    if (dialog.wikiMaterialBusy) return;
    const sourceId = String(dialog.wikiMaterialSelectedSource || "");
    const selected = (dialog.wikiMaterialSources || []).find((sheet) => String(sheet.source_id || "") === sourceId);
    if (!selected) throw new Error(ocTranslate("请先选择需要刷新的装箱计划 Sheet。"));
    const sheetId = String(selected.sheet_id || sourceId.split(":").slice(1).join(":"));
    dialog.wikiMaterialBusy = `sheet:${sourceId}`;
    dialog.wikiMaterialOperationError = "";
    this.renderWikiMaterialSources(dialog);
    try {
      await this.refreshWikiMaterialSource(dialog, selected.workbook_id, sheetId, sourceId);
      if (dialog.wikiMaterialClosed) return;
      await this.previewWikiMaterialImport(dialog, sourceId);
    } finally {
      dialog.wikiMaterialBusy = "";
      if (!dialog.wikiMaterialClosed && !dialog.wikiMaterialOpeningPreview) {
        this.renderWikiMaterialSources(dialog);
      }
    }
  }

  async previewWikiMaterialImport(sourceDialog, requestedSourceId = "") {
    const sourceId = String(requestedSourceId || sourceDialog.wikiMaterialSelectedSource || "");
    if (!sourceId) throw new Error(ocTranslate("请先选择装箱计划 Sheet。"));
    const result = await this.call("overseas_costing.api.materials.preview_material_import", {
      batch_name: this.detailState.batchName,
      source_kind: "wiki_sheet",
      source_id: sourceId,
      sheet_name: null,
    }, true);
    if (!result || !result.ok) throw new Error(result?.message || "装箱计划表预览失败");
    if (sourceDialog.wikiMaterialClosed) return;
    sourceDialog.wikiMaterialOpeningPreview = true;
    sourceDialog.hide();
    this.openMaterialImportPreviewDialog(result);
  }

  async previewMaterialXlsxImport(attachmentName, sheetName = "") {
    const result = await this.call("overseas_costing.api.materials.preview_material_import", {
      batch_name: this.detailState.batchName,
      source_kind: "manual_xlsx",
      source_id: attachmentName,
      sheet_name: sheetName || null,
    }, true);
    if (!result || !result.ok) throw new Error(result?.message || "Excel 预览失败");
    this.openMaterialImportPreviewDialog(result);
  }

  openMaterialImportPreviewDialog(preview) {
    const rows = preview.rows || [];
    const isWiki = preview.source?.kind === "wiki_sheet";
    const isGrid = isWiki || Boolean(preview.source_grid?.cells?.length);
    const dialog = new frappe.ui.Dialog({
      title: isWiki ? ocTranslate("装箱计划表导入预览") : ocTranslate("Excel 导入预览"),
      fields: [{ fieldtype: "HTML", fieldname: "preview", options: isGrid ? this.renderWikiMaterialImportPreview(preview) : `<div class="ocw-mf-import-preview"><div class="ocw-mf-import-summary"><span>${ocTranslate("可补充")} <strong>${preview.summary?.supplement || 0}</strong></span><span>${ocTranslate("冲突")} <strong>${preview.summary?.conflict || 0}</strong></span><span>${ocTranslate("未匹配")} <strong>${preview.summary?.unmatched || 0}</strong></span><span>${ocTranslate("不会新增未知行")}</span></div><div class="ocw-mf-import-table"><table><thead><tr><th>${ocTranslate("源行")}</th><th>${ocTranslate("分类")}</th><th>${ocTranslate("物料")}</th><th>${ocTranslate("字段变化 / 处理")}</th></tr></thead><tbody>${rows.map((row) => `<tr class="is-${this.escape(row.classification || "unmatched")}"><td>${this.escape(row.source_row || "--")}</td><td>${this.escape({ supplement: "可补充", conflict: "冲突", unmatched: "未匹配", no_change: "无变化" }[row.classification] || row.classification || "--")}</td><td>${this.escape(row.incoming?.material_code || "--")}${row.match_status === "choice_required" ? `<select data-mf-match-row="${this.escape(row.source_row)}"><option value="">${ocTranslate("请选择原行")}</option>${(row.candidates || []).map((candidate) => `<option value="${this.escape(candidate.stable_line_key || "")}">${ocTranslate("行 {0} · {1}", [this.escape(candidate.row_no || "--"), this.escape(candidate.material_code || "--")])}</option>`).join("")}</select>` : ""}</td><td>${(row.changes || []).length ? row.changes.map((change) => `<div><span>${this.escape(change.field)}：${this.escape(this.formatValue(change.old ?? "--"))} → ${this.escape(this.formatValue(change.new ?? "--"))}</span>${change.conflict ? `<select data-mf-conflict-row="${this.escape(row.source_row)}" data-fieldname="${this.escape(change.field)}"><option value="keep_current">${ocTranslate("保留当前值")}</option><option value="use_source">${ocTranslate("采用 Excel")}</option></select>` : ""}</div>`).join("") : "--"}</td></tr>`).join("")}</tbody></table></div><div class="ocw-mf-dialog-note">${ocTranslate("仅补充发货数量、单位、重量、体积、计费重和项目归属；OA 采购事实不被覆盖。")}</div></div>` }],
      primary_action_label: isGrid ? undefined : "确认整体导入",
      primary_action: isGrid ? undefined : () => this.applyMaterialXlsxImport(dialog, preview),
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-mf-dialog ocw-mf-import-dialog");
    if (!isGrid && preview.source_context?.root_kind === "expense") {
      dialog.fields_dict.preview.$wrapper.prepend(this.renderCurrentSourceReviewControls(preview.source_context, preview.cargo_review));
    }
    if (isGrid) {
      this.setupPackingSpreadsheetDialog(dialog, preview);
      dialog.$wrapper
        .on("click", "[data-action='mf-wiki-import-cancel']", () => dialog.hide())
        .on("click", "[data-action='mf-wiki-import-confirm']", () => {
          this.confirmPackingSpreadsheet(dialog).catch((error) => this.showError(error));
        });
    }
  }

  materialImportFieldLabel(fieldname) {
    return {
      actual_shipped_qty: ocTranslate("发货数量"),
      shipped_uom: ocTranslate("发货单位"),
      net_weight_kg: ocTranslate("净重 kg"),
      gross_weight_kg: ocTranslate("毛重 kg"),
      volume_m3: ocTranslate("体积 m³"),
      chargeable_weight_kg: ocTranslate("计费重 kg"),
      project_collection: ocTranslate("项目归属"),
    }[fieldname] || fieldname;
  }

  renderWikiMaterialImportPreview(preview) {
    return this.renderPackingSpreadsheetPreview(preview);
  }

  async applyMaterialXlsxImport(dialog, preview) {
    return this.applyMaterialImport(dialog, preview);
  }

  collectMaterialImportChoices(dialog, preview) {
    const baseChoices = dialog._ocwMaterialImportBaseChoices || {};
    const choices = {
      fields: {},
      matches: { ...(baseChoices.matches || {}) },
      source_fields: { ...(baseChoices.source_fields || {}) },
      merged_source_fields: {},
      group_confirmations: { ...(baseChoices.group_confirmations || {}) },
      allocations: JSON.parse(JSON.stringify(baseChoices.allocations || {})),
      source_validation: { ...(baseChoices.source_validation || {}) },
    };
    dialog.$wrapper.find("[data-mf-conflict-row]").each((_, node) => {
      const row = $(node).attr("data-mf-conflict-row");
      choices.fields[row] = choices.fields[row] || {};
      choices.fields[row][$(node).attr("data-fieldname")] = $(node).val();
    });
    dialog.$wrapper.find("[data-mf-match-row]").each((_, node) => {
      choices.matches[$(node).attr("data-mf-match-row")] = $(node).val();
    });
    dialog.$wrapper.find("[data-mf-source-field]").each((_, node) => {
      const row = $(node).attr("data-source-row");
      choices.source_fields[row] = choices.source_fields[row] || {};
      choices.source_fields[row][$(node).attr("data-fieldname")] = $(node).val();
    });
    dialog.$wrapper.find("[data-mf-merged-source-field]").each((_, node) => {
      const row = $(node).attr("data-source-row");
      choices.merged_source_fields[row] = choices.merged_source_fields[row] || {};
      choices.merged_source_fields[row][$(node).attr("data-fieldname")] = $(node).val();
    });
    dialog.$wrapper.find("[data-mf-group-confirmation]").each((_, node) => {
      choices.group_confirmations[$(node).attr("data-mf-group-confirmation")] = $(node).is(":checked");
    });
    dialog.$wrapper.find("[data-mf-source-validation]").each((_, node) => {
      choices.source_validation[$(node).attr("data-mf-source-validation")] = $(node).is(":checked");
    });
    dialog.$wrapper.find("[data-mf-allocation]").each((_, node) => {
      const $input = $(node);
      const groupId = $input.attr("data-group-id");
      const sourceKey = $input.attr("data-source-key");
      choices.allocations[groupId] = choices.allocations[groupId] || {};
      choices.allocations[groupId][sourceKey] = choices.allocations[groupId][sourceKey] || {};
      choices.allocations[groupId][sourceKey][$input.attr("data-fieldname")] = String($input.val() || "").trim();
    });
    if (preview.merged_preview_hash) choices.merged_preview_hash = preview.merged_preview_hash;
    if (preview.merge_reviews) choices.merge_reviews = preview.merge_reviews;
    return choices;
  }

  async applyMaterialImport(dialog, preview) {
    const choices = this.collectMaterialImportChoices(dialog, preview);
    if (preview.source_context?.root_kind === "expense") {
      const sourceReview = this.collectCurrentSourceReviewControls(dialog.$wrapper);
      choices.confirm_complete_cargo = sourceReview.complete_cargo;
      choices._source_review = sourceReview;
    }
    const validationError = this.validateWikiMaterialAllocations(preview, choices);
    if (validationError) throw new Error(validationError);
    if (!(await this.ensureEditSession()) || dialog._pg?.closed) return;
    const result = await this.call("overseas_costing.api.materials.apply_material_import", {
      batch_name: this.detailState.batchName,
      preview_revision: preview.preview_revision,
      choices_json: JSON.stringify(choices),
      edit_token: this.detailState.editToken,
      expected_modified: this.detailState.expectedModified,
    }, true);
    if (result?.code === "MERGED_PREVIEW_CONFIRMATION_REQUIRED" && result.merged_preview) {
      dialog._ocwMaterialImportBaseChoices = {
        matches: choices.matches,
        source_fields: choices.source_fields,
        group_confirmations: choices.group_confirmations,
        allocations: choices.allocations,
        source_validation: choices.source_validation,
      };
      const mergedPreview = {
        ...result.merged_preview,
        source_grid: result.merged_preview.source_grid || preview.source_grid,
        merge_reviews: result.merged_preview.merge_reviews || preview.merge_reviews,
        preview_revision: preview.preview_revision,
        merged_preview_hash: result.merged_preview_hash,
      };
      dialog._ocwMaterialPreview = mergedPreview;
      dialog.fields_dict.preview.$wrapper.html(this.renderWikiMaterialImportPreview(mergedPreview));
      if (dialog._pg) this.switchPackingPreviewTab(dialog, "result");
      frappe.show_alert({ message: ocTranslate("已生成合并后的最终预览，请核对后确认写入"), indicator: "blue" });
      return;
    }
    if (!result || !result.ok) {
      const messages = {
        ALLOCATION_REQUIRED: "请填写所有合箱物料的净重、毛重和体积。",
        ALLOCATION_TOTAL_MISMATCH: "合箱分配合计与来源整箱数据不一致。",
        GROUP_CONFIRMATION_REQUIRED: "请确认所有候选合并包装组。",
        MERGE_REVIEW_REQUIRED: "请返回原表核对黄色边框的合并范围。",
        SOURCE_FIELD_CHOICE_REQUIRED: "请处理来源中的单位或项目归属冲突。",
        MERGED_PREVIEW_CONFIRMATION_REQUIRED: "请先核对合并后的最终预览。",
        LINE_CHOICE_REQUIRED: "请为重复物料选择对应的采购明细行。",
        DUPLICATE_TARGET_SELECTION: "两组来源行选中了同一采购明细，请重新预览并分别指定。",
        SOURCE_VALIDATION_CONFIRMATION_REQUIRED: "请先确认装箱计划表的数据校验提示。",
        SOURCE_CHANGED: "装箱计划表已经更新，请重新预览后再确认。",
        PREVIEW_CHANGED: "装箱计划解析结果已经变化，请重新预览。",
        BATCH_VERSION_CHANGED: "批次数据已经变化，请刷新页面后重新操作。",
      };
      throw new Error(result?.message || messages[result?.code] || result?.code || "资料导入失败");
    }
    this.updateMaterialFeeExpectedModified(result);
    if (dialog._pg) dialog._pg.allowClose = true;
    dialog.hide();
    frappe.show_alert({ message: result.review_saved ? ocTranslate("已采用采购支出货物清单，共 {0} 行", [preview.cargo_review?.rows?.length || 0]) : ocTranslate("已更新 {0} 行、{1} 个字段", [result.updated_count || 0, result.changed_field_count || 0]), indicator: "green" });
    await this.loadMaterialFeeWorkspace({ quiet: true });
    if (!result.review_saved) this.restartMaterialAIForChangedSources();
  }

  validateWikiMaterialAllocations(preview, choices) {
    for (const group of preview.confirmation_groups || []) {
      if (group.source_correction_required) return ocTranslate("第 {0} 行的真实合并跨度矛盾，请修正原表后重新预览。", [(group.row_numbers || []).join("、")]);
    }
    for (const group of preview.shared_groups || []) {
      if (group.allocation_required === false) continue;
      const groupLabel = ocTranslate("第 {0} 行共箱", [(group.row_numbers || []).join("、")]);
      const values = choices.allocations?.[group.group_id] || {};
      for (const [fieldname, metric] of Object.entries(group.metrics || {})) {
        const rawValues = (group.participants || []).map((participant) => String(values?.[participant.source_key]?.[fieldname] ?? "").trim());
        if (rawValues.some((value) => value === "")) return ocTranslate("请完整填写 {0} 的{1}。", [groupLabel, this.materialImportFieldLabel(fieldname)]);
        const numbers = rawValues.map((value) => Number(value));
        if (numbers.some((value) => !Number.isFinite(value) || value < 0)) return ocTranslate("请完整填写 {0} 的{1}。", [groupLabel, this.materialImportFieldLabel(fieldname)]);
        const precision = Math.max(0, Number(metric.precision || 0));
        const factor = 10 ** precision;
        const actual = Math.round(numbers.reduce((sum, value) => sum + value, 0) * factor);
        const expected = Math.round(Number(metric.value || 0) * factor);
        if (actual !== expected) return ocTranslate("{0} 的{1}分配合计应为 {2}。", [groupLabel, this.materialImportFieldLabel(fieldname), metric.value]);
      }
    }
    for (const group of preview.confirmation_groups || []) {
      if (preview.source_grid?.cells?.length || choices.group_confirmations?.[group.group_id] !== true) return ocTranslate("请返回原表核对第 {0} 行的合并关系。", [(group.row_numbers || []).join("、")]);
    }
    for (const issue of preview.source_validation?.blocking || []) {
      if (issue.confirmation_required && choices.source_validation?.[issue.confirmation_key] !== true) return ocTranslate("请先确认：{0}。", [issue.message || issue.code]);
      if (!issue.confirmation_required) return issue.message || "原表存在待修正数据，请核对来源单元格。";
    }
    for (const row of preview.rows || []) {
      if (row.match_status === "choice_required" && !choices.matches?.[row.source_row]) return ocTranslate("请为来源行 {0} 选择采购明细。", [row.source_row]);
      for (const conflict of row.source_conflicts || []) {
        const selected = preview.is_merged_preview
          ? choices.merged_source_fields?.[row.source_row]?.[conflict.field]
          : choices.source_fields?.[row.source_row]?.[conflict.field];
        if (!selected) return ocTranslate("请处理来源行 {0} 的{1}冲突。", [row.source_row, this.materialImportFieldLabel(conflict.field)]);
      }
    }
    return "";
  }
