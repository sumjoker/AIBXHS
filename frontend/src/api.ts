import axios from "axios";

const API_BASE = "/api";

// 配置 axios 实例
const apiClient = axios.create({
  baseURL: API_BASE,
  timeout: 180000,
  headers: {
    "Content-Type": "application/json",
  },
  paramsSerializer: (params) => {
    // 自定义参数序列化，处理数组参数
    const searchParams = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (Array.isArray(value)) {
        // 数组参数用同一个 key 多次传递
        value.forEach((item) => {
          if (item !== undefined && item !== null) {
            searchParams.append(key, item);
          }
        });
      } else if (value !== undefined && value !== null) {
        searchParams.append(key, value);
      }
    });
    return searchParams.toString();
  },
});

// 请求拦截器 - 自动添加 token
apiClient.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem("token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  },
);

// 响应拦截器
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    // 如果是 401 未授权，清除 token（只有当不是在登录页面时才跳转）
    if (error.response?.status === 401) {
      const url = error.config?.url || "";

      // 只有当不是登录请求且不在登录页面时才跳转
      if (
        !url.includes("/auth/login") &&
        window.location.pathname !== "/login"
      ) {
        console.warn("认证过期，跳转到登录页");
        localStorage.removeItem("token");
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  },
);

// ========== Auth API ==========
export const authApi = {
  login: (username: string, password: string) =>
    apiClient.post("/auth/login", { username, password }),

  register: (
    username: string,
    email: string,
    password: string,
    nickname?: string,
    company_name?: string,
    company_code?: string,
  ) =>
    apiClient.post("/auth/register", {
      username,
      email,
      password,
      nickname,
      company_name,
      company_code,
    }),

  getMe: () => apiClient.get("/auth/me"),

  changePassword: (oldPassword: string, newPassword: string) =>
    apiClient.post("/auth/change-password", {
      old_password: oldPassword,
      new_password: newPassword,
    }),
};

// ========== Dashboard API ==========
export const dashboardApi = {
  getStats: () => apiClient.get("/dashboard/stats"),
};

// ========== Inventory API ==========
export const inventoryApi = {
  getAlerts: () => apiClient.get("/inventory/alerts"),
  getList: () => apiClient.get("/inventory/"),
  updateStock: (id: string, data: any) =>
    apiClient.put(`/inventory/${id}`, data),

  // ========== Restock (补货) API ==========
  // 概览与列表同口径：接受与 /restock/search 相同的筛选参数
  getOverview: (params?: {
    keyword?: string;
    holiday_filter?: string;
    account?: string[];
    country?: string[];
  }) => apiClient.get("/restock/overview", { params }),
  calculate: (params?: { snapshot_ids?: string }) =>
    apiClient.post("/restock/calculate", params),
  getCalculateStatus: (taskId: string) =>
    apiClient.get(`/restock/calculate/status/${taskId}`),
  import: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiClient.post("/restock/import", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  search: (params: any) => apiClient.get("/restock/search", { params }),
  getStockoutTop10: () => apiClient.get("/restock/stockout-top10"),
  getOverstockTop10: () => apiClient.get("/restock/overstock-top10"),
  getInboundDetails: (asin: string, account?: string) =>
    apiClient.get("/restock/inbound-details", { params: { asin, account } }),
  getLatestDate: () => apiClient.get("/restock/latest-date"),
  getFilterOptions: () => apiClient.get("/restock/filter-options"),
  exportInventory: (params?: {
    keyword?: string;
    risk_level?: string[];
    account?: string[];
    country?: string[];
    fields?: string[];
  }) => {
    const searchParams = new URLSearchParams();
    if (params?.keyword) searchParams.append("keyword", params.keyword);
    if (params?.risk_level)
      params.risk_level.forEach((r) => searchParams.append("risk_level", r));
    if (params?.account)
      params.account.forEach((a) => searchParams.append("account", a));
    if (params?.country)
      params.country.forEach((c) => searchParams.append("country", c));
    if (params?.fields)
      params.fields.forEach((f) => searchParams.append("fields", f));
    return apiClient.get(`/restock/export?${searchParams.toString()}`, {
      responseType: "blob",
    });
  },
  syncFeishuInbound: () => apiClient.post("/restock/sync-feishu-inbound"),
  getSyncFeishuStatus: () => apiClient.get("/restock/sync-feishu-status"),
  updateInspectionQuantity: (snapshotId: number, quantity: number) =>
    apiClient.put("/restock/inspection-quantity", null, {
      params: { snapshot_id: snapshotId, inspection_quantity: quantity },
    }),
  getSummaryChildren: (asin: string, account?: string) =>
    apiClient.get("/restock/summary-children", { params: { asin, account } }),
  markHoliday: (snapshotIds: number[], isHoliday: boolean) =>
    apiClient.post("/restock/mark-holiday", {
      snapshot_ids: snapshotIds,
      is_holiday: isHoliday,
    }),
  markProductStatus: (
    snapshotIds: number[],
    action: string,
    holidayType?: string,
  ) =>
    apiClient.post("/restock/mark-product-status", {
      snapshot_ids: snapshotIds,
      action: action,
      holiday_type: holidayType,
    }),
  allocateShipment: (
    items: {
      asin?: string;
      sku?: string;
      country: string;
      purchase_qty: number;
    }[],
  ) => apiClient.post("/restock/allocate-shipment", { items }),
};

// ========== Local Inventory API ==========
export const localInventoryApi = {
  import: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiClient.post("/local-inventory/import", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  getSummary: () => apiClient.get("/local-inventory/summary"),
  getList: (params?: { keyword?: string; page?: number; page_size?: number }) =>
    apiClient.get("/local-inventory/list", { params }),
  clear: () => apiClient.delete("/local-inventory/clear"),
  importReduction: (country: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiClient.post(
      `/local-inventory/import-reduction?country=${encodeURIComponent(country)}`,
      formData,
      {
        headers: { "Content-Type": "multipart/form-data" },
      },
    );
  },
  downloadReductionResult: (fileId: string) =>
    apiClient.get(`/local-inventory/import-reduction/result/${fileId}`, {
      responseType: "blob",
    }),
};

// ========== Tags API（标签管理：节日/强制补货/词库/审计/星拓拉取） ==========
export const tagsApi = {
  list: (params?: {
    asin?: string;
    account?: string;
    tag_type?: string;
    keyword?: string;
    page?: number;
    page_size?: number;
  }) => apiClient.get("/tags", { params }),
  // 行级作用域：account 指定店铺行（汇总行会展开到其共享库存子行）；空串=该 ASIN 全部店铺
  setForceReplenish: (asin: string, reason: string, sku?: string, account?: string) =>
    apiClient.post("/tags/force-replenish", {
      asin,
      reason,
      sku: sku || "",
      account: account || "",
    }),
  clearForceReplenish: (asin: string, account?: string) =>
    apiClient.delete(`/tags/force-replenish/${encodeURIComponent(asin)}`, {
      params: { account: account || "" },
    }),
  setHoliday: (asin: string, holidayValue: string, sku?: string, account?: string) =>
    apiClient.post("/tags/holiday", {
      asin,
      holiday_value: holidayValue,
      sku: sku || "",
      account: account || "",
    }),
  unlockHoliday: (
    asin: string,
    holidayValue: string,
    clear?: boolean,
    account?: string,
  ) =>
    apiClient.post("/tags/holiday/unlock", {
      asin,
      holiday_value: holidayValue,
      clear: !!clear,
      account: account || "",
    }),
  runAutoTagging: () => apiClient.post("/tags/auto-tagging/run"),
  previewMatch: (asin?: string, keywords?: string[]) =>
    apiClient.post("/tags/preview", { asin, keywords }),
  listLexicon: (params?: {
    holiday_name?: string;
    page?: number;
    page_size?: number;
  }) => apiClient.get("/tags/lexicon", { params }),
  addLexicon: (body: {
    holiday_name: string;
    keyword: string;
    match_type?: string;
    window_start?: string | null;
    window_end?: string | null;
  }) => apiClient.post("/tags/lexicon", body),
  // US-4 AC2：修改词库条目（只传需要改的字段；未传字段保持原值）
  updateLexicon: (
    entryId: number,
    body: {
      holiday_name?: string;
      keyword?: string;
      match_type?: string;
      site?: string;
      window_start?: string | null;
      window_end?: string | null;
      status?: number;
      remark?: string;
    },
  ) => apiClient.put(`/tags/lexicon/${entryId}`, body),
  deleteLexicon: (entryId: number) =>
    apiClient.delete(`/tags/lexicon/${entryId}`),
  listAudit: (params?: {
    asin?: string;
    source?: string;
    tag_type?: string;
    page?: number;
    page_size?: number;
  }) => apiClient.get("/tags/audit", { params }),
  getStartopStatus: () => apiClient.get("/tags/startop/status"),
  pullStartop: (beginDate?: string, endDate?: string) =>
    apiClient.post("/tags/startop/pull", {
      begin_date: beginDate,
      end_date: endDate,
    }),
  // F10 兜底通道：上传星拓定时报告导入词数据（幂等：文件哈希去重；仅 admin）
  importStartopReport: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiClient.post("/tags/startop/report-import", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  getFilterConfig: () => apiClient.get("/tags/filter-config"),
  saveFilterConfig: (config: {
    negative_margin_threshold: number;
    tag_window_days: number;
  }) => apiClient.post("/tags/filter-config", config),
};

// ========== Reviews API ==========
export const reviewsApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    search?: string;
    sort_by?: string;
    sort_order?: string;
    start_date?: string;
    end_date?: string;
    status?: string;
    importance_level?: string;
    department?: string;
  }) => {
    // 过滤掉undefined、null和空字符串的参数
    const filteredParams: any = {};
    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== "") {
          filteredParams[key] = value;
        }
      });
    }
    return apiClient.get("/reviews/", { params: filteredParams });
  },
  getById: (id: string) => apiClient.get(`/reviews/${id}`),
  updateStatus: (id: string, status: string) =>
    apiClient.put(`/reviews/${id}/status`, { status }),
  processSuggestion: (id: string, suggestionIndex: number, note: string) =>
    apiClient.post(`/reviews/${id}/suggestions/${suggestionIndex}/process`, { note }),
  updateImportance: (id: string, importance_level: string | undefined) =>
    apiClient.put(`/reviews/${id}/importance`, { importance_level }),
  batchAnalyze: (ids: string[]) =>
    apiClient.post("/reviews/analyze/batch", ids),
  getNewCount: () => apiClient.get("/reviews/new/count"),
  getStats: () => apiClient.get("/reviews/stats"),
  getNegativeRanking: (months?: number) =>
    apiClient.get("/reviews/negative-ranking", {
      params: months ? { months } : {},
    }),
  // 差评板块订阅（推送对象配置）
  getSectionSubscribers: () => apiClient.get("/reviews/sections/subscribers"),
  updateSectionSubscribers: (payload: Record<string, number[]>) =>
    apiClient.put("/reviews/sections/subscribers", payload),
};

