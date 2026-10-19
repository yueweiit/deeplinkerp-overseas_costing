frappe.ui.form.on("Overseas Cost ERP Settings", {
    refresh(frm) {
        toggle_push_mode_fields(frm);
        frm.add_custom_button(__("测试连接"), () => {
            frm.save().then(() => {
                frappe.call({
                    method: "overseas_costing.services.erp_client.check_erp_connection",
                    freeze: true,
                    freeze_message: "正在检查 ERP 连接...",
                    callback(r) {
                        const result = r.message || {};
                        const indicator = result.ok ? "green" : "red";
                        frappe.msgprint({
                            title: __("ERP 连接检查"),
                            indicator,
                            message: result.message || "没有返回检查结果。",
                        });
                    },
                });
            });
        });
    },
    push_mode(frm) {
        toggle_push_mode_fields(frm);
    },
});

function toggle_push_mode_fields(frm) {
    const mode = frm.doc.push_mode || "standard_purchase";
    const isStandard = mode === "standard_purchase" || mode === "标准模块（物料+采购订单）";
    ["company", "supplier", "cost_center", "item_group", "stock_uom", "default_currency", "schedule_date"].forEach((fieldname) => {
        frm.toggle_display(fieldname, isStandard);
    });
    ["target_doctype", "http_method", "payload_field", "field_map_json"].forEach((fieldname) => {
        frm.toggle_display(fieldname, !isStandard);
    });
}
