import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { Input, Button, Typography, message, Spin, List, Avatar, Segmented, Popconfirm } from 'antd'
import { Send, MessageSquare, Plus, Package, Trash2, StopCircle } from 'lucide-react'
import { chatApi, chatStreamApi } from '../api'
import { useTheme } from '../contexts/ThemeContext'
import { useStreamingChat, ChatMessage } from '../hooks/useStreamingChat'
import MarkdownRenderer from '../components/common/MarkdownRenderer'

const { Title, Text } = Typography

interface ChatSession {
  id: number
  session_id: string
  title: string
  created_at: string
  message_count?: number
}

const CHAT_CONFIGS = {
  review: {
    title: '差评分析助手',
    subtitle: '智能分析差评数据，提供专业改进建议',
    color: '#1890ff',
    welcome: '您好！我是跨境电商差评分析助手。\n\n您可以问我以下问题：\n- 帮我看看这周的差评\n- 查看 ASIN B09XYZ 最近 7 天的差评\n- 分析最近的差评核心问题',
    placeholder: '输入差评相关问题...',
    icon: MessageSquare,
  },
  inventory: {
    title: '库存分析助手',
    subtitle: '智能分析库存数据，提供断货预警和补货建议',
    color: '#722ed1',
    welcome: '您好！我是库存AI分析助手。\n\n您可以问我以下问题：\n- 哪些商品有断货风险？\n- 需要补货的商品有哪些？\n- 帮我分析一下库存状况\n- 低库存商品有哪些？',
    placeholder: '输入库存相关问题，如：哪些商品有断货风险？',
    icon: Package,
  },
}

// 单条消息组件 - React.memo 避免滚动时无关消息重复渲染
const ChatMessageItem = React.memo(({ msg, userMessageBg, assistantColor }: {
  msg: ChatMessage;
  userMessageBg: string;
  assistantColor: string;
}) => (
  <div
    style={{
      display: 'flex',
      justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
      marginBottom: '16px',
      gap: '10px',
    }}
  >
    <div
      style={{
        width: '36px',
        height: '36px',
        borderRadius: '50%',
        backgroundColor: msg.role === 'user' ? userMessageBg : assistantColor,
        color: 'white',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontWeight: 'bold',
        flexShrink: 0,
        fontSize: '13px',
        boxShadow: '0 2px 4px rgba(0,0,0,0.1)'
      }}
    >
      {msg.role === 'user' ? 'U' : 'AI'}
    </div>
    <div
      style={{
        maxWidth: '75%',
        padding: '14px 18px',
        borderRadius: msg.role === 'user'
          ? '16px 16px 4px 16px'
          : '16px 16px 16px 4px',
        backgroundColor: msg.role === 'user' ? userMessageBg : 'white',
        color: msg.role === 'user' ? 'white' : '#333',
        boxShadow: '0 1px 2px rgba(0,0,0,0.08)',
        wordBreak: 'break-word',
        overflowWrap: 'break-word',
        border: msg.role === 'assistant' ? '1px solid #f0f0f0' : 'none'
      }}
    >
      {msg.role === 'user' ? (
        <span>{msg.content}</span>
      ) : (
        <MarkdownRenderer content={msg.content} />
      )}
      {msg.isStreaming && (
        <span style={{
          display: 'inline-block',
          width: '8px',
          height: '16px',
          backgroundColor: assistantColor,
          marginLeft: '4px',
          animation: 'blink 1s infinite'
        }} />
      )}
    </div>
  </div>
))
ChatMessageItem.displayName = 'ChatMessageItem'

