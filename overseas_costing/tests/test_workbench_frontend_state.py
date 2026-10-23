from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PARTS = ROOT / "page" / "overseas_cost_workbench" / "parts"


def _state_result(script: str) -> dict:
    state_file = PARTS / "05-workbench-state.js"
    completed = subprocess.run(
        ["node", "-e", f"const s=require({json.dumps(str(state_file))}); {script}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _fee_workspace_result(script: str) -> dict:
    workspace_file = PARTS / "78-material-fee-workspace.js"
    grid_file = PARTS / "79-material-import-grid.js"
    completed = subprocess.run(
        ["node", "-e", "eval(require('fs').readFileSync(0, 'utf8'))"],
        # Linux caps a single argv entry at 128 KiB; translation catalogs exceed it.
        # Preserve -e's existing global built-ins, but pass the real script on stdin.
        input=(
                "const fs=require('fs');"
                f"const gridFile={json.dumps(str(grid_file))};"
                f"const source=fs.readFileSync({json.dumps(str(workspace_file))},'utf8')+(fs.existsSync(gridFile)?fs.readFileSync(gridFile,'utf8'):'');"
                "const Harness=Function(`return class FeeWorkspaceHarness {${source}}`)();"
                "global.frappe={show_alert:(value)=>global.alerts.push(value)};"
                "global.alerts=[];"
                f"(async()=>{{{script}}})().catch((error)=>{{console.error(error);process.exit(1)}});"
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _detail_workspace_result(script: str) -> dict:
    workspace_file = PARTS / "82-detail-page.js"
    completed = subprocess.run(
        [
            "node",
            "-e",
            (
                "const fs=require('fs');"
                f"const source=fs.readFileSync({json.dumps(str(workspace_file))},'utf8');"
                "const Harness=Function(`return class DetailWorkspaceHarness {${source}}`)();"
                "global.alerts=[];global.frappe={show_alert:(value)=>global.alerts.push(value)};"
                "global.window={clearInterval:()=>{},setInterval:()=>42};"
                f"(async()=>{{{script}}})().catch((error)=>{{console.error(error);process.exit(1)}});"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_existing_physical_cells_use_reasoned_correction_while_blank_cells_stay_editable() -> None:
    source = (PARTS / "78-material-fee-workspace.js").read_text(encoding="utf-8")
    fields = source.split("materialPurchaseCorrectionFields()", 1)[1].split(
        "renderMaterialFeeGridCell", 1
    )[0]
    renderer = source.split("renderMaterialFeeGridCell", 1)[1].split(
        "shipmentValuationStatus", 1
    )[0]

    assert '"gross_weight_kg"' in fields
    assert '"volume_m3"' in fields
    assert "this.materialPurchaseCorrectionFields().has(column.field)" in renderer
    assert "!this.materialValueIsPlaceholder(column.field, originalValue, item)" in renderer


def test_cost_trial_entry_uses_ai_review_and_exposes_user_recovery_actions() -> None:
    source = (PARTS / "78-material-fee-workspace.js").read_text(encoding="utf-8")
    trial_block = source.split("async refreshMaterialFeeCostPreview", 1)[1].split(
        "acceptSavedComprehensiveCost", 1
    )[0]

    assert "start_cost_trial_ai_review" in trial_block
    assert "calculate_comprehensive_cost" not in trial_block
    for endpoint in (
        "get_cost_trial_ai_review_status",
        "preview_cost_trial",
        "confirm_cost_trial",
        "discard_cost_trial_ai_review",
    ):
        assert endpoint in source
    for label in ("重新让 AI 判断", "返回补资料", "放弃试算", "应用调整并试算", "凭证优先"):
        assert label in source
    assert "确认说明" not in trial_block
    assert "系统优先沿用已有口径，仅在必要时请求 AI" in source
    assert "逐 SKU 金额由服务端规则引擎计算" in source
    assert 'result.status === "CONFIRMING"' in source
    assert 'result.status === "CONFIRMED"' in source
    assert "未采用的 AI 结果不计入" not in source


FEE_INPUT_FIXTURE = r"""
function makeFeeInput({amount, currency, originalAmount, originalCurrency, expenseCategory, originalExpenseCategory, forceActual=false, feeKey='international_sea_freight'}) {
  const classes = new Set();
  const data = {};
  const attrs = {title: ''};
  const errorElement = {
    length: 1,
    value: '',
    hidden: true,
    text(next) { if (arguments.length > 0) { this.value = next; return this; } return this.value; },
    attr(name, next) { if (arguments.length > 1) { this[name] = next; return this; } return this[name]; },
    toggleClass(name, enabled) { if (name === 'is-visible') this.hidden = !enabled; return this; },
  };
  const amountInput = makeInput('amount', amount, originalAmount);
  const currencyInput = makeInput('currency', currency, originalCurrency);
  const nameInput = expenseCategory === undefined ? null : makeInput('expense_category', expenseCategory, originalExpenseCategory);
  function makeInput(kind, value, original) {
    return {
      length: 1,
      kind,
      value,
      disabled: false,
      attrs: {
        'data-fee-key': feeKey,
        'data-mf-fee-input': kind,
        'data-original-value': original,
        ...(kind === 'amount' && forceActual ? {'data-mf-force-actual': '1'} : {}),
      },
      val(next) { if (arguments.length > 0) { this.value = next; return this; } return this.value; },
      attr(name, next) { if (arguments.length > 1) { this.attrs[name] = next; return this; } return this.attrs[name]; },
      data(name, next) { if (arguments.length > 1) { data[name] = next; return this; } return data[name]; },
      prop(name, next) { if (name === 'disabled') this.disabled = next; return this; },
      closest() { return cell; },
    };
  }
  const collection = {
    prop(name, next) { [amountInput, currencyInput, nameInput].filter(Boolean).forEach(input => input.prop(name, next)); return this; },
    attr(name, next) { [amountInput, currencyInput, nameInput].filter(Boolean).forEach(input => input.attr(name, next)); return this; },
  };
  const cell = {
    data(name, next) { if (arguments.length > 1) { data[name] = next; return this; } return data[name]; },
    addClass(name) { classes.add(name); return this; },
    removeClass(name) { name.split(/\s+/).forEach((item) => classes.delete(item)); return this; },
    hasClass(name) { return classes.has(name); },
    attr(name, next) { if (arguments.length > 1) { attrs[name] = next; return this; } return attrs[name]; },
    find(selector) {
      if (selector === '[data-mf-fee-amount]') return amountInput;
      if (selector === '[data-mf-fee-currency]') return currencyInput;
      if (selector === '[data-mf-other-fee-name]') return nameInput || {length: 0};
      if (selector === '[data-mf-fee-error]') return errorElement;
      return collection;
    },
  };
  amountInput.cell = cell;
  currencyInput.cell = cell;
  return {amountInput, currencyInput, nameInput, errorElement, cell, classes, attrs};
}
"""


def test_dingtalk_detail_tab_has_stable_url_and_resource() -> None:
    result = _state_result(
        "console.log(JSON.stringify({"
        "tab:s.parseWorkbenchState('https://example.test/desk/overseas-cost-workbench?screen=detail&tab=dingtalk').tab,"
        "resource:s.detailTabResource('dingtalk')}));"
    )

    assert result == {"tab": "dingtalk", "resource": "dingtalk"}


def test_module_rail_is_not_confused_with_workspace_sidebar() -> None:
    bootstrap = (PARTS / "00-bootstrap.js").read_text(encoding="utf-8")
    shell = (PARTS / "10-shell.js").read_text(encoding="utf-8")

    ensure_block = bootstrap.split("function ensureDeskModuleSidebar", 1)[1].split(
        "function syncWorkspaceSidebarHeaderIcon", 1
    )[0]
    assert '$(".body-sidebar-container' not in ensure_block
    assert ".custom-filters-right-sidebar-container" in ensure_block
    hide_block = shell.split("hideDeskChrome()", 1)[1].split("restoreDeskChrome()", 1)[0]
    assert ".custom-filters-right-sidebar-container" not in hide_block
    on_show = bootstrap.split('frappe.pages["overseas-cost-workbench"].on_page_show', 1)[1]
    assert "applyModuleSidebarPreference()" in on_show


def test_workspace_header_reuses_authorized_desktop_icon_and_restores_original() -> None:
    bootstrap = (PARTS / "00-bootstrap.js").read_text(encoding="utf-8")
    shell = (PARTS / "10-shell.js").read_text(encoding="utf-8")
    stylesheet = (PARTS / "20-desk-layout.css").read_text(encoding="utf-8")

    assert "function syncWorkspaceSidebarHeaderIcon" in bootstrap
    assert "frappe.boot.desktop_icons" in bootstrap
    assert ".body-sidebar-container .sidebar-header > .sidebar-item-icon" in bootstrap
    assert "desktopIcon.logo_url || desktopIcon.icon_image" in bootstrap
    assert "ocw-workspace-sidebar-icon" in bootstrap
    assert "_workspaceSidebarIconSnapshot" in shell
    assert ".ocw-workspace-sidebar-icon" in stylesheet
    slot_selector = ".body-sidebar-container .sidebar-header > .sidebar-item-icon {"
    assert slot_selector in stylesheet
    slot_rule = stylesheet.split(slot_selector, 1)[1].split("}", 1)[0]
    assert "width: 32px" in slot_rule
    assert "height: 32px" in slot_rule
    assert "flex: 0 0 32px" in slot_rule


def test_generated_workbench_assets_include_dingtalk_parts_and_match_deployed_copy() -> None:
    page = ROOT / "page" / "overseas_cost_workbench"
    deployed = ROOT / "overseas_costing" / "page" / "overseas_cost_workbench"
    javascript = (page / "overseas_cost_workbench.js").read_text(encoding="utf-8")
    stylesheet = (page / "overseas_cost_workbench.css").read_text(encoding="utf-8")

    assert "renderDingtalkApprovalTab" in javascript
    assert "openDingtalkPackingPreview" in javascript
    assert ".ocw-dingtalk-approval-card" in stylesheet
    assert javascript == (deployed / "overseas_cost_workbench.js").read_text(encoding="utf-8")
    assert stylesheet == (deployed / "overseas_cost_workbench.css").read_text(encoding="utf-8")


def test_packing_flow_source_switch_clears_unconfirmed_preview() -> None:
    result = _state_result(
        "let state=s.createPackingFlowState();"
        "state=s.selectPackingSource(state,'manual_attachment','ATT-1');"
        "state=s.applyPackingPreview(state,{source_revision:'old',groups:[],validation:{blocking:[]},totals:{gross_weight_kg:{value:'10'},volume_m3:{value:'2'}}});"
        "state=s.selectPackingSource(state,'wiki_sheet','WB:ST-1');"
        "console.log(JSON.stringify({kind:state.sourceKind,id:state.sourceId,preview:state.preview,snapshot:state.snapshot,step:state.step}));"
    )

    assert result == {
        "kind": "wiki_sheet",
        "id": "WB:ST-1",
        "preview": None,
        "snapshot": None,
        "step": 1,
    }


def test_packing_flow_requires_group_resolution_before_step_three() -> None:
    result = _state_result(
        "let state=s.selectPackingSource(s.createPackingFlowState(),'wiki_sheet','WB:ST-1');"
        "state=s.applyPackingPreview(state,{source_revision:'rev-1',groups:[{group_id:'G-1',needs_confirmation:true}],validation:{blocking:[{code:'group_confirmation_required'}]},totals:{gross_weight_kg:{value:'10'},volume_m3:{value:'2'}}});"
        "const before=state.canCompare;"
        "state=s.resolvePackageGroup(state,'G-1','confirm_shared');"
        "console.log(JSON.stringify({before,after:state.canCompare,action:state.resolutions.groups['G-1'].action}));"
    )

    assert result == {"before": False, "after": True, "action": "confirm_shared"}


def test_packing_flow_requires_total_basis_and_supports_explicit_row_partition() -> None:
    result = _state_result(
        "let state=s.selectPackingSource(s.createPackingFlowState(),'wiki_sheet','WB:ST-1');"
        "state=s.applyPackingPreview(state,{source_revision:'rev-1',groups:[{group_id:'G-1',row_numbers:[2,3,4],needs_confirmation:true}],validation:{blocking:[{code:'group_confirmation_required'},{code:'total_mismatch',field:'gross_weight_kg'}]},totals:{gross_weight_kg:{value:'0',declared_value:'0',calculated_value:'10'},volume_m3:{value:'2'}}});"
        "state=s.resolvePackageGroupRows(state,'G-1',[2,3,4],[2,3]);"
        "const before=state.canCompare;"
        "state=s.resolvePackingTotal(state,'gross_weight_kg','use_calculated');"
        "console.log(JSON.stringify({before,after:state.canCompare,group:state.resolutions.groups['G-1'],total:state.resolutions.totals.gross_weight_kg}));"
    )

    assert result == {
        "before": False,
        "after": True,
        "group": {"action": "partition", "partitions": [[2, 3], [4]]},
        "total": {"action": "use_calculated"},
    }


def test_freight_quote_requires_scope_confirmation_and_remark() -> None:
    result = _state_result(
        "const base={currency:'USD',weight_unit_price:'1',volume_unit_price:'2'};"
        "const missing=s.buildFreightQuotePayload(null,base);"
        "const ready=s.buildFreightQuotePayload(null,{...base,scope_confirmed:true,quote_remark:'same route'});"
        "console.log(JSON.stringify({missing:s.freightQuoteIsReady(missing),ready:s.freightQuoteIsReady(ready)}));"
    )

    assert result == {"missing": False, "ready": True}


def test_packing_flow_refresh_replaces_preview_and_quote_has_no_formal_cost_action() -> None:
    result = _state_result(
        "let state=s.selectPackingSource(s.createPackingFlowState(),'wiki_sheet','WB:ST-1');"
        "state=s.applyPackingPreview(state,{source_revision:'old',groups:[{group_id:'OLD'}],material_rows:[{material_code:'OLD'}],validation:{blocking:[]},totals:{gross_weight_kg:{value:'10'},volume_m3:{value:'2'}}});"
        "state=s.applyPackingPreview(state,{source_revision:'new',groups:[{group_id:'NEW'}],material_rows:[{material_code:'NEW'}],validation:{blocking:[]},totals:{gross_weight_kg:{value:'20'},volume_m3:{value:'3'}}});"
        "const quote=s.buildFreightQuotePayload(state,{currency:'usd',scope_confirmed:true,weight_unit_price:'1.2',volume_unit_price:'500',weight_surcharge:'10',volume_surcharge:'20',quote_remark:'test'});"
        "console.log(JSON.stringify({revision:state.preview.source_revision,groups:state.preview.groups.map(x=>x.group_id),rows:state.preview.material_rows.map(x=>x.material_code),quote,hasAction:Object.prototype.hasOwnProperty.call(quote,'action')}));"
    )

    assert result == {
        "revision": "new",
        "groups": ["NEW"],
        "rows": ["NEW"],
        "quote": {
            "currency": "USD",
            "scope_confirmed": True,
            "weight": {"unit_price": "1.2", "surcharge": "10"},
            "volume": {"unit_price": "500", "surcharge": "20"},
            "quote_remark": "test",
        },
        "hasAction": False,
    }


def test_packing_flow_static_ui_contract() -> None:
    flow = (PARTS / "86-packing-flow.js").read_text(encoding="utf-8")
    documents = (PARTS / "65-manual-documents.js").read_text(encoding="utf-8")
    stylesheet = (PARTS / "47-packing-flow.css").read_text(encoding="utf-8")
    source_service = (ROOT / "services" / "packing_source_service.py").read_text(encoding="utf-8")

    for label in (
            "本地上传",
            "钉钉审批附件/评论",
            "装箱计划表",
            "刷新所选 Sheet",
        "系统推荐",
        "预览这张装箱计划",
        "共享箱级数据",
        "保存草稿",
        "下一步：比较运费",
        "独立试算，不修改正式费用",
        "保存比较结果",
        "选择表内合计",
        "选择明细加总",
    ):
        assert label in flow
    assert "获取装箱单" in documents
    assert "openPackingFlowDialog" in flow
    assert "localStorage.getItem" in flow
    assert 'dialog.packingSourceTab = options.sourceTab || "wiki"' in flow
    assert flow.index('["wiki", "装箱计划表"]') < flow.index('["approval", "钉钉审批附件/评论"]')
    assert flow.index('["approval", "钉钉审批附件/评论"]') < flow.index('["local", "本地上传"]')
    assert "selectRecommendedPackingSource" in flow
    assert "auto_select_recommended" in flow
    assert "recommendation_reasons" in flow
    assert "business_date" in flow
    assert "知识库年度表" not in flow
    assert "需要刷新的年度表" not in flow
    assert "钉钉知识库刷新失败" not in flow
    assert "知识库 Sheet" not in source_service
    assert ".ocw-packing-flow" in stylesheet
    assert ".ocw-packing-recommendation" in stylesheet
    assert "position: sticky" in stylesheet
    # 品牌色不再由本弹窗声明，改由 .modal 上的共享调色板提供（见
    # test_dialog_theme_palette_is_declared_once_for_every_modal）。
    assert ".ocw-packing-flow-dialog .modal-dialog" in stylesheet


def test_interactive_theme_uses_deeplink_blue_without_legacy_teal() -> None:
    redesign = (PARTS / "25-workbench-redesign.css").read_text(encoding="utf-8").lower()
    detail = (PARTS / "45-detail-page.css").read_text(encoding="utf-8").lower()
    all_styles = "\n".join(path.read_text(encoding="utf-8").lower() for path in PARTS.glob("*.css"))

    assert "--ocw-accent: #0b8cf0" in redesign
    assert "--ocw-accent-dark: #076fbe" in redesign
    assert "--ocw-accent-soft: #eaf5ff" in redesign
    for legacy_teal in ("#087d82", "#05666b", "#e9f7f6", "rgba(8, 125, 130", "#318e92"):
        assert legacy_teal not in redesign + detail
    for legacy_interactive_color in (
        "#0877d1",
        "#0d8bf2",
        "#0876d1",
        "#075fa5",
        "#cfe2dc",
        "#eef8f4",
        "#8ac8c1",
        "#f5fbfa",
        "#e9f5f3",
    ):
        assert legacy_interactive_color not in all_styles
    assert ".ocw-issue.is-ready { color: #067647; }" in redesign


def _css_rules(stylesheet: str) -> list[tuple[list[str], str]]:
    # 先去掉注释：注释里会写「原来在 .ocw-page 上声明」这类文字，不去掉就会被当成选择器的一部分。
    source = re.sub(r"/\*.*?\*/", "", stylesheet, flags=re.DOTALL)
    return [
        ([name.strip() for name in selectors.split(",")], body)
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", source)
    ]


def test_dialog_theme_palette_is_declared_once_for_every_modal() -> None:
    """调色板只允许两处声明：页面作用域一处、对话框作用域一处。

    Frappe 的 Dialog 是追加到 body 下的，不在 .ocw-page / .ocw-mf-workspace 子树里；裸
    var(--ocw-brand) 在那里取不到值，整条声明会在计算期失效（border 变 none、background 变
    transparent），而 color:#fff 仍然生效 ⇒ 主按钮＝白底白字：占着位置却看不见。

    历史上是给每个弹窗各抄一份变量块来顶（.ocw-packing-flow-dialog / .ocw-cost-trial-dialog /
    .ocw-settlement-modal / .ocw-voucher-modal），而全站有 20 个对话框 wrapper，漏抄一个就复发
    ——「刷新来源」旁边那个上传按钮就是这样渲染出来又看不见的。现在统一由 .modal 兜底（Frappe
    的对话框都带这个类），这条测试守住「不许再退回去逐个弹窗声明」。
    """
    stylesheets = {
        path.name: path.read_text(encoding="utf-8") for path in sorted(PARTS.glob("*.css"))
    }

    def owners(declaration: str) -> list[str]:
        found: set[str] = set()
        for stylesheet in stylesheets.values():
            for selectors, body in _css_rules(stylesheet):
                if declaration in body:
                    found.update(selectors)
        return sorted(found)

    assert owners("--ocw-brand:") == [".modal", ".ocw-page"]
    assert owners("--mf-blue:") == [".modal", ".ocw-mf-workspace"]

    shell_palette = next(
        body for selectors, body in _css_rules(stylesheets["10-shell.css"]) if selectors == [".modal"]
    )
    for declaration in (
        "--ocw-ink:",
        "--ocw-muted:",
        "--ocw-line:",
        "--ocw-paper:",
        "--ocw-panel:",
        "--ocw-brand:",
        "--ocw-brand-dark:",
        "--ocw-brand-soft:",
        "--ocw-accent:",
        "--ocw-accent-soft:",
        "--ocw-mono:",
    ):
        assert declaration in shell_palette, f"对话框作用域缺少 {declaration}"

    material_rules = _css_rules(stylesheets["48-material-fee-workspace.css"])
    material_palette = next(
        body for selectors, body in material_rules if selectors == [".ocw-mf-workspace", ".modal"]
    )
    for declaration in ("--mf-blue:", "--mf-blue-soft:", "--mf-border:", "--mf-text:", "--mf-muted:"):
        assert declaration in material_palette, f"物料对话框作用域缺少 {declaration}"
    # 拆出变量块时不能把工作区自己的布局声明带进对话框。
    workspace = next(body for selectors, body in material_rules if selectors == [".ocw-mf-workspace"])
    assert "display: grid" in workspace
    assert "display: grid" not in material_palette


def test_every_var_reference_in_the_workbench_resolves_to_a_declaration() -> None:
    """CSS 里每个 var(--x) 都必须取得到值，否则整条声明在计算期静默失效。

    这是同一个缺陷类里的第二种死法：上一条管的是「声明了但作用域够不着」（对话框里裸
    var(--ocw-brand) ⇒ 主按钮白底白字），这条管的是「名字压根没人声明过」—— 此时 var()
    退化成保证无效值，`font-family` / `color` 整条被丢掉，元素安静地继承父级，看起来"就是
    没生效"而不像坏了。历史上 `--font-stack`（OCR 文本框）与 `--ocw-ink-muted`（错误行标签）
    就是这样从上线起一直没起作用。

    声明只允许来自两处，都算在册：parts CSS 里的自定义属性，以及 parts JS 里内联注入的
    那几个表格宽度（`style="--ocw-…: …"`，见 78-material-fee-workspace.js 的 grid shell）。
    """

    declared: set[str] = set()
    used: set[str] = set()
    for path in sorted(PARTS.glob("*.css")):
        # 注释里会写「原来是 --ocw-brand」这类文字，去掉再扫，免得把说明当成声明。
        source = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
        declared.update(re.findall(r"(--[\w-]+)\s*:", source))
        used.update(re.findall(r"var\(\s*(--[\w-]+)", source))
    for path in sorted(PARTS.glob("*.js")):
        # JS 侧只当声明来源：`--x` 不是合法 JS 标识符，出现即说明是在拼内联样式。
        declared.update(re.findall(r"(--[\w-]+)\s*:", path.read_text(encoding="utf-8")))

    assert sorted(used - declared) == [], "这些变量没有声明处，引用它们的声明会整条失效"


def test_detail_overview_contains_responsive_columns_and_its_own_fee_scroller() -> None:
    detail = (PARTS / "45-detail-page.css").read_text(encoding="utf-8").lower()
    dashboard = detail.split(".ocw-detail-overview-dashboard {", 1)[1].split("}", 1)[0]
    fee_scroll = detail.split(".ocw-overview-fee-scroll {", 1)[1].split("}", 1)[0]

    assert "grid-template-columns" in dashboard
    assert "minmax(0, 2fr)" in dashboard
    assert "overflow-x: auto" in fee_scroll
    assert "@media (max-width: 1100px)" in detail


def test_dingtalk_missing_source_has_repair_action_and_never_claims_unlinked() -> None:
    approval_page = (PARTS / "84-dingtalk-approval.js").read_text(encoding="utf-8")
    workbench_events = (PARTS / "35-workbench-view.js").read_text(encoding="utf-8")

    assert "request_batch_dingtalk_approval_repair" in approval_page
    assert "钉钉审批正在补同步" in approval_page
    assert "物流审批尚未同步，暂无法判断关联采购审批" in approval_page
    assert "data-action='repair-dingtalk-approval'" in approval_page
    assert "[data-action='repair-dingtalk-approval']" in workbench_events


def test_overview_reconciles_purchase_approval_status_from_postgres_detail() -> None:
    detail_page = (PARTS / "82-detail-page.js").read_text(encoding="utf-8")
    approval_page = (PARTS / "84-dingtalk-approval.js").read_text(encoding="utf-8")

    overview_block = detail_page.split("renderOverviewDetailTab()", 1)[1].split(
        "detailDocumentAdapter()", 1
    )[0]
    assert "this.loadDingtalkApprovalDetail()" in overview_block
    assert 'this.detailState.tab === "overview"' in overview_block
    assert "approval.batch_name !== batch.name" in overview_block

    assert "syncPurchaseApprovalStatusFromDingtalk" in approval_page
    assert 'excluded.length ? (approvals.length ? "partial" : "excluded") : "valid"' in approval_page
    assert 'sourceStatus.invalid_business = true' in approval_page
    assert 'sourceStatus.invalid_business_scope = "linked_purchase_approval"' in approval_page
    assert 'sourceStatus.invalid_business = false' in approval_page
    assert "linked_purchase_approval_statuses" in approval_page
    assert "if (attachmentName) return attachmentName;" not in approval_page
    assert "processInstanceId && fileId" in approval_page


def test_documents_tab_is_replaced_only_by_phase_one_material_fee_workspace() -> None:
    detail_page = (PARTS / "82-detail-page.js").read_text(encoding="utf-8")
    workspace = (PARTS / "78-material-fee-workspace.js").read_text(encoding="utf-8")
    workspace += (PARTS / "78-fee-evidence-review-matrix.js").read_text(encoding="utf-8")
    workspace += (PARTS / "79-material-import-grid.js").read_text(encoding="utf-8")
    stylesheet = (PARTS / "48-material-fee-workspace.css").read_text(encoding="utf-8")
    documents_block = detail_page.split("async renderDocumentsDetailTab()", 1)[1].split(
        "async renderVoucherDetailTab()", 1
    )[0]

    assert "loadMaterialFeeWorkspace" in documents_block
    assert "renderManualDocumentPanel" not in documents_block
    for label in (
        "费用与凭证",
        "开始试算",
        "物料与装箱数据",
            "只看缺项",
            "展开辅助列",
            "获取装箱资料",
            "AI 分析资料",
            "本地上传装箱单",
        "确认写入物料表",
        "净重 kg",
        "详细待办",
        "SKU 综合单价试算",
    ):
        assert label in workspace
    for endpoint in (
        "overseas_costing.api.material_fee_workspace.get_snapshot",
        "overseas_costing.api.material_fee_workspace.check_freshness",
        "overseas_costing.api.material_fee_workspace.refresh_snapshot",
        "overseas_costing.api.materials.set_shipping_quantity",
        "overseas_costing.api.materials.preview_material_import",
        "overseas_costing.api.materials.apply_material_import",
        "overseas_costing.api.packing_api.list_packing_sheet_catalog",
        "overseas_costing.api.packing_api.list_packing_attachment_sources",
        "overseas_costing.api.packing_api.request_packing_sheet_refresh",
        "overseas_costing.api.calculate.update_item_field",
            "overseas_costing.api.calculate.batch_update_items",
            "overseas_costing.api.materials.list_project_route_options",
        "overseas_costing.api.fees.save_fee",
        "overseas_costing.api.fees.start_fee_evidence_review",
        "overseas_costing.api.fees.set_fee_evidence_status",
        "overseas_costing.api.import_api.preview_oa_source_attachment",
    ):
        assert endpoint in workspace
    assert "确认所选草稿" in workspace
    assert "openWikiMaterialImportDialog" in workspace
    assert "previewWikiMaterialImport" in workspace
    assert "data-mf-wiki-source" in workspace
    assert "shared_groups" in workspace
    assert "confirmation_groups" in workspace
    assert "row_states" in workspace
    assert "candidate_regions" in workspace
    assert "批次外与未匹配行" in workspace
    assert "candidate?.reason" in workspace
    assert "批次外疑似合并组" not in workspace
    assert "source_fields" in workspace
    assert "merged_source_fields" in workspace
    assert "MERGED_PREVIEW_CONFIRMATION_REQUIRED" in workspace
    assert "已生成合并后的最终预览，请核对后确认写入" in workspace
    assert "group_confirmations" in workspace
    assert "allocations" in workspace
    assert "source_validation" in workspace
    assert "data-mf-source-validation" in workspace
    assert "DUPLICATE_TARGET_SELECTION" in workspace
    assert "未识别出可安全拆分的金额，可保留凭证后人工补录" in workspace
    assert "writeback" not in workspace.lower()
    assert 'data-action="detail-primary" data-primary-action="recalculate"' in workspace
    assert ".ocw-mf-workspace" in stylesheet
    assert ".ocw-mf-cell.is-missing" in stylesheet
    assert ".ocw-mf-cell.is-default" in stylesheet
    assert ".ocw-mf-cell.is-save-error" in stylesheet


def test_fee_workspace_pending_evidence_is_linked_without_inflating_missing_count() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1'};"
        "workspace.materialFeeState={batchName:'B-1',materials:{missing_cell_count:0,items:[]},"
        "fees:{summary:{missing_evidence_fee_count:2,pending_evidence_fee_count:7},fees:[]},"
        "preview:{summary:{total_cost_rmb:'0.00'}}};"
        "workspace.escape=(value)=>String(value ?? '');"
        "workspace.formatValue=(value)=>String(value);"
        "workspace.renderMaterialFeeGrid=()=>'';workspace.renderMaterialFeeTodos=()=>'';"
        "workspace.renderMaterialFeeCostTable=()=>'';"
        "let html='';workspace.$root={find:()=>({html:(value)=>{html=value}})};"
        "workspace.renderMaterialFeeWorkspace();"
        "const row=workspace.renderMaterialFeeRow({logical_fee_key:'import_tax',expense_category:'\u8fdb\u53e3\u7a0e\u8d39',"
        "amount_status:'ACTUAL',amount:'2000',currency:'RMB',allocation:{status:'ALLOCATED'},"
        "evidence_state:'PENDING',evidence:[{name:'EV-1',evidence_role:'tax_certificate',validation_status:'PENDING'}]});"
        "console.log(JSON.stringify({metric:html.includes('<span>\u8d39\u7528\u51ed\u8bc1\u672a\u9f50</span><strong>2</strong>'),"
        "linked:row.includes('is-info')&&row.includes('\u5df2\u5173\u8054'),"
        "pending:row.includes('\u7ec8\u6838\u72b6\u6001\uff1a\u5f85\u6838\u5bf9'),"
        "actions:row.includes('\u786e\u8ba4\u6709\u6548')&&row.includes('\u6807\u8bb0\u65e0\u6548')}));"
    )

    assert result == {"metric": True, "linked": True, "pending": True, "actions": True}


def test_fee_workspace_missing_saved_amount_is_visible_inline_and_keeps_more_settings() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);workspace.escape=(value)=>String(value ?? '');"
        "workspace.formatValue=(value)=>String(value);"
        "const row=workspace.renderMaterialFeeRow({logical_fee_key:'destination_delivery',"
        "expense_category:'\u76ee\u7684\u5730\u914d\u9001\u8d39',amount_status:'MISSING',amount:'2000',currency:'RMB',"
        "allocation_basis:'gross_weight',scope_type:'ALL_ITEMS',allocation:{status:'NOT_ALLOCATED'},evidence:[]});"
        "console.log(JSON.stringify({amount:row.includes('data-mf-fee-amount')&&row.includes('value=\"2000\"'),"
        "currency:row.includes('data-mf-fee-currency')&&row.includes('value=\"RMB\"'),"
        "firstEstimate:row.includes('\u9996\u6b21\u91d1\u989d\u9ed8\u8ba4\u6682\u4f30'),forceActual:row.includes('data-mf-force-actual=\"1\"'),"
        "more:row.includes('\u66f4\u591a\u8bbe\u7f6e')}));"
    )

    assert result == {
        "amount": True,
        "currency": True,
        "firstEstimate": True,
        "forceActual": True,
        "more": True,
    }


def test_express_default_zero_row_explains_mexico_confirmation_without_actual_marker():
    from overseas_costing.services.fee_service import build_default_fee_templates
    fee = build_default_fee_templates('EXPRESS')[-1]
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);workspace.escape=(value)=>String(value ?? '');"
        f"const fee={json.dumps(fee, ensure_ascii=False)};"
        "console.log(JSON.stringify({html:workspace.renderMaterialFeeRow(fee)}));"
    )
    html = result['html']
    assert '当地快递费' in html
    assert 'value="MXN" selected' in html and 'value="0"' in html
    assert '默认暂估 0，待墨西哥确认' in html
    assert '由墨西哥同事补充' in html
    assert 'data-mf-force-actual="1"' not in html


def test_conflicting_fee_row_shows_saved_row_and_conflicts_without_edit_controls():
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);workspace.escape=(value)=>String(value ?? '');"
        "const fee={name:'OA-ROW',logical_fee_key:'international_air_freight',expense_category:'国际空运费',"
        "amount:20360,currency:'RMB',amount_status:'ESTIMATED',requires_review:true,"
        "duplicate_rule_names:['OA-ROW','MANUAL-ROW'],evidence:[{name:'E1',evidence_role:'freight_invoice'}]};"
        "console.log(JSON.stringify({html:workspace.renderMaterialFeeRow(fee)}));"
    )
    html = result['html']
    assert '费用重复' in html and 'OA-ROW' in html and 'MANUAL-ROW' in html
    assert '20360' in html and 'freight_invoice' in html
    assert 'data-mf-fee-input' not in html and 'data-action="mf-edit-fee"' not in html
    assert '已计入试算' not in html


