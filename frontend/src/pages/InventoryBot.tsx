import React, {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
} from "react";
import {
  Card,
  Row,
  Col,
  Table,
  Tag,
  Statistic,
  Button,
  Input,
  InputNumber,
  Select,
  Space,
  Modal,
  Empty,
  Spin,
  message,
  Tooltip,
  Upload,
  Popconfirm,
  Popover,
  Checkbox,
  Divider,
  Pagination,
  Radio,
  Image,
} from "antd";
import type { TablePaginationConfig, ColumnsType } from "antd/es/table";
import type { UploadProps } from "antd";
import {
  Package,
  Image as ImageIcon,
  AlertTriangle,
  AlertCircle,
  CheckCircle,
  Search,
  Truck,
  BarChart3,
  Upload as UploadIcon,
  ChevronDown,
  ChevronRight,
  TrendingUp,
  TrendingDown,
  Warehouse,
  Trash2,
  Download,
  RefreshCw,
  Columns,
  Pin,
  PinOff,
  Lock,
} from "lucide-react";
import { UploadOutlined, DownloadOutlined } from "@ant-design/icons";
import { inventoryApi, localInventoryApi, tagsApi } from "../api";
import { useTheme } from "../contexts/ThemeContext";
import { useAuth } from "../contexts/AuthContext";
import { useResponsive } from "../hooks/useResponsive";

// ==================== TypeScript Interfaces ====================

interface OverviewData {
  total_sku: number;
  red_count: number;
  yellow_count: number;
  green_count: number;
  snapshot_date: string;
  latest_updated_at?: string | null;
  stockout_top10: StockoutItem[];
  overstock_top10: OverstockItem[];
}

interface StockoutItem {
  asin: string;
  product_name: string;
  account: string;
  country: string;
  days_of_supply: number;
  fba_stock: number;
  daily_sales: number;
  sales_7d?: number;
  sales_14d?: number;
  daily_avg_7d?: number;
  daily_avg_14d?: number;
  stockout_date: string;
  total_stock: number;
  suggest_qty?: number;
  reason?: string;
}

interface OverstockItem {
  asin: string;
  product_name: string;
  account: string;
  country: string;
  total_stock: number;
  age_12_plus: number;
  age_9_12: number;
  age_6_9: number;
  age_3_6: number;
}

interface InventoryItem {
  id: number;
  image_url?: string;
  /** 展开包装出的共享库存子行标记（构造 displayList 时动态附加） */
  _isChild?: boolean;
  asin: string;
  sku: string;
  fnsku: string;
  msku: string;
  product_name: string;
  account: string;
  country: string;
  fba_stock: number;
  fba_available: number;
  fba_pending_transfer: number;
  fba_in_transfer: number;
  fba_inbound_processing: number;
  fba_inbound: number;
  total_stock: number;
  daily_sales: number;
  sales_7d?: number;
  sales_14d?: number;
  days_of_supply: number;
  stockout_date: string | null;
  risk_level: string;
  replenishment_status: string;
  summary_flag: string;
  local_inventory?: number;
  /** 海外仓可售库存（飞书多维表同步，仅主行有值） */
  overseas_inventory?: number;
  /** 海外仓最大库龄（天） */
  overseas_max_age?: number | null;
  inspection_quantity?: number;
  suggest_qty?: number;
  replenishment_reason?: string;
  age_12_plus?: number;
  gross_margin?: number;
  return_rate_30d?: number;
  return_rate_60d?: number;
  is_holiday?: string;
  is_discontinued?: boolean;
  force_replenish?: number;
  filter_status?: string;
  tag_locked?: number;
}

interface InboundDetail {
  shipment_id: string;
  quantity: number;
  transport_method: string;
  ship_date: string | null;
  estimated_available_date: string | null;
  estimated_arrival_date: string | null;
}

interface LocalInventorySummary {
  total_sku: number;
  total_quantity: number;
  latest_batch_date: string | null;
}

/** 海外仓同步结果汇总（/restock/overseas-summary） */
interface OverseasInventorySummary {
  total_sku: number;
  total_qty: number;
  latest_record_date: string | null;
  last_synced_at: string | null;
}

// ==================== Helper Functions ====================

const getDaysSupplyColor = (days: number): string => {
  if (days <= 30) return "#cf1322";
  if (days <= 60) return "#fa8c16";
  return "#52c41a";
};

const getDaysSupplyTag = (days: number) => {
  if (days <= 30) return <Tag color="red">{days}天</Tag>;
  if (days <= 60) return <Tag color="orange">{days}天</Tag>;
  return <Tag color="green">{days}天</Tag>;
};

const getRiskLevelTag = (level: string) => {
  if (level === "red" || level === "红色")
    return <Tag color="red">断货风险</Tag>;
  if (level === "yellow" || level === "黄色")
    return <Tag color="orange">库存预警</Tag>;
  if (level === "green" || level === "绿色")
    return <Tag color="green">库存正常</Tag>;
  return <Tag>{level}</Tag>;
};

const formatNumber = (num: number | null | undefined): string => {
  if (num === null || num === undefined) return "-";
  return num.toLocaleString();
};

const truncateText = (text: string, maxLen: number): string => {
  if (!text) return "-";
  return text.length > maxLen ? text.substring(0, maxLen) + "..." : text;
};

// ==================== 国家 / 大区（区域）工具 ====================
// 国家代码 -> 中文名
const SITE_CODE_TO_COUNTRY: Record<string, string> = {
  US: "美国",
  USA: "美国",
  UK: "英国",
  GB: "英国",
  DE: "德国",
  FR: "法国",
  IT: "意大利",
  ES: "西班牙",
  JP: "日本",
  CA: "加拿大",
  MX: "墨西哥",
  AU: "澳大利亚",
  NL: "荷兰",
  SE: "瑞典",
  PL: "波兰",
  BE: "比利时",
  IE: "爱尔兰",
  AT: "奥地利",
  TR: "土耳其",
  SG: "新加坡",
  AE: "阿联酋",
  SA: "沙特阿拉伯",
  EG: "埃及",
  IN: "印度",
  BR: "巴西",
};

// 中文国家名 -> 大区
export const COUNTRY_TO_CONTINENT: Record<string, string> = {
  英国: "欧洲",
  德国: "欧洲",
  法国: "欧洲",
  意大利: "欧洲",
  西班牙: "欧洲",
  荷兰: "欧洲",
  瑞典: "欧洲",
  波兰: "欧洲",
  比利时: "欧洲",
  爱尔兰: "欧洲",
  奥地利: "欧洲",
  土耳其: "欧洲",
  美国: "北美洲",
  加拿大: "北美洲",
  墨西哥: "北美洲",
  日本: "亚洲",
  新加坡: "亚洲",
  印度: "亚洲",
  阿联酋: "亚洲",
  巴西: "中南美洲",
  澳大利亚: "大洋洲",
  沙特阿拉伯: "中东",
  埃及: "中东",
};

// 中文国家名 -> 代码（供店铺标签匹配）
const COUNTRY_TO_CODE: Record<string, string> = {
  美国: "US",
  英国: "UK",
  德国: "DE",
  法国: "FR",
  意大利: "IT",
  西班牙: "ES",
  日本: "JP",
  加拿大: "CA",
  墨西哥: "MX",
  澳大利亚: "AU",
  荷兰: "NL",
  瑞典: "SE",
  波兰: "PL",
  比利时: "BE",
  爱尔兰: "IE",
  奥地利: "AT",
  土耳其: "TR",
  新加坡: "SG",
  阿联酋: "AE",
  沙特阿拉伯: "SA",
  埃及: "EG",
  印度: "IN",
  巴西: "BR",
};

const CONTINENT_ORDER = [
  "欧洲",
  "北美洲",
  "亚洲",
  "中南美洲",
  "大洋洲",
  "中东",
];

// 根据中文国家名获取大区，未知国家归入"其他"
const getContinent = (country: string): string => {
  if (!country) return "其他";
  for (const key of Object.keys(COUNTRY_TO_CONTINENT)) {
    if (country.includes(key)) return COUNTRY_TO_CONTINENT[key];
  }
  return "其他";
};

// 将扁平 options [{value,label}] 按大区分组，返回 [{label, items}] 结构，
// 供级联筛选面板（左大区 / 右成员）直接使用；无需再塞「X（全选）」选项，
// 「整合欧洲」等需求由面板内「选择本组」勾选实现。
const buildGroupedOptions = (
  options: { value: string; label: string }[],
  regionOf: (value: string) => string,
): { label: string; items: { value: string; label: string }[] }[] => {
  const groups: Record<string, { value: string; label: string }[]> = {};
  for (const opt of options) {
    const region = regionOf(opt.value);
    (groups[region] ||= []).push(opt);
  }
  const result: { label: string; items: { value: string; label: string }[] }[] =
    [];
  for (const continent of CONTINENT_ORDER) {
    if (groups[continent] && groups[continent].length > 0) {
      result.push({ label: continent, items: groups[continent] });
      delete groups[continent];
    }
  }
  const leftovers = Object.values(groups).flat();
  if (leftovers.length > 0) {
    result.push({ label: "其他", items: leftovers });
  }
  return result;
};

// 根据国家名或国家代码判断店铺所属大区（店铺标签形如 "品牌-US" / "品牌-美国" / "品牌"）
const getStoreContinentFromLabel = (label: string): string => {
  for (const [code, country] of Object.entries(SITE_CODE_TO_COUNTRY)) {
    if (label.includes(`-${code}`) || label.includes(`-${country}`)) {
      return getContinent(country);
    }
  }
  // 尝试直接匹配中文国家名
  for (const country of Object.keys(COUNTRY_TO_CONTINENT)) {
    if (label.includes(country)) return COUNTRY_TO_CONTINENT[country];
  }
  return "其他";
};

// 表格"国家"列规范化：代码->中文；合并值（顿号）逐项映射后保留
const normalizeCountry = (value: string): string => {
  if (!value) return "-";
  return value
    .split("、")
    .map((c) => {
      const trimmed = c.trim();
      if (!trimmed) return trimmed;
      return SITE_CODE_TO_COUNTRY[trimmed.toUpperCase()] || trimmed;
    })
    .join("、");
};

// ==================== 级联筛选面板（领星风格） ====================
// 左：大区列表；右：该大区成员；顶部搜索、底部 取消/确定。
// 大区行可一次性勾选「选择本组」，从而天然实现「整合欧洲/北美洲…」，
// 下拉里不再堆砌「欧洲（全选）」这类冗余选项。

interface CascaderGroup {
  label: string;
  items: { value: string; label: string }[];
}

interface CascadingFilterProps {
  title: string;
  placeholder?: string;
  value: string[];
  groups: CascaderGroup[];
  onChange: (values: string[]) => void;
  width?: number;
}

const CascadingFilter: React.FC<CascadingFilterProps> = ({
  title,
  placeholder = "请选择",
  value,
  groups,
  onChange,
  width = 180,
}) => {
  const [open, setOpen] = useState(false);
  const [activeGroup, setActiveGroup] = useState<string>("");
  const [draft, setDraft] = useState<string[]>(value);
  const [keyword, setKeyword] = useState("");

  const handleOpenChange = (next: boolean) => {
    if (next) {
      setDraft([...value]);
      setKeyword("");
      const first =
        groups.find((g) => g.items.length > 0)?.label ?? groups[0]?.label ?? "";
      setActiveGroup((prev) =>
        prev && groups.some((g) => g.label === prev) ? prev : first,
      );
    }
    setOpen(next);
  };

  const currentGroup = groups.find((g) => g.label === activeGroup) ?? groups[0];

  const kw = keyword.trim().toLowerCase();
  const filteredItems = (currentGroup?.items ?? []).filter((it) =>
    it.label.toLowerCase().includes(kw),
  );
  const groupAllChecked =
    (currentGroup?.items.length ?? 0) > 0 &&
    (currentGroup?.items ?? []).every((it) => draft.includes(it.value));
  const groupIndeterminate =
    !groupAllChecked &&
    (currentGroup?.items ?? []).some((it) => draft.includes(it.value));

  const toggleItem = (v: string, checked: boolean) => {
    setDraft((prev) =>
      checked ? [...new Set([...prev, v])] : prev.filter((x) => x !== v),
    );
  };
  const toggleGroup = (checked: boolean) => {
    if (!currentGroup) return;
    setDraft((prev) => {
      const set = new Set(prev);
      if (checked) currentGroup.items.forEach((it) => set.add(it.value));
      else currentGroup.items.forEach((it) => set.delete(it.value));
      return [...set];
    });
  };

  const handleConfirm = () => {
    onChange(draft);
    setOpen(false);
  };

  const selectedCount = value.length;

  const content = (
    <div className="inv-cascader-pop">
      <div className="inv-cascader-left">
        {groups.map((g) => (
          <div
            key={g.label}
            className={`inv-cascader-group${g.label === activeGroup ? " active" : ""}`}
            onClick={() => {
              setActiveGroup(g.label);
              setKeyword("");
            }}
          >
            <span className="inv-cascader-group-name">{g.label}</span>
            <span className="inv-cascader-group-count">{g.items.length}</span>
          </div>
        ))}
      </div>
      <div className="inv-cascader-right">
        <Input
          size="small"
          allowClear
          prefix={<Search size={13} color="#bbb" />}
          placeholder={`搜索${title}`}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
        <div className="inv-cascader-selectall">
          <Checkbox
            checked={groupAllChecked}
            indeterminate={groupIndeterminate}
            onChange={(e) => toggleGroup(e.target.checked)}
          >
            选择{activeGroup || "全部"}
          </Checkbox>
        </div>
        <div className="inv-cascader-items">
          {filteredItems.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="无匹配项"
              style={{ marginTop: 20, marginBottom: 8 }}
            />
          ) : (
            filteredItems.map((it) => (
              <div key={it.value} className="inv-cascader-item">
                <Checkbox
                  checked={draft.includes(it.value)}
                  onChange={(e) => toggleItem(it.value, e.target.checked)}
                >
                  {it.label}
                </Checkbox>
              </div>
            ))
          )}
        </div>
      </div>
      <div className="inv-cascader-footer">
        <Button size="small" onClick={() => setOpen(false)}>
          取消
        </Button>
        <Button size="small" type="primary" onClick={handleConfirm}>
          确定
        </Button>
      </div>
    </div>
  );

  return (
    <Popover
      content={content}
      trigger="click"
      open={open}
      onOpenChange={handleOpenChange}
      placement="bottomLeft"
      getPopupContainer={(trigger) => trigger.parentElement ?? document.body}
    >
      <Button
        className={`inv-cascader-trigger${selectedCount > 0 ? " has-value" : ""}`}
        style={{ width }}
      >
        <span className="inv-cascader-trigger-text">
          {selectedCount > 0 ? `${title}（${selectedCount}）` : placeholder}
        </span>
        <ChevronDown size={14} color="#999" />
      </Button>
    </Popover>
  );
};

// ==================== Component ====================

