import React, { useState, useEffect } from 'react'
import { Card, Row, Col, Typography, Select, Empty } from 'antd'
import { MessageSquare, Package, Bot, ChevronRight, Mail } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { reviewsApi, notificationsApi, inventoryApi,emailsApi } from '../api'

const { Title, Text } = Typography

interface ReviewItem {
  id: string
  status: 'new' | 'read' | 'processing' | 'resolved'
  importanceLevel?: string
  asin?: string
  rating?: number
}

const Home: React.FC = () => {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [reviews, setReviews] = useState<ReviewItem[]>([])
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [inventoryStats, setInventoryStats] = useState<{ red: number; yellow: number; green: number } | null>(null)
  const [loading, setLoading] = useState(true)
  const [emailStats, setEmailStats] = useState<{ urgent: number; medium: number; normal: number; total: number }>({ urgent: 0, medium: 0, normal: 0, total: 0 })
  const [filterBot, setFilterBot] = useState<string | undefined>(undefined)
  
  const isHighLevel = (level?: string | null) => {
    return level === 'high' || level === '严重'
  }
  
  const isMediumLevel = (level?: string | null) => {
    return level === 'medium' || level === '中等'
  }
  
  const isLowLevel = (level?: string | null) => {
    return level === 'low' || level === '轻微'
  }
  
  const getGreeting = () => {
    const hour = new Date().getHours()
    if (hour < 12) return '早上好'
    if (hour < 18) return '下午好'
    return '晚上好'
  }

  useEffect(() => {
    fetchData()
  }, [])

  const fetchData = async () => {
    try {
      setLoading(true)
      const [reviewsRes, inventoryRes, emailCountRes] = await Promise.all([
        reviewsApi.getList({ page_size: 100 }),
        emailsApi.getUnfollowedCount(),
        inventoryApi.getOverview()
      ])

      if (reviewsRes.data.success) {
        const data = reviewsRes.data.data
        
        // 去重
        const uniqueReviews = data.filter((item: ReviewItem, index: number, self: ReviewItem[]) => 
          index === self.findIndex((t) => t.id === item.id)
        )
        
        setReviews(uniqueReviews)
      }

      if (emailCountRes.data.success) {
        setEmailStats(emailCountRes.data.data)
      }
      if (inventoryRes.data.success) {
        const d = inventoryRes.data.data
        setInventoryStats({ red: d.red_count || 0, yellow: d.yellow_count || 0, green: d.green_count || 0 })
      }
    } catch (error) {
      console.error('获取数据失败:', error)
    } finally {
      setLoading(false)
    }
  }

  const reviewStats = {
    high: {
      unhandled: reviews.filter(r => isHighLevel(r.importanceLevel) && r.status !== 'resolved').length,
      handled: reviews.filter(r => isHighLevel(r.importanceLevel) && r.status === 'resolved').length
    },
    medium: {
      unhandled: reviews.filter(r => isMediumLevel(r.importanceLevel) && r.status !== 'resolved').length,
      handled: reviews.filter(r => isMediumLevel(r.importanceLevel) && r.status === 'resolved').length
    },
    low: {
      unhandled: reviews.filter(r => isLowLevel(r.importanceLevel) && r.status !== 'resolved').length,
      handled: reviews.filter(r => isLowLevel(r.importanceLevel) && r.status === 'resolved').length
    }
  }

  const allBots = [
    {
      id: 'review',
      title: '差评机器人',
      icon: <MessageSquare size={32} />,
      color: '#cf1322',
      description: '智能分析差评，快速响应客户反馈',
      path: '/review',
      stats: reviewStats,
      hasPending: reviewStats.high.unhandled + reviewStats.medium.unhandled + reviewStats.low.unhandled > 0,
      priority: reviewStats.high.unhandled > 0 ? 0 : reviewStats.medium.unhandled > 0 ? 1 : 2,
    },
    {
      id: 'inventory',
      title: '库存机器人',
      icon: <Package size={32} />,
      color: '#faad14',
      description: '实时监控库存，智能预警提醒',
      path: '/inventory',
      // stats: null,
      // hasPending: false,
      // priority: 3,
      stats: inventoryStats
    },
    {
      id: 'chat',
      title: 'AI聊天助手',
      icon: <Bot size={32} />,
      color: '#1890ff',
      description: '智能问答，高效解决问题',
      path: '/chat',
      stats: null,
      hasPending: false,
      priority: 3,
    },
    {
      id: 'email',
      title: '邮件机器人',
      icon: <Mail size={32} />,
      color: '#722ed1',
      description: '智能邮件处理，及时跟进客户邮件',
      path: '/email',
      stats: 'email' as const,
      hasPending: emailStats.urgent + emailStats.medium + emailStats.normal > 0,
      priority: emailStats.urgent > 0 ? 0 : emailStats.medium > 0 ? 1 : 2,
    },
  ]

  const visibleBots = allBots
    .filter(bot => bot.hasPending)
    .sort((a, b) => a.priority - b.priority)

  const filteredBots = filterBot
    ? allBots.filter(bot => bot.id === filterBot)
    : visibleBots

  const filterOptions = allBots.map(bot => ({
    value: bot.id,
    label: bot.title,
  }))

  const renderReviewStats = (stats: any) => {
    return (
      <div style={{ marginTop: 16 }}>
        <Row gutter={[8, 8]}>
          <Col span={8}>
            <div style={{ textAlign: 'center', padding: '8px 0', background: '#fff2f0', borderRadius: 8 }}>
              <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>严重</div>
              <div style={{ fontSize: 20, fontWeight: 'bold', color: '#cf1322' }}>{stats.high.unhandled}</div>
            </div>
          </Col>
          <Col span={8}>
            <div style={{ textAlign: 'center', padding: '8px 0', background: '#fffbe6', borderRadius: 8 }}>
              <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>中等</div>
              <div style={{ fontSize: 20, fontWeight: 'bold', color: '#faad14' }}>{stats.medium.unhandled}</div>
            </div>
          </Col>
          <Col span={8}>
            <div style={{ textAlign: 'center', padding: '8px 0', background: '#e6f7ff', borderRadius: 8 }}>
              <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>轻微</div>
              <div style={{ fontSize: 20, fontWeight: 'bold', color: '#1890ff' }}>{stats.low.unhandled}</div>
            </div>
          </Col>
        </Row>
      </div>
    )
  }

 const renderEmailStats = () => {
    
    return (
      <div style={{ marginTop: 16 }}>
        <Row gutter={[8, 8]}>
          <Col span={8}>
            <div style={{ textAlign: 'center', padding: '8px 0', background: '#fff2f0', borderRadius: 8 }}>
              <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>紧急</div>
              <div style={{ fontSize: 20, fontWeight: 'bold', color: '#cf1322' }}>{emailStats.urgent}</div>
            </div>
          </Col>
          <Col span={8}>
            <div style={{ textAlign: 'center', padding: '8px 0', background: '#fffbe6', borderRadius: 8 }}>
              <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>中等</div>
              <div style={{ fontSize: 20, fontWeight: 'bold', color: '#faad14' }}>{emailStats.medium}</div>
            </div>
          </Col>
          <Col span={8}>
            <div style={{ textAlign: 'center', padding: '8px 0', background: '#e6f7ff', borderRadius: 8 }}>
              <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>一般</div>
              <div style={{ fontSize: 20, fontWeight: 'bold', color: '#1890ff' }}>{emailStats.normal}</div>
            </div>
          </Col>
        </Row>
      </div>
    )
  }
    
  const renderInventoryStats = (stats: any) => {
    return (
      <div style={{ marginTop: 16 }}>
        <Row gutter={[8, 8]}>
          <Col span={8}>
            <div style={{ textAlign: 'center', padding: '8px 0', background: '#fff2f0', borderRadius: 8 }}>
              <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>断货风险</div>
              <div style={{ fontSize: 20, fontWeight: 'bold', color: '#cf1322' }}>{stats.red}</div>
            </div>
          </Col>
          <Col span={8}>
            <div style={{ textAlign: 'center', padding: '8px 0', background: '#fffbe6', borderRadius: 8 }}>
              <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>库存预警</div>
              <div style={{ fontSize: 20, fontWeight: 'bold', color: '#faad14' }}>{stats.yellow}</div>
            </div>
          </Col>
          <Col span={8}>
            <div style={{ textAlign: 'center', padding: '8px 0', background: '#f6ffed', borderRadius: 8 }}>
              <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>库存正常</div>
              <div style={{ fontSize: 20, fontWeight: 'bold', color: '#52c41a' }}>{stats.green}</div>
            </div>
          </Col>
        </Row>
      </div>
    )
  }

  return (
    <div style={{ height: '100%', overflowY: 'auto', padding: '24px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <Title level={3} style={{ margin: 0 }}>{user?.username || '用户'}，{getGreeting()}！</Title>
        <Select
          placeholder="选择机器人"
          value={filterBot}
          onChange={(value) => setFilterBot(value)}
          allowClear
          showSearch
          filterOption={(input, option) =>
            (option?.label as string || '').toLowerCase().includes(input.toLowerCase())
          }
          style={{ width: 180 }}
          options={filterOptions}
        />
      </div>

        <Row gutter={[24, 24]}>
          {filteredBots.length > 0 ? (
            filteredBots.map(module => (
              <Col xs={24} sm={12} md={12} lg={8} key={module.id}>
              <Card
                loading={loading}
                onClick={() => navigate(module.path)}
                style={{
                  height: '100%',
                  cursor: 'pointer',
                  borderLeft: `4px solid ${module.color}`,
                  boxShadow: '0 2px 8px rgba(0, 0, 0, 0.1)',
                  transition: 'all 0.3s'
                }}
                styles={{ body: { padding: 24 } }}
              >
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                    <div style={{
                      width: 56,
                      height: 56,
                      borderRadius: 12,
                      background: `${module.color}15`,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      color: module.color
                    }}>
                      {module.icon}
                    </div>
                    <div>
                      <Title level={4} style={{ margin: 0, marginBottom: 4 }}>{module.title}</Title>
                      <Text type="secondary" style={{ fontSize: 13 }}>{module.description}</Text>
                    </div>
                  </div>
                  <ChevronRight size={20} color="#999" />
                </div>

                {module.stats && module.stats !== 'email' && renderReviewStats(module.stats)}
                {module.stats === 'email' && renderEmailStats()}
                {module.id === 'review' && module.stats && renderReviewStats(module.stats)}
                {module.id === 'inventory' && module.stats && renderInventoryStats(module.stats)}
              </Card>
            </Col>
            ))
          ) : (
            <div style={{
              width: '100%',
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              padding: '120px 0'
            }}>
              <Empty description="今日无待办" />
            </div>
          )}
        </Row>

    </div>
  )
}

export default Home
