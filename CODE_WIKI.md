# 宝鑫华盛AI助手 - 跨境电商智能运营平台 · Code Wiki

## 1. 项目概述

宝鑫华盛AI助手是一个面向跨境电商卖家的智能运营平台，专注于差评分析、库存管理和AI智能问答三大核心功能。

**核心功能：**

- **差评机器人**: 自动拉取各平台商品评论，AI分析差评内容，识别核心问题，提供处理建议，支持重要性分级（严重/中等/轻微）
- **库存机器人**: 导入FBA库存Excel快照，AI计算日均销量（可配置权重公式），自动识别断货风险和冗余库存，生成补货建议和智能预警
- **AI聊天助手**: 双模式AI对话（差评分析/库存分析），支持SSE流式响应，集成库存查询和差评分析工具调用，支持会话管理和导出
- **数据看板**: 销售趋势、库存分布、预警概览、最新差评实时监控
- **部门与角色管理**: 多租户架构，部门-用户多对多关联，店铺归属部门管理
- **飞书集成**: 飞书多维表FBA在途数据同步

**目标用户**: 跨境电商运营团队、供应链管理人员、客服团队

---

## 2. 项目架构

```
┌─────────────────────────────────────────────────────────┐
│                    前端 React 18                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐  │
│  │ Ant Design│  │  Recharts │  │  SSE 流式 Chat       │  │
│  │  Pro 组件 │  │  图表库   │  │  (EventSource)       │  │
│  └──────────┘  └──────────┘  └──────────────────────┘  │
│                    Axios HTTP Client                    │
│         Auth Interceptor + Response Interceptor         │
└───────────────────────┬─────────────────────────────────┘
                        │ HTTP / SSE
                        ▼
┌─────────────────────────────────────────────────────────┐
│                   后端 FastAPI                           │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Middleware: CORS / JWT 认证 / 权限控制           │   │
│  ├──────────────────────────────────────────────────┤   │
│  │  Routers: /api/auth, /api/chat, /api/reviews,    │   │
│  │  /api/inventory, /api/restock, /api/dashboard,   │   │
│  │  /api/stores, /api/products, /api/departments,   │   │
│  │  /api/notifications, /api/tenants, /api/business-│   │
│  │  settings, /api/local-inventory, /api/store-     │   │
│  │  mapping                                          │   │
│  ├──────────────────────────────────────────────────┤   │
│  │  Services: auth, inventory(导入/计算/搜索/导出),  │   │
│  │  chat(AI对话+工具调用), streaming(SSE), translate, │   │
│  │  feishu(飞书集成), scheduler(定时任务)             │   │
│  ├──────────────────────────────────────────────────┤   │
│  │  Models: Tenant, User, Store, Product,           │   │
│  │  InventoryRecord/Alert/Action, Review/Analysis/  │   │
│  │  Handling, InventorySnapshot, InboundShipmentDet, │   │
│  │  ReplenishmentDecision, ConversationHistory,      │   │
│  │  Department, UserDepartment, BusinessSettings,    │   │
│  │  LocalInventory                                   │   │
│  ├──────────────────────────────────────────────────┤   │
│  │  Security: 输入验证/清理/速率限制/数据脱敏         │   │
│  └──────────────────────────────────────────────────┘   │
│                       │                                 │
│                       ▼                                 │
│              MySQL 8.0 / SQLAlchemy 2.0                 │
└─────────────────────────────────────────────────────────┘
```

### 完整目录结构