// ========== Departments API ==========
export const departmentsApi = {
  getList: () => apiClient.get("/departments/"),
  getRegions: () => apiClient.get("/departments/regions/list"),
  create: (data: { name: string; description?: string }) =>
    apiClient.post("/departments/", data),
  update: (id: number, data: { name?: string; description?: string }) =>
    apiClient.put(`/departments/${id}`, data),
  delete: (id: number) => apiClient.delete(`/departments/${id}`),
  getMembers: (id: number) => apiClient.get(`/departments/${id}/members`),
  addMember: (deptId: number, userId: number) =>
    apiClient.post(`/departments/${deptId}/members`, { user_id: userId }),
  removeMember: (deptId: number, userId: number) =>
    apiClient.delete(`/departments/${deptId}/members/${userId}`),
  getAllUsers: () => apiClient.get("/departments/users/all"),
  updateUserDepartments: (userId: number, departmentIds: number[]) =>
    apiClient.put(`/departments/users/${userId}/departments`, departmentIds),
  createUser: (data: {
    username: string;
    email: string;
    role?: string;
    role_id?: number;
  }) => apiClient.post("/departments/users", data),
  updateUser: (
    userId: number,
    data: {
      username?: string;
      email?: string;
      nickname?: string;
      role?: string;
      role_id?: number;
    },
  ) => apiClient.put(`/departments/users/${userId}`, data),
  deleteUser: (userId: number) =>
    apiClient.delete(`/departments/users/${userId}`),
  toggleUserStatus: (userId: number) =>
    apiClient.put(`/departments/users/${userId}/toggle-status`),
  changeUserPassword: (userId: number, newPassword: string) =>
    apiClient.put(`/departments/users/${userId}/change-password`, {
      new_password: newPassword,
    }),
  batchAssignDepartments: (data: {
    user_ids: number[];
    department_ids: number[];
  }) => apiClient.post("/departments/users/batch-assign", data),
  batchEnableUsers: (userIds: number[]) =>
    apiClient.post("/departments/users/batch-enable", { user_ids: userIds }),
  batchDisableUsers: (userIds: number[]) =>
    apiClient.post("/departments/users/batch-disable", { user_ids: userIds }),
  batchChangePassword: (userIds: number[], newPassword: string) =>
    apiClient.post("/departments/users/batch-password", {
      user_ids: userIds,
      new_password: newPassword,
    }),
  batchDeleteUsers: (userIds: number[]) =>
    apiClient.post("/departments/users/batch-delete", { user_ids: userIds }),
};

// ========== Notifications API ==========
export const notificationsApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    unread_only?: boolean;
  }) => apiClient.get("/notifications/", { params }),
  getUnreadCount: () => apiClient.get("/notifications/unread-count"),
  markAsRead: (id: number) => apiClient.put(`/notifications/${id}/read`),
  markAllAsRead: () => apiClient.put("/notifications/read-all"),
};

// ========== Chat API ==========
export const chatApi = {
  sendMessage: (
    message: string,
    sessionId?: string,
    chatType: string = "review",
  ) =>
    apiClient.post(
      "/chat",
      { message, session_id: sessionId, chat_type: chatType },
      { timeout: 300000 },
    ),
  getSessions: (chatType?: string) =>
    apiClient.get("/chat/sessions", {
      params: chatType ? { chat_type: chatType } : {},
    }),
  getSessionMessages: (sessionId: string) =>
    apiClient.get(`/chat/sessions/${sessionId}/messages`),

  deleteSession: (sessionId: string) =>
    apiClient.delete(`/chat/sessions/${sessionId}`),
};

// ========== Chat API (Streaming) ==========
export const chatStreamApi = {
  sendMessage: async (
    message: string,
    sessionId?: string,
    chatType: string = "review",
  ): Promise<Response> => {
    return fetch("/api/chat/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${localStorage.getItem("token") || ""}`,
      },
      body: JSON.stringify({
        message,
        session_id: sessionId,
        chat_type: chatType,
      }),
    });
  },

  searchSessions: (query: string, chatType?: string, limit?: number) =>
    apiClient.post("/chat/search", { query, chat_type: chatType, limit }),

  exportSession: (
    sessionId: string,
    format: "markdown" | "json" | "txt" = "markdown",
  ) =>
    apiClient.post(
      "/chat/export",
      { session_id: sessionId, format },
      { responseType: "text" },
    ),
};
// ========== Product Page Info (评分优化) API ==========
export const productPageInfoApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    asin_search?: string;
    sku_search?: string;
    store_filter?: string | null;
    rating_status?: number | null;
    low_score?: boolean | null;
    rated_no_video?: boolean | null;
  }) => apiClient.get("/product-page-info/", { params: params }),
  getById: (id: number) => apiClient.get(`/product-page-info/${id}`),
  getStoreOptions: () => apiClient.get("/product-page-info/stores/options"),
  deleteCompetitor: (id: number, competitorLine: string) =>
    apiClient.put(`/product-page-info/${id}/delete-competitor`, {
      competitor_line: competitorLine,
    }),
  addCompetitor: (id: number, asin: string, price: string) =>
    apiClient.post(`/product-page-info/${id}/add-competitor`, {
      asin,
      price,
    }),
  submitRating: (ids: number[]) =>
    apiClient.post("/product-page-info/submit-rating", { ids }),
  deleteRecords: (ids: number[]) =>
    apiClient.post("/product-page-info/delete-records", { ids }),
  getRanking: () => apiClient.get("/product-page-info/ranking"),
  refreshInventory: () => apiClient.post("/product-page-info/refresh-inventory"),
  submitEdit: (data: { item_id: number; store: string; field_type: string; sku: string; content: string; reset_rating: boolean }) =>
    apiClient.post("/product-page-info/submit-edit", data),
  aiWrite: (data: { item_id: number; field_type: string; content: string; suggestion?: string }) =>
    apiClient.post("/product-page-info/ai-write", data),
  aiSuggestLog: (itemId: number, suggestions: Record<string, string>) =>
    apiClient.post(`/product-page-info/${itemId}/ai-suggest-log`, { suggestions }),
  updateRatingStatus: (id: number, ratingStatus: number) =>
    apiClient.put(`/product-page-info/${id}/rating-status`, { rating_status: ratingStatus }),
  updateTrafficKeywords: (id: number, trafficKeywords: string) =>
    apiClient.put(`/product-page-info/${id}/traffic-keywords`, { traffic_keywords: trafficKeywords }),
  adSubmit: (data: {
    ad_type: string[];
    keyword_asin_source: string;
    bid_strategy: string;
    sku: string;
    site: string;
    manual_exact_bid: string;
    manual_broad_bid: string;
    manual_phrase_bid: string;
    related_exact_bid: string;
    related_expanded_bid: string;
    strongest_related_asin: string;
    key_keyword: string;
    limit_word: string;
    ad_asin: string;
    ad_keyword: string;
    item_id?: number;
  }) => apiClient.post("/product-page-info/ad-submit", data),
};
// ========== Stores API ==========
export const storesApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    name_search?: string;
    site_search?: string;
    search?: string;
    platform?: string;
    site?: string;
    assignment_fallback?: boolean;
  }) => apiClient.get("/stores/", { params }),
  getAll: () => apiClient.get("/stores/all"),
  create: (data: {
    name: string;
    platform?: string;
    site?: string;
    inventory_name?: string;
    platform_store_id?: string;
    shop_abbr?: string;
    group_id?: number;
  }) => apiClient.post("/stores/", data),
  update: (
    id: number,
    data: {
      name?: string;
      platform?: string;
      site?: string;
      inventory_name?: string;
      platform_store_id?: string;
      group_id?: number;
      status?: string;
    },
  ) => apiClient.put(`/stores/${id}`, data),
  delete: (id: number) => apiClient.delete(`/stores/${id}`),
  batchUpdateGroup: (data: {
    store_ids: number[];
    group_id?: number | null;
  }) => apiClient.post("/stores/batch-update-group", data),
  // 店铺分配人员
  getMembers: (storeId: number) => apiClient.get(`/stores/${storeId}/members`),
  addMembers: (storeId: number, data: { user_ids: number[] }) =>
    apiClient.post(`/stores/${storeId}/members`, data),
  removeMember: (storeId: number, userId: number) =>
    apiClient.delete(`/stores/${storeId}/members/${userId}`),
  setMembers: (storeId: number, data: { user_ids: number[] }) =>
    apiClient.put(`/stores/${storeId}/members`, data),
};

