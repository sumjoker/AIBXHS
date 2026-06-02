import React, { useState, useEffect, useCallback, useMemo } from "react";
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
} from "antd";
import type { TablePaginationConfig, ColumnsType } from "antd/es/table";
import type { UploadProps } from "antd";
import {
  Package,
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
} from "lucide-react";
import { UploadOutlined, DownloadOutlined } from "@ant-design/icons";
import { inventoryApi, localInventoryApi } from "../api";
import { useTheme } from "../contexts/ThemeContext";

// ==================== TypeScript Interfaces ====================

interface OverviewData {
  total_sku: number;
  red_count: number;
  yellow_count: number;
  green_count: number;
  snapshot_date: string;
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
  stockout_date: string;
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
}

interface InventoryItem {
  id: number;
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
  days_of_supply: number;
  stockout_date: string | null;
  risk_level: string;
  replenishment_status: string;
  summary_flag: string;
  local_inventory?: number;
  inspection_quantity?: number;
  suggest_qty?: number;
  replenishment_reason?: string;
  age_12_plus?: number;
  gross_margin?: number;
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

// ==================== Component ====================

const InventoryBot: React.FC = () => {
  const { currentTheme } = useTheme();
  const [messageApi, contextHolder] = message.useMessage();

  // --- Loading states ---
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [tableLoading, setTableLoading] = useState(false);
  const [importLoading, setImportLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [syncingFeishu, setSyncingFeishu] = useState(false);
  const [syncButtonLabel, setSyncButtonLabel] = useState('同步FBA在途');
  const [syncProgress, setSyncProgress] = useState(0);
  const [syncStep, setSyncStep] = useState('');

  // --- 补货计算状态（跨页面持久化） ---
  const [calculating, setCalculating] = useState(false);
  const [calcButtonLabel, setCalcButtonLabel] = useState('重新计算');

  // --- Export states ---
  const [exportModalVisible, setExportModalVisible] = useState(false);
  const [selectedExportFields, setSelectedExportFields] = useState<string[]>([
    'asin', 'sku', 'product_name', 'account', 'country', 'fba_stock', 
    'fba_inbound', 'total_stock', 'daily_sales', 'days_of_supply', 
    'risk_level', 'suggest_qty'
  ]);
  // 导出字段选项
  const exportFieldOptions = [
    { label: 'ASIN', value: 'asin' },
    { label: 'SKU', value: 'sku' },
    { label: 'FNSKU', value: 'fnsku' },
    { label: 'MSKU', value: 'msku' },
    { label: '品名', value: 'product_name' },
    { label: '店铺', value: 'account' },
    { label: '国家', value: 'country' },
    { label: '分类', value: 'category' },
    { label: '品牌', value: 'brand' },
    { label: 'FBA库存', value: 'fba_stock' },
    { label: '可售', value: 'fba_available' },
    { label: '待调仓', value: 'fba_pending_transfer' },
    { label: 'FBA预留', value: 'fba_in_transfer' },
    { label: '入库中', value: 'fba_inbound_processing' },
    { label: '在途', value: 'fba_inbound' },
    { label: '查验货件数量', value: 'inspection_quantity' },
    { label: '本地仓库存', value: 'local_inventory' },
    { label: '总库存', value: 'total_stock' },
    { label: '毛利率', value: 'gross_margin' },
    { label: '日均销量', value: 'daily_sales' },
    { label: '可售天数', value: 'days_of_supply' },
    { label: '断货时间', value: 'stockout_date' },
    { label: '风险等级', value: 'risk_level' },
    { label: '建议补货数量', value: 'suggest_qty' },
    { label: '补货原因', value: 'replenishment_reason' },
    { label: '补货状态', value: 'replenishment_status' },
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
    pageSizeOptions: ["10", "20", "50", "100"],
  });

  // --- Sorting ---
  const [sortField, setSortField] = useState<string | undefined>("suggest_qty");
  const [sortOrder, setSortOrder] = useState<string | undefined>("desc");

  // --- Table Filter State ---
  const [tableRiskFilter, setTableRiskFilter] = useState<string[] | undefined>(
    undefined,
  );

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
  const [localSummary, setLocalSummary] = useState<LocalInventorySummary | null>(null);
  const [localLoading, setLocalLoading] = useState(false);

  // --- Inspection editing states ---
  const [editingInspectionId, setEditingInspectionId] = useState<number | null>(null);
  const [editingInspectionVal, setEditingInspectionVal] = useState<number>(0);

  // --- Summary expandable states ---
  const [expandedParentIds, setExpandedParentIds] = useState<Set<number>>(new Set());
  const [childrenMap, setChildrenMap] = useState<Record<number, InventoryItem[]>>({});

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
  const COLUMN_META = useMemo(() => [
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
    { key: "local_inventory", label: "本地仓" },
    { key: "daily_sales", label: "日均销量" },
    { key: "days_of_supply", label: "可售天数" },
    { key: "stockout_date", label: "断货时间" },
    { key: "suggest_qty", label: "建议补货" },
    { key: "age_12_plus", label: "12月+库龄" },
    { key: "risk_level", label: "风险等级" },
  ], []);

  const defaultVisibility = useMemo(() =>
    Object.fromEntries(COLUMN_META.map(col => [col.key, true])),
  [COLUMN_META]);

  const [columnVisibility, setColumnVisibility] = useState<Record<string, boolean>>(defaultVisibility);
  const [columnSettingsOpen, setColumnSettingsOpen] = useState(false);
  const [frozenColumns, setFrozenColumns] = useState<Set<string>>(new Set(["_expand", "asin", "action"]));

  // ==================== Data Fetching ====================

  const fetchOverview = useCallback(async () => {
    try {
      setOverviewLoading(true);
      const response = await inventoryApi.getOverview();
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
  }, [messageApi]);

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
    ) => {
      try {
        setTableLoading(true);
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
    fetchOverview();
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
    );

    // 获取筛选选项
    inventoryApi.getFilterOptions().then(res => {
      const data = res.data?.data || res.data;
      if (data) {
        // 后端返回的是对象数组 [{value, label}]，直接使用
        setAccountOptions(data.stores || []);
        setAllAccountOptions(data.stores || []); // 保存所有店铺选项
        setCountryOptions(data.countries || []);
      }
    }).catch(err => {
      console.error('获取筛选选项失败:', err);
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
    );
  };

  const handleAccountFilterChange = (values: string[]) => {
    setAccountFilter(values || []);
    setPagination((prev) => ({ ...prev, current: 1 }));
    fetchInventoryList(
      1,
      pagination.pageSize,
      searchText,
      tableRiskFilter,
      values || [],
      countryFilter,
      sortField,
      sortOrder,
    );
  };

  const handleCountryFilterChange = (values: string[]) => {
    const selectedCountries = values || [];
    setCountryFilter(selectedCountries);
    setAccountFilter([]); // 清空店铺筛选，因为换了国家后店铺列表会变
    setPagination((prev) => ({ ...prev, current: 1 }));

    // 根据选中的国家过滤店铺列表
    if (selectedCountries.length > 0) {
      // 店铺格式是 "店铺名-国家代码"，如 "JeVenis-US"
      // 国家选项是中文，需要匹配店铺中的国家代码
      const filteredStores = allAccountOptions.filter(store => {
        // 店铺值格式: "店铺名-US" 或 "店铺名-美国"
        return selectedCountries.some(country => {
          // 尝试匹配国家代码或国家名称
          const countryCodeMap: Record<string, string> = {
            '美国': 'US',
            '英国': 'UK',
            '德国': 'DE',
            '法国': 'FR',
            '意大利': 'IT',
            '西班牙': 'ES',
            '日本': 'JP',
            '加拿大': 'CA',
            '墨西哥': 'MX',
            '澳大利亚': 'AU',
            '荷兰': 'NL',
            '瑞典': 'SE',
            '波兰': 'PL',
            '比利时': 'BE',
            '新加坡': 'SG',
            '阿联酋': 'AE',
            '印度': 'IN',
            '巴西': 'BR',
          };
          const code = countryCodeMap[country] || country;
          // 检查店铺值是否包含国家代码
          return store.value.includes(`-${code}`) || store.value.includes(`-${country}`);
        });
      });
      setAccountOptions(filteredStores);
    } else {
      // 没有选择国家，显示所有店铺
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
    );
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
    );
  };

  const handlePageChange = (page: number, pageSize: number) => {
    setPagination(prev => ({ ...prev, current: page, pageSize }));
    fetchInventoryList(
      page,
      pageSize,
      searchText,
      tableRiskFilter,
      accountFilter,
      countryFilter,
      sortField,
      sortOrder,
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
    );
  };

  const handleImportData = async () => {
    try {
      setImportLoading(true);
      const response = await inventoryApi.calculate();
      const taskId = response.data?.data?.task_id;
      if (taskId) {
        setCalculating(true);
        setCalcButtonLabel('计算中...');
        saveCalcStatusToStorage({ status: "running", taskId, step: "启动中" });
        pollCalcStatus(taskId);
      } else {
        messageApi.success("补货计算完成");
        fetchInventoryList(1, pagination.pageSize, searchText, tableRiskFilter, accountFilter, countryFilter, sortField, sortOrder);
        fetchOverview();
      }
    } catch (error: any) {
      console.error("启动计算失败:", error);
      messageApi.error(error?.response?.data?.detail || "操作失败");
    } finally {
      setImportLoading(false);
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
      await inventoryApi.updateInspectionQuantity(record.id, editingInspectionVal);
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
      setExpandedParentIds(prev => {
        const next = new Set(prev);
        next.delete(parentId);
        return next;
      });
      return;
    }
    try {
      const response = await inventoryApi.getSummaryChildren(record.asin);
      const data = response.data?.data || [];
      const children = (Array.isArray(data) ? data : []).map(c => ({ ...c, _isChild: true }));
      setChildrenMap(prev => ({ ...prev, [parentId]: children }));
      setExpandedParentIds(prev => {
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
        result.push(...childrenMap[item.id].map(c => ({ ...c, _isChild: true } as InventoryItem)));
      }
    }
    return result;
  }, [inventoryList, expandedParentIds, childrenMap]);

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
      const response = await localInventoryApi.importReduction(reductionCountry, reductionFile);
      if (response.data?.success) {
        const data = response.data.data;
        setReductionResult(data);
        messageApi.success(`导入完成：共${data.total}条，更新${data.updated}条，跳过${data.skipped}条`);

        // 自动触发增量补货计算（异步）
        if (data.snapshot_ids && data.snapshot_ids.length > 0) {
          try {
            const calcResp = await inventoryApi.calculate({ snapshot_ids: data.snapshot_ids.join(",") });
            const taskId = calcResp.data?.data?.task_id;
            if (taskId) {
              setCalculating(true);
              setCalcButtonLabel('计算中...');
              saveCalcStatusToStorage({ status: "running", taskId, step: "启动中" });
              pollCalcStatus(taskId);
            } else {
              messageApi.success("补货计算完成");
              fetchInventoryList(1, pagination.pageSize, searchText, tableRiskFilter, accountFilter, countryFilter, sortField, sortOrder);
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
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `减表导入结果_${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      messageApi.success('结果文件下载成功');
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
      sortOrder
    );
  }, [fetchOverview, fetchLocalSummary, fetchInventoryList, pagination, searchText, tableRiskFilter, accountFilter, countryFilter, sortField, sortOrder]);

  const handleExport = async () => {
    setExportModalVisible(true);
  };

  const handleConfirmExport = async () => {
    setExporting(true);
    try {
      const res = await inventoryApi.exportInventory({
        keyword: searchText || undefined,
        risk_level: tableRiskFilter && tableRiskFilter.length > 0 ? tableRiskFilter : undefined,
        account: accountFilter.length > 0 ? accountFilter : undefined,
        country: countryFilter.length > 0 ? countryFilter : undefined,
        fields: selectedExportFields.length > 0 ? selectedExportFields : undefined,
      });

      const blob = new Blob([res.data], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `库存明细_${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      messageApi.success('导出成功');
      setExportModalVisible(false);
    } catch (error) {
      messageApi.error('导出失败');
    } finally {
      setExporting(false);
    }
  };

  // 保存同步状态到 localStorage
  const saveSyncStatusToStorage = (status: any) => {
    localStorage.setItem('feishuSyncStatus', JSON.stringify({
      ...status,
      savedAt: Date.now()
    }));
  };

  // 从 localStorage 读取同步状态
  const loadSyncStatusFromStorage = () => {
    const saved = localStorage.getItem('feishuSyncStatus');
    if (saved) {
      try {
        const status = JSON.parse(saved);
        // 如果状态是30分钟内的，认为是有效的
        if (Date.now() - status.savedAt < 30 * 60 * 1000) {
          return status;
        }
      } catch (e) {
        console.error('解析同步状态失败', e);
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
        messageApi.success('同步任务已启动，请稍候...');
        // 开始轮询状态
        pollSyncStatus();
      } else {
        messageApi.warning(res.data?.data?.message || '同步任务正在运行中');
        setSyncingFeishu(false);
      }
    } catch (error) {
      messageApi.error('启动同步失败');
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
        const step = status.step || '';
        const progress = status.progress || 0;
        setSyncButtonLabel(`同步中 ${progress}% - ${step}`);
        setSyncProgress(progress);
        setSyncStep(step);
        // 继续轮询
        setTimeout(pollSyncStatus, 1500);
      } else {
        // 同步完成
        setSyncingFeishu(false);
        setSyncButtonLabel('同步FBA在途');
        setSyncProgress(0);
        setSyncStep('');
        // 清除 localStorage
        localStorage.removeItem('feishuSyncStatus');
        
        if (status?.error) {
          messageApi.error(`同步失败: ${status.error}`);
        } else if (status?.progress === 100) {
          messageApi.success(`同步完成！更新 ${status?.updated || 0} 条记录`);
        }
      }
    } catch (error) {
      setSyncingFeishu(false);
      setSyncButtonLabel('同步FBA在途');
      setSyncProgress(0);
      setSyncStep('');
      messageApi.error('获取同步状态失败');
    }
  };

  // 页面加载时检查是否有保存的同步状态
  useEffect(() => {
    const savedStatus = loadSyncStatusFromStorage();
    if (savedStatus && savedStatus.is_running) {
      // 恢复同步状态
      setSyncingFeishu(true);
      setSyncButtonLabel(`同步中 ${savedStatus.progress || 0}% - ${savedStatus.step || ''}`);
      setSyncProgress(savedStatus.progress || 0);
      setSyncStep(savedStatus.step || '');
      // 继续轮询
      pollSyncStatus();
    }
  }, []);

  // ========== 补货计算状态持久化（跨页面） ==========

  const CALC_STATUS_KEY = 'calcStatus';

  const saveCalcStatusToStorage = (status: any) => {
    localStorage.setItem(CALC_STATUS_KEY, JSON.stringify({
      ...status,
      savedAt: Date.now()
    }));
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
        console.error('解析计算状态失败', e);
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
        const progressText = task.progress != null ? ` ${task.progress}%` : '';
        setCalcButtonLabel(`计算中${progressText}`);
        setTimeout(() => pollCalcStatus(taskId), 2000);
      } else {
        setCalculating(false);
        setCalcButtonLabel('重新计算');
        localStorage.removeItem(CALC_STATUS_KEY);

        if (task?.status === "completed") {
          messageApi.success(`补货计算完成`);
          fetchInventoryList(1, pagination.pageSize, searchText, tableRiskFilter, accountFilter, countryFilter, sortField, sortOrder);
          fetchOverview();
        } else if (task?.status === "failed") {
          messageApi.warning(`补货计算失败: ${task?.error || "未知错误"}`);
        }
      }
    } catch (error) {
      setCalculating(false);
      setCalcButtonLabel('重新计算');
      localStorage.removeItem(CALC_STATUS_KEY);
      messageApi.warning('查询计算状态失败');
    }
  };

  // 页面加载时检查是否有正在运行的计算任务
  useEffect(() => {
    const savedStatus = loadCalcStatusFromStorage();
    if (savedStatus && (savedStatus.status === "running" || savedStatus.status === "pending") && savedStatus.taskId) {
      setCalculating(true);
      setCalcButtonLabel(`计算中...${savedStatus.step || ''}`);
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
            onClick={(e) => { e.stopPropagation(); handleToggleExpand(record); }}
            style={{ cursor: "pointer", display: "inline-flex", alignItems: "center" }}
          >
            {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
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
      width: 80,
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
                    {formatNumber(Math.max(0, (record.fba_stock || 0) - (record.fba_available || 0) - (record.fba_pending_transfer || 0) - (record.fba_inbound_processing || 0)))}
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
                      color: record.gross_margin != null && record.gross_margin >= 0 ? "#52c41a" : "#999",
                    }}
                  >
                    {record.gross_margin != null ? `${(record.gross_margin * 100).toFixed(1)}%` : "-"}
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
              <span style={{ fontWeight: 500 }}>{formatNumber(originalInbound)}</span>
            </div>
            <div style={{ fontSize: 12, marginBottom: 4 }}>
              <span style={{ color: "#8c8c8c" }}>已查验: </span>
              <span style={{ fontWeight: 500, color: "#722ed1" }}>{formatNumber(inspected)}</span>
            </div>
            <div style={{ fontSize: 12, borderTop: "1px solid #434343", paddingTop: 4 }}>
              <span style={{ color: "#8c8c8c" }}>实际在途: </span>
              <span style={{ fontWeight: 700, color: effectiveInbound > 0 ? "#1890ff" : "#999" }}>
                {formatNumber(effectiveInbound)}
              </span>
            </div>
          </div>
        );
        return (
          <Tooltip title={tooltipContent} color="#262626">
            <span style={{ color: effectiveInbound > 0 ? "#1890ff" : "#999", fontWeight: effectiveInbound > 0 ? 600 : 400 }}>
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
            style={{ color: qty > 0 ? "#722ed1" : "#d9d9d9", fontWeight: qty > 0 ? 600 : 400, cursor: "pointer" }}
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
      render: (val: number | undefined, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return <span style={{ color: "#999" }}>-</span>;
        }
        if (val === null || val === undefined) return "-";
        return (
          <span style={{ color: val >= 0 ? "#52c41a" : "#cf1322", fontWeight: 500 }}>
            {(val * 100).toFixed(1)}%
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
      title: "日均销量",
      dataIndex: "daily_sales",
      key: "daily_sales",
      width: 100,
      align: "right",
      render: (val: number, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return <span style={{ color: "#999" }}>-</span>;
        }
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
            <Tooltip title={record.replenishment_reason || `建议补货 ${qty} 件`}>
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
        if (val == null || val === 0) return <span style={{ color: "#d9d9d9" }}>-</span>;
        return <span style={{ color: val > 0 ? "#cf1322" : undefined, fontWeight: val > 0 ? 600 : undefined }}>{val.toLocaleString()}</span>;
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
      title: "操作",
      key: "action",
      width: 120,
      fixed: "right",
      render: (_: any, record: InventoryItem) => {
        if (record.summary_flag === "共享库存") {
          return null;
        }
        return (
          <Button
            type="link"
            size="small"
            icon={<Truck size={14} />}
            onClick={() => handleViewInbound(record.asin, record.account)}
          >
            在途详情
          </Button>
        );
      },
    },
  ];

  const CHILD_REAL_FIELDS = new Set(["asin", "sku", "product_name", "account", "country", "local_inventory", "gross_margin", "daily_sales"]);

  const childWrappedColumns = useMemo(() => {
    const visibleCols = inventoryColumns.filter(col => {
      const key = (col as any).key;
      if (key === "_expand" || key === "action") return true;
      return columnVisibility[key] !== false;
    });
    return visibleCols.map(col => {
      const colType = col as any;
      const dataKey = colType.dataIndex as string;
      const originalRender = colType.render;
      const colKey = colType.key as string;
      const isFrozen = colType.fixed === "left" || colType.fixed === "right" || frozenColumns.has(colKey);
      return {
        ...colType,
        fixed: isFrozen ? (colType.fixed || "left") : undefined,
        render: (val: any, record: any, index: number) => {
          if (!record._isChild) {
            return originalRender ? originalRender(val, record, index) : (val ?? "-");
          }
          if (!dataKey) return <span style={{ color: "#ccc" }}>-</span>;
          if (dataKey === "gross_margin") {
            if (val === null || val === undefined) return <span style={{ color: "#ccc" }}>-</span>;
            return <span style={{ color: val >= 0 ? "#52c41a" : "#cf1322", fontWeight: 500 }}>{(val * 100).toFixed(1)}%</span>;
          }
          if (dataKey === "daily_sales") {
            if (val == null || val === undefined) return <span style={{ color: "#ccc" }}>-</span>;
            return <span style={{ fontWeight: 500 }}>{val.toFixed(2)}</span>;
          }
          if (CHILD_REAL_FIELDS.has(dataKey)) {
            return val ?? "-";
          }
          return <span style={{ color: "#ccc" }}>-</span>;
        },
      };
    });
  }, [inventoryColumns, columnVisibility, frozenColumns]);

  const inboundColumns: ColumnsType<InboundDetail> = [
    {
      title: "货件单号",
      dataIndex: "shipment_id",
      key: "shipment_id",
      width: 180,
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

  return (
    <div style={{ height: "100%", overflowY: "auto", overflowX: "hidden", padding: "0 0 24px 0" }}>
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
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Package color={currentTheme.primary} size={32} />
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>
            库存机器人
          </h1>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          {snapshotDate && (
            <span style={{ color: "#888", fontSize: 13 }}>
              数据更新时间: {snapshotDate}
            </span>
          )}
          <Upload
            beforeUpload={handleFileUpload}
            showUploadList={false}
            accept=".xlsx,.xls"
          >
            <Button icon={<UploadIcon size={15} />} loading={importLoading}>
              导入Excel
            </Button>
          </Upload>
          <Button
            type="primary"
            icon={<BarChart3 size={15} />}
            loading={importLoading || calculating}
            onClick={handleImportData}
            style={{
              background: currentTheme.primary,
              borderColor: currentTheme.primary,
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
          }}>
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
              bodyStyle={{ padding: "16px 20px" }}
              onClick={() => handleTableRiskFilterChange([])}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = "#1890ff";
                e.currentTarget.style.boxShadow = "0 2px 8px rgba(24, 144, 255, 0.15)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = "#f0f0f0";
                e.currentTarget.style.boxShadow = "none";
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div>
                  <div style={{ fontSize: 12, color: "#8c8c8c", marginBottom: 4 }}>总SKU数</div>
                  <div style={{ fontSize: 28, fontWeight: 700, color: "#1890ff", lineHeight: 1 }}>
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
                border: tableRiskFilter?.includes("red") ? "2px solid #cf1322" : "1px solid #f0f0f0",
                background: tableRiskFilter?.includes("red") ? "#fff1f0" : "white",
              }}
              bodyStyle={{ padding: "16px 20px" }}
              onClick={() => {
                const newFilter = tableRiskFilter?.includes("red")
                  ? tableRiskFilter.filter(f => f !== "red")
                  : [...(tableRiskFilter || []), "red"];
                handleTableRiskFilterChange(newFilter.length > 0 ? newFilter : []);
              }}
              onMouseEnter={(e) => {
                if (!tableRiskFilter?.includes("red")) {
                  e.currentTarget.style.borderColor = "#cf1322";
                  e.currentTarget.style.boxShadow = "0 2px 8px rgba(207, 19, 34, 0.15)";
                }
              }}
              onMouseLeave={(e) => {
                if (!tableRiskFilter?.includes("red")) {
                  e.currentTarget.style.borderColor = "#f0f0f0";
                  e.currentTarget.style.boxShadow = "none";
                }
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div>
                  <div style={{ fontSize: 12, color: "#8c8c8c", marginBottom: 4 }}>断货风险</div>
                  <div style={{ fontSize: 28, fontWeight: 700, color: "#cf1322", lineHeight: 1 }}>
                    {overviewData?.red_count ?? 0}
                    <span style={{ fontSize: 12, fontWeight: 400, color: "#cf1322", marginLeft: 4 }}>SKU</span>
                  </div>
                </div>
                <AlertTriangle size={32} color="#cf1322" style={{ opacity: 0.8 }} />
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
                border: tableRiskFilter?.includes("yellow") ? "2px solid #fa8c16" : "1px solid #f0f0f0",
                background: tableRiskFilter?.includes("yellow") ? "#fff7e6" : "white",
              }}
              bodyStyle={{ padding: "16px 20px" }}
              onClick={() => {
                const newFilter = tableRiskFilter?.includes("yellow")
                  ? tableRiskFilter.filter(f => f !== "yellow")
                  : [...(tableRiskFilter || []), "yellow"];
                handleTableRiskFilterChange(newFilter.length > 0 ? newFilter : []);
              }}
              onMouseEnter={(e) => {
                if (!tableRiskFilter?.includes("yellow")) {
                  e.currentTarget.style.borderColor = "#fa8c16";
                  e.currentTarget.style.boxShadow = "0 2px 8px rgba(250, 140, 22, 0.15)";
                }
              }}
              onMouseLeave={(e) => {
                if (!tableRiskFilter?.includes("yellow")) {
                  e.currentTarget.style.borderColor = "#f0f0f0";
                  e.currentTarget.style.boxShadow = "none";
                }
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div>
                  <div style={{ fontSize: 12, color: "#8c8c8c", marginBottom: 4 }}>库存预警</div>
                  <div style={{ fontSize: 28, fontWeight: 700, color: "#fa8c16", lineHeight: 1 }}>
                    {overviewData?.yellow_count ?? 0}
                    <span style={{ fontSize: 12, fontWeight: 400, color: "#fa8c16", marginLeft: 4 }}>SKU</span>
                  </div>
                </div>
                <AlertCircle size={32} color="#fa8c16" style={{ opacity: 0.8 }} />
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
                border: tableRiskFilter?.includes("green") ? "2px solid #52c41a" : "1px solid #f0f0f0",
                background: tableRiskFilter?.includes("green") ? "#f6ffed" : "white",
              }}
              bodyStyle={{ padding: "16px 20px" }}
              onClick={() => {
                const newFilter = tableRiskFilter?.includes("green")
                  ? tableRiskFilter.filter(f => f !== "green")
                  : [...(tableRiskFilter || []), "green"];
                handleTableRiskFilterChange(newFilter.length > 0 ? newFilter : []);
              }}
              onMouseEnter={(e) => {
                if (!tableRiskFilter?.includes("green")) {
                  e.currentTarget.style.borderColor = "#52c41a";
                  e.currentTarget.style.boxShadow = "0 2px 8px rgba(82, 196, 26, 0.15)";
                }
              }}
              onMouseLeave={(e) => {
                if (!tableRiskFilter?.includes("green")) {
                  e.currentTarget.style.borderColor = "#f0f0f0";
                  e.currentTarget.style.boxShadow = "none";
                }
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div>
                  <div style={{ fontSize: 12, color: "#8c8c8c", marginBottom: 4 }}>库存正常</div>
                  <div style={{ fontSize: 28, fontWeight: 700, color: "#52c41a", lineHeight: 1 }}>
                    {overviewData?.green_count ?? 0}
                    <span style={{ fontSize: 12, fontWeight: 400, color: "#52c41a", marginLeft: 4 }}>SKU</span>
                  </div>
                </div>
                <CheckCircle size={32} color="#52c41a" style={{ opacity: 0.8 }} />
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
                <b style={{ color: "#722ed1" }}>
                  {localSummary.total_sku}
                </b>{" "}
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
            bodyStyle={{ padding: 0 }}
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
                        <div style={{ textAlign: "right", flexShrink: 0, display: "flex", flexDirection: "row", gap: 6, alignItems: "center" }}>
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
                            <Tooltip title={item.reason || `建议补货 ${item.suggest_qty} 件`}>
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
                              <span style={{ color: "#262626" }}>{item.account || "-"}</span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>国家: </span>
                              <span style={{ color: "#262626" }}>{item.country || "-"}</span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>FBA库存: </span>
                              <span style={{ color: "#262626" }}>{formatNumber(item.fba_stock)}</span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>日均销量: </span>
                              <span style={{ color: "#262626" }}>{item.daily_sales != null ? item.daily_sales.toFixed(2) : "-"}</span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>预计断货: </span>
                              <span style={{ color: "#cf1322", fontWeight: 500 }}>{item.stockout_date || "-"}</span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>总库存: </span>
                              <span style={{ color: "#262626" }}>{formatNumber(item.total_stock)}</span>
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
            bodyStyle={{ padding: 0 }}
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
                              <span style={{ color: "#262626" }}>{item.account || "-"}</span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>国家: </span>
                              <span style={{ color: "#262626" }}>{item.country || "-"}</span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>总库存: </span>
                              <span style={{ color: "#fa8c16", fontWeight: 600 }}>{formatNumber(item.total_stock)}</span>
                            </div>
                            <div></div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>12月以上: </span>
                              <span style={{ color: "#262626" }}>{formatNumber(item.age_12_plus)}</span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>9-12月: </span>
                              <span style={{ color: "#262626" }}>{formatNumber(item.age_9_12)}</span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>6-9月: </span>
                              <span style={{ color: "#262626" }}>{formatNumber(item.age_6_9)}</span>
                            </div>
                            <div>
                              <span style={{ color: "#8c8c8c" }}>3-6月: </span>
                              <span style={{ color: "#262626" }}>{formatNumber(item.age_3_6)}</span>
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
        <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
          <Input
            placeholder="搜索ASIN/SKU/品名/店铺..."
            prefix={<Search size={15} color="#bbb" />}
            allowClear
            style={{ width: 280 }}
            value={searchText}
            onChange={(e) => handleSearch(e.target.value)}
          />
          <Select
            mode="multiple"
            placeholder="筛选国家（先选）"
            allowClear
            showSearch
            style={{ width: 140 }}
            value={countryFilter}
            onChange={handleCountryFilterChange}
            options={countryOptions}
            maxTagCount={2}
            filterOption={(input, option) =>
              (option?.label as string)
                ?.toLowerCase()
                .includes(input.toLowerCase())
            }
          />
          <Select
            mode="multiple"
            placeholder="筛选店铺（后选）"
            allowClear
            showSearch
            style={{ width: 180 }}
            value={accountFilter}
            onChange={handleAccountFilterChange}
            options={accountOptions}
            maxTagCount={2}
            filterOption={(input, option) =>
              (option?.label as string)
                ?.toLowerCase()
                .includes(input.toLowerCase())
            }
          />
          <Button
            icon={<RefreshCw size={16} />}
            onClick={handleSyncFeishu}
            loading={syncingFeishu}
          >
            {syncButtonLabel}
          </Button>
          {tableRiskFilter && tableRiskFilter.length > 0 && (
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span style={{ fontSize: 12, color: "#8c8c8c" }}>已选:</span>
              {tableRiskFilter.includes("red") && (
                <Tag
                  color="error"
                  closable
                  onClose={() => handleTableRiskFilterChange(tableRiskFilter.filter(f => f !== "red"))}
                >
                  断货风险
                </Tag>
              )}
              {tableRiskFilter.includes("yellow") && (
                <Tag
                  color="warning"
                  closable
                  onClose={() => handleTableRiskFilterChange(tableRiskFilter.filter(f => f !== "yellow"))}
                >
                  库存预警
                </Tag>
              )}
              {tableRiskFilter.includes("green") && (
                <Tag
                  color="success"
                  closable
                  onClose={() => handleTableRiskFilterChange(tableRiskFilter.filter(f => f !== "green"))}
                >
                  库存正常
                </Tag>
              )}
              <Button type="link" size="small" onClick={() => handleTableRiskFilterChange([])} style={{ padding: 0, height: "auto" }}>
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
      `}</style>
      {/* ===== 5. Inventory Detail Table ===== */}
      <Card
        title={
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
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
                    <span style={{ fontWeight: 400, color: "#888", marginLeft: 8, fontSize: 12 }}>
                      （勾选显示 / 点击固定）
                    </span>
                  </div>
                }
                content={
                  <div style={{ width: 240, maxHeight: 400, overflowY: "auto" }}>
                    {COLUMN_META.map(col => {
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
                            background: isVisible ? (isFrozen ? "#f0f7ff" : "transparent") : "#fafafa",
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
                              setColumnVisibility(prev => ({ ...prev, [col.key]: e.target.checked }));
                            }}
                          >
                            <span style={{ fontSize: 13, color: isVisible ? "#262626" : "#bbb" }}>
                              {col.label}
                            </span>
                          </Checkbox>
                          <span
                            style={{
                              fontSize: 11,
                              color: isFrozen ? currentTheme.primary : "#d9d9d9",
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
                          setColumnVisibility(Object.fromEntries(COLUMN_META.map(c => [c.key, true])));
                          setFrozenColumns(new Set(["_expand", "asin", "action"]));
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
                <Button
                  icon={<Columns size={14} />}
                  size="small"
                >
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
        <Table
          columns={childWrappedColumns}
          dataSource={displayList}
          rowKey={(record: any) => record._isChild ? `child_${record.id}` : record.id}
          loading={tableLoading}
          pagination={false}
          onChange={handleTableChange}
          scroll={{ x: 1620 }}
          size="small"
          rowClassName={(record) => {
            let classes = [];
            if (record.summary_flag === "共享库存" || record.summary_flag === "是") {
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
        {total > 0 && (
          <div style={{ display: "flex", justifyContent: "flex-end", padding: "16px 0 0" }}>
            <Pagination
              current={pagination.current || 1}
              pageSize={pagination.pageSize || 20}
              total={total}
              showTotal={(t) => `共 ${t} 条`}
              showSizeChanger
              pageSizeOptions={["10", "20", "50", "100"]}
              onChange={handlePageChange}
              onShowSizeChange={handlePageChange}
            />
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
        width={800}
      >
        <Spin spinning={inboundLoading}>
          {inboundDetails && inboundDetails.length > 0 ? (
            <Table
              columns={inboundColumns}
              dataSource={inboundDetails}
              rowKey="shipment_id"
              pagination={false}
              size="small"
            />
          ) : (
            <Empty
              description="暂无在途货件数据"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          )}
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
        width={600}
      >
        <div style={{ marginBottom: 16 }}>
          <Space>
            <Button
              size="small"
              onClick={() => setSelectedExportFields(exportFieldOptions.map(f => f.value))}
            >
              全选
            </Button>
            <Button
              size="small"
              onClick={() => setSelectedExportFields([])}
            >
              清空
            </Button>
            <Button
              size="small"
              onClick={() => setSelectedExportFields([
                'asin', 'sku', 'product_name', 'account', 'country', 'fba_stock', 
                'fba_inbound', 'total_stock', 'daily_sales', 'days_of_supply', 
                'risk_level', 'suggest_qty'
              ])}
            >
              默认
            </Button>
          </Space>
        </div>
        <Checkbox.Group
          value={selectedExportFields}
          onChange={(values) => setSelectedExportFields(values as string[])}
          style={{ width: '100%' }}
        >
          <Row gutter={[16, 8]}>
            {exportFieldOptions.map(field => (
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
            fetchInventoryList(1, pagination.pageSize, searchText, tableRiskFilter, accountFilter, countryFilter, sortField, sortOrder);
          }
        }}
        footer={null}
        destroyOnClose
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div>
            <div style={{ marginBottom: 8, fontWeight: 500 }}>选择国家 <span style={{ color: "red" }}>*</span></div>
            <Select
              style={{ width: "100%" }}
              placeholder="请选择国家"
              value={reductionCountry || undefined}
              onChange={(val) => setReductionCountry(val)}
              options={countryOptions}
            />
          </div>
          <div>
            <div style={{ marginBottom: 8, fontWeight: 500 }}>上传文件 <span style={{ color: "red" }}>*</span></div>
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
            <div style={{ background: "#f6ffed", border: "1px solid #b7eb8f", borderRadius: 6, padding: 12, marginTop: 8 }}>
              <div style={{ fontWeight: 500, marginBottom: 8 }}>导入结果</div>
              <div>总条数：{reductionResult.total}</div>
              <div>更新：{reductionResult.updated}</div>
              <div>跳过：{reductionResult.skipped}</div>
              <Button
                type="link"
                icon={<DownloadOutlined />}
                onClick={() => handleDownloadReductionResult(reductionResult.result_file_id)}
                style={{ padding: 0, marginTop: 8 }}
              >
                下载导入结果
              </Button>
            </div>
          )}
        </div>
      </Modal>
    </div>
  );
};

export default InventoryBot;