```
AIBXHS/
├── CODE_WIKI.md                          # 本文档
├── backend/
│   ├── main.py                           # FastAPI 应用入口，注册所有路由和中间件
│   ├── config.py                         # Pydantic Settings 配置管理（数据库、JWT、AI等）
│   ├── dependencies.py                   # 依赖注入（JWT认证、权限校验、数据库会话）
│   ├── requirements.txt                  # Python 依赖清单
│   ├── database/
│   │   └── database.py                   # SQLAlchemy 引擎创建、SessionLocal、get_db、init_db
│   ├── models/
│   │   ├── base.py                       # BaseModel（自动 created_at/updated_at/deleted_at 时间戳）
│   │   ├── tenant.py                     # Tenant 租户模型
│   │   ├── user.py                       # User 用户模型
│   │   ├── store.py                      # Store 店铺模型（platform/site/department_id/inventory_name）
│   │   ├── product.py                    # Product 商品模型
│   │   ├── inventory.py                  # InventoryRecord/InventoryAlert/InventoryAction
│   │   ├── review.py                     # Review/ReviewAnalysis/ReviewHandling（含ImportanceLevel枚举）
│   │   ├── restock.py                    # InventorySnapshot/InboundShipmentDetail/ReplenishmentDecision
│   │   ├── conversation.py               # ConversationHistory（chat_type/is_deleted）
│   │   ├── department.py                 # Department/UserDepartment
│   │   ├── business_settings.py          # BusinessSettings（公式配置存储）
│   │   └── local_inventory.py            # LocalInventory（本地仓库存）
│   ├── routers/
│   │   ├── auth.py                       # /api/auth 认证
│   │   ├── chat.py                       # /api/chat 聊天
│   │   ├── dashboard.py                  # /api/dashboard 看板
│   │   ├── inventory.py                  # /api/inventory 库存预警
│   │   ├── restock.py                    # /api/restock 补货管理
│   │   ├── reviews.py                    # /api/reviews 评论管理
│   │   ├── departments.py                # /api/departments 部门管理
│   │   ├── notifications.py              # /api/notifications 通知
│   │   ├── stores.py                     # /api/stores 店铺
│   │   ├── products.py                   # /api/products 商品
│   │   ├── tenants.py                    # /api/tenants 租户
│   │   ├── local_inventory.py            # /api/local-inventory 本地仓
│   │   ├── business_settings.py          # /api/business-settings 业务设置
│   │   └── store_mapping.py              # /api/store-mapping 店铺映射
│   ├── services/
│   │   ├── auth_service.py               # JWT令牌、密码哈希、用户注册/登录
│   │   ├── inventory_service.py          # 核心：Excel导入、日均销量计算、搜索、概览、导出
│   │   ├── inventory_import_service.py   # 后台异步导入库存（防止超时）
│   │   ├── local_inventory_service.py    # 本地仓库存Excel导入/查询
│   │   ├── chat_service.py               # AI聊天（差评分析/库存分析/工具调用）
│   │   ├── streaming_service.py          # SSE流式响应
│   │   ├── translate_service.py          # AI翻译
│   │   ├── feishu_service.py             # 飞书多维表集成
│   │   ├── feishu_sync_service.py        # 飞书FBA在途同步（后台线程）
│   │   └── scheduler.py                  # APScheduler 定时任务
│   ├── schemas/
│   │   └── chat_schemas.py               # Pydantic 请求/响应模型
│   ├── utils/
│   │   ├── security.py                   # 安全工具（输入验证/清理/速率限制/脱敏）
│   │   └── store_mapping.py              # 店铺名映射工具
│   ├── tests/
│   │   ├── __init__.py
│   │   └── test_chat.py                  # 聊天功能测试（含安全测试）
│   └── scripts/
│       ├── init_data.py                  # 初始化数据库种子数据
│       ├── check_*.py                    # 数据检查脚本
│       ├── test_*.py                     # 测试脚本
│       ├── add_*.py / fill_*.py          # 数据添加/填充脚本
│       ├── analyze_*.py                  # 数据分析脚本
│       ├── fix_*.py / rollback_*.py      # 数据修复/回滚脚本
│       ├── backups/                      # 备份JSON文件
│       └── reports/                      # 分析报告JSON
├── frontend/
│   ├── index.html                        # HTML 入口
│   ├── vite.config.ts                    # Vite 构建配置
│   ├── package.json                      # 项目依赖（React 18 + Vite 5 + AntD 5）
│   ├── tsconfig.json                     # TypeScript 配置
│   └── src/
│       ├── main.tsx                      # React 入口（ThemeProvider + ConfigProvider）
│       ├── App.tsx                       # 路由配置（AuthProvider + BrowserRouter）
│       ├── api.ts                        # Axios 封装（所有API端点）
│       ├── index.css                     # 全局样式 + 主题变量
│       ├── contexts/
│       │   ├── AuthContext.tsx            # 认证状态管理（login/register/logout）
│       │   └── ThemeContext.tsx           # 6种主题色管理
│       ├── hooks/
│       │   └── useStreamingChat.ts        # SSE流式聊天Hook（rAF节流）
│       ├── components/
│       │   ├── Layout/
│       │   │   └── MainLayout.tsx         # 主布局（侧边栏+顶栏+通知中心）
│       │   ├── ThemeSwitcher/
│       │   │   └── index.tsx             # 主题切换下拉
│       │   ├── common/
│       │   │   └── MarkdownRenderer.tsx   # 轻量Markdown渲染器
│       │   ├── ProtectedRoute.tsx         # 路由守卫
│       │   └── ChangePasswordModal.tsx    # 修改密码弹窗
│       └── pages/
│           ├── Login.tsx                  # 登录页
│           ├── Register.tsx               # 注册页
│           ├── Home.tsx                   # 首页（待办事项+通知）
│           ├── Dashboard.tsx              # 数据看板（图表+统计）
│           ├── InventoryBot.tsx           # 库存机器人（概览+表格+导入+导出）
│           ├── ReviewBot.tsx              # 差评机器人（列表+筛选+分析+批量操作）
│           ├── ChatBot.tsx                # AI聊天助手（双模式+流式+会话管理）
│           ├── OrgManagement.tsx          # 组织管理（部门+用户）
│           ├── StoreManagement.tsx        # 店铺管理（CRUD+部门分配）
│           ├── ProductManagement.tsx      # 产品管理（可伸缩列+CRUD）
│           ├── TenantManagement.tsx       # 租户管理
│           └── BusinessSettings.tsx       # 业务设置（日均销量权重公式配置）
├── database/
│   ├── schema.sql                        # 完整DDL（含所有表）
│   ├── drop_inventory_tables.sql         # 删除库存相关表
│   └── migrations/
│       ├── v1_add_departments_notifications.sql  # v1: 部门+通知+重要性
│       ├── v1_simple.sql                        # v1简化版
│       ├── v2_add_store_inventory_name.sql       # v2: inventory_name字段
│       ├── migrate.py                     # Python 迁移执行脚本
│       ├── run_migration.py               # 迁移运行器
│       ├── run_store_mapping_migration.py # 店铺映射迁移
│       ├── check_db.py                    # 数据库检查
│       ├── check_importance_data.py       # 重要性数据检查
│       ├── create_test_data.py            # 测试数据创建
│       ├── create_test_user.py            # 测试用户创建
│       ├── fix_default_importance.py      # 修复默认重要性
│       ├── fix_store_names.py             # 修复店铺名
│       ├── restore_store_names.py         # 恢复店铺名
│       ├── restore_stores_full.py         # 完全恢复店铺
│       ├── rollback_*.py                  # 回滚脚本
│       ├── reset_admin_password.py        # 重置管理员密码
│       ├── simple_test.py                 # 简单测试
│       ├── test_notification.py           # 通知测试
│       ├── update_mapping_rules.py        # 更新映射规则
│       └── *.json                         # 备份和报告文件
```