// ========== Products API ==========
export const productsApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    search?: string;
    product_type?: string;
    status?: string;
    hide_zero_stock?: boolean;
    around_product_id?: number;
  }) => apiClient.get("/products/", { params }),
  getById: (id: number) => apiClient.get(`/products/${id}`),
  create: (data: {
    product_code?: string;
    name: string;
    name_en?: string;
    product_type?: string | string[];
    product_attribute?: string;
    category?: string;
    brand?: string;
    purchase_price?: number;
    sale_price?: number;
    main_image?: string;
    images?: string[];
    video_url?: string;
    weight?: number;
    length?: number;
    width?: number;
    height?: number;
    status?: string;
    is_robot_monitored?: boolean;
    local_quantity?: number;
    local_warehouse?: string;
    local_inbound_date?: string;
    local_stock_age?: number;
  }) => apiClient.post("/products/", data),
  // 生成下一个产品编码（成品 BXHS-CP-##### / 配件 BXHS-PJ-#####）
  getNextCode: (productType: string = "finished") =>
    apiClient.get("/products/next-code", { params: { product_type: productType } }),
  update: (
    id: number,
    data: {
      product_code?: string;
      name?: string;
      name_en?: string;
      product_type?: string;
      product_attribute?: string;
      category?: string;
      brand?: string;
      purchase_price?: number;
      sale_price?: number;
      main_image?: string;
      images?: string[];
      video_url?: string;
      weight?: number;
      length?: number;
      width?: number;
      height?: number;
      status?: string;
      is_robot_monitored?: boolean;
      local_quantity?: number;
      local_warehouse?: string;
      local_inbound_date?: string;
      local_stock_age?: number;
    },
  ) => apiClient.put(`/products/${id}`, data),
  delete: (id: number) => apiClient.delete(`/products/${id}`),
  getProfitMargins: (productId: number) =>
    apiClient.get(`/products/${productId}/profit-margins`),
  getProfitMarginsBatch: (productIds: number[]) =>
    apiClient.post("/products/profit-margins/batch", {
      product_ids: productIds,
    }),
  getStoreGroupSku: (productId: number, storeGroupId?: number) =>
    apiClient.get(`/products/${productId}/store-group-sku`, {
      params: storeGroupId ? { store_group_id: storeGroupId } : {},
    }),
  getStoreGroupSkusBatch: (productIds: number[], storeGroupId?: number) =>
    apiClient.post(
      "/products/store-group-skus/batch",
      { product_ids: productIds },
      { params: storeGroupId ? { store_group_id: storeGroupId } : {} },
    ),
  getPlatformProducts: (productId: number) =>
    apiClient.get(`/products/${productId}/platform-products`),
  createPlatformProduct: (
    productId: number,
    data: {
      platform: string;
      store_ids: number[];
      platform_product_id?: string;
      asin?: string;
      spu?: string;
      sku?: string;
      title?: string;
      title_en?: string;
      image_url?: string;
      images?: string[];
      description?: string;
      bullet_points?: string;
      keywords?: string;
      currency?: string;
      price?: number;
      cost_price?: number;
      status?: string;
    },
  ) => apiClient.post(`/products/${productId}/platform-products`, data),
  updatePlatformProduct: (
    productId: number,
    ppId: number,
    data: {
      platform_product_id?: string;
      asin?: string;
      spu?: string;
      sku?: string;
      title?: string;
      title_en?: string;
      image_url?: string;
      images?: string[];
      description?: string;
      bullet_points?: string;
      keywords?: string;
      currency?: string;
      price?: number;
      cost_price?: number;
      status?: string;
      store_ids?: number[];
    },
  ) => apiClient.put(`/products/${productId}/platform-products/${ppId}`, data),
  deletePlatformProduct: (productId: number, ppId: number) =>
    apiClient.delete(`/products/${productId}/platform-products/${ppId}`),
  downloadTemplate: () =>
    apiClient.get("/products/template/download", {
      responseType: "blob",
    }),
  uploadPreview: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiClient.post("/products/upload/preview", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  batchImport: (data: any) => apiClient.post("/products/batch-import", data),
  getImportRecordStatus: (recordId: number) =>
    apiClient.get(`/products/import-records/${recordId}/status`),
  getIncompleteList: (params: {
    page?: number;
    page_size?: number;
    issue?: string;
    keyword?: string;
  }) => apiClient.get("/products/incomplete-list", { params }),
  batchUpdateMissing: (items: any[]) =>
    apiClient.post("/products/batch-update-missing", { items }),
  exportProducts: (params?: {
    search?: string;
    product_type?: string[];
    product_attribute?: string;
    status?: string;
  }) =>
    apiClient.get("/products/export", {
      params,
      paramsSerializer: (params) => {
        const items: string[] = [];
        Object.entries(params).forEach(([key, value]) => {
          if (Array.isArray(value)) {
            value.forEach((v) => items.push(`${key}=${encodeURIComponent(v)}`));
          } else if (value !== undefined && value !== null) {
            items.push(`${key}=${encodeURIComponent(String(value))}`);
          }
        });
        return items.join("&");
      },
      responseType: "blob",
    }),
  batchDelete: (ids: number[]) =>
    apiClient.post("/products/batch-delete", { ids }),
  batchBindAccessory: (data: {
    finished_product_ids: number[];
    accessory_ids: number[];
    quantity?: number;
  }) => apiClient.post("/products/batch-bind-accessory", data),
  getImportRecords: (params?: {
    status?: string;
    created_by?: string;
    start_date?: string;
    end_date?: string;
    page?: number;
    page_size?: number;
  }) => apiClient.get("/products/import-records", { params }),
  getImportRecordPreviewData: (id: number) =>
    apiClient.get(`/products/import-records/${id}/preview-data`),
  getImportRecordDetail: (id: number) =>
    apiClient.get(`/products/import-records/${id}`),
};

// ========== Upload API (火山引擎 TOS) ==========
export const uploadApi = {
  uploadImage: (file: File, customName?: string) => {
    const formData = new FormData();
    formData.append("file", file);
    if (customName) formData.append("custom_name", customName);
    return apiClient.post("/upload/image", formData, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 60000,
    });
  },
  uploadVideo: (file: File, customName?: string) => {
    const formData = new FormData();
    formData.append("file", file);
    if (customName) formData.append("custom_name", customName);
    return apiClient.post("/upload/video", formData, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 300000,
    });
  },
  deleteFile: (fileUrl: string) =>
    apiClient.post("/upload/delete", { file_url: fileUrl }),
};

// ========== Inventory Count API ==========
export const inventoryCountApi = {
  downloadTemplate: () =>
    apiClient.get("/inventory-count/template", { responseType: "blob" }),
  upload: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiClient.post("/inventory-count/upload", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  confirm: (data: { items: any[] }) =>
    apiClient.post("/inventory-count/confirm", data),
};

// ========== Store Groups API ==========
export const storeGroupsApi = {
  getList: () => apiClient.get("/store-groups/"),
  getTree: () => apiClient.get("/store-groups/tree"),
  getMyGroup: () => apiClient.get("/store-groups/my-group"),
  getGroupStores: (groupId: number) =>
    apiClient.get(`/store-groups/${groupId}/stores`),
  create: (data: { name: string; description?: string }) =>
    apiClient.post("/store-groups/", data),
  update: (id: number, data: { name?: string; description?: string }) =>
    apiClient.put(`/store-groups/${id}`, data),
  delete: (id: number) => apiClient.delete(`/store-groups/${id}`),
  batchAddStores: (groupId: number, storeIds: number[]) =>
    apiClient.post(`/store-groups/${groupId}/stores`, { store_ids: storeIds }),
  removeStore: (groupId: number, storeId: number) =>
    apiClient.delete(`/store-groups/${groupId}/stores/${storeId}`),
  // 店铺分组人员管理
  getMembers: (groupId: number) =>
    apiClient.get(`/store-groups/${groupId}/members`),
  setMembers: (groupId: number, data: { user_ids: number[] }) =>
    apiClient.put(`/store-groups/${groupId}/members`, data),
};

// ========== Tenants API ==========
export const tenantsApi = {
  getList: () => apiClient.get("/tenants/"),
  getById: (id: number) => apiClient.get(`/tenants/${id}`),
  create: (data: { name: string; code: string; status?: string }) =>
    apiClient.post("/tenants/", data),
  update: (
    id: number,
    data: {
      name?: string;
      code?: string;
      status?: string;
    },
  ) => apiClient.put(`/tenants/${id}`, data),
  delete: (id: number) => apiClient.delete(`/tenants/${id}`),
  generateBindingCode: (id: number) =>
    apiClient.put(`/tenants/${id}/generate-binding-code`),
  bind: (binding_code: string) =>
    apiClient.post("/tenants/bind", { binding_code }),
};

// ========== Inbound Orders API ==========
export const inboundOrdersApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    status?: string;
    inbound_type?: string;
    search?: string;
  }) => apiClient.get("/inbound-orders/", { params }),
  getPendingPurchaseItems: (productId: number) =>
    apiClient.get(`/inbound-orders/pending-purchase-items/${productId}`),
  create: (data: {
    order_number: string;
    inbound_type: string;
    purchase_order_id?: number;
    warehouse?: string;
    handler?: string;
    inbound_date?: string;
    notes?: string;
    items: {
      product_id: number;
      quantity: number;
      unit_price?: number;
      batch_number?: string;
      production_date?: string;
      expiry_date?: string;
      warehouse?: string;
      shelf_number?: string;
      notes?: string;
      purchase_order_item_id?: number;
    }[];
  }) => apiClient.post("/inbound-orders/", data),
  update: (
    id: number,
    data: {
      order_number?: string;
      inbound_type?: string;
      purchase_order_id?: number;
      warehouse?: string;
      handler?: string;
      inbound_date?: string;
      notes?: string;
    },
  ) => apiClient.put(`/inbound-orders/${id}`, data),
  confirm: (id: number) => apiClient.put(`/inbound-orders/${id}/confirm`),
  checkPurchaseDiff: (
    items: Array<{
      product_id: number;
      quantity: number;
      purchase_order_item_id?: number | null;
    }>,
  ) => apiClient.post("/inbound-orders/check-purchase-diff", items),
  notifyInboundDiff: (order_number: string, warnings: any[]) =>
    apiClient.post("/inbound-orders/notify-inbound-diff", {
      order_number,
      warnings,
    }),
  getPendingDiffItems: () =>
    apiClient.get("/inbound-orders/pending-diff-items"),
  resolveDiffs: (
    resolutions: Array<{ inbound_item_id: number; resolution: string }>,
  ) => apiClient.post("/inbound-orders/resolve-diffs", resolutions),
  delete: (id: number) => apiClient.delete(`/inbound-orders/${id}`),
  downloadTemplate: () =>
    apiClient.get(`/inbound-orders/template/download`, {
      responseType: "blob",
    }),
  uploadPreview: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiClient.post("/inbound-orders/upload/preview", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
};

// ========== Outbound Orders API ==========
export const outboundOrdersApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    status?: string;
    outbound_type?: string;
    search?: string;
  }) => apiClient.get("/outbound-orders/", { params }),
  create: (data: {
    order_number: string;
    outbound_type: string;
    warehouse?: string;
    handler?: string;
    outbound_date?: string;
    notes?: string;
    items: {
      product_id: number;
      quantity: number;
      unit_price?: number;
      notes?: string;
    }[];
  }) => apiClient.post("/outbound-orders/", data),
  update: (
    id: number,
    data: {
      order_number?: string;
      outbound_type?: string;
      warehouse?: string;
      handler?: string;
      outbound_date?: string;
      notes?: string;
    },
  ) => apiClient.put(`/outbound-orders/${id}`, data),
  confirm: (id: number) => apiClient.put(`/outbound-orders/${id}/confirm`),
  delete: (id: number) => apiClient.delete(`/outbound-orders/${id}`),
  downloadTemplate: () =>
    apiClient.get(`/outbound-orders/template/download`, {
      responseType: "blob",
    }),
  uploadPreview: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiClient.post("/outbound-orders/upload/preview", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
};

// ========== Purchase Orders API ==========
export const purchaseOrdersApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    status?: string;
    supplier?: string;
    search?: string;
  }) => apiClient.get("/purchase-orders/", { params }),
  create: (data: {
    order_number: string;
    supplier?: string;
    contact_person?: string;
    contact_phone?: string;
    warehouse?: string;
    expected_date?: string;
    notes?: string;
    items: {
      product_id: number;
      quantity: number;
      unit_price?: number;
      notes?: string;
      store_group_id?: number | null;
    }[];
  }) => apiClient.post("/purchase-orders/", data),
  update: (
    id: number,
    data: {
      order_number?: string;
      supplier?: string;
      contact_person?: string;
      contact_phone?: string;
      warehouse?: string;
      expected_date?: string;
      notes?: string;
      status?: string;
      items?: {
        product_id: number;
        quantity: number;
        unit_price?: number;
        supplier?: string;
        notes?: string;
        store_group_id?: number | null;
      }[];
    },
  ) => apiClient.put(`/purchase-orders/${id}`, data),
  delete: (id: number) => apiClient.delete(`/purchase-orders/${id}`),
  cancelApproval: (id: number) =>
    apiClient.post(`/purchase-orders/${id}/cancel-approval`),
  downloadTemplate: () =>
    apiClient.get(`/purchase-orders/template/download`, {
      responseType: "blob",
    }),
  uploadPreview: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiClient.post("/purchase-orders/upload/preview", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  exportOrders: (ids: number[]) =>
    apiClient.post(
      "/purchase-orders/export",
      { ids },
      { responseType: "blob" },
    ),
};

