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
  ) =>
    apiClient.post("/auth/register", { username, email, password, nickname }),

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
  getOverview: () => apiClient.get("/restock/overview"),
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
    if (params?.keyword) searchParams.append('keyword', params.keyword);
    if (params?.risk_level) params.risk_level.forEach(r => searchParams.append('risk_level', r));
    if (params?.account) params.account.forEach(a => searchParams.append('account', a));
    if (params?.country) params.country.forEach(c => searchParams.append('country', c));
    if (params?.fields) params.fields.forEach(f => searchParams.append('fields', f));
    return apiClient.get(`/restock/export?${searchParams.toString()}`, { responseType: 'blob' });
  },
  syncFeishuInbound: () => apiClient.post("/restock/sync-feishu-inbound"),
  getSyncFeishuStatus: () => apiClient.get("/restock/sync-feishu-status"),
  updateInspectionQuantity: (snapshotId: number, quantity: number) =>
    apiClient.put("/restock/inspection-quantity", null, { params: { snapshot_id: snapshotId, inspection_quantity: quantity } }),
  getSummaryChildren: (asin: string) =>
    apiClient.get("/restock/summary-children", { params: { asin } }),
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
    return apiClient.post(`/local-inventory/import-reduction?country=${encodeURIComponent(country)}`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  downloadReductionResult: (fileId: string) =>
    apiClient.get(`/local-inventory/import-reduction/result/${fileId}`, { responseType: 'blob' }),
};

// ========== Reviews API ==========
export const reviewsApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    asin_search?: string;
    product_name_search?: string;
    sku_search?: string;
    sort_by?: string;
    sort_order?: string;
    start_date?: string;
    end_date?: string;
    status?: string;
    importance_level?: string;
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
  updateImportance: (id: string, importance_level: string | undefined) =>
    apiClient.put(`/reviews/${id}/importance`, { importance_level }),
  batchAnalyze: (ids: string[]) =>
    apiClient.post("/reviews/analyze/batch", ids),
  getNewCount: () => apiClient.get("/reviews/new/count"),
};

// ========== Departments API ==========
export const departmentsApi = {
  getList: () => apiClient.get("/departments/"),
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
  createUser: (data: { username: string; email: string; role?: string }) =>
    apiClient.post("/departments/users", data),
  batchAssignDepartments: (data: {
    user_ids: number[];
    department_ids: number[];
  }) => apiClient.post("/departments/users/batch-assign", data),
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
  sendMessage: (message: string, sessionId?: string, chatType: string = "review") =>
    apiClient.post(
      "/chat",
      { message, session_id: sessionId, chat_type: chatType },
      { timeout: 300000 },
    ),
  getSessions: (chatType?: string) =>
    apiClient.get("/chat/sessions", { params: chatType ? { chat_type: chatType } : {} }),
  getSessionMessages: (sessionId: string) =>
    apiClient.get(`/chat/sessions/${sessionId}/messages`),

  deleteSession: (sessionId: string) =>
    apiClient.delete(`/chat/sessions/${sessionId}`)
};

// ========== Chat API (Streaming) ==========
export const chatStreamApi = {
  sendMessage: async (
    message: string,
    sessionId?: string,
    chatType: string = "review"
  ): Promise<Response> => {
    return fetch('/api/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('token') || ''}`
      },
      body: JSON.stringify({ message, session_id: sessionId, chat_type: chatType })
    });
  },

  searchSessions: (query: string, chatType?: string, limit?: number) =>
    apiClient.post("/chat/search", { query, chat_type: chatType, limit }),

  exportSession: (sessionId: string, format: 'markdown' | 'json' | 'txt' = 'markdown') =>
    apiClient.post("/chat/export", { session_id: sessionId, format }, { responseType: 'text' })
};

// ========== Stores API ==========
export const storesApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    name_search?: string;
    site_search?: string;
  }) => apiClient.get("/stores/", { params }),
  create: (data: {
    name: string;
    platform?: string;
    site?: string;
    platform_store_id?: string;
    department_id?: number;
  }) => apiClient.post("/stores/", data),
  update: (
    id: number,
    data: {
      name?: string;
      platform?: string;
      site?: string;
      platform_store_id?: string;
      department_id?: number;
      status?: string;
    },
  ) => apiClient.put(`/stores/${id}`, data),
  delete: (id: number) => apiClient.delete(`/stores/${id}`),
  batchUpdateDepartment: (data: {
    store_ids: number[];
    department_id?: number;
  }) => apiClient.post("/stores/batch-update-department", data),
};

// ========== Products API ==========
export const productsApi = {
  getList: (params?: {
    page?: number;
    page_size?: number;
    store_id?: number;
    asin_search?: string;
    sku_search?: string;
    name_search?: string;
  }) => apiClient.get("/products/", { params }),
  getById: (id: number) => apiClient.get(`/products/${id}`),
  create: (data: {
    store_id: number;
    asin: string;
    name: string;
    sku?: string;
    name_en?: string;
    image_url?: string;
    category?: string;
    brand?: string;
    price?: number;
    cost_price?: number;
    status?: string;
    is_robot_monitored?: boolean;
  }) => apiClient.post("/products/", data),
  update: (
    id: number,
    data: {
      store_id?: number;
      asin?: string;
      name?: string;
      sku?: string;
      name_en?: string;
      image_url?: string;
      category?: string;
      brand?: string;
      price?: number;
      cost_price?: number;
      status?: string;
      is_robot_monitored?: boolean;
    },
  ) => apiClient.put(`/products/${id}`, data),
  delete: (id: number) => apiClient.delete(`/products/${id}`),
};

// ========== Tenants API ==========
export const tenantsApi = {
  getList: () => apiClient.get("/tenants/"),
  getById: (id: number) => apiClient.get(`/tenants/${id}`),
  update: (
    id: number,
    data: {
      name?: string;
      code?: string;
      status?: string;
    },
  ) => apiClient.put(`/tenants/${id}`, data),
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
    apiClient.post(`/emails/${id}/ai-reply`, { requirements }, { timeout: 180000 }),
  batchUpdateFollowUp: (email_ids: string[], follow_up_status: number) =>
    apiClient.put('/emails/batch/follow-up', { email_ids, follow_up_status }),
  getDepartmentTodos: () => apiClient.get('/emails/department-todos'),
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

  listSettings: () =>
    apiClient.get<BusinessSetting[]>("/business-settings/"),

  updateSetting: (settingType: string, data: { formula_config: DailySalesConfig; is_active?: number }) =>
    apiClient.put<BusinessSetting>(`/business-settings/${settingType}`, data),

  resetSetting: (settingType: string) =>
    apiClient.post<BusinessSetting>(`/business-settings/reset/${settingType}`),
};

export default apiClient;