---

## 3. 技术栈

### 后端技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | 3.10+ | 运行语言 |
| FastAPI | 0.109.0 | Web框架 |
| Uvicorn | 0.27.0 | ASGI服务器 |
| SQLAlchemy | 2.0.25 | ORM框架 |
| PyMySQL | 1.1.0 | MySQL驱动 |
| Pydantic | 2.5.3 | 数据验证 |
| Pydantic-Settings | 2.1.0 | 配置管理 |
| python-jose | 3.3.0 | JWT令牌 |
| passlib[bcrypt] | 1.7.4 | 密码哈希 |
| OpenAI | 1.10.0 | AI大模型客户端 |
| APScheduler | 3.10.4 | 定时任务 |
| pandas | 2.x | Excel数据处理 |
| python-multipart | 0.0.6 | 文件上传 |
| httpx | 0.26.0 | HTTP客户端 |
| python-dotenv | 1.0.0 | 环境变量 |
| pytest | 7.x | 单元测试 |

### 前端技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| React | 18.2.0 | UI框架 |
| TypeScript | 5.2.2 | 类型系统 |
| Vite | 5.0.8 | 构建工具 |
| Ant Design | 5.12.0 | 组件库 |
| Axios | 1.6.5 | HTTP请求 |
| React Router | 6.22.0 | 路由 |
| Recharts | 2.10.3 | 图表库 |
| Lucide React | 0.300.0 | 图标库 |
| dayjs | 1.11.10 | 日期处理 |
| react-resizable | 3.1.3 | 可伸缩列 |

---

## 4. 数据库设计

项目采用 MySQL 8.0 数据库，共有 18 张表，以下按模块分类说明。

### 4.1 租户与用户（多租户架构）

| 表名 | 说明 | 核心字段（ORM模型） |
|------|------|---------------------|
| `tenants` | 租户表 | `id`, `name`, `code`(唯一索引), `status`(active/suspended/expired). *注：schema.sql中有更多字段（contact_name/phone/email, plan_type, plan_expire_at, max_users/max_stores, config），但ORM模型仅包含基本字段* |
| `users` | 用户表 | `id`, `tenant_id`(FK), `username`, `email`, `password_hash`, `nickname`, `role`(admin/operator/viewer), `status` |
| `departments` | 部门表 | `id`, `tenant_id`(FK), `name`, `description` |
| `user_departments` | 用户-部门关联 | `id`, `user_id`(FK), `department_id`(FK), 支持多对多关系 |

> **注意**：ORM 模型（SQLAlchemy）定义的字段是 schema.sql DDL 的子集。schema.sql 包含更完整的业务字段设计（如套餐、计费、审计等）。应用运行依赖 ORM 模型自动创建的表结构，但 schema.sql 中的额外字段需手动迁移。

### 4.2 店铺与商品

| 表名 | 说明 | 核心字段 |
|------|------|----------|
| `stores` | 店铺表 | `id`, `tenant_id`(FK), `name`, `platform`(amazon/shopee/lazada/tiktok/other), `site`(US/UK/CA等), `department_id`(FK), `inventory_name`(库存匹配别名), `status`(active/inactive/error) |
| `products` | 商品表 | `id`, `tenant_id`(FK), `store_id`(FK), `asin`, `sku`, `name`, `name_en`, `image_url`, `category`, `brand`, `price`(DECIMAL), `cost_price`(DECIMAL), `status`, `is_robot_monitored`, `config`(JSON), `created_by`(FK) |

### 4.3 库存管理

| 表名 | 说明 | 核心字段 |
|------|------|----------|
| `inventory_records` | 库存记录 | `id`, `product_id`(FK), `store_id`(FK), `warehouse_code`, `quantity`, `quantity_in_transit`, `quantity_available`, `safe_stock`, `daily_sales`, `days_remaining`(generated), `record_date`, `source`(manual/api_sync/import) |
| `inventory_alerts` | 库存预警 | `id`, `product_id`(FK), `store_id`(FK), `alert_type`(low_stock/out_of_stock/overstock/price_change), `severity`(info/warning/danger/critical), `title`, `current_stock`, `safe_stock`, `suggestions`(JSON), `status`(new/acknowledged/processing/resolved/dismissed), `priority`(1-10) |
| `inventory_actions` | 库存操作 | `id`, `product_id`(FK), `store_id`(FK), `alert_id`(FK), `action_type`(price_adjust/ad_budget/promotion/restock/other), `status`(pending/executing/success/failed/cancelled), `triggered_by`(system_auto/manual/schedule) |
| `inventory_snapshots` | 库存快照 | `id`, `asin`, `sku`, `fnsku`, `msku`, `product_name`, `account`, `country`, `fba_stock`, `fba_inbound`, `fba_available`, `total_stock`, `daily_sales`, `days_supply_fba`, `stockout_date`, `sales_3d/7d/14d/30d/60d/90d`, `daily_avg_3d/7d/14d/30d/60d/90d`, `stock_up_duration`, 库龄字段(age_0_3/3_6/6_9/9_12/12_plus) |
| `inbound_shipment_details` | 在途详情 | `id`, `snapshot_id`(FK), `shipment_id`, `quantity`, `estimated_arrival_date`, `transport_method`, `logistics_method`, `ship_date` |
| `replenishment_decisions` | 补货决策 | `id`, `snapshot_id`(FK), `asin`, `product_name`, `account`, `country`, `suggested_quantity`, `reason`, `priority`, `risk_level` |
| `local_inventories` | 本地仓库存 | `id`, `sku`, `product_name`, `quantity`, `warehouse_code`, `record_date`, `tenant_id` |