// ========== Inventory Batches API ==========
export const inventoryBatchesApi = {
  getProductBatches: (productId: number) =>
    apiClient.get(`/inventory-batches/product/${productId}`),
  getProductHistory: (productId: number) =>
    apiClient.get(`/inventory-batches/product/${productId}/history`),
  getReport: (params?: { page?: number; page_size?: number }) =>
    apiClient.get("/inventory-batches/report", { params }),
  updateShelfNumber: (batchId: number, shelfNumber: string) =>
    apiClient.put(`/inventory-batches/${batchId}/shelf-number`, {
      shelf_number: shelfNumber,
    }),
};

// ========== Operation Logs API ==========
export const operationLogsApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    module?: string;
    action?: string;
    user_id?: number;
    target_id?: number;
    target_type?: string;
    start_date?: string;
    end_date?: string;
    search?: string;
  }) => apiClient.get("/operation-logs/", { params }),
  getModules: () => apiClient.get("/operation-logs/modules"),
  getActions: () => apiClient.get("/operation-logs/actions"),
};

// ========== Permissions API ==========
export const permissionsApi = {
  // 角色管理
  getRoles: () => apiClient.get("/permissions/roles"),
  createRole: (data: {
    name: string;
    code: string;
    description?: string;
    sort_order?: number;
  }) => apiClient.post("/permissions/roles", data),
  updateRole: (
    id: number,
    data: { name?: string; description?: string; sort_order?: number },
  ) => apiClient.put(`/permissions/roles/${id}`, data),
  deleteRole: (id: number) => apiClient.delete(`/permissions/roles/${id}`),
  copyRole: (roleId: number, data: { name: string; code: string; description?: string }) =>
    apiClient.post(`/permissions/roles/${roleId}/copy`, data),

  // 权限管理
  getPermissions: (type?: string) =>
    apiClient.get("/permissions/permissions", { params: { type } }),

  // 角色权限
  getRolePermissions: (roleId: number) =>
    apiClient.get(`/permissions/roles/${roleId}/permissions`),
  updateRolePermissions: (roleId: number, permissionIds: number[]) =>
    apiClient.put(`/permissions/roles/${roleId}/permissions`, {
      permission_ids: permissionIds,
    }),

  // 角色用户
  getRoleUsers: (roleId: number) =>
    apiClient.get(`/permissions/roles/${roleId}/users`),
  updateRoleUsers: (roleId: number, userIds: number[]) =>
    apiClient.put(`/permissions/roles/${roleId}/users`, { user_ids: userIds }),

  // 用户管理
  getAllUsers: () => apiClient.get("/permissions/users"),

  // 当前用户权限
  getMyPermissions: () => apiClient.get("/permissions/my-permissions"),

  // 初始化默认权限
  initDefaultPermissions: () =>
    apiClient.post("/permissions/init-default-permissions"),

  // 补充缺失权限
  addMissingPermissions: () =>
    apiClient.post("/permissions/add-missing-permissions"),
};

// ========== Stock Transfer API ==========
export const stockTransfersApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    status?: string;
    source_warehouse?: string;
    search?: string;
    start_date?: string;
    end_date?: string;
  }) => apiClient.get("/stock-transfers/", { params }),
  getDetail: (id: number) => apiClient.get(`/stock-transfers/${id}`),
  create: (data: {
    order_number: string;
    source_warehouse: string;
    target_warehouse: string;
    notes?: string;
    items: {
      product_id: number;
      batch_id?: number;
      batch_number?: string;
      shelf_number?: string;
      target_shelf_number?: string;
      quantity: number;
      unit_price?: number;
      notes?: string;
    }[];
  }) => apiClient.post("/stock-transfers/", data),
  update: (
    id: number,
    data: {
      order_number?: string;
      source_warehouse?: string;
      target_warehouse?: string;
      notes?: string;
    },
  ) => apiClient.put(`/stock-transfers/${id}`, data),
  confirm: (id: number) => apiClient.put(`/stock-transfers/${id}/confirm`),
  delete: (id: number) => apiClient.delete(`/stock-transfers/${id}`),
  getWarehouses: () => apiClient.get("/stock-transfers/warehouses/list"),
  getProductsByWarehouse: (warehouse: string) =>
    apiClient.get("/stock-transfers/products/by-warehouse", {
      params: { warehouse },
    }),
};

export const warehousesApi = {
  getList: (params?: {
    search?: string;
    status?: string;
    page?: number;
    page_size?: number;
  }) => apiClient.get("/warehouses/", { params }),
  create: (data: {
    name: string;
    code?: string;
    address?: string;
    contact_person?: string;
    contact_phone?: string;
    notes?: string;
  }) => apiClient.post("/warehouses/", data),
  update: (
    id: number,
    data: {
      name?: string;
      code?: string;
      address?: string;
      contact_person?: string;
      contact_phone?: string;
      status?: string;
      notes?: string;
    },
  ) => apiClient.put(`/warehouses/${id}`, data),
  delete: (id: number) => apiClient.delete(`/warehouses/${id}`),
};

// ========== Emails API ==========
export const emailsApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    buyer_mail_number_search?: string;
    store_name_search?: string;
    sort_by?: string;
    sort_order?: string;
  }) => {
    const filteredParams: any = {};
    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== "") {
          filteredParams[key] = value;
        }
      });
    }
    return apiClient.get("/emails/", { params: filteredParams });
  },
  getById: (id: string) => apiClient.get(`/emails/${id}`),
  updateFollowUp: (id: string, follow_up_status: number) =>
    apiClient.put(`/emails/${id}/follow-up`, { follow_up_status }),
  updateNeedReply: (id: string, need_reply: number, reply_text?: string) =>
    apiClient.put(`/emails/${id}/need-reply`, { need_reply, reply_text }),
  getStoreNames: () => apiClient.get("/emails/store-names"),
  getUnfollowedCount: () => apiClient.get("/emails/unfollowed-count"),
  aiReply: (id: string, requirements: string) =>
    apiClient.post(
      `/emails/${id}/ai-reply`,
      { requirements },
      { timeout: 180000 },
    ),
  batchUpdateFollowUp: (email_ids: string[], follow_up_status: number) =>
    apiClient.put("/emails/batch/follow-up", { email_ids, follow_up_status }),
  getDepartmentTodos: () => apiClient.get("/emails/department-todos"),
  reRunRobot: (id: string) => apiClient.post(`/emails/${id}/re-run`),
};

// ========== Business Settings API ==========
export interface FormulaWeight {
  period: string;
  label: string;
  weight: number;
}

export interface DailySalesConfig {
  type: string;
  weights: FormulaWeight[];
}

export interface BusinessSetting {
  id: number;
  setting_type: string;
  setting_name: string;
  formula_config: DailySalesConfig;
  is_active: number;
}

export const businessSettingsApi = {
  getSetting: (settingType: string) =>
    apiClient.get<BusinessSetting>(`/business-settings/${settingType}`),

  listSettings: () => apiClient.get<BusinessSetting[]>("/business-settings/"),

  updateSetting: (
    settingType: string,
    data: { formula_config: DailySalesConfig; is_active?: number },
  ) =>
    apiClient.put<BusinessSetting>(`/business-settings/${settingType}`, data),

  resetSetting: (settingType: string) =>
    apiClient.post<BusinessSetting>(`/business-settings/reset/${settingType}`),
};

// ========== Product Bindings API ==========
export const productBindingsApi = {
  getByFinished: (productId: number) =>
    apiClient.get(`/product-bindings/by-finished/${productId}`),
  getByAccessory: (productId: number) =>
    apiClient.get(`/product-bindings/by-accessory/${productId}`),
  create: (data: {
    finished_product_id: number;
    accessory_product_id: number;
    quantity: number;
  }) => apiClient.post("/product-bindings/", data),
  update: (
    bindingId: number,
    data: {
      finished_product_id: number;
      accessory_product_id: number;
      quantity: number;
    },
  ) => apiClient.put(`/product-bindings/${bindingId}`, data),
  delete: (bindingId: number) =>
    apiClient.delete(`/product-bindings/${bindingId}`),
};

