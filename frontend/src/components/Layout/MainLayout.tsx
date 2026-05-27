import React, { useState, useEffect, useRef } from 'react'
import { Layout, Menu, theme, Dropdown, Avatar, Space, Typography, Badge, List, Button, Popover, Empty, Spin, Modal } from 'antd'
import { useNavigate, useLocation } from 'react-router-dom'
import {
  Home,
  Package,
  MessageSquare,
  Database,
  Bot,
  LogOut,
  User,
  Bell,
  Settings,
  Key,
  BarChart3,
  ClipboardList,
  Store,
  ShoppingBag,
  Users,
  Building2,
  Mail,
} from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import { useTheme } from '../../contexts/ThemeContext'
import ThemeSwitcher from '../ThemeSwitcher'
import ChangePasswordModal from '../ChangePasswordModal'
import { notificationsApi } from '../../api'
import dayjs from 'dayjs'

const { Header, Sider, Content } = Layout
const { Title, Text } = Typography

interface MainLayoutProps {
  children: React.ReactNode
}

interface Notification {
  id: number
  type: string
  title: string
  content: string
  link: string
  is_read: boolean
  created_at: string
}

const MainLayout: React.FC<MainLayoutProps> = ({ children }) => {
  const [collapsed, setCollapsed] = useState(false)
  const [changePasswordOpen, setChangePasswordOpen] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuth()
  const { currentTheme } = useTheme()
  const {
    token: { colorBgContainer, borderRadiusLG },
  } = theme.useToken()

  const [unreadCount, setUnreadCount] = useState(0)
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [notifLoading, setNotifLoading] = useState(false)
  const [notifOpen, setNotifOpen] = useState(false)
  const [selectedNotification, setSelectedNotification] = useState<Notification | null>(null)
  const [detailModalOpen, setDetailModalOpen] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const isAdmin = user?.role === 'admin'

  // 从通知列表计算未读数量
  const calculateUnreadCount = (notifList: Notification[]) => {
    return notifList.filter(n => !n.is_read).length
  }

  useEffect(() => {
    if (user) {
      fetchNotifications()
      fetchUnreadCount()
      pollRef.current = setInterval(fetchUnreadCount, 60000)
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [user])

  const fetchUnreadCount = async () => {
    try {
      const res = await notificationsApi.getUnreadCount()
      if (res.data.success) setUnreadCount(res.data.data.count)
    } catch (e) {
      // ignore
    }
  }

  const fetchNotifications = async () => {
    setNotifLoading(true)
    try {
      const res = await notificationsApi.getList({ page: 1, page_size: 10 })
      if (res.data.success) {
        const newNotifications = res.data.data
        setNotifications(newNotifications)
        setUnreadCount(calculateUnreadCount(newNotifications))
      }
    } catch (e) {
      // ignore
    } finally {
      setNotifLoading(false)
    }
  }

  const handleNotifOpen = (visible: boolean) => {
    setNotifOpen(visible)
    if (visible) fetchNotifications()
  }

  const handleMarkAsRead = async (id: number) => {
    try {
      await notificationsApi.markAsRead(id)
      setNotifications(prev => prev.map(n => n.id === id ? { ...n, is_read: true } : n))
      setUnreadCount(prev => Math.max(0, prev - 1))
    } catch (e) {
      // ignore
    }
  }

  const handleMarkAllRead = async () => {
    try {
      await notificationsApi.markAllAsRead()
      setNotifications(prev => prev.map(n => ({ ...n, is_read: true })))
      setUnreadCount(0)
    } catch (e) {
      // ignore
    }
  }

  const handleNotifClick = (notif: Notification) => {
    setSelectedNotification(notif)
    setDetailModalOpen(true)
    setNotifOpen(false)
    
    if (!notif.is_read) {
      handleMarkAsRead(notif.id)
    }
  }
  
  const handleDetailModalOk = () => {
    if (selectedNotification) {
      // 检查是否是差评通知
      const isReviewNotification = selectedNotification.title?.includes('未处理差评') || selectedNotification.type === 'warning'
      if (selectedNotification.link) {
        navigate(selectedNotification.link)
      } else if (isReviewNotification) {
        navigate('/review')
      }
    }
    setDetailModalOpen(false)
  }
  
  const handleGoToReview = () => {
    navigate('/review')
    setDetailModalOpen(false)
  }

  const menuItems = [
    {
      key: '/',
      icon: <Home size={20} />,
      label: '首页',
    },
    {
      key: '/todo',
      icon: <ClipboardList size={20} />,
      label: '待办事项',
    },
    {
      key: '/chat',
      icon: <Bot size={20} />,
      label: 'AI聊天助手',
    },
    {
      key: '/inventory',
      icon: <Package size={20} />,
      label: '库存机器人',
    },
    {
      key: '/review',
      icon: <MessageSquare size={20} />,
      label: '差评机器人',
    },
    {
      key: '/email',
      icon: <Mail size={20} />,
      label: '邮件机器人',
    },
    ...(isAdmin
      ? [
          {
            key: '/org',
            icon: <Users size={20} />,
            label: '角色管理',
          },
          {
            key: '/stores',
            icon: <Store size={20} />,
            label: '店铺管理',
          },
          {
            key: '/products',
            icon: <ShoppingBag size={20} />,
            label: '产品管理',
          },
          {
            key: '/tenants',
            icon: <Building2 size={20} />,
            label: '租户管理',
          },
        ]
      : []),
  ]

  const getPageTitle = () => {
    const pathMap: Record<string, string> = {
      '/': '首页',
      '/todo': '待办事项',
      '/chat': 'AI聊天助手',
      '/inventory': '库存机器人',
      '/review': '差评机器人',
      '/email': '邮件机器人',
      '/org': '角色管理',
      '/stores': '店铺管理',
      '/products': '产品管理',
      '/tenants': '租户管理',
    }
    return pathMap[location.pathname] || '未知页面'
  }

  const userMenuItems: any[] = [
    {
      key: 'changePassword',
      icon: <Key size={16} />,
      label: '修改密码',
      onClick: () => {
        setChangePasswordOpen(true)
      },
    },
    {
      type: 'divider',
    },
    {
      key: 'logout',
      icon: <LogOut size={16} />,
      label: '退出登录',
      onClick: () => {
        logout()
        navigate('/login')
      },
    },
  ]

  const notificationContent = (
    <div style={{ width: 400, maxHeight: 500, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 16px', borderBottom: '1px solid #f0f0f0', marginBottom: 0 }}>
        <Text strong style={{ fontSize: 14 }}>消息通知</Text>
        {unreadCount > 0 && (
          <Button type="link" size="small" onClick={handleMarkAllRead}>全部已读</Button>
        )}
      </div>
      {notifLoading ? (
        <div style={{ textAlign: 'center', padding: 32 }}><Spin /></div>
      ) : notifications.length === 0 ? (
        <Empty description="暂无通知" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <List
          style={{ overflow: 'auto', flex: 1, padding: '4px 0' }}
          dataSource={notifications}
          renderItem={(item) => (
            <List.Item
              style={{
                padding: '12px 16px',
                cursor: 'pointer',
                background: item.is_read ? 'transparent' : '#f6ffed',
                borderRadius: 6,
                marginBottom: 4,
                margin: '0 8px',
                border: '1px solid #f0f0f0'
              }}
              onClick={() => handleNotifClick(item)}
            >
              <List.Item.Meta
                avatar={
                  <Badge dot={!item.is_read}>
                    <Bell size={18} color={item.is_read ? '#999' : currentTheme.primary} />
                  </Badge>
                }
                title={
                  <Text style={{ fontSize: 14 }} strong={!item.is_read}>
                    {item.title}
                  </Text>
                }
                description={
                  <div>
                    <Text type="secondary" style={{ fontSize: 12, lineHeight: 1.6, display: 'block', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {item.content}
                    </Text>
                    <Text type="secondary" style={{ fontSize: 11, marginTop: 4, display: 'block' }}>
                      {dayjs(item.created_at).format('YYYY-MM-DD HH:mm')}
                    </Text>
                  </div>
                }
              />
            </List.Item>
          )}
        />
      )}
    </div>
  )

  return (
    <Layout style={{ height: '100vh', overflow: 'hidden' }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        theme="light"
        style={{ height: '100%' }}
      >
        <div style={{ height: 64, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <Bot size={32} color={currentTheme.primary} />
          {!collapsed && <span style={{ marginLeft: 8, fontSize: 18, fontWeight: 'bold', color: currentTheme.primary }}>宝鑫华盛AI</span>}
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{
            height: 'calc(100% - 64px)',
            overflowY: 'auto',
            '--ant-menu-item-selected-bg': currentTheme.selectedBg,
            '--ant-menu-item-selected-color': currentTheme.primary,
            '--ant-menu-item-color': currentTheme.primary,
            '--ant-color-primary': currentTheme.primary,
          } as React.CSSProperties}
        />
      </Sider>
      <Layout style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        <Header style={{ padding: '0 24px', background: colorBgContainer, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0, height: 64 }}>
          <Title level={4} style={{ margin: 0 }}>{getPageTitle()}</Title>
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
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
                  style={{ color: '#666' }}
                />
              </Badge>
            </Popover>
            <ThemeSwitcher />
            <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
              <Space style={{ cursor: 'pointer' }}>
                <Avatar 
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
            margin: '16px',
            padding: 0,
            background: colorBgContainer,
            borderRadius: borderRadiusLG,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            flex: 1,
            minHeight: 0
          }}
        >
          {children}
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
        footer={selectedNotification && (selectedNotification.title?.includes('未处理差评') || selectedNotification.type === 'warning') ? (
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <Button onClick={() => setDetailModalOpen(false)}>
              关闭
            </Button>
            <Button type="primary" onClick={handleGoToReview}>
              前往处理
            </Button>
          </div>
        ) : (
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button onClick={() => setDetailModalOpen(false)}>
              关闭
            </Button>
          </div>
        )}
        width={500}
      >
        {selectedNotification && (
          <div style={{ padding: '8px 0' }}>
            <div style={{ marginBottom: 16 }}>
              <Text strong style={{ fontSize: 16 }}>{selectedNotification.title}</Text>
            </div>
            
            <div style={{ 
              background: '#f5f5f5', 
              padding: 16, 
              borderRadius: 8, 
              marginBottom: 16,
              lineHeight: 1.8
            }}>
              <Text style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {selectedNotification.content}
              </Text>
            </div>
            
            <div style={{ display: 'flex', justifyContent: 'space-between', color: '#999', fontSize: 13 }}>
              <span>
                <Bell size={14} style={{ marginRight: 4, display: 'inline' }} />
                {selectedNotification.type === 'warning' ? '警告' : '通知'}
              </span>
              <span>{dayjs(selectedNotification.created_at).format('YYYY年MM月DD日 HH:mm')}</span>
            </div>
          </div>
        )}
      </Modal>
    </Layout>
  )
}

export default MainLayout