### 4.4 评论管理

| 表名 | 说明 | 核心字段 |
|------|------|----------|
| `reviews` | 评论表 | `id`, `store_id`(FK), `product_id`(FK), `review_id`(平台ID), `reviewer_name`, `rating`(1-5), `title`, `content`, `content_translated`, `is_negative`(generated: rating<=3), `review_date`, `status`(new/read/processing/resolved/dismissed), `priority`, `tags`(JSON), `importance_level`(high/medium/low), `feishu_record_id` |
| `review_analyses` | AI分析结果 | `id`, `review_id`(FK,唯一), `model`, `sentiment`(positive/neutral/negative), `sentiment_score`, `key_points`(JSON), `topics`(JSON), `suggestions`(JSON), `summary`, `analysis_time`(ms) |
| `review_handlings` | 处理记录 | `id`, `review_id`(FK), `handler_id`(FK), `action`(read/tag/comment/reply/dismiss/other), `note`, `reply_content`, `reply_sent`(boolean) |

### 4.5 系统与通用

| 表名 | 说明 | 核心字段 | ORM模型 |
|------|------|----------|---------|
| `conversation_history` | 对话历史 | `user_id`(FK), `session_id`, `role`, `content`, `chat_type`(review/inventory), `is_deleted` | 有 |
| `notifications` | 消息通知 | `tenant_id`, `user_id`, `type`(alert/info/warning/success), `title`, `content`, `link`, `is_read`(generated) | 无（使用原生SQL操作） |
| `business_settings` | 业务设置 | `setting_type`, `setting_name`, `formula_config`(JSON), `is_active` | 有 |
| `scheduled_tasks` | 定时任务 | `tenant_id`, `task_name`, `task_type`, `cron_expression`, `config`(JSON), `status` | 仅schema.sql |
| `task_execution_logs` | 任务日志 | `task_id`(FK), `status`, `start_time`, `end_time`, `duration`, `error_message` | 仅schema.sql |
| `audit_logs` | 审计日志 | `user_id`(FK), `action`, `resource_type`, `resource_id`, `old_value`(JSON), `new_value`(JSON) | 仅schema.sql |
| `system_configs` | 系统配置 | `config_key`, `config_value`, `config_type`, `is_encrypted` | 仅schema.sql |

> **说明**：`notifications` 表由 scheduler.py 中 `push_daily_review_notifications_job()` 通过原生SQL操作（INSERT/SELECT），未定义 ORM 模型。`scheduled_tasks`、`task_execution_logs`、`audit_logs`、`system_configs` 仅定义在 schema.sql 中，暂未在代码中使用。

---

## 5. 后端模块说明

### 5.1 Models 层

所有模型继承自 [base.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/models/base.py) 中的 `Base`（declarative_base）和 `BaseModel`（提供自动时间戳和软删除）。

#### Tenant ([tenant.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/models/tenant.py))
- 多租户架构核心模型
- 字段(ORM): `name`, `code`(唯一索引), `status`(默认active)
- 关系: 一对多 -> User, Store, Product, Department
- *注：schema.sql 中还定义了 `contact_name/phone/email`, `plan_type`, `plan_expire_at`, `max_users/max_stores`, `config`(JSON) 等字段，ORM模型不包含*

#### User ([user.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/models/user.py))
- 用户模型，继承 BaseModel（自动获得 created_at/updated_at/deleted_at）
- 字段: `username`, `email`, `password_hash`, `nickname`, `role`(默认operator), `status`(默认active)
- 关系: 多对一 -> Tenant; 多对多 -> Department (通过 UserDepartment)

#### Store ([store.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/models/store.py))
- 店铺模型，支持多平台
- 字段: `name`, `platform`(字符串,非ENUM), `site`, `department_id`(FK可空), `inventory_name`(库存匹配名称，用于Excel导入时账号名称映射), `status`(默认active)
- 关系: 多对一 -> Tenant, Department

#### Product ([product.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/models/product.py))
- 商品模型，包含 ASIN/SKU 和价格信息
- 字段: `asin`, `sku`, `name`, `name_en`, `image_url`, `category`, `brand`, `price`(DECIMAL), `cost_price`(DECIMAL), `status`(active/inactive/archived), `is_robot_monitored`(Boolean), `config`(JSON), `created_by`(FK)
- 关系: 多对一 -> Tenant, Store

#### Inventory 模块 ([inventory.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/models/inventory.py))
- `InventoryRecord`: 库存记录，唯一约束(product_id, record_date)，`days_remaining`为generated字段
- `InventoryAlert`: 库存预警，支持多种预警类型和严重程度，`suggestions`为JSON
- `InventoryAction`: 操作记录，关联预警，记录执行结果

#### Review 模块 ([review.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/models/review.py))
- `Review`: 评论主表，字段包括 `asin`, `reviewer_name`, `rating`(1-5), `title`, `content`, `translated_title`, `translated_content`, `review_date`, `account`, `site`, `return_rate`, `status`(new/read/processing/resolved/dismissed), `importance_level`(high/medium/low)
- `ReviewAnalysis`: AI分析结果，一对一关联Review，存储 `sentiment`(positive/neutral/negative), `sentiment_score`, `key_points`(JSON), `topics`(JSON), `suggestions`(JSON), `summary`, `model`, `analysis_time`(ms)
- `ReviewHandling`: 处理记录，关联Review和Handler，记录 `action`(read/tag/comment/reply/dismiss/other), `note`, `reply_content`, `reply_sent`(boolean)

