// Partial-class harnesses need the same translator as the assembled page.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const bootstrap = fs.readFileSync(
  path.join(__dirname, "../page/overseas_cost_workbench/parts/00-bootstrap.js"),
  "utf8"
);
vm.runInThisContext(bootstrap.slice(0, bootstrap.indexOf('frappe.pages[')));
