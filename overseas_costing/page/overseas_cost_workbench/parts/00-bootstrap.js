function ocTranslate(text, args = []) {
  if (typeof globalThis.__ === "function") return globalThis.__(text, args);
  return String(text).replace(/\{(\d+)\}/g, (_, index) => args[index] ?? "");
}

frappe.pages["overseas-cost-workbench"] = frappe.pages["overseas-cost-workbench"] || {};

function hideDeskChromeWhenReady(workbench) {
  workbench.hideDeskChrome();
  requestAnimationFrame(() => workbench.hideDeskChrome());
  window.setTimeout(() => {
    if (!$(workbench.wrapper).is(":visible")) return;
    workbench.hideDeskChrome();
    ensureDeskModuleSidebar(workbench);
    syncWorkspaceSidebarHeaderIcon(workbench);
  }, 300);
}

function ensureDeskModuleSidebar(workbench) {
  // DeeplinkERP 将授权后的模块图标放在这个独立容器。
  // .body-sidebar-container 是工作区内部侧栏，不是 ERP 模块栏。
  const $nativeSidebar = $(".custom-filters-right-sidebar-container").first();
  if ($nativeSidebar.length) {
    if (!workbench._erpModuleSidebarSnapshot) {
      workbench._erpModuleSidebarSnapshot = {
        element: $nativeSidebar.get(0),
        display: $nativeSidebar.get(0).style.display,
        visibility: $nativeSidebar.get(0).style.visibility,
        opacity: $nativeSidebar.get(0).style.opacity,
      };
    }
    $nativeSidebar.css({ display: "", visibility: "visible", opacity: "1" });
    $("#ocw-erp-module-sidebar-fallback").remove();
    $("body").removeClass("ocw-has-erp-module-sidebar-fallback");
    return;
  }
}

function syncWorkspaceSidebarHeaderIcon(workbench) {
  const desktopIcon = (frappe.boot.desktop_icons || []).find(
    (icon) => icon && icon.hidden != 1 && icon.label === "海外成本核算"
  );
  const iconSource = desktopIcon && (desktopIcon.logo_url || desktopIcon.icon_image);
  const $target = $(".body-sidebar-container .sidebar-header > .sidebar-item-icon").first();
  if (!iconSource || !$target.length) return;

  if (!workbench._workspaceSidebarIconSnapshot) {
    workbench._workspaceSidebarIconSnapshot = {
      element: $target.get(0),
      html: $target.html(),
    };
  }

  $target.empty().append(
    $("<img>", {
      class: "ocw-workspace-sidebar-icon",
      src: iconSource,
      alt: "",
    })
  );
}

frappe.pages["overseas-cost-workbench"].on_page_load = function (wrapper) {
  const workbench = new OverseasCostWorkbench(wrapper);
  frappe.pages["overseas-cost-workbench"].workbench = workbench;
  workbench.init();
  ensureDeskModuleSidebar(workbench);
  syncWorkspaceSidebarHeaderIcon(workbench);
  hideDeskChromeWhenReady(workbench);
  // 离开工作台时恢复桌面外壳（侧栏 / 顶部标签栏 / 右侧栏），避免影响其它页面。
  $(wrapper).on("hide", function () {
    workbench.restoreDeskChrome();
  });
};

frappe.pages["overseas-cost-workbench"].on_page_show = function () {
  const workbench = frappe.pages["overseas-cost-workbench"].workbench;
  if (!workbench) return;
  ensureDeskModuleSidebar(workbench);
  syncWorkspaceSidebarHeaderIcon(workbench);
  hideDeskChromeWhenReady(workbench);
  workbench.applyDeskLayout();
  workbench.applyModuleSidebarPreference();
  workbench.resumeWorkbenchReleaseMonitor();
  requestAnimationFrame(() => workbench.applyDeskLayout());
};