#### Restock 模块 ([restock.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/models/restock.py))
- `InventorySnapshot`: 库存快照表，存储从Excel导入的完整库存数据，包含销量、库存、库龄等字段
- `InboundShipmentDetail`: 在途货件详情，关联快照，记录货件号、数量、预计到货日期、运输方式
- `ReplenishmentDecision`: 补货决策表，存储AI计算出的补货建议（建议数量、原因、优先级、风险等级）

#### ConversationHistory ([conversation.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/models/conversation.py))
- 对话历史记录，区分聊天类型
- 字段: `user_id`(FK), `session_id`, `role`(user/assistant/system), `content`, `chat_type`(review/inventory), `function_name`, `is_deleted`(Boolean,默认False)

#### Department & UserDepartment ([department.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/models/department.py))
- `Department`: 部门，关联租户，支持软删除
- `UserDepartment`: 用户-部门多对多关联表，唯一约束(user_id, department_id)

#### BusinessSettings ([business_settings.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/models/business_settings.py))
- 业务设置表，用于存储日均销量计算权重公式
- 字段: `setting_type`(唯一), `setting_name`, `formula_config`(JSON), `is_active`(boolean)

#### LocalInventory ([local_inventory.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/models/local_inventory.py))
- 本地仓库存记录
- 字段: `sku`, `product_name`, `quantity`, `warehouse_code`, `record_date`, `tenant_id`

---

### 5.2 Services 层

#### [auth_service.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/services/auth_service.py)
- `get_password_hash(password)`: bcrypt密码哈希
- `verify_password(plain, hashed)`: 密码验证
- `create_access_token(data, expires_delta)`: 创建JWT访问令牌
- `create_user(db, username, email, password, ...)`: 创建新用户（自动分配租户）
- `authenticate_user(db, username, password)`: 用户认证

#### [inventory_service.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/services/inventory_service.py)
核心库存服务，包含以下主要函数：

- `import_inventory_data(db, file_path/file_content, filename)`: 导入Excel库存数据（极速版）：
  1. 读取Excel（pandas），重命名列（中文→英文字段名）
  2. 数据清洗，计算日均销量（权重公式由BusinessSettings配置）
  3. 清理旧数据（删三张表），批量插入快照（1000条/批）
  4. 解析在途详情，插入到inbound_shipment_details
  5. 计算补货决策，插入到replenishment_decisions
  6. 返回导入统计（总行数、库存预警统计、断货/冗余Top10）

- `get_overview(db, tenant_id)`: 获取库存概览（总SKU、红/黄/绿等级统计、断货/冗余Top10）
- `search_inventory(db, keyword, risk_level, account, country, page, page_size)`: 搜索库存（支持多条件筛选）
- `get_inbound_details(db, asin, account)`: 获取指定商品的在途详情
- `get_latest_date(db)`: 获取最新快照日期
- `export_inventory_data(db, keyword, risk_level, account, country, fields)`: 导出库存为Excel

字段映射示例（Excel中文→数据库字段）：
```
"ASIN" → "asin", "品名" → "product_name", "店铺" → "account",
"FBA库存" → "fba_stock", "3天销量" → "sales_3d",
"可售天数(FBA)" → "days_supply_fba", "断货时间" → "stockout_date"
```

#### [inventory_import_service.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/services/inventory_import_service.py)
- 后台异步导入库存（防止Excel导入耗时过长导致前端超时）
- 使用后台线程执行导入，前端轮询导入状态

#### [local_inventory_service.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/services/local_inventory_service.py)
- 导入本地仓库存Excel
- 查询本地仓库存列表和摘要统计

#### [chat_service.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/services/chat_service.py)
AI聊天服务，支持两种对话模式：

- `process_chat_message(db, user, message, session_id, chat_type)`: 处理聊天消息：
  1. 生成会话ID（如无）
  2. 保存用户消息到数据库
  3. 构建对话上下文（历史消息+系统提示词）
  4. 调用AI大模型（含工具调用：`query_inventory_by_asin`, `get_review_analysis`）
  5. 保存AI回复到数据库
  6. 返回回复内容和会话ID

- 系统提示词根据chat_type不同：
  - `review`: 差评分析助手角色，分析差评原因、提供改进建议
  - `inventory`: 库存分析助手角色，分析库存数据、预警断货风险

- 工具函数：
  - `query_inventory_by_asin(asin)`: 查询指定ASIN的库存信息
  - `get_review_analysis(params)`: 查询差评分析数据

#### [streaming_service.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/services/streaming_service.py)
SSE（Server-Sent Events）流式响应服务：

- `generate_streaming_response(db, user, message, session_id, chat_type)`: 生成流式AI回复
  - 使用异步生成器逐块产生SSE数据块
  - 数据块类型: `start`, `thinking`, `content`, `done`, `error`
  - 前端通过 `EventSource` 或 `fetch + ReadableStream` 消费

#### [translate_service.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/services/translate_service.py)
- AI翻译服务，用于评论翻译

#### [feishu_service.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/services/feishu_service.py)
- 飞书多维表集成服务
- 从飞书多维表获取FBA在途货件数据
- 支持并发获取（ThreadPoolExecutor, max_workers=5）

#### [feishu_sync_service.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/services/feishu_sync_service.py)
- 飞书FBA在途数据同步服务
- 后台线程执行，内存状态管理（is_running/progress/total/updated/error）
- 同步逻辑：
  1. 获取飞书tenant_access_token
  2. 批量获取最近90天数据
  3. 批量UPDATE `inbound_shipment_details` 表的transport_method和estimated_arrival_date
- 提供 `start_sync_async()`, `get_sync_status()` API

#### [scheduler.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/services/scheduler.py)
- 基于APScheduler的定时任务管理
- 初始化时注册定时任务

---

