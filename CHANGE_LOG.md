# 项目变更日志 (CHANGE LOG)

记录所有针对此项目的开发操作、代码修改、文档更新等。

---

## 2026-11-04

### [前端] 修复「在途详情」在途为 0 仍可点击（该修复在 inventory-optimize 分支丢失）
- **操作**：恢复「在途=0 时按钮置灰不可点 + 悬停提示『无在途货件』」
- **根因**：该修复由 `541f01e`（feat/ai-image-gen 检查点提交，2026-10-27）引入，但只存在于 `feat/ai-image-gen` / `backup/img-20261027` 分支，`inventory-optimize` 的 HEAD 未包含该改动
- **实现**：`record.fba_inbound <= 0` 时 `disabled`；Tooltip 外层加 `span` 承接 hover（antd 5 不再自动包裹 disabled 子元素，disabled 控件不派发鼠标事件），按钮 `pointer-events: none` 确保提示始终可见
- **涉及文件**：`frontend/src/pages/InventoryBot.tsx`（操作列「在途详情」按钮）

## 2026-11-03

### [后端+前端] 海外仓每日 8:00 定时同步 + 任务持久化 + 全量代码审查
- **操作**：新增每日定时同步，并把「切页不中断、状态实时可见」对齐 FBA 同步机制；随后对海外仓全部改动做完整审查并修复发现的问题
- **定时任务**（`backend/services/scheduler.py`）：
  - `overseas_inventory_sync_job`：cron 每天 8:00（`OVERSEAS_SYNC_HOUR` 可配），`misfire_grace_time=3600` + `coalesce=True`
  - 分布式锁 `overseas_inventory_sync`，锁内等待任务结束（≤20 分钟）→ 多进程/多 worker 不重复跑
  - `_schedule_overseas_catch_up()`：启动补跑，服务在 8:00 后启动且当天未同步 → 60 秒后自动补一次
  - `overseas_backfill_job`：每小时自查新快照（新行海外仓为 0）并按明细表回写主行，解决「库存导入晚于 8:00 导致白天显示全 0」
- **任务持久性**：
  - 任务在服务端 daemon 线程执行，与 HTTP 请求/页面生命周期解耦 → 切页、关标签、刷新均不中断
  - 前端 `localStorage`（`overseasSyncStatus`，30 分钟有效）恢复进行中任务并继续轮询，与 FBA 一致
  - 新增「单轮询链」与「卸载即停」两个守卫，修掉手动+恢复并发轮询、离开页面后仍空转请求的问题
- **审查修复**：
  - `matched` 原用 UPDATE rowcount（只统计值变化行）→ 改为 `COUNT(DISTINCT s.id)` 精确统计
  - 新增陈旧源数据保护：源表日期早于库中最新 → 跳过写入（`skipped=stale_source`），避免用旧数据覆盖新数据
  - `start_sync_async` 增加 `threading.Lock`，并发 10 请求实测仅 1 个任务启动；返回值新增 `reason`，前端不再靠字符串匹配
  - 飞书「日期」统一按 UTC+8 解析与过滤，不再依赖服务器时区
  - 明细写入由逐行 executemany（499 行 19s）改为多行 VALUES（2 次往返）
  - 主行清零范围收敛到非子行，写放大减半
- **涉及文件**：`backend/config.py`、`backend/.env.example`、`backend/services/scheduler.py`、`backend/services/overseas_inventory_service.py`、`frontend/src/pages/InventoryBot.tsx`