@pytest.mark.parametrize('amount,currency,key', [('', 'MXN', 'import_tax'), ('0', 'MXN', 'destination_delivery'), ('0', 'RMB', 'express_surcharge')])
def test_untouched_mexico_fee_input_never_saves_or_reports_empty_error(amount, currency, key):
    result = _fee_workspace_result(FEE_INPUT_FIXTURE + f"""
const workspace=Object.create(Harness.prototype);workspace.detailState={{batchName:'B-1'}};
const state=workspace.ensureMaterialFeeState();
const fixture=makeFeeInput({{amount:{json.dumps(amount)},originalAmount:{json.dumps(amount)},currency:{json.dumps(currency)},originalCurrency:{json.dumps(currency)},feeKey:{json.dumps(key)}}});
let writes=0;workspace.call=async()=>{{writes++;throw new Error('Untouched input must not write')}};
await workspace.saveMaterialFeeInlineAmount(fixture.amountInput);
console.log(JSON.stringify({{writes,error:fixture.errorElement.value,drafts:state.feeDrafts}}));
""")
    assert result == {'writes': 0, 'error': '', 'drafts': {}}


def test_clearing_saved_mexico_fee_still_requires_an_amount():
    result = _fee_workspace_result(FEE_INPUT_FIXTURE + """
const workspace=Object.create(Harness.prototype);workspace.detailState={batchName:'B-1'};
const fixture=makeFeeInput({amount:'',originalAmount:'100',currency:'MXN',originalCurrency:'MXN',feeKey:'import_tax'});
await workspace.saveMaterialFeeInlineAmount(fixture.amountInput);
console.log(JSON.stringify({error:fixture.errorElement.value}));
""")
    assert '费用金额不能为空' in result['error']