### 5.3 Routers/API 层

所有路由通过 `main.py` 注册，统一前缀 `/api`。

| 路由文件 | 前缀 | 端点 | 说明 |
|---------|------|------|------|
| [auth.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/routers/auth.py) | `/api/auth` | `POST /register` | 用户注册 |
| | | `POST /login` | 用户登录，返回JWT |
| | | `GET /me` | 获取当前用户信息 |
| | | `POST /change-password` | 修改密码 |
| [chat.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/routers/chat.py) | `/api/chat` | `POST /` | 发送聊天消息 |
| | | `POST /stream` | SSE流式聊天 |
| | | `GET /sessions` | 获取会话列表 |
| | | `GET /sessions/{id}/messages` | 获取会话消息 |
| | | `DELETE /sessions/{id}` | 删除会话 |
| | | `POST /search` | 搜索会话 |
| | | `POST /export` | 导出会话 |
| [dashboard.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/routers/dashboard.py) | `/api/dashboard` | `GET /stats` | 获取看板统计数据 |
| [inventory.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/routers/inventory.py) | `/api/inventory` | `GET /` | 库存记录列表 |
| | | `GET /alerts` | 库存预警列表 |
| | | `PUT /{id}` | 更新库存记录 |
| [restock.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/routers/restock.py) | `/api/restock` | `POST /import` | 导入库存Excel |
| | | `POST /calculate` | 重新计算补货 |
| | | `GET /overview` | 库存概览 |
| | | `GET /search` | 搜索库存 |
| | | `GET /stockout-top10` | 断货Top10 |
| | | `GET /overstock-top10` | 冗余Top10 |
| | | `GET /inbound-details` | 在途详情 |
| | | `GET /latest-date` | 最新快照日期 |
| | | `GET /filter-options` | 筛选选项 |
| | | `GET /export` | 导出Excel |
| | | `POST /sync-feishu-inbound` | 同步飞书在途 |
| | | `GET /sync-feishu-status` | 飞书同步状态 |
| [reviews.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/routers/reviews.py) | `/api/reviews` | `GET /` | 评论列表（支持分页/搜索/排序/筛选） |
| | | `GET /{id}` | 评论详情 |
| | | `PUT /{id}/status` | 更新处理状态 |
| | | `PUT /{id}/importance` | 更新重要性等级 |
| | | `POST /analyze/batch` | 批量AI分析 |
| | | `GET /new/count` | 新评论数量 |
| [departments.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/routers/departments.py) | `/api/departments` | `GET /` | 部门列表 |
| | | `POST /` | 创建部门 |
| | | `PUT /{id}` | 更新部门 |
| | | `DELETE /{id}` | 删除部门 |
| | | `GET /{id}/members` | 部门成员 |
| | | `POST /{id}/members` | 添加成员 |
| | | `DELETE /{id}/members/{uid}` | 移除成员 |
| | | `GET /users/all` | 所有用户 |
| | | `PUT /users/{uid}/departments` | 更新用户部门 |
| | | `POST /users` | 创建用户 |
| | | `POST /users/batch-assign` | 批量分配部门 |
| [notifications.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/routers/notifications.py) | `/api/notifications` | `GET /` | 通知列表 |
| | | `GET /unread-count` | 未读数量 |
| | | `PUT /{id}/read` | 标记已读 |
| | | `PUT /read-all` | 全部已读 |
| [stores.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/routers/stores.py) | `/api/stores` | `GET /` | 店铺列表（分页） |
| | | `POST /` | 创建店铺 |
| | | `PUT /{id}` | 更新店铺 |
| | | `DELETE /{id}` | 删除店铺 |
| | | `POST /batch-update-department` | 批量更新部门 |
| [products.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/routers/products.py) | `/api/products` | `GET /` | 商品列表（分页） |
| | | `GET /{id}` | 商品详情 |
| | | `POST /` | 创建商品 |
| | | `PUT /{id}` | 更新商品 |
| | | `DELETE /{id}` | 删除商品 |
| [tenants.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/routers/tenants.py) | `/api/tenants` | `GET /` | 租户列表 |
| | | `GET /{id}` | 租户详情 |
| | | `PUT /{id}` | 更新租户 |
| [local_inventory.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/routers/local_inventory.py) | `/api/local-inventory` | `POST /import` | 导入本地仓Excel |
| | | `GET /summary` | 本地仓摘要 |
| | | `GET /list` | 本地仓列表 |
| | | `DELETE /clear` | 清空数据 |
| [business_settings.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/routers/business_settings.py) | `/api/business-settings` | `GET /` | 所有设置 |
| | | `GET /{type}` | 按类型获取 |
| | | `PUT /{type}` | 更新设置 |
| | | `POST /reset/{type}` | 重置设置 |
| [store_mapping.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/routers/store_mapping.py) | `/api/store-mapping` | `GET /` | 获取映射关系 |
| | | `POST /auto-update` | 自动更新映射 |

---

### 5.4 配置与依赖

#### [config.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/config.py)
基于Pydantic Settings的配置管理，从环境变量读取：
- `DATABASE_URL`: MySQL连接字符串
- `SECRET_KEY`: JWT密钥
- `ALGORITHM`: JWT算法（HS256）
- `ACCESS_TOKEN_EXPIRE_MINUTES`: 令牌过期时间
- `AI_API_KEY`: AI大模型API密钥
- `AI_BASE_URL`: AI服务地址
- `AI_MODEL`: 模型名称

#### [dependencies.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/dependencies.py)
- `get_db()`: 数据库会话依赖（每请求自动关闭）
- `get_current_user()`: JWT令牌验证，返回当前用户
- `require_admin()`: 管理员权限校验

