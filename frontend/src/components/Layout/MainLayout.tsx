import React, { useState, useEffect, useRef } from "react";
import {
  Layout,
  Menu,
  theme,
  Dropdown,
  Avatar,
  Space,
  Typography,
  Badge,
  List,
  Button,
  Popover,
  Empty,
  Input,
  Spin,
  Modal,
} from "antd";
import { useNavigate, useLocation } from "react-router-dom";
import {
  Home,
  Package,
  MessageSquare,
  Bot,
  LogOut,
  User,
  Bell,
  Key,
  ClipboardList,
  Store,
  ShoppingBag,
  Users,
  Building2,
  Target,
  ArrowDownCircle,
  ArrowUpCircle,
  Truck,
  ArrowLeftRight,
  Shield,
  Warehouse,
  Boxes,
  Settings,
  Mail,
  Megaphone,
  PackagePlus,
  Ship,
  Contact,
  ChevronLeft,
  ChevronRight,
  Search,
  Star,
  AlertTriangle,
  Sparkles,
  Table2,
  Rocket,
  Video,
  Film,
  Image as ImageIcon,
  ClipboardCheck,
} from "lucide-react";
import { useAuth } from "../../contexts/AuthContext";
import { useTheme } from "../../contexts/ThemeContext";
import ThemeSwitcher from "../ThemeSwitcher";
import ChangePasswordModal from "../ChangePasswordModal";
import ErrorBoundary from "../ErrorBoundary";
import KeepAliveHost from "../KeepAliveHost";
import { notificationsApi } from "../../api";
import dayjs from "dayjs";

const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;

interface MainLayoutProps {
  children: React.ReactNode;
}

interface Notification {
  id: number;
  type: string;
  title: string;
  content: string;
  link: string;
  is_read: boolean;
  created_at: string;
}