def test_fee_workspace_enter_and_blur_share_inline_save_path() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);const handlers={};"
        "workspace.$root={on:(event,selector,handler)=>{handlers[`${event} ${selector}`]=handler}};"
        "workspace.loadMaterialFeeWorkspace=()=>{};workspace.ensureMaterialFeeState=()=>({});"
        "workspace.renderMaterialFeeWorkspace=()=>{};workspace.openMaterialFeeDialog=()=>{};"
        "workspace.openMaterialFeeEvidenceDialog=()=>{};workspace.setMaterialFeeEvidenceStatus=async()=>{};"
        "workspace.refreshMaterialFeeCostPreview=async()=>{};"
        "workspace.openMaterialXlsxUploader=()=>{};workspace.saveMaterialFeeCell=async()=>{};"
        "workspace.previewMaterialPaste=()=>{};let saves=0;workspace.saveMaterialFeeInlineAmount=async()=>{saves+=1};"
        "global.$=(value)=>value;workspace.bindMaterialFeeWorkspaceEvents();"
        "let prevented=false,blurred=false;const input={blur:()=>{blurred=true}};"
        "handlers['keydown [data-mf-fee-input]']({key:'Enter',preventDefault:()=>{prevented=true},currentTarget:input});"
        "await handlers['blur [data-mf-fee-input]']({currentTarget:{}});await Promise.resolve();"
        "console.log(JSON.stringify({prevented,blurred,saves}));"
    )

    assert result == {"prevented": True, "blurred": True, "saves": 1}


def test_fee_workspace_tab_within_amount_cell_saves_only_after_leaving_cell() -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);const handlers={};"
        "workspace.detailState={batchName:'B-1'};workspace.materialFeeState={batchName:'B-1',feeDrafts:{},focusedFeeInput:null};"
        "workspace.$root={on:(event,selector,handler)=>{handlers[`${event} ${selector}`]=handler}};"
        "workspace.loadMaterialFeeWorkspace=()=>{};workspace.renderMaterialFeeWorkspace=()=>{};workspace.openMaterialFeeDialog=()=>{};"
        "workspace.openMaterialFeeEvidenceDialog=()=>{};workspace.setMaterialFeeEvidenceStatus=async()=>{};workspace.refreshMaterialFeeCostPreview=async()=>{};"
        "workspace.openMaterialXlsxUploader=()=>{};workspace.saveMaterialFeeCell=async()=>{};"
        "workspace.previewMaterialPaste=()=>{};let saves=0;workspace.saveMaterialFeeInlineAmount=async(input)=>{saves+=1;input.closest().find('[data-mf-fee-input]').prop('disabled',true)};"
        "const fixture=makeFeeInput({amount:'2400',currency:'USD',originalAmount:'2000',originalCurrency:'RMB'});global.$=(value)=>value;"
        "workspace.bindMaterialFeeWorkspaceEvents();"
        "handlers['focus [data-mf-fee-input]']({currentTarget:fixture.currencyInput});"
        "handlers['blur [data-mf-fee-input]']({currentTarget:fixture.currencyInput,relatedTarget:fixture.amountInput});await Promise.resolve();"
        "const within={saves,amountDisabled:fixture.amountInput.disabled,currencyDisabled:fixture.currencyInput.disabled};"
        "handlers['focus [data-mf-fee-input]']({currentTarget:fixture.amountInput});"
        "handlers['blur [data-mf-fee-input]']({currentTarget:fixture.amountInput,relatedTarget:null});await Promise.resolve();"
        "console.log(JSON.stringify({within,afterLeaving:{saves,amountDisabled:fixture.amountInput.disabled,currencyDisabled:fixture.currencyInput.disabled}}));"
    )

    assert result == {
        "within": {"saves": 0, "amountDisabled": False, "currencyDisabled": False},
        "afterLeaving": {"saves": 1, "amountDisabled": True, "currencyDisabled": True},
    }


def test_fee_workspace_blur_clears_focus_target_until_another_fee_input_focuses() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);const handlers={};"
        "workspace.detailState={batchName:'B-1'};workspace.materialFeeState={batchName:'B-1',feeDrafts:{},focusedFeeInput:null};"
        "workspace.$root={on:(event,selector,handler)=>{handlers[`${event} ${selector}`]=handler}};"
        "workspace.loadMaterialFeeWorkspace=()=>{};workspace.renderMaterialFeeWorkspace=()=>{};workspace.openMaterialFeeDialog=()=>{};"
        "workspace.openMaterialFeeEvidenceDialog=()=>{};workspace.setMaterialFeeEvidenceStatus=async()=>{};workspace.refreshMaterialFeeCostPreview=async()=>{};"
        "workspace.openMaterialXlsxUploader=()=>{};workspace.saveMaterialFeeCell=async()=>{};"
        "workspace.previewMaterialPaste=()=>{};workspace.saveMaterialFeeInlineAmount=async()=>{};"
        "const input={attrs:{'data-fee-key':'fee-a','data-mf-fee-input':'amount'},attr(name){return this.attrs[name]}};global.$=(value)=>value;"
        "workspace.bindMaterialFeeWorkspaceEvents();handlers['focus [data-mf-fee-input]']({currentTarget:input});"
        "const afterFocus={...workspace.materialFeeState.focusedFeeInput};handlers['blur [data-mf-fee-input]']({currentTarget:input});"
        "console.log(JSON.stringify({afterFocus,afterBlur:workspace.materialFeeState.focusedFeeInput}));"
    )

    assert result == {"afterFocus": {"feeKey": "fee-a", "field": "amount"}, "afterBlur": None}


def test_fee_workspace_missing_saved_amount_submits_estimate_and_refreshes_fee_preview() -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'token-1',expectedModified:'m1',header:{modified:'m1'}};"
        "const fee={logical_fee_key:'international_sea_freight',rule_code:'sea-freight',expense_category:'\u56fd\u9645\u6d77\u8fd0\u8d39',"
        "amount_status:'MISSING',amount:'2000',currency:'RMB',allocation_basis:'volume',basis_field:'volume',"
        "scope_type:'ITEMS',scope_value_json:'[\"LINE-1\"]',required_evidence_role:'freight_invoice',"
        "included_in_fee_key:'',priority_no:3,remark:'keep me',is_active:1,is_enabled:1};"
        "workspace.materialFeeState={batchName:'B-1',fees:{fees:[fee]},requestId:0};workspace.ensureEditSession=async()=>true;"
        "workspace.getDetailBatch=()=>({name:'B-1',current_version:'V-1'});"
        "workspace.escape=(value)=>String(value ?? '');workspace.normalizeErrorMessage=(error)=>error.message;"
        "workspace.renderMaterialFeeWorkspace=()=>{};const calls=[];workspace.call=async(endpoint,args)=>{calls.push({endpoint,args});"
        "if(endpoint.endsWith('save_fee'))return {ok:true,batch_modified:'m2',message:'saved'};"
        "if(endpoint.endsWith('refresh_snapshot'))return {ok:true,batch_name:'B-1',version_name:'V-1',cache:{status:'ready'},data:{"
        "detail:{ok:true,batch_name:'B-1',version_name:'V-1',header:{name:'B-1',modified:'m2'}},materials:{items:[]},"
        "fees:{summary:{missing_amount_fee_count:0},fees:[{...fee,amount_status:'ACTUAL'}]},"
        "preview:{summary:{total_cost_rmb:'2200.00'}},settlement:{viewed_version:'V-1'}}};"
        "if(endpoint.endsWith('get_source_ai_review_status'))return {ok:true,status:'NONE'};throw new Error(endpoint)};"
        "const fixture=makeFeeInput({amount:'2000',currency:'RMB',originalAmount:'2000',originalCurrency:'RMB',forceActual:true});"
        "await workspace.saveMaterialFeeInlineAmount(fixture.amountInput);"
        "const payload=JSON.parse(calls[0].args.fee_payload);"
        "console.log(JSON.stringify({endpoints:calls.map((row)=>row.endpoint),payload,"
        "expectedModified:workspace.detailState.expectedModified,headerModified:workspace.detailState.header.modified,"
        "fees:workspace.materialFeeState.fees,preview:workspace.materialFeeState.preview,alerts:global.alerts}));"
    )

    assert result["endpoints"] == [
        "overseas_costing.api.fees.save_fee",
        "overseas_costing.api.material_fee_workspace.refresh_snapshot",
        "overseas_costing.api.materials.get_source_ai_review_status",
    ]
    assert result["payload"] == {
        "logical_fee_key": "international_sea_freight",
        "rule_code": "sea-freight",
        "expense_category": "\u56fd\u9645\u6d77\u8fd0\u8d39",
        "amount_status": "ESTIMATED",
        "amount": "2000",
        "currency": "RMB",
        "allocation_basis": "volume",
        "scope_type": "ITEMS",
        "scope_value_json": '["LINE-1"]',
        "required_evidence_role": "freight_invoice",
        "included_in_fee_key": "",
        "priority_no": 3,
        "remark": "keep me",
        "status_change_reason": "",
        "is_active": 1,
        "is_enabled": 1,
    }
    assert result["expectedModified"] == "m2"
    assert result["headerModified"] == "m2"
    assert result["fees"]["summary"]["missing_amount_fee_count"] == 0
    assert result["preview"]["summary"]["total_cost_rmb"] == "2200.00"
    assert result["alerts"][-1]["indicator"] == "green"


@pytest.mark.parametrize(
    ("amount", "currency", "message_fragment"),
    [
        ("", "RMB", "金额"),
        ("-1", "RMB", "金额"),
        ("Infinity", "RMB", "金额"),
        ("2400", "", "币种"),
        ("2400", "US", "币种"),
        ("2400", "U1D", "币种"),
        ("2400", "EUR", "币种"),
    ],
)
def test_fee_workspace_invalid_inline_value_stays_visible_without_writing(
    amount: str, currency: str, message_fragment: str
) -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'token-1',expectedModified:'m1',header:{modified:'m1'}};"
        "const fee={logical_fee_key:'international_sea_freight',expense_category:'\\u56fd\\u9645\\u6d77\\u8fd0\\u8d39',"
        "amount_status:'ACTUAL',amount:'2000',currency:'RMB',allocation_basis:'volume',scope_type:'ALL_ITEMS',scope_value_json:'[]'};"
        "workspace.materialFeeState={batchName:'B-1',fees:{fees:[fee]},requestId:0};workspace.ensureEditSession=async()=>true;"
        "workspace.renderMaterialFeeWorkspace=()=>{};workspace.normalizeErrorMessage=(error)=>error.message;let writeCalls=0;"
        "workspace.call=async(endpoint)=>{if(endpoint.endsWith('save_fee'))writeCalls+=1;return endpoint.endsWith('get_fee_worklist')?{fees:[fee]}:{ok:true,batch_modified:'m2',summary:{}}};"
        f"const fixture=makeFeeInput({{amount:{json.dumps(amount)},currency:{json.dumps(currency)},originalAmount:'2000',originalCurrency:'RMB'}});"
        "await workspace.saveMaterialFeeInlineAmount(fixture.amountInput);"
        "const draft=workspace.materialFeeState.feeDrafts?.international_sea_freight||{};"
        "console.log(JSON.stringify({writeCalls,amount:fixture.amountInput.val(),currency:fixture.currencyInput.val(),"
        "draft,errorText:fixture.errorElement.text(),red:fixture.cell.hasClass('is-save-error'),"
        "amountInvalid:fixture.amountInput.attr('aria-invalid'),currencyInvalid:fixture.currencyInput.attr('aria-invalid'),"
        "describedBy:fixture.amountInput.attr('aria-describedby')}));"
    )

    assert result["writeCalls"] == 0
    assert result["amount"] == amount
    assert result["currency"] == currency
    assert result["draft"]["amount"] == amount
    assert result["draft"]["currency"] == currency
    assert message_fragment in result["draft"]["error"]
    assert message_fragment in result["errorText"]
    assert result["red"] is True
    assert result["amountInvalid"] == "true"
    assert result["currencyInvalid"] == "true"
    assert result["describedBy"]


def test_fee_workspace_missing_saved_amount_currency_blur_also_submits_estimate() -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'token-1',expectedModified:'m1',header:{modified:'m1'}};"
        "const fee={logical_fee_key:'international_sea_freight',expense_category:'\\u56fd\\u9645\\u6d77\\u8fd0\\u8d39',"
        "amount_status:'MISSING',amount:'2000',currency:'RMB',allocation_basis:'volume',scope_type:'ALL_ITEMS',scope_value_json:'[]'};"
        "workspace.materialFeeState={batchName:'B-1',fees:{fees:[fee]},requestId:0};workspace.ensureEditSession=async()=>true;"
        "workspace.renderMaterialFeeWorkspace=()=>{};workspace.loadMaterialFeeWorkspace=async()=>true;let payload=null;workspace.call=async(endpoint,args)=>{"
        "if(endpoint.endsWith('save_fee')){payload=JSON.parse(args.fee_payload);return {ok:true,batch_modified:'m2'}}"
        "if(endpoint.endsWith('get_fee_worklist'))return {fees:[{...fee,amount_status:'ACTUAL'}]};return {summary:{}}};"
        "const fixture=makeFeeInput({amount:'2000',currency:'RMB',originalAmount:'2000',originalCurrency:'RMB',forceActual:true});"
        "await workspace.saveMaterialFeeInlineAmount(fixture.currencyInput);"
        "console.log(JSON.stringify({payload}));"
    )

    assert result["payload"]["amount_status"] == "ESTIMATED"
    assert result["payload"]["amount"] == "2000"
    assert result["payload"]["currency"] == "RMB"


def test_fee_workspace_unchanged_actual_blur_does_not_leave_stale_draft() -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);workspace.detailState={batchName:'B-1'};"
        "const fee={logical_fee_key:'fee-a',expense_category:'A 费用',amount_status:'ACTUAL',amount:'100',currency:'RMB'};"
        "workspace.materialFeeState={batchName:'B-1',fees:{fees:[fee]},feeDrafts:{},requestId:0,feeRequestId:0};let calls=0;"
        "workspace.call=async()=>{calls+=1};const fixture=makeFeeInput({amount:'100',currency:'RMB',originalAmount:'100',originalCurrency:'RMB',feeKey:'fee-a'});"
        "await workspace.saveMaterialFeeInlineAmount(fixture.amountInput);"
        "console.log(JSON.stringify({calls,draft:workspace.materialFeeState.feeDrafts['fee-a']||null}));"
    )

    assert result == {"calls": 0, "draft": None}


def test_fee_workspace_render_restores_draft_error_and_accessible_labels() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);workspace.escape=(value)=>String(value??'').replaceAll('&','&amp;').replaceAll('\\\"','&quot;');"
        "workspace.materialFeeState={feeDrafts:{'legacy:weird/key':{amount:'2400.5',currency:'USD',error:'\\u5e76\\u53d1\\u51b2\\u7a81'}}};"
        "const html=workspace.renderMaterialFeeRow({logical_fee_key:'legacy:weird/key',expense_category:'\\u7279\\u6b8a\\u8d39\\u7528',"
        "amount_status:'ACTUAL',amount:'2000',currency:'RMB',allocation_basis:'volume',scope_type:'ALL_ITEMS',allocation:{status:'ALLOCATED'},evidence:[]});"
        "console.log(JSON.stringify({draftAmount:html.includes('value=\\\"2400.5\\\"'),draftCurrency:html.includes('value=\\\"USD\\\"'),"
        "inlineError:html.includes('\\u5e76\\u53d1\\u51b2\\u7a81'),invalid:html.includes('aria-invalid=\\\"true\\\"'),"
        "safeId:html.includes('ocw-mf-fee-error-legacy_58_weird_47_key'),amountLabel:html.includes('aria-label=\\\"特殊费用原币金额\\\"'),"
        "currencyLabel:html.includes('aria-label=\\\"特殊费用币种\\\"')}));"
    )

    assert result == {
        "draftAmount": True,
        "draftCurrency": True,
        "inlineError": True,
        "invalid": True,
        "safeId": True,
        "amountLabel": True,
        "currencyLabel": True,
    }


def test_fee_workspace_save_one_row_preserves_other_draft_and_restores_focus() -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'token-1',expectedModified:'m1',header:{modified:'m1'}};"
        "const feeA={logical_fee_key:'fee-a',expense_category:'A 费用',amount_status:'ACTUAL',amount:'100',currency:'RMB',allocation_basis:'volume',scope_type:'ALL_ITEMS',scope_value_json:'[]',allocation:{status:'ALLOCATED'},evidence:[]};"
        "const feeB={...feeA,logical_fee_key:'fee-b',expense_category:'B 费用',amount:'200'};"
        "workspace.materialFeeState={batchName:'B-1',page:1,pageLength:200,fees:{fees:[feeA,feeB],summary:{}},materials:{items:[]},preview:{summary:{}},requestId:0,feeRequestId:0,feeDrafts:{},focusedFeeInput:null};"
        "workspace.ensureEditSession=async()=>true;workspace.escape=(value)=>String(value??'');workspace.formatValue=(value)=>String(value??'');"
        "workspace.renderMaterialFeeGrid=()=>'';workspace.renderMaterialFeeTodos=()=>'';workspace.renderMaterialFeeCostTable=()=>'';"
        "let html='';const content={html:(value)=>{html=value}};let focusCalls=0;const focusTarget={"
        "attr:(name)=>name==='data-fee-key'?'fee-b':name==='data-mf-fee-input'?'amount':'',"
        "focus:()=>{focusCalls+=1},value:'250',setSelectionRange:()=>{}};"
        "const allInputs={each:(callback)=>{callback(0,focusTarget)}};workspace.$root={find:(selector)=>selector===\"[data-area='detail-content']\"?content:selector==='[data-mf-fee-input]'?allInputs:{length:0}};global.$=(value)=>value;"
        "workspace.getDetailBatch=()=>({name:'B-1',current_version:'V-1'});"
        "let resolveSave;workspace.call=async(endpoint)=>{if(endpoint.endsWith('save_fee'))return await new Promise((resolve)=>{resolveSave=resolve});"
        "if(endpoint.endsWith('refresh_snapshot'))return {ok:true,batch_name:'B-1',version_name:'V-1',cache:{status:'ready'},data:{"
        "detail:{ok:true,batch_name:'B-1',version_name:'V-1',header:{name:'B-1',modified:'m2'}},materials:{items:[]},"
        "fees:{fees:[feeA,feeB],summary:{}},preview:{summary:{}},settlement:{viewed_version:'V-1'}}};"
        "if(endpoint.endsWith('get_source_ai_review_status'))return {ok:true,status:'NONE'};throw new Error(endpoint)};"
        "const fixtureA=makeFeeInput({amount:'150',currency:'RMB',originalAmount:'100',originalCurrency:'RMB',feeKey:'fee-a'});"
        "const fixtureB=makeFeeInput({amount:'250',currency:'USD',originalAmount:'200',originalCurrency:'RMB',feeKey:'fee-b'});"
        "const saving=workspace.saveMaterialFeeInlineAmount(fixtureA.amountInput);await new Promise((resolve)=>setImmediate(resolve));"
        "workspace.updateMaterialFeeDraftFromInput(fixtureB.amountInput);workspace.materialFeeState.focusedFeeInput={feeKey:'fee-b',field:'amount'};"
        "resolveSave({ok:true,batch_modified:'m2'});await saving;"
        "console.log(JSON.stringify({draftA:workspace.materialFeeState.feeDrafts['fee-a']||null,draftB:workspace.materialFeeState.feeDrafts['fee-b']||null,"
        "focusCalls,bRendered:html.includes('data-fee-key=\\\"fee-b\\\"')&&html.includes('value=\\\"250\\\"')&&html.includes('value=\\\"USD\\\"')}));"
    )

    assert result["draftA"] is None
    assert result["draftB"]["amount"] == "250"
    assert result["draftB"]["currency"] == "USD"
    assert result["focusCalls"] == 1
    assert result["bRendered"] is True