#### [database.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/database/database.py)
- 创建 SQLAlchemy `engine`（连接池）
- `SessionLocal`: 会话工厂
- `Base`: declarative_base
- `get_db()`: 生成器函数，FastAPI依赖注入
- `init_db()`: 创建所有表

---

## 6. 前端架构

### 6.1 页面组件

| 页面 | 路径 | 功能 |
|------|------|------|
| [Login.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/pages/Login.tsx) | `/login` | 登录表单，动态主题背景 |
| [Register.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/pages/Register.tsx) | `/register` | 注册表单 |
| [Home.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/pages/Home.tsx) | `/` | 首页：欢迎语、模块卡片、待办事项、通知栏 |
| [Dashboard.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/pages/Dashboard.tsx) | `/dashboard` | 看板：4个统计卡片、销售趋势折线图、库存分布柱状图、预警/差评表格 |
| [InventoryBot.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/pages/InventoryBot.tsx) | `/inventory` | 库存管理：概览统计、库存表格、导入、导出、搜索筛选、在途详情、本地仓 |
| [ReviewBot.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/pages/ReviewBot.tsx) | `/review` | 差评管理：评论列表、搜索/排序/筛选、重要性标记、批量AI分析 |
| [ChatBot.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/pages/ChatBot.tsx) | `/chat` | AI聊天：双模式切换(差评/库存)、会话列表、流式消息渲染 |
| [OrgManagement.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/pages/OrgManagement.tsx) | `/org` | 组织管理：部门CRUD、用户管理、部门成员分配、批量分配 |
| [StoreManagement.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/pages/StoreManagement.tsx) | `/stores` | 店铺管理：CRUD、搜索、批量更新部门 |
| [ProductManagement.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/pages/ProductManagement.tsx) | `/products` | 产品管理：CRUD、可伸缩列、列显隐设置 |
| [TenantManagement.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/pages/TenantManagement.tsx) | `/tenants` | 租户管理：列表、编辑 |
| [BusinessSettings.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/pages/BusinessSettings.tsx) | `/business-settings` | 业务设置：日均销量权重公式配置 |

### 6.2 上下文 (Contexts)

- [AuthContext.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/contexts/AuthContext.tsx): 用户认证状态管理，提供 `login/register/logout` 方法，自动从 localStorage 恢复 token，启动时验证用户身份
- [ThemeContext.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/contexts/ThemeContext.tsx): 6种主题色管理（紫罗兰/天空蓝/翡翠绿/珊瑚橙/玫瑰红/青柠绿），持久化到 localStorage，动态设置CSS变量

### 6.3 Hook

- [useStreamingChat.ts](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/hooks/useStreamingChat.ts): SSE流式聊天Hook：
  - 使用 `fetch + ReadableStream` 消费 SSE
  - `requestAnimationFrame` 节流渲染（~60ms），大幅减少Markdown解析开销
  - 支持停止生成（AbortController）
  - 返回 messages、isStreaming、streamingContent、sendMessage、stopStreaming 等

### 6.4 API 封装 ([api.ts](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/api.ts))

Axios 实例配置：
- baseURL: `/api`
- timeout: 180s（聊天请求300s）
- 请求拦截器：自动附加 `Bearer token`
- 响应拦截器：401自动跳转登录页
- 参数序列化：支持数组参数的多次传递

封装了以下API模块：
- `authApi`: login, register, getMe, changePassword
- `dashboardApi`: getStats
- `inventoryApi`: alerts, list, update, restock子模块（import/calculate/overview/search/export/sync等）
- `localInventoryApi`: import, summary, list, clear
- `reviewsApi`: list, detail, updateStatus, updateImportance, batchAnalyze, getNewCount
- `departmentsApi`: list, CRUD, members, users, batchAssign
- `notificationsApi`: list, unreadCount, markRead, markAllRead
- `chatApi`: send, sessions, messages, delete
- `chatStreamApi`: sendMessage(SSE), searchSessions, exportSession
- `storesApi`: list, CRUD, batchUpdateDepartment
- `productsApi`: list, CRUD
- `tenantsApi`: list, get, update
- `businessSettingsApi`: get, list, update, reset

### 6.5 组件

- [MainLayout.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/components/Layout/MainLayout.tsx): 主导航布局
  - 左侧可折叠 Sider，菜单项根据角色(admin/普通用户)动态显示
  - 顶部 Header：页面标题、主题切换、通知中心（Popover+Badge）、用户菜单
  - 通知中心：轮询未读数量（60s间隔）、通知列表、标记已读、详情弹窗
- [ProtectedRoute.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/components/ProtectedRoute.tsx): 路由守卫，未认证跳转登录
- [MarkdownRenderer.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/components/common/MarkdownRenderer.tsx): 轻量Markdown渲染器（标题/列表/粗体）
- [ChangePasswordModal.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/components/ChangePasswordModal.tsx): 修改密码弹窗
- [ThemeSwitcher](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/components/ThemeSwitcher/index.tsx): 主题色切换下拉

---

## 7. 数据库 Schema 与迁移

### [schema.sql](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/database/schema.sql)
完整的数据库表结构定义，包含以下部分：
- 通用基础表：tenants, users, stores, departments, user_departments, notifications
- 库存相关：products, inventory_records, inventory_alerts, inventory_actions
- 评论相关：reviews, review_analyses, review_handlings
- 系统相关：scheduled_tasks, task_execution_logs, audit_logs, system_configs, notifications