### [后端+前端] 库存机器人新增「海外仓同步」（飞书多维表 → 数据库 → 主行显示）
- **操作**：接入海外仓多维表（`GeaRbmErAa5KaHsFoKwc3wQXn4e` / `tblWnRHyxPehIsmE`），按 SKU + 品名同步最新库存并在库存列表主行展示
- **同步规则（覆盖式，不追加历史）**：
  - 源表为「按日快照」结构：同一 `(SKU, SKU名称)` 每天一条，21251 条历史记录实际仅 499 个 SKU，故以 `(SKU, SKU名称)` 为唯一键，取「日期」最大的一条
  - 拉取方式：按「日期」精确过滤逐天取（最近 2 天，每天 1 次请求、约 499 条，约 3s），异常/单日超 500 条时自动回退全表顺序扫描（43 页，约 2 分钟）
  - 落库方式：先按租户清空 `overseas_inventories` 再写入去重结果（源表消失的 SKU 随之清除，不会残留脏数据）
  - 展示口径：按 `(sku, product_name)` 回写 `inventory_snapshots` 最新快照的**主行**（`summary_flag != '共享库存'`），子行不落值，避免展开子行重复计数；品名缺失（名称列直接填 SKU）时降级为 SKU 单键匹配
- **执行详情**：
  - 新增 `overseas_inventories` 表（唯一键 `tenant_id + sku + product_name`，字段：站点/可售/最大库龄/源日期/飞书 record_id）
  - `inventory_snapshots` 新增 `overseas_inventory` / `overseas_max_age` 两列（`init_db()` 幂等 ALTER）
  - 实测：源表 20261103 快照 499 个 SKU、可售合计 22475，命中主行 190 条（含汇总行 56 条 + 普通主行 134 条），子行 0 条
  - 前端库存机器人新增「海外仓」列（青蓝色数值 + Tooltip 显示最大库龄）与「同步海外仓」按钮（复用 FBA 同步的进度轮询交互）
- **涉及文件**：
  - `backend/config.py`（新增 `FEISHU_OVERSEAS_BASE_TOKEN` / `FEISHU_OVERSEAS_TABLE_ID`）
  - `backend/.env.example`、`backend/.env`（配置海外仓表 ID，复用 `FEISHU_SYNC_APP_ID/SECRET`）
  - `backend/models/overseas_inventory.py`（新建）
  - `backend/models/restock.py`（InventorySnapshot 新增 2 列）
  - `backend/database/database.py`（注册模型 + 幂等 ALTER）
  - `backend/services/overseas_inventory_service.py`（新建：拉取/去重/覆盖写入/主行回写/状态机）
  - `backend/routers/restock.py`（`POST /restock/sync-overseas`、`GET /restock/sync-overseas-status`、`GET /restock/overseas-summary`）
  - `backend/services/inventory_service.py`（`search_inventory` 返回海外仓字段）
  - `frontend/src/api.ts`、`frontend/src/pages/InventoryBot.tsx`（列 + 按钮 + 轮询）

## 2026-08-07

### [文档] 重写 CODE_WIKI.md 为结构化完整版（以真实代码为准）
- **操作**：基于三个 Explore 代理对 backend/frontend/database 的真实代码交叉核对，全面重写 CODE_WIKI.md
- **执行详情**：
  - 修正过时技术栈：README 写 Node.js/Express/MongoDB 实为 FastAPI/MySQL；统一端口为前端 3000 / 后端 8002（旧文档 5173/8000 有误）
  - 补全后端 33 router、22 service、19 model 文件/52 表、7 条广告规则、RBAC 鉴权链、7 个定时任务
  - 补全前端 Provider/路由/api.ts（31 模块）、22 页面、6 套主题、useStreamingChat 死代码说明
  - 补全数据库多租户隔离策略、软删除、无 Alembic 迁移方式、`schema.sql` 已过时说明
  - 新增「已知问题与技术债」13 项（含 `AI_SEMAPHORE` 未定义、`.env` 明文凭据、`store_groups` 无 DDL、`AdRetention` 调用缺失方法等）
  - 新增顶层脚本（inventory.py 原型 / _reset_ids.py 高危 / _verify_gross_margin.py）与 Excel 数据资产说明
- **涉及文件**：`CODE_WIKI.md`（重写）

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