def test_fee_workspace_inline_save_failure_preserves_value_and_allows_retry() -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'token-1',expectedModified:'m1',header:{name:'B-1',modified:'m1'}};"
        "workspace.materialFeeState={batchName:'B-1',fees:{fees:[{logical_fee_key:'international_sea_freight',"
        "expense_category:'\u56fd\u9645\u6d77\u8fd0\u8d39',amount_status:'ACTUAL',amount:'2000',currency:'RMB',"
        "allocation_basis:'volume',scope_type:'ALL_ITEMS',scope_value_json:'[]'}]},requestId:0};"
        "workspace.ensureEditSession=async()=>true;workspace.normalizeErrorMessage=(error)=>error.message;"
        "const endpoints=[];workspace.call=async(endpoint)=>{endpoints.push(endpoint);throw new Error(endpoint.endsWith('save_fee')?'\u5e76\u53d1\u51b2\u7a81':'\u540c\u6b65\u5931\u8d25')};"
        "const fixture=makeFeeInput({amount:'2400',currency:'USD',originalAmount:'2000',originalCurrency:'RMB'});"
        "await workspace.saveMaterialFeeInlineAmount(fixture.amountInput);"
        "console.log(JSON.stringify({amount:fixture.amountInput.val(),currency:fixture.currencyInput.val(),"
        "disabled:fixture.amountInput.disabled||fixture.currencyInput.disabled,saving:fixture.cell.data('saving'),"
        "error:fixture.cell.hasClass('is-save-error'),title:fixture.attrs.title,errorText:fixture.errorElement.text(),"
        "amountInvalid:fixture.amountInput.attr('aria-invalid'),describedBy:fixture.amountInput.attr('aria-describedby'),alerts:global.alerts,endpoints}));"
    )

    assert result["amount"] == "2400"
    assert result["currency"] == "USD"
    assert result["disabled"] is False
    assert result["saving"] is False
    assert result["error"] is True
    assert "\u5e76\u53d1\u51b2\u7a81" in result["title"]
    assert "\u9875\u9762\u5237\u65b0" in result["title"]
    assert "\u5e76\u53d1\u51b2\u7a81" in result["errorText"]
    assert "\u9875\u9762\u5237\u65b0" in result["errorText"]
    assert result["amountInvalid"] == "true"
    assert result["describedBy"]
    assert result["endpoints"] == [
        "overseas_costing.api.fees.save_fee",
        "overseas_costing.api.material_fee_workspace.refresh_snapshot",
    ]
    assert result["alerts"][-1]["indicator"] == "red"
    assert "\u5e76\u53d1\u51b2\u7a81" in result["alerts"][-1]["message"]
    assert "\u9875\u9762\u5237\u65b0" in result["alerts"][-1]["message"]


def test_fee_workspace_conflict_refreshes_revision_and_latest_fee_before_explicit_retry() -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'token-1',expectedModified:'m1',header:{name:'B-1',modified:'m1'}};"
        "const oldFee={logical_fee_key:'international_sea_freight',rule_code:'old-rule',expense_category:'\u56fd\u9645\u6d77\u8fd0\u8d39',"
        "amount_status:'ACTUAL',amount:'2000',currency:'RMB',allocation_basis:'volume',scope_type:'ALL_ITEMS',scope_value_json:'[]',remark:'old'};"
        "const latestFee={...oldFee,rule_code:'latest-rule',allocation_basis:'gross_weight',remark:'latest'};"
        "workspace.materialFeeState={batchName:'B-1',fees:{fees:[oldFee]},preview:{summary:{}},requestId:0};"
        "workspace.ensureEditSession=async()=>true;workspace.normalizeErrorMessage=(error)=>error.message;let renders=0;"
        "workspace.renderMaterialFeeWorkspace=()=>{renders+=1};workspace.getDetailBatch=()=>({name:'B-1',current_version:'V-1'});"
        "const saves=[];const endpoints=[];let snapshotCalls=0;"
        "workspace.call=async(endpoint,args)=>{endpoints.push(endpoint);"
        "if(endpoint.endsWith('save_fee')){saves.push(args);if(saves.length===1)throw new Error('\u5e76\u53d1\u51b2\u7a81');"
        "if(args.expected_modified!=='m2')throw new Error('\u4ecd\u4f7f\u7528\u65e7\u7248\u672c');return {ok:true,batch_modified:'m3',message:'saved'}}"
        "if(endpoint.endsWith('refresh_snapshot')){snapshotCalls+=1;const modified=snapshotCalls===1?'m2':'m3';return {ok:true,batch_name:'B-1',version_name:'V-1',cache:{status:'ready'},data:{"
        "detail:{ok:true,batch_name:'B-1',version_name:'V-1',header:{name:'B-1',modified}},materials:{items:[]},"
        "fees:{summary:{},fees:[latestFee]},preview:{summary:{total_cost_rmb:'2400.00'}},settlement:{viewed_version:'V-1'}}}}"
        "if(endpoint.endsWith('get_source_ai_review_status'))return {ok:true,status:'NONE'};throw new Error(endpoint)};"
        "const fixture=makeFeeInput({amount:'2400',currency:'USD',originalAmount:'2000',originalCurrency:'RMB'});"
        "await workspace.saveMaterialFeeInlineAmount(fixture.amountInput);"
        "const afterConflict={saveCount:saves.length,expectedModified:workspace.detailState.expectedModified,"
        "headerModified:workspace.detailState.header.modified,fee:workspace.materialFeeState.fees.fees[0],"
        "amount:fixture.amountInput.val(),currency:fixture.currencyInput.val(),red:fixture.cell.hasClass('is-save-error'),renders};"
        "await workspace.saveMaterialFeeInlineAmount(fixture.amountInput);"
        "console.log(JSON.stringify({afterConflict,saveCount:saves.length,secondExpected:saves[1]?.expected_modified,"
        "secondPayload:JSON.parse(saves[1]?.fee_payload||'{}'),finalExpected:workspace.detailState.expectedModified,endpoints}));"
    )

    assert result["afterConflict"] == {
        "saveCount": 1,
        "expectedModified": "m2",
        "headerModified": "m2",
        "fee": {
            "logical_fee_key": "international_sea_freight",
            "rule_code": "latest-rule",
            "expense_category": "\u56fd\u9645\u6d77\u8fd0\u8d39",
            "amount_status": "ACTUAL",
            "amount": "2000",
            "currency": "RMB",
            "allocation_basis": "gross_weight",
            "scope_type": "ALL_ITEMS",
            "scope_value_json": "[]",
            "remark": "latest",
        },
        "amount": "2400",
        "currency": "USD",
        "red": True,
        "renders": 0,
    }
    assert result["saveCount"] == 2
    assert result["secondExpected"] == "m2"
    assert result["secondPayload"]["amount"] == "2400"
    assert result["secondPayload"]["currency"] == "USD"
    assert result["secondPayload"]["rule_code"] == "latest-rule"
    assert result["secondPayload"]["allocation_basis"] == "gross_weight"
    assert result["secondPayload"]["remark"] == "latest"
    assert result["finalExpected"] == "m3"


def test_fee_workspace_edit_session_failure_is_visible_and_refreshes_readonly_state() -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'',expectedModified:'m1',header:{name:'B-1',modified:'m1'}};"
        "const fee={logical_fee_key:'international_sea_freight',expense_category:'\u56fd\u9645\u6d77\u8fd0\u8d39',amount_status:'ACTUAL',amount:'2000',currency:'RMB',allocation_basis:'volume',scope_type:'ALL_ITEMS',scope_value_json:'[]'};"
        "workspace.materialFeeState={batchName:'B-1',fees:{fees:[fee]},preview:{summary:{}},requestId:0};"
        "workspace.ensureEditSession=async()=>false;workspace.normalizeErrorMessage=(error)=>error.message;const endpoints=[];"
        "workspace.call=async(endpoint)=>{endpoints.push(endpoint);if(endpoint.endsWith('refresh_snapshot'))return {ok:true,cache:{status:'ready'},data:{"
        "detail:{ok:true,batch_name:'B-1',version_name:'V-1',header:{modified:'m2'}},materials:{items:[]},fees:{fees:[fee]},preview:{summary:{}}}};throw new Error(endpoint)};"
        "const fixture=makeFeeInput({amount:'2400',currency:'RMB',originalAmount:'2000',originalCurrency:'RMB'});"
        "await workspace.saveMaterialFeeInlineAmount(fixture.amountInput);"
        "console.log(JSON.stringify({red:fixture.cell.hasClass('is-save-error'),disabled:fixture.amountInput.disabled,"
        "expectedModified:workspace.detailState.expectedModified,endpoints,alerts:global.alerts}));"
    )

    assert result["red"] is True
    assert result["disabled"] is False
    assert result["expectedModified"] == "m2"
    assert "overseas_costing.api.fees.save_fee" not in result["endpoints"]
    assert result["endpoints"] == [
        "overseas_costing.api.material_fee_workspace.refresh_snapshot",
    ]
    assert result["alerts"][-1]["indicator"] == "red"


def test_fee_workspace_manual_reload_refreshes_batch_modified() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',expectedModified:'m1',header:{name:'B-1',modified:'m1'}};"
        "workspace.materialFeeState={batchName:'B-1',page:1,pageLength:200,requestId:0};"
        "workspace.getDetailBatch=()=>({name:'B-1',current_version:'V-1'});workspace.renderDetailTabLoading=()=>{};"
        "let renders=0;workspace.renderMaterialFeeWorkspace=()=>{renders+=1};const endpoints=[];"
        "workspace.call=async(endpoint)=>{endpoints.push(endpoint);if(endpoint.endsWith('refresh_snapshot'))"
        "return {ok:true,batch_name:'B-1',version_name:'V-1',cache:{status:'ready',input_fingerprint:'fp-2'},data:{"
        "detail:{ok:true,batch_name:'B-1',version_name:'V-1',header:{name:'B-1',modified:'m2',status:'Dirty'}},"
        "materials:{items:[]},fees:{fees:[],summary:{}},preview:{summary:{}},settlement:{viewed_version:'V-1'}}};"
        "if(endpoint.endsWith('get_source_ai_review_status'))return {ok:true,status:'NONE'};"
        "throw new Error('unexpected endpoint '+endpoint)};await workspace.loadMaterialFeeWorkspace({forceRefresh:true});"
        "console.log(JSON.stringify({expectedModified:workspace.detailState.expectedModified,"
        "header:workspace.detailState.header,endpoints,renders}));"
    )

    assert result["expectedModified"] == "m2"
    assert result["header"]["modified"] == "m2"
    assert result["header"]["status"] == "Dirty"
    assert result["endpoints"] == [
        "overseas_costing.api.material_fee_workspace.refresh_snapshot",
        "overseas_costing.api.materials.get_source_ai_review_status",
    ]
    assert result["renders"] == 1


def test_fee_workspace_renders_snapshot_before_ai_and_freshness_finish() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',expectedModified:'m1',header:{name:'B-1',modified:'m1'}};"
        "workspace.materialFeeState={batchName:'B-1',page:1,pageLength:200,requestId:0};"
        "workspace.getDetailBatch=()=>({name:'B-1',current_version:'V-1'});workspace.renderDetailTabLoading=()=>{};"
        "let renders=0;workspace.renderMaterialFeeWorkspace=()=>{renders+=1};const endpoints=[];let releaseAI,releaseFresh;"
        "workspace.call=async(endpoint)=>{endpoints.push(endpoint);"
        "if(endpoint.endsWith('get_snapshot'))return {ok:true,batch_name:'B-1',version_name:'V-1',cache:{status:'ready',served_from_cache:true,input_fingerprint:'fp-1'},data:{"
        "detail:{ok:true,batch_name:'B-1',version_name:'V-1',header:{name:'B-1',modified:'m2'}},"
        "materials:{items:[{name:'FAST'}]},fees:{fees:[],summary:{}},preview:{summary:{total_cost_rmb:'100.00'}},settlement:{viewed_version:'V-1'}}};"
        "if(endpoint.endsWith('get_source_ai_review_status'))return await new Promise(resolve=>{releaseAI=resolve});"
        "if(endpoint.endsWith('check_freshness'))return await new Promise(resolve=>{releaseFresh=resolve});"
        "throw new Error('unexpected endpoint '+endpoint)};"
        "const loaded=await workspace.loadMaterialFeeWorkspace();"
        "const before={loaded,renders,endpoints:[...endpoints],item:workspace.materialFeeState.materials.items[0].name,cache:workspace.materialFeeState.cache};"
        "releaseAI({ok:true,status:'NONE'});releaseFresh({ok:true,unchanged:true,current_fingerprint:'fp-1',checked_at:'now'});"
        "await new Promise(resolve=>setImmediate(resolve));"
        "console.log(JSON.stringify({before,afterRenders:renders}));"
    )

    assert result["before"]["loaded"] is True
    assert result["before"]["renders"] == 1
    assert result["before"]["item"] == "FAST"
    assert result["before"]["cache"]["served_from_cache"] is True
    assert result["before"]["endpoints"] == [
        "overseas_costing.api.material_fee_workspace.get_snapshot",
        "overseas_costing.api.materials.get_source_ai_review_status",
        "overseas_costing.api.material_fee_workspace.check_freshness",
    ]


def test_fee_workspace_changed_fingerprint_refreshes_without_reusing_old_response() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',header:{name:'B-1'}};"
        "workspace.materialFeeState={batchName:'B-1',page:1,pageLength:200,requestId:0};"
        "workspace.getDetailBatch=()=>({name:'B-1',current_version:'V-1'});workspace.renderDetailTabLoading=()=>{};"
        "let renders=0;workspace.renderMaterialFeeWorkspace=()=>{renders+=1};workspace.renderMaterialFeeWorkspacePreservingPosition=()=>{renders+=1};"
        "const endpoints=[];const makeSnapshot=(name,modified,fp)=>({ok:true,batch_name:'B-1',version_name:'V-1',cache:{status:'ready',input_fingerprint:fp},data:{"
        "detail:{ok:true,batch_name:'B-1',version_name:'V-1',header:{name:'B-1',modified}},materials:{items:[{name}]},"
        "fees:{fees:[],summary:{}},preview:{summary:{}},settlement:{viewed_version:'V-1'}}});"
        "workspace.call=async(endpoint)=>{endpoints.push(endpoint);if(endpoint.endsWith('get_snapshot'))return makeSnapshot('OLD','m1','fp-1');"
        "if(endpoint.endsWith('get_source_ai_review_status'))return {ok:true,status:'NONE'};"
        "if(endpoint.endsWith('check_freshness'))return {ok:true,unchanged:false,current_fingerprint:'fp-2',checked_at:'now'};"
        "if(endpoint.endsWith('refresh_snapshot'))return makeSnapshot('NEW','m2','fp-2');throw new Error(endpoint)};"
        "await workspace.loadMaterialFeeWorkspace();await new Promise(resolve=>setImmediate(resolve));"
        "console.log(JSON.stringify({item:workspace.materialFeeState.materials.items[0].name,modified:workspace.detailState.expectedModified,"
        "fingerprint:workspace.materialFeeState.cache.input_fingerprint,renders,endpoints}));"
    )

    assert result["item"] == "NEW"
    assert result["modified"] == "m2"
    assert result["fingerprint"] == "fp-2"
    assert result["renders"] == 2
    assert result["endpoints"][-2:] == [
        "overseas_costing.api.material_fee_workspace.check_freshness",
        "overseas_costing.api.material_fee_workspace.refresh_snapshot",
    ]


def test_fee_workspace_freshness_failure_keeps_last_good_snapshot_visible() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',header:{name:'B-1'}};"
        "workspace.materialFeeState={batchName:'B-1',page:1,pageLength:200,requestId:0};"
        "workspace.getDetailBatch=()=>({name:'B-1',current_version:'V-1'});workspace.renderDetailTabLoading=()=>{};workspace.normalizeErrorMessage=e=>e.message;"
        "let renders=0;workspace.renderMaterialFeeWorkspace=()=>{renders+=1};workspace.call=async(endpoint)=>{"
        "if(endpoint.endsWith('get_snapshot'))return {ok:true,batch_name:'B-1',version_name:'V-1',cache:{status:'ready',input_fingerprint:'fp-1'},data:{"
        "detail:{ok:true,batch_name:'B-1',version_name:'V-1',header:{name:'B-1',modified:'m1'}},materials:{items:[{name:'TRUSTED'}]},"
        "fees:{fees:[],summary:{}},preview:{summary:{}},settlement:{viewed_version:'V-1'}}};"
        "if(endpoint.endsWith('get_source_ai_review_status'))return {ok:true,status:'NONE'};"
        "if(endpoint.endsWith('check_freshness'))throw new Error('本地数据库繁忙');throw new Error(endpoint)};"
        "await workspace.loadMaterialFeeWorkspace();await new Promise(resolve=>setImmediate(resolve));"
        "console.log(JSON.stringify({item:workspace.materialFeeState.materials.items[0].name,renders,cache:workspace.materialFeeState.cache}));"
    )

    assert result["item"] == "TRUSTED"
    assert result["renders"] == 1
    assert result["cache"]["status"] == "stale"
    assert result["cache"]["refresh_error"] == "本地数据库繁忙"