const InventoryBot: React.FC = () => {
  const { currentTheme } = useTheme();
  const { hasPermission } = useAuth();
  const res = useResponsive();
  const [messageApi, contextHolder] = message.useMessage();

  // --- Loading states ---
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [tableLoading, setTableLoading] = useState(false);
  const [importLoading, setImportLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [syncingFeishu, setSyncingFeishu] = useState(false);
  const [syncButtonLabel, setSyncButtonLabel] = useState("同步FBA在途");
  const [syncProgress, setSyncProgress] = useState(0);
  const [syncStep, setSyncStep] = useState("");
  // --- 海外仓同步状态（飞书多维表 → 主行） ---
  const [syncingOverseas, setSyncingOverseas] = useState(false);
  const [overseasButtonLabel, setOverseasButtonLabel] = useState("同步海外仓");
  const overseasPollingRef = useRef(false); // 是否已有一条轮询链在跑，避免并发轮询
  const overseasMountedRef = useRef(true); // 组件是否仍然挂载，卸载后停止轮询

  // --- 补货计算状态（跨页面持久化） ---
  const [calculating, setCalculating] = useState(false);
  const [calcButtonLabel, setCalcButtonLabel] = useState("重新计算");

  // --- Export states ---
  const [exportModalVisible, setExportModalVisible] = useState(false);
  const [selectedExportFields, setSelectedExportFields] = useState<string[]>([
    "asin",
    "sku",
    "product_name",
    "account",
    "country",
    "fba_stock",
    "fba_inbound",
    "total_stock",
    "daily_sales",
    "days_of_supply",
    "risk_level",
    "suggest_qty",
  ]);
  // 导出字段选项
  const exportFieldOptions = [
    { label: "ASIN", value: "asin" },
    { label: "SKU", value: "sku" },
    { label: "FNSKU", value: "fnsku" },
    { label: "MSKU", value: "msku" },
    { label: "品名", value: "product_name" },
    { label: "店铺", value: "account" },
    { label: "国家", value: "country" },
    { label: "分类", value: "category" },
    { label: "品牌", value: "brand" },
    { label: "FBA库存", value: "fba_stock" },
    { label: "可售", value: "fba_available" },
    { label: "待调仓", value: "fba_pending_transfer" },
    { label: "FBA预留", value: "fba_in_transfer" },
    { label: "入库中", value: "fba_inbound_processing" },
    { label: "在途", value: "fba_inbound" },
    { label: "查验货件数量", value: "inspection_quantity" },
    { label: "本地仓库存", value: "local_inventory" },
    { label: "海外仓库存", value: "overseas_inventory" },
    { label: "总库存", value: "total_stock" },
    { label: "毛利率", value: "gross_margin" },
    { label: "30天退货率", value: "return_rate_30d" },
    { label: "60天退货率", value: "return_rate_60d" },
    { label: "日均销量", value: "daily_sales" },
    { label: "7天销量", value: "sales_7d" },
    { label: "14天销量", value: "sales_14d" },
    { label: "7日日均销量", value: "daily_avg_7d" },
    { label: "14日日均销量", value: "daily_avg_14d" },
    { label: "可售天数", value: "days_of_supply" },
    { label: "断货时间", value: "stockout_date" },
    { label: "风险等级", value: "risk_level" },
    { label: "建议补货数量", value: "suggest_qty" },
    { label: "补货原因", value: "replenishment_reason" },
    { label: "补货状态", value: "replenishment_status" },
    { label: "节日类型", value: "is_holiday" },
    { label: "停售", value: "is_discontinued" },
    { label: "强制补货", value: "force_replenish" },
    { label: "人工锁定", value: "tag_locked" },
    { label: "过滤状态", value: "filter_status" },
  ];

  // --- Data states ---
  const [overviewData, setOverviewData] = useState<OverviewData | null>(null);
  const [inventoryList, setInventoryList] = useState<InventoryItem[]>([]);
  const [total, setTotal] = useState(0);

  // --- Filter states ---
  const [searchText, setSearchText] = useState("");
  const [accountFilter, setAccountFilter] = useState<string[]>([]);
  const [countryFilter, setCountryFilter] = useState<string[]>([]);
  const [accountOptions, setAccountOptions] = useState<
    { value: string; label: string }[]
  >([]);
  const [countryOptions, setCountryOptions] = useState<
    { value: string; label: string }[]
  >([]);
  const [allAccountOptions, setAllAccountOptions] = useState<
    { value: string; label: string }[]
  >([]); // 保存所有店铺选项，用于国家切换时过滤

  // --- Pagination ---
  const [pagination, setPagination] = useState<TablePaginationConfig>({
    current: 1,
    pageSize: 20,
    showSizeChanger: true,
    showTotal: (t) => `共 ${t} 条`,
    pageSizeOptions: ["10", "20", "50", "100", "200"],
  });

  // --- Sorting ---
  const [sortField, setSortField] = useState<string | undefined>("suggest_qty");
  const [sortOrder, setSortOrder] = useState<string | undefined>("desc");

  // --- Table Filter State ---
  const [tableRiskFilter, setTableRiskFilter] = useState<string[] | undefined>(
    undefined,
  );
  const [holidayFilter, setHolidayFilter] =
    useState<string>("all");

  // --- Modal states ---
  const [inboundModalVisible, setInboundModalVisible] = useState(false);
  const [inboundDetails, setInboundDetails] = useState<InboundDetail[]>([]);
  const [inboundLoading, setInboundLoading] = useState(false);
  const [inboundAsin, setInboundAsin] = useState("");

  // --- Expanded rows for TOP10 lists ---
  const [expandedStockout, setExpandedStockout] = useState<string | null>(null);
  const [expandedOverstock, setExpandedOverstock] = useState<string | null>(
    null,
  );

  // --- Collapsed state for TOP10 cards ---
  const [stockoutCollapsed, setStockoutCollapsed] = useState(true);
  const [overstockCollapsed, setOverstockCollapsed] = useState(true);

  // --- Local inventory states ---
  const [localSummary, setLocalSummary] =
    useState<LocalInventorySummary | null>(null);
  const [localLoading, setLocalLoading] = useState(false);

  // --- Overseas inventory states（飞书海外仓同步结果，用于跨页面展示上次同步结果） ---
  const [overseasSummary, setOverseasSummary] =
    useState<OverseasInventorySummary | null>(null);

  // --- Inspection editing states ---
  const [editingInspectionId, setEditingInspectionId] = useState<number | null>(
    null,
  );
  const [editingInspectionVal, setEditingInspectionVal] = useState<number>(0);

  // --- Summary expandable states ---
  const [expandedParentIds, setExpandedParentIds] = useState<Set<number>>(
    new Set(),
  );
  const [childrenMap, setChildrenMap] = useState<
    Record<number, InventoryItem[]>
  >({});

  // --- Reduction import states ---
  const [reductionModalVisible, setReductionModalVisible] = useState(false);
  const [reductionCountry, setReductionCountry] = useState<string>("");
  const [reductionFile, setReductionFile] = useState<File | null>(null);
  const [reductionImporting, setReductionImporting] = useState(false);
  const [reductionResult, setReductionResult] = useState<{
    total: number;
    updated: number;
    skipped: number;
    snapshot_ids?: number[];
    result_file_id: string;
  } | null>(null);

  // --- Column settings states ---
  const COLUMN_META = useMemo(
    () => [
      { key: "image_url", label: "图片" },
      { key: "asin", label: "ASIN" },
      { key: "sku", label: "SKU" },
      { key: "product_name", label: "品名" },
      { key: "account", label: "店铺" },
      { key: "country", label: "国家" },
      { key: "fba_stock", label: "FBA库存" },
      { key: "fba_inbound", label: "在途" },
      { key: "inspection_quantity", label: "查验货件" },
      { key: "total_stock", label: "总库存" },
      { key: "gross_margin", label: "毛利率" },
      { key: "return_rate_30d", label: "30天退货率" },
      { key: "return_rate_60d", label: "60天退货率" },
      { key: "local_inventory", label: "本地仓" },
      { key: "overseas_inventory", label: "海外仓" },
      { key: "daily_sales", label: "日均销量" },
      { key: "sales_7d", label: "7天销量" },
      { key: "sales_14d", label: "14天销量" },
      { key: "daily_avg_7d", label: "7日日均" },
      { key: "daily_avg_14d", label: "14日日均" },
      { key: "days_of_supply", label: "可售天数" },
      { key: "stockout_date", label: "断货时间" },
      { key: "suggest_qty", label: "建议补货" },
      { key: "age_12_plus", label: "12月+库龄" },
      { key: "risk_level", label: "风险等级" },
      { key: "tag_holiday", label: "节日标签" },
      { key: "force_replenish", label: "强制补货" },
      { key: "tag_locked", label: "人工锁定" },
    ],
    [],
  );

  const defaultVisibility = useMemo(
    () => Object.fromEntries(COLUMN_META.map((col) => [col.key, true])),
    [COLUMN_META],
  );

  const [columnVisibility, setColumnVisibility] =
    useState<Record<string, boolean>>(defaultVisibility);
  const [columnSettingsOpen, setColumnSettingsOpen] = useState(false);
  const [frozenColumns, setFrozenColumns] = useState<Set<string>>(
    new Set(["_expand", "asin", "action"]),
  );

  // ==================== Data Fetching ====================

  const fetchOverview = useCallback(async (over?: {
    keyword?: string;
    holiday?: string;
    account?: string[];
    country?: string[];
  }) => {
    try {
      setOverviewLoading(true);
      // 概览与列表同口径：带上当前筛选，使面板「总SKU数」= 表格该筛选下的条数。
      // handler 里状态更新是异步的，故支持传 over 显式覆盖（用新值而非旧 state）。
      const kw = over?.keyword !== undefined ? over.keyword : searchText;
      const hf = over?.holiday !== undefined ? over.holiday : holidayFilter;
      const ac = over?.account !== undefined ? over.account : accountFilter;
      const co = over?.country !== undefined ? over.country : countryFilter;
      const params: any = {};
      if (kw) params.keyword = kw;
      if (hf) params.holiday_filter = hf;
      if (ac && ac.length > 0) params.account = ac;
      if (co && co.length > 0) params.country = co;
      const response = await inventoryApi.getOverview(params);
      const data = response.data?.data || response.data;
      if (data) {
        setOverviewData(data);
      }
    } catch (error) {
      console.error("获取概览数据失败:", error);
      messageApi.error("获取概览数据失败");
    } finally {
      setOverviewLoading(false);
    }
  }, [messageApi, searchText, holidayFilter, accountFilter, countryFilter]);

  const fetchLocalSummary = useCallback(async () => {
    try {
      const response = await localInventoryApi.getSummary();
      const data = response.data?.data || response.data;
      if (data) {
        setLocalSummary(data);
      }
    } catch (error) {
      console.error("获取本地仓库存汇总失败:", error);
    } finally {
      setLocalLoading(false);
    }
  }, []);

  const fetchOverseasSummary = useCallback(async () => {
    try {
      const response = await inventoryApi.getOverseasSummary();
      const data = response.data?.data || response.data;
      if (data) {
        setOverseasSummary(data);
      }
    } catch (error) {
      // 未配置海外仓表时接口可能不可用，静默降级，不打扰用户
      console.error("获取海外仓汇总失败:", error);
    }
  }, []);

  const fetchInventoryList = useCallback(
    async (
      page = 1,
      pageSize = 20,
      search?: string,
      risk?: string | string[],
      account?: string[],
      country?: string[],
      sortF?: string,
      sortOrd?: string,
      holiday?: string,
    ) => {
      try {
        setTableLoading(true);
        // 列表刷新后旧勾选可能已不在当前视图 → 清空，避免批量操作作用到不可见行
        setSelectedRowKeys([]);
        const params: any = {
          page,
          page_size: pageSize,
        };
        if (search) params.keyword = search;
        if (risk) params.risk_level = risk;
        if (account && account.length > 0) params.account = account;
        if (country && country.length > 0) params.country = country;
        if (sortF) params.sort_field = sortF;
        if (sortOrd) params.sort_order = sortOrd;
        // 始终显式传 holiday_filter（含 "all"）：后端据此区分「全部=不过滤」与「未指定=按 KEEP 过滤」
        if (holiday) params.holiday_filter = holiday;

        console.log("发送请求参数:", params);

        const response = await inventoryApi.search(params);
        const data = response.data?.data || response.data;
        console.log("API响应数据:", data);
        if (data) {
          // 使用后端返回的数据和总数（后端已处理共享库存过滤和分页）
          const items = data.items || data.list || [];
          setInventoryList(Array.isArray(items) ? items : []);
          setTotal(data.total || 0);
          console.log("设置总数:", data.total);
          // 注意：筛选选项从 /filter-options API 获取，不从列表数据提取
        }
      } catch (error) {
        console.error("获取库存列表失败:", error);
        messageApi.error("获取库存列表失败");
      } finally {
        setTableLoading(false);
      }
    },
    [messageApi],
  );

  useEffect(() => {
    fetchOverview({
      keyword: searchText,
      holiday: holidayFilter,
      account: accountFilter,
      country: countryFilter,
    });
    fetchLocalSummary();
    fetchInventoryList(
      1,
      pagination.pageSize,
      searchText,
      tableRiskFilter,
      accountFilter,
      countryFilter,
      sortField,
      sortOrder,
      holidayFilter,
    );

    // 获取筛选选项
    inventoryApi
      .getFilterOptions()
      .then((res) => {
        const data = res.data?.data || res.data;
        if (data) {
          // 后端返回的是对象数组 [{value, label}]，直接使用
          setAccountOptions(data.stores || []);
          setAllAccountOptions(data.stores || []); // 保存所有店铺选项
          setCountryOptions(data.countries || []);
        }
      })
      .catch((err) => {
        console.error("获取筛选选项失败:", err);
      });

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ==================== Event Handlers ====================

  const handleSearch = (value: string) => {
    setSearchText(value);
    setPagination((prev) => ({ ...prev, current: 1 }));
    fetchInventoryList(
      1,
      pagination.pageSize,
      value,
      tableRiskFilter,
      accountFilter,
      countryFilter,
      sortField,
      sortOrder,
      holidayFilter,
    );
     fetchOverview({ keyword: value });
  };

  const handleAccountFilterChange = (values: string[]) => {
    const next = [...new Set(values || [])];
    setAccountFilter(next);
    setPagination((prev) => ({ ...prev, current: 1 }));
    fetchInventoryList(
      1,
      pagination.pageSize,
      searchText,
      tableRiskFilter,
      next,
      countryFilter,
      sortField,
      sortOrder,
      holidayFilter,
    );
    fetchOverview({ account: next });
  };

  const handleCountryFilterChange = (values: string[]) => {
    const selectedCountries = [...new Set(values || [])];
    setCountryFilter(selectedCountries);
    setAccountFilter([]); // 清空店铺筛选，因为换了国家后店铺列表会变
    setPagination((prev) => ({ ...prev, current: 1 }));

    // 根据选中的国家过滤店铺列表
    if (selectedCountries.length > 0) {
      // 店铺格式是 "店铺名-国家代码"，如 "JeVenis-US"
      // 国家选项是中文，需要匹配店铺中的国家代码
      const filteredStores = allAccountOptions.filter((store) => {
        return selectedCountries.some((country) => {
          const code = COUNTRY_TO_CODE[country] || country;
          return (
            store.value.includes(`-${code}`) ||
            store.value.includes(`-${country}`)
          );
        });
      });
      setAccountOptions(filteredStores);
    } else {
      setAccountOptions(allAccountOptions);
    }

    fetchInventoryList(
      1,
      pagination.pageSize,
      searchText,
      tableRiskFilter,
      [], // 清空店铺筛选
      selectedCountries,
      sortField,
      sortOrder,
      holidayFilter,
    );
    fetchOverview({ country: selectedCountries, account: [] });
  };

  const handleTableChange = (
    _pag: TablePaginationConfig,
    _filters: any,
    sorter: any,
  ) => {
    // 处理排序（分页由独立 Pagination 组件处理）
    let newSortField: string | undefined = undefined;
    let newSortOrder: string | undefined = undefined;

    if (sorter.field) {
      newSortField = sorter.field as string;
      if (sorter.order === "ascend") {
        newSortOrder = "asc";
      } else if (sorter.order === "descend") {
        newSortOrder = "desc";
      }
    }

    setSortField(newSortField);
    setSortOrder(newSortOrder);

    fetchInventoryList(
      pagination.current || 1,
      pagination.pageSize || 20,
      searchText,
      tableRiskFilter,
      accountFilter,
      countryFilter,
      newSortField,
      newSortOrder,
      holidayFilter,
    );
  };

  const handlePageChange = (page: number, pageSize: number) => {
    setPagination((prev) => ({ ...prev, current: page, pageSize }));
    fetchInventoryList(
      page,
      pageSize,
      searchText,
      tableRiskFilter,
      accountFilter,
      countryFilter,
      sortField,
      sortOrder,
      holidayFilter,
    );
  };

  // 处理风险等级筛选（从顶部筛选栏）
  const handleTableRiskFilterChange = (value: string[]) => {
    const newFilter = value && value.length > 0 ? value : undefined;
    setTableRiskFilter(newFilter);
    fetchInventoryList(
      1,
      pagination.pageSize,
      searchText,
      newFilter,
      accountFilter,
      countryFilter,
      sortField,
      sortOrder,
      holidayFilter,
    );
  };

  const handleImportData = async () => {
    try {
      setImportLoading(true);
      const response = await inventoryApi.calculate();
      const taskId = response.data?.data?.task_id;
      if (taskId) {
        setCalculating(true);
        setCalcButtonLabel("计算中...");
        saveCalcStatusToStorage({ status: "running", taskId, step: "启动中" });
        pollCalcStatus(taskId);
      } else {
        messageApi.success("补货计算完成");
        fetchInventoryList(
          1,
          pagination.pageSize,
          searchText,
          tableRiskFilter,
          accountFilter,
          countryFilter,
          sortField,
          sortOrder,
          holidayFilter,
        );
        fetchOverview();
      }
    } catch (error: any) {
      console.error("启动计算失败:", error);
      messageApi.error(error?.response?.data?.detail || "操作失败");
    } finally {
      setImportLoading(false);
    }
  };

  // ==================== 行级标签：人工锁定徽标 → 居中弹窗（查看/解除，K5） ====================
  // 作用域 = (asin, account)：点哪一行就只作用于哪一店铺行（汇总行会展开到其共享库存子行）。
  const [lockModal, setLockModal] = useState<{
    open: boolean;
    asin: string;
    account: string;
    loading: boolean;
    tags: any[];
  }>({ open: false, asin: "", account: "", loading: false, tags: [] });

  /** 刷新主列表 + 概览（操作列各处共用；**并行**发起，串行会多花 ~650ms） */
  const refreshInventory = useCallback(() => {
    void Promise.all([
      fetchInventoryList(
        pagination.current || 1,
        pagination.pageSize || 20,
        searchText,
        tableRiskFilter,
        accountFilter,
        countryFilter,
        sortField,
        sortOrder,
        holidayFilter,
      ),
      fetchOverview(),
    ]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    fetchInventoryList,
    fetchOverview,
    pagination.current,
    pagination.pageSize,
    searchText,
    tableRiskFilter,
    accountFilter,
    countryFilter,
    sortField,
    sortOrder,
    holidayFilter,
  ]);

  const openLockModal = async (asin: string, account?: string) => {
    const acct = account || "";
    setLockModal({ open: true, asin, account: acct, loading: true, tags: [] });
    try {
      const res: any = await tagsApi.list({ asin, account: acct, page_size: 50 });
      const items =
        res?.data?.data?.items || res?.data?.items || [];
      setLockModal({ open: true, asin, account: acct, loading: false, tags: items });
    } catch (error: any) {
      setLockModal((m) => ({ ...m, loading: false }));
      messageApi.error(error?.response?.data?.detail || "获取标签失败");
    }
  };

  const releaseLockedTag = async (tag: any, clear: boolean) => {
    try {
      let res: any;
      if (tag.tag_type === "force_replenish") {
        res = await tagsApi.clearForceReplenish(lockModal.asin, lockModal.account);
      } else {
        res = await tagsApi.unlockHoliday(
          lockModal.asin, tag.tag_value, clear, lockModal.account);
      }
      const changed = res?.data?.data?.changed;
      if (changed === false) {
        messageApi.warning("未找到可解除的标签（可能已被解除）");
      } else {
        messageApi.success(
          clear ? "已解除锁定并清除标签" : "已解除锁定，恢复自动管辖",
        );
      }
      await openLockModal(lockModal.asin, lockModal.account);
      refreshInventory();
    } catch (error: any) {
      messageApi.error(error?.response?.data?.detail || "操作失败");
    }
  };

  const handleViewInbound = async (asin: string, account?: string) => {
    try {
      setInboundAsin(asin);
      setInboundModalVisible(true);
      setInboundLoading(true);
      const response = await inventoryApi.getInboundDetails(asin, account);
      const data = response.data?.data || response.data;
      setInboundDetails(Array.isArray(data) ? data : []);
    } catch (error) {
      console.error("获取在途详情失败:", error);
      messageApi.error("获取在途详情失败");
      setInboundDetails([]);
    } finally {
      setInboundLoading(false);
    }
  };

  const handleSaveInspection = async (record: InventoryItem) => {
    const originalVal = record.inspection_quantity || 0;
    if (editingInspectionVal === originalVal) {
      setEditingInspectionId(null);
      return;
    }
    try {
      await inventoryApi.updateInspectionQuantity(
        record.id,
        editingInspectionVal,
      );
      messageApi.success("查验数量已更新");
      setEditingInspectionId(null);
      fetchInventoryList(
        pagination.current,
        pagination.pageSize,
        searchText,
        tableRiskFilter,
        accountFilter,
        countryFilter,
        sortField,
        sortOrder,
        holidayFilter,
      );
    } catch (error: any) {
      console.error("更新查验数量失败:", error);
      messageApi.error(error?.response?.data?.detail || "更新失败");
    }
  };

  // ==================== Summary Expandable ====================

  const handleToggleExpand = async (record: InventoryItem) => {
    const parentId = record.id;
    if (!parentId) return;
    if (expandedParentIds.has(parentId)) {
      setExpandedParentIds((prev) => {
        const next = new Set(prev);
        next.delete(parentId);
        return next;
      });
      return;
    }
    try {
      const response = await inventoryApi.getSummaryChildren(
        record.asin,
        record.account,
      );
      const data = response.data?.data || [];
      const children = (Array.isArray(data) ? data : []).map((c) => ({
        ...c,
        _isChild: true,
      }));
      setChildrenMap((prev) => ({ ...prev, [parentId]: children }));
      setExpandedParentIds((prev) => {
        const next = new Set(prev);
        next.add(parentId);
        return next;
      });
    } catch (error) {
      console.error("获取子行数据失败:", error);
      messageApi.error("获取子行数据失败");
    }
  };

  const displayList = useMemo(() => {
    const result: InventoryItem[] = [];
    for (const item of inventoryList) {
      result.push(item);
      if (item.id && expandedParentIds.has(item.id) && childrenMap[item.id]) {
        result.push(
          ...childrenMap[item.id].map(
            (c) => ({ ...c, _isChild: true }) as InventoryItem,
          ),
        );
      }
    }
    return result;
  }, [inventoryList, expandedParentIds, childrenMap]);

  // ==================== 批量操作：行勾选 + 批量取消标记 / 批量取消强制补货 ====================
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [batchLoading, setBatchLoading] = useState(false);

  /** 已勾选行对应的原始记录（子行不可勾选：汇总行的操作已覆盖其共享库存子行） */
  const selectedRecords = useMemo(
    () =>
      displayList.filter(
        (r: any) => !r._isChild && selectedRowKeys.includes(r.id),
      ),
    [displayList, selectedRowKeys],
  );

  /** 批量取消标记（停售/节日）——后端按 snapshot_ids 批量清并自动覆盖汇总行子行 */
  const batchClearMark = () => {
    const recs = selectedRecords;
    if (!recs.length) return;
    Modal.confirm({
      title: `批量取消标记（已选 ${recs.length} 行）`,
      content:
        "将清除所选行的「节日 / 停售」标记（含自动打标写入的节日标签，并抑制其被自动加回）；所选行若为汇总行，会连同其共享库存子行一起清除。确定继续吗？",
      okText: "确定取消标记",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        setBatchLoading(true);
        try {
          const ids = recs.map((r: any) => r.id);
          const res: any = await inventoryApi.markProductStatus(ids, "clear");
          const n = res?.data?.data?.suppressed_tags;
          messageApi.success(
            `已批量取消标记 ${ids.length} 行${n ? `（并清除 ${n} 条节日标签）` : ""}`,
          );
          setSelectedRowKeys([]);
          refreshInventory();
        } catch (error: any) {
          messageApi.error(error?.response?.data?.detail || "批量取消标记失败");
          return Promise.reject();
        } finally {
          setBatchLoading(false);
        }
      },
    });
  };

  /** 批量取消强制补货——按 (asin, account) 行级解除，逐行并发调用 */
  const batchClearForce = () => {
    const recs = selectedRecords;
    if (!recs.length) return;
    Modal.confirm({
      title: `批量取消强制补货（已选 ${recs.length} 行）`,
      content:
        "将解除所选行的强制补货标签；所选行若为汇总行，会连同其共享库存子行一起解除。确定继续吗？",
      okText: "确定解除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        setBatchLoading(true);
        try {
          const results = await Promise.allSettled(
            recs.map((r: any) =>
              tagsApi.clearForceReplenish(r.asin, r.account),
            ),
          );
          const changed = results.filter(
            (x: any) =>
              x.status === "fulfilled" && x.value?.data?.data?.changed !== false,
          ).length;
          const skipped = results.filter(
            (x: any) =>
              x.status === "fulfilled" && x.value?.data?.data?.changed === false,
          ).length;
          const failed = results.length - changed - skipped;
          if (changed) messageApi.success(`已解除强制补货 ${changed} 行`);
          if (skipped) messageApi.warning(`${skipped} 行本就没有强制补货标签`);
          if (failed) messageApi.error(`${failed} 行解除失败`);
          setSelectedRowKeys([]);
          refreshInventory();
        } finally {
          setBatchLoading(false);
        }
      },
    });
  };


  const handleFileUpload = async (file: File) => {
    try {
      setImportLoading(true);
      const response = await inventoryApi.import(file);
      if (response.data?.success) {
        messageApi.success(
          `导入成功：${response.data.data?.total_rows || 0}条记录`,
        );
        fetchOverview();
        fetchInventoryList(
          1,
          pagination.pageSize,
          searchText,
          tableRiskFilter,
          accountFilter,
          countryFilter,
          sortField,
          sortOrder,
          holidayFilter,
        );
      } else {
        messageApi.warning(response.data?.message || "导入完成，请检查数据");
      }
    } catch (error: any) {
      console.error("导入失败:", error);
      messageApi.error(error?.response?.data?.detail || "导入失败");
    } finally {
      setImportLoading(false);
    }
    return false; // 阻止默认上传行为
  };

  const handleClearLocalInventory = async () => {
    try {
      setLocalLoading(true);
      const response = await localInventoryApi.clear();
      if (response.data?.success) {
        messageApi.success("本地仓库存已清空");
        fetchLocalSummary();
        fetchOverview();
        fetchInventoryList(
          1,
          pagination.pageSize,
          searchText,
          tableRiskFilter,
          accountFilter,
          countryFilter,
          sortField,
          sortOrder,
          holidayFilter,
        );
      }
    } catch (error: any) {
      messageApi.error(error?.response?.data?.detail || "清空失败");
    } finally {
      setLocalLoading(false);
    }
  };

  const handleReductionImport = async () => {
    if (!reductionCountry) {
      messageApi.warning("请先选择国家");
      return;
    }
    if (!reductionFile) {
      messageApi.warning("请选择文件");
      return;
    }
    try {
      setReductionImporting(true);
      setReductionResult(null);
      const response = await localInventoryApi.importReduction(
        reductionCountry,
        reductionFile,
      );
      if (response.data?.success) {
        const data = response.data.data;
        setReductionResult(data);
        messageApi.success(
          `导入完成：共${data.total}条，更新${data.updated}条，跳过${data.skipped}条`,
        );

        // 自动触发增量补货计算（异步）
        if (data.snapshot_ids && data.snapshot_ids.length > 0) {
          try {
            const calcResp = await inventoryApi.calculate({
              snapshot_ids: data.snapshot_ids.join(","),
            });
            const taskId = calcResp.data?.data?.task_id;
            if (taskId) {
              setCalculating(true);
              setCalcButtonLabel("计算中...");
              saveCalcStatusToStorage({
                status: "running",
                taskId,
                step: "启动中",
              });
              pollCalcStatus(taskId);
            } else {
              messageApi.success("补货计算完成");
              fetchInventoryList(
                1,
                pagination.pageSize,
                searchText,
                tableRiskFilter,
                accountFilter,
                countryFilter,
                sortField,
                sortOrder,
                holidayFilter,
              );
              fetchOverview();
            }
          } catch (calcError) {
            console.error("启动补货计算失败:", calcError);
            messageApi.warning("补货计算未启动，请稍后手动点击重新计算");
          }
        }
      } else {
        messageApi.warning(response.data?.message || "导入完成，请检查数据");
      }
    } catch (error: any) {
      console.error("导入减表失败:", error);
      messageApi.error(error?.response?.data?.detail || "导入失败");
    } finally {
      setReductionImporting(false);
    }
  };

  const handleDownloadReductionResult = async (fileId: string) => {
    try {
      const response = await localInventoryApi.downloadReductionResult(fileId);
      const blob = new Blob([response.data], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `减表导入结果_${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      messageApi.success("结果文件下载成功");
    } catch (error) {
      console.error("下载结果文件失败:", error);
      messageApi.error("下载结果文件失败");
    }
  };

  const handleRefresh = useCallback(() => {
    fetchOverview();
    fetchLocalSummary();
    fetchInventoryList(
      pagination.current || 1,
      pagination.pageSize || 20,
      searchText,
      tableRiskFilter,
      accountFilter,
      countryFilter,
      sortField,
      sortOrder,
      holidayFilter,
    );
  }, [
    fetchOverview,
    fetchLocalSummary,
    fetchInventoryList,
    pagination,
    searchText,
    tableRiskFilter,
    accountFilter,
    countryFilter,
    sortField,
    sortOrder,
    holidayFilter,
  ]);

  const handleExport = async () => {
    setExportModalVisible(true);
  };

  const handleConfirmExport = async () => {
    setExporting(true);
    try {
      const res = await inventoryApi.exportInventory({
        keyword: searchText || undefined,
        risk_level:
          tableRiskFilter && tableRiskFilter.length > 0
            ? tableRiskFilter
            : undefined,
        account: accountFilter.length > 0 ? accountFilter : undefined,
        country: countryFilter.length > 0 ? countryFilter : undefined,
        fields:
          selectedExportFields.length > 0 ? selectedExportFields : undefined,
      });

      const blob = new Blob([res.data], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `库存明细_${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      messageApi.success("导出成功");
      setExportModalVisible(false);
    } catch (error) {
      messageApi.error("导出失败");
    } finally {
      setExporting(false);
    }
  };

  // 保存同步状态到 localStorage
  const saveSyncStatusToStorage = (status: any) => {
    localStorage.setItem(
      "feishuSyncStatus",
      JSON.stringify({
        ...status,
        savedAt: Date.now(),
      }),
    );
  };

  // 从 localStorage 读取同步状态
  const loadSyncStatusFromStorage = () => {
    const saved = localStorage.getItem("feishuSyncStatus");
    if (saved) {
      try {
        const status = JSON.parse(saved);
        // 如果状态是30分钟内的，认为是有效的
        if (Date.now() - status.savedAt < 30 * 60 * 1000) {
          return status;
        }
      } catch (e) {
        console.error("解析同步状态失败", e);
      }
    }
    return null;
  };

  // 同步飞书FBA在途数据
  const handleSyncFeishu = async () => {
    setSyncingFeishu(true);
    try {
      const res = await inventoryApi.syncFeishuInbound();
      if (res.data?.data?.started) {
        messageApi.success("同步任务已启动，请稍候...");
        // 开始轮询状态
        pollSyncStatus();
      } else {
        messageApi.warning(res.data?.data?.message || "同步任务正在运行中");
        setSyncingFeishu(false);
      }
    } catch (error) {
      messageApi.error("启动同步失败");
      setSyncingFeishu(false);
    }
  };

  // 轮询同步状态
  const pollSyncStatus = async () => {
    try {
      const res = await inventoryApi.getSyncFeishuStatus();
      const status = res.data?.data;

      // 保存状态到 localStorage
      saveSyncStatusToStorage(status);

      if (status?.is_running) {
        // 更新按钮文字显示进度
        const step = status.step || "";
        const progress = status.progress || 0;
        setSyncButtonLabel(`同步中 ${progress}% - ${step}`);
        setSyncProgress(progress);
        setSyncStep(step);
        // 继续轮询
        setTimeout(pollSyncStatus, 1500);
      } else {
        // 同步完成
        setSyncingFeishu(false);
        setSyncButtonLabel("同步FBA在途");
        setSyncProgress(0);
        setSyncStep("");
        // 清除 localStorage
        localStorage.removeItem("feishuSyncStatus");

        if (status?.error) {
          messageApi.error(`同步失败: ${status.error}`);
        } else if (status?.progress === 100) {
          messageApi.success(`同步完成！更新 ${status?.updated || 0} 条记录`);
        }
      }
    } catch (error) {
      setSyncingFeishu(false);
      setSyncButtonLabel("同步FBA在途");
      setSyncProgress(0);
      setSyncStep("");
      messageApi.error("获取同步状态失败");
    }
  };

  // ========== 海外仓同步（飞书多维表 → 数据库 → 主行） ==========
  // 与 FBA 同步一致：任务在服务端后台线程跑，前端只轮询状态；
  // 进行中的状态写入 localStorage，切页/刷新回来后自动恢复并继续轮询。

  const OVERSEAS_SYNC_STATUS_KEY = "overseasSyncStatus";

  const saveOverseasStatusToStorage = (status: any) => {
    localStorage.setItem(
      OVERSEAS_SYNC_STATUS_KEY,
      JSON.stringify({ ...status, savedAt: Date.now() }),
    );
  };

  const loadOverseasStatusFromStorage = () => {
    const saved = localStorage.getItem(OVERSEAS_SYNC_STATUS_KEY);
    if (!saved) return null;
    try {
      const status = JSON.parse(saved);
      // 与 FBA 同步一致的 30 分钟有效期，避免恢复早已结束的任务
      if (Date.now() - status.savedAt < 30 * 60 * 1000) return status;
      localStorage.removeItem(OVERSEAS_SYNC_STATUS_KEY);
    } catch (e) {
      console.error("解析海外仓同步状态失败", e);
    }
    return null;
  };

  const refreshAfterOverseasSync = () => {
    fetchInventoryList(
      pagination.current || 1,
      pagination.pageSize,
      searchText,
      tableRiskFilter,
      accountFilter,
      countryFilter,
      sortField,
      sortOrder,
      holidayFilter,
    );
    fetchOverseasSummary();
  };

  const stopOverseasPolling = () => {
    overseasPollingRef.current = false;
    setSyncingOverseas(false);
    setOverseasButtonLabel("同步海外仓");
    localStorage.removeItem(OVERSEAS_SYNC_STATUS_KEY);
  };

  const pollOverseasStatus = async (attempt = 0) => {
    // 组件已卸载（切走页面）就停掉本条轮询链，回来时靠 localStorage 恢复，
    // 避免离开页面后仍持续请求后端
    if (!overseasMountedRef.current) {
      overseasPollingRef.current = false;
      return;
    }
    // 最长轮询 20 分钟（与后端定时任务的等待上限一致），避免异常时无限轮询
    const MAX_POLL = 800;
    if (attempt > MAX_POLL) {
      stopOverseasPolling();
      messageApi.warning("海外仓同步耗时过长，已停止轮询，请稍后刷新查看结果");
      return;
    }
    try {
      const res = await inventoryApi.getSyncOverseasStatus();
      const status = res.data?.data;
      if (status?.is_running) {
        saveOverseasStatusToStorage(status);
        setSyncingOverseas(true);
        setOverseasButtonLabel(`海外仓同步 ${status.progress || 0}%`);
        setTimeout(() => pollOverseasStatus(attempt + 1), 1500);
        return;
      }
      stopOverseasPolling();
      if (status?.error) {
        messageApi.error(`海外仓同步失败: ${status.error}`);
      } else if (status?.skipped) {
        // 源表数据比库中更旧（上游回退/补数），后端主动跳过，不应提示成功
        messageApi.warning(status.step || "本次同步已跳过");
      } else if (status?.progress === 100) {
        messageApi.success(
          status.step || `海外仓同步完成，写入 ${status.updated || 0} 个 SKU`,
        );
        refreshAfterOverseasSync();
      }
    } catch (error) {
      stopOverseasPolling();
      messageApi.error("获取海外仓同步状态失败");
    }
  };

  /** 启动一条轮询链（同一时刻只允许一条，避免手动触发与恢复触发并发轮询） */
  const startOverseasPolling = () => {
    if (overseasPollingRef.current) return;
    overseasPollingRef.current = true;
    pollOverseasStatus(0);
  };

  const handleSyncOverseas = async () => {
    setSyncingOverseas(true);
    setOverseasButtonLabel("海外仓同步 0%");
    try {
      const res = await inventoryApi.syncOverseasInventory();
      const data = res.data?.data;
      if (data?.started) {
        messageApi.success("海外仓同步任务已启动，请稍候...");
        startOverseasPolling();
      } else if (data?.reason === "running") {
        // 已在运行（如 8:00 定时任务 / 其他标签页触发的任务）→ 接入轮询，不重复启动
        messageApi.info("海外仓同步任务正在运行中，已接入进度显示");
        startOverseasPolling();
      } else {
        messageApi.warning(data?.message || "海外仓同步未启动");
        stopOverseasPolling();
      }
    } catch (error) {
      messageApi.error("启动海外仓同步失败");
      stopOverseasPolling();
    }
  };

  // 进入页面时：恢复「进行中」的海外仓任务（含 8:00 定时任务）+ 拉取上次同步结果
  useEffect(() => {
    overseasMountedRef.current = true;
    const saved = loadOverseasStatusFromStorage();
    if (saved?.is_running) {
      setSyncingOverseas(true);
      setOverseasButtonLabel(`海外仓同步 ${saved.progress || 0}%`);
      startOverseasPolling();
    }
    fetchOverseasSummary();
    return () => {
      // 切走页面：停掉轮询链（任务本身在服务端继续跑），回来时由 localStorage 恢复
      overseasMountedRef.current = false;
      overseasPollingRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 页面加载时检查是否有保存的同步状态
  useEffect(() => {
    const savedStatus = loadSyncStatusFromStorage();
    if (savedStatus && savedStatus.is_running) {
      // 恢复同步状态
      setSyncingFeishu(true);
      setSyncButtonLabel(
        `同步中 ${savedStatus.progress || 0}% - ${savedStatus.step || ""}`,
      );
      setSyncProgress(savedStatus.progress || 0);
      setSyncStep(savedStatus.step || "");
      // 继续轮询
      pollSyncStatus();
    }
  }, []);

  // ========== 补货计算状态持久化（跨页面） ==========

  const CALC_STATUS_KEY = "calcStatus";

  const saveCalcStatusToStorage = (status: any) => {
    localStorage.setItem(
      CALC_STATUS_KEY,
      JSON.stringify({
        ...status,
        savedAt: Date.now(),
      }),
    );
  };

  const loadCalcStatusFromStorage = () => {
    const saved = localStorage.getItem(CALC_STATUS_KEY);
    if (saved) {
      try {
        const status = JSON.parse(saved);
        if (Date.now() - status.savedAt < 30 * 60 * 1000) {
          return status;
        }
      } catch (e) {
        console.error("解析计算状态失败", e);
      }
    }
    return null;
  };

  const pollCalcStatus = async (taskId: string) => {
    try {
      const res = await inventoryApi.getCalculateStatus(taskId);
      const task = res.data?.data;

      saveCalcStatusToStorage({ ...task, taskId });

      if (task?.status === "running" || task?.status === "pending") {
        const progressText = task.progress != null ? ` ${task.progress}%` : "";
        setCalcButtonLabel(`计算中${progressText}`);
        setTimeout(() => pollCalcStatus(taskId), 2000);
      } else {
        setCalculating(false);
        setCalcButtonLabel("重新计算");
        localStorage.removeItem(CALC_STATUS_KEY);

        if (task?.status === "completed") {
          messageApi.success(`补货计算完成`);
          fetchInventoryList(
            1,
            pagination.pageSize,
            searchText,
            tableRiskFilter,
            accountFilter,
            countryFilter,
            sortField,
            sortOrder,
            holidayFilter,
          );
          fetchOverview();
        } else if (task?.status === "failed") {
          messageApi.warning(`补货计算失败: ${task?.error || "未知错误"}`);
        }
      }
    } catch (error) {
      setCalculating(false);
      setCalcButtonLabel("重新计算");
      localStorage.removeItem(CALC_STATUS_KEY);
      messageApi.warning("查询计算状态失败");
    }
  };

  // 页面加载时检查是否有正在运行的计算任务
  useEffect(() => {
    const savedStatus = loadCalcStatusFromStorage();
    if (
      savedStatus &&
      (savedStatus.status === "running" || savedStatus.status === "pending") &&
      savedStatus.taskId
    ) {
      setCalculating(true);
      setCalcButtonLabel(`计算中...${savedStatus.step || ""}`);
      pollCalcStatus(savedStatus.taskId);
    }
  }, []);

  // ==================== Table Columns ====================

  const inventoryColumns: ColumnsType<InventoryItem> = [
    {
      title: "",
      key: "_expand",
      width: 40,
      fixed: "left",
      render: (_: any, record: InventoryItem) => {
        if (record.summary_flag !== "是") return null;
        const isExpanded = record.id ? expandedParentIds.has(record.id) : false;
        return (
          <span
            onClick={(e) => {
              e.stopPropagation();
              handleToggleExpand(record);
            }}
            style={{
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
            }}
          >
            {isExpanded ? (
              <ChevronDown size={14} />
            ) : (
              <ChevronRight size={14} />
            )}
          </span>
        );
      },
    },
    {
      title: "ASIN",
      dataIndex: "asin",
      key: "asin",
      width: 130,
      fixed: "left",
      render: (val: string, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return (
            <span style={{ color: "#1890ff", fontWeight: 600 }}>
              {val || "-"}
            </span>
          );
        }
        return val || "-";
      },
    },
    {
      title: "图片",
      dataIndex: "image_url",
      key: "image_url",
      width: 70,
      render: (_: any, record: InventoryItem) => renderProductImage(record.image_url),
    },
    {
      title: "SKU",
      dataIndex: "sku",
      key: "sku",
      width: 120,
      render: (val: string, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return (
            <span style={{ color: "#1890ff", fontWeight: 500 }}>
              {val || "-"}
            </span>
          );
        }
        return val || "-";
      },
    },
    {
      title: "品名",
      dataIndex: "product_name",
      key: "product_name",
      width: 200,
      ellipsis: true,
      render: (text: string, record: InventoryItem) => (
        <Tooltip title={text}>
          {record.summary_flag === "共享库存" ? (
            <span style={{ color: "#1890ff", fontWeight: 500 }}>
              {text || "-"}
            </span>
          ) : (
            <span>{text || "-"}</span>
          )}
        </Tooltip>
      ),
    },
    {
      title: "店铺",
      dataIndex: "account",
      key: "account",
      width: 120,
      render: (val: string, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return <Tag color="blue">{val || "-"}</Tag>;
        }
        return val || "-";
      },
    },
    {
      title: "国家",
      dataIndex: "country",
      key: "country",
      width: 100,
      render: (val: string) => <span>{normalizeCountry(val)}</span>,
    },
    {
      title: "FBA库存",
      dataIndex: "fba_stock",
      key: "fba_stock",
      width: 90,
      align: "center",
      render: (val: number, record: InventoryItem) => {
        const tooltipContent = (
          <div style={{ padding: "4px 0" }}>
            <table style={{ fontSize: 12, borderCollapse: "collapse" }}>
              <tbody>
                <tr>
                  <td
                    style={{
                      padding: "4px 12px 4px 0",
                      color: "#666",
                      minWidth: 60,
                    }}
                  >
                    FNSKU
                  </td>
                  <td style={{ padding: "4px 0", fontWeight: 500 }}>
                    {record.fnsku || "-"}
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "4px 12px 4px 0", color: "#666" }}>
                    MSKU
                  </td>
                  <td style={{ padding: "4px 0", fontWeight: 500 }}>
                    {record.msku || "-"}
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "4px 12px 4px 0", color: "#666" }}>
                    可售
                  </td>
                  <td
                    style={{
                      padding: "4px 0",
                      fontWeight: 500,
                      color: "#52c41a",
                    }}
                  >
                    {formatNumber(record.fba_available)}
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "4px 12px 4px 0", color: "#666" }}>
                    待调仓
                  </td>
                  <td
                    style={{
                      padding: "4px 0",
                      fontWeight: 500,
                      color: "#fa8c16",
                    }}
                  >
                    {formatNumber(record.fba_pending_transfer)}
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "4px 12px 4px 0", color: "#666" }}>
                    FBA预留
                  </td>
                  <td
                    style={{
                      padding: "4px 0",
                      fontWeight: 500,
                      color: "#faad14",
                    }}
                  >
                    {formatNumber(
                      Math.max(
                        0,
                        (record.fba_stock || 0) -
                          (record.fba_available || 0) -
                          (record.fba_pending_transfer || 0) -
                          (record.fba_inbound_processing || 0),
                      ),
                    )}
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "4px 12px 4px 0", color: "#666" }}>
                    入库中
                  </td>
                  <td
                    style={{
                      padding: "4px 0",
                      fontWeight: 500,
                      color: "#1890ff",
                    }}
                  >
                    {formatNumber(record.fba_inbound_processing)}
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "4px 12px 4px 0", color: "#666" }}>
                    毛利率
                  </td>
                  <td
                    style={{
                      padding: "4px 0",
                      fontWeight: 500,
                      color:
                        record.gross_margin != null && record.gross_margin >= 0
                          ? "#52c41a"
                          : "#999",
                    }}
                  >
                    {record.gross_margin != null
                      ? `${(record.gross_margin * 100).toFixed(1)}%`
                      : "-"}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        );
        if (val && val > 0) {
          return (
            <Tooltip title={tooltipContent}>
              <span
                style={{
                  display: "inline-block",
                  color: "#1890ff",
                  fontWeight: 600,
                  fontSize: 14,
                  cursor: "pointer",
                  padding: "2px 8px",
                  borderRadius: "4px",
                  background: "#f0f7ff",
                  transition: "background 0.2s",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "#d6eaff";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "#f0f7ff";
                }}
              >
                {formatNumber(val)}
              </span>
            </Tooltip>
          );
        }
        return (
          <span
            style={{
              color: "#999",
              fontSize: 13,
            }}
          >
            {formatNumber(val)}
          </span>
        );
      },
    },
    {
      title: "在途",
      dataIndex: "fba_inbound",
      key: "fba_inbound",
      width: 80,
      align: "right",
      render: (val: number, record: InventoryItem) => {
        const originalInbound = val || 0;
        const inspected = record.inspection_quantity || 0;
        const effectiveInbound = originalInbound - inspected;
        if (record.summary_flag === "共享库存") {
          return (
            <span style={{ color: "#1890ff", fontWeight: 600 }}>
              {formatNumber(val)}
            </span>
          );
        }
        const tooltipContent = (
          <div style={{ padding: "4px 0" }}>
            <div style={{ fontSize: 12, marginBottom: 4 }}>
              <span style={{ color: "#8c8c8c" }}>原始导入在途: </span>
              <span style={{ fontWeight: 500 }}>
                {formatNumber(originalInbound)}
              </span>
            </div>
            <div style={{ fontSize: 12, marginBottom: 4 }}>
              <span style={{ color: "#8c8c8c" }}>已查验: </span>
              <span style={{ fontWeight: 500, color: "#722ed1" }}>
                {formatNumber(inspected)}
              </span>
            </div>
            <div
              style={{
                fontSize: 12,
                borderTop: "1px solid #434343",
                paddingTop: 4,
              }}
            >
              <span style={{ color: "#8c8c8c" }}>实际在途: </span>
              <span
                style={{
                  fontWeight: 700,
                  color: effectiveInbound > 0 ? "#1890ff" : "#999",
                }}
              >
                {formatNumber(effectiveInbound)}
              </span>
            </div>
          </div>
        );
        return (
          <Tooltip title={tooltipContent} color="#262626">
            <span
              style={{
                color: effectiveInbound > 0 ? "#1890ff" : "#999",
                fontWeight: effectiveInbound > 0 ? 600 : 400,
              }}
            >
              {formatNumber(effectiveInbound)}
            </span>
          </Tooltip>
        );
      },
    },
    {
      title: "查验货件",
      dataIndex: "inspection_quantity",
      key: "inspection_quantity",
      width: 90,
      align: "center",
      render: (val: number | undefined, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return <span style={{ color: "#999" }}>-</span>;
        }
        const qty = val || 0;
        if (editingInspectionId === record.id) {
          return (
            <InputNumber
              size="small"
              min={0}
              value={editingInspectionVal}
              onChange={(value) => setEditingInspectionVal(value || 0)}
              onPressEnter={() => handleSaveInspection(record)}
              onBlur={() => handleSaveInspection(record)}
              style={{ width: 70 }}
              autoFocus
            />
          );
        }
        return (
          <span
            onClick={() => {
              setEditingInspectionId(record.id);
              setEditingInspectionVal(qty);
            }}
            style={{
              color: qty > 0 ? "#722ed1" : "#d9d9d9",
              fontWeight: qty > 0 ? 600 : 400,
              cursor: "pointer",
            }}
          >
            {qty > 0 ? qty.toLocaleString() : "点击填写"}
          </span>
        );
      },
    },
    {
      title: "总库存",
      dataIndex: "total_stock",
      key: "total_stock",
      width: 100,
      align: "right",
      sorter: true,
      sortOrder:
        sortField === "total_stock"
          ? sortOrder === "asc"
            ? "ascend"
            : "descend"
          : undefined,
      render: (val: number, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return (
            <span style={{ color: "#1890ff", fontWeight: 600 }}>
              {formatNumber(val)}
            </span>
          );
        }
        return formatNumber(val);
      },
    },
    {
      title: "毛利率",
      dataIndex: "gross_margin",
      key: "gross_margin",
      width: 90,
      align: "right",
      sorter: true,
      sortOrder:
        sortField === "gross_margin"
          ? sortOrder === "asc"
            ? "ascend"
            : "descend"
          : undefined,
      render: (val: number | undefined, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return <span style={{ color: "#999" }}>-</span>;
        }
        if (val === null || val === undefined) return "-";
        return (
          <span
            style={{ color: val >= 0 ? "#52c41a" : "#cf1322", fontWeight: 500 }}
          >
            {(val * 100).toFixed(1)}%
          </span>
        );
      },
    },
    {
      title: "30天退货率",
      dataIndex: "return_rate_30d",
      key: "return_rate_30d",
      width: 100,
      align: "right",
      sorter: true,
      sortOrder:
        sortField === "return_rate_30d"
          ? sortOrder === "asc"
            ? "ascend"
            : "descend"
          : undefined,
      render: (val: number | undefined, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return <span style={{ color: "#999" }}>-</span>;
        }
        if (val === null || val === undefined) return "-";
        return (
          <span style={{ color: val > 0 ? "#cf1322" : "#999", fontWeight: 500 }}>
            {(val * 100).toFixed(2)}%
          </span>
        );
      },
    },
    {
      title: "60天退货率",
      dataIndex: "return_rate_60d",
      key: "return_rate_60d",
      width: 100,
      align: "right",
      sorter: true,
      sortOrder:
        sortField === "return_rate_60d"
          ? sortOrder === "asc"
            ? "ascend"
            : "descend"
          : undefined,
      render: (val: number | undefined, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return <span style={{ color: "#999" }}>-</span>;
        }
        if (val === null || val === undefined) return "-";
        return (
          <span style={{ color: val > 0 ? "#cf1322" : "#999", fontWeight: 500 }}>
            {(val * 100).toFixed(2)}%
          </span>
        );
      },
    },
    {
      title: "本地仓",
      dataIndex: "local_inventory",
      key: "local_inventory",
      width: 90,
      align: "right",
      sorter: true,
      sortOrder:
        sortField === "local_inventory"
          ? sortOrder === "asc"
            ? "ascend"
            : "descend"
          : undefined,
      render: (val: number | undefined, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return <span style={{ color: "#999" }}>-</span>;
        }
        const qty = val || 0;
        if (qty > 0) {
          return (
            <Tooltip title="运营上传的本地仓库存">
              <span
                style={{
                  color: "#722ed1",
                  fontWeight: 600,
                  fontSize: 13,
                }}
              >
                {formatNumber(qty)}
              </span>
            </Tooltip>
          );
        }
        return <span style={{ color: "#ccc" }}>0</span>;
      },
    },
    {
      title: "海外仓",
      dataIndex: "overseas_inventory",
      key: "overseas_inventory",
      width: 95,
      align: "right",
      sorter: true,
      sortOrder:
        sortField === "overseas_inventory"
          ? sortOrder === "asc"
            ? "ascend"
            : "descend"
          : undefined,
      render: (val: number | undefined, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return <span style={{ color: "#999" }}>-</span>;
        }
        const qty = val || 0;
        const age = record.overseas_max_age;
        const tip = [
          "来自飞书多维表「海外仓」每日快照",
          age !== null && age !== undefined ? `最大库龄：${age} 天` : null,
        ]
          .filter(Boolean)
          .join("\n");
        if (qty > 0) {
          return (
            <Tooltip title={<span style={{ whiteSpace: "pre-line" }}>{tip}</span>}>
              <span
                style={{
                  color: "#08979c",
                  fontWeight: 600,
                  fontSize: 13,
                }}
              >
                {formatNumber(qty)}
              </span>
            </Tooltip>
          );
        }
        return <span style={{ color: "#ccc" }}>0</span>;
      },
    },
    {
      title: "日均销量",
      dataIndex: "daily_sales",
      key: "daily_sales",
      width: 100,
      align: "right",
      sorter: true,
      sortOrder:
        sortField === "daily_sales"
          ? sortOrder === "asc"
            ? "ascend"
            : "descend"
          : undefined,
      render: (val: number, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return <span style={{ color: "#999" }}>-</span>;
        }
        if (val == null || val === undefined) return "-";
        return val.toFixed(2);
      },
    },
    {
      title: "7天销量",
      dataIndex: "sales_7d",
      key: "sales_7d",
      width: 100,
      align: "right",
      sorter: true,
      sortOrder:
        sortField === "sales_7d"
          ? sortOrder === "asc"
            ? "ascend"
            : "descend"
          : undefined,
      render: (val: number | undefined) => {
        if (val == null || val === undefined) return "-";
        return formatNumber(val);
      },
    },
    {
      title: "14天销量",
      dataIndex: "sales_14d",
      key: "sales_14d",
      width: 100,
      align: "right",
      sorter: true,
      sortOrder:
        sortField === "sales_14d"
          ? sortOrder === "asc"
            ? "ascend"
            : "descend"
          : undefined,
      render: (val: number | undefined) => {
        if (val == null || val === undefined) return "-";
        return formatNumber(val);
      },
    },
    {
      title: "7日日均",
      dataIndex: "daily_avg_7d",
      key: "daily_avg_7d",
      width: 100,
      align: "right",
      sorter: true,
      sortOrder:
        sortField === "daily_avg_7d"
          ? sortOrder === "asc"
            ? "ascend"
            : "descend"
          : undefined,
      render: (val: number | undefined) => {
        if (val == null || val === undefined) return "-";
        return val.toFixed(2);
      },
    },
    {
      title: "14日日均",
      dataIndex: "daily_avg_14d",
      key: "daily_avg_14d",
      width: 100,
      align: "right",
      sorter: true,
      sortOrder:
        sortField === "daily_avg_14d"
          ? sortOrder === "asc"
            ? "ascend"
            : "descend"
          : undefined,
      render: (val: number | undefined) => {
        if (val == null || val === undefined) return "-";
        return val.toFixed(2);
      },
    },
    {
      title: "可售天数",
      dataIndex: "days_of_supply",
      key: "days_of_supply",
      width: 120,
      align: "center",
      sorter: true,
      sortOrder:
        sortField === "days_of_supply"
          ? sortOrder === "asc"
            ? "ascend"
            : "descend"
          : undefined,
      render: (val: number, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return <Tag color="blue">共享库存</Tag>;
        }
        return getDaysSupplyTag(val);
      },
    },
    {
      title: "断货时间",
      dataIndex: "stockout_date",
      key: "stockout_date",
      width: 120,
      sorter: true,
      sortOrder:
        sortField === "stockout_date"
          ? sortOrder === "asc"
            ? "ascend"
            : "descend"
          : undefined,
      render: (val: string | null, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return <span style={{ color: "#999" }}>-</span>;
        }
        return val || "-";
      },
    },
    {
      title: "建议补货",
      dataIndex: "suggest_qty",
      key: "suggest_qty",
      width: 110,
      align: "center",
      sorter: true,
      sortOrder:
        sortField === "suggest_qty"
          ? sortOrder === "asc"
            ? "ascend"
            : "descend"
          : undefined,
      render: (val: number | undefined, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return <span style={{ color: "#999" }}>-</span>;
        }
        const qty = val || 0;
        if (qty > 0) {
          return (
            <Tooltip
              title={record.replenishment_reason || `建议补货 ${qty} 件`}
            >
              <span
                style={{
                  display: "inline-block",
                  background: "#fff7e6",
                  color: "#d46b08",
                  fontWeight: 700,
                  fontSize: 14,
                  padding: "2px 10px",
                  borderRadius: 4,
                  cursor: "pointer",
                }}
              >
                +{qty.toLocaleString()}
              </span>
            </Tooltip>
          );
        }
        return <span style={{ color: "#d9d9d9" }}>-</span>;
      },
    },
    {
      title: "12月+库龄",
      dataIndex: "age_12_plus",
      key: "age_12_plus",
      width: 110,
      align: "center",
      sorter: true,
      sortOrder:
        sortField === "age_12_plus"
          ? sortOrder === "asc"
            ? "ascend"
            : "descend"
          : undefined,
      render: (val: number) => {
        if (val == null || val === 0)
          return <span style={{ color: "#d9d9d9" }}>-</span>;
        return (
          <span
            style={{
              color: val > 0 ? "#cf1322" : undefined,
              fontWeight: val > 0 ? 600 : undefined,
            }}
          >
            {val.toLocaleString()}
          </span>
        );
      },
    },
    {
      title: "风险等级",
      dataIndex: "risk_level",
      key: "risk_level",
      width: 100,
      align: "center",
      render: (val: string, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return <Tag color="blue">共享库存</Tag>;
        }
        return getRiskLevelTag(val);
      },
    },
    {
      // 节日标签列：展示本行生效的节日标记（快照标记 ∪ 生效节日标签，后端已按逗号合并）。
      // 操作列的按钮不再显示节日文本（改为「标记 / 取消标记」），节日内容统一在本列呈现。
      title: "节日标签",
      dataIndex: "is_holiday",
      key: "tag_holiday",
      width: 130,
      align: "center",
      render: (val: any, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") return null;
        const vals = String(val ?? "")
          .split(",")
          .map((v) => v.trim())
          .filter(Boolean);
        if (!vals.length) return <Tag color="default">-</Tag>;
        return (
          <span>
            {vals.map((v) => (
              <Tag color="purple" key={v} style={{ marginInlineEnd: 2 }}>
                {v}
              </Tag>
            ))}
          </span>
        );
      },
    },
    {
      title: "强制补货",
      dataIndex: "force_replenish",
      key: "force_replenish",
      width: 100,
      align: "center",
      render: (val: any, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return null;
        }
        if (record.force_replenish === 1) {
          return (
            <Tooltip title="强制补货标签生效中：该商品无视节日/负利润排除，始终参与补货。">
              <Tag color="gold" icon={<Pin size={12} />}>
                强制补货
              </Tag>
            </Tooltip>
          );
        }
        return <Tag color="default">-</Tag>;
      },
    },
    {
      title: "人工锁定",
      dataIndex: "tag_locked",
      key: "tag_locked",
      width: 100,
      align: "center",
      render: (_: any, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return null;
        }
        if (record.tag_locked === 1) {
          return (
            <Tooltip title="该店铺行有生效中的人工标签（K5 人工锁定优先）：自动打标不会覆盖或清除。点击查看并解除锁定。">
              <Tag
                color="purple"
                icon={<Lock size={12} />}
                style={{ cursor: "pointer" }}
                onClick={() => openLockModal(record.asin, record.account)}
              >
                人工锁定
              </Tag>
            </Tooltip>
          );
        }
        return <Tag color="default">-</Tag>;
      },
    },
    {
      title: "操作",
      key: "action",
      // 宽度必须容纳「在途详情 + 强制补货 + 第3槽(标记/停售/节日)」同时出现的最坏情形。
      // 真机实测（Chromium 1600x900）固定部分：padding 8+8、在途详情 94、gap 4、
      // 强制补货 94、gap 4 = 212px；第3槽按最长 K6 节日名「感恩节黑五」留 84px
      // （Tag 内 maxWidth，见下方节日渲染）→ 合计 296px，取 310 留 14px 余量。
      // 原 width:200 时单元格 scrollWidth=248、溢出 48px，导致第 3 槽的「标记」
      // 被 sticky 容器完全裁掉（实测 visibleW=0），即本缺陷根因。
      width: 310,
      fixed: "right",
      render: (_: any, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return null;
        }
        const isSummary = record.summary_flag === "是";
        // 在途为 0（无货件明细）时「在途详情」不可点，只给提示
        const noInbound = (record.fba_inbound || 0) <= 0;
        const confirmMessage = (action: string) => {
          let msg = `确定要${action}该商品吗？`;
          if (isSummary) {
            msg += "该操作将同时标记/取消标记其所有子行，是否继续？";
          }
          return msg;
        };
        return (
          <Space size={4} style={{ flexWrap: "nowrap", whiteSpace: "nowrap" }}>
            {/* 与线上版本保持一字不差的实现：Tooltip + disabled（541f01e）。
                不要额外加 pointer-events:none / span 包一层 —— 那会让「置灰后鼠标悬浮没有高亮反馈」，
                与线上观感不一致。 */}
            <Tooltip title={noInbound ? "无在途货件" : ""}>
              <Button
                type="link"
                size="small"
                icon={<Truck size={14} />}
                disabled={noInbound}
                onClick={() => handleViewInbound(record.asin, record.account)}
              >
                在途详情
              </Button>
            </Tooltip>
            {record.force_replenish === 1 ? (
              // 与「强制补货」按钮同结构（icon + 4 字），保证两态等宽 —— 否则第 3 槽
              // 「标记」的 X 位置随行漂移（实测 22px，操作列横向不对齐的根因）。
              // 确认弹窗用页面中心 Modal（与「设置强制补货」一致），不用锚定 Popconfirm。
              <Button
                type="link"
                size="small"
                danger
                icon={<PinOff size={14} />}
                onClick={() => {
                  Modal.confirm({
                    title: `解除强制补货：${record.asin}${record.account ? "（" + record.account + "）" : ""}`,
                    content:
                      "解除后该店铺行将恢复常规过滤规则（节日/负利润排除重新生效）。确定解除吗？",
                    okText: "确定解除",
                    cancelText: "取消",
                    okButtonProps: { danger: true },
                    onOk: async () => {
                      try {
                        const res: any = await tagsApi.clearForceReplenish(
                          record.asin,
                          record.account,
                        );
                        const changed = res?.data?.data?.changed;
                        if (changed === false) {
                          messageApi.warning(
                            "未找到可解除的强制补货标签（可能已被解除）",
                          );
                        } else {
                          messageApi.success("已解除强制补货标签");
                        }
                        refreshInventory();
                      } catch (error: any) {
                        messageApi.error(
                          error?.response?.data?.detail || "操作失败",
                        );
                        return Promise.reject();
                      }
                    },
                  });
                }}
              >
                解除强制
              </Button>
            ) : (
              <Button
                type="link"
                size="small"
                icon={<Pin size={14} />}
                onClick={() => {
                  let reason = "";
                  Modal.confirm({
                    title: `设置强制补货：${record.asin}${record.account ? "（" + record.account + "）" : ""}`,
                    content: (
                      <div>
                        <p style={{ marginBottom: 4 }}>
                          该店铺行将无视节日/负利润排除，始终参与补货（停售除外）。
                          若本行为汇总行，将一并作用于其共享库存子行。
                        </p>
                        <Input.TextArea
                          placeholder="必填：请填写设置理由（如「黑五蓄水，运营总监确认」）"
                          onChange={(e) => (reason = e.target.value)}
                        />
                      </div>
                    ),
                    okText: "确认设置",
                    cancelText: "取消",
                    onOk: async () => {
                      if (!reason.trim()) {
                        messageApi.error("设置强制补货必须填写理由");
                        return Promise.reject();
                      }
                      try {
                        await tagsApi.setForceReplenish(
                          record.asin,
                          reason.trim(),
                          record.sku,
                          record.account,
                        );
                        messageApi.success("已设置强制补货标签");
                        fetchInventoryList(
                          pagination.current || 1,
                          pagination.pageSize || 20,
                          searchText,
                          tableRiskFilter,
                          accountFilter,
                          countryFilter,
                          sortField,
                          sortOrder,
                          holidayFilter,
                        );
                      } catch (error: any) {
                        messageApi.error(
                          error?.response?.data?.detail || "操作失败",
                        );
                        return Promise.reject();
                      }
                    },
                  });
                }}
              >
                强制补货
              </Button>
            )}
            {record.is_discontinued === true || record.is_holiday ? (
              // 已标记 → 显示「取消标记」（不再显示节日文本，节日内容见「节日标签」列）。
              // 确认统一用页面中心 Modal；**不再使用锚定 Popconfirm**（曾与外层 Modal 双弹窗）。
              <Button
                type="link"
                size="small"
                danger
                onClick={() => {
                  Modal.confirm({
                    title: `取消标记：${record.asin}${record.account ? "（" + record.account + "）" : ""}`,
                    content: confirmMessage(
                      "取消标记（同时清除节日/停售标记；若节日来自自动打标，会一并抑制该标签，自动打标不会再自动加回）",
                    ),
                    okText: "确定取消",
                    cancelText: "关闭",
                    okButtonProps: { danger: true },
                    onOk: async () => {
                      try {
                        const res: any = await inventoryApi.markProductStatus(
                          [record.id],
                          "clear",
                        );
                        const n = res?.data?.data?.suppressed_tags;
                        messageApi.success(
                          n ? `已取消标记（并清除 ${n} 条节日标签）` : "已取消标记",
                        );
                        refreshInventory();
                      } catch (error: any) {
                        messageApi.error(
                          error?.response?.data?.detail || "操作失败",
                        );
                      }
                    },
                  });
                }}
              >
                取消标记
              </Button>
            ) : (
              // 未标记 → 「标记」：页面中心 Modal 内选择标记类型（含停售），不再用锚定下拉菜单
              <Button
                type="link"
                size="small"
                onClick={() => {
                  let markType = "圣诞";
                  Modal.confirm({
                    title: `标记：${record.asin}${record.account ? "（" + record.account + "）" : ""}`,
                    content: (
                      <div>
                        <p style={{ marginBottom: 6 }}>
                          标记后该行将从断货风险TOP10、冗余库存TOP10及数量统计中排除。
                        </p>
                        <Radio.Group
                          defaultValue="圣诞"
                          onChange={(e) => (markType = e.target.value)}
                        >
                          <Radio.Button value="圣诞">圣诞</Radio.Button>
                          <Radio.Button value="万圣">万圣</Radio.Button>
                          <Radio.Button value="跨年">跨年</Radio.Button>
                          <Radio.Button value="其他">其他</Radio.Button>
                          <Radio.Button value="__discontinued__">
                            停售
                          </Radio.Button>
                        </Radio.Group>
                        <p style={{ marginTop: 6, marginBottom: 0, color: "#999", fontSize: 12 }}>
                          若本行为汇总行，将一并作用于其共享库存子行。
                        </p>
                      </div>
                    ),
                    okText: "确认标记",
                    cancelText: "取消",
                    onOk: async () => {
                      try {
                        if (markType === "__discontinued__") {
                          await inventoryApi.markProductStatus(
                            [record.id],
                            "discontinued",
                          );
                          messageApi.success("已标记为停售");
                        } else {
                          await inventoryApi.markProductStatus(
                            [record.id],
                            "holiday",
                            markType,
                          );
                          messageApi.success(`已标记为${markType}`);
                        }
                        refreshInventory();
                      } catch (error: any) {
                        messageApi.error(
                          error?.response?.data?.detail || "操作失败",
                        );
                        return Promise.reject();
                      }
                    },
                  });
                }}
              >
                标记
              </Button>
            )}
          </Space>
        );
      },
    },
  ];

  // 主行 / 子行共用的产品图片渲染（子行列包装会替换主 render，需单独分支调用）
  const renderProductImage = (url?: string) => {
    const fallback = (
      <div
        style={{
          width: 40,
          height: 40,
          background: "#f0f0f0",
          borderRadius: 4,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <ImageIcon size={16} color="#ccc" />
      </div>
    );
    return url ? (
      <Image
        src={url}
        width={40}
        height={40}
        style={{ objectFit: "cover", borderRadius: 4 }}
        preview={{ mask: false }}
        loading="lazy"
        fallback="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0MCIgaGVpZ2h0PSI0MCI+PHJlY3Qgd2lkdGg9IjQwIiBoZWlnaHQ9IjQwIiBmaWxsPSIjZjBmMGYwIi8+PC9zdmc+"
      />
    ) : (
      fallback
    );
  };

  const CHILD_REAL_FIELDS = new Set([
    "asin",
    "sku",
    "product_name",
    "account",
    "country",
    "local_inventory",
    "gross_margin",
    "return_rate_30d",
    "return_rate_60d",
    "daily_sales",
    "sales_7d",
    "sales_14d",
    "daily_avg_7d",
    "daily_avg_14d",
    "force_replenish",
    "tag_locked",
    "is_holiday",
  ]);

  const childWrappedColumns = useMemo(() => {
    const visibleCols = inventoryColumns.filter((col) => {
      const key = (col as any).key;
      if (key === "_expand" || key === "action") return true;
      return columnVisibility[key] !== false;
    });
    return visibleCols.map((col) => {
      const colType = col as any;
      const dataKey = colType.dataIndex as string;
      const originalRender = colType.render;
      const colKey = colType.key as string;
      const isFrozen =
        colType.fixed === "left" ||
        colType.fixed === "right" ||
        frozenColumns.has(colKey);
      return {
        ...colType,
        fixed: isFrozen ? colType.fixed || "left" : undefined,
        render: (val: any, record: any, index: number) => {
          if (!record._isChild) {
            return originalRender
              ? originalRender(val, record, index)
              : (val ?? "-");
          }
          if (!dataKey) return <span style={{ color: "#ccc" }}>-</span>;
          if (dataKey === "gross_margin") {
            if (val === null || val === undefined)
              return <span style={{ color: "#ccc" }}>-</span>;
            return (
              <span
                style={{
                  color: val >= 0 ? "#52c41a" : "#cf1322",
                  fontWeight: 500,
                }}
              >
                {(val * 100).toFixed(1)}%
              </span>
            );
          }
          if (dataKey.startsWith("return_rate_")) {
            // 子行退货率：与主行口径一致，小数 → 百分比
            if (val === null || val === undefined)
              return <span style={{ color: "#ccc" }}>-</span>;
            return (
              <span
                style={{
                  color: val > 0 ? "#cf1322" : "#999",
                  fontWeight: 500,
                }}
              >
                {(val * 100).toFixed(2)}%
              </span>
            );
          }
          if (dataKey === "daily_sales") {
            if (val == null || val === undefined)
              return <span style={{ color: "#ccc" }}>-</span>;
            return <span style={{ fontWeight: 500 }}>{val.toFixed(2)}</span>;
          }
          if (dataKey === "tag_locked") {
            // 子行与父行同产品维度（c.asin == 父 asin），标识应一致；点击同样可解除
            return val === 1 ? (
              <Tooltip title="该店铺行有人工锁定标签（K5）。点击查看并解除锁定。">
                <Tag
                  color="purple"
                  icon={<Lock size={12} />}
                  style={{ cursor: "pointer" }}
                  onClick={() => openLockModal(record.asin, record.account)}
                >
                  人工锁定
                </Tag>
              </Tooltip>
            ) : (
              <span style={{ color: "#ccc" }}>-</span>
            );
          }
          if (dataKey === "image_url") {
            // 子行与父行同产品：同样渲染图片（后端 get_summary_children 已返回 image_url）
            return renderProductImage(val);
          }
          if (dataKey === "force_replenish") {
            return val === 1 ? (
              <Tag color="gold">强制补货</Tag>
            ) : (
              <span style={{ color: "#ccc" }}>-</span>
            );
          }
          if (CHILD_REAL_FIELDS.has(dataKey)) {
            return val ?? "-";
          }
          return <span style={{ color: "#ccc" }}>-</span>;
        },
      };
    });
  }, [inventoryColumns, columnVisibility, frozenColumns]);

  /** 表格总宽 = 当前可见列宽之和。
   *  替代硬编码 1620：列可增删/隐藏时保持与真实列宽一致，
   *  避免 fixed 布局下 max(1620, 列宽和) 造成的列挤压与右侧空白拖拽区。
   *  注意：返回值为 number，作为 useEffect 依赖时按值比较，
   *  不会因 childWrappedColumns 每次 render 换新引用而重复触发。 */
  const tableScrollX = useMemo(
    () =>
      childWrappedColumns.reduce<number>(
        (sum, col) => sum + (Number((col as any).width) || 120),
        0,
      ),
    [childWrappedColumns],
  );

  const inboundColumns: ColumnsType<InboundDetail> = [
    {
      title: "货件单号",
      dataIndex: "shipment_id",
      key: "shipment_id",
      width: 180,
    },
    {
      title: "发货日期",
      dataIndex: "ship_date",
      key: "ship_date",
      width: 110,
      render: (val: string | null) => val || "-",
    },
    {
      title: "数量",
      dataIndex: "quantity",
      key: "quantity",
      width: 80,
      align: "right",
      render: (val: number) => formatNumber(val),
    },
    {
      title: "运输方式",
      dataIndex: "transport_method",
      key: "transport_method",
      width: 100,
      render: (val: string) => val || "-",
    },
    {
      title: "预计到港时间",
      dataIndex: "estimated_arrival_date",
      key: "estimated_arrival_date",
      width: 120,
      render: (val: string | null) => val || "-",
    },
    {
      title: "预计可售时间",
      dataIndex: "estimated_available_date",
      key: "estimated_available_date",
      width: 120,
      render: (_val: string | null, record: InboundDetail) => {
        const arrival = record.estimated_arrival_date;
        if (!arrival || arrival === "-" || arrival.trim() === "") return "-";
        const d = new Date(arrival);
        if (isNaN(d.getTime())) return "-";
        d.setDate(d.getDate() + 7);
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, "0");
        const day = String(d.getDate()).padStart(2, "0");
        return `${y}-${m}-${day}`;
      },
    },
  ];

  // ==================== Render ====================

  const snapshotDate = overviewData?.snapshot_date || "";

  // 按大区分组：国家筛选面板（级联）
  const countryGroups = useMemo<CascaderGroup[]>(
    () => buildGroupedOptions(countryOptions, getContinent),
    [countryOptions],
  );
  // 按大区分组：店铺筛选面板（随国家联动变化的 accountOptions）
  const storeGroups = useMemo<CascaderGroup[]>(
    () =>
      buildGroupedOptions(accountOptions, (v) => {
        const found = accountOptions.find((o) => o.value === v);
        return getStoreContinentFromLabel(found?.label ?? v);
      }),
    [accountOptions],
  );

  // 表格横向滚动条（置于分页控件上方）所需的引用与同步逻辑
  const hScrollAreaRef = useRef<HTMLDivElement | null>(null); // 包裹 Table，用于捕获滚动与定位内部滚动容器
  const hScrollBarRef = useRef<HTMLDivElement | null>(null); // 自定义横向滚动条
  const hScrollFillerRef = useRef<HTMLDivElement | null>(null); // 滚动条内部占位，宽度=表格内容宽度
  const syncingHScroll = useRef(false);
  const hScrollTicking = useRef(false); // 横向滚动同步按帧节流，保证 500条/页 大表拖拽丝滑

  // 定位表格实际的横向滚动容器（优先最外层 .ant-table-content，其次 .ant-table-body）
  const getHScrollEl = (): HTMLElement | null => {
    const root = hScrollAreaRef.current;
    if (!root) return null;
    const content = root.querySelector<HTMLElement>(".ant-table-content");
    const body = root.querySelector<HTMLElement>(".ant-table-body");
    if (content && content.scrollWidth > content.clientWidth) return content;
    if (body && body.scrollWidth > body.clientWidth) return body;
    return content || body;
  };

  // 自定义滚动条滚动 -> 同步表格横向位置（按帧节流）
  // 优化（C1）：进入即比对「条/表」当前 scrollLeft，
  // 差值 < 1px 视为已同步直接返回，打断「条→表→条」的冗余同步回合，
  // 把每次拖拽手势的 repaint 从 2 次压到 1 次，缓解 500 行大表横向卡顿。
  // 快拖优化（200 行实测）：大步长 scrollLeft → 单帧大面积栅格化 + sticky 重绘
  // → 掉帧 → 用户拖更多 → 步长更大的正反馈（「越快越卡」）。改为 lerp 平滑：
  // 每帧向目标收敛 45% 且单帧限步 120px，把大步分摊成多个小步，
  // 单帧栅格化面积恒定，快拖帧率不再随速度恶化；停手后 ~2-3 帧滑到准位。
  const handleControlScroll = () => {
    const bar = hScrollBarRef.current;
    if (!bar) return;
    const el = getHScrollEl();
    if (!el) return;
    if (Math.abs(el.scrollLeft - bar.scrollLeft) < 1) return;
    if (syncingHScroll.current || hScrollTicking.current) return;
    hScrollTicking.current = true;
    requestAnimationFrame(() => {
      hScrollTicking.current = false;
      if (syncingHScroll.current) return;
      const el2 = getHScrollEl();
      if (!el2) return;
      const target = bar.scrollLeft;
      const diff = target - el2.scrollLeft;
      const maxStep = 120;
      const next =
        Math.abs(diff) <= maxStep
          ? target
          : el2.scrollLeft + Math.sign(diff) * Math.max(maxStep, Math.abs(diff) * 0.45 > maxStep ? maxStep : Math.abs(diff) * 0.45);
      syncingHScroll.current = true;
      el2.scrollLeft = next;
      syncingHScroll.current = false;
      // 未到位：继续下一帧收敛（拖动中 target 持续更新，自然跟随）
      if (Math.abs(el2.scrollLeft - target) > 1) handleControlScroll();
    });
  };

  // 表格横向滚动 -> 同步自定义滚动条位置（按帧节流）
  // 优化（C1）：同上，差值 < 1px 直接返回，避免「表→条→表」冗余回合。
  const handleAreaScroll = () => {
    const el = getHScrollEl();
    if (!el) return;
    const bar = hScrollBarRef.current;
    if (!bar) return;
    if (Math.abs(el.scrollLeft - bar.scrollLeft) < 1) return;
    if (syncingHScroll.current || hScrollTicking.current) return;
    hScrollTicking.current = true;
    requestAnimationFrame(() => {
      hScrollTicking.current = false;
      if (syncingHScroll.current) return;
      const bar2 = hScrollBarRef.current;
      if (!bar2) return;
      syncingHScroll.current = true;
      bar2.scrollLeft = el.scrollLeft;
      syncingHScroll.current = false;
    });
  };

  // 数据 / 列 / 容器宽度变化后，把滚动条占位宽度校准为表格内容实际宽度
  useEffect(() => {
    const root = hScrollAreaRef.current;
    const filler = hScrollFillerRef.current;
    if (!root || !filler) return;

    const sync = () => {
      const el = getHScrollEl();
      if (!el) return;
      // + 纵向滚动条宽度：让自定义条的最大 scrollLeft 与表体完全一致，
      // 否则拖到底会差出一条纵向滚动条的宽度（约 15px）。
      const vBar = el.offsetWidth - el.clientWidth;
      const w = Math.max(el.scrollWidth + vBar, el.clientWidth);
      filler.style.width = `${w}px`;
    };

    const raf = requestAnimationFrame(sync);
    // 容器宽度变化（窗口缩放 / 侧边栏折叠）也需重新校准
    const ro = new ResizeObserver(sync);
    ro.observe(root);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [displayList, tableLoading, total, tableScrollX]);

  return (
    <div
      className="inv-page-scroll"
      style={{
        height: "100%",
        overflowY: "auto",
        overflowX: "hidden",
        padding: "0 0 0 0",
      }}
    >
      {contextHolder}

      {/* ===== 1. Page Title Bar ===== */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 24,
          flexWrap: "wrap",
          gap: 12,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 16,
            flex: 1,
            flexWrap: "wrap",
          }}
        >
          {snapshotDate && (
            <span
              style={{
                color: "#555",
                fontSize: 13,
                background: "#fafafa",
                border: "1px solid #eee",
                borderRadius: 4,
                padding: "3px 10px",
              }}
              title="数据日期为库存快照的业务日期"
            >
              数据日期: <b>{snapshotDate}</b>
            </span>
          )}
          {/* 海外仓上次同步结果：来自数据库，切页/重启后依然可见（每日 8:00 自动同步） */}
          {overseasSummary && overseasSummary.total_sku > 0 && (
            <Tooltip
              title={
                <span style={{ whiteSpace: "pre-line" }}>
                  {`已同步 SKU：${overseasSummary.total_sku} 个\n可售合计：${overseasSummary.total_qty}\n源数据日期：${overseasSummary.latest_record_date || "-"}\n最后同步：${overseasSummary.last_synced_at || "-"}`}
                </span>
              }
            >
              <span
                style={{
                  color: "#08979c",
                  fontSize: 13,
                  background: "#e6fffb",
                  border: "1px solid #b5f5ec",
                  borderRadius: 4,
                  padding: "3px 10px",
                }}
              >
                海外仓: <b>{overseasSummary.total_sku}</b> 个 ·{" "}
                {overseasSummary.latest_record_date?.slice(5) || "-"}
              </span>
            </Tooltip>
          )}
          <Button
            type="primary"
            icon={<BarChart3 size={15} />}
            loading={importLoading || calculating}
            onClick={handleImportData}
            style={{
              background: currentTheme.primary,
              borderColor: currentTheme.primary,
              marginLeft: "auto",
            }}
          >
            {calcButtonLabel}
          </Button>
          {localSummary && localSummary.total_sku > 0 && (
            <Popconfirm
              title="确定要清空所有本地仓库存数据吗？"
              onConfirm={handleClearLocalInventory}
              okText="确定"
              cancelText="取消"
            >
              <Button
                icon={<Trash2 size={14} />}
                danger
                size="small"
                loading={localLoading}
              >
                清空
              </Button>
            </Popconfirm>
          )}
          <Button
            type="primary"
            icon={<Warehouse size={15} />}
            style={{
              background: currentTheme.primary,
              borderColor: currentTheme.primary,
            }}
            onClick={() => {
              setReductionModalVisible(true);
              setReductionCountry("");
              setReductionFile(null);
              setReductionResult(null);
            }}
          >
            导入本地仓库
          </Button>
        </div>
      </div>

      {/* ===== 2. Statistics Overview Cards ===== */}
      <Spin spinning={overviewLoading}>
        <Row gutter={16} style={{ marginBottom: 24 }}>
          <Col xs={12} sm={12} md={6}>
            <Card
              size="small"
              bordered={false}
              style={{
                borderRadius: 8,
                cursor: "pointer",
                transition: "all 0.2s ease",
                border: "1px solid #f0f0f0",
              }}
              styles={{ body: { padding: "16px 20px" } }}
              onClick={() => handleTableRiskFilterChange([])}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = "#1890ff";
                e.currentTarget.style.boxShadow =
                  "0 2px 8px rgba(24, 144, 255, 0.15)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = "#f0f0f0";
                e.currentTarget.style.boxShadow = "none";
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  <div
                    style={{ fontSize: 12, color: "#8c8c8c", marginBottom: 4 }}
                  >
                    总SKU数
                  </div>
                  <div
                    style={{
                      fontSize: 28,
                      fontWeight: 700,
                      color: "#1890ff",
                      lineHeight: 1,
                    }}
                  >
                    {overviewData?.total_sku ?? 0}
                  </div>
                </div>
                <BarChart3 size={32} color="#1890ff" style={{ opacity: 0.8 }} />
              </div>
            </Card>
          </Col>
          <Col xs={12} sm={12} md={6}>
            <Card
              size="small"
              bordered={false}
              style={{
                borderRadius: 8,
                cursor: "pointer",
                transition: "all 0.2s ease",
                border: tableRiskFilter?.includes("red")
                  ? "2px solid #cf1322"
                  : "1px solid #f0f0f0",
                background: tableRiskFilter?.includes("red")
                  ? "#fff1f0"
                  : "white",
              }}
              styles={{ body: { padding: "16px 20px" } }}
              onClick={() => {
                const newFilter = tableRiskFilter?.includes("red")
                  ? tableRiskFilter.filter((f) => f !== "red")
                  : [...(tableRiskFilter || []), "red"];
                handleTableRiskFilterChange(
                  newFilter.length > 0 ? newFilter : [],
                );
              }}
              onMouseEnter={(e) => {
                if (!tableRiskFilter?.includes("red")) {
                  e.currentTarget.style.borderColor = "#cf1322";
                  e.currentTarget.style.boxShadow =
                    "0 2px 8px rgba(207, 19, 34, 0.15)";
                }
              }}
              onMouseLeave={(e) => {
                if (!tableRiskFilter?.includes("red")) {
                  e.currentTarget.style.borderColor = "#f0f0f0";
                  e.currentTarget.style.boxShadow = "none";
                }
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  <div
                    style={{ fontSize: 12, color: "#8c8c8c", marginBottom: 4 }}
                  >
                    断货风险
                  </div>
                  <div
                    style={{
                      fontSize: 28,
                      fontWeight: 700,
                      color: "#cf1322",
                      lineHeight: 1,
                    }}
                  >
                    {overviewData?.red_count ?? 0}
                    <span
                      style={{
                        fontSize: 12,
                        fontWeight: 400,
                        color: "#cf1322",
                        marginLeft: 4,
                      }}
                    >
                      SKU
                    </span>
                  </div>
                </div>
                <AlertTriangle
                  size={32}
                  color="#cf1322"
                  style={{ opacity: 0.8 }}
                />
              </div>
            </Card>
          </Col>
          <Col xs={12} sm={12} md={6}>
            <Card
              size="small"
              bordered={false}
              style={{
                borderRadius: 8,
                cursor: "pointer",
                transition: "all 0.2s ease",
                border: tableRiskFilter?.includes("yellow")
                  ? "2px solid #fa8c16"
                  : "1px solid #f0f0f0",
                background: tableRiskFilter?.includes("yellow")
                  ? "#fff7e6"
                  : "white",
              }}
              styles={{ body: { padding: "16px 20px" } }}
              onClick={() => {
                const newFilter = tableRiskFilter?.includes("yellow")
                  ? tableRiskFilter.filter((f) => f !== "yellow")
                  : [...(tableRiskFilter || []), "yellow"];
                handleTableRiskFilterChange(
                  newFilter.length > 0 ? newFilter : [],
                );
              }}
              onMouseEnter={(e) => {
                if (!tableRiskFilter?.includes("yellow")) {
                  e.currentTarget.style.borderColor = "#fa8c16";
                  e.currentTarget.style.boxShadow =
                    "0 2px 8px rgba(250, 140, 22, 0.15)";
                }
              }}
              onMouseLeave={(e) => {
                if (!tableRiskFilter?.includes("yellow")) {
                  e.currentTarget.style.borderColor = "#f0f0f0";
                  e.currentTarget.style.boxShadow = "none";
                }
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  <div
                    style={{ fontSize: 12, color: "#8c8c8c", marginBottom: 4 }}
                  >
                    库存预警
                  </div>
                  <div
                    style={{
                      fontSize: 28,
                      fontWeight: 700,
                      color: "#fa8c16",
                      lineHeight: 1,
                    }}
                  >
                    {overviewData?.yellow_count ?? 0}
                    <span
                      style={{
                        fontSize: 12,
                        fontWeight: 400,
                        color: "#fa8c16",
                        marginLeft: 4,
                      }}
                    >
                      SKU
                    </span>
                  </div>
                </div>
                <AlertCircle
                  size={32}
                  color="#fa8c16"
                  style={{ opacity: 0.8 }}
                />
              </div>
            </Card>
          </Col>
          <Col xs={12} sm={12} md={6}>
            <Card
              size="small"
              bordered={false}
              style={{
                borderRadius: 8,
                cursor: "pointer",
                transition: "all 0.2s ease",
                border: tableRiskFilter?.includes("green")
                  ? "2px solid #52c41a"
                  : "1px solid #f0f0f0",
                background: tableRiskFilter?.includes("green")
                  ? "#f6ffed"
                  : "white",
              }}
              styles={{ body: { padding: "16px 20px" } }}
              onClick={() => {
                const newFilter = tableRiskFilter?.includes("green")
                  ? tableRiskFilter.filter((f) => f !== "green")
                  : [...(tableRiskFilter || []), "green"];
                handleTableRiskFilterChange(
                  newFilter.length > 0 ? newFilter : [],
                );
              }}
              onMouseEnter={(e) => {
                if (!tableRiskFilter?.includes("green")) {
                  e.currentTarget.style.borderColor = "#52c41a";
                  e.currentTarget.style.boxShadow =
                    "0 2px 8px rgba(82, 196, 26, 0.15)";
                }
              }}
              onMouseLeave={(e) => {
                if (!tableRiskFilter?.includes("green")) {
                  e.currentTarget.style.borderColor = "#f0f0f0";
                  e.currentTarget.style.boxShadow = "none";
                }
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  <div
                    style={{ fontSize: 12, color: "#8c8c8c", marginBottom: 4 }}
                  >
                    库存正常
                  </div>
                  <div
                    style={{
                      fontSize: 28,
                      fontWeight: 700,
                      color: "#52c41a",
                      lineHeight: 1,
                    }}
                  >
                    {overviewData?.green_count ?? 0}
                    <span
                      style={{
                        fontSize: 12,
                        fontWeight: 400,
                        color: "#52c41a",
                        marginLeft: 4,
                      }}
                    >
                      SKU
                    </span>
                  </div>
                </div>
                <CheckCircle
                  size={32}
                  color="#52c41a"
                  style={{ opacity: 0.8 }}
                />
              </div>
            </Card>
          </Col>
        </Row>
      </Spin>

      {/* ===== 2.1 Local Inventory Summary ===== */}
      {localSummary && localSummary.total_sku > 0 && (
        <Card
          size="small"
          bordered={false}
          style={{
            marginBottom: 16,
            borderRadius: 8,
            background: "#faf5ff",
            borderLeft: "4px solid #722ed1",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              flexWrap: "wrap",
              gap: 16,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              <Warehouse size={20} color="#722ed1" />
              <span style={{ fontWeight: 600, color: "#531dab" }}>
                本地仓库存
              </span>
            </div>
            <Space size="large">
              <span style={{ color: "#666" }}>
                已录入{" "}
                <b style={{ color: "#722ed1" }}>{localSummary.total_sku}</b>{" "}
                个SKU
              </span>
              <span style={{ color: "#666" }}>
                总计{" "}
                <b style={{ color: "#722ed1" }}>
                  {localSummary.total_quantity.toLocaleString()}
                </b>{" "}
                件
              </span>
              {localSummary.latest_batch_date && (
                <span style={{ color: "#999", fontSize: 12 }}>
                  上传时间: {localSummary.latest_batch_date}
                </span>
              )}
            </Space>
          </div>
        </Card>
      )}

      {/* ===== 3. TOP10 Area - Collapsible Cards ===== */}
      <Row gutter={12} style={{ marginBottom: 16 }}>
        {/* Stockout Risk TOP10 */}
        <Col xs={24} md={12}>
          <Card
            size="small"
            bordered={false}
            style={{ borderRadius: 8, overflow: "hidden" }}
            styles={{ body: { padding: 0 } }}
          >
            {/* Header - Always visible */}
            <div
              onClick={() => setStockoutCollapsed(!stockoutCollapsed)}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "10px 14px",
                cursor: "pointer",
                background: stockoutCollapsed ? "#fff" : "#fff1f0",
                transition: "background 0.2s",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <div
                  style={{
                    width: 32,
                    height: 32,
                    borderRadius: 8,
                    background: "#fff1f0",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <TrendingDown size={18} color="#cf1322" />
                </div>
                <div>
                  <div
                    style={{ fontWeight: 600, fontSize: 14, color: "#262626" }}
                  >
                    断货风险 TOP10
                  </div>
                  <div style={{ fontSize: 12, color: "#8c8c8c" }}>
                    可售天数最低的SKU
                  </div>
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {overviewData?.stockout_top10 &&
                  overviewData.stockout_top10.length > 0 && (
                    <Tag color="error" style={{ margin: 0, borderRadius: 10 }}>
                      {overviewData.stockout_top10[0]?.days_of_supply}天起
                    </Tag>
                  )}
                {stockoutCollapsed ? (
                  <ChevronRight size={16} color="#8c8c8c" />
                ) : (
                  <ChevronDown size={16} color="#cf1322" />
                )}
              </div>
            </div>

            {/* Content - Collapsible */}
            {!stockoutCollapsed &&
              overviewData?.stockout_top10 &&
              overviewData.stockout_top10.length > 0 && (
                <div
                  style={{
                    padding: "0 12px 12px",
                    borderTop: "1px solid #f0f0f0",
                  }}
                >
                  {overviewData.stockout_top10.map((item, index) => (
                    <div key={item.asin}>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          padding: "8px 4px",
                          borderBottom:
                            index < 9 ? "1px dashed #f0f0f0" : "none",
                          cursor: "pointer",
                        }}
                        onClick={() =>
                          setExpandedStockout(
                            expandedStockout === item.asin ? null : item.asin,
                          )
                        }
                      >
                        <span
                          style={{
                            width: 20,
                            height: 20,
                            borderRadius: 4,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            fontSize: 11,
                            fontWeight: 600,
                            marginRight: 8,
                            background: index < 3 ? "#cf1322" : "#f0f0f0",
                            color: index < 3 ? "#fff" : "#8c8c8c",
                          }}
                        >
                          {index + 1}
                        </span>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div
                            style={{
                              fontWeight: 500,
                              fontSize: 12,
                              color: "#262626",
                              marginBottom: 2,
                            }}
                          >
                            {item.asin}
                          </div>
                          <div
                            style={{
                              fontSize: 11,
                              color: "#595959",
                              lineHeight: 1.4,
                              wordBreak: "break-all",
                            }}
                          >
                            {item.product_name || "-"}
                          </div>
                        </div>
                        <div
                          style={{
                            textAlign: "right",
                            flexShrink: 0,
                            display: "flex",
                            flexDirection: "row",
                            gap: 6,
                            alignItems: "center",
                          }}
                        >
                          <span
                            style={{
                              fontWeight: 700,
                              fontSize: 13,
                              color: "#cf1322",
                              background: "#fff1f0",
                              padding: "2px 8px",
                              borderRadius: 4,
                            }}
                          >
                            {item.days_of_supply}天
                          </span>
                          {(item.suggest_qty ?? 0) > 0 && (
                            <Tooltip
                              title={
                                item.reason || `建议补货 ${item.suggest_qty} 件`
                              }
                            >
                              <span
                                style={{
                                  fontWeight: 600,
                                  fontSize: 11,
                                  color: "#d46b08",
                                  background: "#fff7e6",
                                  padding: "2px 6px",
                                  borderRadius: 4,
                                  cursor: "pointer",
                                  border: "1px solid #ffd8bf",
                                }}
                              >
                                补货+{item.suggest_qty}
                              </span>
                            </Tooltip>
                          )}
                        </div>
                      </div>
                      {/* 展开详情 */}
                      {expandedStockout === item.asin && (
                        <div
                          style={{
                            padding: "10px 8px 10px 32px",
                            background: "#fafafa",
                            borderRadius: 4,
                            marginBottom: 4,
                            fontSize: 11,
                            color: "#666",
                          }}
                        >
                          <div
                            style={{
                              display: "grid",
                              gridTemplateColumns: "1fr 1fr",
                              gap: "6px 12px",
                            }}
                          >
                            <div>
                              <span style={{ color: "#8c8c8c" }}>店铺: </span>
                              <span style={{ color: "#262626" }}>
                                {item.account || "-"}
                              </span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>国家: </span>
                              <span style={{ color: "#262626" }}>
                                {item.country || "-"}
                              </span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>
                                FBA库存:{" "}
                              </span>
                              <span style={{ color: "#262626" }}>
                                {formatNumber(item.fba_stock)}
                              </span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>
                                日均销量:{" "}
                              </span>
                              <span style={{ color: "#262626" }}>
                                {item.daily_sales != null
                                  ? item.daily_sales.toFixed(2)
                                  : "-"}
                              </span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>
                                预计断货:{" "}
                              </span>
                              <span
                                style={{ color: "#cf1322", fontWeight: 500 }}
                              >
                                {item.stockout_date || "-"}
                              </span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>总库存: </span>
                              <span style={{ color: "#262626" }}>
                                {formatNumber(item.total_stock)}
                              </span>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
          </Card>
        </Col>

        {/* Overstock TOP10 */}
        <Col xs={24} md={12}>
          <Card
            size="small"
            bordered={false}
            style={{ borderRadius: 8, overflow: "hidden" }}
            styles={{ body: { padding: 0 } }}
          >
            {/* Header - Always visible */}
            <div
              onClick={() => setOverstockCollapsed(!overstockCollapsed)}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "10px 14px",
                cursor: "pointer",
                background: overstockCollapsed ? "#fff" : "#fff7e6",
                transition: "background 0.2s",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <div
                  style={{
                    width: 32,
                    height: 32,
                    borderRadius: 8,
                    background: "#fff7e6",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <TrendingUp size={18} color="#fa8c16" />
                </div>
                <div>
                  <div
                    style={{ fontWeight: 600, fontSize: 14, color: "#262626" }}
                  >
                    冗余库存 TOP10
                  </div>
                  <div style={{ fontSize: 12, color: "#8c8c8c" }}>
                    12月以上库龄最多
                  </div>
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {overviewData?.overstock_top10 &&
                  overviewData.overstock_top10.length > 0 && (
                    <Tag
                      color="warning"
                      style={{ margin: 0, borderRadius: 10 }}
                    >
                      {formatNumber(
                        overviewData.overstock_top10[0]?.age_12_plus,
                      )}
                      件
                    </Tag>
                  )}
                {overstockCollapsed ? (
                  <ChevronRight size={16} color="#8c8c8c" />
                ) : (
                  <ChevronDown size={16} color="#fa8c16" />
                )}
              </div>
            </div>

            {/* Content - Collapsible */}
            {!overstockCollapsed &&
              overviewData?.overstock_top10 &&
              overviewData.overstock_top10.length > 0 && (
                <div
                  style={{
                    padding: "0 12px 12px",
                    borderTop: "1px solid #f0f0f0",
                  }}
                >
                  {overviewData.overstock_top10.map((item, index) => (
                    <div key={item.asin}>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          padding: "8px 4px",
                          borderBottom:
                            index < 9 ? "1px dashed #f0f0f0" : "none",
                          cursor: "pointer",
                        }}
                        onClick={() =>
                          setExpandedOverstock(
                            expandedOverstock === item.asin ? null : item.asin,
                          )
                        }
                      >
                        <span
                          style={{
                            width: 20,
                            height: 20,
                            borderRadius: 4,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            fontSize: 11,
                            fontWeight: 600,
                            marginRight: 8,
                            background: index < 3 ? "#fa8c16" : "#f0f0f0",
                            color: index < 3 ? "#fff" : "#8c8c8c",
                          }}
                        >
                          {index + 1}
                        </span>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div
                            style={{
                              fontWeight: 500,
                              fontSize: 12,
                              color: "#262626",
                              marginBottom: 2,
                            }}
                          >
                            {item.asin}
                          </div>
                          <div
                            style={{
                              fontSize: 11,
                              color: "#595959",
                              lineHeight: 1.4,
                              wordBreak: "break-all",
                            }}
                          >
                            {item.product_name || "-"}
                          </div>
                        </div>
                        <div style={{ textAlign: "right", flexShrink: 0 }}>
                          <span
                            style={{
                              fontWeight: 700,
                              fontSize: 13,
                              color: "#fa8c16",
                              background: "#fff7e6",
                              padding: "2px 8px",
                              borderRadius: 4,
                            }}
                          >
                            {formatNumber(item.age_12_plus)}件
                          </span>
                        </div>
                      </div>
                      {/* 展开详情 */}
                      {expandedOverstock === item.asin && (
                        <div
                          style={{
                            padding: "10px 8px 10px 32px",
                            background: "#fafafa",
                            borderRadius: 4,
                            marginBottom: 4,
                            fontSize: 11,
                            color: "#666",
                          }}
                        >
                          <div
                            style={{
                              display: "grid",
                              gridTemplateColumns: "1fr 1fr",
                              gap: "6px 12px",
                            }}
                          >
                            <div>
                              <span style={{ color: "#8c8c8c" }}>店铺: </span>
                              <span style={{ color: "#262626" }}>
                                {item.account || "-"}
                              </span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>国家: </span>
                              <span style={{ color: "#262626" }}>
                                {item.country || "-"}
                              </span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>总库存: </span>
                              <span
                                style={{ color: "#fa8c16", fontWeight: 600 }}
                              >
                                {formatNumber(item.total_stock)}
                              </span>
                            </div>
                            <div></div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>
                                12月以上:{" "}
                              </span>
                              <span style={{ color: "#262626" }}>
                                {formatNumber(item.age_12_plus)}
                              </span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>9-12月: </span>
                              <span style={{ color: "#262626" }}>
                                {formatNumber(item.age_9_12)}
                              </span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>6-9月: </span>
                              <span style={{ color: "#262626" }}>
                                {formatNumber(item.age_6_9)}
                              </span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>3-6月: </span>
                              <span style={{ color: "#262626" }}>
                                {formatNumber(item.age_3_6)}
                              </span>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
          </Card>
        </Col>
      </Row>

      {/* ===== 4. Search & Filter Bar ===== */}
      <Card
        size="small"
        style={{ marginBottom: 24, borderRadius: 8 }}
        bordered={false}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 16,
            flexWrap: "wrap",
          }}
        >
          <Input
            placeholder="搜索ASIN/SKU/品名/店铺..."
            prefix={<Search size={15} color="#bbb" />}
            allowClear
            style={{ width: 280 }}
            value={searchText}
            onChange={(e) => handleSearch(e.target.value)}
          />
          <CascadingFilter
            title="国家"
            placeholder="筛选国家（先选）"
            width={160}
            value={countryFilter}
            groups={countryGroups}
            onChange={handleCountryFilterChange}
          />
          <CascadingFilter
            title="店铺"
            placeholder="筛选店铺（后选）"
            width={210}
            value={accountFilter}
            groups={storeGroups}
            onChange={handleAccountFilterChange}
          />
          <Tooltip title="标记说明：标记为「节日」或「停售」的商品，将从断货风险TOP10、冗余库存TOP10及数量统计中自动排除。">
            <Select
              placeholder="产品状态"
              style={{ width: 140 }}
              value={holidayFilter}
              onChange={(value) => {
                setHolidayFilter(value);
                setPagination((prev) => ({ ...prev, current: 1 }));
                fetchInventoryList(
                  1,
                  pagination.pageSize,
                  searchText,
                  tableRiskFilter,
                  accountFilter,
                  countryFilter,
                  sortField,
                  sortOrder,
                  value,
                );
                fetchOverview({ holiday: value });
              }}
              options={[
                { label: "全部", value: "all" },
                { label: "正常产品", value: "only_non_holiday" },
                { label: "节日产品-全部", value: "only_holiday" },
                { label: "圣诞", value: "holiday_圣诞" },
                { label: "万圣", value: "holiday_万圣" },
                { label: "跨年", value: "holiday_跨年" },
                { label: "其他", value: "holiday_其他" },
                { label: "停售", value: "only_discontinued" },
              ]}
            />
          </Tooltip>
          <Button
            icon={<RefreshCw size={16} />}
            onClick={handleSyncFeishu}
            loading={syncingFeishu}
          >
            {syncButtonLabel}
          </Button>
          <Tooltip title="从飞书多维表拉取海外仓最新数据，按 SKU+品名 去重后覆盖写入，并回写到主行「海外仓」列">
            <Button
              icon={<Warehouse size={16} />}
              onClick={handleSyncOverseas}
              loading={syncingOverseas}
            >
              {overseasButtonLabel}
            </Button>
          </Tooltip>
          {/* 批量操作：作用于表格勾选的行（表头勾选=全选本页） */}
          <Button
            danger
            disabled={!selectedRecords.length}
            loading={batchLoading}
            onClick={batchClearMark}
          >
            批量取消标记
            {selectedRecords.length ? `(${selectedRecords.length})` : ""}
          </Button>
          <Button
            danger
            disabled={!selectedRecords.length}
            loading={batchLoading}
            onClick={batchClearForce}
          >
            批量取消强制补货
            {selectedRecords.length ? `(${selectedRecords.length})` : ""}
          </Button>
          {tableRiskFilter && tableRiskFilter.length > 0 && (
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span style={{ fontSize: 12, color: "#8c8c8c" }}>已选:</span>
              {tableRiskFilter.includes("red") && (
                <Tag
                  color="error"
                  closable
                  onClose={() =>
                    handleTableRiskFilterChange(
                      tableRiskFilter.filter((f) => f !== "red"),
                    )
                  }
                >
                  断货风险
                </Tag>
              )}
              {tableRiskFilter.includes("yellow") && (
                <Tag
                  color="warning"
                  closable
                  onClose={() =>
                    handleTableRiskFilterChange(
                      tableRiskFilter.filter((f) => f !== "yellow"),
                    )
                  }
                >
                  库存预警
                </Tag>
              )}
              {tableRiskFilter.includes("green") && (
                <Tag
                  color="success"
                  closable
                  onClose={() =>
                    handleTableRiskFilterChange(
                      tableRiskFilter.filter((f) => f !== "green"),
                    )
                  }
                >
                  库存正常
                </Tag>
              )}
              <Button
                type="link"
                size="small"
                onClick={() => handleTableRiskFilterChange([])}
                style={{ padding: 0, height: "auto" }}
              >
                清除
              </Button>
            </div>
          )}
        </div>
      </Card>

      <style>{`
        .summary-row {
          background: #f0f7ff !important;
          font-weight: 600;
        }
        .summary-row td {
          background: #f0f7ff !important;
        }
        .risk-row-red:hover td {
          background: #fff1f0 !important;
        }
        .risk-row-yellow:hover td {
          background: #fff7e6 !important;
        }
        .risk-row-green:hover td {
          background: #f6ffed !important;
        }
        /* ===== 库存明细表：只保留分页上方的自定义横向滚动条 =====
           根因：antd 5.29.3 在 .ant-table-wrapper .ant-table 上注入了
           scrollbar-color（antd/es/table/style/index.js:54，默认
           rgba(0,0,0,0.25) rgba(5,5,5,0.06)）。该属性可继承；
           Chrome ≥121 一旦命中标准滚动条属性，就会整条忽略 ::-webkit-scrollbar
           规则 —— 这正是此前 height:0 屡改无效的原因。
           对策：在本页作用域内把标准属性复位为 auto，回落到 WebKit 伪元素渲染路径。
/* 页面级纵向滚动条：隐藏 UI 但保留滚动功能。
           scrollbar-width:none 是标准属性（Chrome 121+/Firefox），命中后
           浏览器直接不渲染滚动条；::-webkit-scrollbar 兜底旧浏览器。 */
        .inv-page-scroll {
          scrollbar-width: none;
          -ms-overflow-style: none;
        }
        /* Chrome ≥145 已移除 ::-webkit-scrollbar 支持，webkit 隐藏规则失效。
           body 需要的形态：纵条保留（条数多时拖动）、横条不渲染（横向滚动
           由沉底自定义条 .table-h-scroll 以 JS 设 scrollLeft 驱动）。
           scrollbar-width 只能横纵一起控制，无法单边 → 用 overflow-x:hidden：
           横向不再渲染滚动条，溢出区域仍可通过 JS 设 scrollLeft（同步不受影响）。
           !important 用于覆盖 antd 的内联 overflow（inline 无 important）。 */
        .inv-detail-hscroll .ant-table-body {
          scrollbar-width: auto;
          overflow-x: hidden !important;
        }
        /* 合成层策略（200 行 A/B/G 实测取最优）：cell 级 will-change 的稳态
           avg 12.9ms 为各方案最优（B 移除=18.4 掉帧 6 / F 强制合成层=26+）；
           其首帧栅格化尖峰（~163ms，每次滚动过程一次）通过下方 lerp 平滑
           分摊 + 用户加载后首次拖动自然预热消化。 */
        .inv-detail-hscroll .ant-table-cell-fix-left,
        .inv-detail-hscroll .ant-table-cell-fix-right {
          will-change: transform;
        }
        .inv-detail-hscroll .ant-table-content,
        .inv-detail-hscroll .ant-table-header {
          scrollbar-width: none;
        }
        /* 合成层策略已改为 body 级 will-change:scroll-position（见上方）：
           cell 级 will-change 在 200 行/页时产生约 1200 个合成层，
           GPU 栅格化尖峰实测 163ms/帧（A/B 实验见 probe 记录），已移除。 */
        /* 隐藏 rc-table 在「scroll.x + 冻结列」时渲染的原生底部横向滚动条
           （.ant-table-sticky-scroll，内含 .ant-table-sticky-scroll-bar）。
           横向滑动统一由分页上方的自定义滚动条 .table-h-scroll 承担，
           否则冻结列场景会出现第二条横向滑条。
           注：本规则此前仅存在于工作区、从未提交，合并时被丢弃。 */
        .inv-detail-hscroll .ant-table-sticky-scroll {
          display: none;
        }
        /* height 控制横向滚动条厚度 → 0 即隐藏横向条，保留纵向条。
           故意不写 width：纵向滚动条保持浏览器默认厚度，避免与 rc-table 的
           scrollbarSize 补偿值不一致，导致表头 / 表体列错位。 */
        .inv-detail-hscroll .ant-table-body::-webkit-scrollbar,
        .inv-detail-hscroll .ant-table-content::-webkit-scrollbar,
        .inv-detail-hscroll .ant-table-header::-webkit-scrollbar {
          height: 0;
        }
        .inv-detail-hscroll .ant-table-body::-webkit-scrollbar-thumb,
        .inv-detail-hscroll .ant-table-content::-webkit-scrollbar-thumb,
        .inv-detail-hscroll .ant-table-header::-webkit-scrollbar-thumb {
          background: #c5c5c5;
          border-radius: 5px;
        }
        /* 自定义横向滚动条样式
           scrollbar-color 刻意不写（同理避免命中标准属性）；scrollbar-width
           必须显式 auto——见上方断继承注释。
           本元素不在 .ant-table 内，不会继承 antd 注入的 scrollbar-color，
           因此 WebKit 伪元素可直接生效，厚度稳定为 10px。 */
        /* 必须显式写 auto 断掉继承：祖先 .inv-page-scroll 的 scrollbar-width:none
           是继承属性，会被本元素继承 → Chrome ≥121 命中标准属性后整条忽略
           下面的 ::-webkit-scrollbar 样式 → 沉底条消失、原生条漏出。 */
        .table-h-scroll {
          /* 显式 auto 断掉可能的祖先 scrollbar-width 继承 */
          scrollbar-width: auto;
        }
        .table-h-scroll::-webkit-scrollbar {
          height: 10px;
        }
        .table-h-scroll::-webkit-scrollbar-track {
          background: #f0f0f0;
          border-radius: 5px;
        }
        .table-h-scroll::-webkit-scrollbar-thumb {
          background: #c5c5c5;
          border-radius: 5px;
        }
        .table-h-scroll::-webkit-scrollbar-thumb:hover {
          background: #a8a8a8;
        }
        /* 非 WebKit（Firefox）兜底：标准滚动条属性只能整体着色，
           无法只隐藏横向一条，故此处仅做视觉收敛，功能不受影响。 */
        @supports not selector(::-webkit-scrollbar) {
          .table-h-scroll {
            scrollbar-width: thin;
            scrollbar-color: #c5c5c5 #f0f0f0;
          }
        }
        /* ===== 级联筛选面板（领星风格） ===== */
        .inv-cascader-trigger {
          display: inline-flex;
          align-items: center;
          justify-content: space-between;
          padding: 0 8px;
          text-align: left;
        }
        .inv-cascader-trigger.has-value {
          color: #262626;
          border-color: #91caff;
        }
        .inv-cascader-trigger-text {
          flex: 1;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .inv-cascader-pop {
          position: relative;
          display: flex;
          width: 420px;
          height: 320px;
          font-size: 13px;
        }
        .inv-cascader-left {
          width: 110px;
          border-right: 1px solid #f0f0f0;
          overflow-y: auto;
          padding: 4px 0;
        }
        .inv-cascader-group {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 8px 12px;
          cursor: pointer;
          color: #595959;
          border-left: 2px solid transparent;
        }
        .inv-cascader-group:hover {
          background: #f5f5f5;
        }
        .inv-cascader-group.active {
          background: #e6f4ff;
          border-left-color: #1677ff;
          color: #1677ff;
          font-weight: 600;
        }
        .inv-cascader-group-count {
          font-size: 12px;
          color: #bfbfbf;
        }
        .inv-cascader-right {
          flex: 1;
          display: flex;
          flex-direction: column;
          padding: 8px 12px;
        }
        .inv-cascader-selectall {
          padding: 6px 0;
          border-bottom: 1px solid #f5f5f5;
          margin-bottom: 4px;
        }
        .inv-cascader-items {
          flex: 1;
          overflow-y: auto;
        }
        .inv-cascader-item {
          padding: 4px 0;
        }
        .inv-cascader-footer {
          position: absolute;
          right: 12px;
          bottom: 10px;
          display: flex;
          gap: 8px;
        }
      `}</style>
      {/* ===== 5. Inventory Detail Table ===== */}
      <Card
        title={
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              width: "100%",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Package size={18} color={currentTheme.primary} />
              <span>库存明细</span>
            </div>
            <Space size={8}>
              <Button
                icon={<RefreshCw size={14} />}
                size="small"
                onClick={handleRefresh}
                loading={tableLoading}
              >
                刷新
              </Button>
              <Button
                icon={<Download size={14} />}
                size="small"
                onClick={handleExport}
                loading={exporting}
              >
                导出
              </Button>
              <Popover
                open={columnSettingsOpen}
                onOpenChange={setColumnSettingsOpen}
                trigger="click"
                placement="bottomRight"
                title={
                  <div style={{ fontWeight: 600, fontSize: 13 }}>
                    列设置
                    <span
                      style={{
                        fontWeight: 400,
                        color: "#888",
                        marginLeft: 8,
                        fontSize: 12,
                      }}
                    >
                      （勾选显示 / 点击固定）
                    </span>
                  </div>
                }
                content={
                  <div
                    style={{ width: 240, maxHeight: 400, overflowY: "auto" }}
                  >
                    {COLUMN_META.map((col) => {
                      const isVisible = columnVisibility[col.key] !== false;
                      const isFrozen = frozenColumns.has(col.key);
                      return (
                        <div
                          key={col.key}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            padding: "6px 8px",
                            borderRadius: 6,
                            cursor: "pointer",
                            background: isVisible
                              ? isFrozen
                                ? "#f0f7ff"
                                : "transparent"
                              : "#fafafa",
                            transition: "background 0.2s",
                            marginBottom: 2,
                          }}
                          onClick={() => {
                            if (isVisible) {
                              const next = new Set(frozenColumns);
                              if (isFrozen) {
                                next.delete(col.key);
                              } else {
                                next.add(col.key);
                              }
                              setFrozenColumns(next);
                            }
                          }}
                        >
                          <Checkbox
                            checked={isVisible}
                            onChange={(e) => {
                              e.stopPropagation();
                              setColumnVisibility((prev) => ({
                                ...prev,
                                [col.key]: e.target.checked,
                              }));
                            }}
                          >
                            <span
                              style={{
                                fontSize: 13,
                                color: isVisible ? "#262626" : "#bbb",
                              }}
                            >
                              {col.label}
                            </span>
                          </Checkbox>
                          <span
                            style={{
                              fontSize: 11,
                              color: isFrozen
                                ? currentTheme.primary
                                : "#d9d9d9",
                              fontWeight: isFrozen ? 600 : 400,
                              transition: "color 0.2s",
                            }}
                          >
                            {isFrozen ? "已固定" : "固定"}
                          </span>
                        </div>
                      );
                    })}
                    <Divider style={{ margin: "8px 0" }} />
                    <div style={{ display: "flex", gap: 8 }}>
                      <Button
                        size="small"
                        block
                        onClick={() => {
                          setColumnVisibility(
                            Object.fromEntries(
                              COLUMN_META.map((c) => [c.key, true]),
                            ),
                          );
                          setFrozenColumns(
                            new Set(["_expand", "asin", "action"]),
                          );
                        }}
                      >
                        重置
                      </Button>
                      <Button
                        size="small"
                        block
                        type="primary"
                        onClick={() => setColumnSettingsOpen(false)}
                        style={{
                          background: currentTheme.primary,
                          borderColor: currentTheme.primary,
                        }}
                      >
                        完成
                      </Button>
                    </div>
                  </div>
                }
              >
                <Button icon={<Columns size={14} />} size="small">
                  列设置
                </Button>
              </Popover>
            </Space>
          </div>
        }
        size="small"
        bordered={false}
        style={{ borderRadius: 8 }}
      >
        {/* 作用域锚点：.inv-detail-hscroll 用于把页面内 <style> 中的表格
            滚动条规则收敛到本页表格，避免全局污染其他页面的 antd Table */}
        <div
          ref={hScrollAreaRef}
          className="inv-detail-hscroll"
          onScroll={handleAreaScroll}
        >
          <Table
            columns={childWrappedColumns}
            dataSource={displayList}
            rowKey={(record: any) =>
              record._isChild ? `child_${record.id}` : record.id
            }
            rowSelection={{
              selectedRowKeys,
              onChange: (keys) => setSelectedRowKeys(keys),
              // 子行不可勾选：其所属汇总行的操作已覆盖共享库存子行，避免重复操作
              getCheckboxProps: (record: any) => ({
                disabled: record._isChild === true,
              }),
            }}
            loading={tableLoading}
            pagination={false}
            onChange={handleTableChange}
            scroll={{ x: tableScrollX, y: "calc(100vh - 380px)" }}
            size="small"
            rowClassName={(record) => {
              let classes = [];
              if (
                record.summary_flag === "共享库存" ||
                record.summary_flag === "是"
              ) {
                classes.push("summary-row");
              }
              if (record.risk_level === "red") {
                classes.push("risk-row-red");
              } else if (record.risk_level === "yellow") {
                classes.push("risk-row-yellow");
              } else if (record.risk_level === "green") {
                classes.push("risk-row-green");
              }
              return classes.join(" ");
            }}
          />
        </div>
        {total > 0 && (
          <div
            style={{
              position: "sticky",
              bottom: 0,
              background: "#fff",
              borderTop: "1px solid #f0f0f0",
              boxShadow: "0 -4px 8px rgba(0,0,0,0.03)",
              zIndex: 10,
              padding: "6px 0 0",
            }}
          >
            {/* 自定义横向滚动条：控制数据表左右滑动，置于分页控件上方 */}
            <div
              ref={hScrollBarRef}
              className="table-h-scroll"
              onScroll={handleControlScroll}
              style={{
                overflowX: "auto",
                overflowY: "hidden",
                height: 14,
              }}
            >
              <div
                ref={hScrollFillerRef}
                style={{
                  height: 1,
                  width: "100%",
                  pointerEvents: "auto",
                }}
              />
            </div>
            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                padding: "12px 0 0",
              }}
            >
              {/* 每页上限收敛到 200：500 行时 rc-table 无虚拟滚动，
                  约 11500 单元格全量渲染，横向拖拽卡顿；方向 C
                  （相等守卫 + will-change 合成层）缓解后仍不如降量彻底。
                  此处封顶 200，DOM 体量减半，配合 C 已足够顺滑。 */}
              <Pagination
                current={pagination.current || 1}
                pageSize={pagination.pageSize || 20}
                total={total}
                showTotal={(t) => `共 ${t} 条`}
                showSizeChanger
                pageSizeOptions={["10", "20", "50", "100", "200"]}
                onChange={handlePageChange}
                onShowSizeChange={handlePageChange}
              />
            </div>
          </div>
        )}
      </Card>

      {/* ===== 6. Inbound Details Modal ===== */}
      <Modal
        title={
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Truck size={18} color="#722ed1" />
            <span>在途货件详情 - ASIN: {inboundAsin}</span>
          </div>
        }
        open={inboundModalVisible}
        onCancel={() => setInboundModalVisible(false)}
        footer={null}
        width={res.isMobile ? "95vw" : 860}
        centered
        styles={{
          body: { maxHeight: "68vh", overflowY: "auto", paddingTop: 8 },
        }}
      >
        {/* loading/空态给最小高度，避免数据回来后弹窗高度跳变（过场稳定） */}
        <Spin spinning={inboundLoading}>
          <div style={{ minHeight: 180 }}>
            {inboundDetails && inboundDetails.length > 0 ? (
              <Table
                columns={inboundColumns}
                dataSource={inboundDetails}
                rowKey="shipment_id"
                pagination={false}
                size="small"
                scroll={{
                  x: res.isMobile ? true : undefined,
                  y: "calc(68vh - 170px)",
                }}
              />
            ) : (
              <Empty
                description="暂无在途货件数据"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            )}
          </div>
        </Spin>
      </Modal>

      {/* ===== 7. Export Fields Modal ===== */}
      <Modal
        title={
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Download size={18} color="#1890ff" />
            <span>选择导出字段</span>
          </div>
        }
        open={exportModalVisible}
        onCancel={() => setExportModalVisible(false)}
        onOk={handleConfirmExport}
        confirmLoading={exporting}
        width={res.isMobile ? "95vw" : 600}
      >
        <div style={{ marginBottom: 16 }}>
          <Space>
            <Button
              size="small"
              onClick={() =>
                setSelectedExportFields(exportFieldOptions.map((f) => f.value))
              }
            >
              全选
            </Button>
            <Button size="small" onClick={() => setSelectedExportFields([])}>
              清空
            </Button>
            <Button
              size="small"
              onClick={() =>
                setSelectedExportFields([
                  "asin",
                  "sku",
                  "product_name",
                  "account",
                  "country",
                  "fba_stock",
                  "fba_inbound",
                  "total_stock",
                  "daily_sales",
                  "days_of_supply",
                  "risk_level",
                  "suggest_qty",
                ])
              }
            >
              默认
            </Button>
          </Space>
        </div>
        <Checkbox.Group
          value={selectedExportFields}
          onChange={(values) => setSelectedExportFields(values as string[])}
          style={{ width: "100%" }}
        >
          <Row gutter={[16, 8]}>
            {exportFieldOptions.map((field) => (
              <Col span={8} key={field.value}>
                <Checkbox value={field.value}>{field.label}</Checkbox>
              </Col>
            ))}
          </Row>
        </Checkbox.Group>
      </Modal>

      {/* ===== 8. Reduction Import Modal ===== */}
      <Modal
        title="导入本地仓库"
        open={reductionModalVisible}
        onCancel={() => {
          setReductionModalVisible(false);
          if (reductionResult) {
            fetchLocalSummary();
            fetchOverview();
            fetchInventoryList(
              1,
              pagination.pageSize,
              searchText,
              tableRiskFilter,
              accountFilter,
              countryFilter,
              sortField,
              sortOrder,
            );
          }
        }}
        footer={null}
        destroyOnClose
        width={res.isMobile ? "95vw" : 520}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div>
            <div style={{ marginBottom: 8, fontWeight: 500 }}>
              选择国家 <span style={{ color: "red" }}>*</span>
            </div>
            <Select
              style={{ width: "100%" }}
              placeholder="请选择国家"
              value={reductionCountry || undefined}
              onChange={(val) => setReductionCountry(val)}
              options={countryOptions}
            />
          </div>
          <div>
            <div style={{ marginBottom: 8, fontWeight: 500 }}>
              上传文件 <span style={{ color: "red" }}>*</span>
            </div>
            <div style={{ marginBottom: 8, fontSize: 12, color: "#fa8c16" }}>
              上传文件为补货的库存底表
            </div>
            <Upload
              accept=".xlsx,.xls"
              beforeUpload={(file) => {
                setReductionFile(file);
                return false;
              }}
              onRemove={() => setReductionFile(null)}
              maxCount={1}
            >
              <Button icon={<UploadOutlined />}>选择文件</Button>
            </Upload>
          </div>
          <Button
            type="primary"
            onClick={handleReductionImport}
            loading={reductionImporting}
            disabled={!reductionCountry || !reductionFile}
            block
          >
            导入
          </Button>
          {reductionResult && (
            <div
              style={{
                background: "#f6ffed",
                border: "1px solid #b7eb8f",
                borderRadius: 6,
                padding: 12,
                marginTop: 8,
              }}
            >
              <div style={{ fontWeight: 500, marginBottom: 8 }}>导入结果</div>
              <div>总条数：{reductionResult.total}</div>
              <div>更新：{reductionResult.updated}</div>
              <div>跳过：{reductionResult.skipped}</div>
              <Button
                type="link"
                icon={<DownloadOutlined />}
                onClick={() =>
                  handleDownloadReductionResult(reductionResult.result_file_id)
                }
                style={{ padding: 0, marginTop: 8 }}
              >
                下载导入结果
              </Button>
            </div>
          )}
        </div>
      </Modal>
      {/* 行级标签（K5）：查看/解除该店铺行的标签 —— 页面中心弹窗 */}
      <Modal
        open={lockModal.open}
        title={`人工锁定标签：${lockModal.asin}${lockModal.account ? "（" + lockModal.account + "）" : "（全部店铺）"}`}
        centered
        width={760}
        footer={null}
        onCancel={() => setLockModal((m) => ({ ...m, open: false }))}
      >
        <Table
          size="small"
          rowKey={(r: any) => r.id}
          loading={lockModal.loading}
          pagination={false}
          dataSource={lockModal.tags}
          columns={[
            {
              title: "作用域",
              dataIndex: "account",
              width: 170,
              render: (v: string) =>
                v ? (
                  <span>{v}</span>
                ) : (
                  <Tag color="blue">全部店铺</Tag>
                ),
            },
            {
              title: "类型",
              dataIndex: "tag_type",
              width: 96,
              render: (v: string) => (v === "force_replenish" ? "强制补货" : "节日"),
            },
            {
              title: "值",
              dataIndex: "tag_value",
              width: 120,
              render: (v: string) => (v === "1" ? "-" : v),
            },
            {
              title: "来源",
              dataIndex: "source",
              width: 80,
              render: (v: string) => (v === "manual" ? "人工" : "自动"),
            },
            {
              title: "状态",
              key: "st",
              width: 110,
              render: (_: any, r: any) =>
                r.active !== 1 ? (
                  <Tag>已失效</Tag>
                ) : r.locked === 1 ? (
                  <Tag color="red">人工锁定</Tag>
                ) : (
                  <Tag color="blue">自动管辖</Tag>
                ),
            },
            {
              title: "操作",
              key: "op",
              render: (_: any, r: any) => {
                if (r.active !== 1) return "-";
                if (r.tag_type === "force_replenish") {
                  return (
                    <Button
                      type="link"
                      size="small"
                      danger
                      onClick={() => releaseLockedTag(r, true)}
                    >
                      解除强制补货
                    </Button>
                  );
                }
                if (r.locked === 1) {
                  return (
                    <Space size={4}>
                      <Button
                        type="link"
                        size="small"
                        onClick={() => releaseLockedTag(r, false)}
                      >
                        解除锁定
                      </Button>
                      <Button
                        type="link"
                        size="small"
                        danger
                        onClick={() => releaseLockedTag(r, true)}
                      >
                        解除并清除
                      </Button>
                    </Space>
                  );
                }
                return "-";
              },
            },
          ]}
        />
        <p style={{ color: "#999", marginTop: 8, marginBottom: 0 }}>
          人工锁定（K5）：自动打标不会覆盖或清除锁定标签；解除后恢复自动管辖。
        </p>
      </Modal>
    </div>
  );
};

export default InventoryBot;