重要设计特点：
- 统一使用 `utf8mb4_unicode_ci` 字符集
- `InnoDB` 引擎，支持事务和外键
- 软删除字段 `deleted_at`（除分析/操作/审计等日志表外）
- `GENERATED ALWAYS AS ... STORED` 字段：`inventory_records.days_remaining`, `reviews.is_negative`, `notifications.is_read`
- 唯一约束：`uk_tenant_asin`(products), `uk_platform_review_id`(reviews), `uk_product_date`(inventory_records), `uk_user_dept`(user_departments)

### migrations/ 目录

| 脚本 | 说明 |
|------|------|
| [v1_add_departments_notifications.sql](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/database/migrations/v1_add_departments_notifications.sql) | v1迁移：创建departments/user_departments表，stores加department_id，reviews加importance_level，创建notifications表 |
| [v1_simple.sql](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/database/migrations/v1_simple.sql) | v1简化版本 |
| [v2_add_store_inventory_name.sql](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/database/migrations/v2_add_store_inventory_name.sql) | v2迁移：stores加inventory_name字段 |
| [migrate.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/database/migrations/migrate.py) | Python脚本自动化执行迁移 |
| [run_migration.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/database/migrations/run_migration.py) | 迁移运行入口 |
| [check_db.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/database/migrations/check_db.py) | 数据库结构检查 |
| [rollback_*.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/database/migrations/rollback_*.py) | 回滚迁移 |

---

## 8. 脚本工具

`backend/scripts/` 目录包含大量实用脚本：

### 初始化与检查
- `init_data.py`: 初始化数据库种子数据
- `check_*.py`: 数据一致性检查脚本

### 数据修复
- `fix_*.py`: 修复数据问题（如店铺名、重要性等级）
- `add_*.py` / `fill_*.py`: 添加或填充缺失数据

### 分析报告
- `analyze_*.py`: 数据分析脚本
- `reports/` 目录：JSON格式的分析报告文件（店铺映射分析、库存匹配分析、修复计划等）

### 备份与回滚
- `backups/` 目录：店铺数据备份JSON
- `rollback_*.py`: 数据回滚脚本

### 测试
- `test_*.py`: 独立测试脚本

---

## 9. 项目运行方式

### 环境要求
- Python 3.10+
- Node.js 18+
- MySQL 8.0+

### 后端启动

```bash
# 1. 安装依赖
cd backend
pip install -r requirements.txt

# 2. 配置环境变量（创建 .env 文件）
DATABASE_URL=mysql+pymysql://user:password@localhost:3306/aibxhs?charset=utf8mb4
SECRET_KEY=your-secret-key
AI_API_KEY=your-ai-api-key
AI_BASE_URL=https://your-ai-service.com/v1
AI_MODEL=qwen-turbo

# 3. 初始化数据库
python -c "from database.database import init_db; init_db()"

# 4. 启动服务器
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 前端启动

```bash
# 1. 安装依赖
cd frontend
npm install

# 2. 配置代理（vite.config.ts 中配置后端代理）
# 3. 启动开发服务器
npm run dev

# 4. 构建生产版本
npm run build
```

### 开发流程

1. **后端添加新功能**：创建 Model → 创建 Service → 创建 Router → 注册到 main.py
2. **前端添加新页面**：创建 Page 组件 → 添加 API 函数 → 配置路由 → 添加菜单项
3. **数据库迁移**：创建迁移 SQL → 使用 migrate.py 执行

---

## 10. 开发指南

### 如何添加新的数据库模型

1. 在 `backend/models/` 下创建新文件，定义 SQLAlchemy ORM 类
2. 继承 `BaseModel`（自动获得 created_at/updated_at/deleted_at）
3. 在 `schema.sql` 中添加对应的 CREATE TABLE 语句
4. 在新模型文件被导入时，会自动注册到 SQLAlchemy metadata

### 如何添加新的 API 路由

1. 在 `backend/routers/` 下创建新文件，使用 `APIRouter(prefix="/api/xxx")`
2. 实现端点函数，使用 `Depends(get_db)` 和 `Depends(get_current_user)`
3. 在 [main.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/main.py) 中 `app.include_router()`
4. 在 `frontend/src/api.ts` 中添加对应的 API 函数

### 如何添加新的前端页面

1. 在 `frontend/src/pages/` 下创建新组件
2. 在 [App.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/App.tsx) 中添加路由（使用 ProtectedRoute 包裹）
3. 在 [MainLayout.tsx](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/frontend/src/components/Layout/MainLayout.tsx) 的 menuItems 中添加菜单项
4. 在 `api.ts` 中添加对应的 API 调用函数

### 安全开发规范

项目内置了完整的[安全工具模块](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/utils/security.py)：
- 输入验证（SQL注入/XSS/路径遍历/命令注入检测）
- 输入清理（HTML转义、危险字符移除）
- 速率限制（内存中的RateLimiter）
- 数据脱敏（手机/邮箱/身份证/银行卡等）
- 安全令牌生成

### 店铺库存映射机制

店铺与库存数据的匹配通过 [store_mapping.py](file:///c:/Users/Administrator/Desktop/AI/AIBXHS/backend/utils/store_mapping.py) 实现：

1. 映射规则：`(数据库店铺名, 站点) → 库存店铺名`
2. 例如 `("云南金顺公司", "US") → "JeVenis-US"`
3. 支持精确匹配和模糊匹配
4. `inventory_name` 字段用于存储库存匹配名，自动更新

### 日均销量计算机制

日均销量通过可配置的权重公式计算，存储在 `business_settings` 表中：

```python
daily_sales = daily_avg_3d * w1 + daily_avg_7d * w2 + ...
```

默认权重：7d=0.2, 14d=0.2, 30d=0.2, 60d=0.2, 90d=0.2, 3d=0.0
用户可在"业务设置"页面自定义权重。