def test_all_material_fee_metrics_use_buttons_and_green_checks_when_clear() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);workspace.escape=value=>String(value??'');"
        "console.log(JSON.stringify({"
        "materials:workspace.renderMaterialFeeMetric('基础资料待补',0,'danger','materials','装箱单物料信息'),"
        "fee:workspace.renderMaterialFeeMetric('费用金额待补',0,'danger','fees','运费、税费、清关费等运输费用'),"
        "evidence:workspace.renderMaterialFeeMetric('费用凭证未齐',0,'warn','fees','运费、税费、关税等费用凭证'),"
        "estimated:workspace.renderMaterialFeeMetric('实际费用待确认',0,'warn','fees','当前使用暂估金额，等待确认实际金额'),"
        "nonzero:workspace.renderMaterialFeeMetric('基础资料待补',2,'danger','materials','装箱单物料信息')}));"
    )

    for key in ("materials", "fee", "evidence", "estimated"):
        assert result[key].startswith('<button class="ocw-mf-metric is-cleared"')
        assert 'data-action="mf-jump-status"' in result[key]
        assert "✓" in result[key]
        assert "已处理" in result[key]
        assert ">0<" not in result[key]
    assert 'data-target="materials"' in result["materials"]
    assert 'data-target="fees"' in result["fee"]
    assert "运费、税费、关税等费用凭证" in result["evidence"]
    assert "is-cleared" not in result["nonzero"] and ">2<" in result["nonzero"]


def test_material_fee_metric_jump_only_scrolls_and_focuses_target_section() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);"
        "const state={onlyMissing:false,marker:'unchanged'};workspace.materialFeeState=state;"
        "const calls=[];const element={scrollIntoView:options=>calls.push(['scroll',options]),focus:options=>calls.push(['focus',options])};"
        "workspace.$root={find:selector=>({length:1,0:element})};"
        "const exists=typeof workspace.jumpToMaterialFeeSection==='function';"
        "if(exists){workspace.jumpToMaterialFeeSection('fees');workspace.jumpToMaterialFeeSection('materials');}"
        "console.log(JSON.stringify({exists,calls,state,hasHandler:Harness.toString().includes(\"[data-action='mf-jump-status']\")}));"
    )

    assert result["exists"] is True
    assert result["hasHandler"] is True
    assert result["calls"] == [
        ["scroll", {"behavior": "smooth", "block": "start"}],
        ["focus", {"preventScroll": True}],
        ["scroll", {"behavior": "smooth", "block": "start"}],
        ["focus", {"preventScroll": True}],
    ]
    assert result["state"] == {"onlyMissing": False, "marker": "unchanged"}


def test_material_fee_metric_styles_keep_buttons_responsive_and_focus_visible() -> None:
    stylesheet = (PARTS / "48-material-fee-workspace.css").read_text(encoding="utf-8")

    metric_rule = stylesheet.split(".ocw-mf-metric {", 1)[1].split("}", 1)[0]
    assert "cursor: pointer" in metric_rule
    assert "font: inherit" in metric_rule
    assert ".ocw-mf-metric:focus-visible" in stylesheet
    assert ".ocw-mf-section[data-mf-status-section]" in stylesheet
    assert "scroll-margin-top" in stylesheet
    assert "@media (max-width: 1050px)" in stylesheet
    assert "@media (max-width: 640px)" in stylesheet
    mobile_rule = stylesheet.split("@media (max-width: 640px)", 1)[1]
    assert ".ocw-mf-section-title { flex-direction: column; align-items: stretch; }" in mobile_rule
    assert ".ocw-mf-cost-actions { justify-content: flex-start; }" in mobile_rule
    assert stylesheet.index(".ocw-mf-cost-actions { display: flex;") < stylesheet.index(
        ".ocw-mf-cost-actions { justify-content: flex-start; }"
    )


def test_cost_trial_dialog_defines_visible_brand_buttons_and_narrow_layout() -> None:
    stylesheet = (PARTS / "48-material-fee-workspace.css").read_text(encoding="utf-8")
    source = (PARTS / "78-material-fee-workspace.js").read_text(encoding="utf-8")

    # 品牌色由 .modal 上的共享调色板提供（见
    # test_dialog_theme_palette_is_declared_once_for_every_modal），本弹窗只按变量取色。
    primary_rule = stylesheet.split(".ocw-cost-trial-dialog .ocw-primary-btn", 1)[1].split("}", 1)[0]
    assert "color: #fff" in primary_rule
    assert "var(--ocw-brand" in primary_rule
    assert ".ocw-cost-trial-dialog .modal-content" in stylesheet
    assert "max-height: calc(100vh - 24px)" in stylesheet
    assert ".ocw-cost-trial-dialog .modal-body" in stylesheet
    assert "overflow: hidden" in stylesheet.split(
        ".ocw-cost-trial-dialog .modal-body", 1
    )[1].split("}", 1)[0]
    assert ".ocw-cost-trial-review > main" in stylesheet
    assert "overflow: auto" in stylesheet.split(
        ".ocw-cost-trial-review > main", 1
    )[1].split("}", 1)[0]
    assert ".ocw-cost-trial-review > footer" not in stylesheet
    assert ".ocw-cost-trial-secondary-actions" in stylesheet
    assert 'find?.(".modal-footer")' in source
    assert 'class="ocw-cost-trial-secondary-actions"' in source
    assert ".insertBefore?.($anchor)" in source
    mobile = stylesheet.split("@media (max-width: 720px)", 1)[1]
    assert ".ocw-cost-trial-dialog .modal-footer" in mobile
    assert ".ocw-cost-trial-dialog .modal-footer .btn-primary" in mobile
    assert "order: -3" in mobile
    assert "width: 100%" in mobile
    assert ".ocw-cost-trial-secondary-actions" in mobile
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in mobile


def test_fee_workspace_save_does_not_render_after_switching_to_vouchers() -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'token-1',expectedModified:'m1',header:{modified:'m1'}};"
        "const fee={logical_fee_key:'international_sea_freight',expense_category:'\u56fd\u9645\u6d77\u8fd0\u8d39',amount_status:'ACTUAL',amount:'2000',currency:'RMB',allocation_basis:'volume',scope_type:'ALL_ITEMS',scope_value_json:'[]'};"
        "workspace.materialFeeState={batchName:'B-1',fees:{fees:[fee]},preview:{summary:{}},requestId:0};"
        "workspace.ensureEditSession=async()=>true;workspace.normalizeErrorMessage=(error)=>error.message;let renders=0;"
        "workspace.renderMaterialFeeWorkspace=()=>{renders+=1};workspace.getDetailBatch=()=>({name:'B-1',current_version:'V-1'});"
        "let releaseReads;const readsGate=new Promise((resolve)=>{releaseReads=resolve});workspace.call=async(endpoint)=>{"
        "if(endpoint.endsWith('save_fee'))return {ok:true,batch_modified:'m2'};await readsGate;"
        "if(endpoint.endsWith('get_batch_detail'))return {ok:true,header:{name:'B-1',modified:'m2'}};"
        "if(endpoint.endsWith('get_material_grid'))return {items:[]};if(endpoint.endsWith('get_fee_worklist'))return {fees:[fee]};"
        "return {summary:{total_cost_rmb:'2400.00'}}};"
        "const fixture=makeFeeInput({amount:'2400',currency:'RMB',originalAmount:'2000',originalCurrency:'RMB'});"
        "const saving=workspace.saveMaterialFeeInlineAmount(fixture.amountInput);await new Promise((resolve)=>setImmediate(resolve));"
        "workspace.detailState.tab='vouchers';releaseReads();"
        "await saving;console.log(JSON.stringify({renders,tab:workspace.detailState.tab}));"
    )

    assert result == {"renders": 0, "tab": "vouchers"}


def test_fee_workspace_save_response_does_not_mutate_a_new_batch() -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'token-1',expectedModified:'m1',header:{name:'B-1',modified:'m1'}};"
        "const fee={logical_fee_key:'international_sea_freight',expense_category:'\u56fd\u9645\u6d77\u8fd0\u8d39',amount_status:'ACTUAL',amount:'2000',currency:'RMB',allocation_basis:'volume',scope_type:'ALL_ITEMS',scope_value_json:'[]'};"
        "workspace.materialFeeState={batchName:'B-1',fees:{fees:[fee]},preview:{summary:{}},requestId:0};"
        "workspace.ensureEditSession=async()=>true;workspace.normalizeErrorMessage=(error)=>error.message;let renders=0;"
        "workspace.renderMaterialFeeWorkspace=()=>{renders+=1};let resolveSave;const endpoints=[];"
        "workspace.call=async(endpoint)=>{endpoints.push(endpoint);if(endpoint.endsWith('save_fee'))"
        "return await new Promise((resolve)=>{resolveSave=resolve});if(endpoint.endsWith('get_fee_worklist'))return {fees:[]};return {summary:{}}};"
        "const fixture=makeFeeInput({amount:'2400',currency:'RMB',originalAmount:'2000',originalCurrency:'RMB'});"
        "const saving=workspace.saveMaterialFeeInlineAmount(fixture.amountInput);await new Promise((resolve)=>setImmediate(resolve));"
        "workspace.detailState={batchName:'B-2',versionName:'V-2',tab:'documents',editToken:'token-2',expectedModified:'n1',header:{name:'B-2',modified:'n1'}};"
        "workspace.materialFeeState={batchName:'B-2',fees:{fees:[]},preview:{summary:{}},requestId:0};"
        "resolveSave({ok:true,batch_modified:'m2'});await saving;"
        "console.log(JSON.stringify({renders,expectedModified:workspace.detailState.expectedModified,"
        "headerModified:workspace.detailState.header.modified,endpoints,disabled:fixture.amountInput.disabled}));"
    )

    assert result == {
        "renders": 0,
        "expectedModified": "n1",
        "headerModified": "n1",
        "endpoints": ["overseas_costing.api.fees.save_fee"],
        "disabled": True,
    }


def test_fee_workspace_load_does_not_apply_previous_batch_results() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',expectedModified:'m1',header:{name:'B-1',modified:'m1'}};"
        "workspace.materialFeeState={batchName:'B-1',page:1,pageLength:200,requestId:0};"
        "workspace.getDetailBatch=()=>({name:'B-1',current_version:'V-1'});workspace.renderDetailTabLoading=()=>{};"
        "let renders=0;workspace.renderMaterialFeeWorkspace=()=>{renders+=1};let release;const gate=new Promise((resolve)=>{release=resolve});"
        "workspace.call=async(endpoint)=>{await gate;if(endpoint.endsWith('get_batch_detail'))return {ok:true,header:{modified:'m2'}};"
        "if(endpoint.endsWith('get_material_grid'))return {items:[{name:'OLD'}]};if(endpoint.endsWith('get_fee_worklist'))return {fees:[]};return {summary:{}}};"
        "const loading=workspace.loadMaterialFeeWorkspace();workspace.detailState.batchName='B-2';release();await loading;"
        "console.log(JSON.stringify({renders,expectedModified:workspace.detailState.expectedModified,materials:workspace.materialFeeState.materials||null}));"
    )

    assert result == {"renders": 0, "expectedModified": "m1", "materials": None}


def test_fee_workspace_local_recovery_does_not_cancel_full_reload() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',expectedModified:'m1',header:{name:'B-1',modified:'m1'}};"
        "workspace.materialFeeState={batchName:'B-1',page:1,pageLength:200,loading:false,requestId:0,feeRequestId:0,"
        "feeDrafts:{'fee-a':{amount:'2400',currency:'USD',error:'\\u5e76\\u53d1\\u51b2\\u7a81',touched:true}},materials:null,fees:null,preview:null};"
        "workspace.getDetailBatch=()=>({name:'B-1',current_version:'V-1'});workspace.renderDetailTabLoading=()=>{};let renders=0;"
        "workspace.renderMaterialFeeWorkspace=()=>{renders+=1};let releaseFull;const fullGate=new Promise((resolve)=>{releaseFull=resolve});"
        "workspace.call=async(endpoint)=>{if(endpoint.endsWith('get_snapshot')){await fullGate;return {ok:true,batch_name:'B-1',version_name:'V-1',cache:{status:'ready'},data:{"
        "detail:{ok:true,batch_name:'B-1',version_name:'V-1',header:{modified:'m3'}},materials:{items:[{name:'FULL'}]},"
        "fees:{fees:[{logical_fee_key:'fee-full'}]},preview:{summary:{source:'full'}},settlement:{viewed_version:'V-1'}}}}"
        "if(endpoint.endsWith('refresh_snapshot'))return {ok:true,batch_name:'B-1',version_name:'V-1',cache:{status:'ready'},data:{"
        "detail:{ok:true,batch_name:'B-1',version_name:'V-1',header:{modified:'m2'}},materials:{items:[{name:'RECOVERY'}]},"
        "fees:{fees:[{logical_fee_key:'fee-recovery'}]},preview:{summary:{source:'recovery'}},settlement:{viewed_version:'V-1'}}};"
        "if(endpoint.endsWith('get_source_ai_review_status'))return {ok:true,status:'NONE'};throw new Error(endpoint)};"
        "const full=workspace.loadMaterialFeeWorkspace();await new Promise((resolve)=>setImmediate(resolve));"
        "const recovered=await workspace.recoverMaterialFeeReadonlyState('B-1');releaseFull();await full;"
        "console.log(JSON.stringify({recovered,loading:workspace.materialFeeState.loading,renders,requestId:workspace.materialFeeState.requestId,"
        "feeRequestId:workspace.materialFeeState.feeRequestId,expectedModified:workspace.detailState.expectedModified,"
        "materials:workspace.materialFeeState.materials,draft:workspace.materialFeeState.feeDrafts['fee-a']}));"
    )

    assert result["recovered"] is True
    assert result["loading"] is False
    assert result["renders"] == 1
    assert result["requestId"] == 1
    assert result["feeRequestId"] == 1
    assert result["expectedModified"] == "m3"
    assert result["materials"] == {"items": [{"name": "FULL"}]}
    assert result["draft"]["amount"] == "2400"
    assert result["draft"]["error"] == "并发冲突"


def test_fee_workspace_successful_save_invalidates_write_before_full_reload() -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'token-1',expectedModified:'m1',header:{name:'B-1',modified:'m1'}};"
        "const oldFee={logical_fee_key:'fee-a',expense_category:'A 费用',amount_status:'ACTUAL',amount:'100',currency:'RMB',allocation_basis:'volume',scope_type:'ALL_ITEMS',scope_value_json:'[]'};"
        "const newFee={...oldFee,amount:'150',currency:'USD'};"
        "workspace.materialFeeState={batchName:'B-1',page:1,pageLength:200,loading:false,requestId:0,feeRequestId:0,"
        "feeDrafts:{'fee-b':{amount:'88',currency:'EUR',error:'',touched:true}},materials:{items:[]},fees:{fees:[oldFee]},preview:{summary:{}}};"
        "workspace.getDetailBatch=()=>({name:'B-1',current_version:'V-1',modified:workspace.detailState.expectedModified});"
        "workspace.ensureEditSession=async()=>true;workspace.renderDetailTabLoading=()=>{};let renders=0;workspace.renderMaterialFeeWorkspace=()=>{renders+=1};"
        "let releaseOld;const oldGate=new Promise((resolve)=>{releaseOld=resolve});const counts={snapshot:0,ai:0};let writes=0;"
        "workspace.call=async(endpoint)=>{if(endpoint.endsWith('save_fee')){writes+=1;return {ok:true,batch_modified:'m2',message:'saved'}}"
        "if(endpoint.endsWith('get_source_ai_review_status')){counts.ai+=1;return {ok:true,status:'NONE'}}"
        "if(endpoint.endsWith('get_snapshot')){counts.snapshot+=1;await oldGate;return {ok:true,batch_name:'B-1',version_name:'V-1',cache:{status:'ready'},data:{"
        "detail:{ok:true,batch_name:'B-1',version_name:'V-1',header:{name:'B-1',modified:'m1'}},materials:{items:[{name:'OLD'}]},"
        "fees:{fees:[oldFee],summary:{}},preview:{summary:{total_cost_rmb:'100.00'}},settlement:{viewed_version:'V-1'}}}}"
        "if(endpoint.endsWith('refresh_snapshot')){counts.snapshot+=1;return {ok:true,batch_name:'B-1',version_name:'V-1',cache:{status:'ready'},data:{"
        "detail:{ok:true,batch_name:'B-1',version_name:'V-1',header:{name:'B-1',modified:'m2'}},materials:{items:[{name:'NEW'}]},"
        "fees:{fees:[newFee],summary:{}},preview:{summary:{total_cost_rmb:'150.00'}},settlement:{viewed_version:'V-1'}}}}"
        "throw new Error(endpoint)};"
        "const oldFull=workspace.loadMaterialFeeWorkspace();await new Promise((resolve)=>setImmediate(resolve));"
        "const fixture=makeFeeInput({amount:'150',currency:'USD',originalAmount:'100',originalCurrency:'RMB',feeKey:'fee-a'});"
        "await workspace.saveMaterialFeeInlineAmount(fixture.amountInput);releaseOld();await oldFull;"
        "console.log(JSON.stringify({writes,counts,requestId:workspace.materialFeeState.requestId,loading:workspace.materialFeeState.loading,"
        "expectedModified:workspace.detailState.expectedModified,headerModified:workspace.detailState.header.modified,"
        "fee:workspace.materialFeeState.fees.fees[0],materials:workspace.materialFeeState.materials,draftA:workspace.materialFeeState.feeDrafts['fee-a']||null,"
        "draftB:workspace.materialFeeState.feeDrafts['fee-b'],renders,alerts:global.alerts}));"
    )

    assert result["writes"] == 1
    assert result["counts"] == {"snapshot": 2, "ai": 1}
    assert result["requestId"] == 2
    assert result["loading"] is False
    assert result["expectedModified"] == "m2"
    assert result["headerModified"] == "m2"
    assert result["fee"]["amount"] == "150"
    assert result["fee"]["currency"] == "USD"
    assert result["materials"] == {"items": [{"name": "NEW"}]}
    assert result["draftA"] is None
    assert result["draftB"]["amount"] == "88"
    assert result["renders"] == 1
    assert result["alerts"][-1] == {"message": "saved", "indicator": "green"}


