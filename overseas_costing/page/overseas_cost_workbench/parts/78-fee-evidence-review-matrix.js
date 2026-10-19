  feeEvidenceMatrixColumns() {
    return [
      { key: "IGI", label: ocTranslate("IGI"), feeLogicalKey: "import_tax" },
      { key: "IVA", label: ocTranslate("IVA"), feeLogicalKey: "import_tax" },
      { key: "DTA", label: ocTranslate("DTA"), feeLogicalKey: "import_tax" },
      { key: "PRV", label: ocTranslate("PRV"), feeLogicalKey: "import_tax" },
      { key: "PRV_IVA", label: ocTranslate("PRV IVA"), feeLogicalKey: "import_tax" },
      { key: "CUSTOMS_SERVICE", label: ocTranslate("清关服务费"), feeLogicalKey: "customs_clearance_fee" },
    ];
  }

  feeEvidenceMatrixPageSize() {
    return 100;
  }

  feeEvidenceMatrixPageWindow(draft = {}, review = {}) {
    const rows = this.feeEvidenceMaterialMatrix(draft).rows || [];
    const pageSize = this.feeEvidenceMatrixPageSize();
    const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
    const page = Math.max(0, Math.min(pageCount - 1, Number.isInteger(review.matrixPage) ? review.matrixPage : 0));
    const start = page * pageSize;
    return { page, pageCount, pageSize, start, end: Math.min(rows.length, start + pageSize), total: rows.length };
  }

  setFeeEvidenceMatrixPage(review = {}, draft = {}, page = 0) {
    const window = this.feeEvidenceMatrixPageWindow(draft, { ...review, matrixPage: Number(page) });
    review.matrixPage = window.page;
    return window;
  }

  feeEvidenceSourcePageWindow(draft = {}, review = {}) {
    const refs = this.feeEvidenceReviewSourceRefs(draft);
    const pageSize = 100;
    const pageCount = Math.max(1, Math.ceil(refs.length / pageSize));
    const page = Math.max(0, Math.min(pageCount - 1, Number.isInteger(review.sourcePage) ? review.sourcePage : 0));
    const start = page * pageSize;
    return { refs, page, pageCount, start, end: Math.min(refs.length, start + pageSize), total: refs.length };
  }

  setFeeEvidenceSourcePage(review = {}, draft = {}, page = 0) {
    const window = this.feeEvidenceSourcePageWindow(draft, { ...review, sourcePage: Number(page) });
    review.sourcePage = window.page;
    return window;
  }

  feeEvidenceUnmatchedWarnings(draft = {}) {
    const cache = this.feeEvidenceDraftMatrixCache(draft);
    if (cache.unmatchedWarnings) return cache.unmatchedWarnings;
    const matrix = this.feeEvidenceMaterialMatrix(draft);
    const seen = new Set();
    cache.unmatchedWarnings = (matrix.unmatched_lines || draft.unmatched_lines || []).filter((row) => {
      if (String(row?.reason_code || "") === "MATERIAL_MATRIX_LEDGER_ONLY") return false;
      const key = String(row?.message || row?.reason || row?.reason_code || "请核对原附件");
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    return cache.unmatchedWarnings;
  }

  feeEvidenceWarningPageWindow(draft = {}, review = {}) {
    const rows = this.feeEvidenceUnmatchedWarnings(draft);
    const pageSize = 100;
    const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
    const page = Math.max(0, Math.min(pageCount - 1, Number.isInteger(review.warningPage) ? review.warningPage : 0));
    const start = page * pageSize;
    return { rows, page, pageCount, start, end: Math.min(rows.length, start + pageSize), total: rows.length };
  }

  setFeeEvidenceWarningPage(review = {}, draft = {}, page = 0) {
    const window = this.feeEvidenceWarningPageWindow(draft, { ...review, warningPage: Number(page) });
    review.warningPage = window.page;
    return window;
  }

  feeEvidenceMatrixColumnKey(component = {}) {
    const type = String(component.component_type || "").toUpperCase();
    const taxCode = String(component.tax_code || "").toUpperCase();
    const feeKey = String(component.fee_logical_key || component.logical_fee_key || "");
    if (type === "CUSTOMS_SERVICE" || taxCode === "CUSTOMS_SERVICE" || feeKey === "customs_clearance_fee") return "CUSTOMS_SERVICE";
    return this.feeEvidenceMatrixColumns().some((column) => column.key === taxCode && column.feeLogicalKey === "import_tax") ? taxCode : "";
  }

  feeEvidenceDraftMatrixCache(draft = {}) {
    this._feeEvidenceDraftMatrixCaches = this._feeEvidenceDraftMatrixCaches || new WeakMap();
    let cache = this._feeEvidenceDraftMatrixCaches.get(draft);
    const matrix = draft.material_matrix || null;
    if (!cache || cache.matrix !== matrix) {
      cache = { matrix, cells: new Map(), rowIndexes: null, populatedCoordinates: null, sourceRefs: null, allSourceRefs: null, unmatchedWarnings: null };
      this._feeEvidenceDraftMatrixCaches.set(draft, cache);
    }
    return cache;
  }

  feeEvidenceMaterialMatrix(draft = {}) {
    const matrix = draft.material_matrix;
    if (matrix && Array.isArray(matrix.rows)) return matrix;
    const rows = (draft.item_options || []).map((item) => ({
      item: String(item.item || item.name || ""),
      stable_line_key: String(item.stable_line_key || item.item || item.name || ""),
      material_code: String(item.material_code || ""),
      product_name: String(item.product_name || ""),
      hs_code: String(item.hs_code || ""),
      hs_suggestions: [],
      cells: {},
    }));
    const byItem = new Map(rows.map((row) => [row.item, row]));
    (draft.components || []).forEach((component, index) => {
      if (this.feeEvidenceIsLedgerComponent(component || {})) return;
      const row = byItem.get(String(component?.item || ""));
      const columnKey = this.feeEvidenceMatrixColumnKey(component || {});
      if (!row || !columnKey) return;
      row.cells[columnKey] = row.cells[columnKey] || { proposals: [], saved: [] };
      row.cells[columnKey].proposals.push(index);
    });
    return {
      columns: this.feeEvidenceMatrixColumns().map((column) => ({
        key: column.key,
        label: column.label,
        fee_logical_key: column.feeLogicalKey,
      })),
      rows,
      saved_components: [],
      unmatched_lines: draft.unmatched_lines || [],
      missing_fx: Boolean(draft.missing_fx),
    };
  }

  feeEvidenceIsLedgerComponent(component = {}) {
    return String(component.component_type || "").toUpperCase() === "REFUND_REVERSAL" ||
      String(component.accounting_role || "").toUpperCase() === "SETTLEMENT" ||
      String(component.cost_effect || "").toUpperCase() === "LEDGER_ONLY";
  }

  feeEvidenceLedgerComponentLabel(component = {}) {
    if (String(component.component_type || "").toUpperCase() === "REFUND_REVERSAL") return ocTranslate("退款／冲回");
    if (String(component.accounting_role || "").toUpperCase() === "SETTLEMENT") return ocTranslate("结算台账");
    return ocTranslate("仅记台账");
  }

  feeEvidenceStableKey(value) {
    if (Array.isArray(value)) return `[${value.map((item) => this.feeEvidenceStableKey(item)).join(",")}]`;
    if (value && typeof value === "object") {
      return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${this.feeEvidenceStableKey(value[key])}`).join(",")}}`;
    }
    return JSON.stringify(value);
  }

  feeEvidenceComponentSourceRefs(component = {}) {
    const refs = [];
    const seen = new Set();
    const append = (ref) => {
      if (!ref || typeof ref !== "object" || Array.isArray(ref)) return;
      const key = this.feeEvidenceStableKey(ref);
      if (seen.has(key)) return;
      seen.add(key);
      refs.push(ref);
    };
    (component.source_refs || []).forEach(append);
    append(component.source_evidence);
    return refs;
  }

  feeEvidenceUniqueSourceRefs(rows = []) {
    const refs = [];
    const seen = new Set();
    rows.forEach((ref) => {
      if (!ref || typeof ref !== "object" || Array.isArray(ref)) return;
      const key = this.feeEvidenceStableKey(ref);
      if (seen.has(key)) return;
      seen.add(key);
      refs.push(ref);
    });
    return refs;
  }

  feeEvidenceMatrixSourceRefs(draft = {}) {
    const cache = this.feeEvidenceDraftMatrixCache(draft);
    if (cache.sourceRefs) return cache.sourceRefs;
    const matrix = this.feeEvidenceMaterialMatrix(draft);
    const refs = [];
    (matrix.rows || []).forEach((row) => {
      Object.values(row.cells || {}).forEach((cell) => {
        (cell?.proposals || []).forEach((index) => {
          refs.push(...this.feeEvidenceComponentSourceRefs(this.feeEvidenceComponentAt(draft, index) || {}));
        });
        (cell?.saved || []).forEach((index) => {
          refs.push(...this.feeEvidenceComponentSourceRefs((matrix.saved_components || [])[index] || {}));
        });
      });
    });
    cache.sourceRefs = this.feeEvidenceUniqueSourceRefs(refs);
    return cache.sourceRefs;
  }

  feeEvidenceReviewSourceRefs(draft = {}) {
    const cache = this.feeEvidenceDraftMatrixCache(draft);
    if (cache.allSourceRefs) return cache.allSourceRefs;
    const matrix = this.feeEvidenceMaterialMatrix(draft);
    const unmatched = matrix.unmatched_lines || draft.unmatched_lines || [];
    cache.allSourceRefs = this.feeEvidenceUniqueSourceRefs([
      ...(draft.evidence?.source_refs || []),
      ...(draft.fee_splits || []).flatMap((row) => row.source_refs || []),
      ...this.feeEvidenceLedgerComponents(draft).flatMap((row) => this.feeEvidenceComponentSourceRefs(row)),
      ...this.feeEvidenceMatrixSourceRefs(draft),
      ...unmatched.flatMap((row) => this.feeEvidenceComponentSourceRefs(row)),
    ]);
    return cache.allSourceRefs;
  }

  feeEvidenceLedgerComponents(draft = {}) {
    const result = [];
    const seen = new Set();
    const append = (component) => {
      if (!component || !this.feeEvidenceIsLedgerComponent(component)) return;
      const proposalId = String(component.proposal_id || "");
      if (!proposalId || seen.has(proposalId)) return;
      seen.add(proposalId);
      result.push(component);
    };
    if (String(draft.component_contract?.mode || "") === "INDEXED_COLUMNS_V1") {
      const matrix = this.feeEvidenceMaterialMatrix(draft);
      (matrix.unmatched_lines || []).forEach((line) => {
        if (String(line?.reason_code || "") !== "MATERIAL_MATRIX_LEDGER_ONLY") return;
        append(this.feeEvidenceComponentAt(draft, line.proposal_index));
      });
    } else {
      (draft.components || []).forEach(append);
    }
    return result;
  }

  defaultFeeEvidenceReviewSelections(draft = {}) {
    const matrix = this.feeEvidenceMaterialMatrix(draft);
    const columnFeeKeys = new Map(this.feeEvidenceMatrixColumns().map((column) => [column.key, column.feeLogicalKey]));
    const matrixFeeKeys = new Set();
    (matrix.rows || []).forEach((row) => Object.entries(row.cells || {}).forEach(([columnKey, cell]) => {
      if ((cell?.proposals || []).length || (cell?.saved || []).length) matrixFeeKeys.add(columnFeeKeys.get(columnKey));
    }));
    const ledgerComponents = this.feeEvidenceLedgerComponents(draft);
    const hasDefaultLedgerComponent = ledgerComponents.some((row) => row.default_selected && String(row.proposal_id || ""));
    const selected = new Set();
    const evidence = draft.evidence || {};
    if (evidence.default_selected || matrixFeeKeys.size || hasDefaultLedgerComponent) {
      selected.add(String(evidence.proposal_id || "evidence:classification"));
    }
    (draft.fee_splits || []).forEach((row) => {
      if (row.default_selected || matrixFeeKeys.has(String(row.logical_fee_key || ""))) {
        const proposalId = String(row.proposal_id || "");
        if (proposalId) selected.add(proposalId);
      }
    });
    ledgerComponents.forEach((row) => {
      const proposalId = String(row.proposal_id || "");
      if (row.default_selected && proposalId) selected.add(proposalId);
    });
    return selected;
  }

  feeEvidenceIndexedValue(store = {}, fieldname, rowIndex) {
    const column = store?.columns?.[fieldname];
    if (!column || typeof column !== "object") return undefined;
    let valueIndex = rowIndex;
    if (Array.isArray(column.rows)) {
      let low = 0;
      let high = column.rows.length - 1;
      let found = -1;
      while (low <= high) {
        const middle = Math.floor((low + high) / 2);
        const candidate = Number(column.rows[middle]);
        if (candidate === rowIndex) { found = middle; break; }
        if (candidate < rowIndex) low = middle + 1;
        else high = middle - 1;
      }
      if (found < 0) return undefined;
      valueIndex = found;
    }
    if (Object.prototype.hasOwnProperty.call(column, "constant")) return column.constant;
    if (Array.isArray(column.values)) return column.values[valueIndex];
    if (Array.isArray(column.dictionary) && Array.isArray(column.indices)) {
      return column.dictionary[column.indices[valueIndex]];
    }
    return undefined;
  }

  feeEvidenceComponentAt(draft = {}, rowIndex) {
    if (!Number.isInteger(rowIndex) || rowIndex < 0) return null;
    const store = draft.component_store || {};
    if (String(draft.component_contract?.mode || "") !== "INDEXED_COLUMNS_V1" || store.format !== "INDEXED_COLUMNS_V1") {
      const component = (draft.components || [])[rowIndex];
      return component && typeof component === "object" ? component : null;
    }
    if (rowIndex >= Number(store.count || 0)) return null;
    const component = {};
    Object.keys(store.columns || {}).forEach((fieldname) => {
      const value = this.feeEvidenceIndexedValue(store, fieldname, rowIndex);
      if (value !== undefined) component[fieldname] = value;
    });
    return Object.keys(component).length ? component : null;
  }

  feeEvidencePlainNumber(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "";
    return Number(number.toFixed(6)).toString();
  }

  feeEvidenceAggregateComponents(components = []) {
    const originalByCurrency = new Map();
    let amountRmb = 0;
    let hasRmb = false;
    let missingFx = false;
    const sourceProposalIds = new Set();
    const warnings = [];
    components.forEach((component) => {
      const currency = String(component?.currency || "").toUpperCase() || "UNKNOWN";
      const rawRmb = component?.amount_rmb;
      try {
        const originalMinor = this.feeEvidenceAmountMinor(component?.original_amount);
        originalByCurrency.set(currency, (originalByCurrency.get(currency) || 0n) + originalMinor);
        if (rawRmb === null || rawRmb === undefined || rawRmb === "") missingFx = true;
      } catch (_error) {}
      const rmb = Number(rawRmb);
      if (rawRmb !== null && rawRmb !== undefined && rawRmb !== "" && Number.isFinite(rmb)) {
        amountRmb += rmb;
        hasRmb = true;
      }
      const proposalId = String(component?.proposal_id || "");
      if (proposalId) sourceProposalIds.add(proposalId);
      if (component?.warning) warnings.push(String(component.warning));
      if (component?.needs_review) warnings.push("建议需人工复核。");
      if (component?.has_conflict) warnings.push("建议存在冲突。");
    });
    const currencies = [...originalByCurrency.keys()];
    return {
      originalAmount: currencies.length === 1 ? this.feeEvidenceMinorText(originalByCurrency.get(currencies[0])) : "",
      currency: currencies.length === 1 ? currencies[0] : (currencies.length ? "MIXED" : ""),
      amountRmb: missingFx ? null : (hasRmb ? this.feeEvidencePlainNumber(amountRmb) : ""),
      missingFx,
      sourceProposalIds: [...sourceProposalIds],
      warning: [...new Set(warnings)].join(" "),
    };
  }

  resolveFeeEvidenceMatrixCell(draft = {}, rowIndex, columnKey) {
    const cache = this.feeEvidenceDraftMatrixCache(draft);
    const cacheKey = `${rowIndex}:${columnKey}`;
    if (cache.cells.has(cacheKey)) return cache.cells.get(cacheKey);
    const matrix = this.feeEvidenceMaterialMatrix(draft);
    const row = matrix.rows?.[rowIndex] || {};
    const compact = row.cells?.[columnKey] || { proposals: [], saved: [] };
    const proposals = (compact.proposals || []).map((index) => this.feeEvidenceComponentAt(draft, index)).filter(Boolean);
    const savedRows = matrix.saved_components || [];
    const saved = (compact.saved || []).map((index) => savedRows[index]).filter((value) => value && typeof value === "object");
    const suggestion = this.feeEvidenceAggregateComponents(proposals);
    const current = this.feeEvidenceAggregateComponents(saved.length ? saved : proposals);
    const proposalIndexes = new Set((compact.proposals || []).filter(Number.isInteger));
    const savedIndexes = new Set((compact.saved || []).filter(Number.isInteger));
    const suggestionDetails = (row.hs_suggestion_details || []).filter((detail) => detail && typeof detail === "object");
    const hsSuggestions = suggestionDetails.length ? suggestionDetails.filter((detail) =>
      (detail.proposals || []).some((index) => proposalIndexes.has(index)) ||
      (detail.saved || []).some((index) => savedIndexes.has(index))
    ).map((detail) => detail.hs_code).filter(Boolean) :
      (proposalIndexes.size || savedIndexes.size ? (row.hs_suggestions || []).filter(Boolean) : []);
    const warnings = [current.warning, suggestion.warning];
    if (hsSuggestions.length) warnings.push(ocTranslate("HS 差异：凭证建议 {0}，当前物料为 {1}。", [hsSuggestions.join("、"), row.hs_code || "未填写"]));
    if (current.missingFx || suggestion.missingFx) warnings.push("缺汇率");
    if (saved.length && proposals.length) warnings.push("已保存值优先，AI／规则建议仅供对照。");
    const resolved = {
      originalAmount: current.originalAmount,
      currency: current.currency,
      amountRmb: current.amountRmb,
      missingFx: current.missingFx,
      origin: saved.length ? "SAVED" : (proposals.length ? "AI" : "EMPTY"),
      suggestedOriginalAmount: suggestion.originalAmount,
      suggestedCurrency: suggestion.currency,
      suggestedAmountRmb: suggestion.amountRmb,
      sourceProposalIds: suggestion.sourceProposalIds,
      warning: [...new Set(warnings.filter(Boolean))].join(" "),
    };
    cache.cells.set(cacheKey, resolved);
    return resolved;
  }

  feeEvidenceMatrixEditKey(item, columnKey) {
    return `${String(item || "")}\u001f${String(columnKey || "")}`;
  }

  ensureFeeEvidenceMatrixState(review = {}, draft = {}) {
    review.matrixEdits = review.matrixEdits || {};
    if (!Number.isInteger(review.matrixRevision)) review.matrixRevision = 0;
    if (review.matrixDetailsOpen === undefined) review.matrixDetailsOpen = false;
    if (!Number.isInteger(review.matrixPage)) review.matrixPage = 0;
    if (!Number.isInteger(review.sourcePage)) review.sourcePage = 0;
    if (!Number.isInteger(review.warningPage)) review.warningPage = 0;
    this.setFeeEvidenceMatrixPage(review, draft, review.matrixPage);
    return review.matrixEdits;
  }

  feeEvidenceMatrixRowIndex(draft = {}, item) {
    const cache = this.feeEvidenceDraftMatrixCache(draft);
    if (!cache.rowIndexes) {
      cache.rowIndexes = new Map(this.feeEvidenceMaterialMatrix(draft).rows.map((row, index) => [String(row.item || ""), index]));
    }
    return cache.rowIndexes.get(String(item || "")) ?? -1;
  }

  setFeeEvidenceMatrixValue(review, draft, item, columnKey, rawValue) {
    const value = String(rawValue ?? "").trim();
    if (value) this.feeEvidenceValidateAmountBounds(value);
    this.ensureFeeEvidenceMatrixState(review, draft);
    const rowIndex = this.feeEvidenceMatrixRowIndex(draft, item);
    if (rowIndex < 0 || !this.feeEvidenceMatrixColumns().some((column) => column.key === columnKey)) throw new Error(ocTranslate("物料或税费列已变更，请刷新草稿。"));
    const base = this.resolveFeeEvidenceMatrixCell(draft, rowIndex, columnKey);
    this.setFeeEvidenceMatrixEdit(review, draft, rowIndex, columnKey, {
      value,
      dirty: true,
      cleared: value === "",
      sourceProposalIds: [...base.sourceProposalIds],
    });
  }

  adoptFeeEvidenceMatrixSuggestion(review, draft, item, columnKey) {
    this.ensureFeeEvidenceMatrixState(review, draft);
    const rowIndex = this.feeEvidenceMatrixRowIndex(draft, item);
    const base = this.resolveFeeEvidenceMatrixCell(draft, rowIndex, columnKey);
    this.setFeeEvidenceMatrixEdit(review, draft, rowIndex, columnKey, {
      value: String(base.suggestedOriginalAmount || ""),
      dirty: true,
      cleared: !base.suggestedOriginalAmount,
      adoptedAI: true,
      sourceProposalIds: [...base.sourceProposalIds],
    });
  }

  setFeeEvidenceMatrixEdit(review, draft, rowIndex, columnKey, edit) {
    const matrix = this.feeEvidenceMaterialMatrix(draft);
    const row = matrix.rows[rowIndex] || {};
    const totalsCache = review._feeEvidenceMatrixTotals;
    const signature = this.feeEvidenceMatrixTotalsSignature(draft, review);
    const canApplyDelta = totalsCache?.revision === review.matrixRevision && totalsCache.signature === signature;
    const previous = canApplyDelta ? this.feeEvidenceMatrixEffectiveCell(draft, review, rowIndex, columnKey) : null;
    review.matrixEdits[this.feeEvidenceMatrixEditKey(row.item, columnKey)] = edit;
    review.matrixRevision += 1;
    if (!canApplyDelta) return;
    try {
      const current = this.feeEvidenceMatrixEffectiveCell(draft, review, rowIndex, columnKey);
      const column = this.feeEvidenceMatrixColumns().find((candidate) => candidate.key === columnKey);
      const group = totalsCache.value[column?.feeLogicalKey];
      if (!group) throw new Error(ocTranslate("税费列已变更。"));
      this.feeEvidenceAccumulateTotalCell(group, previous, -1);
      this.feeEvidenceAccumulateTotalCell(group, current, 1);
      this.feeEvidenceFinalizeTotalGroup(group);
      totalsCache.revision = review.matrixRevision;
    } catch (_error) {
      review._feeEvidenceMatrixTotals = null;
    }
  }

  feeEvidenceMatrixPopulatedCoordinates(draft = {}, review = {}) {
    const cache = this.feeEvidenceDraftMatrixCache(draft);
    if (!cache.populatedCoordinates) {
      const allowed = new Set(this.feeEvidenceMatrixColumns().map((column) => column.key));
      cache.populatedCoordinates = [];
      this.feeEvidenceMaterialMatrix(draft).rows.forEach((row, rowIndex) => {
        Object.keys(row.cells || {}).forEach((columnKey) => {
          if (allowed.has(columnKey)) cache.populatedCoordinates.push([rowIndex, columnKey]);
        });
      });
    }
    const result = [...cache.populatedCoordinates];
    const seen = new Set(result.map(([rowIndex, columnKey]) => `${rowIndex}:${columnKey}`));
    Object.keys(review.matrixEdits || {}).forEach((editKey) => {
      const [item, columnKey] = editKey.split("\u001f");
      const rowIndex = this.feeEvidenceMatrixRowIndex(draft, item);
      const key = `${rowIndex}:${columnKey}`;
      if (rowIndex >= 0 && !seen.has(key)) {
        seen.add(key);
        result.push([rowIndex, columnKey]);
      }
    });
    return result;
  }

  feeEvidenceMatrixTotalsSignature(draft = {}, review = {}) {
    const feeIds = new Set((draft.fee_splits || []).map((row) => String(row.proposal_id || "")));
    const edits = {};
    Object.entries(review.edits || {}).forEach(([proposalId, values]) => {
      if (feeIds.has(proposalId)) edits[proposalId] = values;
    });
    return JSON.stringify({
      selections: review.selections && typeof review.selections[Symbol.iterator] === "function" ? [...review.selections].filter((id) => feeIds.has(String(id))).sort() : null,
      edits,
    });
  }

  invalidateFeeEvidenceMatrixTotals(review = {}) {
    review._feeEvidenceMatrixTotals = null;
  }

  feeEvidenceAccumulateTotalCell(group, cell, direction) {
    if (!String(cell?.value || "").trim()) return;
    const multiplier = BigInt(direction);
    group.activeCount += direction;
    group.allocatedMinor += this.feeEvidenceAmountMinor(cell.value) * multiplier;
    if (cell.missingFx || cell.amountRmb === null) group.missingFxCount += direction;
    else if (Number.isFinite(Number(cell.amountRmb))) group.allocatedRmb += direction * Number(cell.amountRmb);
  }

  feeEvidenceFinalizeTotalGroup(group) {
    group.allocatedText = this.feeEvidenceMinorText(group.allocatedMinor);
    group.allocatedOriginal = group.allocatedText;
    group.remainingMinor = group.feeTotalMinor - group.allocatedMinor;
    group.remainingText = this.feeEvidenceMinorText(group.remainingMinor);
    group.remaining = group.remainingText;
    group.missingFx = group.missingFxCount > 0;
    group.overage = group.remainingMinor < 0n;
  }

  feeEvidenceMatrixEffectiveCell(draft, review, rowIndex, columnKey) {
    const matrix = this.feeEvidenceMaterialMatrix(draft);
    const row = matrix.rows[rowIndex] || {};
    const base = this.resolveFeeEvidenceMatrixCell(draft, rowIndex, columnKey);
    const edit = review?.matrixEdits?.[this.feeEvidenceMatrixEditKey(row.item, columnKey)];
    const value = edit ? String(edit.value ?? "") : String(base.originalAmount || "");
    const column = this.feeEvidenceMatrixColumns().find((candidate) => candidate.key === columnKey);
    const feeAuthority = this.feeEvidenceFeeTotals(draft, review)[column?.feeLogicalKey] || {};
    const currency = String(feeAuthority.currency || "");
    const hasAuthority = Number(feeAuthority.selectedCount || 0) > 0 && currency && currency !== "MIXED";
    let amountRmb = null;
    let missingFx = Boolean(value);
    let originalRatio = null;
    try {
      const baseMinor = this.feeEvidenceAmountMinor(base.originalAmount);
      if (baseMinor > 0n) {
        const ratioScale = 1000000000n;
        originalRatio = Number((this.feeEvidenceAmountMinor(value) * ratioScale) / baseMinor) / Number(ratioScale);
      }
    } catch (_error) {}
    if (!value) {
      amountRmb = "";
      missingFx = false;
    } else if (!hasAuthority) {
      amountRmb = null;
      missingFx = true;
    } else if (currency === "RMB") {
      amountRmb = this.normalizeFeeEvidenceMatrixAmount(value);
      missingFx = false;
    } else if (edit?.adoptedAI && base.suggestedCurrency === currency) {
      amountRmb = base.suggestedAmountRmb;
      missingFx = base.suggestedAmountRmb === null;
    } else if (base.currency === currency && edit && Number.isFinite(originalRatio) && base.amountRmb !== null && base.amountRmb !== "") {
      amountRmb = this.feeEvidencePlainNumber(originalRatio * Number(base.amountRmb));
      missingFx = false;
    } else if (base.currency === currency && !edit) {
      amountRmb = base.amountRmb;
      missingFx = base.missingFx || base.amountRmb === null;
    }
    return {
      ...base,
      value,
      currency,
      amountRmb,
      missingFx,
      dirty: Boolean(edit?.dirty),
      cleared: Boolean(edit?.cleared),
      adoptedAI: Boolean(edit?.adoptedAI),
      sourceProposalIds: edit ? [...(edit.sourceProposalIds || [])] : [...base.sourceProposalIds],
    };
  }

  normalizeFeeEvidenceMatrixAmount(rawValue) {
    return this.feeEvidenceMinorText(this.feeEvidenceAmountMinor(rawValue));
  }

  feeEvidenceValidateAmountBounds(rawValue) {
    const value = String(rawValue ?? "").trim();
    if (value.length > 64) throw new Error(ocTranslate("物料税费金额文本过长。"));
    if ((value.match(/\d/g) || []).length > 28) throw new Error(ocTranslate("物料税费金额数字位数过多。"));
    const decimalMatch = value.match(/^(?:\d+|\d*\.(\d*))$/);
    if (decimalMatch && String(decimalMatch[1] || "").replace(/0+$/, "").length > 2) throw new Error(ocTranslate("物料税费金额最多保留两位小数。"));
    return value;
  }

  feeEvidenceAmountMinor(rawValue) {
    const value = this.feeEvidenceValidateAmountBounds(rawValue);
    if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(value)) {
      throw new Error(ocTranslate("物料税费金额必须是非负有限数。"));
    }
    const [integerRaw, decimalRaw = ""] = (value.startsWith(".") ? `0${value}` : value).split(".");
    const integer = integerRaw.replace(/^0+(?=\d)/, "") || "0";
    const decimal = decimalRaw.replace(/0+$/, "");
    if (decimal.length > 2) throw new Error(ocTranslate("物料税费金额最多保留两位小数。"));
    return (BigInt(integer) * 100n) + BigInt((decimal + "00").slice(0, 2));
  }

  feeEvidenceMinorText(rawMinor) {
    const minor = typeof rawMinor === "bigint" ? rawMinor : BigInt(rawMinor || 0);
    const negative = minor < 0n;
    const absolute = negative ? -minor : minor;
    const integer = absolute / 100n;
    const decimal = String(absolute % 100n).padStart(2, "0").replace(/0+$/, "");
    return `${negative ? "-" : ""}${integer}${decimal ? `.${decimal}` : ""}`;
  }

  serializeFeeEvidenceMatrix(draft = {}, review = {}) {
    const matrix = this.feeEvidenceMaterialMatrix(draft);
    const cells = [];
    matrix.rows.forEach((row, rowIndex) => {
      this.feeEvidenceMatrixColumns().forEach((column) => {
        const effective = this.feeEvidenceMatrixEffectiveCell(draft, review, rowIndex, column.key);
        if (!String(effective.value || "").trim()) return;
        cells.push({
          item: String(row.item || ""),
          column_key: column.key,
          original_amount: this.normalizeFeeEvidenceMatrixAmount(effective.value),
          source_proposal_ids: [...new Set((effective.sourceProposalIds || []).map(String).filter(Boolean))],
        });
      });
    });
    return { cells };
  }

  feeEvidenceFeeTotals(draft = {}, review = {}) {
    const create = () => ({ feeTotal: "0", feeTotalMinor: 0n, feeTotalText: "0", currency: "", selectedCount: 0, amountValid: true, currencyValid: true });
    const result = { import_tax: create(), customs_clearance_fee: create() };
    const hasSelectionState = review.selections && typeof review.selections.has === "function";
    (draft.fee_splits || []).forEach((split) => {
      const key = String(split.logical_fee_key || "");
      if (!result[key]) return;
      const proposalId = String(split.proposal_id || "");
      const isSelected = hasSelectionState ? review.selections.has(proposalId) : Boolean(split.default_selected);
      if (!isSelected) return;
      const edit = review.edits?.[String(split.proposal_id || "")] || {};
      try {
        result[key].feeTotalMinor += this.feeEvidenceAmountMinor(edit.amount ?? split.amount);
      } catch (_error) {
        result[key].amountValid = false;
      }
      const currency = String(edit.currency ?? split.currency ?? "").toUpperCase().replace("CNY", "RMB");
      if (currency) result[key].currency = result[key].currency && result[key].currency !== currency ? "MIXED" : currency;
      if (!{"RMB": 1, "MXN": 1, "USD": 1}[currency]) result[key].currencyValid = false;
      result[key].selectedCount += 1;
    });
    Object.values(result).forEach((group) => {
      group.feeTotalText = this.feeEvidenceMinorText(group.feeTotalMinor);
      group.feeTotal = group.feeTotalText;
    });
    return result;
  }

  feeEvidenceMatrixTotals(draft = {}, review = {}) {
    this.ensureFeeEvidenceMatrixState(review, draft);
    const signature = this.feeEvidenceMatrixTotalsSignature(draft, review);
    if (review._feeEvidenceMatrixTotals?.revision === review.matrixRevision && review._feeEvidenceMatrixTotals.signature === signature) {
      return review._feeEvidenceMatrixTotals.value;
    }
    const feeTotals = this.feeEvidenceFeeTotals(draft, review);
    const totals = {
      import_tax: { ...feeTotals.import_tax, allocatedMinor: 0n, allocatedOriginal: "0", allocatedText: "0", allocatedRmb: 0, missingFx: false, missingFxCount: 0, activeCount: 0 },
      customs_clearance_fee: { ...feeTotals.customs_clearance_fee, allocatedMinor: 0n, allocatedOriginal: "0", allocatedText: "0", allocatedRmb: 0, missingFx: false, missingFxCount: 0, activeCount: 0 },
    };
    const columns = new Map(this.feeEvidenceMatrixColumns().map((column) => [column.key, column]));
    this.feeEvidenceMatrixPopulatedCoordinates(draft, review).forEach(([rowIndex, columnKey]) => {
        const column = columns.get(columnKey);
        if (!column) return;
        const cell = this.feeEvidenceMatrixEffectiveCell(draft, review, rowIndex, columnKey);
        this.feeEvidenceAccumulateTotalCell(totals[column.feeLogicalKey], cell, 1);
    });
    Object.values(totals).forEach((group) => {
      this.feeEvidenceFinalizeTotalGroup(group);
    });
    review._feeEvidenceMatrixTotals = { revision: review.matrixRevision, signature, value: totals };
    return totals;
  }

  validateFeeEvidenceMatrix(draft = {}, review = {}) {
    const payload = this.serializeFeeEvidenceMatrix(draft, review);
    const totals = this.feeEvidenceMatrixTotals(draft, review);
    const labels = { import_tax: ocTranslate("进口税费"), customs_clearance_fee: ocTranslate("清关费") };
    Object.entries(totals).forEach(([key, group]) => {
      if (!group.activeCount) return;
      if (!group.selectedCount) throw new Error(ocTranslate("请选择{0}费用拆分后再确认。", [labels[key] || key]));
      if (group.selectedCount !== 1) throw new Error(ocTranslate("{0}只能选择一条费用拆分作为金额和币种权威。", [labels[key] || key]));
      if (!group.currencyValid || !group.currency || group.currency === "MIXED") throw new Error(ocTranslate("{0}费用拆分必须使用唯一支持币种。", [labels[key] || key]));
      if (!group.amountValid) throw new Error(ocTranslate("{0}费用总额必须是有效非负金额。", [labels[key] || key]));
    });
    const overage = Object.values(totals).find((group) => group.overage);
    if (overage) throw new Error(ocTranslate("已分摊金额超过费用总额，请调整后再确认。"));
    return payload;
  }

  feeEvidenceSourceLabel(ref = {}) {
    const region = ref.region || ref.image_region;
    return [
      ref.file_name || ref.attachment || ref.file,
      ref.sheet,
      ref.page ? ocTranslate("第 {0} 页", [ref.page]) : "",
      ref.text_line ? ocTranslate("文本第 {0} 行", [ref.text_line]) : "",
      ref.row ? ocTranslate("表格第 {0} 行", [ref.row]) : "",
      ref.cell ? ocTranslate("单元格 {0}", [ref.cell]) : "",
      region ? ocTranslate("图片区域 {0}", [typeof region === "string" ? region : JSON.stringify(region)]) : "",
      ref.path,
    ].filter(Boolean).join(" · ");
  }

  renderFeeEvidenceReviewWarning(row = {}) {
    if (!row.has_conflict && !row.needs_review && !row.warning) return "";
    const message = row.warning || (row.has_conflict ? "AI 与规则结果冲突，已纳入预览，请人工核对。" : "证据或匹配信心不足，已纳入预览，可修改或清空。");
    return `<em class="ocw-mf-review-warning">${this.escape(message)}</em>`;
  }

  renderFeeEvidenceVoucherDetails(draft = {}, review = {}) {
    const evidence = draft.evidence || {};
    const evidenceId = this.escape(evidence.proposal_id || "evidence:classification");
    const statusOptions = [["ESTIMATED", "暂估"], ["ACTUAL", "实际"]];
    const refundParents = draft.refund_parent_options || [];
    const hasSelectionState = review.selections && typeof review.selections.has === "function";
    const selected = (row) => (hasSelectionState ? review.selections.has(String(row?.proposal_id || "")) : Boolean(row?.default_selected)) ? "checked" : "";
    const editValue = (row, fieldname, fallback = "") => review.edits?.[String(row?.proposal_id || "")]?.[fieldname] ?? row?.[fieldname] ?? fallback;
    const open = review.matrixDetailsOpen ? " open" : "";
    const feeRows = draft.fee_splits || [];
    const ledgerRows = this.feeEvidenceLedgerComponents(draft);
    return `<details class="ocw-mf-voucher-details" data-mf-fee-voucher-details${open}>
      <summary><span><b>${ocTranslate("凭证摘要")}</b><small>${this.escape(evidence.evidence_type || ocTranslate("待核对"))} · ${this.escape(evidence.accounting_role || ocTranslate("待核对"))} · ${this.escape(evidence.currency || "")} ${this.escape(evidence.original_amount || ocTranslate("待补录"))}</small></span><em>${ocTranslate("展开核对详情")}</em></summary>
      <div class="ocw-mf-voucher-detail-body">
        ${this.renderCurrentSourceReviewControls(draft.source_context || {}, null, { fees: true })}
        ${this.renderFeeEvidenceReviewWarning(evidence)}
        <label class="ocw-mf-review-choice"><input type="checkbox" data-mf-fee-review-select data-proposal-id="${evidenceId}" ${selected(evidence)}><span><strong>${ocTranslate("采纳凭证事实")}</strong><small>${this.escape(evidence.reason || ocTranslate("请核对凭证类型和会计作用"))}</small></span></label>
        <div class="ocw-mf-review-fields">
          <label>${ocTranslate("凭证类型")}<select data-mf-fee-review-edit data-proposal-id="${evidenceId}" data-fieldname="evidence_type">${[["QUOTE","报价"],["PREPAYMENT","预付款／暂缴"],["FINAL_INVOICE","最终发票／结算单"],["TAX_CERTIFICATE","完税凭证"],["PAYMENT","付款流水"],["REFUND","退款／贷项"],["OTHER","其他"]].map(([value,label]) => `<option value="${value}" ${value === editValue(evidence,"evidence_type") ? "selected" : ""}>${ocTranslate(label)}</option>`).join("")}</select></label>
          <label>${ocTranslate("会计作用")}<select data-mf-fee-review-edit data-proposal-id="${evidenceId}" data-fieldname="accounting_role">${[["ESTIMATE","更新暂估"],["FINAL_BILL","最终账单"],["SETTLEMENT","付款／退款流水"],["REFERENCE","仅作参考"]].map(([value,label]) => `<option value="${value}" ${value === editValue(evidence,"accounting_role") ? "selected" : ""}>${ocTranslate(label)}</option>`).join("")}</select></label>
          <label>${ocTranslate("凭证总额")}<input data-mf-fee-review-edit data-proposal-id="${evidenceId}" data-fieldname="original_amount" value="${this.escape(editValue(evidence,"original_amount"))}" inputmode="decimal"></label>
          <label>${ocTranslate("币种")}<select data-mf-fee-review-edit data-proposal-id="${evidenceId}" data-fieldname="currency">${this.materialFeeCurrencyOptions().map((option) => `<option value="${option.value}" ${option.value === editValue(evidence,"currency","RMB") ? "selected" : ""}>${option.label}</option>`).join("")}</select></label>
          <label>${ocTranslate("方向")}<select data-mf-fee-review-edit data-proposal-id="${evidenceId}" data-fieldname="direction"><option value="DEBIT" ${editValue(evidence,"direction","DEBIT") !== "CREDIT" ? "selected" : ""}>${ocTranslate("付款／应付")}</option><option value="CREDIT" ${editValue(evidence,"direction") === "CREDIT" ? "selected" : ""}>${ocTranslate("退款／冲回")}</option></select></label>
          <label>${ocTranslate("关联原付款")}<select data-mf-fee-review-edit data-proposal-id="${evidenceId}" data-fieldname="related_evidence"><option value="">${ocTranslate("无／待核对")}</option>${refundParents.map((row) => `<option value="${this.escape(row.name || "")}" ${String(row.name || "") === String(editValue(evidence,"related_evidence")) ? "selected" : ""}>${this.escape([row.currency,row.original_amount,row.attachment || row.name].filter(Boolean).join(" · "))}</option>`).join("")}</select></label>
          <label class="is-checkbox"><input type="checkbox" data-mf-fee-review-edit data-proposal-id="${evidenceId}" data-fieldname="is_final" ${Number(editValue(evidence,"is_final",0)) ? "checked" : ""}> ${ocTranslate("最终凭证")}</label>
        </div>
        <div class="ocw-mf-voucher-fee-splits"><h4>${ocTranslate("费用拆分")}</h4>${feeRows.length ? feeRows.map((row) => `<label class="ocw-mf-review-choice ${row.has_conflict || row.needs_review ? "is-warning" : ""}"><input type="checkbox" data-mf-fee-review-select data-proposal-id="${this.escape(row.proposal_id || "")}" ${selected(row)}><span><strong>${this.escape(row.label || row.logical_fee_key)} · ${this.escape(editValue(row,"currency",row.currency || ""))} <input data-mf-fee-review-edit data-proposal-id="${this.escape(row.proposal_id || "")}" data-fieldname="amount" value="${this.escape(editValue(row,"amount"))}" inputmode="decimal"></strong><small><select data-mf-fee-review-edit data-proposal-id="${this.escape(row.proposal_id || "")}" data-fieldname="amount_status">${statusOptions.map(([value,label]) => `<option value="${value}" ${value === editValue(row,"amount_status",evidence.suggested_amount_status) ? "selected" : ""}>${ocTranslate(label)}</option>`).join("")}</select></small>${this.renderFeeEvidenceReviewWarning(row)}</span></label>`).join("") : `<p>${ocTranslate("未识别出可安全拆分的金额，可保留凭证后人工补录。")}</p>`}</div>
        ${ledgerRows.length ? `<section class="ocw-mf-ledger-components"><header><h4>${ocTranslate("矩阵外台账分项")}</h4><p>${ocTranslate("结算、退款或冲回只记入台账，不重复计入物料成本。")}</p></header>${ledgerRows.map((row) => {
          const proposalId = this.escape(row.proposal_id || "");
          const refs = this.feeEvidenceComponentSourceRefs(row).map((ref) => this.feeEvidenceSourceLabel(ref)).filter(Boolean);
          return `<label class="ocw-mf-ledger-component ${row.has_conflict || row.needs_review || row.warning ? "is-warning" : ""}"><input type="checkbox" data-mf-fee-review-select data-proposal-id="${proposalId}" ${selected(row)}><span><strong>${this.feeEvidenceLedgerComponentLabel(row)} · ${this.escape(row.currency || "")} ${this.escape(row.original_amount ?? "--")}</strong><small>${refs.map((value) => this.escape(value)).join("；") || ocTranslate("来源位置待核对")}</small>${this.renderFeeEvidenceReviewWarning(row)}</span></label>`;
        }).join("")}</section>` : ""}
        <div class="ocw-mf-voucher-unclassified"><b>${ocTranslate("凭证待归类差额")}</b><span>${this.escape(draft.unclassified_difference || "0.00")} ${this.escape(evidence.currency || "")}</span><small>${ocTranslate("未明确归属前不伪造物料分摊。")}</small></div>
      </div>
    </details>`;
  }

  renderFeeEvidenceMatrixCell(draft, review, rowIndex, column) {
    const matrix = this.feeEvidenceMaterialMatrix(draft);
    const row = matrix.rows[rowIndex] || {};
    const cell = this.feeEvidenceMatrixEffectiveCell(draft, review, rowIndex, column.key);
    const classes = [cell.warning ? "is-warning" : "", cell.dirty ? "is-dirty" : "", cell.origin === "SAVED" ? "is-manual" : ""].filter(Boolean).join(" ");
    const rmb = cell.value ? (cell.missingFx || cell.amountRmb === null ? "缺汇率" : `¥ ${this.escape(cell.amountRmb || "0")}`) : "未分摊";
    const currencyLabel = cell.currency || "待选择费用币种";
    const suggestion = cell.origin === "SAVED" && cell.suggestedOriginalAmount ? `<div class="ocw-mf-matrix-suggestion"><span>${ocTranslate("AI 建议 {0} {1}", [this.escape(cell.suggestedCurrency || cell.currency), this.escape(cell.suggestedOriginalAmount)])}</span><button type="button" data-action="mf-fee-matrix-use-ai" data-item="${this.escape(row.item || "")}" data-column-key="${column.key}">${ocTranslate("采用 AI 建议")}</button></div>` : "";
    return `<td class="ocw-mf-matrix-cell ${classes}" data-mf-fee-matrix-cell="${this.escape(row.item || "")}:${column.key}">
      <label><span class="sr-only">${this.escape(row.material_code || row.item || "")} ${column.label}</span><input data-mf-fee-matrix-input="1" data-item="${this.escape(row.item || "")}" data-column-key="${column.key}" inputmode="decimal" value="${this.escape(cell.value)}" placeholder="${ocTranslate('补录')}"></label>
      <small>${this.escape(currencyLabel)} · ${rmb}</small>
      ${cell.dirty && !cell.adoptedAI ? `<b class="ocw-mf-matrix-manual-badge">${ocTranslate("人工调整")}</b>` : (cell.origin === "SAVED" && !cell.adoptedAI ? `<b class="ocw-mf-matrix-manual-badge">${ocTranslate("人工值")}</b>` : (cell.value ? `<b class="ocw-mf-matrix-ai-badge">${ocTranslate("AI／规则")}</b>` : ""))}
      ${suggestion}${cell.warning ? `<em>${this.escape(cell.warning)}</em>` : ""}
    </td>`;
  }

  renderFeeEvidenceMatrixFooter(draft, review) {
    const totals = this.feeEvidenceMatrixTotals(draft, review);
    const render = (label, group) => `<div class="${group.overage ? "is-overage" : ""}"><b>${ocTranslate("{0}已分摊", [label])}</b><span>${this.escape(group.currency || ocTranslate("待选择费用币种"))} ${group.allocatedText}${group.missingFx ? ocTranslate(" · RMB 缺汇率") : ` · RMB ${this.feeEvidencePlainNumber(group.allocatedRmb)}`}</span><small>${ocTranslate("费用总额 {0} · 剩余差额 {1}", [group.feeTotalText, group.remainingText])}</small></div>`;
    return `<div class="ocw-mf-matrix-totals" data-mf-fee-matrix-totals>${render("进口税费", totals.import_tax)}${render("清关费", totals.customs_clearance_fee)}<p>${ocTranslate("确认应用仅写入费用分项，不会自动试算、确认版本或推送 ERP。")}</p></div>`;
  }

  renderFeeEvidenceReviewDraft(draft = {}, review = {}) {
    this.ensureFeeEvidenceMatrixState(review, draft);
    const matrix = this.feeEvidenceMaterialMatrix(draft);
    const columns = this.feeEvidenceMatrixColumns();
    const pageWindow = this.feeEvidenceMatrixPageWindow(draft, review);
    const warningWindow = this.feeEvidenceWarningPageWindow(draft, review);
    const unmatched = warningWindow.rows.slice(warningWindow.start, warningWindow.end);
    const sourceWindow = this.feeEvidenceSourcePageWindow(draft, review);
    const sourceRefs = sourceWindow.refs.slice(sourceWindow.start, sourceWindow.end);
    const rows = matrix.rows.slice(pageWindow.start, pageWindow.end).map((row, pageRowIndex) => {
      const rowIndex = pageWindow.start + pageRowIndex;
      const cells = columns.map((column) => this.renderFeeEvidenceMatrixCell(draft, review, rowIndex, column)).join("");
      const rowCells = columns.map((column) => this.feeEvidenceMatrixEffectiveCell(draft, review, rowIndex, column.key));
      const active = rowCells.filter((cell) => String(cell.value || "").trim());
      const originalCurrencies = [...new Set(active.map((cell) => cell.currency).filter(Boolean))];
      let originalTotal = "";
      try {
        originalTotal = this.feeEvidenceMinorText(active.reduce((sum, cell) => sum + this.feeEvidenceAmountMinor(cell.value), 0n));
      } catch (_error) {
        originalTotal = "待修正";
      }
      const rmbTotal = active.reduce((sum, cell) => cell.missingFx ? sum : sum + (Number(cell.amountRmb) || 0), 0);
      const hasWarning = rowCells.some((cell) => cell.warning);
      const dirty = rowCells.some((cell) => cell.dirty);
      const hsSuggestions = (row.hs_suggestions || []).filter(Boolean);
      return `<tr class="${hasWarning || hsSuggestions.length ? "is-warning" : ""}">
        <th scope="row"><strong>${this.escape(row.material_code || row.item || "--")}</strong><small>${this.escape(row.product_name || row.item || "")}</small></th>
        <td class="ocw-mf-matrix-hs" data-mf-fee-matrix-hs="${this.escape(row.item || "")}"><span>${this.escape(row.hs_code || "--")}</span>${hsSuggestions.length ? `<em>${ocTranslate("HS 差异：{0}", [hsSuggestions.map((value) => this.escape(value)).join("、")])}</em>` : ""}</td>
        ${cells}
        <td class="ocw-mf-matrix-row-total"><strong>${originalCurrencies.length === 1 ? `${this.escape(originalCurrencies[0])} ${originalTotal}` : (active.length ? ocTranslate("多币种") : "--")}</strong><small>RMB ${this.feeEvidencePlainNumber(rmbTotal)}${active.some((cell) => cell.missingFx) ? ocTranslate(" + 缺汇率") : ""}</small></td>
        <td class="ocw-mf-matrix-status"><b>${dirty ? ocTranslate("已人工调整") : (hasWarning || hsSuggestions.length ? ocTranslate("待核对") : ocTranslate("已预览"))}</b><small>${ocTranslate("{0} 个分项", [active.length])}</small></td>
      </tr>`;
    }).join("");
    const warnings = warningWindow.total ? `<aside class="ocw-mf-matrix-unmatched"><b>${ocTranslate("无法归属到具体物料的凭证明细（{0}）", [warningWindow.total])}</b>${unmatched.map((row) => `<p>${this.escape(row.message || row.reason || row.reason_code || ocTranslate("请核对原附件"))}</p>`).join("")}${warningWindow.pageCount > 1 ? `<nav class="ocw-mf-source-pager"><span>${ocTranslate("异常第 {0} / {1} 页", [warningWindow.page + 1, warningWindow.pageCount])}</span><button type="button" data-action="mf-fee-warning-prev-page" ${warningWindow.page === 0 ? "disabled" : ""}>${ocTranslate("上一页")}</button><button type="button" data-action="mf-fee-warning-next-page" ${warningWindow.page >= warningWindow.pageCount - 1 ? "disabled" : ""}>${ocTranslate("下一页")}</button></nav>` : ""}</aside>` : "";
    return `<div class="ocw-mf-fee-review-draft ocw-mf-fee-review-a1">
      ${this.renderFeeEvidenceVoucherDetails(draft, review)}
      ${warnings}
      ${matrix.missing_fx || draft.missing_fx ? `<div class="ocw-mf-review-warning is-block">${ocTranslate("缺汇率：可保存原币事实，RMB 小计不计入试算。")}</div>` : ""}
      <section class="ocw-mf-matrix-section"><header><div><h4>${ocTranslate("物料税费预览")}</h4><p>${ocTranslate("每个物料一行；AI／规则建议已默认纳入，清空单元格即不采用。")}</p></div><nav class="ocw-mf-matrix-pager" aria-label="${ocTranslate('物料分页')}"><span>${ocTranslate("第 {0} / {1} 页 · 共 {2} 行", [pageWindow.page + 1, pageWindow.pageCount, pageWindow.total])}</span><button type="button" data-action="mf-fee-matrix-prev-page" ${pageWindow.page === 0 ? "disabled" : ""}>${ocTranslate("上一页")}</button><button type="button" data-action="mf-fee-matrix-next-page" ${pageWindow.page >= pageWindow.pageCount - 1 ? "disabled" : ""}>${ocTranslate("下一页")}</button></nav></header>
        <div class="ocw-mf-matrix-scroll"><table class="ocw-mf-matrix-table"><thead><tr><th>${ocTranslate("物料 / SKU")}</th><th>HS</th>${columns.map((column) => `<th>${column.label}</th>`).join("")}<th>${ocTranslate("物料合计")}</th><th>${ocTranslate("核对状态")}</th></tr></thead><tbody>${rows || `<tr><td colspan="10">${ocTranslate("当前批次暂无物料")}</td></tr>`}</tbody></table></div>
      </section>
      ${sourceWindow.total ? `<details class="ocw-mf-matrix-sources"><summary>${ocTranslate("来源证据（{0}）", [sourceWindow.total])}</summary><p>${sourceRefs.map((ref) => this.escape(this.feeEvidenceSourceLabel(ref))).filter(Boolean).join("；")}</p>${sourceWindow.pageCount > 1 ? `<nav class="ocw-mf-source-pager"><span>${ocTranslate("来源第 {0} / {1} 页", [sourceWindow.page + 1, sourceWindow.pageCount])}</span><button type="button" data-action="mf-fee-source-prev-page" ${sourceWindow.page === 0 ? "disabled" : ""}>${ocTranslate("上一页")}</button><button type="button" data-action="mf-fee-source-next-page" ${sourceWindow.page >= sourceWindow.pageCount - 1 ? "disabled" : ""}>${ocTranslate("下一页")}</button></nav>` : ""}</details>` : ""}
      ${this.renderFeeEvidenceMatrixFooter(draft, review)}
    </div>`;
  }
