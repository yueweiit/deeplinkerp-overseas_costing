# 其他费用可重复明细实施计划

## 生产发布补充（2026-10-23 用户已授权）

生产已迁移到独立仓库 `yueweiit/deeplinkerp-overseas_costing` 的 `main`，现场 `app-revisions.json` 与远端均为 `2a72899c698c5d84adf0caa25156cfb4ad05ee8b`。原仓库的 `overseas_costing` 分支和旧自动部署脚本不是当前发布入口，禁止触发其中的历史库存转移。只将原 `b800459..c66f7dd` 费用差异移植到独立副本 `/Users/smk/.codex/worktrees/other-fees-production-20261023`，保留当前多语言实现。

- [x] 核实现场镜像、13 个 App 的源码提交、维护状态、磁盘及发布互斥锁；其他 App 的 main 与现场提交一致。
- [x] 新生产基线全测：4736 passed、13 skipped。复用已有费用测试先复现缺失功能，再合入原实现；冲突仅在翻译包装处，保留翻译及原值标识。
- [x] 沿用原 en/es 字典与 `ocTranslate`，补新控件文案；新增一个跨语言契约、展开为两个语言用例，验证显示翻译不改变名称、标识、币种和金额。
- [ ] 新基线费用重点与完整测试、稳定生成物、语法与 diff 检查，独立移植复查后提交精确版本。
- [ ] 非强制推送独立仓库 main，等待现有 CI 测试；镜像通过现场 `upgrade_bench.sh build` 构建。构建期间服务保持运行；核对镜像源提交，其他 12 App 不变才允许切换。
- [ ] 复用现场 `upgrade_bench.sh deploy <唯一镜像>`：互斥锁、维护/队列排空、数据库及附件完整备份与校验、离线迁移、服务及资源检查、恢复原维护状态。本次关闭可选旧镜像清理，保留回滚镜像和所有备份；不执行旧库存迁移或生产费用写入。
- [ ] 生产独立回读资源哈希、版本、数据库及进程健康、登录 HTTP；登录真实 UI 只读检查批次费用入口、紧凑控件与合计，不通过新增/删除真实费用制造验收数据。
- [ ] 在现有完成报告补发布记录、备份与验收、同口径行数/测试数量及未验证边界；不把跳过的 PostgreSQL 集成测试或纯计算验收说成生产写入验收。

本节为 2026-10-23 用户确认后的当前计划，替代下方历史多选交互。基线 `60bebbc`，隔离副本 `/Users/smk/.codex/worktrees/other-fees-20261023`，工作区干净。当前会话按 writing-plans / executing-plans / TDD 执行，不推送、不部署。

## 当前任务

### 后续反馈：删除与紧凑排版（基线 edf423f）

沿用本地副本与已授权范围，不部署生产。修改 `parts/78-material-fee-workspace.js` / `48-material-fee-workspace.css`，测试仍扩展既有 `test_other_fee_picker_frontend.py` / `test_other_fees.py`，使用原工作台构建器生成资源。

- [x] 先写失败测试：每行×、三字段横向容器且无常驻小字、停用保留金额/凭证并退出分摊、重试零重复、失败不隐藏、来源/只读保护、目标行错误草稿可丢弃而其他草稿不变。
- [x] 运行对应 pytest 文件确认缺少删除入口/方法的失败，再复用 `trackMaterialFeeWrite`、编辑权、`save_fee`、只读失败恢复实施 `removeOtherFeeRow`；相同停用标识不重建。
- [x] 添加/删除共用已有费用快照刷新能力，确保排队的金额先保存且移除使用最新记录；无需新表、迁移或接口。
- [x] 名称、币种、金额统一 34px 控件并横排；保留无障碍名称和可见错误，移除多余字样、重复下拉符号和旧样式。顶部与底部说明不叠加占空间。
- [x] 构建并重复构建无变化、运行现有 15 个相关测试文件与 JS/diff 检查；独立复查后实际浏览器验证删除后金额回落、刷新不出现、其他行不变、排版和错误恢复。
- [x] 更新既有报告（新增/修改/删除、业务/参数用例及行数、兼容和未执行事项），提交本地分支，不推送部署。

### 1. 独立实例与服务端默认

文件：`services/transport_fee_service.py`、`services/fee_service.py`、`services/fee_allocation_service.py`、`tests/test_other_fees.py`（均位于 `overseas_costing/`）。

- [x] 先扩展既有测试，运行 `python3 -m pytest -q --tb=short overseas_costing/tests/test_other_fees.py`，确认动态标识默认值、非法标识和同名独立行契约失败。
- [x] 新增共享 `is_other_fee_key`，识别旧类型及 `other_custom:<32位小写十六进制UUID>`。动态行复用 other_custom 模板，但保留自己的 logical_fee_key/rule_code。增加海关查验费常用名称。
- [x] 沿用原分摊算法，动态实例默认毛重、保留人工明确依据优先级。验证重试同标识零新增、不同标识同名不覆盖、凭证独立、缺毛重与金额守恒。

```python
row = fee_service.normalize_fee_payload({'logical_fee_key': 'other_custom:'+'a'*32, 'expense_category':'海关查验费', 'amount':'100'})
assert (row['currency'], row['allocation_basis'], row['rule_code']) == ('MXN', 'gross_weight', row['logical_fee_key'])
```

### 2. 替换前端交互

文件：`page/overseas_cost_workbench/parts/78-material-fee-workspace.js`、`48-material-fee-workspace.css`；测试 `tests/test_other_fee_picker_frontend.py`。