def test_fee_workspace_duplicate_blur_is_ignored_while_inline_save_is_pending() -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'token-1',expectedModified:'m1'};"
        "workspace.materialFeeState={batchName:'B-1',fees:{fees:[{logical_fee_key:'international_sea_freight',"
        "expense_category:'\u56fd\u9645\u6d77\u8fd0\u8d39',amount_status:'ACTUAL',amount:'2000',currency:'RMB',"
        "allocation_basis:'volume',scope_type:'ALL_ITEMS',scope_value_json:'[]'}]}};"
        "let resolveSave;let saveCalls=0;workspace.ensureEditSession=async()=>true;"
        "workspace.normalizeErrorMessage=(error)=>error.message;workspace.loadMaterialFeeWorkspace=async()=>true;"
        "workspace.call=async()=>{saveCalls+=1;return await new Promise((resolve)=>{resolveSave=resolve})};"
        "const fixture=makeFeeInput({amount:'2400',currency:'RMB',originalAmount:'2000',originalCurrency:'RMB'});"
        "const first=workspace.saveMaterialFeeInlineAmount(fixture.amountInput);"
        "const second=workspace.saveMaterialFeeInlineAmount(fixture.amountInput);await Promise.resolve();await Promise.resolve();"
        "resolveSave({ok:true,batch_modified:'m2'});await Promise.all([first,second]);"
        "console.log(JSON.stringify({saveCalls}));"
    )

    assert result == {"saveCalls": 1}


def test_fee_workspace_rapid_cross_row_saves_are_serialized_and_share_one_refresh() -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'token-1',expectedModified:'m1',header:{modified:'m1'}};"
        "const feeA={logical_fee_key:'fee-a',expense_category:'A 费用',amount_status:'ACTUAL',amount:'100',currency:'RMB',allocation_basis:'volume',scope_type:'ALL_ITEMS',scope_value_json:'[]'};"
        "const feeB={...feeA,logical_fee_key:'fee-b',expense_category:'B 费用',amount:'200'};"
        "workspace.materialFeeState={batchName:'B-1',fees:{fees:[feeA,feeB]},requestId:0,feeDrafts:{},pendingWrites:new Set()};"
        "workspace.ensureEditSession=async()=>true;workspace.normalizeErrorMessage=(error)=>error.message;"
        "let releaseFirst;const firstGate=new Promise((resolve)=>{releaseFirst=resolve});let saveCalls=0,active=0,maxActive=0,refreshCalls=0;const expected=[];"
        "workspace.call=async(endpoint,args)=>{if(!endpoint.endsWith('save_fee'))throw new Error(endpoint);"
        "saveCalls+=1;const callNo=saveCalls;expected.push(args.expected_modified);active+=1;maxActive=Math.max(maxActive,active);"
        "if(callNo===1)await firstGate;active-=1;return {ok:true,batch_modified:callNo===1?'m2':'m3',message:'saved'}};"
        "workspace.loadMaterialFeeWorkspace=async()=>{refreshCalls+=1;workspace.materialFeeState.cacheDirty=false;return true};"
        "const fixtureA=makeFeeInput({amount:'150',currency:'RMB',originalAmount:'100',originalCurrency:'RMB',feeKey:'fee-a'});"
        "const fixtureB=makeFeeInput({amount:'250',currency:'RMB',originalAmount:'200',originalCurrency:'RMB',feeKey:'fee-b'});"
        "const first=workspace.saveMaterialFeeInlineAmount(fixtureA.amountInput);"
        "const second=workspace.saveMaterialFeeInlineAmount(fixtureB.amountInput);await new Promise((resolve)=>setImmediate(resolve));"
        "const beforeRelease={saveCalls,maxActive};releaseFirst();await Promise.all([first,second]);"
        "console.log(JSON.stringify({beforeRelease,saveCalls,maxActive,refreshCalls,expected,modified:workspace.detailState.expectedModified,pending:workspace.materialFeeState.pendingWrites.size}));"
    )

    assert result == {
        "beforeRelease": {"saveCalls": 1, "maxActive": 1},
        "saveCalls": 2,
        "maxActive": 1,
        "refreshCalls": 1,
        "expected": ["m1", "m2"],
        "modified": "m3",
        "pending": 0,
    }


def test_fee_workspace_drops_queued_write_after_batch_switch() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents'};"
        "const oldState={batchName:'B-1',pendingWrites:new Set(),feeDrafts:{}};workspace.materialFeeState=oldState;"
        "let releaseFirst;const firstGate=new Promise((resolve)=>{releaseFirst=resolve});let firstCalls=0,secondCalls=0;"
        "const first=workspace.trackMaterialFeeWrite(async()=>{firstCalls+=1;await firstGate;return true});"
        "const second=workspace.trackMaterialFeeWrite(async()=>{secondCalls+=1;return true});"
        "await new Promise((resolve)=>setImmediate(resolve));workspace.detailState={batchName:'B-2',versionName:'V-2',tab:'documents'};"
        "workspace.materialFeeState={batchName:'B-2',pendingWrites:new Set(),feeDrafts:{}};releaseFirst();"
        "const values=await Promise.all([first,second]);"
        "console.log(JSON.stringify({firstCalls,secondCalls,values,oldPending:oldState.pendingWrites.size}));"
    )

    assert result == {"firstCalls": 1, "secondCalls": 0, "values": [True, False], "oldPending": 0}


def test_fee_workspace_warns_when_shared_refresh_fails_after_save() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents'};"
        "workspace.materialFeeState={batchName:'B-1',pendingWrites:new Set(),feeDrafts:{},cacheDirty:true};"
        "let refreshCalls=0;workspace.loadMaterialFeeWorkspace=async()=>{refreshCalls+=1;return false};"
        "const saved=await workspace.trackMaterialFeeWrite(async()=>true);"
        "console.log(JSON.stringify({saved,refreshCalls,alerts:global.alerts,pending:workspace.materialFeeState.pendingWrites.size}));"
    )

    assert result == {
        "saved": True,
        "refreshCalls": 1,
        "alerts": [{"message": "数据已保存，但最新状态读取失败，请点击重试。", "indicator": "orange"}],
        "pending": 0,
    }


def test_fee_workspace_stale_shared_refresh_does_not_warn_newer_request() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents'};"
        "workspace.materialFeeState={batchName:'B-1',requestId:1,pendingWrites:new Set(),feeDrafts:{},cacheDirty:true};"
        "let releaseRefresh;workspace.loadMaterialFeeWorkspace=async()=>{workspace.materialFeeState.requestId+=1;"
        "await new Promise((resolve)=>{releaseRefresh=resolve});return false};"
        "const saving=workspace.trackMaterialFeeWrite(async()=>true);await new Promise((resolve)=>setImmediate(resolve));"
        "workspace.materialFeeState.requestId+=1;releaseRefresh();const saved=await saving;"
        "console.log(JSON.stringify({saved,alerts:global.alerts,requestId:workspace.materialFeeState.requestId,pending:workspace.materialFeeState.pendingWrites.size}));"
    )

    assert result == {"saved": True, "alerts": [], "requestId": 3, "pending": 0}


def test_fee_dialog_save_joins_inline_write_queue_and_shared_refresh() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'token-1',expectedModified:'m1',header:{modified:'m1'}};"
        "workspace.materialFeeState={batchName:'B-1',pendingWrites:new Set(),feeDrafts:{}};workspace.ensureEditSession=async()=>true;"
        "let releaseFirst;const firstGate=new Promise((resolve)=>{releaseFirst=resolve});let saveCalls=0,refreshCalls=0,hidden=0;const expected=[];"
        "workspace.call=async(endpoint,args)=>{if(!endpoint.endsWith('save_fee'))throw new Error(endpoint);saveCalls+=1;expected.push(args.expected_modified);return {ok:true,batch_modified:'m3'}};"
        "workspace.loadMaterialFeeWorkspace=async()=>{refreshCalls+=1;workspace.materialFeeState.cacheDirty=false;return true};"
        "const first=workspace.trackMaterialFeeWrite(async()=>{await firstGate;workspace.updateMaterialFeeExpectedModified({batch_modified:'m2'});return true});"
        "const dialog={get_values:()=>({amount_status:'ACTUAL',amount:'25',currency:'RMB',scope_type:'ALL_ITEMS',included_in_fee_key:'',remark:''}),"
        "$wrapper:{find:()=>({toArray:()=>[]})},hide:()=>{hidden+=1}};"
        "const fee={logical_fee_key:'fee-a',expense_category:'A 费用',amount_status:'ACTUAL',amount:'20',currency:'RMB',allocation_basis:'goods_value',scope_type:'ALL_ITEMS',scope_value_json:'[]'};"
        "const dialogSave=workspace.saveMaterialFeeDialog(dialog,fee);await new Promise((resolve)=>setImmediate(resolve));const beforeRelease={saveCalls,refreshCalls};"
        "releaseFirst();await Promise.all([first,dialogSave]);"
        "console.log(JSON.stringify({beforeRelease,saveCalls,refreshCalls,expected,hidden,modified:workspace.detailState.expectedModified,pending:workspace.materialFeeState.pendingWrites.size}));"
    )

    assert result == {
        "beforeRelease": {"saveCalls": 0, "refreshCalls": 0},
        "saveCalls": 1,
        "refreshCalls": 1,
        "expected": ["m2"],
        "hidden": 1,
        "modified": "m3",
        "pending": 0,
    }


def test_fee_status_save_joins_inline_write_queue() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'token-1',expectedModified:'m1',header:{modified:'m1'}};"
        "const fee={logical_fee_key:'fee-a',expense_category:'A 费用',amount_status:'MISSING',amount:'25',currency:'RMB',allocation_basis:'goods_value',scope_type:'ALL_ITEMS',scope_value_json:'[]'};"
        "workspace.materialFeeState={batchName:'B-1',fees:{fees:[fee]},pendingWrites:new Set(),feeDrafts:{}};workspace.ensureEditSession=async()=>true;"
        "let releaseFirst;const firstGate=new Promise((resolve)=>{releaseFirst=resolve});let saveCalls=0,refreshCalls=0;const expected=[];"
        "workspace.call=async(endpoint,args)=>{if(!endpoint.endsWith('save_fee'))throw new Error(endpoint);saveCalls+=1;expected.push(args.expected_modified);return {ok:true,batch_modified:'m3'}};"
        "workspace.loadMaterialFeeWorkspace=async()=>{refreshCalls+=1;workspace.materialFeeState.cacheDirty=false;return true};"
        "const first=workspace.trackMaterialFeeWrite(async()=>{await firstGate;workspace.updateMaterialFeeExpectedModified({batch_modified:'m2'});return true});"
        "const attrs={'data-fee-key':'fee-a','data-original-value':'MISSING'};let value='ESTIMATED',disabled=false;const select={"
        "attr:(name)=>attrs[name],val(next){if(arguments.length){value=next;return this}return value},prop(name,next){if(name==='disabled')disabled=next;return this}};"
        "const statusSave=workspace.changeMaterialFeeStatus(select);await new Promise((resolve)=>setImmediate(resolve));const beforeRelease={saveCalls,refreshCalls};"
        "releaseFirst();await Promise.all([first,statusSave]);"
        "console.log(JSON.stringify({beforeRelease,saveCalls,refreshCalls,expected,modified:workspace.detailState.expectedModified,disabled,pending:workspace.materialFeeState.pendingWrites.size}));"
    )

    assert result == {
        "beforeRelease": {"saveCalls": 0, "refreshCalls": 0},
        "saveCalls": 1,
        "refreshCalls": 1,
        "expected": ["m2"],
        "modified": "m3",
        "disabled": True,
        "pending": 0,
    }


def test_fee_workspace_edit_session_acquire_is_single_flight() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);workspace.detailState={batchName:'B-1',editToken:'',editExpiresAt:''};"
        "workspace.materialFeeState={batchName:'B-1',feeDrafts:{},requestId:0,feeRequestId:0};let acquireCalls=0,release;"
        "workspace.ensureEditSession=async()=>{acquireCalls+=1;await new Promise((resolve)=>{release=resolve});workspace.detailState.editToken='token-1';return true};"
        "const first=workspace.ensureMaterialFeeEditSession();const second=workspace.ensureMaterialFeeEditSession();"
        "await new Promise((resolve)=>setImmediate(resolve));release();const results=await Promise.all([first,second]);"
        "console.log(JSON.stringify({acquireCalls,results,token:workspace.detailState.editToken}));"
    )

    assert result == {"acquireCalls": 1, "results": [True, True], "token": "token-1"}


def test_edit_session_late_old_batch_acquire_releases_without_overwriting_current_token() -> None:
    result = _detail_workspace_result(
        "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',editToken:'',editExpiresAt:'',expectedModified:'m1',renewTimer:null};"
        "workspace.batches={'B-1':{name:'B-1',modified:'m1'},'B-2':{name:'B-2',modified:'n1'}};"
        "workspace.getDetailBatch=()=>workspace.batches[workspace.detailState.batchName];workspace.updateEditLeaseStatus=()=>{};"
        "workspace.formatDateTimeMinute=(value)=>String(value||'');workspace.$root={find:()=>({addClass(){return this},text(){return this},attr(){return this}})};"
        "const pending={};const releases=[];workspace.call=async(endpoint,args)=>{"
        "if(endpoint.endsWith('acquire'))return await new Promise((resolve)=>{pending[args.batch_name]=resolve});"
        "if(endpoint.endsWith('release')){releases.push(args);return {ok:true}}throw new Error(endpoint)};"
        "const first=workspace.ensureEditSession();await new Promise((resolve)=>setImmediate(resolve));"
        "workspace.detailState.batchName='B-2';workspace.detailState.expectedModified='n1';"
        "const second=workspace.ensureEditSession();await new Promise((resolve)=>setImmediate(resolve));"
        "pending['B-2']({ok:true,edit_token:'token-b2',expires_at:'2099-01-01',modified:'n1'});const secondReady=await second;"
        "pending['B-1']({ok:true,edit_token:'token-b1',expires_at:'2099-01-01',modified:'m1'});const firstReady=await first;"
        "console.log(JSON.stringify({firstReady,secondReady,batchName:workspace.detailState.batchName,token:workspace.detailState.editToken,"
        "expectedModified:workspace.detailState.expectedModified,releases,alerts:global.alerts}));"
    )

    assert result == {
        "firstReady": False,
        "secondReady": True,
        "batchName": "B-2",
        "token": "token-b2",
        "expectedModified": "n1",
        "releases": [{"batch_name": "B-1", "edit_token": "token-b1"}],
        "alerts": [],
    }


def test_fee_workspace_builds_save_payload_from_latest_fee_after_lease() -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'',editExpiresAt:'',expectedModified:'m1',header:{modified:'m1'}};"
        "const oldFee={logical_fee_key:'fee-a',rule_code:'OLD',expense_category:'A 费用',amount_status:'ACTUAL',amount:'100',currency:'RMB',allocation_basis:'volume',scope_type:'ALL_ITEMS',scope_value_json:'[]',remark:'old'};"
        "const newFee={...oldFee,rule_code:'NEW',allocation_basis:'gross_weight',scope_type:'ITEMS',scope_value_json:'[\\\"LINE-2\\\"]',remark:'new'};"
        "workspace.materialFeeState={batchName:'B-1',fees:{fees:[oldFee]},feeDrafts:{},requestId:0,feeRequestId:0};"
        "let releaseLease;workspace.ensureEditSession=async()=>{await new Promise((resolve)=>{releaseLease=resolve});workspace.detailState.editToken='token-2';return true};"
        "workspace.loadMaterialFeeWorkspace=async()=>true;let saveArgs=null;workspace.call=async(endpoint,args)=>{if(endpoint.endsWith('save_fee')){saveArgs=args;return {ok:true,batch_modified:'m3'}}};"
        "const fixture=makeFeeInput({amount:'150',currency:'USD',originalAmount:'100',originalCurrency:'RMB',feeKey:'fee-a'});"
        "const saving=workspace.saveMaterialFeeInlineAmount(fixture.amountInput);await new Promise((resolve)=>setImmediate(resolve));"
        "workspace.materialFeeState.fees={fees:[newFee]};workspace.detailState.expectedModified='m2';workspace.detailState.header.modified='m2';releaseLease();await saving;"
        "console.log(JSON.stringify({expectedModified:saveArgs.expected_modified,token:saveArgs.edit_token,payload:JSON.parse(saveArgs.fee_payload)}));"
    )

    assert result["expectedModified"] == "m2"
    assert result["token"] == "token-2"
    assert result["payload"]["rule_code"] == "NEW"
    assert result["payload"]["allocation_basis"] == "gross_weight"
    assert result["payload"]["scope_type"] == "ITEMS"
    assert result["payload"]["scope_value_json"] == '["LINE-2"]'
    assert result["payload"]["remark"] == "new"


