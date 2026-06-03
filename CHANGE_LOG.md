# 项目变更日志 (CHANGE LOG)

记录所有针对此项目的开发操作、代码修改、文档更新等。

---

## 2026-05-26

### [文档] 创建项目 Code Wiki 与操作日志
- **操作**：创建项目整体 Code Wiki 文档与操作日志规范
- **执行详情**：完成项目架构分析，重写 CODE_WIKI.md，创建 CHANGE_LOG.md
- **涉及文件**：
  - `CODE_WIKI.md`（重写）
  - `CHANGE_LOG.md`（新建）
  - `.trae/specs/generate-code-wiki/spec.md`（新建）
  - `.trae/specs/generate-code-wiki/tasks.md`（新建）
  - `.trae/specs/generate-code-wiki/checklist.md`（新建）

### [前端] 库存机器人页面日均销量显示优化
- **操作**：日均销量统一保留两位小数
- **执行详情**：
  - 库存表格「日均销量」列改为 `val.toFixed(2)` 显示两位小数
  - 断货风险 TOP10 面板中「日均销量」改为 `item.daily_sales.toFixed(2)` 显示两位小数
  - 在途详情弹窗「预计可售时间」改为根据「预计到港时间+7天」计算显示（仅前端计算，不改后端数据）
  - 预计到港时间为 `-` 时，预计可售时间也显示 `-`
- **涉及文件**：
  - `frontend/src/pages/InventoryBot.tsx`（日均销量列 render、断货TOP10 render、在途列 render）

### [后端] 日均销量精度统一处理
- **操作**：在后端 search_inventory() 统一 round(daily_sales, 2)，导出路径自动继承
- **执行详情**：
  - 后端 `backend/services/inventory_service.py` 中 `search_inventory()` 返回前对 `daily_sales` 执行 `round(val, 2)`
  - 导出函数 `export_inventory_to_excel()` 通过 `search_inventory()` 获取数据，自动获得四舍五入值
  - 前端 `.toFixed(2)` 保留不动，确保 JavaScript 中尾零正确显示（"2.50" 不显示为 "2.5"）
  - 最终效果：API返回、前端表格、TOP10面板、Excel导出，所有展示路径日均销量均为两位小数
- **涉及文件**：
  - `backend/services/inventory_service.py`（search_inventory 中 daily_sales 增加 round）

### [后端] 库存导入改为 UPSERT 方案（替代 DELETE ALL）
- **操作**：将库存导入从 DELETE ALL + INSERT ALL 改为逐行 UPSERT
- **执行详情**：
  - 删除 `_do_import()` 和 `import_inventory_data()` 中的 3 条全表 DELETE 语句
  - 新增步骤2：建立**全量**现有数据索引（不限日期）`{(asin, account, country) → id}` 和 `{(summary_flag, asin) → id}`
  - 新增步骤3 UPSERT 逻辑：按行类型匹配
    - `"0"` 和 `"共享库存"` 行 → 按 `(asin, account, country)` 匹配
    - `"是"` 汇总行 → 按 `(summary_flag, asin)` 匹配
    - 匹配到则 UPDATE（snapshot_id 不变），未匹配则 INSERT
  - 新增孤儿清理：全量索引对比当日导入数据 → 旧日期数据自动被清除
  - 空库/无数据时：索引为空 → 所有行走 INSERT，行为与原一致
- **涉及文件**：
  - `backend/services/inventory_import_service.py`（_do_import 全部重写）
  - `backend/services/inventory_service.py`（import_inventory_data 全部重写）