const ChatBot: React.FC = () => {
  const { currentTheme } = useTheme()
  const [chatType, setChatType] = useState<'review' | 'inventory'>('review')
  const [input, setInput] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [loadingSessions, setLoadingSessions] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [isSearching, setIsSearching] = useState(false)
  const [loadingMessages, setLoadingMessages] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const loadingRef = useRef(false)
  const isUserScrollingRef = useRef(false)
  const scrollContainerRef = useRef<HTMLDivElement>(null)

  const config = CHAT_CONFIGS[chatType]

  // 使用流式聊天Hook
  const {
    messages,
    isStreaming,
    streamingContent,
    sendMessage: sendStreamingMessage,
    stopStreaming,
    clearMessages,
    setMessages
  } = useStreamingChat({
    onError: (error) => message.error(error),
    onComplete: (sid) => {
      setSessionId(sid)
      loadSessions()
    }
  })

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'auto' })
  }, [])

  // 监听用户手动滚动：如果用户向上滚动，暂停自动滚动
  useEffect(() => {
    const container = scrollContainerRef.current
    if (!container) return

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container
      // 距离底部超过 80px 视为用户在向上滚动
      isUserScrollingRef.current = (scrollHeight - scrollTop - clientHeight) > 80
    }

    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => container.removeEventListener('scroll', handleScroll)
  }, [])

  // 仅在用户未手动滚动时自动滚到底部
  useEffect(() => {
    if (!isUserScrollingRef.current) {
      scrollToBottom()
    }
  }, [messages, streamingContent, scrollToBottom])

  // 切换机器人类型时重置
  useEffect(() => {
    if (chatType) {
      clearMessages()
      setSessionId(null)
      loadSessions()
    }
  }, [chatType])

  const loadSessions = async () => {
    try {
      setLoadingSessions(true)
      const response = await chatApi.getSessions(chatType)
      setSessions(response.data || [])
    } catch (error) {
      console.error('加载会话失败:', error)
    } finally {
      setLoadingSessions(false)
    }
  }

  const loadSessionMessages = useCallback(async (sid: string) => {
    // 防止重复点击
    if (loadingRef.current) return
    loadingRef.current = true
    
    try {
      setLoadingMessages(true)
      const response = await chatApi.getSessionMessages(sid)

      const loadedMessages: ChatMessage[] = (response.data || []).map((msg: any) => ({
        id: msg.id?.toString() || Date.now().toString(),
        role: (msg.role === 'user' || msg.role === 'assistant') ? msg.role : 'assistant',
        content: msg.content || '',
        timestamp: msg.created_at ? new Date(msg.created_at) : new Date()
      }))

      setSessionId(sid)
      setMessages(loadedMessages)
    } catch (error) {
      console.error('加载会话消息失败:', error)
      message.error('加载历史消息失败')
    } finally {
      setLoadingMessages(false)
      loadingRef.current = false
    }
  }, [setMessages])

  const resetChat = useCallback(() => {
    setSessionId(null)
    clearMessages()
  }, [clearMessages])

  const handleSendMessage = useCallback(async () => {
    if (!input.trim() || isStreaming) return
    const messageText = input
    setInput('')
    await sendStreamingMessage(messageText, sessionId || undefined, chatType)
  }, [input, isStreaming, sessionId, chatType, sendStreamingMessage])

  const handleSearch = useCallback(async () => {
    if (!searchQuery.trim()) {
      loadSessions()
      return
    }
    try {
      setIsSearching(true)
      const response = await chatStreamApi.searchSessions(searchQuery, chatType)
      setSessions(response.data || [])
    } catch (error) {
      console.error('搜索失败:', error)
      message.error('搜索失败')
    } finally {
      setIsSearching(false)
    }
  }, [searchQuery, chatType])

  const handleDelete = useCallback(async (sid: string) => {
    try {
      await chatApi.deleteSession(sid)
      message.success('删除成功')
      // 如果删除的是当前会话，清空消息
      if (sid === sessionId) {
        clearMessages()
        setSessionId(null)
      }
      // 刷新会话列表
      loadSessions()
    } catch (error) {
      message.error('删除失败')
    }
  }, [sessionId, clearMessages, loadSessions])

  // 使用 useMemo 优化显示消息计算
  const displayMessages = useMemo(() => {
    if (isStreaming && streamingContent) {
      return [
        ...messages,
        {
          id: 'streaming',
          role: 'assistant' as const,
          content: streamingContent,
          timestamp: new Date(),
          isStreaming: true
        }
      ]
    }
    return messages
  }, [messages, isStreaming, streamingContent])

  const handleKeyPress = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSendMessage()
    }
  }, [handleSendMessage])

  // 缓存消息列表渲染 - 避免每次渲染都重新创建组件
  const messageList = useMemo(() => {
    return displayMessages.map(msg => (
      <ChatMessageItem
        key={msg.id}
        msg={msg}
        userMessageBg={currentTheme.userMessageBg}
        assistantColor={config.color}
      />
    ))
  }, [displayMessages, currentTheme.userMessageBg, config.color])

  return (
    <div style={{
      height: '100%',
      display: 'flex',
      gap: '20px',
      padding: '24px',
      boxSizing: 'border-box',
      overflow: 'hidden'
    }}>
      {/* 左侧会话列表 */}
      <div style={{ width: '280px', flexShrink: 0, height: '100%' }}>
        <div style={{
          height: '100%',
          borderRadius: '8px',
          boxShadow: '0 1px 2px rgba(0,0,0,0.06)',
          display: 'flex',
          flexDirection: 'column',
          backgroundColor: 'white',
          border: '1px solid #f0f0f0'
        }}>
          <div style={{
            padding: '16px',
            borderBottom: '1px solid #f0f0f0',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center'
          }}>
            <Title level={5} style={{ margin: 0, fontSize: '16px' }}>会话历史</Title>
            <Button
              type="text"
              icon={<Plus size={16} />}
              onClick={resetChat}
              style={{ color: config.color }}
            >
              新会话
            </Button>
          </div>

          {/* 搜索框 */}
          <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0' }}>
            <Input.Search
              placeholder="搜索会话..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onSearch={handleSearch}
              loading={isSearching}
              allowClear
              size="small"
            />
          </div>

          <div style={{ flex: 1, overflowY: 'auto' }}>
            {loadingSessions ? (
              <div style={{ textAlign: 'center', padding: '40px 24px' }}>
                <Spin size="small" />
              </div>
            ) : sessions.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '40px 24px', color: '#999' }}>
                <config.icon size={40} style={{ marginBottom: '12px', opacity: 0.5 }} />
                <div>暂无会话记录</div>
                <div style={{ fontSize: '12px', marginTop: '4px' }}>点击右上角创建新会话</div>
              </div>
            ) : (
              <List
                dataSource={sessions}
                renderItem={(session) => (
                  <List.Item
                    style={{
                      cursor: 'pointer',
                      backgroundColor: session.session_id === sessionId ? currentTheme.selectedBg : 'transparent',
                      padding: '12px 16px',
                      margin: '0',
                      borderBottom: '1px solid #f0f0f0',
                      transition: 'background-color 0.2s'
                    }}
                    onClick={() => loadSessionMessages(session.session_id)}
                    actions={[
                      <Popconfirm
                        key="delete"
                        title="删除会话"
                        description="确定要删除此会话吗？删除后不可恢复。"
                        onConfirm={(e) => {
                          e?.stopPropagation()
                          handleDelete(session.session_id)
                        }}
                        okText="确定"
                        cancelText="取消"
                        okButtonProps={{ danger: true }}
                      >
                        <Button
                          type="text"
                          size="small"
                          danger
                          icon={<Trash2 size={14} />}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </Popconfirm>
                    ]}
                  >
                    <List.Item.Meta
                      avatar={
                        <Avatar
                          style={{ backgroundColor: config.color }}
                          icon={<config.icon size={14} />}
                        />
                      }
                      title={<Text strong style={{ fontSize: '13px' }}>{session.title}</Text>}
                      description={
                        <Text type="secondary" ellipsis style={{ fontSize: '11px' }}>
                          {new Date(session.created_at).toLocaleString('zh-CN', {
                            month: 'short',
                            day: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit'
                          })}
                          {session.message_count !== undefined && ` · ${session.message_count}条消息`}
                        </Text>
                      }
                    />
                  </List.Item>
                )}
              />
            )}
          </div>
        </div>
      </div>

      {/* 右侧聊天区域 */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: '0', height: '100%' }}>
        <div style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          borderRadius: '8px',
          boxShadow: '0 1px 2px rgba(0,0,0,0.06)',
          overflow: 'hidden',
          backgroundColor: 'white',
          border: '1px solid #f0f0f0'
        }}>
          <div style={{
            padding: '16px 24px',
            borderBottom: '1px solid #f0f0f0',
            flexShrink: 0
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <Title level={4} style={{ margin: 0, color: config.color, fontSize: '18px' }}>
                {config.title}
              </Title>
              <Segmented
                value={chatType}
                onChange={(value) => setChatType(value as 'review' | 'inventory')}
                options={[
                  { label: '差评分析', value: 'review' },
                  { label: '库存分析', value: 'inventory' },
                ]}
                style={{ background: '#f5f5f5' }}
              />
            </div>
            <Text type="secondary" style={{ fontSize: '13px' }}>
              {config.subtitle}
            </Text>
          </div>

          {/* 消息列表区域 */}
          <div
            ref={scrollContainerRef}
            style={{
              flex: 1,
              overflowY: 'auto',
              padding: '20px',
              backgroundColor: '#fafafa',
              minHeight: 0,
              height: 0
            }}
          >
            {/* 加载历史消息时显示 */}
            {loadingMessages && (
              <div style={{ display: 'flex', justifyContent: 'center', padding: '40px' }}>
                <Spin size="large" tip="加载历史消息中..." />
              </div>
            )}

            {/* 欢迎消息 */}
            {!loadingMessages && displayMessages.length === 0 && (
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'flex-start',
                  marginBottom: '16px',
                  gap: '10px',
                  animation: 'fadeIn 0.3s ease'
                }}
              >
                <div
                  style={{
                    width: '36px',
                    height: '36px',
                    borderRadius: '50%',
                    backgroundColor: config.color,
                    color: 'white',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontWeight: 'bold',
                    flexShrink: 0,
                    fontSize: '13px',
                    boxShadow: '0 2px 4px rgba(0,0,0,0.1)'
                  }}
                >
                  AI
                </div>
                <div
                  style={{
                    maxWidth: '75%',
                    padding: '14px 18px',
                    borderRadius: '16px 16px 16px 4px',
                    backgroundColor: 'white',
                    color: '#333',
                    boxShadow: '0 1px 2px rgba(0,0,0,0.08)',
                    wordBreak: 'break-word',
                    overflowWrap: 'break-word',
                    border: '1px solid #f0f0f0'
                  }}
                >
                  <MarkdownRenderer content={config.welcome} />
                </div>
              </div>
            )}

            {/* 消息列表 */}
            {!loadingMessages && messageList}

            {/* 流式生成中提示 */}
            {isStreaming && !streamingContent && !loadingMessages && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div
                  style={{
                    width: '36px',
                    height: '36px',
                    borderRadius: '50%',
                    backgroundColor: config.color,
                    color: 'white',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontWeight: 'bold',
                    flexShrink: 0,
                    fontSize: '13px',
                    boxShadow: '0 2px 4px rgba(0,0,0,0.1)'
                  }}
                >
                  AI
                </div>
                <div
                  style={{
                    padding: '14px 24px',
                    borderRadius: '16px 16px 16px 4px',
                    backgroundColor: 'white',
                    boxShadow: '0 1px 2px rgba(0,0,0,0.08)',
                    display: 'flex',
                    alignItems: 'center',
                    border: '1px solid #f0f0f0'
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Spin size="small" />
                    <span style={{ color: '#666', fontSize: '14px' }}>正在思考中...</span>
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* 输入区域 */}
          <div
            style={{
              padding: '16px 20px',
              backgroundColor: 'white',
              borderTop: '1px solid #f0f0f0',
              display: 'flex',
              gap: '12px',
              alignItems: 'center',
              flexShrink: 0
            }}
          >
            <Input.TextArea
              placeholder={config.placeholder}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyPress}
              disabled={isStreaming || loadingMessages}
              autoSize={{ minRows: 1, maxRows: 4 }}
              style={{
                flex: 1,
                borderRadius: '8px',
                padding: '10px 16px',
                border: '1px solid #d9d9d9',
                boxShadow: 'none',
                resize: 'none'
              }}
            />
            {isStreaming ? (
              <Button
                type="primary"
                danger
                icon={<StopCircle size={16} />}
                onClick={stopStreaming}
                style={{
                  borderRadius: '8px',
                  padding: '0 24px',
                  height: '40px'
                }}
              >
                停止
              </Button>
            ) : (
              <Button
                type="primary"
                icon={<Send size={16} />}
                onClick={handleSendMessage}
                disabled={!input.trim() || loadingMessages}
                style={{
                  borderRadius: '8px',
                  padding: '0 24px',
                  height: '40px',
                  backgroundColor: config.color,
                  borderColor: config.color
                }}
              >
                发送
              </Button>
            )}
          </div>
        </div>
      </div>
      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes blink {
          0%, 50% { opacity: 1; }
          51%, 100% { opacity: 0; }
        }
      `}</style>
    </div>
  )
}

export default ChatBot
