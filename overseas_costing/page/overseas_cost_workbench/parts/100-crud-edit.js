  async setAllExpanded(expanded) {
    const displayBatches = this.getDisplayedBatches();
    if (expanded) {
      await this.prefetchBatchItems(displayBatches);
      this.expandedBatchNames = new Set(displayBatches.map((batch) => batch.name));
      this.addAudit("人工", "manual", "全部展开");
    } else {
      this.expandedBatchNames.clear();
      this.addAudit("系统", "system", "全部收起");
    }
    this.renderTable();
  }

  openAddBatchDialog() {
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("添加报关运单"),
      fields: [
        { fieldtype: "Data", fieldname: "batch_no", label: ocTranslate("批次号/来源单号"), reqd: 1 },
        { fieldtype: "Data", fieldname: "customs_no", label: ocTranslate("报关单号") },
        { fieldtype: "Data", fieldname: "waybill_no", label: ocTranslate("运单号/物流单号") },
        { fieldtype: "Data", fieldname: "container_no", label: ocTranslate("柜号") },
        { fieldtype: "Select", fieldname: "transport_mode", label: ocTranslate("运输方式"), options: "海运\n空运\n快递", default: "海运" },
        {
          fieldtype: "Select",
          fieldname: "business_type",
          label: ocTranslate("业务类型"),
          options: "海运正报正清\n海运 DDP（双清包税）\n空运 DDP（双清包税）\n正常空运\n快递",
          default: "海运正报正清",
        },
        { fieldtype: "Data", fieldname: "project_collection", label: ocTranslate("项目归集") },
        { fieldtype: "Data", fieldname: "source_approval_no", label: ocTranslate("钉钉审批编号") },
        { fieldtype: "Data", fieldname: "source_instance_id", label: ocTranslate("钉钉实例ID（procInstId）") },
        { fieldtype: "Small Text", fieldname: "source_dingtalk_url", label: ocTranslate("钉钉审批链接") },
        { fieldtype: "Small Text", fieldname: "source_remark", label: ocTranslate("备注") },
      ],
      primary_action_label: ocTranslate("确认新增"),
      primary_action: (values) => {
        const batchPayload = {
          ...values,
          batch_no: String(values.batch_no || "").trim(),
          customs_no: String(values.customs_no || "").trim(),
          waybill_no: String(values.waybill_no || "").trim(),
          container_no: String(values.container_no || "").trim(),
          source_instance_id: String(values.source_instance_id || "").trim(),
          source_dingtalk_url: String(values.source_dingtalk_url || "").trim(),
        };
        const label = batchPayload.customs_no || batchPayload.waybill_no || batchPayload.batch_no;
        frappe.confirm(
          `
            <div class="ocw-confirm-copy">
              <h4>${ocTranslate("确认新增报关运单？")}</h4>
              <p>${ocTranslate("将新增「{0}」空白批次。", [this.escape(label || ocTranslate("未命名批次"))])}</p>
              <div class="ocw-confirm-note">${ocTranslate("新增后可继续添加物料或导入附件补数。")}</div>
            </div>
          `,
          async () => {
            const created = await this.createBatch(batchPayload);
            if (created) dialog.hide();
          }
        );
      },
    });
    dialog.show();
  }

  async createBatch(batchPayload) {
    try {
      const result = await this.call(
        "overseas_costing.api.batch.create_batch",
        {
          batch_payload: JSON.stringify(batchPayload),
        },
        true
      );
      if (!result.ok) throw new Error(result.message || "新增报关运单失败");
      this.resetFilterValues();
      this.activeBatchName = result.batch_name || "";
      await this.loadBatches();
      if (result.batch_name) {
        const batch = this.findBatch(result.batch_name);
        await this.loadBatchItems(result.batch_name, batch ? batch.current_version : result.version_name, true);
        this.expandedBatchNames.add(result.batch_name);
        this.renderTable();
        this.updateSearchResult();
      }
      frappe.show_alert({ message: result.message || ocTranslate("报关运单已新增"), indicator: "green" });
      return true;
    } catch (error) {
      this.showError(error);
      return false;
    }
  }

  openAddMaterialDialog(batchName) {
    const batch = this.findBatch(batchName);
    if (!batch) return;
    this.activeBatchName = batch.name;
    this.exportPinnedBatchName = batch.name;
    const dialog = new frappe.ui.Dialog({
      title: ocTranslate("添加新物料"),
      fields: [
        { fieldtype: "Data", fieldname: "material_code", label: ocTranslate("物料编码"), reqd: 1 },
        { fieldtype: "Data", fieldname: "product_name", label: ocTranslate("物料名称（中文）"), reqd: 1 },
        { fieldtype: "Data", fieldname: "product_name_es", label: ocTranslate("物料名称（西语）") },
        { fieldtype: "Data", fieldname: "spec_model", label: ocTranslate("规格型号 Especificación / Modelo") },
        { fieldtype: "Float", fieldname: "unit_price", label: ocTranslate("采购单价"), default: 0 },
        { fieldtype: "Float", fieldname: "quantity", label: ocTranslate("采购数量"), default: 1 },
        { fieldtype: "Data", fieldname: "unit", label: ocTranslate("单位") },
        { fieldtype: "Data", fieldname: "recipient", label: ocTranslate("收件人") },
        { fieldtype: "Data", fieldname: "import_name", label: ocTranslate("海关进口名称") },
        { fieldtype: "Data", fieldname: "hs_code", label: ocTranslate("海关分类编码") },
        { fieldtype: "Data", fieldname: "category", label: ocTranslate("大类分类") },
      ],
      primary_action_label: ocTranslate("确认新增"),
      primary_action: (values) => {
        const itemPayload = {
          ...values,
          transport_mode: batch.transport_mode || "SEA",
          customs_no: batch.customs_no || "",
          waybill_no: batch.waybill_no || "",
          goods_value: Number(values.unit_price || 0) * Number(values.quantity || 0),
        };
        frappe.confirm(
          `
            <div class="ocw-confirm-copy">
              <h4>${ocTranslate("确认新增物料？")}</h4>
              <p>${ocTranslate("将在 {0} 下新增「{1}」。", [this.escape(batch.waybill_no || batch.batch_no || batch.name), this.escape(values.product_name || values.material_code)])}</p>
            </div>
          `,
          async () => {
            const created = await this.createMaterial(batch, itemPayload);
            if (created) dialog.hide();
          }
        );
      },
    });
    dialog.show();
  }

  async createMaterial(batch, itemPayload) {
    let acquired = null;
    try {
      const inDetail = this.detailState?.batchName === batch.name;
      if (inDetail && !(await this.ensureMaterialFeeEditSession())) return false;
      if (!inDetail) {
        acquired = await this.call("overseas_costing.api.edit_session.acquire", { batch_name: batch.name });
        if (!acquired?.ok) throw new Error(acquired?.message || "无法获取编辑权。");
      }
      const result = await this.call(
        "overseas_costing.api.calculate.create_item",
        {
          batch_name: batch.name,
          version_name: inDetail ? this.detailState.versionName : batch.current_version,
          item_payload: JSON.stringify(itemPayload),
          remark: "前端添加新物料",
          edit_token: inDetail ? this.detailState.editToken : acquired.edit_token,
          expected_modified: inDetail ? this.detailState.expectedModified : acquired.modified,
        },
        true
      );
      if (!result.ok) throw new Error(result.message || "新增物料失败");
      if (inDetail) this.updateMaterialFeeExpectedModified(result);
      if (inDetail) this.materialFeeState?.packingGroupSelections?.clear();
      this.markBatchDirty(batch.name);
      this.resetFilterValues();
      if (inDetail && this.detailState.tab === "documents") await this.loadMaterialFeeWorkspace({ quiet: true });
      else await this.loadBatchItems(batch.name, batch.current_version, true);
      await this.loadAuditLogs(batch.name, inDetail ? this.detailState.versionName : batch.current_version);
      this.expandedBatchNames.add(batch.name);
      this.renderTable();
      this.updateSearchResult();
      frappe.show_alert({ message: result.message || ocTranslate("物料已新增"), indicator: "green" });
      return true;
    } catch (error) {
      this.showError(error);
      return false;
    } finally {
      if (acquired?.edit_token) {
        await this.call("overseas_costing.api.edit_session.release", { batch_name: batch.name, edit_token: acquired.edit_token }).catch(() => {});
      }
    }
  }

  confirmDeleteMaterial(batchName, itemName, itemLabel) {
    const batch = this.findBatch(batchName);
    if (!batch || !itemName) return;
    frappe.confirm(
      `
        <div class="ocw-confirm-copy">
          <h4>${ocTranslate("确认排除物料？")}</h4>
          <p>${ocTranslate("将从 {0} 的当前表中排除：{1}。", [this.escape(batch.waybill_no || batch.batch_no || batch.name), this.escape(itemLabel || itemName)])}</p>
          <div class="ocw-confirm-note">${ocTranslate("该操作不会物理删除来源和历史，可从“已排除物料”恢复。")}</div>
        </div>
      `,
      async () => {
        await this.deleteMaterial(batch, itemName, itemLabel);
      }
    );
  }

  async deleteMaterial(batch, itemName, itemLabel) {
    let acquired = null;
    try {
      const inDetail = this.detailState?.batchName === batch.name;
      if (inDetail && !(await this.ensureMaterialFeeEditSession())) return;
      if (!inDetail) {
        acquired = await this.call("overseas_costing.api.edit_session.acquire", { batch_name: batch.name });
        if (!acquired?.ok) throw new Error(acquired?.message || "无法获取编辑权。");
      }
      const result = await this.call(
        "overseas_costing.api.calculate.delete_item",
        {
          item_name: itemName,
          batch_name: batch.name,
          version_name: inDetail ? this.detailState.versionName : batch.current_version,
          remark: ocTranslate("前端软排除物料：{0}", [itemLabel || itemName]),
          edit_token: inDetail ? this.detailState.editToken : acquired.edit_token,
          expected_modified: inDetail ? this.detailState.expectedModified : acquired.modified,
        },
        true
      );
      if (!result.ok) throw new Error(result.message || "排除物料失败");
      if (inDetail) this.updateMaterialFeeExpectedModified(result);
      this.markBatchDirty(batch.name);
      if (inDetail && this.detailState.tab === "documents") await this.loadMaterialFeeWorkspace({ quiet: true });
      else await this.loadBatchItems(batch.name, batch.current_version, true);
      await this.loadAuditLogs(batch.name, inDetail ? this.detailState.versionName : batch.current_version);
      this.expandedBatchNames.add(batch.name);
      this.renderTable();
      frappe.show_alert({ message: result.message || ocTranslate("物料已排除，可恢复"), indicator: "green" });
    } catch (error) {
      this.showError(error);
    } finally {
      if (acquired?.edit_token) {
        await this.call("overseas_costing.api.edit_session.release", { batch_name: batch.name, edit_token: acquired.edit_token }).catch(() => {});
      }
    }
  }

  async startCellEdit($cell, event = null, autoOpenSelect = false) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    if (!$cell.length || $cell.data("saving") || $cell.hasClass("is-editing")) return;
    if ($cell.attr("data-editable-cell") !== "1") return;
    if (this.detailState && this.detailState.batchName) {
      try {
        if (!(await this.ensureEditSession())) return;
      } catch (error) {
        this.showError(error);
        return;
      }
      this.detailState.dirty = true;
    }

    const fieldname = $cell.attr("data-fieldname");
    const oldValue = $cell.attr("data-raw-value") || "";
    const label = $cell.attr("data-field-label") || fieldname;
    const isNumeric = this.isNumericField(fieldname);
    const options = this.selectOptions[fieldname] || null;
    const selectedValue = options ? this.normalizeSelectValue(fieldname, oldValue) : oldValue;

    $cell.removeData("cancelled saving");
    $cell.data("original-html", $cell.html());
    $cell.addClass("is-editing");

    if (options) {
      const hasSelectedOption = options.some((option) => String(option) === selectedValue);
      const emptyOption = selectedValue ? "" : `<option value="" selected>${ocTranslate("请选择")}</option>`;
      const currentOption =
        selectedValue && !hasSelectedOption
          ? `<option value="${this.escape(selectedValue)}" selected>${this.escape(this.selectOptionLabel(fieldname, selectedValue))}</option>`
          : "";
      const optionHtml = options
        .map(
          (option) =>
            `<option value="${this.escape(option)}" ${String(option) === selectedValue ? "selected" : ""}>${this.escape(this.selectOptionLabel(fieldname, option))}</option>`
        )
        .join("");
      $cell.html(`<select class="ocw-cell-editor ocw-cell-select" aria-label="${this.escape(ocTranslate(label))}">${emptyOption}${currentOption}${optionHtml}</select>`);
    } else {
      $cell.html(`
        <input
          class="ocw-cell-editor"
          aria-label="${this.escape(ocTranslate(label))}"
          value="${this.escape(oldValue)}"
          ${isNumeric ? 'inputmode="decimal"' : ""}
        />
      `);
    }

    const editor = $cell.find(".ocw-cell-editor").get(0);
    if (editor) {
      if (autoOpenSelect && options && typeof editor.showPicker === "function") {
        editor.focus();
        try {
          editor.showPicker();
          return;
        } catch (error) {
          // Some browsers only allow showPicker in stricter user-gesture windows.
        }
      }
      window.requestAnimationFrame(() => {
        editor.focus();
        if (editor.setSelectionRange && editor.value !== undefined) {
          const pos = String(editor.value).length;
          editor.setSelectionRange(pos, pos);
        }
      });
    }
  }

  cancelCellEdit($cell) {
    if (!$cell.length || !$cell.hasClass("is-editing")) return;
    $cell.data("cancelled", true);
    $cell.removeData("saving committing");
    $cell.removeClass("is-editing is-saving");
    $cell.html($cell.data("original-html") || this.renderCell($cell.attr("data-raw-value"), { fieldname: $cell.attr("data-fieldname") }));
    if (this.detailState) this.detailState.dirty = false;
  }

  async commitCellEdit($cell) {
    if (!$cell.length || !$cell.hasClass("is-editing") || $cell.data("saving") || $cell.data("committing") || $cell.data("cancelled")) {
      $cell.removeData("cancelled");
      return;
    }
    $cell.data("committing", true);

    const $editor = $cell.find(".ocw-cell-editor");
    const oldValue = $cell.attr("data-raw-value") || "";
    const fieldname = $cell.attr("data-fieldname");
    const newValue = this.selectOptions[fieldname] ? this.normalizeSelectValue(fieldname, $editor.val()) : this.normalizeEditorValue($editor.val());
    const oldComparableValue = this.selectOptions[fieldname] ? this.normalizeSelectValue(fieldname, oldValue) : oldValue;
    const fieldLabel = $cell.attr("data-field-label") || fieldname;
    const batchName = $cell.attr("data-batch-name");
    const itemName = $cell.attr("data-item-name");
    const versionName = $cell.attr("data-version-name") || null;
    const isSpecialOverride = $cell.attr("data-special-override") === "1";
    const purchaseCorrectionFields = new Set([
      "goods_value",
      "unit_price",
      "purchase_currency",
      "purchase_uom",
      "unit_price_uom",
    ]);
    const oldValueIsMissing = typeof this.materialValueIsPlaceholder === "function"
      ? this.materialValueIsPlaceholder(fieldname, oldValue, {})
      : ["", "-", "--", "/", "\\", "N/A", "NA", "NULL", "无", "暂无"].includes(String(oldValue || "").trim().toUpperCase());
    const isPurchaseCorrection = purchaseCorrectionFields.has(fieldname) && !oldValueIsMissing;
    const itemLabel = this.getLocalItemLabel(batchName, itemName);

    if (newValue === oldComparableValue) {
      this.cancelCellEdit($cell);
      return;
    }

    const confirmed = await this.requestEditConfirm(fieldLabel, this.formatCellValue(newValue, { fieldname }));
    if (!confirmed) {
      this.cancelCellEdit($cell);
      return;
    }

    let remark = "";
    if (isSpecialOverride || isPurchaseCorrection) {
      remark = await this.requestEditRemark(fieldLabel);
      if (!remark) {
        this.cancelCellEdit($cell);
        return;
      }
    }

    $cell.data("saving", true).addClass("is-saving").html(`<span class="ocw-cell-saving">${ocTranslate("保存中")}</span>`);
    try {
      const result = await this.call(
        "overseas_costing.api.calculate.update_item_field",
        {
          item_name: itemName,
          fieldname,
          value: newValue,
          version_name: versionName,
          remark,
          edit_token: this.detailState?.editToken || null,
          expected_modified: this.detailState?.expectedModified || null,
        },
        true
      );
      if (!result.ok) {
        throw new Error(result.message || "字段保存失败");
      }
      this.activeBatchName = batchName;
      this.markBatchDirty(batchName);
      this.updateLocalItemValue(batchName, itemName, fieldname, result.value);
      if (this.detailState) {
        this.acceptBatchWriteRevision?.(result.batch_modified);
        if (this.detailState.header && result.batch_modified) this.detailState.header.modified = result.batch_modified;
        this.detailState.dirty = false;
      }
      if (this.detailState && this.detailState.batchName === batchName && this.detailState.tab === "items") {
        await this.loadSkuPage();
      } else {
        await this.loadBatchItems(batchName, versionName, true);
        await this.loadAuditLogs(batchName, versionName);
        this.renderTable();
      }
      frappe.show_alert({ message: result.message || ocTranslate("字段已保存"), indicator: result.changed ? "green" : "blue" });
    } catch (error) {
      $cell.removeData("saving committing");
      this.cancelCellEdit($cell);
      this.showError(error);
    }
  }

  requestEditConfirm(fieldLabel, newValue) {
    return new Promise((resolve) => {
      frappe.confirm(
        `
          <div class="ocw-confirm-copy">
            <h4>${ocTranslate("确认修改")}</h4>
            <p>${ocTranslate("确认将「{0}」修改为「{1}」？", [this.escape(fieldLabel), this.escape(this.formatValue(newValue))])}</p>
          </div>
        `,
        () => resolve(true),
        () => resolve(false)
      );
    });
  }

  requestEditRemark(fieldLabel) {
    return new Promise((resolve) => {
      let settled = false;
      const dialog = new frappe.ui.Dialog({
        title: ocTranslate("填写修改原因"),
        fields: [
          {
            fieldtype: "Small Text",
            fieldname: "remark",
            label: ocTranslate("{0} 修改原因", [fieldLabel]),
            reqd: 1,
          },
        ],
        primary_action_label: ocTranslate("保存"),
        primary_action: (values) => {
          settled = true;
          dialog.hide();
          resolve(String(values.remark || "").trim());
        },
      });
      dialog.onhide = () => {
        if (!settled) resolve("");
      };
      dialog.show();
    });
  }

  markBatchDirty(batchName) {
    const batch = this.findBatch(batchName);
    if (batch) batch.status = "Dirty";
  }

  updateLocalItemValue(batchName, itemName, fieldname, value) {
    const items = this.batchItems[batchName] || [];
    const row = items.find((item) => item.name === itemName);
    if (!row) return;
    row[fieldname] = value;
    row.manual_override_flag = 1;
  }

  getLocalItemLabel(batchName, itemName) {
    const items = this.batchItems[batchName] || [];
    const row = items.find((item) => item.name === itemName);
    if (!row) return "";
    return this.materialAuditLabel(row);
  }

  materialAuditLabel(row) {
    if (!row) return "";
    const code = row.material_code || "";
    const name = row.product_name || "";
    if (code && name) return `${code} / ${name}`;
    return code || name || row.name || ocTranslate("未命名物料");
  }
