
<map>
  <node ID="root" TEXT="广告机器人">
    <node TEXT="数据来源（5个Excel文件）" ID="data_source" STYLE="bubble" POSITION="right">
      <node TEXT="广告活动数据.xlsx" ID="f_campaign" STYLE="fork">
        <node TEXT="维度：广告活动" ID="f_campaign_dim" STYLE="fork"/>
        <node TEXT="核心字段：广告活动/广告类型/预算/预算使用比例/广告花费/广告花费占比/曝光/点击/CPC/广告订单/广告销量/广告销售额/CVR/ACOS/CPO/竞价策略/广告组合/listing浏览量/搜索结果首页首位(IS)" ID="f_campaign_fields" STYLE="fork"/>
      </node>
      <node TEXT="关键词数据.xlsx" ID="f_keyword" STYLE="fork">
        <node TEXT="维度：关键词" ID="f_keyword_dim" STYLE="fork"/>
        <node TEXT="核心字段：关键词/匹配类型/状态/服务状态/竞价/建议竞价/基准值/广告活动/ABA日排名/广告花费/曝光/搜索结果首页首位(IS)/点击/CTR/CPC/广告订单/广告销量/广告销售额/CVR/ACOS/ROAS/CPO/广告花费占比" ID="f_keyword_fields" STYLE="fork"/>
      </node>
      <node TEXT="搜索词数据.xlsx" ID="f_search" STYLE="fork">
        <node TEXT="维度：用户搜索词" ID="f_search_dim" STYLE="fork"/>
        <node TEXT="核心字段：搜索词/搜索词来源/匹配类型/广告活动/ABA日排名/广告花费/曝光/点击/CTR/CPC/广告订单/广告销量/广告销售额/CVR/ACOS/ROAS/CPO/广告花费占比" ID="f_search_fields" STYLE="fork"/>
      </node>
      <node TEXT="商品投放数据.xlsx" ID="f_target" STYLE="fork">
        <node TEXT="维度：商品投放（ASIN定位）" ID="f_target_dim" STYLE="fork"/>
        <node TEXT="核心字段：商品投放/状态/服务状态/竞价/建议竞价/基准值/广告活动/广告花费/曝光/搜索结果首页首位(IS)/点击/CTR/CPC/广告订单/广告销量/广告销售额/CVR/ACOS/ROAS/CPO/广告花费占比" ID="f_target_fields" STYLE="fork"/>
      </node>
      <node TEXT="广告数据.xlsx" ID="f_ad" STYLE="fork">
        <node TEXT="维度：广告（SKU/ASIN层级）" ID="f_ad_dim" STYLE="fork"/>
        <node TEXT="核心字段：广告/状态/服务状态/SPV视频/价格/评分/评分数/广告类型/广告活动/FBM可售/FBA可用库存/广告花费/曝光/点击/CTR/CPC/广告订单/广告销量/广告销售额/CVR/ACOS/ROAS/CPO/广告花费占比" ID="f_ad_fields" STYLE="fork"/>
      </node>
      <node TEXT="通用格式说明" ID="data_format" STYLE="fork">
        <node TEXT="站点/店铺格式：US/A（国家/店铺，需拆分）" ID="fmt_store" STYLE="fork"/>
        <node TEXT="广告类型格式：SP自动/SP手动/SB/SD（需解析类型+投放方式）" ID="fmt_type" STYLE="fork"/>
        <node TEXT="预算格式：$45.00 每日（需提取数值）" ID="fmt_budget" STYLE="fork"/>
        <node TEXT="建议竞价格式：$0.36 ($0.3-$0.45)（需提取建议值+范围）" ID="fmt_bid" STYLE="fork"/>
        <node TEXT="特殊值：ACOS=∞ / ABA日排名=-- / listing浏览量=--（需转null）" ID="fmt_special" STYLE="fork"/>
        <node TEXT="日期：Excel无日期列，需从文件名或导入参数获取" ID="fmt_date" STYLE="fork"/>
      </node>
    </node>
    <node TEXT="数据存储" ID="storage" STYLE="bubble" POSITION="right">
      <node TEXT="统一快照表 ad_report_snapshots" ID="snap_table" STYLE="fork">
        <node TEXT="所有报告类型统一存储" ID="snap_unified" STYLE="fork"/>
        <node TEXT="report_type 区分：campaign/keyword/search_term/product" ID="snap_type" STYLE="fork"/>
        <node TEXT="按 (tenant_id, date, report_type, campaign_name, keyword/search_term/asin) UPSERT" ID="snap_upsert" STYLE="fork"/>
        <node TEXT="支持历史数据（按 date 保留每天快照）" ID="snap_history" STYLE="fork"/>
      </node>
      <node TEXT="4个日度分表（可选，用于规则引擎和健康分）" ID="daily_tables" STYLE="fork">
        <node TEXT="ad_campaign_daily" ID="daily_campaign" STYLE="fork"/>
        <node TEXT="ad_keyword_daily" ID="daily_keyword" STYLE="fork"/>
        <node TEXT="ad_search_term_daily" ID="daily_search" STYLE="fork"/>
        <node TEXT="ad_product_daily" ID="daily_product" STYLE="fork"/>
      </node>
      <node TEXT="规则与建议表" ID="rule_tables" STYLE="fork">
        <node TEXT="ad_optimization_rules（自定义规则）" ID="rule_custom" STYLE="fork"/>
        <node TEXT="ad_optimization_suggestion（建议池，状态机：待处理→已确认→已执行/已忽略/已失效）" ID="rule_suggestion" STYLE="fork"/>
        <node TEXT="ad_execution_log（执行日志）" ID="rule_log" STYLE="fork"/>
      </node>
    </node>
    <node TEXT="广告管理" ID="ad_mgmt" STYLE="bubble" POSITION="right">
      <node TEXT="广告概览统计" ID="overview" STYLE="fork">
        <node TEXT="8个核心指标卡片：总花费/总销售额/总订单/总点击/ACOS/ROAS/CTR/CPC" ID="overview_metrics" STYLE="fork"/>
        <node TEXT="健康分卡片：100分制 6维度（ACOS/ROAS/CTR/CVR/预算利用率/CPC），4等级（优秀/良好/一般/差）" ID="overview_health" STYLE="fork"/>
        <node TEXT="7天趋势表格" ID="overview_trend" STYLE="fork"/>
      </node>
      <node TEXT="广告活动视图" ID="view_campaign" STYLE="fork">
        <node TEXT="活动列表表格：Campaign/预算/预算使用比例/花费/花费占比/ACOS/ROAS/CTR/CPC/CVR/订单/销售额/健康分" ID="view_campaign_table" STYLE="fork"/>
        <node TEXT="健康分等级筛选：优秀/良好/一般/差" ID="view_campaign_filter_health" STYLE="fork"/>
        <node TEXT="按ACOS/ROAS/花费排序" ID="view_campaign_sort" STYLE="fork"/>
        <node TEXT="活动分析：识别高花费低效活动、预算不足活动" ID="view_campaign_analysis" STYLE="fork"/>
      </node>
      <node TEXT="关键词视图" ID="view_keyword" STYLE="fork">
        <node TEXT="关键词列表：关键词/匹配类型/竞价/建议竞价/ABA排名/花费/ACOS/ROAS/订单/CTR/CPC" ID="view_keyword_table" STYLE="fork"/>
        <node TEXT="关键词分级：高转化词/烧钱词/潜力词/普通词" ID="view_keyword_grade" STYLE="fork"/>
        <node TEXT="ACOS范围筛选 + 匹配类型筛选" ID="view_keyword_filter" STYLE="fork"/>
        <node TEXT="关联规则建议标记：显示对应规则触发的建议标签" ID="view_keyword_suggestion" STYLE="fork"/>
      </node>
      <node TEXT="搜索词视图" ID="view_search" STYLE="fork">
        <node TEXT="搜索词列表：搜索词/搜索词来源/匹配类型/广告活动/ABA排名/花费/ACOS/ROAS/订单" ID="view_search_table" STYLE="fork"/>
        <node TEXT="否定关键词建议（烧钱不转化词）" ID="view_search_negative" STYLE="fork"/>
        <node TEXT="新关键词挖掘建议（高转化搜索词）" ID="view_search_mining" STYLE="fork"/>
      </node>
      <node TEXT="商品视图" ID="view_product" STYLE="fork">
        <node TEXT="商品列表：广告(ASIN/SKU)/价格/评分/评分数/FBA库存/广告类型/广告活动/花费/ACOS/ROAS/订单/CTR" ID="view_product_table" STYLE="fork"/>
        <node TEXT="商品投放列表：商品投放/竞价/建议竞价/广告活动/花费/ACOS/ROAS/订单" ID="view_target_table" STYLE="fork"/>
      </node>
      <node TEXT="优化建议管理" ID="suggestion_mgmt" STYLE="fork">
        <node TEXT="状态Tab：待处理/已确认/已执行/已忽略/已失效" ID="suggestion_tab" STYLE="fork"/>
        <node TEXT="详情Drawer：完整建议信息 + AI分析" ID="suggestion_drawer" STYLE="fork"/>
        <node TEXT="状态流转：待处理→已确认→已执行（写执行日志）/已忽略/已失效" ID="suggestion_flow" STYLE="fork"/>
        <node TEXT="手动触发规则引擎重新生成建议" ID="suggestion_run" STYLE="fork"/>
      </node>
      <node TEXT="规则配置" ID="rule_config" STYLE="fork">
        <node TEXT="7条预定义规则：预算未花完/ACOS过高/ROAS过低/CTR过低/CPC过高/CVR过低/预算利用率低" ID="rule_predefined" STYLE="fork"/>
        <node TEXT="阈值编辑（可保存）" ID="rule_threshold" STYLE="fork"/>
        <node TEXT="启用/禁用开关（可保存）" ID="rule_switch" STYLE="fork"/>
        <node TEXT="自定义规则创建（条件+动作）" ID="rule_custom_create" STYLE="fork"/>
        <node TEXT="手动执行规则" ID="rule_execute" STYLE="fork"/>
      </node>
      <node TEXT="执行日志" ID="exec_log" STYLE="fork">
        <node TEXT="日志列表：规则名/动作/目标/状态/执行人/执行时间" ID="exec_log_list" STYLE="fork"/>
        <node TEXT="状态筛选：成功/失败" ID="exec_log_filter" STYLE="fork"/>
      </node>
      <node TEXT="多维度搜索（活动/关键词/搜索词/商品）" ID="search" STYLE="fork"/>
      <node TEXT="导出Excel报告" ID="export" STYLE="fork"/>
      <node TEXT="筛选选项（国家-店铺联动）" ID="filter" STYLE="fork"/>
    </node>
    <node TEXT="AI集成" ID="ai" STYLE="bubble" POSITION="right">
      <node TEXT="查询广告表现" ID="ai_query" STYLE="fork">
        <node TEXT="自然语言查询：最近7天哪个活动表现最好？" ID="ai_query_nl" STYLE="fork"/>
        <node TEXT="数据来源：ad_report_snapshots 聚合" ID="ai_query_source" STYLE="fork"/>
        <node TEXT="返回：结构化指标 + AI解读" ID="ai_query_result" STYLE="fork"/>
      </node>
      <node TEXT="分析关键词效果" ID="ai_keyword" STYLE="fork">
        <node TEXT="关键词分级：高转化/烧钱/潜力/普通" ID="ai_keyword_grade" STYLE="fork"/>
        <node TEXT="识别问题词：高ACOS+高花费" ID="ai_keyword_problem" STYLE="fork"/>
        <node TEXT="返回：分级列表 + AI诊断" ID="ai_keyword_result" STYLE="fork"/>
      </node>
      <node TEXT="生成优化建议" ID="ai_suggest" STYLE="fork">
        <node TEXT="降低竞价建议（高ACOS关键词）" ID="ai_suggest_bid" STYLE="fork"/>
        <node TEXT="提高预算建议（高ROAS活动）" ID="ai_suggest_budget" STYLE="fork"/>
        <node TEXT="否定关键词建议（烧钱不转化词）" ID="ai_suggest_negative" STYLE="fork"/>
        <node TEXT="新关键词挖掘建议（高转化搜索词）" ID="ai_suggest_mining" STYLE="fork"/>
        <node TEXT="AI仅做数据解读和问题诊断，不生成操作决策" ID="ai_suggest_scope" STYLE="fork"/>
      </node>
    </node>
    <node TEXT="数据导入流程" ID="import_flow" STYLE="bubble" POSITION="right">
      <node TEXT="Excel导入（前端上传）" ID="import_excel" STYLE="fork">
        <node TEXT="POST /api/ads/import（鉴权+tenant_id）" ID="import_api" STYLE="fork"/>
        <node TEXT="支持5种报告文件自动识别" ID="import_detect" STYLE="fork"/>
        <node TEXT="字段映射：中文表头→模型字段" ID="import_mapping" STYLE="fork"/>
        <node TEXT="值解析：站点拆分/预算解析/广告类型解析/特殊值处理" ID="import_parse" STYLE="fork"/>
        <node TEXT="日期处理：从文件名或导入参数获取" ID="import_date" STYLE="fork"/>
        <node TEXT="UPSERT：按唯一键更新或插入" ID="import_upsert" STYLE="fork"/>
        <node TEXT="导入后自动触发规则引擎生成建议" ID="import_trigger" STYLE="fork"/>
      </node>
      <node TEXT="影刀RPA导入" ID="import_rpa" STYLE="fork">
        <node TEXT="POST /api/ads/sync/rpa（鉴权+tenant_id）" ID="import_rpa_api" STYLE="fork"/>
        <node TEXT="影刀先调用 /api/auth/login 获取token" ID="import_rpa_auth" STYLE="fork"/>
        <node TEXT="影刀从星拓ERP下载报表后推送" ID="import_rpa_source" STYLE="fork"/>
        <node TEXT="支持4种数据批量upsert（campaigns/keywords/search_terms/products）" ID="import_rpa_batch" STYLE="fork"/>
      </node>
      <node TEXT="导入状态查询" ID="import_status" STYLE="fork">
        <node TEXT="GET /api/ads/import-status" ID="import_status_api" STYLE="fork"/>
        <node TEXT="返回：进度/步骤/结果/错误" ID="import_status_result" STYLE="fork"/>
      </node>
    </node>
    <node TEXT="前端路由与权限" ID="frontend" STYLE="bubble" POSITION="right">
      <node TEXT="路由：/ads → AdBotPage" ID="fe_route" STYLE="fork"/>
      <node TEXT="8个Tab：概览/活动/关键词/搜索词/商品/建议/规则/日志" ID="fe_tabs" STYLE="fork"/>
      <node TEXT="权限：robot:ad:view（查看）+ robot:ad:manage（管理）" ID="fe_perm" STYLE="fork"/>
      <node TEXT="筛选：国家-店铺联动 + 日期范围 + 报告类型" ID="fe_filter" STYLE="fork"/>
    </node>
  </node>
</map>