- [x] 改写旧勾选专属测试，保留名称/金额/失败草稿保护；先运行对应 pytest 文件确认新入口缺失失败。
- [x] 用 `addOtherFeeRow` 和“＋ 添加其他费用”替换勾选切换方法，UUID 作为独立实例，使用 `trackMaterialFeeWrite` 与现有 `save_fee`。只显示合计，不保存合计费用。
- [x] 左侧分类固定其他费用；中间名称输入旁增加常用名称原生下拉，change 后写入同一名称草稿，沿用行内保存。更多设置使用同一类型识别方法。
- [x] 删除无调用的旧勾选事件、展开状态和对应样式。验证连续添加、选择/手填、同名、只改名、旧记录、来源/只读/AI、租约失败和错误草稿。

### 3. 快照与验收

- [x] 修改 `material_fee_workspace_snapshot_service.py` schema 3→4，复用既有快照测试验证旧 schema 3 重建并返回五项名称模板。
- [x] 运行既有 15 个费用相关测试文件，命令和结果记入现有报告。使用 `python3 overseas_costing/scripts/build_workbench_assets.py` 生成两套资源，重复构建无变化，两个生成 JS 通过 `node --check`，`git diff --check` 通过。
- [x] 保留当前本地预览测试记录后更新资源；浏览器验证两次＋、下拉、手填、独立金额、刷新、三种运输方式和物料分摊；不写 ERP。
- [x] 独立代码复查，修复有效问题并复测；更新既有报告，区分手写/生成物/测试/文档行数和业务场景/参数化展开数，列明删除与兼容保留、未执行生产验收。提交本地分支，不推送部署。

---

## 历史实施记录（已被上述交互替代）

> 执行方式：当前会话按 executing-plans 逐项实施。用户已经授权实施，不再增加设计确认步骤。

**Goal:** 所有运输方式都能多选其他费用、逐项填写 MXN 金额并正确分摊到物料成本。

**Architecture:** 在原有费用类型、工作列表、保存接口与费用表中扩展四个可选项。每项仍是原有费用记录，汇总只显示，计算复用现有分摊及保存试算流程。

**Tech Stack:** Frappe / Python、现有 JavaScript/jQuery 工作台、pytest、Node。

## 1. 类型和默认值

- [x] 运行现有 `test_fee_service.py`、`test_fee_allocation_service.py`、`test_saved_comprehensive_cost.py` 取得基线（156 项通过）。
- [x] 运行新增 `test_other_fees.py`，确认缺少其他费用模板导致失败，再实现通过。
- [x] 在 `transport_fee_service.py` 定义稳定类型；在 `fee_service.py` 复用配送费模板构建四个可选项并返回菜单，已选行继续进入现有费用工作列表。
- [x] 对新增类型缺省币种/状态/分摊依据采用 MXN / ESTIMATED / gross_weight。已有类型的默认值及历史金额不变。

业务契约示例：

```python
fee = fee_service.normalize_fee_payload({"logical_fee_key": "other_port_misc", "amount": "100"})
assert fee["currency"] == "MXN"
assert fee["allocation_basis"] == "gross_weight"
assert fee["amount_status"] == "ESTIMATED"
```

## 2. 多选器与独立输入

- [x] 运行 `test_other_fee_picker_frontend.py`，确认菜单与选择行为缺失导致失败，再实现通过。
- [x] 在 `78-material-fee-workspace.js` 的现有费用表内渲染可折叠多选列表，并为 change 事件复用 `trackMaterialFeeWrite` 和 `save_fee`。
- [x] 勾选保存独立暂估行；取消时使用现有 NOT_INCURRED 状态并保留原金额、备注、凭证；重新勾选更新原行，禁止恢复停用记录。
- [x] 在自定义行的更多设置中增加费用名称字段；沿用已有金额、币种、状态、凭证和适用物料入口。
- [x] 用户反馈名称入口不直观后，将名称直接展示在自定义费用行内，复用金额保存队列，补充只改名称、空名称与失败草稿保留测试。
- [x] 在 `48-material-fee-workspace.css` 增加响应式多选器样式。显示分币种合计，不生成合计费用记录。

关键验收：

```javascript
await w.toggleOtherFeeType('other_port_misc', true);
await w.toggleOtherFeeType('other_agency', true);
assert.equal(w.materialFeeState.fees.fees.length, 2);
await w.toggleOtherFeeType('other_port_misc', false);
assert.equal(w.findMaterialFee('other_port_misc').amount_status, 'NOT_INCURRED');
```

## 3. 计算与回归

- [x] 将新增类型的默认分摊依据接入现有 SYSTEM_ALLOCATION_BASES。
- [x] 使用 `build_saved_cost_data` 验证：两条物料货值合计 400 RMB，新增其他费用 300 MXN、汇率 2，保存后总成本为 550 RMB；毛重 1:3 对应新增物料费用 75:225 MXN。
- [x] 验证未发生、已包含不计费，缺毛重阻止分摊，尾差守恒，重复保存不新增行。
- [x] 运行相关费用、API、前端、试算回归；构建工作台资源，重复构建无变化，JS 语法及 diff 检查通过。
- [x] 在本地 Chrome 验证实际费用表渲染和事件；样例服务使用真实纯计算函数、进程内数据，未部署至 Frappe 或写入生产数据。

额外完成：编辑权失效后清除旧租约以便重试；凭证物化拒绝恢复已停用的费用；快照结构版本升到 3，避免旧缓存隐藏新增菜单。上述行为都先通过失败测试复现，再修复并复测。

## 交付报告

列明新增、修改、删除、手写与生成物行数、独立业务场景与展开用例数、实际测试命令和结果、兼容保留及未处理技术债。没有新数据库、第二套保存接口、汇总计费记录或重复计算引擎。