// ========== Product Selection API ==========
export const productSelectionApi = {
  getTypes: () => apiClient.get("/product-selection/types"),
  getDates: (site?: string) => apiClient.get("/product-selection/dates", { params: site ? { site } : {} }),
  getSites: () => apiClient.get("/product-selection/sites"),
  getList: (params?: {
    page?: number;
    page_size?: number;
    search?: string;
    product_type?: string;
    site?: string;
    date_filter?: string;
    status?: string[];
    sort_by?: string;
    sort_order?: string;
    rate_mode?: string;
  }) => apiClient.get("/product-selection/", { params }),
  getById: (id: number) => apiClient.get(`/product-selection/${id}`),
  submitForApproval: (id: number, data?: {
    ali_1688_url?: string;
    purchase_price?: number;
    purchase_quantity?: number;
    store_group_id?: number;
  }) =>
    apiClient.post(`/product-selection/${id}/submit-for-approval`, data || {}),
  getProfitSettings: () => apiClient.get("/product-selection/profit-settings"),
  saveProfitSettings: (data: {
    site: string;
    ad_fee_rate: number;
    storage_fee_rate: number;
    tax_rate: number;
    return_rate: number;
  }) => apiClient.put("/product-selection/profit-settings", data),
  resetProfitSettings: () => apiClient.post("/product-selection/profit-settings/reset"),
  approve: (id: number, data?: { product_name?: string }) =>
    apiClient.post(`/product-selection/${id}/approve`, data || {}),
  batchApprove: (data: { items: { selection_id: number; product_name?: string }[] }) =>
    apiClient.post(`/product-selection/batch-approve`, data),
  cancelApprovalApplication: (id: number) =>
    apiClient.post(`/product-selection/${id}/cancel-approval-application`, {}),
  batchCancelApproval: (ids: number[]) =>
    apiClient.post(`/product-selection/batch-cancel-approval`, ids),
  revokeApproval: (id: number) =>
    apiClient.post(`/product-selection/${id}/revoke-approval`, {}),
  batchRevokeApproval: (ids: number[]) =>
    apiClient.post(`/product-selection/batch-revoke-approval`, ids),
  reject: (data: { ids: number[]; reason?: string }) =>
    apiClient.post(`/product-selection/reject`, data),
  create: (data: {
    product_title: string;
    url?: string;
    asin?: string;
    image_url?: string;
    rating?: number;
    review_count?: number;
    keywords?: string;
    price?: number;
    commission?: number;
    first_leg_cost?: number;
    last_mile_cost?: number;
    weight_kg?: number;
    cost_at_15_profit?: number;
    product_type?: string;
    monthly_sales?: number;
    traffic_trend?: string;
    category?: string[];
  }) => apiClient.post("/product-selection/", data),
  update: (
    id: number,
    data: {
      product_title?: string;
      url?: string;
      asin?: string;
      image_url?: string;
      rating?: number;
      review_count?: number;
      keywords?: string;
      price?: number;
      commission?: number;
      first_leg_cost?: number;
      last_mile_cost?: number;
      weight_kg?: number;
      cost_at_15_profit?: number;
      product_type?: string;
      monthly_sales?: number;
      traffic_trend?: string;
      category?: string[];
    },
  ) => apiClient.put(`/product-selection/${id}`, data),
  delete: (id: number) => apiClient.delete(`/product-selection/${id}`),
  analyze: (id: number) =>
    apiClient.post(`/product-selection/${id}/analyze`, {}, { timeout: 180000 }),
  batchAnalyze: (ids: number[]) =>
    apiClient.post("/product-selection/batch-analyze", ids, { timeout: 30000 }),
  batchAnalyzeProgress: (batchId: string) =>
    apiClient.get(`/product-selection/batch-analyze/progress/${batchId}`),
  recalcScores: () =>
    apiClient.post("/product-selection/recalc-scores", {}, { timeout: 60000 }),
  switchRate: (useRealtime: boolean) =>
    apiClient.post(
      "/product-selection/switch-rate",
      { use_realtime: useRealtime },
      { timeout: 60000 },
    ),
};
// ========== Ads API ==========
export const adsApi = {
  import: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiClient.post("/ads/import", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  getImportStatus: () => apiClient.get("/ads/import-status"),
  getOverview: (params?: {
    account?: string[];
    country?: string[];
    date_from?: string;
    date_to?: string;
    report_type?: string;
  }) => apiClient.get("/ads/overview", { params }),
  search: (params: any) => apiClient.get("/ads/search", { params }),
  getPerformance: (params?: any) =>
    apiClient.get("/ads/performance", { params }),
  getKeywordAnalysis: (params?: any) =>
    apiClient.get("/ads/keyword-analysis", { params }),
  getSearchTermAnalysis: (params?: any) =>
    apiClient.get("/ads/search-term-analysis", { params }),
  export: (params?: any) =>
    apiClient.get("/ads/export", {
      params,
      responseType: "blob",
    }),
  getFilterOptions: (country?: string) =>
    apiClient.get("/ads/filter-options", {
      params: country ? { country } : {},
    }),
  getAiSuggestions: (params?: any) =>
    apiClient.get("/ads/ai-suggestions", { params }),
  getHealthScore: (params?: {
    campaign_id?: string;
    date_from?: string;
    date_to?: string;
  }) => apiClient.get("/ads/health-score", { params }),
  syncRpa: (data: { report_type: string; date: string; records: any[] }) =>
    apiClient.post("/ads/sync/rpa", data),
};

// ========== Ad Rules API ==========
export const adRulesApi = {
  getList: () => apiClient.get("/ad-rules/list"),
  create: (data: {
    name: string;
    rule_type: string;
    conditions: any;
    actions: any;
  }) => apiClient.post("/ad-rules/create", data),
  update: (id: number, data: any) => apiClient.put(`/ad-rules/${id}`, data),
  delete: (id: number) => apiClient.delete(`/ad-rules/${id}`),
  execute: (ruleIds?: number[]) => {
    const params = new URLSearchParams();
    if (ruleIds) ruleIds.forEach((id) => params.append("rule_ids", String(id)));
    return apiClient.post(`/ad-rules/execute?${params.toString()}`);
  },
  getPredefined: () => apiClient.get("/ad-rules/predefined"),
};

// ========== Ad Suggestions API ==========
export const adSuggestionsApi = {
  list: (params?: {
    status?: string;
    priority?: string;
    target_type?: string;
    page?: number;
    page_size?: number;
  }) => apiClient.get("/ad-suggestions/list", { params }),
  getById: (id: number) => apiClient.get(`/ad-suggestions/${id}`),
  updateStatus: (id: number, status: string) =>
    apiClient.put(`/ad-suggestions/${id}/status`, { status }),
  runRules: (date: string) =>
    apiClient.post("/ad-suggestions/run-rules", { date }),
  delete: (id: number) => apiClient.delete(`/ad-suggestions/${id}`),
};

// ========== Ad Execution Logs API ==========
export const adExecutionLogsApi = {
  list: (params?: {
    rule_name?: string;
    status?: string;
    page?: number;
    page_size?: number;
  }) => apiClient.get("/ad-execution-logs/list", { params }),
  getById: (id: number) => apiClient.get(`/ad-execution-logs/${id}`),
};

// ========== Replenishment Orders API ==========
export const replenishmentOrdersApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    status?: string;
    store_group_id?: number;
    search?: string;
  }) => apiClient.get("/replenishment-orders/", { params }),
  getDetail: (id: number) => apiClient.get(`/replenishment-orders/${id}`),
  create: (data: any) => apiClient.post("/replenishment-orders/", data),
  update: (id: number, data: any) =>
    apiClient.put(`/replenishment-orders/${id}`, data),
  delete: (id: number) => apiClient.delete(`/replenishment-orders/${id}`),
  batchDelete: (ids: number[]) =>
    apiClient.post("/replenishment-orders/batch-delete", { ids }),
  batchConvert: (data: {
    ids: number[];
    supplier?: string;
    contact_person?: string;
    contact_phone?: string;
    notes?: string;
  }) => apiClient.post("/replenishment-orders/batch-convert", data),
  batchApprove: (ids: number[]) =>
    apiClient.post("/replenishment-orders/batch-approve", { ids }),
  batchImport: (groups: any[]) =>
    apiClient.post("/replenishment-orders/batch-import", { groups }),
  approve: (id: number) =>
    apiClient.post(`/replenishment-orders/${id}/approve`),
  cancelApproval: (id: number) =>
    apiClient.post(`/replenishment-orders/${id}/cancel-approval`),
  downloadTemplate: () =>
    apiClient.get("/replenishment-orders/template/download", {
      responseType: "blob",
    }),
  // nameOverrides: {行号: 品名}，缺失信息弹窗创建产品后重新解析时传入编辑后的品名
  uploadPreview: (file: File, nameOverrides?: Record<string, string>) => {
    const formData = new FormData();
    formData.append("file", file);
    if (nameOverrides && Object.keys(nameOverrides).length > 0) {
      formData.append("name_overrides", JSON.stringify(nameOverrides));
    }
    return apiClient.post("/replenishment-orders/upload/preview", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
};

// ========== Shipments API ==========
export const shipmentsApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    status?: string;
    search?: string;
    start_date?: string;
    end_date?: string;
    store_group_id?: number;
  }) => apiClient.get("/shipments/", { params }),
  getDetail: (id: number) => apiClient.get(`/shipments/${id}`),
  create: (data: {
    order_number: string;
    store_group_id?: number;
    store_group_name?: string;
    notes?: string;
    items: {
      product_id: number;
      product_code?: string;
      product_name?: string;
      stock_quantity?: number;
    }[];
  }) => apiClient.post("/shipments/", data),
  update: (id: number, data: any) => apiClient.put(`/shipments/${id}`, data),
  confirm: (id: number) => apiClient.put(`/shipments/${id}/confirm`),
  delete: (id: number) => apiClient.delete(`/shipments/${id}`),
  getKpiCount: () => apiClient.get("/shipments/kpi-count"),
  batchConfirm: (ids: number[]) =>
    apiClient.post("/shipments/batch-confirm", { ids }),
  batchDelete: (ids: number[]) =>
    apiClient.post("/shipments/batch-delete", { ids }),
  batchConvertOutbound: (ids: number[], notes?: string) =>
    apiClient.post("/shipments/batch-convert-outbound", { ids, notes }),
  exportDetail: (id: number) =>
    apiClient.get(`/shipments/${id}/export`, { responseType: "blob" }),
  importDetail: (id: number, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiClient.post(`/shipments/${id}/import`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
};

// ========== Base Table (底表管理) API ==========
export const baseTableApi = {
  getSummary: (params?: {
    page?: number;
    page_size?: number;
    issue?: string;
    keyword?: string;
    group_id?: number;
    sort_by?: string;
    sort_order?: string;
  }) =>
    apiClient.get("/base-table/summary", {
      params,
    }),
  getMyGroups: () => apiClient.get("/base-table/my-groups"),
  getProductWarehouses: (productId: number) =>
    apiClient.get(`/base-table/${productId}/warehouses`),
  getProductPurchaseOrders: (productId: number) =>
    apiClient.get(`/base-table/${productId}/purchase-orders`),
  getProductReplenishmentOrders: (productId: number) =>
    apiClient.get(`/base-table/${productId}/replenishment-orders`),
};

// ========== Suppliers API ==========
export const suppliersApi = {
  getList: (params?: { page?: number; page_size?: number; search?: string }) =>
    apiClient.get("/suppliers/", { params }),
  listAll: () => apiClient.get("/suppliers/list-all"),
  create: (data: {
    name: string;
    contact_person?: string;
    contact_phone?: string;
    address?: string;
    notes?: string;
  }) => apiClient.post("/suppliers/", data),
  update: (
    id: number,
    data: {
      name?: string;
      contact_person?: string;
      contact_phone?: string;
      address?: string;
      notes?: string;
    },
  ) => apiClient.put(`/suppliers/${id}`, data),
  delete: (id: number) => apiClient.delete(`/suppliers/${id}`),
};

// ========== Product Development Bot API ==========
// 后端表 ad_product_info 字段映射：
//   title            -> 标题（只填英文）
//   product_title + keywords（合并） -> 关键词（只填英文）
//   product_name_zh  -> 产品名提取
//   reference_images -> 采购图片（逗号分隔URL/文件名）
//   category/developer/urgency/seasonality/competitors/purchase_price
//   de_title/de_keywords/development_date 对应表中的同名中文列
export interface ProductDevelopmentPayload {
  title?: string;
  product_title?: string;
  keywords?: string;
  product_name_zh?: string;
  category?: string;
  developer?: string;
  urgency?: string;
  seasonality?: string;
  competitors?: string;
  purchase_price?: number;
  de_keywords?: string;
  en_keywords?: string;
  au_keywords?: string;
  development_date?: string;
  reference_images?: string[];
  warehouse_note?: string;
  site_owners?: string;
  related_store?: string;
  purchase_links?: string[];
  purchase_info?: string;
  // 采购分组对应：[{link, images[], info}]
  purchase_items?: { link?: string; images?: string[]; info?: string }[];
  // 关联店铺采购数量对应：[{store, quantity}]
  store_quantities?: { store: string; quantity: number }[];
}

export interface DevelopmentProject
  extends Omit<ProductDevelopmentPayload, "warehouse_note" | "site_owners"> {
  record_id: string;
  purchase_images: string[];
  // 产品库平台商品对应（店铺/SKU/ASIN 列表）
  platform_items?: { store?: string; sku?: string; asin?: string }[];
  purchased?: string;
  purchase_note?: string | null;
  product_code?: string | null;
  site_owners?: string | null;
  warehouse_note?: string | null;
  six_face_images?: string[];
  designer_images?: string[];
  designer_complete_date?: string | null;
  image_review?: string | null;
  doc_name?: string | null;
  doc_link?: string | null;
}

export interface DevPendingCounts {
  purchase: number;
  six_face: number;
  designer: number;
  review: number;
}

export interface DevMyRoles {
  departments: string[];
  is_admin: boolean;
  can_view_all: boolean;
  can_purchase: boolean;
  can_warehouse: boolean;
  can_design: boolean;
  can_review: boolean;
  can_replenish: boolean;
}

export interface ProjectDocElement {
  type: "text" | "image";
  content?: string;
  file_token?: string;
}

export interface ProjectDocInfo {
  doc_name?: string | null;
  doc_link?: string | null;
  elements?: ProjectDocElement[];
  content?: string | null;
  message?: string;
  /** 被屏蔽的采购链接数量（非采购部用户） */
  links_hidden?: number;
}

export const developmentBotApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    search?: string;
    category?: string;
    urgency?: string;
    seasonality?: string;
    developer?: string;
    date?: string;
    date_start?: string;
    date_end?: string;
    status?: string;
    missing?: string;
  }) => apiClient.get("/product-development/projects", { params }),
  getFilters: () => apiClient.get("/product-development/projects/filters"),
  getDetail: (record_id: string) =>
    apiClient.get(`/product-development/projects/${record_id}`),
  create: (data: ProductDevelopmentPayload) =>
    apiClient.post("/product-development/projects", data),
  createBatch: (data: ProductDevelopmentPayload[]) =>
    apiClient.post("/product-development/projects/batch", { projects: data }),
  update: (record_id: string, data: Partial<ProductDevelopmentPayload>) =>
    apiClient.put(`/product-development/projects/${record_id}`, data),
  remove: (record_id: string) =>
    apiClient.delete(`/product-development/projects/${record_id}`),
  getDoc: (record_id: string) =>
    apiClient.get(`/product-development/projects/${record_id}/doc`),
  getCategories: () => apiClient.get("/product-development/categories"),
  addCategory: (name: string) =>
    apiClient.post("/product-development/categories", { name }),
  downloadImportTemplate: () =>
    apiClient.get("/product-development/import-template", { responseType: "blob" }),
  importProjects: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiClient.post("/product-development/import-projects", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  parseImportProjects: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiClient.post("/product-development/import-projects/parse", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  getPendingCounts: () =>
    apiClient.get("/product-development/pending-counts"),
  getMyRoles: () => apiClient.get("/product-development/my-roles"),
  updatePurchase: (record_id: string, data: { purchase_price: number; purchased?: string; purchase_note?: string }) =>
    apiClient.put(`/product-development/projects/${record_id}/purchase`, data),
  updateWarehouse: (record_id: string, data: { six_face_images?: string[]; warehouse_note?: string }) =>
    apiClient.put(`/product-development/projects/${record_id}/warehouse`, data),
  uploadDesignerImage: (record_id: string, images: string[]) =>
    apiClient.put(`/product-development/projects/${record_id}/designer-image`, { images }),
  reviewImage: (record_id: string, approved: boolean) =>
    apiClient.put(`/product-development/projects/${record_id}/review`, { approved }),
  createReplenishment: (data: {
    store_group_id?: number;
    notes?: string;
    items: { record_id: string; quantity: number; notes?: string }[];
  }) => apiClient.post("/product-development/projects/replenishment", data),
  // 按店铺分组采购数量生成进销存采购单
  createPurchaseOrder: (record_id: string) =>
    apiClient.post(`/product-development/projects/${record_id}/purchase-order`),
  // 回传飞书多维表（存在则修改，不存在则新增并回传记录ID）
  pushToFeishu: (record_id: string) =>
    apiClient.post(`/product-development/projects/${record_id}/push-to-feishu`),
};

// ========== AI 创作中心 API ==========
export interface AmazonProductAnalysisResult {
  product_name_cn: string;
  product_name_en: string;
  product_type: string;
  product_size?: string;
  target_audience: string[];
  selling_points: string[];
  material: string[];
  usage_scenarios: string[];
  usage_methods: string[];
  product_components: string[];
  colors: string[];
  amazon_category: string;
  keywords: string[];
  visible_text: string[];
  confidence: number;
  uncertain_information: string[];
}

export interface VideoStoryboardShot {
  timestamp: string;
  shot_purpose: string;
  description?: string;
  shot_type: string;
  camera_movement: string;
  character_action: string;
  character_expression: string;
  product_action: string;
  product_position: string;
  composition: string;
  environment: string;
  voiceover: string;
  voiceover_cn: string;
  sound: string;
}

export interface CreativeStrategy {
  persona_identity: string;
  persona_role: string;
  age: string;
  relationship_to_product: string;
  story_background: string;
  consumption_scene: string;
  core_pain_point: string;
  core_selling_point: string;
  emotion: string;
  video_style: string;
  camera_language: string;
}

export interface VideoConcept {
  concept_title: string;
  marketing_goal: string;
  creative_strategy: CreativeStrategy;
  story: string;
  character: string;
  environment: string;
  music: string;
  storyboard: VideoStoryboardShot[];
}

export interface VideoPrompt {
  concept_title: string;
  final_prompt: string;
}

export type VideoMarket =
  | 'US' | 'CA' | 'AU' | 'UK'
  | 'DE' | 'AT' | 'CH'
  | 'FR' | 'ES' | 'MX' | 'IT'
  | 'JP' | 'KR' | 'BR'
  | 'ME' | 'SEA' | 'CN';
export type VideoDuration = 5 | 10 | 15;
export type VideoRatio = 'auto' | '9:16' | '16:9' | '1:1' | '4:3' | '3:4' | '21:9';
export type VideoModel = 'minimax-h3' | 'minimax-h3-lite';
/** 视频分辨率档位：0.3 / 0.5 为 megapixels 档位，768P 即 0.98 满画幅 */
export type VideoResolution = '0.3' | '0.5' | '768P';

/** 口播语言：'auto' 表示跟随目标市场自动决定 */
export type VoiceoverLanguage =
  | 'auto' | 'English' | 'German' | 'French' | 'Spanish' | 'Italian'
  | 'Japanese' | 'Korean' | 'Portuguese' | 'Arabic';

/** 视频类型：口播带货（含口播台词）/ 产品展示（纯画面展示，无台词） */
export type VideoType = '口播带货' | '产品展示';

export interface VoiceoverPlanParams {
  /** 视频时长（10~30 秒） */
  duration: VideoDuration;
  /** 目标市场（中文名，如“美国”） */
  target_market: string;
  /** 口播语言 */
  voiceover_language: VoiceoverLanguage;
  /** 视频类型（默认口播带货） */
  video_type?: VideoType;
  /** 画面比例 */
  aspect_ratio: VideoRatio;
  /** 产品名称（可空，模型会从图片识别） */
  product_name?: string;
  /** 指定的方案类型（可不填） */
  specified_types?: string[];
  /** 风格偏好 */
  style_preference?: string;
  /** 换一换时需避免重复的已生成类型 */
  avoid_types?: string[];
  /** 用户已确认的产品信息卡文本（编辑后重新生成时使用） */
  confirmed_card?: string;
}

export interface VoiceoverPlanResult {
  product: AmazonProductAnalysisResult;
  concepts: VideoConcept[];
  raw_text: string;
}

/** 视频生成任务（MiniMax H3） */
export interface AIVideoTask {
  id: number;
  title: string;
  /** 排队中 / 生成中 / 已完成 / 失败 */
  status: string;
  /** 生成者用户名 */
  creator_name: string;
  prompt: string;
  /** 二次优化后的 H3 结构化提示词（Ref2VA 六段式英文，实际提交给模型） */
  h3_prompt?: string;
  market: VideoMarket;
  /** 口播语言，「自动」表示跟随目标市场 */
  voiceover_language: string;
  /** 视频类型：口播带货/产品展示 */
  video_type?: string;
  model: VideoModel;
  resolution: VideoResolution;
  duration: VideoDuration;
  ratio: VideoRatio;
  /** 本次生成使用的参考图地址 */
  images: string[];
  video_url: string | null;
  /** 从产品管理选图提交时记录的产品编码/品名 */
  product_code?: string;
  product_name?: string;
  /** 已完成的超分结果（后端按源视频地址匹配附加），用于并排对比展示 */
  upscale_video_url?: string;
  /** 绑定状态：成片已通过审核绑定至产品（后端附加） */
  bound?: boolean;
  /** ComfyUI 任务 ID */
  h3_prompt_id: string;
  /** 生成总耗时（秒），未完成为 null */
  cost_seconds: number | null;
  error_message: string | null;
  created_at: string;
}

export interface GenerateVideoParams {
  prompt: string;
  duration: VideoDuration;
  resolution: VideoResolution;
  ratio: VideoRatio;
  model: VideoModel;
  /** 目标市场代码，如 US / DE */
  market: VideoMarket;
  /** 口播语言，'auto' 表示跟随目标市场 */
  voiceover_language: VoiceoverLanguage;
  /** 视频类型（默认口播带货） */
  video_type?: VideoType;
  title: string;
  /** 从产品管理选图提交时记录的产品编码/品名 */
  product_code?: string;
  product_name?: string;
}

/** 视频超分（Topaz 星光 2.6）任务 */
export interface VideoUpscaleTask {
  id: number;
  title: string;
  creator_name: string;
  source_video_url: string;
  source_width?: number | null;
  source_height?: number | null;
  scale?: number | null;
  status: string;
  error_message?: string;
  video_url?: string;
  /** 绑定状态：成片或源视频已通过审核绑定至产品（后端附加） */
  bound?: boolean;
  created_at: string;
  cost_seconds?: number | null;
}

/** 视频绑定审核申请（AI视频 / 高清处理「绑定至产品」先审后绑） */
export interface VideoBindingRequest {
  id: number;
  video_url: string;
  /** 与 video_url 同值，语义化别名（兼容图片） */
  media_url?: string;
  /** video=视频（写 products.videos）/ image=图片（写 products.images） */
  media_type?: "video" | "image";
  media_type_label?: string;
  product_id: number;
  product_code?: string;
  product_name?: string;
  source: "ai_video" | "upscale" | "ai_image";
  source_label?: string;
  task_title?: string;
  status: "pending" | "approved" | "rejected";
  requester_name?: string;
  reject_reason?: string;
  reviewer_name?: string;
  reviewed_at?: string;
  created_at?: string;
}

/** AMAZON 平台 SKU×店铺组合（首条视频绑定弹窗数据源） */
export interface AmazonSkuEntry {
  pp_id: number;
  store_id: number;
  sku: string;
  asin: string;
  has_asin: boolean;
  store_name: string;
  site: string;
  ziniao_account: string;
  ziniao_start_name: string;
}

export const videoBindingApi = {
  /** 提交绑定申请（ai_video:use）；media_type=image 时绑定的是图片 */
  submit: (data: {
    product_id: number;
    video_url: string;
    source?: string;
    media_type?: "video" | "image";
    task_title?: string;
  }) =>
    apiClient.post<{ success: boolean; message: string }>("/video-bindings", data),
  /** 申请列表（ai_video:approve），status 不传查全部；status_counts 为各状态计数（页签括号展示） */
  getList: (params: { status?: string; search?: string; page?: number; page_size?: number }) =>
    apiClient.get<{
      success: boolean;
      data: {
        items: VideoBindingRequest[];
        total: number;
        status_counts?: { pending: number; approved: number; rejected: number; all: number };
      };
    }>(
      "/video-bindings",
      { params },
    ),
  /** 待审核数量（首页视频机器人KPI卡片，ai_video:approve） */
  getPendingCount: () =>
    apiClient.get<{ success: boolean; pending_count: number }>("/video-bindings/pending-count"),
  /** 通过申请并真正绑定视频；data.is_first_video=true 表示该产品首条视频且飞书视频表已配置 */
  approve: (id: number) =>
    apiClient.post<{ success: boolean; message: string; data?: { videos?: string[]; is_first_video?: boolean } }>(
      `/video-bindings/${id}/approve`,
    ),
  /** 拒绝申请（可带原因） */
  reject: (id: number, reason?: string) =>
    apiClient.post<{ success: boolean; message: string }>(`/video-bindings/${id}/reject`, { reason }),
  /** 产品 AMAZON 平台 SKU×店铺列表（首条视频直传弹窗） */
  getAmazonSkus: (productId: number) =>
    apiClient.get<{ success: boolean; data: { items: AmazonSkuEntry[] } }>(
      "/video-bindings/amazon-skus",
      { params: { product_id: productId } },
    ),
  /** 视频直传飞书多维表 SKU 记录（记录数=视频数×SKU数；video_urls 多视频或 video_url 单视频） */
  uploadSkuToFeishu: (data: {
    product_id: number;
    video_url?: string;
    video_urls?: string[];
    items: { pp_id: number; store_id: number }[];
  }) =>
    apiClient.post<{
      success: boolean;
      message: string;
      data?: { success: number; failed: number; errors: string[] };
    }>("/video-bindings/feishu-sku-upload", data),
};

export const aiCreationApi = {
  /** 新方案：一次生成产品信息卡 + 3 套口播视频提示词方案。
 * 生成耗时 1~5 分钟以上，走 SSE 流式响应（15 秒心跳）防止网关读超时 504 */
  generateVoiceoverPlans: (files: File[], params: VoiceoverPlanParams) => {
    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));
    formData.append("duration", String(params.duration));
    formData.append("target_market", params.target_market);
    formData.append(
      "voiceover_language",
      params.voiceover_language === "auto" ? "自动" : params.voiceover_language,
    );
    formData.append("video_type", params.video_type || "口播带货");
    formData.append("aspect_ratio", params.aspect_ratio);
    formData.append("product_name", params.product_name || "");
    if (params.specified_types?.length) {
      formData.append("specified_types", params.specified_types.join("、"));
    }
    if (params.avoid_types?.length) {
      formData.append("avoid_types", params.avoid_types.join("、"));
    }
    if (params.style_preference) {
      formData.append("style_preference", params.style_preference);
    }
    if (params.confirmed_card) {
      formData.append("confirmed_card", params.confirmed_card);
    }

    const readSse = async (resp: Response) => {
      const reader = resp.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let result: { success: boolean; data: VoiceoverPlanResult } | null = null;
      const handleFrame = (frame: string) => {
        const dataLine = frame
          .split("\n")
          .find((l) => l.startsWith("data:"));
        if (!dataLine) return; // 心跳帧（: ping）没有 data 行
        const payload = JSON.parse(dataLine.slice(5).trim());
        if (payload.detail) {
          const err: any = new Error(payload.detail);
          err.response = { data: { detail: payload.detail } };
          throw err;
        }
        result = payload;
      };
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() || "";
        for (const frame of frames) handleFrame(frame);
      }
      if (buffer.trim()) handleFrame(buffer);
      if (!result) throw new Error("方案生成中断，请重试");
      return result;
    };

    const token = localStorage.getItem("token");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 960000);
    return fetch(`${API_BASE}/ai-creation/generate-voiceover-plans`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      body: formData,
      signal: controller.signal,
    })
      .then(async (resp) => {
        clearTimeout(timer);
        if (!resp.ok || !resp.body) {
          // 非 200（401/400 等校验错误）：保持与 axios 错误结构兼容
          let detail = `请求失败（${resp.status}）`;
          try {
            const j = await resp.json();
            detail = j.detail || detail;
          } catch {
            /* 忽略解析失败 */
          }
          const err: any = new Error(detail);
          err.response = { status: resp.status, data: { detail } };
          throw err;
        }
        return { data: await readSse(resp) };
      })
      .catch((e: any) => {
        if (e?.name === "AbortError") {
          const err: any = new Error("AI 生成超时，请稍后重试");
          err.response = { data: { detail: err.message } };
          throw err;
        }
        throw e;
      })
      .finally(() => clearTimeout(timer));
  },
  /** 代理取图：图片 URL 浏览器直连被 CORS 拦截时（如 TOS 桶未配置跨域），由后端代取图片字节 */
  proxyImage: (url: string) =>
    apiClient.get<Blob>("/ai-creation/proxy-image", {
      params: { url },
      responseType: "blob",
      timeout: 60000,
    }),
  /** 立即生成：提交 MiniMax H3 视频生成任务（长时间生成，立即返回任务记录） */
  generateVideo: (files: File[], params: GenerateVideoParams) => {
    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));
    formData.append("prompt", params.prompt);
    formData.append("duration", String(params.duration));
    formData.append("resolution", params.resolution);
    formData.append("ratio", params.ratio);
    formData.append("model", params.model);
    formData.append("market", params.market);
    formData.append(
      "voiceover_language",
      params.voiceover_language === "auto" ? "自动" : params.voiceover_language,
    );
    formData.append("video_type", params.video_type || "口播带货");
    formData.append("title", params.title);
    formData.append("product_code", params.product_code || "");
    formData.append("product_name", params.product_name || "");
    return apiClient.post<{ success: boolean; data: AIVideoTask }>(
      "/ai-creation/generate-video",
      formData,
      {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 300000,
      },
    );
  },
  /** 视频生成任务列表（分页 + 关键词搜索 + 绑定状态筛选，含状态与成片地址） */
  listVideoTasks: (
    page = 1,
    pageSize = 20,
    keyword = "",
    bindStatus: "all" | "bound" | "unbound" = "all",
  ) =>
    apiClient.get<{ success: boolean; data: { items: AIVideoTask[]; total: number } }>(
      "/ai-creation/video-tasks",
      { params: { page, page_size: pageSize, keyword, bind_status: bindStatus } },
    ),
  /** 删除视频生成任务记录 */
  deleteVideoTask: (taskId: number) =>
    apiClient.delete<{ success: boolean }>(`/ai-creation/video-tasks/${taskId}`),
  /** 取消排队中/生成中的视频生成任务（同步移除/中断 ComfyUI 任务） */
  cancelVideoTask: (taskId: number) =>
    apiClient.post<{ success: boolean; message: string }>(
      `/ai-creation/video-tasks/${taskId}/cancel`,
    ),
  /** 智能重试失败的视频任务（原记录重试）：能接续 ComfyUI 就接续收片，
   *  否则服务端直接取历史参考图原位重新提交，无需前端取图 */
  recoverVideoTask: (taskId: number) =>
    apiClient.post<{ success: boolean; action: "resumed" | "resubmitted"; message: string }>(
      `/ai-creation/video-tasks/${taskId}/recover`,
    ),
  /** 提交视频超分任务（Topaz 星光 2.6，后台下载/上传/入队/轮询） */
  createUpscaleTask: (params: {
    title: string;
    source_video_url: string;
    source_width?: number;
    source_height?: number;
    scale: number;
  }) =>
    apiClient.post<{ success: boolean; data: VideoUpscaleTask }>(
      "/ai-creation/upscale-tasks",
      params,
      { timeout: 300000 },
    ),
  /** 超分任务列表（分页 + 标题搜索 + 绑定状态筛选） */
  listUpscaleTasks: (
    page = 1,
    pageSize = 20,
    keyword = "",
    bindStatus: "all" | "bound" | "unbound" = "all",
  ) =>
    apiClient.get<{ success: boolean; data: { items: VideoUpscaleTask[]; total: number } }>(
      "/ai-creation/upscale-tasks",
      { params: { page, page_size: pageSize, keyword, bind_status: bindStatus } },
    ),
  /** 删除超分任务 */
  deleteUpscaleTask: (taskId: number) =>
    apiClient.delete<{ success: boolean }>(`/ai-creation/upscale-tasks/${taskId}`),
  /** 取消排队中/处理中的超分任务（同步移除/中断 ComfyUI 任务） */
  cancelUpscaleTask: (taskId: number) =>
    apiClient.post<{ success: boolean; message: string }>(
      `/ai-creation/upscale-tasks/${taskId}/cancel`,
    ),
  /** 重试失败的超分任务：在原记录上重置状态并重新入队 */
  retryUpscaleTask: (taskId: number) =>
    apiClient.post<{ success: boolean; message: string; data: VideoUpscaleTask }>(
      `/ai-creation/upscale-tasks/${taskId}/retry`,
    ),
  /** 下载超分任务视频（target: result=超分结果 / source=源视频），blob 流式下载 */
  downloadUpscaleVideo: (taskId: number, target: "result" | "source") =>
    apiClient.get(`/ai-creation/upscale-tasks/${taskId}/download`, {
      params: { target },
      responseType: "blob",
      timeout: 600000,
    }),
  /** 绑定视频至产品详情（最多 6 个，超出报「产品已达到视频上限」） */
  bindProductVideo: (productId: number, videoUrl: string) =>
    apiClient.post<{ success: boolean; data: { videos: string[] } }>(
      `/products/${productId}/videos`,
      { video_url: videoUrl },
    ),
  /** 旧方案：仅识别商品信息 */
  analyzeProduct: (files: File[]) => {
    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));
    return apiClient.post<{ success: boolean; data: AmazonProductAnalysisResult; text?: string }>(
      "/ai-creation/analyze-product",
      formData,
      {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 300000,
      },
    );
  },
  generateVideoConcepts: (
    product_profile: AmazonProductAnalysisResult,
    market: VideoMarket = "US",
    duration: VideoDuration = 15,
    previous_concepts?: VideoConcept[],
    aspect_ratio: VideoRatio = "9:16",
  ) =>
    apiClient.post<{ success: boolean; data: VideoConcept[] }>(
      "/ai-creation/generate-video-concepts",
      { product_profile, market, duration, previous_concepts, aspect_ratio },
      { timeout: 300000 },
    ),
  generateVideoPrompts: (product_profile: AmazonProductAnalysisResult, concepts: VideoConcept[]) =>
    apiClient.post<{ success: boolean; data: VideoPrompt[] }>(
      "/ai-creation/generate-video-prompts",
      { product_profile, concepts },
      { timeout: 300000 },
    ),
};