@pytest.mark.parametrize(
    "lease_error",
    ["编辑会话已过期，请重新进入编辑。", "当前批次正在被其他用户编辑。"],
)
def test_fee_workspace_expired_or_invalid_lease_reacquires_on_next_explicit_save(lease_error: str) -> None:
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + "const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:'expired-token',editExpiresAt:'2000-01-01T00:00:00Z',expectedModified:'m1',header:{modified:'m1'}};"
        "const fee={logical_fee_key:'fee-a',expense_category:'A 费用',amount_status:'ACTUAL',amount:'100',currency:'RMB',allocation_basis:'volume',scope_type:'ALL_ITEMS',scope_value_json:'[]'};"
        "workspace.materialFeeState={batchName:'B-1',fees:{fees:[fee]},feeDrafts:{},requestId:0,feeRequestId:0};"
        "let acquireCalls=0;const tokensAtAcquire=[];workspace.ensureEditSession=async()=>{acquireCalls+=1;tokensAtAcquire.push(workspace.detailState.editToken);workspace.detailState.editToken=`fresh-${acquireCalls}`;workspace.detailState.editExpiresAt='2099-01-01T00:00:00Z';return true};"
        "workspace.normalizeErrorMessage=(error)=>error.message;workspace.recoverMaterialFeeReadonlyState=async()=>true;workspace.loadMaterialFeeWorkspace=async()=>true;"
        f"let saveCalls=0;workspace.call=async()=>{{saveCalls+=1;if(saveCalls===1)throw new Error({json.dumps(lease_error)});return {{ok:true,batch_modified:'m2'}}}};"
        "const fixture=makeFeeInput({amount:'150',currency:'RMB',originalAmount:'100',originalCurrency:'RMB',feeKey:'fee-a'});"
        "await workspace.saveMaterialFeeInlineAmount(fixture.amountInput);const afterFirst={saveCalls,acquireCalls,token:workspace.detailState.editToken};"
        "await workspace.saveMaterialFeeInlineAmount(fixture.amountInput);"
        "console.log(JSON.stringify({tokensAtAcquire,afterFirst,saveCalls,acquireCalls,finalToken:workspace.detailState.editToken}));"
    )

    assert result["tokensAtAcquire"] == ["", ""]
    assert result["afterFirst"] == {"saveCalls": 1, "acquireCalls": 1, "token": ""}
    assert result["saveCalls"] == 2
    assert result["acquireCalls"] == 2
    assert result["finalToken"] == "fresh-2"


@pytest.mark.parametrize("stale_phase", ["lease", "success", "save", "recovery"])
@pytest.mark.parametrize("stale_reason", ["tab", "full_reload"])
def test_fee_workspace_stale_failure_does_not_touch_detached_input_or_toast(
    stale_phase: str, stale_reason: str
) -> None:
    switch_statement = (
        "workspace.detailState.tab='vouchers';"
        if stale_reason == "tab"
        else "workspace.materialFeeState.requestId+=1;"
    )
    result = _fee_workspace_result(
        FEE_INPUT_FIXTURE
        + f"const stalePhase={json.dumps(stale_phase)};const workspace=Object.create(Harness.prototype);"
        "workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',editToken:stalePhase==='lease'?'':'token-1',expectedModified:'m1',header:{modified:'m1'}};"
        "const fee={logical_fee_key:'fee-a',expense_category:'A 费用',amount_status:'ACTUAL',amount:'100',currency:'RMB',allocation_basis:'volume',scope_type:'ALL_ITEMS',scope_value_json:'[]'};"
        "workspace.materialFeeState={batchName:'B-1',fees:{fees:[fee]},feeDrafts:{},requestId:0,feeRequestId:0};"
        "workspace.normalizeErrorMessage=(error)=>error.message;let resolveSave,rejectSave,releaseRecovery,releaseLease;"
        "workspace.ensureEditSession=async()=>{if(stalePhase!=='lease')return true;await new Promise((resolve)=>{releaseLease=resolve});workspace.detailState.editToken='token-1';return true};"
        "workspace.call=async()=>{if(stalePhase==='recovery')throw new Error('并发冲突');"
        "if(stalePhase==='save'||stalePhase==='success')return await new Promise((resolve,reject)=>{resolveSave=resolve;rejectSave=reject});return {ok:true,batch_modified:'m2'}};"
        "workspace.loadMaterialFeeWorkspace=async()=>{workspace.materialFeeState.requestId+=1;return false};"
        "let recoverCalls=0;workspace.recoverMaterialFeeReadonlyState=async()=>{recoverCalls+=1;if(stalePhase==='save')return false;"
        "return await new Promise((resolve)=>{releaseRecovery=resolve})};let renders=0;workspace.renderMaterialFeeWorkspace=()=>{renders+=1};"
        "const fixture=makeFeeInput({amount:'150',currency:'USD',originalAmount:'100',originalCurrency:'RMB',feeKey:'fee-a'});"
        "let stale=false,postStaleMutations=0;for(const [target,names] of [[fixture.cell,['data','addClass','removeClass','attr']],[fixture.amountInput,['prop','attr']],[fixture.currencyInput,['prop','attr']]]){"
        "for(const name of names){const original=target[name].bind(target);target[name]=(...args)=>{if(stale)postStaleMutations+=1;return original(...args)}}}"
        "const saving=workspace.saveMaterialFeeInlineAmount(fixture.amountInput);await new Promise((resolve)=>setImmediate(resolve));"
        + switch_statement
        + "stale=true;if(stalePhase==='lease')releaseLease();else if(stalePhase==='success')resolveSave({ok:true,batch_modified:'m2'});"
        "else if(stalePhase==='save')rejectSave(new Error('并发冲突'));else releaseRecovery(false);"
        "await saving;const draft=workspace.materialFeeState.feeDrafts['fee-a']||null;"
        "console.log(JSON.stringify({postStaleMutations,recoverCalls,renders,alerts:global.alerts,draft,tab:workspace.detailState.tab,requestId:workspace.materialFeeState.requestId}));"
    )

    assert result["postStaleMutations"] == 0
    assert result["recoverCalls"] == (1 if stale_phase == "recovery" else 0)
    assert result["alerts"] == []
    if stale_phase == "success":
        assert result["draft"] is None
    else:
        assert result["draft"]["amount"] == "150"
        assert result["draft"]["currency"] == "USD"
    if stale_phase == "lease":
        assert not result["draft"].get("error")
    elif stale_phase != "success":
        assert "并发冲突" in result["draft"]["error"]
    assert result["renders"] == (1 if stale_phase == "save" and stale_reason == "full_reload" else 0)


def test_fee_workspace_exclusion_is_in_trial_result_without_an_allocation_column() -> None:
    result = _fee_workspace_result(
        "const workspace=Object.create(Harness.prototype);workspace.escape=(value)=>String(value ?? '');"
        "workspace.formatValue=(value)=>String(value);"
        "const row=workspace.renderMaterialFeeRow({logical_fee_key:'import_tax',expense_category:'\u8fdb\u53e3\u7a0e\u8d39',"
        "amount_status:'ESTIMATED',amount:'88',currency:'EUR',allocation_basis:'goods_value',"
        "scope_type:'ALL_ITEMS',allocation:{status:'MISSING_BASIS'},evidence:[]});"
        "workspace.detailState={batchName:'B-1',header:{summary_snapshot:{comprehensive_cost:{excluded_fees:[{expense_category:'进口税费',reason_code:'ALLOCATION_DENOMINATOR_ZERO'}]}}}};workspace.materialFeeState={batchName:'B-1'};"
        "const trial=workspace.renderMaterialFeeCostTable();"
        "console.log(JSON.stringify({basis:row.includes('采购货值'),rawCode:trial.includes('ALLOCATION_DENOMINATOR_ZERO'),excluded:trial.includes('未计入费用'),chinese:trial.includes('采购货值')}));"
    )

    assert result == {"basis": False, "rawCode": False, "excluded": True, "chinese": True}


def test_wiki_material_source_dialog_uses_local_catalog_and_refreshes_only_selected_sheet() -> None:
    workspace = (PARTS / "78-material-fee-workspace.js").read_text(encoding="utf-8")
    workspace += (PARTS / "79-material-import-grid.js").read_text(encoding="utf-8")

    assert "overseas_costing.api.packing_api.list_packing_sheet_catalog" in workspace
    assert "overseas_costing.api.packing_api.list_packing_attachment_sources" in workspace
    assert 'data-action="mf-wiki-refresh-selected"' in workspace
    assert 'data-action="mf-wiki-preview-card"' in workspace
    assert "loadMaterialAttachmentSources" in workspace
    assert "refreshSelectedWikiMaterialSource" in workspace
    assert 'data-action="mf-wiki-refresh-all"' not in workspace
    assert "request_packing_workbook_refresh" not in workspace
    assert "previewFreshWikiMaterialImport" not in workspace
    assert "wikiMaterialRefreshedSources" not in workspace
    assert '缓存已过期' in workspace
    assert '刷新所选 Sheet' in workspace
    assert "wikiMaterialClosed" in workspace
    assert "hide.bs.modal.ocwMfWiki" in workspace
    assert "preserveOnError" in workspace
    assert 'data-action="mf-wiki-refresh"' not in workspace
    assert "系统只读取服务器已授权的钉钉装箱计划表" not in workspace
    assert "预览所选 Sheet" not in workspace
    assert "确认写入物料表" in workspace


def test_packing_freight_flow_uses_local_catalog_and_selected_sheet_refresh() -> None:
    packing_flow = (PARTS / "86-packing-flow.js").read_text(encoding="utf-8")

    assert "overseas_costing.api.packing_api.list_packing_sheet_catalog" in packing_flow
    assert "overseas_costing.api.packing_api.list_packing_attachment_sources" in packing_flow
    assert "request_packing_workbook_refresh" not in packing_flow
    assert "refreshPackingWorkbook" not in packing_flow
    assert 'data-action="packing-refresh-list"' not in packing_flow
    assert 'data-action="packing-refresh-selected"' in packing_flow
    assert "刷新所选 Sheet" in packing_flow
    assert "wikiPreviewReady" in packing_flow
    assert '["ready", "stale"].includes' in packing_flow
    assert "selectedSource?.content_hash" in packing_flow


def test_recalculate_ui_blocks_invalid_approval_batches() -> None:
    calculation = (PARTS / "30-calculation-erp.js").read_text(encoding="utf-8")
    table = (PARTS / "75-table-and-list.js").read_text(encoding="utf-8")
    audit = (PARTS / "90-audit-logs.js").read_text(encoding="utf-8")

    assert "batch.source_status || {}" in calculation
    assert "invalid_business" in calculation
    assert "已拒绝、撤销或终止" in calculation
    assert "if (!result?.ok)" in calculation
    assert "recalculateDisabled" in table
    assert "sourceStatus.invalid_business" in audit


def test_manual_oa_pull_does_not_report_failed_save_as_completed() -> None:
    import_ui = (PARTS / "50-import-category.js").read_text(encoding="utf-8")
    pull_block = import_ui.split("async pullOaLogisticsApprovals(dialog, values)", 1)[1].split(
        "async repullGapDingtalk", 1
    )[0]

    assert "if (!result?.ok)" in pull_block
    assert "failed_count" in pull_block
    assert "throw new Error" in pull_block


def test_batch_source_provenance_fields_are_not_editable_by_cost_users() -> None:
    path = ROOT / "overseas_costing" / "doctype" / "overseas_cost_batch" / "overseas_cost_batch.json"
    definition = json.loads(path.read_text(encoding="utf-8"))
    fields = {row["fieldname"]: row for row in definition["fields"]}

    for fieldname in ("source_type", "source_data_id", "source_approval_no", "source_instance_id", "extra_json"):
        assert fields[fieldname]["permlevel"] == 1


def test_fee_workspace_currency_is_a_three_option_select_and_dialog_has_no_basis():
    result = _fee_workspace_result(r"""
const workspace=Object.create(Harness.prototype);
workspace.detailState={batchName:'B-1'};
const fee={logical_fee_key:'freight',amount_status:'ACTUAL',amount:0,currency:'CNY'};
workspace.materialFeeState={batchName:'B-1',fees:{fees:[fee]},materials:{items:[]}};
workspace.escape=(value)=>String(value??'');
const row=workspace.renderMaterialFeeRow(fee);
let fields;global.frappe.ui={Dialog:class {constructor(options){fields=options.fields;this.$wrapper={addClass(){}}}show(){}}};
workspace.openMaterialFeeDialog('freight');
console.log(JSON.stringify({row,fields}));
""")
    assert '<select data-mf-fee-input="currency"' in result["row"]
    amount_cell = result["row"].split('ocw-mf-fee-inline-fields', 1)[1].split('</div>', 1)[0]
    assert amount_cell.count("<option ") == 3
    assert 'value="RMB" selected' in result["row"]
    assert "人民币" in result["row"] and "比索" in result["row"] and "美金" in result["row"]
    fields = {field["fieldname"]: field for field in result["fields"]}
    assert "allocation_basis" not in fields
    assert fields["currency"]["fieldtype"] == "Select"
    assert [option["value"] for option in fields["currency"]["options"]] == ["RMB", "MXN", "USD"]
    assert fields["currency"]["default"] == "RMB"
    assert fields["amount"]["default"] == 0


PREVIEW_WORKSPACE_FIXTURE = r"""
const workspace=Object.create(Harness.prototype);
workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents'};
const state=workspace.ensureMaterialFeeState();state.preview={summary:{total_cost_rmb:'100.00'}};
const button={disabled:false,label:'',prop(name,value){this[name]=value;return this},text(value){this.label=value;return this},attr(){return this}};
workspace.$root={find(selector){if(selector.includes('mf-preview-cost'))return button;return {length:0,toArray:()=>[],each(){},get(){return null},filter(){return this},first(){return this}}}};
workspace.escape=(value)=>String(value??'');workspace.renderMaterialFeeWorkspace=()=>{workspace.renders=(workspace.renders||0)+1};
workspace.renders=0;workspace.calls=0;
workspace.call=async()=>{workspace.calls++;return {ok:true,summary:{total_cost_rmb:'200.00'}}};
"""


def test_trial_saves_current_version_and_updates_other_tabs():
    result = _fee_workspace_result(PREVIEW_WORKSPACE_FIXTURE + r"""
workspace.detailState.editToken='TOKEN';workspace.detailState.expectedModified='M1';
workspace.ensureEditSession=async()=>true;
workspace.batches=[{name:'B-1',estimated_total_cost_rmb:100}];
state.costTrialAI={runId:'RUN',status:'READY',draft:{fee_suggestions:[]},selections:[],preview:{preview_token:'P'}};
state.costTrialDialog={hide(){}};workspace.renderCostTrialAIReviewDialog=()=>{};
let endpoint,args;workspace.call=async(e,a)=>{endpoint=e;args=a;return {ok:true,saved:true,batch_modified:'M2',summary:{total_cost_rmb:'64800.00'},summary_snapshot:{total_cost_rmb:'64800.00',calculation_schema:2}}};
await workspace.confirmCostTrialAI();
console.log(JSON.stringify({endpoint,args,modified:workspace.detailState.expectedModified,batch:workspace.batches[0]}));
""")
    assert result["endpoint"].endswith(".confirm_cost_trial")
    assert result["args"]["edit_token"] == "TOKEN" and result["args"]["expected_modified"] == "M1"
    assert result["args"]["preview_token"] == "P"
    assert result["modified"] == "M2"
    assert float(result["batch"]["estimated_total_cost_rmb"]) == 64800
    assert result["batch"]["status"] == "Calculated"


def test_cost_trial_requests_use_unified_transport_without_legacy_inline_option():
    source = (PARTS / "78-material-fee-workspace.js").read_text(encoding="utf-8")
    endpoints = [
        "start_cost_trial_ai_review",
        "get_cost_trial_ai_review_status",
        "preview_cost_trial",
        "confirm_cost_trial",
        "discard_cost_trial_ai_review",
    ]

    for endpoint in endpoints:
        matches = list(re.finditer(rf'this\.call\("overseas_costing\.api\.calculate\.{endpoint}"', source))
        assert matches, endpoint
    assert "inlineErrors" not in source


def test_local_packing_attachment_previews_exact_sheet_without_dingtalk_download():
    result = _fee_workspace_result(r"""
const workspace=Object.create(Harness.prototype);workspace.detailState={batchName:'B',versionName:'V'};
workspace.ensureEditSession=async()=>true;workspace.renderWikiMaterialSources=()=>{};
const source={source_id:'ATT',attachment_name:'ATT',source_kind:'manual_attachment',available:true,sheets:['采购明细']};
const dialog={materialBatchName:'B',materialVersionName:'V',materialAttachmentSources:[source],hide(){this.hidden=true}};
const calls=[];workspace.call=async(endpoint,args)=>{calls.push([endpoint,args]);return {ok:true,rows:[]}};
workspace.openMaterialImportPreviewDialog=()=>{};
await workspace.previewMaterialAttachmentSource(dialog,'ATT');console.log(JSON.stringify(calls));
""")
    assert [call[0].split(".")[-1] for call in result] == ["preview_material_import"]
    assert result[0][1] == dict(batch_name="B", source_kind="manual_attachment", source_id="ATT", sheet_name="采购明细")


def test_current_expense_attachment_loads_uncached_sheet_names_before_preview():
    result = _fee_workspace_result(r"""
const workspace=Object.create(Harness.prototype);workspace.detailState={batchName:'B',versionName:'V'};
workspace.renderWikiMaterialSources=()=>{};workspace.openMaterialImportPreviewDialog=()=>{};
const source={source_id:'ATT',attachment_name:'ATT',source_kind:'approval_attachment',available:true,sheets:[]};
const dialog={materialBatchName:'B',materialVersionName:'V',sourceContext:{fingerprint:'ctx'},materialAttachmentSources:[source],hide(){this.hidden=true}};
const calls=[];workspace.call=async(endpoint,args)=>{calls.push([endpoint,args]);return endpoint.endsWith('list_packing_attachment_sheets')?{ok:true,sheets:['货物'],source_context:{fingerprint:'ctx'}}:{ok:true,rows:[]}};
await workspace.previewMaterialAttachmentSource(dialog,'ATT');console.log(JSON.stringify(calls));
""")
    assert [call[0].split('.')[-1] for call in result] == ['list_packing_attachment_sheets','preview_material_import']
    assert result[1][1]['sheet_name'] == '货物'