const MainLayout: React.FC<MainLayoutProps> = ({ children }) => {
  const [collapsed, setCollapsed] = useState(false);
  const [changePasswordOpen, setChangePasswordOpen] = useState(false);
  const [menuSearchText, setMenuSearchText] = useState("");
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout, hasPermission } = useAuth();
  const { currentTheme } = useTheme();
  const {
    token: { colorBgContainer, borderRadiusLG },
  } = theme.useToken();

  const [unreadCount, setUnreadCount] = useState(0);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [notifLoading, setNotifLoading] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [selectedNotification, setSelectedNotification] =
    useState<Notification | null>(null);
  const [detailModalOpen, setDetailModalOpen] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const isAdmin = user?.role === "admin";

  // 从通知列表计算未读数量
  const calculateUnreadCount = (notifList: Notification[]) => {
    return notifList.filter((n) => !n.is_read).length;
  };

  useEffect(() => {
    if (user) {
      fetchNotifications();
      fetchUnreadCount();
      pollRef.current = setInterval(fetchUnreadCount, 60000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [user]);

  const fetchUnreadCount = async () => {
    try {
      const res = await notificationsApi.getUnreadCount();
      if (res.data.success) setUnreadCount(res.data.data.count);
    } catch (e) {
      // ignore
    }
  };

  const fetchNotifications = async () => {
    setNotifLoading(true);
    try {
      const res = await notificationsApi.getList({ page: 1, page_size: 10 });
      if (res.data.success) {
        const newNotifications = res.data.data;
        setNotifications(newNotifications);
        // 获取完整的未读总数
        const countRes = await notificationsApi.getUnreadCount();
        if (countRes.data.success) setUnreadCount(countRes.data.data.count);
      }
    } catch (e) {
      // ignore
    } finally {
      setNotifLoading(false);
    }
  };

  const handleNotifOpen = (visible: boolean) => {
    setNotifOpen(visible);
    if (visible) fetchNotifications();
  };

  const handleMarkAsRead = async (id: number) => {
    try {
      await notificationsApi.markAsRead(id);
      setNotifications((prev) =>
        prev.map((n) => (n.id === id ? { ...n, is_read: true } : n)),
      );
      setUnreadCount((prev) => Math.max(0, prev - 1));
    } catch (e) {
      // ignore
    }
  };

  const handleMarkAllRead = async () => {
    try {
      await notificationsApi.markAllAsRead();
      setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
      setUnreadCount(0);
    } catch (e) {
      // ignore
    }
  };

  const handleNotifClick = (notif: Notification) => {
    setSelectedNotification(notif);
    setDetailModalOpen(true);
    setNotifOpen(false);

    if (!notif.is_read) {
      handleMarkAsRead(notif.id);
    }
  };

  const handleGoToReview = () => {
    if (selectedNotification?.link) {
      navigate(selectedNotification.link);
    } else {
      navigate("/review");
    }
    setDetailModalOpen(false);
  };

  const inventoryPaths = [
    "/replenishment",
    "/purchase",
    "/inbound",
    "/outbound",
    "/stock-transfer",
    "/shipment",
    "/warehouses",
    "/suppliers",
    "/base-table",
  ];
  const systemPaths = [
    "/org",
    "/permissions",
    "/operation-logs",
    "/tenants",
    "/stores",
  ];
  const mediaPaths = ["/ai-creation", "/ai-image"];

  const [openKeys, setOpenKeys] = useState<string[]>(() => {
    const currentPath = location.pathname;
    const keys: string[] = [];
    if (inventoryPaths.includes(currentPath)) keys.push("inventory-group");
    if (systemPaths.includes(currentPath)) keys.push("system-group");
    if (mediaPaths.includes(currentPath)) keys.push("media-creation");
    return keys;
  });

  useEffect(() => {
    const currentPath = location.pathname;
    setOpenKeys((prev) => {
      const next = [...prev];
      if (
        inventoryPaths.includes(currentPath) &&
        !next.includes("inventory-group")
      ) {
        next.push("inventory-group");
      }
      if (systemPaths.includes(currentPath) && !next.includes("system-group")) {
        next.push("system-group");
      }
      if (mediaPaths.includes(currentPath) && !next.includes("media-creation")) {
        next.push("media-creation");
      }
      return next;
    });
  }, [location.pathname]);

  const menuItems = [
    {
      key: "/",
      icon: <Home size={20} />,
      label: "首页",
    },
    {
      key: "/todo",
      icon: <ClipboardList size={20} />,
      label: "KPI",
    },
    ...(hasPermission("chat:use")
      ? [
          {
            key: "/chat",
            icon: <Bot size={20} />,
            label: "AI聊天助手",
            shortLabel: "AI",
          },
        ]
      : []),
    ...(hasPermission("robot:inventory:view")
      ? [
          {
            key: "/inventory",
            icon: <Package size={20} />,
            label: "库存机器人",
            shortLabel: "库存",
          },
        ]
      : []),
    ...(hasPermission("robot:review:view")
      ? [
          {
            key: "/review",
            icon: <MessageSquare size={20} />,
            label: "差评机器人",
            shortLabel: "差评",
          },
        ]
      : []),
    ...(hasPermission("robot:email:view")
      ? [
          {
            key: "/email",
            icon: <Mail size={20} />,
            label: "邮件机器人",
            shortLabel: "邮件",
          },
        ]
      : []),
    ...(hasPermission("robot:rating:view")
      ? [
          {
            key: "/rating-optimization",
            icon: <Star size={20} />,
            label: "页面优化机器人",
            shortLabel: "页面优化",
          },
        ]
      : []),
    ...(hasPermission("robot:ad:view")
      ? [
          {
            key: "/ads",
            icon: <Megaphone size={20} />,
            label: "广告机器人",
            shortLabel: "广告",
          },
        ]
      : []),     
    ...(hasPermission('product_selection:view')
      ? [{
          key: '/product-selection',
          icon: <Target size={20} />,
          label: '选品机器人',
        }] : []),
    ...(hasPermission('ai_video:use') || hasPermission('ai_image:use')
      ? [{
          key: 'media-creation',
          icon: <Sparkles size={20} />,
          label: '媒体创作中心',
          shortLabel: '媒体',
          children: [
            ...(hasPermission('ai_video:use')
              ? [{
                  key: '/ai-creation',
                  icon: <Video size={18} />,
                  label: 'AI视频',
                }]
              : []),
            // AI生图有独立权限码：只给美工开生图、不给视频时也要能进
            ...(hasPermission('ai_image:use')
              ? [{
                  key: '/ai-image',
                  icon: <ImageIcon size={18} />,
                  label: 'AI生图',
                }]
              : []),
            ...(hasPermission('ai_video:use')
              ? [{
                  key: '/upscale-center',
                  icon: <Film size={18} />,
                  label: '高清处理',
                }]
              : []),
            ...(hasPermission('ai_video:approve')
              ? [{
                  key: '/video-binding-review',
                  icon: <ClipboardCheck size={18} />,
                  label: '媒体绑定审核',
                }]
              : []),
          ],
        }] : []),
    ...(hasPermission("development:view")
      ? [{
          key: "/development",
          icon: <Rocket size={20} />,
          label: "开发机器人",
          shortLabel: "开发",
        }]
      : []),
    {
      key: "/data-alert",
      icon: <AlertTriangle size={20} />,
      label: "数据驾驶舱",
    },
    ...(hasPermission("product:view")
      ? [
          {
            key: "/products",
            icon: <ShoppingBag size={20} />,
            label: "产品管理",
            shortLabel: "产品",
          },
        ]
      : []),
    {
      key: "inventory-group",
      icon: <Boxes size={20} />,
      label: "进销存",
      children: [
        ...(hasPermission('replenishment:view')
          ? [{
              key: '/replenishment',
              icon: <PackagePlus size={18} />,
              label: '补货管理',
            }] : []),
        ...(hasPermission('purchase:view')
          ? [{
              key: '/purchase',
              icon: <Truck size={18} />,
              label: '采购管理',
            }] : []),
        ...(hasPermission('inbound:view')
          ? [{
              key: '/inbound',
              icon: <ArrowDownCircle size={18} />,
              label: '入库管理',
            }] : []),
        ...(hasPermission('outbound:view')
          ? [{
              key: '/outbound',
              icon: <ArrowUpCircle size={18} />,
              label: '出库管理',
            }] : []),
        ...(hasPermission('stock_transfer:view')
          ? [{
              key: '/stock-transfer',
              icon: <ArrowLeftRight size={18} />,
              label: '挪货管理',
            }] : []),
        ...(hasPermission('shipment:view')
          ? [{
              key: '/shipment',
              icon: <Ship size={18} />,
              label: '发货管理',
            }] : []),
        ...(hasPermission('warehouse:view')
          ? [{
              key: '/warehouses',
              icon: <Warehouse size={18} />,
              label: '仓库管理',
            }] : []),
        ...(hasPermission('supplier:view')
          ? [{
              key: '/suppliers',
              icon: <Contact size={18} />,
              label: '供应商管理',
            }] : []),
        ...(hasPermission('base_table:view')
          ? [{
              key: '/base-table',
              icon: <Table2 size={18} />,
              label: '底表管理',
            }] : []),
      ] as any[],
    },
    {
      key: "system-group",
      icon: <Settings size={20} />,
      label: "系统设置",
      shortLabel: "系统",
      children: [
        ...(hasPermission("org:view")
          ? [
              {
                key: "/org",
                icon: <Users size={18} />,
                label: "组织管理",
              },
            ]
          : []),
        ...(hasPermission("permission:view")
          ? [
              {
                key: "/permissions",
                icon: <Shield size={18} />,
                label: "权限管理",
              },
            ]
          : []),
        ...(hasPermission("log:view")
          ? [
              {
                key: "/operation-logs",
                icon: <ClipboardList size={18} />,
                label: "操作日志",
              },
            ]
          : []),
        {
          key: "/tenants",
          icon: <Building2 size={18} />,
          label: "公司设置",
        },
        ...(hasPermission("robot:inventory:settings")
          ? [
              {
                key: "/business-settings",
                icon: <Settings size={20} />,
                label: "业务设置",
              },
            ]
          : []),
        ...(hasPermission("store:view")
          ? [
              {
                key: "/stores",
                icon: <Store size={18} />,
                label: "店铺管理",
              },
            ]
          : []),
      ] as any[],
    },
  ].filter((item) => !item.children || item.children.length > 0);

  // 菜单搜索：按名称过滤菜单项，分组内有命中子项时保留分组并展开
  const menuKeyword = menuSearchText.trim().toLowerCase();
  const isMenuSearching = menuKeyword.length > 0;
  const filteredMenuItems = isMenuSearching
    ? menuItems
        .map((item: any) => {
          if (item.children) {
            const matchedChildren = item.children.filter((c: any) =>
              String(c.label || "").toLowerCase().includes(menuKeyword),
            );
            if (matchedChildren.length > 0) {
              return { ...item, children: matchedChildren };
            }
            return String(item.label || "").toLowerCase().includes(menuKeyword)
              ? item
              : null;
          }
          return String(item.label || "").toLowerCase().includes(menuKeyword)
            ? item
            : null;
        })
        .filter(Boolean)
    : menuItems;
  const searchedOpenKeys = filteredMenuItems
    .filter((item: any) => item.children)
    .map((item: any) => item.key);

  const getPageTitle = () => {
    const pathMap: Record<string, string> = {
      "/": "首页",
      "/todo": "KPI",
      "/chat": "AI聊天助手",
      "/inventory": "库存机器人",
      "/business-settings": "业务设置",
      "/review": "差评机器人",
      "/email": "邮件机器人",
      "/ads": "广告机器人",
      "/rating-optimization": "页面优化机器人",
      "/data-alert": "数据驾驶舱",
      "/org": "组织管理",
      "/permissions": "权限管理",
      "/stores": "店铺管理",
      "/products": "产品管理",
      "/inbound": "入库管理",
      "/outbound": "出库管理",
      "/purchase": "采购管理",
      "/replenishment": "补货管理",
      "/stock-transfer": "挪货管理",
      "/shipment": "发货管理",
      "/suppliers": "供应商管理",
      "/warehouses": "仓库管理",
      '/base-table': '底表管理',
      "/operation-logs": "操作日志",
      "/tenants": "公司设置",
      "/product-selection": "选品机器人",
      "/development": "开发机器人",
      "/development/new": "添加新品",
      '/ai-creation': 'AI视频',
      '/ai-image': 'AI生图',
      '/upscale-center': '高清处理',
      '/video-binding-review': '媒体绑定审核',
    };
    return pathMap[location.pathname] || "未知页面";
  };

  const userMenuItems: any[] = [
    {
      key: "changePassword",
      icon: <Key size={16} />,
      label: "修改密码",
      onClick: () => {
        setChangePasswordOpen(true);
      },
    },
    {
      type: "divider",
    },
    {
      key: "logout",
      icon: <LogOut size={16} />,
      label: "退出登录",
      onClick: () => {
        logout();
        navigate("/login");
      },
    },
  ];

  const notificationContent = (
    <div
      style={{
        width: 400,
        maxHeight: 500,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "12px 16px",
          borderBottom: "1px solid #f0f0f0",
          marginBottom: 0,
        }}
      >
        <Text strong style={{ fontSize: 14 }}>
          消息通知
        </Text>
        {unreadCount > 0 && (
          <Button type="link" size="small" onClick={handleMarkAllRead}>
            全部已读
          </Button>
        )}
      </div>
      {notifLoading ? (
        <div style={{ textAlign: "center", padding: 32 }}>
          <Spin />
        </div>
      ) : notifications.length === 0 ? (
        <Empty description="暂无通知" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <List
          style={{ overflow: "auto", flex: 1, padding: "4px 0" }}
          dataSource={notifications}
          renderItem={(item) => (
            <List.Item
              style={{
                padding: "12px 16px",
                cursor: "pointer",
                background: item.is_read ? "transparent" : "#f6ffed",
                borderRadius: 6,
                marginBottom: 4,
                margin: "0 8px",
                border: "1px solid #f0f0f0",
              }}
              onClick={() => handleNotifClick(item)}
            >
              <List.Item.Meta
                avatar={
                  <Badge dot={!item.is_read}>
                    <Bell
                      size={18}
                      color={item.is_read ? "#999" : currentTheme.primary}
                    />
                  </Badge>
                }
                title={
                  <Text style={{ fontSize: 14 }} strong={!item.is_read}>
                    {item.title}
                  </Text>
                }
                description={
                  <div>
                    <Text
                      type="secondary"
                      style={{
                        fontSize: 12,
                        lineHeight: 1.6,
                        display: "block",
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                      }}
                    >
                      {item.content}
                    </Text>
                    <Text
                      type="secondary"
                      style={{ fontSize: 11, marginTop: 4, display: "block" }}
                    >
                      {dayjs(item.created_at).format("YYYY-MM-DD HH:mm")}
                    </Text>
                  </div>
                }
              />
            </List.Item>
          )}
        />
      )}
    </div>
  );

  // 折叠菜单项：图标 + 小字标题
  const CollapsedMenuItem = ({
    icon,
    label,
    shortLabel,
    active,
    onClick,
    children,
  }: {
    icon: React.ReactNode;
    label: string;
    shortLabel?: string;
    active?: boolean;
    onClick?: () => void;
    children?: React.ReactNode;
  }) => {
    const itemContent = (
      <div
        onClick={onClick}
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "8px 0",
          cursor: "pointer",
          color: active ? currentTheme.primary : "#666",
          background: active ? currentTheme.selectedBg : "transparent",
          transition: "all 0.2s",
          height: 56,
          width: "100%",
          borderRight: active ? `2px solid ${currentTheme.primary}` : "none",
        }}
      >
        {icon}
        <span
          style={{
            fontSize: 10,
            marginTop: 3,
            lineHeight: 1.2,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            maxWidth: 64,
            textAlign: "center",
          }}
        >
          {shortLabel || label}
        </span>
      </div>
    );

    if (children) {
      return (
        <Dropdown
          menu={{
            items: (children as any[]).map((child) => ({
              key: child.key,
              icon: child.icon,
              label: child.label,
            })),
            selectedKeys: [location.pathname],
            onClick: ({ key }) => navigate(key),
            style: { minWidth: 160 },
          }}
          placement="bottomLeft"
          getPopupContainer={() => document.body}
        >
          {itemContent}
        </Dropdown>
      );
    }

    return itemContent;
  };

  // 构建折叠状态的菜单列表
  const renderCollapsedMenu = () => (
    <div style={{ flex: 1, overflowY: "auto", overflowX: "hidden" }}>
      {menuItems.map((item: any) => {
        if (item.children && item.children.length > 0) {
          // 子菜单组：图标+小字，hover显示Dropdown
          return (
            <CollapsedMenuItem
              key={item.key}
              icon={item.icon}
              label={item.label}
              shortLabel={item.shortLabel}
            >
              {item.children}
            </CollapsedMenuItem>
          );
        }
        // 普通菜单项
        return (
          <CollapsedMenuItem
            key={item.key}
            icon={item.icon}
            label={item.label}
            shortLabel={item.shortLabel}
            active={location.pathname.startsWith("/development") ? item.key === "/development" : location.pathname === item.key}
            onClick={() => navigate(item.key as string)}
          />
        );
      })}
    </div>
  );

  return (
    <div style={{ height: "100vh", overflowX: "auto", overflowY: "hidden" }}>
      <Layout style={{ minWidth: 1480, height: "100%" }}>
        {/* 固定侧边栏：小屏也保持桌面布局，通过整体横向滚动查看 */}
        <Sider
          collapsible
          collapsed={collapsed}
          onCollapse={setCollapsed}
          trigger={null}
          theme="light"
          width={200}
          collapsedWidth={80}
          style={{
            height: "100vh",
            position: "sticky",
            top: 0,
            flexShrink: 0,
            overflow: "visible",
          }}
        >
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              height: "100%",
              overflow: "visible",
            }}
          >
            {/* Logo */}
            <div
              style={{
                height: 64,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                padding: collapsed ? 8 : 16,
                flexShrink: 0,
              }}
            >
              <Bot size={collapsed ? 28 : 32} color={currentTheme.primary} />
              {!collapsed && (
                <span
                  style={{
                    marginLeft: 8,
                    fontSize: 18,
                    fontWeight: "bold",
                    color: currentTheme.primary,
                  }}
                >
                  宝鑫华盛AI
                </span>
              )}
            </div>

            {/* 菜单搜索框（折叠时显示搜索图标，点击展开侧边栏） */}
            {collapsed ? (
              <div
                style={{
                  padding: "8px 0",
                  flexShrink: 0,
                  display: "flex",
                  justifyContent: "center",
                }}
              >
                <Button
                  type="text"
                  icon={<Search size={18} />}
                  onClick={() => setCollapsed(false)}
                />
              </div>
            ) : (
              <div style={{ padding: "0 12px 8px", flexShrink: 0 }}>
                <Input
                  size="small"
                  placeholder="搜索菜单..."
                  prefix={<Search size={14} color="#999" />}
                  value={menuSearchText}
                  onChange={(e) => setMenuSearchText(e.target.value)}
                  allowClear
                />
              </div>
            )}

            {/* 菜单区域 */}
            {collapsed ? (
              renderCollapsedMenu()
            ) : isMenuSearching && filteredMenuItems.length === 0 ? (
              <Empty
                description="无匹配菜单"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                style={{ marginTop: 48 }}
              />
            ) : (
              <Menu
                mode="inline"
                selectedKeys={[location.pathname.startsWith("/development") ? "/development" : location.pathname]}
                openKeys={isMenuSearching ? searchedOpenKeys : openKeys}
                onOpenChange={(keys) => setOpenKeys(keys)}
                items={filteredMenuItems.map(
                  ({ shortLabel, ...rest }: any) => rest,
                )}
                onClick={({ key }) => navigate(key)}
                style={
                  {
                    flex: 1,
                    overflowY: "auto",
                    overflowX: "visible",
                    "--ant-menu-item-selected-bg": currentTheme.selectedBg,
                    "--ant-menu-item-selected-color": currentTheme.primary,
                    "--ant-menu-item-color": currentTheme.primary,
                    "--ant-color-primary": currentTheme.primary,
                  } as React.CSSProperties
                }
              />
            )}

            {/* 折叠/展开按钮 */}
            <div
              onClick={() => setCollapsed(!collapsed)}
              style={{
                height: 48,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                cursor: "pointer",
                borderTop: "1px solid #f0f0f0",
                color: "#999",
                transition: "color 0.2s",
                flexShrink: 0,
              }}
              onMouseEnter={(e) => (e.currentTarget.style.color = "#333")}
              onMouseLeave={(e) => (e.currentTarget.style.color = "#999")}
            >
              {collapsed ? (
                <ChevronRight size={18} />
              ) : (
                <ChevronLeft size={18} />
              )}
            </div>
          </div>
        </Sider>
        <Layout
          style={{
            display: "flex",
            flexDirection: "column",
            minWidth: 1280,
            flexShrink: 0,
          }}
        >
          <Header
            style={{
              padding: "0 24px",
              background: colorBgContainer,
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              flexShrink: 0,
              height: 56,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                minWidth: 0,
              }}
            >
              <Title
                level={4}
                style={{
                  margin: 0,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {getPageTitle()}
              </Title>
            </div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 16,
                flexShrink: 0,
              }}
            >
              <Popover
                content={notificationContent}
                trigger="click"
                open={notifOpen}
                onOpenChange={handleNotifOpen}
                placement="bottomRight"
              >
                <Badge count={unreadCount} size="small" offset={[-2, 2]}>
                  <Button
                    type="text"
                    icon={<Bell size={20} />}
                    style={{ color: "#666" }}
                  />
                </Badge>
              </Popover>
              <ThemeSwitcher />
              <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
                <Space style={{ cursor: "pointer" }}>
                  <Avatar
                    size="default"
                    style={{ backgroundColor: currentTheme.avatarBg }}
                    icon={<User size={16} />}
                  />
                  <span>{user?.nickname || user?.username}</span>
                </Space>
              </Dropdown>
            </div>
          </Header>
          <Content
            style={{
              margin: "16px 16px 0 16px",
              padding: 0,
              background: colorBgContainer,
              borderRadius: borderRadiusLG,
              display: "flex",
              flexDirection: "column",
              /* 必须 hidden（与合并前一致）：本布局高度链被 100vh 锁死，
                 Content 是一个「高度=视口剩余」的固定滚动容器。子页面
                 （如库存页）根 div 为 height:100% + overflowY:auto，
                 其 position:sticky 分页控件相对它吸附，才能恒贴视口底。
                 改成 visible/auto 会让页面根 div 随内容撑高、自身永不
                 滚动，sticky 失效，分页掉到内容底部（合并后的回归根因）。 */
              overflow: "hidden",
              flex: 1,
              minHeight: 0,
              minWidth: 1280,
            }}
          >
            {/* 页面级错误边界：单个页面崩溃不影响侧边栏/顶栏，切换路由自动重置 */}
            <ErrorBoundary resetKey={location.pathname}>
              {children}
            </ErrorBoundary>
            {/* keep-alive宿主：常驻缓存页面，切换路由仅隐藏不卸载；独立错误边界避免被页面崩溃波及 */}
            <ErrorBoundary>
              <KeepAliveHost />
            </ErrorBoundary>
          </Content>
        </Layout>
        <ChangePasswordModal
          open={changePasswordOpen}
          onCancel={() => setChangePasswordOpen(false)}
        />
        <Modal
          title="通知详情"
          open={detailModalOpen}
          onCancel={() => setDetailModalOpen(false)}
          footer={
            selectedNotification &&
            (selectedNotification.title?.includes("未处理差评") ||
              selectedNotification.type === "warning") ? (
              <div
                style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}
              >
                <Button onClick={() => setDetailModalOpen(false)}>关闭</Button>
                <Button type="primary" onClick={handleGoToReview}>
                  前往处理
                </Button>
              </div>
            ) : (
              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <Button onClick={() => setDetailModalOpen(false)}>关闭</Button>
              </div>
            )
          }
          width={500}
        >
          {selectedNotification && (
            <div style={{ padding: "8px 0" }}>
              <div style={{ marginBottom: 16 }}>
                <Text strong style={{ fontSize: 16 }}>
                  {selectedNotification.title}
                </Text>
              </div>

              <div
                style={{
                  background: "#f5f5f5",
                  padding: 16,
                  borderRadius: 8,
                  marginBottom: 16,
                  lineHeight: 1.8,
                }}
              >
                <Text
                  style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
                >
                  {selectedNotification.content}
                </Text>
              </div>

              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  color: "#999",
                  fontSize: 13,
                }}
              >
                <span>
                  <Bell
                    size={14}
                    style={{ marginRight: 4, display: "inline" }}
                  />
                  {selectedNotification.type === "warning" ? "警告" : "通知"}
                </span>
                <span>
                  {dayjs(selectedNotification.created_at).format(
                    "YYYY年MM月DD日 HH:mm",
                  )}
                </span>
              </div>
            </div>
          )}
        </Modal>
      </Layout>
    </div>
  );
};

export default MainLayout;
