  renderAllocationOverview(batch, items = []) {
    const rows = this.buildAllocationOverviewRows(batch, items);
    const sourcePrioritySummary = this.getSourcePrioritySummary(batch);
    const body = rows.length
      ? rows
          .map((row) => {
            const rowClass = row.className ? ` ${this.escape(row.className)}` : "";
            return `
              <div class="ocw-allocation-row${rowClass}">
                <div>
                  <strong>${this.escape(row.amountTitle)}</strong>
                  <span>${this.escape(row.amountText)}</span>
                </div>
                <div title="${this.escape(row.source)}">${this.escape(row.source)}</div>
                <div>${this.escape(row.basis)}</div>
                <div>${this.escape(row.result)}</div>
              </div>
            `;
          })
          .join("")
      : `
        <div class="ocw-allocation-empty">
          ${ocTranslate("暂无可填入的费用分摊金额。请先确认物流费、清关费、税费或杂费，然后点击“重新试算”填入 AI/系统基础分摊金额。")}
        </div>
      `;

    return `
      <div class="ocw-allocation-overview">
        <div class="ocw-allocation-title">
          <div>
            <strong>${ocTranslate("AI/系统基础分摊填入")}</strong>
            <span>${ocTranslate("费用池金额 + 费用来源 + 分摊依据 + 分摊结果")}</span>
          </div>
          <button class="ocw-outline-btn ocw-mini-btn" type="button" data-action="manual-logistics-quote" data-batch-name="${this.escape(batch.name)}">${ocTranslate("物流报价/运费补录")}</button>
        </div>
        <div class="ocw-allocation-policy">
          <strong>${ocTranslate("当前口径")}</strong>
          <span>${ocTranslate("已确认关联且审批通过的物流采购支出，按核对后的覆盖范围替换原费用；国际物流报价保留为暂估依据。装箱和汇率核对后再试算，费用按当前规则分摊；{0}", [this.escape(sourcePrioritySummary)])}</span>
        </div>
        <div class="ocw-allocation-grid">
          <div class="ocw-allocation-head">
            <span>${ocTranslate("费用池金额")}</span>
            <span>${ocTranslate("费用来源")}</span>
            <span>${ocTranslate("分摊依据")}</span>
            <span>${ocTranslate("分摊结果")}</span>
          </div>
          ${body}
        </div>
      </div>
    `;
  }

  buildAllocationOverviewRows(batch, items = []) {
    const buckets = new Map();
    items.forEach((item) => {
      const derived = this.parseJsonObject(item.derived_json);
      const rules = Array.isArray(derived.allocated_rules) ? derived.allocated_rules : [];
      rules.forEach((rule) => this.addAllocationRuleBucket(buckets, rule, item, batch));
    });

    const rows = Array.from(buckets.values()).map((bucket) => this.formatAllocationBucket(bucket));
    const customsRow = this.buildDirectCustomsAllocationRow(items);
    if (customsRow) rows.push(customsRow);
    const hasLogisticsRow = rows.some((row) => /物流|运输|运费|freight|logistics/i.test(`${row.amountTitle || ""} ${row.source || ""}`));
    const logisticsQuoteRow = hasLogisticsRow ? null : this.buildLogisticsQuoteAllocationRow(batch);
    if (logisticsQuoteRow) rows.unshift(logisticsQuoteRow);

    if (!rows.length) {
      const freightAlloc = this.sumRowsNumber(items, "freight_alloc_rmb");
      const freightAllocMxn = this.sumRowsNumber(items, "freight_alloc_mxn");
      const coveredRows = this.countRows(items, (row) => this.isPositive(row.freight_alloc_rmb) || this.isPositive(row.freight_alloc_mxn));
      if (this.isPositive(freightAlloc) || this.isPositive(freightAllocMxn)) {
        rows.push({
          amountTitle: "运输费用分摊",
          amountText: this.isPositive(freightAlloc)
            ? `${this.formatMoney(freightAlloc)} RMB`
            : `${this.formatMoney(freightAllocMxn)} MXN`,
          source: "历史试算结果",
          basis: "按已保存规则快照",
          result: ocTranslate("已分摊到 {0} 行物料", [coveredRows || items.length]),
        });
      }
    }

    return rows;
  }

  buildLogisticsQuoteAllocationRow(batch = {}) {
    const sourceStatus = batch.source_status || {};
    const confirmed = sourceStatus.confirmed_logistics_quote || {};
    const confirmedAmount = this.numericOrNull(confirmed.amount);
    if (this.isPositive(confirmedAmount)) {
      const carrier = String(confirmed.carrier || "").trim();
      const currency = this.normalizeCurrencyCode(confirmed.currency || "RMB");
      const basis = confirmed.allocation_basis || confirmed.basis || "gross_weight";
      return {
        amountTitle: "国际运输费用",
        amountText: `${carrier ? `${carrier} ` : ""}${this.formatMoney(confirmedAmount)} ${currency}`,
        source: "钉钉国际物流 OA/已确认报价",
        basis: this.allocationBasisLabel(basis),
        result: "已进入费用池，重新试算后分摊到物料",
        className: "is-confirmed",
      };
    }

    const candidates = Array.isArray(sourceStatus.logistics_quote_candidates) ? sourceStatus.logistics_quote_candidates : [];
    const validCandidates = candidates
      .map((candidate) => ({
        carrier: String(candidate.carrier || "").trim(),
        amount: this.numericOrNull(candidate.amount),
        currency: this.normalizeCurrencyCode(candidate.currency || "RMB"),
      }))
      .filter((candidate) => this.isPositive(candidate.amount));
    if (!validCandidates.length) return null;

    const sorted = validCandidates.slice().sort((a, b) => Number(a.amount || 0) - Number(b.amount || 0));
    const lowest = sorted[0];
    const lowestCount = sorted.filter(
      (candidate) => candidate.currency === lowest.currency && Math.abs(Number(candidate.amount || 0) - Number(lowest.amount || 0)) < 0.000001
    ).length;
    const carrierLabel = lowestCount > 1 ? ocTranslate("{0} 家最低", [lowestCount]) : lowest.carrier || "最低报价";
    return {
      amountTitle: "国际运输费用候选",
      amountText: ocTranslate("{0} {1} {2}；共 {3} 份待确认", [carrierLabel, this.formatMoney(lowest.amount), lowest.currency, validCandidates.length]),
      source: "钉钉国际物流 OA 报价文本/附件",
      basis: "待人工确认后进入费用池",
      result: "确认报价并重新试算后分摊到物料",
      className: "is-pending",
    };
  }

  getSourcePrioritySummary(batch = {}) {
    const sourceStatus = batch.source_status || {};
    const summary = batch.summary_snapshot || {};
    const policy = sourceStatus.source_priority_policy || summary.source_priority_policy || {};
    return (
      sourceStatus.source_priority_summary ||
      policy.short_summary ||
      "税费听完税凭证；采购价听采购支出 OA；物流结算覆盖费用听最终采购支出 OA；附件和 OCR 只做补充；人工调整保留记录。"
    );
  }

  addAllocationRuleBucket(buckets, rule = {}, item = {}, batch = {}) {
    const ruleCode = String(rule.rule_code || rule.fee_key || "").trim();
    const basis = String(rule.basis || rule.allocation_basis || rule.basis_label || "goods_value").trim();
    const currency = this.normalizeCurrencyCode(rule.currency || "RMB");
    const amount = this.numericOrNull(rule.amount);
    const amountKey = amount === null ? "allocated" : String(amount);
    const key = [ruleCode || "未命名费用", basis, currency, amountKey].join("|");
    const allocatedRmb = Number(this.numericOrNull(rule.allocated_rmb) || 0);
    const allocatedMxn = Number(this.numericOrNull(rule.allocated_mxn) || 0);
    if (!allocatedRmb && !allocatedMxn && amount === null) return;

    if (!buckets.has(key)) {
      buckets.set(key, {
        ruleCode,
        feeName: this.allocationFeeLabel(rule),
        basis,
        currency,
        amount,
        amountRmb: this.numericOrNull(rule.amount_rmb),
        source: this.allocationSourceLabel(rule, batch),
        allocatedRmb: 0,
        allocatedMxn: 0,
        coveredRows: new Set(),
      });
    }
    const bucket = buckets.get(key);
    bucket.allocatedRmb += allocatedRmb;
    bucket.allocatedMxn += allocatedMxn;
    if (item.name || item.row_no) bucket.coveredRows.add(item.name || item.row_no);
  }

  formatAllocationBucket(bucket) {
    let amountText = "--";
    if (bucket.amount !== null && bucket.amount !== undefined) {
      if (bucket.currency !== "RMB" && this.isPositive(bucket.amountRmb)) {
        amountText = ocTranslate("原币 {0} {1}，折合 {2} RMB", [this.formatMoney(bucket.amount), bucket.currency || "RMB", this.formatMoney(bucket.amountRmb)]);
      } else {
        amountText = `${this.formatMoney(bucket.amount)} ${bucket.currency || "RMB"}`;
      }
    } else if (this.isPositive(bucket.allocatedRmb)) {
      amountText = ocTranslate("{0} RMB（按分摊汇总）", [this.formatMoney(bucket.allocatedRmb)]);
    } else if (this.isPositive(bucket.allocatedMxn)) {
      amountText = ocTranslate("{0} MXN（按分摊汇总）", [this.formatMoney(bucket.allocatedMxn)]);
    }

    const resultParts = [];
    if (this.isPositive(bucket.allocatedRmb)) resultParts.push(`${this.formatMoney(bucket.allocatedRmb)} RMB`);
    if (this.isPositive(bucket.allocatedMxn)) resultParts.push(`${this.formatMoney(bucket.allocatedMxn)} MXN`);
    const coveredCount = bucket.coveredRows && bucket.coveredRows.size ? bucket.coveredRows.size : 0;
    if (coveredCount) resultParts.push(ocTranslate("覆盖 {0} 行", [coveredCount]));

    return {
      amountTitle: bucket.feeName || "费用池",
      amountText,
      source: bucket.source || "明细字段/系统规则",
      basis: this.allocationBasisLabel(bucket.basis),
      result: resultParts.length ? resultParts.join("，") : "待试算",
    };
  }

  buildDirectCustomsAllocationRow(items = []) {
    const customsRmb = this.sumRowsNumber(items, "mexico_customs_rmb");
    const customsMxn = this.sumRowsNumber(items, "mexico_customs_mxn");
    if (!this.isPositive(customsRmb) && !this.isPositive(customsMxn)) return null;
    const amountParts = [];
    if (this.isPositive(customsMxn)) amountParts.push(`${this.formatMoney(customsMxn)} MXN`);
    if (this.isPositive(customsRmb)) amountParts.push(`${this.formatMoney(customsRmb)} RMB`);
    const coveredRows = this.countRows(items, (row) => this.isPositive(row.mexico_customs_rmb) || this.isPositive(row.mexico_customs_mxn));
    return {
      amountTitle: "清关/税费",
      amountText: amountParts.join("，"),
      source: "完税凭证、清关资料或明细字段",
      basis: "已匹配到物料行",
      result: ocTranslate("计入 {0} 行物料综合成本", [coveredRows || items.length]),
    };
  }

  allocationFeeLabel(rule = {}) {
    const explicit = String(rule.expense_category || "").trim();
    const code = String(rule.rule_code || rule.fee_key || "").trim();
    const text = `${explicit} ${code}`.toLowerCase();
    if (text.includes("oa_logistics") || text.includes("freight") || text.includes("ocean") || text.includes("运费")) {
      return explicit && !explicit.toLowerCase().includes("freight") ? explicit : ocTranslate("国际运输费用");
    }
    if (code === "china_misc_rmb" || text.includes("china misc")) return ocTranslate("中国段杂费");
    if (code === "mexico_inland_misc_rmb" || text.includes("mexico inland")) return ocTranslate("墨西哥内陆/杂费");
    return explicit || code || ocTranslate("费用池");
  }

  allocationSourceLabel(rule = {}, batch = {}) {
    const remark = String(rule.remark || "").trim();
    const code = String(rule.rule_code || rule.fee_key || "").toLowerCase();
    const sourceStatus = batch.source_status || {};
    if (rule.source_binding_id && Number(rule.is_final) === 1) return ocTranslate("最终物流采购支出 OA");
    if (remark.includes("AI") || rule.is_ai_suggestion) return ocTranslate("AI基础分摊/待人工复核");
    if (remark.includes("钉钉") || code.includes("oa_logistics")) return ocTranslate("钉钉国际物流 OA");
    if ((sourceStatus.confirmed_logistics_quote || {}).amount && (code.includes("freight") || code.includes("logistics"))) {
      return ocTranslate("钉钉国际物流 OA/已确认物流报价");
    }
    if (remark.includes("货代") || code.includes("freight") || code.includes("ocean")) return ocTranslate("国际物流 OA/货代账单");
    if (remark.includes("清关") || remark.includes("墨西哥")) return ocTranslate("清关资料/墨西哥费用资料");
    if (remark.includes("Excel") || remark.includes("明细字段")) return ocTranslate("Excel/OA 明细字段");
    return ocTranslate("明细字段/系统规则");
  }

  allocationBasisLabel(value = "") {
    const basis = String(value || "").trim();
    const labels = {
      goods_value: ocTranslate("按货值比例分摊"),
      gross_weight: ocTranslate("按毛重比例分摊"),
      volume: ocTranslate("按体积比例分摊"),
      chargeable_weight: ocTranslate("按计费重比例分摊"),
      chargeable_weight_kg: ocTranslate("按计费重比例分摊"),
    };
    return labels[basis] || (basis ? ocTranslate("按 {0} 分摊", [basis]) : ocTranslate("按规则分摊"));
  }

  renderChildRow(batch) {
    const items = this.batchItems[batch.name] || [];
    return `
      <tr class="ocw-child-row">
        <td colspan="11">
          <div class="ocw-child-table-shell">
            <div class="ocw-child-table-toolbar">
              <span>${ocTranslate("SKU 成本分摊明细 / 物料详情 · {0} 行", [items.length])}</span>
              <button class="ocw-outline-btn ocw-mini-btn ocw-add-material-sticky" data-action="add-material" data-batch-name="${this.escape(batch.name)}">${ocTranslate("+ 添加新物料")}</button>
            </div>
            ${this.renderAllocationOverview(batch, items)}
            ${this.renderChildTable(batch)}
            <div class="ocw-child-table-x-scroll" data-role="child-table-x-scroll" data-batch-name="${this.escape(batch.name)}" aria-label="${ocTranslate('SKU 明细横向滚动条')}">
              <div class="ocw-child-table-x-scroll-spacer" data-role="child-table-x-scroll-spacer"></div>
            </div>
          </div>
        </td>
      </tr>
    `;
  }

  renderChildTable(batch) {
    const columns = this.getChildDisplayColumns(this.batchColumns || []);
    const items = this.batchItems[batch.name] || [];
    if (!columns.length || !items.length) {
      return `<div class="ocw-muted">${ocTranslate("该报关/运单块暂无 SKU 明细")}</div>`;
    }
    const colgroup = columns
      .map((column, index) => `<col class="${this.escape(this.columnWidthClass(column, index))}" />`)
      .join("");
    const head = columns
      .map((column, index) => {
        const sticky = index < 2 ? `ocw-sticky-head ocw-sticky-${index}` : "";
        return `
          <th class="${sticky} ${this.escape(this.columnAlignClass(column))} notranslate" translate="no" title="${this.escape(column.excel_col + " " + column.label)}">
            ${this.renderHeaderCell(column)}
          </th>
        `;
      })
      .join("");
    const body = items
      .map((row) => {
        const cells = columns
          .map((column, index) => {
            const sticky = index < 2 ? `ocw-sticky-cell ocw-sticky-${index}` : "";
            const editable = this.isEditableColumn(column);
            const rawValue = this.shouldShowEmptyZeroFee(column.fieldname, row[column.fieldname])
              ? ""
              : this.normalizeEditorValue(row[column.fieldname]);
            const displayValue = this.formatCellValue(row[column.fieldname], column);
            return `
              <td
                class="${sticky} ${this.escape(this.columnAlignClass(column))} ${editable ? "ocw-editable-cell" : "ocw-readonly-cell"}"
                title="${this.escape(displayValue || "")}"
                data-editable-cell="${editable ? "1" : "0"}"
                data-batch-name="${this.escape(batch.name)}"
                data-item-name="${this.escape(row.name || "")}"
                data-version-name="${this.escape(row.version || batch.current_version || "")}"
                data-fieldname="${this.escape(column.fieldname)}"
                data-field-label="${this.escape(ocTranslate(column.label))}"
                data-raw-value="${this.escape(rawValue)}"
                data-special-override="${this.specialOverrideFields.has(column.fieldname) ? "1" : "0"}"
              >
                ${this.renderCell(row[column.fieldname], column)}
              </td>
            `;
          })
          .join("");
        const itemLabel = row.product_name || row.material_code || row.name || "未命名物料";
        return `
          <tr>
            ${cells}
            <td class="ocw-row-action-cell">
              <button
                class="ocw-danger-btn ocw-mini-btn"
                data-action="delete-material"
                data-batch-name="${this.escape(batch.name)}"
                data-item-name="${this.escape(row.name || "")}"
                data-item-label="${this.escape(itemLabel)}"
              >${ocTranslate("删除")}</button>
            </td>
          </tr>
        `;
      })
      .join("");

    return `
      <div class="ocw-child-table-head-scroll" data-role="child-table-head-scroll" data-batch-name="${this.escape(batch.name)}">
        <table class="ocw-child-sku-table ocw-child-sku-head-table notranslate" translate="no">
          <colgroup>${colgroup}<col class="ocw-col-row-action" /></colgroup>
          <thead><tr>${head}<th class="ocw-row-action-head">${ocTranslate("操作")}</th></tr></thead>
        </table>
      </div>
      <div class="ocw-child-table-scroll" data-role="child-table-scroll" data-batch-name="${this.escape(batch.name)}">
        <table class="ocw-child-sku-table notranslate" translate="no">
          <colgroup>${colgroup}<col class="ocw-col-row-action" /></colgroup>
          <tbody>${body}</tbody>
        </table>
      </div>
    `;
  }

  renderHeaderCell(column) {
    const parts = this.splitHeaderLabel(column.label);
    const secondary = parts.secondary ? `<span class="ocw-sku-header-secondary">${this.escape(parts.secondary)}</span>` : "";
    return `
      <div class="ocw-sku-header-cell notranslate" translate="no">
        <span class="ocw-sku-header-code notranslate" translate="no">${this.escape(column.excel_col)}</span>
        <span class="ocw-sku-header-primary notranslate" translate="no">${this.escape(parts.primary)}</span>
        ${secondary}
      </div>
    `;
  }

  renderCell(value, column) {
    if (value === null || value === undefined || value === "" || this.shouldShowEmptyZeroFee(column.fieldname, value)) {
      return `<span class="ocw-table-display ocw-table-empty-value">--</span>`;
    }
    const formatted = this.formatCellValue(value, column);
    const highlighted = this.highlightText(formatted, this.filterTermsForColumn(column.fieldname));
    return `<span class="ocw-table-display">${highlighted}</span>`;
  }

  renderParentValue(value, fieldname) {
    return this.highlightText(this.formatValue(value || "--"), this.filterTermsForColumn(fieldname));
  }

  bindHierarchyScrollbars() {
    (this.hierarchyScrollCleanups || []).forEach((cleanup) => cleanup());
    this.hierarchyScrollCleanups = [];
    const bind = ($source, $bar) => {
      if (!$source.length || !$bar.length) return;
      const cleanup = this.bindHorizontalScrollController({
        content: $source.get(0),
        header: $source.prev("[data-role='child-table-head-scroll']").get(0),
        scrollbar: $bar.get(0),
        spacer: $bar.find("[data-role$='scroll-spacer']").get(0),
        onInteraction: () => this.positionChildScrollbars(),
        onRefresh: () => this.positionChildScrollbars(),
      });
      this.hierarchyScrollCleanups.push(cleanup);
    };

    const $hierarchyWrap = this.$root.find("[data-area='table']");
    bind($hierarchyWrap, this.$root.find("[data-role='hierarchy-x-scroll']"));
    this.$root.find("[data-role='child-table-scroll']").each((_, element) => {
      const $source = $(element);
      bind($source, $source.next("[data-role='child-table-x-scroll']"));
    });
    this.positionChildScrollbars();
    $hierarchyWrap
      .off("scroll.ocwChildScrollbarPosition")
      .on("scroll.ocwChildScrollbarPosition", () => {
        window.requestAnimationFrame(() => this.positionChildScrollbars());
      });
    $(window)
      .off("resize.ocwHierarchyScrollbars")
      .on("resize.ocwHierarchyScrollbars", () => {
        window.requestAnimationFrame(() => this.positionChildScrollbars());
      });
  }

  positionChildScrollbars() {
    const $wrap = this.$root.find("[data-area='table']");
    if (!$wrap.length) return;
    const wrap = $wrap.get(0);
    const wrapRect = wrap.getBoundingClientRect();
    const hierarchyBarHeight = this.$root.find("[data-role='hierarchy-x-scroll']").not(".is-hidden").outerHeight() || 0;
    const visibleTop = wrapRect.top;
    const visibleBottom = wrapRect.bottom - hierarchyBarHeight;

    this.$root.find("[data-role='child-table-x-scroll']").each((_, barElement) => {
      const $bar = $(barElement);
      const $shell = $bar.closest(".ocw-child-table-shell");
      const $source = $bar.prev("[data-role='child-table-scroll']");
      if (!$shell.length || !$source.length) {
        $bar.addClass("is-hidden");
        return;
      }
      const shell = $shell.get(0);
      const source = $source.get(0);
      const shellRect = shell.getBoundingClientRect();
      const visibleHeight = Math.min(shellRect.bottom, visibleBottom) - Math.max(shellRect.top, visibleTop);
      const hasHorizontalScroll = source.scrollWidth > source.clientWidth + 1;
      if (!hasHorizontalScroll || visibleHeight < 64) {
        $bar.addClass("is-hidden");
        return;
      }
      const barHeight = barElement.offsetHeight || 18;
      const maxTop = Math.max(0, shell.offsetHeight - barHeight);
      const nextTop = Math.max(0, Math.min(visibleBottom - shellRect.top - barHeight, maxTop));
      barElement.style.top = `${nextTop}px`;
      $bar.removeClass("is-hidden");
    });
  }