def test_direct_source_picker_keeps_authorized_excel_and_drops_non_table_sources():
    """本页签保留「已授权且可导入」的 Excel，排除审批正文/评论等非表格来源。

    历史：这里曾把 approval_attachment 一律排除，导致国际物流的装箱单 Excel
    在物料页「获取装箱资料」里看不到也选不了。但同一批来源在装箱流程弹窗
    （86-packing-flow 的「钉钉审批附件/评论」页签）一直可读可预览，
    服务端 list_packing_attachment_sources 也带 read 权限校验 ——
    因此排除并非安全边界，而是功能不一致。

    分层：过滤在加载器（loadMaterialAttachmentSources）完成，渲染层信任
    加载器已筛好的列表。这里两层分别验证。
    """
    source = (PARTS / "78-material-fee-workspace.js").read_text(encoding="utf-8")
    loader = source.split("async loadMaterialAttachmentSources", 1)[1].split(
        "\n  renderWikiMaterialSources", 1
    )[0]
    # 加载器：两类可导入来源都留，非表格来源（正文/评论/Sheet）剔除。
    assert "supported_for_material_import" in loader
    assert '"manual_attachment", "approval_attachment"' in loader
    assert "manual_attachments" in loader and "approval_sources" in loader

    # 渲染层：喂进去的两类来源都要出现，且能点「预览」。
    result = _fee_workspace_result(r"""
const workspace=Object.create(Harness.prototype);workspace.detailState={batchName:'B',versionName:'V'};
workspace.escape=(value)=>String(value??'');
const dialog={materialSourceTab:'local',wikiMaterialBusy:'',wikiMaterialOperationError:'',materialAttachmentSources:[
  {source_id:'OA',source_label:'国际物流装箱单.xlsx',source_kind:'approval_attachment',available:true,sheets:['Sheet1'],supported_for_material_import:true},
  {source_id:'LOCAL',source_label:'本地装箱单.xlsx',source_kind:'manual_attachment',available:true,sheets:['Sheet1'],supported_for_material_import:true}
]};
const html=workspace.renderMaterialAttachmentSources(dialog);
console.log(JSON.stringify({
  hasApprovalExcel:html.includes('国际物流装箱单.xlsx'),
  hasLocal:html.includes('本地装箱单.xlsx'),
  approvalPortable:html.includes('data-mf-attachment-source="OA"'),
  localPortable:html.includes('data-mf-attachment-source="LOCAL"')}));
""")
    assert result == dict(hasApprovalExcel=True, hasLocal=True,
                         approvalPortable=True, localPortable=True)


def test_trial_waits_for_pending_writes_and_ignores_duplicate_clicks():
    result = _fee_workspace_result(PREVIEW_WORKSPACE_FIXTURE + r"""
workspace.openCostTrialAIReviewDialog=()=>{workspace.opened=true};
workspace.call=async(endpoint)=>{workspace.calls++;
 if(endpoint.endsWith('start_cost_trial_ai_review'))return {ok:true,run_id:'RUN',status:'READY'};
 if(endpoint.endsWith('get_cost_trial_ai_review_status'))return {ok:true,run_id:'RUN',status:'READY',draft:{fee_suggestions:[],default_selections:[]}};
 if(endpoint.endsWith('preview_cost_trial'))return {ok:true,preview_token:'TOKEN'};
 return {ok:true,saved:true,batch_modified:'m2',summary:{total_cost_rmb:'100.00'},summary_snapshot:{}}};
let release;const writing=workspace.trackMaterialFeeWrite(()=>new Promise(resolve=>{release=resolve}));
const first=workspace.refreshMaterialFeeCostPreview();
await new Promise(resolve=>setImmediate(resolve));
const before={calls:workspace.calls,running:state.previewRunning,stage:state.costTrialAI.actionStage};
await workspace.refreshMaterialFeeCostPreview();release();await writing;await first;
console.log(JSON.stringify({before,calls:workspace.calls,opened:workspace.opened,running:state.previewRunning}));
""")
    assert result["before"] == {"calls": 0, "running": True, "stage": "reuse"}
    assert result["calls"] == 3
    assert result.get("opened") is True
    assert result["running"] is False


@pytest.mark.parametrize("change", ["batch", "version", "tab", "reload", "input"])
def test_trial_discards_response_if_context_or_inputs_changed(change):
    result = _fee_workspace_result(PREVIEW_WORKSPACE_FIXTURE + r"""
let release;workspace.call=()=>new Promise(resolve=>{release=resolve});
const running=workspace.refreshMaterialFeeCostPreview();await new Promise(resolve=>setImmediate(resolve));
""" + {
        "batch": "workspace.detailState.batchName='B-2';workspace.materialFeeState={batchName:'B-2',preview:{summary:{total_cost_rmb:'999.00'}}};",
        "version": "workspace.detailState.versionName='V-2';",
        "tab": "workspace.detailState.tab='vouchers';",
        "reload": "state.requestId++;",
        "input": "state.inputRevision++;",
    }[change] + r"""
    release({ok:true,run_id:'RUN',status:'QUEUED'});await running;
console.log(JSON.stringify({preview:workspace.materialFeeState.preview,renders:workspace.renders}));
""")
    assert result["preview"]["summary"]["total_cost_rmb"] == ("999.00" if change == "batch" else "100.00")
    assert result["renders"] == 0


@pytest.mark.parametrize("failure", ["draft", "material", "api"])
def test_trial_does_not_replace_result_after_invalid_input_or_failed_request(failure):
    setup = {
        "draft": "state.feeDrafts={freight:{amount:'',currency:'RMB',error:'费用金额不能为空。'}};",
        "material": "state.materialSaveErrors={'A:volume_m3':'保存失败'};",
        "api": "workspace.call=async()=>{workspace.calls++;return {ok:false,message:'试算服务失败'}};",
    }[failure]
    result = _fee_workspace_result(PREVIEW_WORKSPACE_FIXTURE + setup + r"""
let error='';try {await workspace.refreshMaterialFeeCostPreview()}catch(e){error=e.message}
console.log(JSON.stringify({error,calls:workspace.calls,preview:state.preview,running:state.previewRunning}));
""")
    assert result["error"]
    assert result["calls"] == (1 if failure == "api" else 0)
    assert result["preview"]["summary"]["total_cost_rmb"] == "100.00"
    assert result["running"] is False


def test_unsupported_historical_currency_requires_explicit_selection_in_dialog():
    result = _fee_workspace_result(r"""
const workspace=Object.create(Harness.prototype);workspace.detailState={batchName:'B-1'};workspace.escape=value=>String(value??'');
workspace.materialFeeState={batchName:'B-1',fees:{fees:[{logical_fee_key:'old',amount:100,currency:'EUR'}]},materials:{items:[]}};
let fields;global.frappe.ui={Dialog:class{constructor(options){fields=options.fields;this.$wrapper={addClass(){}}}show(){}}};
workspace.openMaterialFeeDialog('old');console.log(JSON.stringify(fields.find(field=>field.fieldname==='currency')));
""")
    assert result["default"] == ""
    assert [option["value"] for option in result["options"]] == ["", "RMB", "MXN", "USD"]
    assert result["reqd"] == 1


def test_trial_flushes_dirty_fee_before_reading_preview():
    result = _fee_workspace_result(PREVIEW_WORKSPACE_FIXTURE + r"""
state.fees={fees:[{logical_fee_key:'fee'}]};state.feeDrafts={fee:{amount:'20',currency:'RMB'}};
const input={attr(){return 'fee'}};global.$=value=>value;const originalFind=workspace.$root.find;
workspace.$root.find=selector=>selector==='[data-mf-fee-amount]'?{each(callback){callback(0,input)}}:originalFind(selector);
workspace.openCostTrialAIReviewDialog=()=>{};
const order=[];workspace.saveMaterialFeeInlineAmount=async()=>{order.push('save');delete state.feeDrafts.fee};workspace.call=async(endpoint)=>{
 const action=endpoint.split('.').pop();order.push(action);
 if(action==='start_cost_trial_ai_review')return {ok:true,run_id:'RUN',status:'READY'};
 if(action==='get_cost_trial_ai_review_status')return {ok:true,run_id:'RUN',status:'READY',draft:{fee_suggestions:[],default_selections:[]}};
 if(action==='preview_cost_trial')return {ok:true,preview_token:'TOKEN'};
 return {ok:true,saved:true,batch_modified:'m2',summary:{total_cost_rmb:'100.00'},summary_snapshot:{}}};
await workspace.refreshMaterialFeeCostPreview();console.log(JSON.stringify({order,status:state.costTrialAI.status}));
""")
    assert result == {
        "order": [
            "save",
            "start_cost_trial_ai_review",
            "get_cost_trial_ai_review_status",
            "preview_cost_trial",
        ],
        "status": "READY",
    }


MATERIAL_SAVE_FIXTURE = r"""
const workspace=Object.create(Harness.prototype);workspace.detailState={batchName:'B-1',versionName:'V-1',tab:'documents',header:{modified:'m1'}};
const state=workspace.ensureMaterialFeeState();state.materials={items:[{name:'A',volume_m3:'1'}]};workspace.normalizeErrorMessage=error=>error.message;
workspace.ensureEditSession=async()=>true;workspace.loadMaterialFeeWorkspace=async()=>true;
const cell={addClass(){return this},removeClass(){return this},attr(){return this}};
const data={},input={length:1,value:'2',val(){return this.value},attr(name){return {'data-original-value':'1','data-item-name':'A','data-fieldname':'volume_m3'}[name]},data(name,value){if(arguments.length>1){data[name]=value;return this}return data[name]},prop(){return this},closest(){return cell}};
workspace.call=async()=>({ok:true,batch_modified:'m2'});
"""


def test_material_save_denied_lease_is_not_safe_to_trial():
    result = _fee_workspace_result(MATERIAL_SAVE_FIXTURE + r"""
workspace.ensureEditSession=async()=>false;await workspace.saveMaterialFeeCell(input);
console.log(JSON.stringify({errors:state.materialSaveErrors,pending:state.pendingWrites.size}));
""")
    assert result["errors"].get("A:volume_m3")
    assert result["pending"] == 0


def test_reverting_failed_material_input_to_saved_value_clears_trial_blocker():
    result = _fee_workspace_result(MATERIAL_SAVE_FIXTURE + r"""
state.materialSaveErrors={'A:volume_m3':'failed'};input.value='1';await workspace.saveMaterialFeeCell(input);
console.log(JSON.stringify({errors:state.materialSaveErrors}));
""")
    assert result["errors"] == {}


def test_material_save_response_does_not_update_another_batch():
    result = _fee_workspace_result(MATERIAL_SAVE_FIXTURE + r"""
let release;workspace.call=()=>new Promise(resolve=>{release=resolve});let reloads=0;workspace.loadMaterialFeeWorkspace=async()=>{reloads++;return true};
const saving=workspace.saveMaterialFeeCell(input);await new Promise(resolve=>setImmediate(resolve));workspace.detailState={batchName:'B-2',tab:'documents',expectedModified:'other',header:{modified:'other'}};workspace.materialFeeState={batchName:'B-2'};
release({ok:true,batch_modified:'m2'});await saving;console.log(JSON.stringify({modified:workspace.detailState.expectedModified,reloads}));
""")
    assert result == {"modified": "other", "reloads": 0}


def test_material_grid_rerender_keeps_another_cells_unsaved_value():
    result = _fee_workspace_result(MATERIAL_SAVE_FIXTURE + r"""
workspace.escape=value=>String(value??'');workspace.formatValue=value=>String(value);workspace.materialFeeState.materialDrafts={};
workspace.updateMaterialDraftFromInput(input);
const html=workspace.renderMaterialFeeGridCell({name:'A',volume_m3:'1'}, {field:'volume_m3',label:'体积 m³',numeric:true},new Set(),1);
console.log(JSON.stringify({value:html.includes('value="2"'),original:html.includes('data-original-value="1"'),draft:state.materialDrafts['A:volume_m3']}));
""")
    assert result["value"] is True and result["original"] is True
    assert result["draft"]["value"] == "2"


def test_material_grid_exposes_top_bulk_actions_restore_and_failed_cell_retry():
    result = _fee_workspace_result(r"""
const workspace=new Harness();workspace.escape=value=>String(value??'');workspace.formatValue=value=>String(value??'');
workspace.detailState={batchName:'B',versionName:'V',tab:'documents'};const state=workspace.ensureMaterialFeeState();
state.materialDrafts={'I:gross_weight_kg':{itemName:'I',fieldname:'gross_weight_kg',value:'9.7',error:'并发冲突'}};
const columns=workspace.materialFeeGridColumns();
const cell=workspace.renderMaterialFeeGridCell({name:'I',gross_weight_kg:null,requirements:{missing_fields:['gross_weight_kg']}},columns.find(row=>row.field==='gross_weight_kg'),new Set(['gross_weight_kg']),1);
state.materials={packing_group_editable:true,items:[{name:'I',stable_line_key:'L1'}],packing_groups:[]};
const toolbar=workspace.renderMaterialSelectionToolbar();
console.log(JSON.stringify({fields:columns.map(row=>row.field),cell,toolbar}));
""")
    source = (PARTS / "78-material-fee-workspace.js").read_text(encoding="utf-8")
    assert result["fields"][:2] == ["__group_select", "row_no"]
    assert "__actions" not in result["fields"]
    assert 'value="9.7"' in result["cell"] and 'data-action="mf-retry-cell"' in result["cell"]
    assert 'data-action="mf-exclude-selected"' in result["toolbar"]
    assert 'data-action="mf-add-material"' in result["toolbar"]
    assert 'data-action="mf-excluded-materials"' in source


def test_trial_saves_material_draft_before_reading_cost():
    result = _fee_workspace_result(PREVIEW_WORKSPACE_FIXTURE + r"""
state.materialDrafts={'A:volume_m3':{itemName:'A',fieldname:'volume_m3',value:'2'}};
const input={attr(name){return {'data-item-name':'A','data-fieldname':'volume_m3'}[name]}};global.$=value=>value;const originalFind=workspace.$root.find;
workspace.$root.find=selector=>selector==='[data-mf-cell-input]'?{each(callback){callback(0,input)}}:originalFind(selector);
workspace.openCostTrialAIReviewDialog=()=>{};
const order=[];workspace.saveMaterialFeeCell=async()=>{order.push('save');delete state.materialDrafts['A:volume_m3']};workspace.call=async(endpoint)=>{
 const action=endpoint.split('.').pop();order.push(action);
 if(action==='start_cost_trial_ai_review')return {ok:true,run_id:'RUN',status:'READY'};
 if(action==='get_cost_trial_ai_review_status')return {ok:true,run_id:'RUN',status:'READY',draft:{fee_suggestions:[],default_selections:[]}};
 if(action==='preview_cost_trial')return {ok:true,preview_token:'TOKEN'};
 return {ok:true,saved:true,batch_modified:'m2',summary:{total_cost_rmb:'100.00'},summary_snapshot:{}}};
await workspace.refreshMaterialFeeCostPreview();console.log(JSON.stringify({order}));
""")
    assert result["order"] == [
        "save",
        "start_cost_trial_ai_review",
        "get_cost_trial_ai_review_status",
        "preview_cost_trial",
    ]


def test_trial_stops_if_another_material_is_edited_while_flushing():
    result = _fee_workspace_result(PREVIEW_WORKSPACE_FIXTURE + r"""
state.materialDrafts={'B:volume_m3':{itemName:'B',fieldname:'volume_m3',value:'2'}};
const input={attr(name){return {'data-item-name':'B','data-fieldname':'volume_m3'}[name]}};global.$=value=>value;const originalFind=workspace.$root.find;
workspace.$root.find=selector=>selector==='[data-mf-cell-input]'?{each(callback){callback(0,input)}}:originalFind(selector);
workspace.saveMaterialFeeCell=async()=>{delete state.materialDrafts['B:volume_m3'];state.materialDrafts['C:volume_m3']={itemName:'C',fieldname:'volume_m3',value:'9'}};
let error='';try{await workspace.refreshMaterialFeeCostPreview()}catch(e){error=e.message}
console.log(JSON.stringify({calls:workspace.calls,error,total:state.preview.summary.total_cost_rmb,draft:state.materialDrafts['C:volume_m3'].value}));
""")
    assert result["calls"] == 0
    assert result["error"]
    assert result["total"] == "100.00"
    assert result["draft"] == "9"


DEAD_WORKBENCH_ACTIONS = (
    "refresh-batch",
    "delete-batch",
    "writeback-to-erp",
    "preview-purchase",
    "oa-attachments",
    "open-dingtalk",
    "open-generic-link",
    "open-purchase-source",
    "gap-recalculate",
    "open-dingtalk-packing-picker",
)


def test_dead_action_bindings_stay_removed_from_the_workbench() -> None:
    """这些动作没有任何渲染来源，绑定与独占 handler 已在同一次变更里清掉。

    它们不是「待接线」的按钮：能渲染它们的入口本身已被改造掉——抽屉推送改走详情页、
    打开原单改走原生 <a href>、缺口试算统一走页头、附件改走钉钉审批页签。
    留下绑定只会让后来人误判这些入口还在业务手里，故补此守卫。
    """
    for path in sorted(PARTS.glob("*.js")):
        source = path.read_text(encoding="utf-8")
        for action in DEAD_WORKBENCH_ACTIONS:
            assert f"data-action='{action}'" not in source, f"{path.name} 重新绑定了死动作 {action}"
            assert f'data-action="{action}"' not in source, f"{path.name} 重新渲染了死动作 {action}"


def test_orphan_handlers_of_dead_actions_stay_removed() -> None:
    """入口丢失后整链不可达的 handler 不应再出现；被其它入口复用的方法必须留下。"""
    source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(PARTS.glob("*.js")))
    for name in (
        "confirmDeleteBatch",
        "deleteBatch",
        "openPurchasePreviewDialog",
        "previewPurchaseExpense",
        "renderPurchasePreview",
        "renderPurchaseSourceList",
        "renderPurchaseApplyAction",
        "applyPurchaseFillableFields",
        "openOaAttachmentDialog",
        "openDingtalkPackingSourcePicker",
        "isWordFileRef",
    ):
        assert f"{name}(" not in source, f"{name} 仍然存在（应为已删除的孤儿 handler）"
    for name in (
        "refreshBatch",
        "openDingtalkOrder",
        "openDingtalkLink",
        "writebackToErp",
        "recalculate",
        "loadOaFormAttachments",
        "renderOaAttachmentList",
        "openSourceCenterDialog",
        "isTextFileRef",
    ):
        assert f"{name}(" in source, f"{name} 仍被其它入口复用，不该被删"

