# overseas_costing

独立仓库为 [yueweiit/deeplinkerp-overseas_costing](https://github.com/yueweiit/deeplinkerp-overseas_costing)，开发主线和 PR 目标均为 `main`。Frappe App 名保持 `overseas_costing`。

CI 从独立 Branding 仓库获取已冻结的配套库存提交进行核验。旧生产发布任务仍限定原仓库，独立仓库迁移不启用生产部署。

First backend skeleton for the overseas purchase landed costing module.

This directory is prepared as a Frappe custom app structure and will be the
main backend codebase for the project.

Current evidence and field-default policy: [资料来源的字段优先级](docs/source-field-priority.md).

## YW GRUPO MX 转入 LATINGO MX 的代码兼容

公司初始化的墨西哥目标为 `LATINGO MX / LATMX`；外部 OA 项目标签 `YW Centro MX 共享中心 YUEWEI Grupo` 保留，默认路由指向 `LATINGO MX`。中国项目 `LatinGo拉丁购` 仍指向 `拉丁购国际电子商务（东莞）有限公司`。若旧公司或其历史长名仍存在，初始化返回 `COMPANY_MIGRATION_REQUIRED` 并停止全部写入；旧路由仍指向旧公司时也会报告冲突，不自动覆盖。入口见 [company_route_provision_service.py](overseas_costing/services/company_route_provision_service.py)。

历史六家公司改名补丁保留原有映射与冲突校验。旧公司仍存在时，部署检查按历史目标核验并返回 `pending_company_migrations`；旧公司不存在而 `LATINGO MX` 存在时，检查接受新目标，并继续排查旧长名及 `YW GRUPO MX` 的 Company Link、已知 Data 字段、Dynamic Link 和保存的筛选条件。其他公司的完整性要求不变；只含 `LATINGO MX` 的无关站点不会因此触发历史六家公司改名。入口见 [company_rename_service.py](overseas_costing/services/company_rename_service.py)。

代码发布后，实际数据维护仍须单独准备、演练和授权。本次代码不新增迁移补丁，不自动合并公司、搬移业务记录或调整用户权限。现有 `after_migrate` 仍只按路由当前公司补齐缺失默认值，不会把旧路由改到新公司。

数据维护完成后，在相应 Bench 对目标站点运行严格的**只读**公司引用检查（将 `<site>` 替换为站点名）：

```bash
bench --site <site> execute \
  overseas_costing.services.company_rename_service.verify_company_renames_complete \
  --kwargs '{"require_latingo_migration": true}'
```

严格模式在旧公司仍存在时返回 `LATINGO_COMPANY_MIGRATION_REQUIRED`。该检查只验证上述公司引用范围，不能替代科目/仓库映射、已提交单据、权限范围、外部 OA 数据及前后账务一致性的专项迁移验收。默认无参数调用保留迁移前的部署兼容性，其通过不代表数据迁移完成。