// ========== AI 生图（商品套图） ==========
export interface AIImageResultItem {
  type: string;
  label: string;
  seq: number;
  status: "排队中" | "生成中" | "已完成" | "失败" | "已取消";
  url: string | null;
  error: string | null;
  /* A+ 类型的目标尺寸 [宽, 高]（主图类型为 null） */
  target_size?: number[] | null;
  /* 本次提交对应的 ComfyUI 工作流 prompt_id（旧记录可能没有） */
  prompt_id?: string;
  /* 生图时实际提交的完整提示词（落库，ComfyUI /history 过期后仍可查看） */
  final_prompt?: string | null;
  /* 本地 PE 优化后的提示词（生成完成时落库；中转站无此项） */
  enhanced_prompt?: string | null;
  /* 中文对照（中转站：AI 设计时一并产出，仅供阅读） */
  prompt_cn?: string | null;
  /* 提示词是否为 AI 按产品定制（false = 模板回退） */
  prompt_designed?: boolean;
  /* AI 从参考图清点出的件数清单（如 "red-and-white fishing bobbers x2"） */
  inventory?: string[] | null;
  /* A+ 该张使用的版式（内置版式 preset:xxx / 上传版式 db:id） */
  layout?: AIImageLayoutRef | null;
  /* 品牌A+横幅的品牌名（保留大小写） */
  brand_name?: string | null;
}

