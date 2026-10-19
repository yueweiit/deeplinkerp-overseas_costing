  hasActiveFilters() {
    return Object.keys(this.filters).some((key) => {
      if (key === "start_date" || key === "end_date") return !this.hasDefaultDateRange();
      return String(this.filters[key] || "").trim() !== "";
    });
  }

  purchasePriceValue(row = {}) {
    return row.adopted_price?.value ?? row.unit_price;
  }

  purchasePriceCurrency(row = {}) {
    return row.adopted_price?.currency || row.purchase_currency || "";
  }

  workRoleInfo(role = this.workRole) {
    if (role === "finance") {
      return {
        key: "finance",
        label: ocTranslate("财务"),
        title: ocTranslate("财务待核算"),
        blockName: ocTranslate("待核算批次"),
        description: ocTranslate("处理数据已完整批次：核对费用、重新试算并确认结果。"),
        empty: ocTranslate("当前没有数据已完整的待核算批次"),
      };
    }
    return {
      key: "purchase",
      label: ocTranslate("采购"),
      title: ocTranslate("采购待补资料"),
      blockName: ocTranslate("待补资料批次"),
      description: ocTranslate("处理数据不完整批次：补齐采购、装箱单和物流资料。"),
      empty: ocTranslate("当前没有数据不完整的待补资料批次"),
    };
  }

  batchDataCompleteness(batch = {}, items = [], hasLoadedItems = false) {
    const sourceStatus = batch.source_status || {};
    const reasons = [];
    const itemRows = hasLoadedItems ? items : [];
    const itemCount = hasLoadedItems ? itemRows.length : Number(batch.item_count || 0);
    const approvalState = String(sourceStatus.purchase_approval_sync_state || "").trim().toLowerCase();

    if (sourceStatus.invalid_business || approvalState === "invalid" || approvalState === "excluded") {
      reasons.push(sourceStatus.has_oa_logistics ? "关联采购审批已排除" : "采购审批无效");
    }
    if (!this.hasText(batch.subsidiary_code)) reasons.push("缺业务主体");
    if (approvalState === "missing") reasons.push("未关联采购审批");
    if (approvalState === "pending") reasons.push("采购审批状态未同步");
    if (!itemCount) reasons.push("缺少物料明细");
    if (hasLoadedItems) {
      const missingItem = itemRows.filter((row) => !this.hasText(row.material_code) || !this.hasText(row.product_name) || !this.isPositive(row.quantity)).length;
      const missingPurchase = itemRows.filter((row) => !this.isPositive(this.purchasePriceValue(row)) || !this.hasText(this.purchasePriceCurrency(row)) || !this.isPositive(row.goods_value)).length;
      const missingShipping = itemRows.filter((row) => !this.isPositive(row.actual_shipped_qty) || !this.isPositive(row.gross_weight_kg)).length;
      if (missingItem) reasons.push(ocTranslate("物料基础字段缺 {0} 行", [missingItem]));
      if (missingPurchase) reasons.push(ocTranslate("采购金额字段缺 {0} 行", [missingPurchase]));
      if (missingShipping) reasons.push(ocTranslate("实际数量/毛重缺 {0} 行", [missingShipping]));
    } else if (itemCount) {
      reasons.push("待展开明细核对");
    }

    return {
      value: reasons.length ? "数据不完整" : "数据已完整",
      hint: reasons.length ? reasons.join("；") : ocTranslate("业务主体、采购审批、物料、采购金额和发货基础数据已具备"),
      className: reasons.length ? "warn" : "ok",
      complete: !reasons.length,
      reasons,
    };
  }

  matchesWorkRole(batch, items) {
    const hasLoadedItems = Object.prototype.hasOwnProperty.call(this.batchItems, batch.name);
    const complete = this.batchDataCompleteness(batch, items, hasLoadedItems).complete;
    return this.workRole === "finance" ? complete : !complete;
  }

  filterBatches({ ignoreTransport = false, ignoreBusinessType = false } = {}) {
    const customs = this.lower(this.filters.customs_no);
    const waybill = this.lower(this.filters.waybill_no);
    const subsidiary = this.lower(this.filters.subsidiary_code);
    const businessType = ignoreBusinessType ? "" : String(this.filters.business_type || "").trim().toUpperCase();
    const calculationStatus = this.lower(this.filters.calculation_status);
    const erpStatus = this.lower(this.filters.erp_status);
    const transportMode = ignoreTransport ? "" : this.normalizeTransportMode(this.filters.transport_mode);
    const itemFilters = ["material_code", "product_name", "import_name", "hs_code", "category"];
    return this.sortBatchesNewestFirst(this.batches.filter((batch) => {
      const items = this.batchItems[batch.name] || [];
      if (!this.matchesWorkRole(batch, items)) return false;
      if (transportMode && this.batchTransportMode(batch, items) !== transportMode) return false;
      if (subsidiary && !this.lower(batch.subsidiary_code).includes(subsidiary)) return false;
      if (businessType && this.batchBusinessType(batch, items) !== businessType) return false;
      if (calculationStatus) {
        const confirmed = String(batch.confirm_status || batch.status || "").toLowerCase().includes("confirmed");
        const statusText = `${batch.status || ""} ${batch.confirm_status || ""} ${this.batchStatusInfo(batch.status, batch, items.length).sourceLabel} ${
          confirmed ? "已确认" : ""
        }`;
        if (!this.lower(statusText).includes(calculationStatus)) return false;
      }
      if (erpStatus && this.erpWritebackQueueKey(batch) !== erpStatus) return false;
      const customsMatched = !customs || this.batchMatchesQuery(batch, items, customs, [
        "customs_no",
        "waybill_no",
        "container_no",
        "batch_no",
        "source_approval_no",
        "source_instance_id",
        "source_dingtalk_url",
        "source_file_name",
      ]);
      const waybillMatched = !waybill || this.batchMatchesQuery(batch, items, waybill, [
        "waybill_no",
        "container_no",
        "sea_bill_no",
        "commercial_invoice_no",
        "batch_no",
        "source_approval_no",
        "source_instance_id",
        "source_dingtalk_url",
      ]);
      if (!customsMatched || !waybillMatched) return false;
      return itemFilters.every((fieldname) => {
        const needle = this.lower(this.filters[fieldname]);
        if (!needle) return true;
        return items.some((item) => this.itemMatchesField(item, fieldname, needle));
      });
    }));
  }

  sortBatchesNewestFirst(batches) {
    return (batches || []).slice().sort((left, right) => this.batchSortTime(right) - this.batchSortTime(left));
  }

  batchSortTime(batch) {
    const value = batch && (batch.source_created_at || batch.creation || batch.modified);
    if (!value) return 0;
    const parsed = Date.parse(String(value).replace(" ", "T"));
    return Number.isFinite(parsed) ? parsed : 0;
  }

  batchMatchesQuery(batch, items, needle, batchFields) {
    if (!needle) return true;
    const sourceStatus = batch.source_status || {};
    const batchValues = [
      ...batchFields.map((fieldname) => batch[fieldname]),
      sourceStatus.source_no,
      sourceStatus.source_approval_status,
    ];
    if (batchValues.some((value) => this.lower(value).includes(needle))) return true;
    return (items || []).some((item) =>
      [
        item.customs_no,
        item.waybill_no,
        item.source_doc_no,
        item.source_file_name,
        item.source_attachment_id,
        item.dingtalk_instance_id,
        item.dingtalk_official_url,
      ].some((value) => this.lower(value).includes(needle))
    );
  }

  itemMatchesField(item, fieldname, needle) {
    const aliases = {
      material_code: ["material_code", "source_doc_no"],
      product_name: ["product_name", "product_name_es", "spec_model"],
      import_name: ["import_name", "product_name", "product_name_es"],
      hs_code: ["hs_code"],
      category: ["category", "project_collection"],
    };
    const fields = aliases[fieldname] || [fieldname];
    return fields.some((name) => this.lower(item[name]).includes(needle));
  }

  batchTransportMode(batch, items = null) {
    const rows = items || this.batchItems[batch.name] || [];
    return this.normalizeTransportMode(batch.transport_mode || (rows[0] || {}).transport_mode);
  }

  batchBusinessType(batch, items = null) {
    const rows = items || this.batchItems[batch.name] || [];
    const raw = batch.business_type || (rows[0] || {}).business_type || "";
    return String(raw || "").trim().toUpperCase();
  }

  businessTypeLabel(value) {
    const labels = {
      SEA_STANDARD: ocTranslate("海运正报正清"),
      SEA_DDP: ocTranslate("海运 DDP（双清包税）"),
      AIR_DDP: ocTranslate("空运 DDP（双清包税）"),
      AIR_STANDARD: ocTranslate("正常空运"),
      EXPRESS: ocTranslate("快递"),
    };
    const key = String(value || "").trim().toUpperCase();
    return labels[key] || String(value || "");
  }

  businessTypeCompactLabel(value) {
    const labels = {
      SEA_STANDARD: ocTranslate("海运正清"),
      SEA_DDP: ocTranslate("海运双清"),
      AIR_DDP: ocTranslate("空运双清"),
      AIR_STANDARD: ocTranslate("正常空运"),
      EXPRESS: ocTranslate("快递"),
    };
    const key = String(value || "").trim().toUpperCase();
    return labels[key] || this.businessTypeLabel(value);
  }

  countVisibleItems() {
    return this.visibleBatches.reduce((total, batch) => total + (this.batchItems[batch.name] || []).length, 0);
  }

  currentBatchLabel() {
    return this.currentBatchNo() ? ocTranslate("{0} 明细", [this.currentBatchNo()]) : ocTranslate("明细");
  }

  currentBatchNo() {
    const row = this.getActiveBatch();
    return row ? row.batch_no || row.name : "";
  }

  batchReferenceLabel(batch) {
    if (!batch) return ocTranslate("未选择批次");
    return batch.customs_no || batch.waybill_no || batch.batch_no || batch.name || ocTranslate("未命名批次");
  }

  voucherBatchHint(batch) {
    return batch
      ? ocTranslate("文件会优先按报关单号或柜号自动匹配；所选批次用于当前对比和查看已保存记录。")
      : ocTranslate("文件会按报关单号或柜号自动尝试匹配批次。");
  }

  renderBatchOptions(selectedBatchName = "") {
    return this.batches
      .map((batch) => {
        const selected = batch.name === selectedBatchName ? " selected" : "";
        return `<option value="${this.escape(batch.name)}"${selected}>${this.escape(this.batchReferenceLabel(batch))}</option>`;
      })
      .join("");
  }

  getSelectableBatches() {
    if (this.hasActiveFilters()) return this.visibleBatches;
    return this.visibleBatches.length ? this.visibleBatches : this.batches;
  }

  getSelectableBatch(batchName = "", batches = null) {
    const options = batches || this.getSelectableBatches();
    if (!options.length) return null;
    return (
      options.find((batch) => batch.name === batchName) ||
      options.find((batch) => batch.name === this.activeBatchName) ||
      options[0] ||
      null
    );
  }

  findSelectableBatch(batchName, batches = null) {
    const options = batches || this.getSelectableBatches();
    return options.find((batch) => batch.name === batchName) || null;
  }

  renderSelectableBatchOptions(selectedBatchName = "", batches = null) {
    const options = batches || this.getSelectableBatches();
    if (!options.length) return `<option value="">${ocTranslate("当前筛选无批次")}</option>`;
    return options
      .map((batch) => {
        const selected = batch.name === selectedBatchName ? " selected" : "";
        return `<option value="${this.escape(batch.name)}"${selected}>${this.escape(this.batchReferenceLabel(batch))}</option>`;
      })
      .join("");
  }

  scopedBatchHint() {
    return this.hasActiveFilters()
      ? ocTranslate("仅显示当前筛选范围内的批次；可输入批次号、报关单号或钉钉审批编号查询历史批次。")
      : ocTranslate("默认显示最近 {0} 天批次；可输入批次号、报关单号或钉钉审批编号查询历史批次。", [this.defaultRecentDays]);
  }

  renderVisibleBatchOptions(selectedBatchName = "") {
    return this.renderSelectableBatchOptions(selectedBatchName, this.visibleBatches);
  }

  getDataCheckBatch() {
    const selectableBatches = this.getSelectableBatches();
    if (!selectableBatches.length) return null;
    return (
      selectableBatches.find((batch) => batch.name === this.dataCheckBatchName) ||
      this.getSelectableBatch(this.activeBatchName, selectableBatches) ||
      null
    );
  }

  renderDataCheckBatchSelector(batch) {
    const $select = this.$root.find("[data-role='data-check-batch-select']");
    if (!$select.length) return;
    const selectableBatches = this.getSelectableBatches();
    const selectedBatchName = batch ? batch.name : "";
    $select.html(this.renderSelectableBatchOptions(selectedBatchName, selectableBatches));
    $select.prop("disabled", !selectableBatches.length);
  }

  getActiveBatch() {
    return this.findBatch(this.activeBatchName) || this.visibleBatches[0] || this.batches[0] || null;
  }

  getVisibleActiveBatch() {
    return (
      this.visibleBatches.find((batch) => batch.name === this.activeBatchName) ||
      this.visibleBatches[0] ||
      this.findBatch(this.activeBatchName) ||
      null
    );
  }

  findBatch(batchName) {
    return this.batches.find((batch) => batch.name === batchName);
  }

  batchUrlKey(batch) {
    if (!batch) return "";
    return String(batch.batch_no || batch.customs_no || batch.waybill_no || batch.name || "").trim();
  }

  findBatchByUrlKey(value) {
    const key = String(value || "").trim();
    if (!key) return null;
    return (
      this.batches.find((batch) => String(batch.batch_no || "").trim() === key) ||
      this.batches.find((batch) => String(batch.customs_no || "").trim() === key) ||
      this.batches.find((batch) => String(batch.waybill_no || "").trim() === key) ||
      this.findBatch(key) ||
      null
    );
  }

  versionLabel(version) {
    if (!version) return "";
    const type = version.version_type || "";
    if (type === "ESTIMATED") return ocTranslate("暂估版");
    if (type === "ACTUAL") return ocTranslate("实际版");
    if (type === "Estimated") return ocTranslate("暂估版");
    if (type === "Actual") return ocTranslate("实际版");
    return version.version_code || type || "";
  }

  normalizeTransportMode(value) {
    return OverseasCostWorkbenchState.normalizeTransportMode(value);
  }

  transportLabel(value) {
    if (!value) return ocTranslate("未指定");
    const labels = {
      SEA: ocTranslate("海运"),
      AIR: ocTranslate("空运"),
      EXPRESS: ocTranslate("快递"),
    };
    const key = this.normalizeTransportMode(value);
    return labels[key] || String(value);
  }

  formatLogisticsTextSummary(summary) {
    if (!summary || typeof summary !== "object") return "";
    const parts = [];
    const quoteAmount = Number(summary.logistics_quote_amount);
    const quoteCurrency = summary.logistics_quote_currency || "RMB";
    const carrier = summary.logistics_quote_carrier || "";
    if (Number.isFinite(quoteAmount) && quoteAmount > 0) {
      parts.push(`${carrier ? `${carrier} ` : ""}${this.formatMoney(quoteAmount)} ${quoteCurrency}`);
    }
    const mode = summary.transport_mode || summary.transport_mode_raw;
    if (this.hasText(mode)) parts.push(this.transportLabel(mode));
    if (this.hasText(summary.pre_delivery_date)) parts.push(ocTranslate("预计 {0}", [summary.pre_delivery_date]));
    if (this.hasText(summary.destination)) parts.push(summary.destination);
    const grossWeight = Number(summary.gross_weight_kg);
    if (Number.isFinite(grossWeight) && grossWeight > 0) parts.push(`${this.formatNumber(grossWeight)} KG`);
    if (!parts.length) return "";
    const suffix = summary.ai_used ? "（AI辅助）" : "";
    return `${parts.join("；")}${suffix}`;
  }

  selectOptionLabel(fieldname, value) {
    if (fieldname === "purchase_currency") return this.currencyLabel(value);
    if (fieldname === "transport_mode") return this.transportLabel(value);
    return String(value || "");
  }

  normalizeSelectValue(fieldname, value) {
    if (fieldname === "purchase_currency") return this.normalizeCurrencyCode(value);
    if (fieldname === "transport_mode") return this.normalizeTransportMode(value);
    return this.normalizeEditorValue(value);
  }

  normalizeCurrencyCode(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    const compact = text.replace(/\s+/g, "").toLowerCase();
    if (compact.includes("rmb") || compact.includes("cny") || compact.includes("人民币")) return "RMB";
    if (compact.includes("usd") || compact.includes("dólar") || compact.includes("dolar") || compact.includes("美元") || compact.includes("美金")) {
      return "USD";
    }
    if (compact.includes("mxn") || compact.includes("peso") || compact.includes("pesos") || compact.includes("比索") || compact.includes("墨西哥")) {
      return "MXN";
    }
    return text.toUpperCase();
  }

  parseJsonObject(value) {
    if (!value) return {};
    if (typeof value === "object") return value && !Array.isArray(value) ? value : {};
    try {
      const parsed = JSON.parse(String(value));
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch (_error) {
      return {};
    }
  }

  currencyLabel(value) {
    if (!value) return "";
    const labels = {
      RMB: ocTranslate("人民币"),
      CNY: ocTranslate("人民币"),
      USD: ocTranslate("美元"),
      MXN: ocTranslate("比索"),
    };
    const code = this.normalizeCurrencyCode(value);
    return labels[code] || String(value);
  }

  batchStatusInfo(status, batch = {}, itemCount = null) {
    const value = String(status || "").trim();
    const normalized = value.toLowerCase();
    const sourceType = String(batch.source_type || "").trim();
    const count = itemCount === null ? Number(batch.item_count || 0) : Number(itemCount || 0);
    if (normalized.includes("imported")) {
      const isOaTraceOnly = sourceType === "oa_logistics" && !count;
      return {
        label: isOaTraceOnly ? ocTranslate("仅拉取审批") : ocTranslate("已导入"),
        sourceLabel: isOaTraceOnly ? "仅拉取审批" : "已导入",
        statusClass: isOaTraceOnly ? "ocw-check-info" : "ocw-check-ok",
        needsRecalculate: false,
        suggestion: isOaTraceOnly ? ocTranslate("已保存审批追溯，后续解析附件后生成明细") : ocTranslate("数据已导入，可继续核对或试算"),
      };
    }
    if (normalized.includes("dirty")) {
      return {
        label: ocTranslate("待重算"),
        sourceLabel: "待重算",
        statusClass: "ocw-check-warn",
        needsRecalculate: true,
        suggestion: ocTranslate("明细已修改，重算后再复核综合成本"),
      };
    }
    if (normalized.includes("calculated")) {
      return {
        label: ocTranslate("已试算"),
        sourceLabel: "已试算",
        statusClass: "ocw-check-ok",
        needsRecalculate: false,
        suggestion: ocTranslate("可继续核对运费/税费/杂费和分摊结果"),
      };
    }
    if (normalized.includes("draft")) {
      return {
        label: ocTranslate("草稿"),
        sourceLabel: "草稿",
        statusClass: "ocw-check-info",
        needsRecalculate: true,
        suggestion: ocTranslate("导入或补数后点击重新试算"),
      };
    }
    return {
      label: value || ocTranslate("待处理"),
      sourceLabel: value || "待处理",
      statusClass: "ocw-check-info",
      needsRecalculate: false,
      suggestion: ocTranslate("继续核对字段完整性"),
    };
  }

  statusClass(status) {
    const value = String(status || "").toLowerCase();
    if (value.includes("calculated")) return "done";
    if (value.includes("dirty")) return "review";
    if (value.includes("draft")) return "review";
    if (value.includes("imported")) return "done";
    if (value.includes("locked")) return "locked";
    return "active";
  }

  columnAlignClass(column) {
    return this.isNumericField(column.fieldname) ? "is-right" : "is-left";
  }

  isEditableColumn(column) {
    return Boolean(column && column.fieldname && !this.readonlyCalcFields.has(column.fieldname));
  }

  columnWidthClass(column, index) {
    if (index === 0) return "ocw-col-code";
    if (index === 1) return "ocw-col-product";
    if (["import_name", "product_name", "spec_model"].includes(column.fieldname)) return "ocw-col-long";
    if (this.isNumericField(column.fieldname)) return "ocw-col-number";
    return "ocw-col-short";
  }

  isNumericField(fieldname) {
    return [
      "unit_price",
      "quantity",
      "goods_value",
      "china_misc_rmb",
      "china_misc_mxn",
      "china_ocean_usd",
      "cc_rate",
      "cc_anti_dumping",
      "igi_rate",
      "igi_amount",
      "iva_rate",
      "iva_amount",
      "goods_value_ratio",
      "dta",
      "prv_duty",
      "prv_iva",
      "import_tax_total",
      "revalidacion",
      "maniobras",
      "muellaje",
      "entrega_mercancia",
      "previo",
      "service_aa",
      "almacenajes",
      "reconocimiento_aduanero",
      "honorarios",
      "complemento_maniobras",
      "desconsolidacion",
      "maniobra_falso",
      "arrastre",
      "patio_regulador",
      "entrega_vacio",
      "limpieza_contenedor",
      "mexico_customs_mxn",
      "mexico_customs_rmb",
      "mexico_customs_usd",
      "mexico_inland_mxn",
      "mexico_misc_mxn",
      "mexico_inland_misc_rmb",
      "china_to_mexico_freight_rmb",
      "gross_weight_kg",
      "weight_ratio",
      "freight_alloc_rmb",
      "freight_alloc_mxn",
      "total_logistics_mxn",
      "alloc_price_mxn",
      "total_cost_rmb",
      "total_unit_rmb",
    ].includes(fieldname);
  }

  firstBatchValue(batchName, fieldname) {
    const items = this.batchItems[batchName] || [];
    const found = items.find((row) => row[fieldname] !== null && row[fieldname] !== undefined && row[fieldname] !== "");
    return found ? found[fieldname] : "";
  }

  sumBatchNumber(batchName, fieldname) {
    const items = this.batchItems[batchName] || [];
    return items.reduce((total, row) => {
      const number = Number(row[fieldname]);
      return Number.isFinite(number) ? total + number : total;
    }, 0);
  }

  sumRowsNumber(rows, fieldname) {
    return (rows || []).reduce((total, row) => {
      const number = Number(row[fieldname]);
      return Number.isFinite(number) ? total + number : total;
    }, 0);
  }

  firstLoadedValue(rows, fieldname) {
    const found = (rows || []).find((row) => this.hasText(row[fieldname]));
    return found ? found[fieldname] : "";
  }

  countRows(rows, predicate) {
    return (rows || []).reduce((total, row) => (predicate(row) ? total + 1 : total), 0);
  }

  describeProblemRows(rows, predicate, limit = 3) {
    const matches = [];
    (rows || []).forEach((row, index) => {
      if (predicate(row)) {
        matches.push(this.itemLocationLabel(row, index));
      }
    });
    if (!matches.length) return "";
    const head = matches.slice(0, limit).join("、");
    const tail = matches.length > limit ? ocTranslate(" 等 {0} 行", [matches.length]) : "";
    return `${head}${tail}`;
  }

  itemLocationLabel(row, index = 0) {
    const rowNo = row.excel_row_no || row.row_no || index + 1;
    const material = this.materialAuditLabel(row);
    return ocTranslate("第 {0} 行 {1}", [rowNo, material]);
  }

  hasText(value) {
    return String(value || "").trim() !== "";
  }

  isPositive(value) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0;
  }

  lower(value) {
    return String(value || "").toLowerCase();
  }

  shouldShowEmptyZeroFee(fieldname, value) {
    if (!["limpieza_contenedor"].includes(fieldname)) return false;
    const number = Number(value);
    return Number.isFinite(number) && number === 0;
  }

  formatCellValue(value, column) {
    if (column.fieldname === "transport_mode") return this.transportLabel(value);
    if (column.fieldname === "purchase_currency") return this.currencyLabel(value);
    if (this.shouldShowEmptyZeroFee(column.fieldname, value)) return "";
    if (this.isNumericField(column.fieldname)) return this.formatDisplayNumber(value, column.fieldname);
    return this.formatValue(value);
  }

  isMonetaryField(fieldname) {
    return OverseasCostWorkbenchState.isMonetaryField(fieldname);
  }

  formatDisplayNumber(value, fieldname) {
    return OverseasCostWorkbenchState.formatDisplayNumber(value, fieldname);
  }

  formatMoney(value) {
    return OverseasCostWorkbenchState.formatMoney(value);
  }

  formatValue(value) {
    if (value === null || value === undefined || value === "") return "";
    if (typeof value === "number") return this.formatNumber(value);
    return String(value);
  }

  formatDateTimeMinute(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    const match = text.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})/);
    if (match) return `${match[1]} ${match[2]}`;
    return text;
  }

  formatAuditValue(value, fieldname = "") {
    if (value === null || value === undefined || value === "") return "空";
    if (typeof value === "object") return JSON.stringify(value);
    const text = String(value);
    const number = Number(text.replace(/,/g, ""));
    if (text.trim() !== "" && Number.isFinite(number) && /^-?\d+(\.\d+)?$/.test(text.replace(/,/g, ""))) {
      return this.formatDisplayNumber(number, fieldname);
    }
    return text;
  }

  formatNumber(value) {
    return OverseasCostWorkbenchState.formatNumber(value);
  }

  normalizeEditorValue(value) {
    if (value === null || value === undefined) return "";
    return String(value).replace(/,/g, "").trim();
  }

  getDefaultPullDateRange() {
    const endDate = new Date();
    const startDate = new Date(endDate);
    startDate.setDate(startDate.getDate() - (this.defaultRecentDays - 1));
    return {
      start_date: this.formatDateInput(startDate),
      end_date: this.formatDateInput(endDate),
    };
  }

  formatDateInput(date) {
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  nowText() {
    const date = new Date();
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  escape(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  showError(error) {
    if (error?.workbenchReleaseHandled || error?.workbenchReleaseBlocked) return;
    const detail = this.extractStructuredError(error);
    const message = this.normalizeErrorMessage(error);
    const rows = detail
      ? [
          ["环节", detail.stage],
          ["范围", detail.scope],
          ["原因", detail.reason],
          ["下一步", detail.next_action],
        ].map(([label, value]) => `<div class="ocw-error-row"><strong>${ocTranslate(label)}</strong><span>${this.escape(value)}</span></div>`).join("")
      : `<div class="ocw-error-row"><strong>${ocTranslate("原因")}</strong><span>${this.escape(message)}</span></div><div class="ocw-error-row"><strong>${ocTranslate("下一步")}</strong><span>${ocTranslate("刷新后重试或查看当前批次的操作记录")}</span></div>`;
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("操作失败"),
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "error_detail",
          options: `
            <div class="ocw-error-dialog">
              <div class="ocw-error-title">${ocTranslate("海外采购综合成本核算")}</div>
              <div class="ocw-error-message">${rows}</div>
            </div>
          `,
        },
      ],
      primary_action_label: ocTranslate("我知道了"),
      primary_action: () => dialog.hide(),
    });
    dialog.show();
    dialog.$wrapper.addClass("ocw-error-modal");
  }

  extractStructuredError(error) {
    const candidates = [
      error?.error,
      error?.message?.error,
      error?.responseJSON?.message?.error,
      error?.xhr?.responseJSON?.message?.error,
    ];
    for (const candidate of candidates) {
      if (candidate && typeof candidate === "object" && candidate.reason) {
        return {
          stage: String(candidate.stage || "操作"),
          scope: String(candidate.scope || "当前范围"),
          reason: String(candidate.reason || "操作失败"),
          next_action: String(candidate.next_action || "刷新后重试或查看操作记录"),
        };
      }
    }
    return null;
  }

  normalizeErrorMessage(error) {
    const raw = this.extractReadableError(error);
    let message = raw ? String(raw) : "操作失败";
    message = message.replace(/^Server Error\s*/i, "").trim() || "操作失败";
    message = message.replace(/^ValueError:\s*/i, "");
    if (message.includes("工作簿中不存在工作表")) {
      message += "\n\n建议：工作表名称可以留空，由系统自动识别；只有一个工作表的文件会自动使用该工作表。";
    }
    return message;
  }

  extractReadableError(error) {
    if (!error) return "";
    if (typeof error === "string") return error;
    if (error.message && typeof error.message === "string" && error.message !== "[object Object]") {
      return error.message;
    }
    const response = error.responseJSON || error.responseText || error.xhr?.responseJSON || error.xhr?.responseText;
    const serverMessage = this.extractServerMessage(response);
    if (serverMessage) return serverMessage;
    if (error._server_messages || error.exception || error.exc || error.statusText) {
      return this.extractServerMessage(error) || error.exception || error.exc || error.statusText;
    }
    try {
      return JSON.stringify(error);
    } catch (_error) {
      return "操作失败";
    }
  }
}
