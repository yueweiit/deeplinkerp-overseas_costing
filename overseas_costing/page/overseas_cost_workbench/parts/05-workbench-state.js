(function (root, factory) {
  const api = factory();
  root.OverseasCostWorkbenchState = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window, function () {
  const TASKS = new Set(["pending", "cost", "erp"]);
  const TABS = new Set(["overview", "dingtalk", "documents", "items", "vouchers", "review", "audit"]);
  const TRANSPORT_MODE_ALIASES = Object.freeze({
    SEA: "SEA",
    AIR: "AIR",
    EXPRESS: "EXPRESS",
    "海运": "SEA",
    "海运整柜": "SEA",
    "contenedor marítimo海运整柜": "SEA",
    "contenedor maritimo海运整柜": "SEA",
    "空运": "AIR",
    air: "AIR",
    "correo express快递": "EXPRESS",
    "快递": "EXPRESS",
    express: "EXPRESS",
    OCEAN: "SEA",
    OCEAN_FREIGHT: "SEA",
    SEA_FREIGHT: "SEA",
    MARITIME: "SEA",
    SEA_STANDARD: "SEA",
    SEA_DDP: "SEA",
    AIR_FREIGHT: "AIR",
    COURIER: "EXPRESS",
    DOUBLE_CLEAR: "EXPRESS",
  });
  const MONETARY_FIELDS = new Set([
    "unit_price",
    "unit_price_candidate",
    "goods_value",
    "goods_value_candidate",
    "china_misc_rmb",
    "china_misc_mxn",
    "china_ocean_usd",
    "cc_anti_dumping",
    "igi_amount",
    "iva_amount",
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
    "freight_alloc_rmb",
    "freight_alloc_mxn",
    "total_logistics_mxn",
    "alloc_price_mxn",
    "total_cost_rmb",
    "total_unit_rmb",
    "actual_total_cost_rmb",
    "estimated_total_cost_rmb",
    "total_goods_value",
    "recognized_fee_rmb",
    "logistics_allocated_rmb",
    "logistics_quote_amount",
    "logistics_fee",
    "clearance_fee",
    "clearance_fee_rmb",
    "manual_clearance_fee",
    "manual_tariff_tax",
    "tariff_tax_total",
    "paid_total_mxn",
    "tax_total_sum_mxn",
    "system_tax_total_mxn",
    "system_tax_total",
    "system_import_tax_total_mxn",
    "tax_total_diff_mxn",
    "final_tax_total_mxn",
    "adjusted_tax_total_mxn",
    "igi_amount_mxn",
    "igi_mxn",
    "iva_amount_mxn",
    "iva_mxn",
    "dta_mxn",
    "prv_mxn",
    "prv_iva_mxn",
    "sales_unit_price",
    "sales_amount",
    "sales_amount_rmb",
    "sales_cost_rmb",
    "other_sales_expense_rmb",
    "gross_profit_rmb",
    "profit_rmb",
    "original_unit_price",
    "comprehensive_unit_price",
    "allocated_logistics_cost",
    "allocated_clearance_tax_cost",
    "amount",
    "amount_rmb",
    "allocated_rmb",
    "allocated_mxn",
  ]);
  const VOUCHER_VALIDATION_MONETARY_FIELDS = Object.freeze({
    tax_total_matches_paid: "paid_total_mxn",
    system_tax_total: "system_tax_total",
  });
  const GROUP_RANGES = {
    basic: ["A", "H"],
    purchase: ["I", "P"],
    logistics: ["Q", "Z"],
    tax: ["AA", "AJ"],
    total: ["AK", "BE"],
  };

  function parseWorkbenchState(input) {
    const url = new URL(input, "http://localhost");
    const screen = url.searchParams.get("screen") === "detail" ? "detail" : "workbench";
    const taskValue = url.searchParams.get("task") || "pending";
    const tabValue = url.searchParams.get("tab") || "items";
    return {
      screen,
      batch: String(url.searchParams.get("batch") || ""),
      tab: TABS.has(tabValue) ? tabValue : "items",
      task: TASKS.has(taskValue) ? taskValue : "pending",
      q: String(url.searchParams.get("q") || ""),
      page: Math.max(1, Number.parseInt(url.searchParams.get("page") || "1", 10) || 1),
      issue: String(url.searchParams.get("issue") || ""),
      businessType: String(url.searchParams.get("business_type") || ""),
      subsidiaryCode: String(url.searchParams.get("subsidiary_code") || ""),
      hasDateRange: url.searchParams.has("start_date") || url.searchParams.has("end_date"),
      startDate: String(url.searchParams.get("start_date") || ""),
      endDate: String(url.searchParams.get("end_date") || ""),
      erpStatus: String(url.searchParams.get("erp_status") || ""),
      reviewStatus: url.searchParams.get("review_status") === "confirmed" ? "confirmed" : "pending",
      reviewWarning: ["estimated", "evidence_missing"].includes(url.searchParams.get("review_warning")) ? url.searchParams.get("review_warning") : "",
    };
  }

  function buildWorkbenchUrl(input, nextState) {
    const url = new URL(input, "http://localhost");
    Object.entries(nextState).forEach(([key, value]) => {
      // An explicitly cleared boundary is different from first-load defaults.
      if (["start_date", "end_date"].includes(key) && value === "") {
        url.searchParams.set(key, "");
        return;
      }
      if (value === "" || value === null || value === undefined || value === false) {
        url.searchParams.delete(key);
      } else {
        url.searchParams.set(key, String(value));
      }
    });
    return `${url.pathname}${url.search}${url.hash}`;
  }

  function columnsForGroup(columns, group) {
    if (group === "all" || !GROUP_RANGES[group]) return columns.slice();
    const [start, end] = GROUP_RANGES[group];
    const startIndex = columns.findIndex((column) => column.excel_col === start);
    const endIndex = columns.findIndex((column) => column.excel_col === end);
    if (startIndex < 0 || endIndex < startIndex) {
      return columns.filter((column) => ["material_code", "product_name"].includes(column.fieldname));
    }
    const fixed = new Set(["material_code", "product_name"]);
    return columns.filter(
      (column, index) => fixed.has(column.fieldname) || (index >= startIndex && index <= endIndex)
    );
  }

  function primaryActionForIssue(issue) {
    const actions = {
      purchase: { action: "supplement", label: ocTranslate("补资料") },
      logistics: { action: "supplement", label: ocTranslate("补资料") },
      calculation: { action: "recalculate", label: ocTranslate("重新计算") },
      erp_failed: { action: "erp_retry", label: ocTranslate("处理回写") },
      ready: { action: "view", label: ocTranslate("查看详情") },
    };
    return actions[issue] || actions.ready;
  }

  function detailTabForAction(action) {
    if (action === "supplement") return "documents";
    if (action === "review") return "documents";
    if (action === "review_remediation") return "review";
    if (action === "erp_retry") return "overview";
    return "items";
  }

  function formatNumber(value) {
    if (value === undefined || value === null || value === "") return "";
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    return number.toLocaleString("zh-CN", { maximumFractionDigits: 6 });
  }

  function formatMoney(value) {
    if (value === undefined || value === null || value === "") return "";
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    return number.toLocaleString("zh-CN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function isMonetaryField(fieldname) {
    return MONETARY_FIELDS.has(String(fieldname || ""));
  }

  function formatDisplayNumber(value, fieldname) {
    return isMonetaryField(fieldname) ? formatMoney(value) : formatNumber(value);
  }

  function voucherValidationMonetaryField(code) {
    return VOUCHER_VALIDATION_MONETARY_FIELDS[String(code || "")] || "";
  }

  function formatVoucherValidationNumber(value, code) {
    const monetaryField = voucherValidationMonetaryField(code);
    return monetaryField ? formatDisplayNumber(value, monetaryField) : formatNumber(value);
  }

  function normalizeTransportMode(value) {
    const text = String(value || "").replace("（", "(").replace("）", ")").trim();
    if (!text) return "";
    const lowered = text.toLowerCase();
    const direct = Object.entries(TRANSPORT_MODE_ALIASES).find(([alias]) => alias.toLowerCase() === lowered);
    if (direct) return direct[1];
    if (text.includes("海运") || lowered.includes("marít") || lowered.includes("marit")) return "SEA";
    if (text.includes("空运") || lowered.includes("air")) return "AIR";
    if (text.includes("快递") || lowered.includes("express") || lowered.includes("correo")) return "EXPRESS";
    return text.toUpperCase();
  }

  function resolveManualDocumentLogisticsType(value) {
    const mode = normalizeTransportMode(value);
    return ["SEA", "AIR", "EXPRESS"].includes(mode) ? mode : "";
  }

  function partitionManualDocumentAttachments(items, currentMode) {
    const currentType = resolveManualDocumentLogisticsType(currentMode);
    return (items || []).reduce(
      (groups, item) => {
        const itemType = resolveManualDocumentLogisticsType(item && item.logistics_type);
        if (currentType && itemType === currentType) groups.current.push(item);
        else groups.historical.push(item);
        return groups;
      },
      { current: [], historical: [] }
    );
  }

  function detailTabResource(tab) {
    const resources = {
      overview: "detail",
      dingtalk: "dingtalk",
      documents: "documents",
      items: "items",
      vouchers: "vouchers",
      review: "review",
      audit: "audit",
    };
    return resources[tab] || resources.items;
  }

  function syncModuleSidebar(collapsed, sidebar) {
    const method = collapsed ? "close" : "open";
    if (!sidebar || typeof sidebar[method] !== "function") return false;
    sidebar[method]();
    return true;
  }

  function createPackingFlowState() {
    return {
      step: 1,
      sourceKind: "",
      sourceId: "",
      sheetName: "",
      preview: null,
      resolutions: { groups: {}, totals: {} },
      snapshot: null,
      quote: null,
      calculation: null,
      canCompare: false,
    };
  }

  function selectPackingSource(state, sourceKind, sourceId, sheetName = "") {
    const current = state || createPackingFlowState();
    const kind = String(sourceKind || "");
    const id = String(sourceId || "");
    const sheet = String(sheetName || "");
    if (current.sourceKind === kind && current.sourceId === id && current.sheetName === sheet) {
      return { ...current };
    }
    return {
      ...createPackingFlowState(),
      sourceKind: kind,
      sourceId: id,
      sheetName: sheet,
    };
  }

  function packingFlowCanCompare(preview, resolutions = {}) {
    if (!preview) return false;
    const totals = preview.totals || {};
    const totalDecisions = resolutions.totals || {};
    const resolvedTotalValue = (field) => {
      const total = totals[field] || {};
      const decision = (totalDecisions[field] || {}).action;
      if (decision === "use_declared") return total.declared_value;
      if (decision === "use_calculated") return total.calculated_value;
      return total.value;
    };
    const gross = Number(resolvedTotalValue("gross_weight_kg"));
    const volume = Number(resolvedTotalValue("volume_m3"));
    if (!(gross > 0) || !(volume > 0)) return false;
    const groups = resolutions.groups || {};
    const unresolved = (preview.groups || []).some((group) => {
      if (!group || !group.needs_confirmation) return false;
      const decision = groups[String(group.group_id || "")] || {};
      return !["confirm_shared", "link", "shared", "split", "partition"].includes(String(decision.action || decision));
    });
    if (unresolved) return false;
    return !((preview.validation || {}).blocking || []).some((item) => {
      if (!item || item.code === "group_confirmation_required") return false;
      if (item.code === "conflicting_merge_ranges" && !unresolved) return false;
      if (item.code === "total_mismatch") {
        const decision = totalDecisions[String(item.field || "")] || {};
        return !["use_declared", "use_calculated"].includes(String(decision.action || decision));
      }
      return true;
    });
  }

  function applyPackingPreview(state, preview) {
    const nextPreview = preview ? JSON.parse(JSON.stringify(preview)) : null;
    const next = {
      ...(state || createPackingFlowState()),
      step: nextPreview ? 2 : 1,
      preview: nextPreview,
      resolutions: { groups: {}, totals: {} },
      snapshot: null,
      quote: null,
      calculation: null,
    };
    next.canCompare = packingFlowCanCompare(next.preview, next.resolutions);
    return next;
  }

  function resolvePackageGroup(state, groupId, action) {
    const current = state || createPackingFlowState();
    const next = {
      ...current,
      resolutions: {
        ...(current.resolutions || {}),
        groups: {
          ...((current.resolutions || {}).groups || {}),
          [String(groupId || "")]: { action: String(action || "") },
        },
      },
    };
    next.canCompare = packingFlowCanCompare(next.preview, next.resolutions);
    return next;
  }

  function resolvePackageGroupRows(state, groupId, allRows, selectedRows) {
    const rows = Array.from(new Set((allRows || []).map(Number))).filter(Number.isFinite);
    const selected = Array.from(new Set((selectedRows || []).map(Number))).filter((row) => rows.includes(row));
    if (!selected.length || selected.length === rows.length) {
      return resolvePackageGroup(state, groupId, selected.length === rows.length ? "confirm_shared" : "");
    }
    const selectedSet = new Set(selected);
    const partitions = [selected, ...rows.filter((row) => !selectedSet.has(row)).map((row) => [row])];
    const current = state || createPackingFlowState();
    const next = {
      ...current,
      resolutions: {
        ...(current.resolutions || {}),
        groups: {
          ...((current.resolutions || {}).groups || {}),
          [String(groupId || "")]: { action: "partition", partitions },
        },
      },
    };
    next.canCompare = packingFlowCanCompare(next.preview, next.resolutions);
    return next;
  }

  function resolvePackingTotal(state, field, action) {
    const current = state || createPackingFlowState();
    const next = {
      ...current,
      resolutions: {
        ...(current.resolutions || {}),
        groups: { ...((current.resolutions || {}).groups || {}) },
        totals: {
          ...((current.resolutions || {}).totals || {}),
          [String(field || "")]: { action: String(action || "") },
        },
      },
    };
    next.canCompare = packingFlowCanCompare(next.preview, next.resolutions);
    return next;
  }

  function buildFreightQuotePayload(_state, fields = {}) {
    const optional = (value) => {
      const text = String(value ?? "").trim();
      return text === "" ? undefined : text;
    };
    const basis = (prefix) => {
      const result = {};
      [
        ["unit_price", `${prefix}_unit_price`],
        ["surcharge", `${prefix}_surcharge`],
        ["min_quantity", `${prefix}_min_quantity`],
        ["rounding_increment", `${prefix}_rounding_increment`],
        ["min_base_freight", `${prefix}_min_base_freight`],
      ].forEach(([key, fieldname]) => {
        const value = optional(fields[fieldname]);
        if (value !== undefined) result[key] = value;
      });
      return result;
    };
    return {
      currency: String(fields.currency || "").trim().toUpperCase(),
      scope_confirmed: Boolean(fields.scope_confirmed),
      weight: basis("weight"),
      volume: basis("volume"),
      quote_remark: String(fields.quote_remark || "").trim(),
    };
  }

  function freightQuoteIsReady(quote) {
    if (!quote || quote.scope_confirmed !== true || !String(quote.quote_remark || "").trim()) return false;
    if (!/^[A-Z]{3}$/.test(String(quote.currency || ""))) return false;
    return [quote.weight && quote.weight.unit_price, quote.volume && quote.volume.unit_price].every((value) => {
      if (value === undefined || value === null || String(value).trim() === "") return false;
      const number = Number(value);
      return Number.isFinite(number) && number >= 0;
    });
  }

  return {
    parseWorkbenchState,
    buildWorkbenchUrl,
    columnsForGroup,
    primaryActionForIssue,
    detailTabForAction,
    detailTabResource,
    syncModuleSidebar,
    formatNumber,
    formatMoney,
    isMonetaryField,
    formatDisplayNumber,
    voucherValidationMonetaryField,
    formatVoucherValidationNumber,
    normalizeTransportMode,
    resolveManualDocumentLogisticsType,
    partitionManualDocumentAttachments,
    createPackingFlowState,
    selectPackingSource,
    applyPackingPreview,
    resolvePackageGroup,
    resolvePackageGroupRows,
    resolvePackingTotal,
    buildFreightQuotePayload,
    freightQuoteIsReady,
    packingFlowCanCompare,
    MONETARY_FIELDS,
    TRANSPORT_MODE_ALIASES,
    VOUCHER_VALIDATION_MONETARY_FIELDS,
    GROUP_RANGES,
  };
});