/** A+ 版式（官方推荐 / 我的 / 收藏） */
export interface AIImageLayout {
  /** 标识：preset:<内置id> 或 db:<素材id> */
  key: string;
  id: string | number;
  name: string;
  desc?: string;
  /** horizontal / vertical / square */
  ratio_tag?: string;
  /** 给模型的分区描述（上传版式为空，改走版式图） */
  structure?: string;
  /** 缩略图分区数据（归一化坐标） */
  zones?: { x: number; y: number; w: number; h: number; t: "img" | "txt" | "band" | "gap" }[];
  /** 生成用参考图地址（官方模板 / 上传素材） */
  image_url?: string;
  /** 列表缩略图地址（<=480px，仅展示，避免 240 张原图拖死首屏） */
  cover_url?: string;
  source?: "preset" | "upload" | "official";
  favorited?: boolean;
  favorite?: boolean;
  width?: number | null;
  height?: number | null;
}

/** 任务子项里记录的版式快照 */
export interface AIImageLayoutRef {
  key?: string;
  name?: string;
  source?: string;
  ratio_tag?: string;
  structure?: string;
  image_url?: string;
}

export interface AIImageTask {
  id: number;
  title: string;
  status: "排队中" | "生成中" | "已完成" | "部分完成" | "失败" | "已取消";
  creator_name: string;
  generation_types: string[];
  aspect_ratio: string;
  image_count: number;
  product_info: string;
  prompt: string;
  product_code: string;
  product_name: string;
  images: string[];
  result_images: AIImageResultItem[];
  /* 与 result_images 按下标一一对应的 ComfyUI prompt_id（旧记录兜底用） */
  comfy_prompt_ids?: string[];
  /* v2 分组参数 + 品牌设置 */
  aplus_type?: string | null;
  /* v2 为类型数组；v2.1 起为 {类型: 张数} 对象，前端两种形态都要兼容 */
  main_types?: string[] | Record<string, number>;
  main_aspect_ratio?: string | null;
  /** 生成通道：local=本地 Qwen Image 2.1 / relay=中转站 Nano Banana 2 */
  provider?: "local" | "relay";
  provider_label?: string;
  brand_settings?: {
    main_color?: string;
    font_style?: string;
    custom_text?: string;
    country?: string;
    language?: string;
    platform?: string;
    /** A+ 画面文字：no_text=不出现文字（默认）/ with_text=允许 AI 排版文案 */
    text_mode?: string;
    /** 品牌A+横幅的品牌名（保留大小写） */
    brand_name?: string;
  } | null;
  cost_seconds: number | null;
  error_message: string | null;
  created_at: string;
  /** 进行中任务的排队排位（已完成/失败为 null）：排队中到底在等什么 */
  queue?: {
    stage: "comfy" | "relay";
    /** ComfyUI 队列前面还有几个任务 */
    ahead?: number | null;
    /** 本任务在 ComfyUI pending 中的排位（1 基） */
    position?: number | null;
    /** 同账号用户级队列里前面还有几单 */
    user_ahead?: number;
    comfy_running?: number | null;
    comfy_pending?: number | null;
    /** 后端拼好的中文说明，前端直接展示 */
    hint: string;
  } | null;
}

export const aiImageApi = {
  /** 生成类型与比例选项（比例以 ComfyUI 实时能力为准） */
  getOptions: () =>
    apiClient.get<{
      success?: boolean;
      generation_types: Record<string, string>;
      aspect_ratios: Record<string, string>;
      aspect_ratios_all?: Record<string, string>;
      /** size/desc 由服务端按 target_size 下发，前端不再写死尺寸与文案 */
      aplus_types?: Record<
        string,
        { label: string; gen_ratio: string; target_size: number[]; size?: string; desc?: string }
      >;
      main_types?: Record<string, string>;
      /** 主副图卡片说明（key 与 main_types 对应） */
      main_type_descriptions?: Record<string, string>;
      /** 场景图父项 + 两条子路径（不带人物 / 带人物） */
      scene_group?: {
        key: string;
        label: string;
        hint: string;
        variants: { key: string; label: string; hint: string }[];
      };
      brand_options?: {
        countries: string[];
        languages: string[];
        platforms: string[];
        font_styles: string[];
      };
      /** 品牌默认值（与后端 DEFAULT_BRAND_SETTINGS 同源） */
      brand_defaults?: { country?: string; language?: string; platform?: string };
      max_ref_images?: number;
      max_sheets_per_task?: number;
      /** 可选生成模型（本地 / 中转站） */
      providers?: { value: string; label: string; desc?: string; configured?: string }[];
      h3_base_url?: string;
    }>("/ai-image/options"),
  /** 按 prompt_id 查 ComfyUI 执行历史（原始提示词 + PE 优化后提示词） */
  getComfyHistory: (promptId: string) =>
    apiClient.get<{
      success?: boolean;
      prompt_id: string;
      found: boolean;
      original_prompt: string;
      enhanced_prompt: string;
    }>(`/ai-image/comfy-history/${promptId}`),
  /** 提交商品套图生成任务（后台生成，立即返回任务记录）
   *  idempotencyKey：同一份表单复用同一个 key，重复点击/重试不会产生第二单 */
  createTask: (
    files: File[],
    params: {
      title: string;
      generation_types?: string[];
      aspect_ratio?: string;
      image_count: number;
      product_info: string;
      prompt: string;
      product_code?: string;
      product_name?: string;
      /* v2 分组参数 */
      aplus_type?: string;
      main_types?: string[] | Record<string, number>;
      main_aspect_ratio?: string;
      brand_settings?: Record<string, string>;
      /** 生成通道：local=本地 Qwen Image 2.1 / relay=中转站 Nano Banana 2 */
      provider?: "local" | "relay";
      /** A+ 版式标识（preset:xxx / db:id），空=后端随机官方版式；品牌A+横幅忽略 */
      layout_ids?: string[];
      /** 品牌A+横幅必填的英文品牌名（保留大小写，2–20 字符） */
      brand_name?: string;
    },
    idempotencyKey?: string,
  ) => {
    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));
    formData.append("title", params.title);
    formData.append("generation_types", JSON.stringify(params.generation_types || []));
    formData.append("aspect_ratio", params.aspect_ratio || "1:1");
    formData.append("image_count", String(params.image_count));
    formData.append("product_info", params.product_info);
    formData.append("prompt", params.prompt);
    formData.append("product_code", params.product_code || "");
    formData.append("product_name", params.product_name || "");
    if (params.aplus_type) formData.append("aplus_type", params.aplus_type);
    if (params.main_types && Object.keys(params.main_types).length > 0)
      formData.append("main_types", JSON.stringify(params.main_types));
    if (params.main_aspect_ratio)
      formData.append("main_aspect_ratio", params.main_aspect_ratio);
    if (params.brand_settings && Object.keys(params.brand_settings).length)
      formData.append("brand_settings", JSON.stringify(params.brand_settings));
    if (params.provider) formData.append("provider", params.provider);
    formData.append("layout_ids", JSON.stringify(params.layout_ids || []));
    if (params.brand_name) formData.append("brand_name", params.brand_name);
    return apiClient.post<AIImageTask>("/ai-image/tasks", formData, {
      headers: {
        "Content-Type": "multipart/form-data",
        ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
      },
      timeout: 300000,
    });
  },
  /** 套图生成任务列表（分页 + 关键词 + 状态，状态由服务端过滤保证分页自洽） */
  listTasks: (page = 1, pageSize = 20, keyword = "", status = "") =>
    apiClient.get<{ items: AIImageTask[]; total: number }>("/ai-image/tasks", {
      params: { page, page_size: pageSize, keyword, status },
    }),
  /** 单任务详情（轮询单条用，避免整页重复传输） */
  getTask: (taskId: number) => apiClient.get<AIImageTask>(`/ai-image/tasks/${taskId}`),
  /** 取消排队中/生成中的任务 */
  cancelTask: (taskId: number) =>
    apiClient.post<{ success: boolean; message?: string }>(`/ai-image/tasks/${taskId}/cancel`),
  /** 重试失败子项（indices 缺省=全部失败项） */
  retryTask: (taskId: number, indices?: number[]) =>
    apiClient.post<{ retried: number; failed: string[] }>(
      `/ai-image/tasks/${taskId}/retry`,
      indices && indices.length ? { indices } : {},
    ),
  /** 打包下载全部已生成图片 */
  downloadZip: (taskId: number) =>
    apiClient.get<Blob>(`/ai-image/tasks/${taskId}/download.zip`, {
      responseType: "blob",
      timeout: 300000,
    }),
  /** 删除套图生成任务记录 */
  deleteTask: (taskId: number) =>
    apiClient.delete<{ success: boolean }>(`/ai-image/tasks/${taskId}`),

  // ===== A+ 版式（选择排版 → 每种排版生成一张）=====
  /** 版式库：官方推荐 / 我的 / 收藏 */
  getLayouts: () =>
    apiClient.get<{
      presets: AIImageLayout[];
      mine: AIImageLayout[];
      favorites: AIImageLayout[];
      counts?: { official?: number; structural?: number; mine?: number };
      limit: number;
      ratio_tags: { value: string; label: string }[];
    }>("/ai-image/layouts"),
  /** 上传自己的版式参考图 */
  uploadLayout: (file: File, name = "") => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name);
    return apiClient.post<AIImageLayout>("/ai-image/layouts/upload", fd, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 120000,
    });
  },
  /** 收藏 / 取消收藏 */
  favoriteLayout: (key: string, favorite: boolean) =>
    apiClient.post<{ key: string; favorited: boolean }>("/ai-image/layouts/favorite", {
      key,
      favorite,
    }),
  /** 删除自己上传的版式 */
  deleteLayout: (id: number) =>
    apiClient.delete<{ ok: boolean }>(`/ai-image/layouts/${id}`),
  /** 保存版式结构描述（分区文字约束，留空=只用参考图） */
  saveLayoutStructure: (id: number, structure: string) =>
    apiClient.put<{ id: number; structure: string }>(`/ai-image/layouts/${id}/structure`, {
      structure,
    }),
  /** AI 读版式图生成结构描述草稿（不落库） */
  suggestLayoutStructure: (id: number) =>
    apiClient.post<{ id: number; structure: string; name?: string }>(
      `/ai-image/layouts/${id}/structure/suggest`,
      {},
      { timeout: 120000 },
    ),
};

export default apiClient;
