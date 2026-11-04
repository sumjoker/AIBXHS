import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Card, Table, Button, Tag, Input, Radio, Space, Modal, message, Empty, Tooltip, Pagination, Checkbox, Spin } from 'antd'
import { ReloadOutlined, SearchOutlined, CheckOutlined, CloseOutlined } from '@ant-design/icons'
import { videoBindingApi, VideoBindingRequest, AmazonSkuEntry } from '../api'
import { useAuth } from '../contexts/AuthContext'

const PAGE_SIZE_OPTIONS = ['10', '20', '50', '100']

const statusMeta: Record<string, { label: string; color: string }> = {
  pending: { label: '待审核', color: 'orange' },
  approved: { label: '已通过', color: 'success' },
  rejected: { label: '已拒绝', color: 'error' },
}

const sourceMeta: Record<string, { label: string; color: string }> = {
  ai_video: { label: 'AI视频', color: 'blue' },
  upscale: { label: '高清处理', color: 'purple' },
  ai_image: { label: 'AI生图', color: 'green' },
}

/** 媒体绑定审核：AI视频/高清处理/AI生图「绑定至产品」申请的审核列表 */
const VideoBindingReview: React.FC = () => {
  const { hasPermission } = useAuth()
  const [statusFilter, setStatusFilter] = useState<string>('pending')
  const [searchText, setSearchText] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [items, setItems] = useState<VideoBindingRequest[]>([])
  const [loading, setLoading] = useState(false)
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20, total: 0 })
  const [statusCounts, setStatusCounts] = useState({ pending: 0, approved: 0, rejected: 0, all: 0 })
  const [actingId, setActingId] = useState<number | null>(null)
  const [previewItem, setPreviewItem] = useState<VideoBindingRequest | null>(null)
  const [rejectTarget, setRejectTarget] = useState<VideoBindingRequest | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  const [rejectSubmitting, setRejectSubmitting] = useState(false)
  // 首条视频直传飞书 SKU 弹窗（审核通过且产品此前无视频时触发）
  const [skuModalOpen, setSkuModalOpen] = useState(false)
  const [skuLoading, setSkuLoading] = useState(false)
  const [skuItems, setSkuItems] = useState<AmazonSkuEntry[]>([])
  const [skuSelected, setSkuSelected] = useState<string[]>([])
  const [skuSubmitting, setSkuSubmitting] = useState(false)
  const skuTargetRef = useRef<{ productId: number; videoUrl: string } | null>(null)
  const keywordTimerRef = useRef<number | null>(null)

  const loadRequests = useCallback(async (page = pagination.current, pageSize = pagination.pageSize) => {
    setLoading(true)
    try {
      const res = await videoBindingApi.getList({
        status: statusFilter === 'all' ? undefined : statusFilter,
        search: searchText || undefined,
        page,
        page_size: pageSize,
      })
      if (res.data.success) {
        setItems(res.data.data.items || [])
        setPagination(prev => ({ ...prev, current: page, pageSize, total: res.data.data.total || 0 }))
        if (res.data.data.status_counts) setStatusCounts(res.data.data.status_counts)
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '获取绑定申请列表失败')
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, searchText])

  useEffect(() => {
    if (hasPermission('ai_video:approve')) {
      loadRequests(1, pagination.pageSize)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, searchText])

  // 搜索输入：300ms 防抖后回到第 1 页重新加载；清空立即搜、回车立即搜
  const handleSearchChange = (value: string) => {
    setSearchInput(value)
    if (keywordTimerRef.current) window.clearTimeout(keywordTimerRef.current)
    if (!value) {
      // 点击清除按钮时立即重置搜索
      setSearchText('')
      return
    }
    keywordTimerRef.current = window.setTimeout(() => {
      setSearchText(value.trim())
    }, 300)
  }

  const handleSearchEnter = () => {
    if (keywordTimerRef.current) window.clearTimeout(keywordTimerRef.current)
    setSearchText(searchInput.trim())
  }

  // 首绑 SKU 弹窗：拉取 AMAZON 平台 SKU×店铺列表，默认只勾选有 ASIN 的项
  const openSkuModal = async (productId: number, videoUrl: string) => {
    skuTargetRef.current = { productId, videoUrl }
    setSkuModalOpen(true)
    setSkuLoading(true)
    try {
      const res = await videoBindingApi.getAmazonSkus(productId)
      const list = res.data.data?.items || []
      setSkuItems(list)
      setSkuSelected(list.filter(e => e.has_asin).map(e => `${e.pp_id}_${e.store_id}`))
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '获取产品 SKU 列表失败')
      setSkuModalOpen(false)
    } finally {
      setSkuLoading(false)
    }
  }

  const handleSkuUploadSubmit = async () => {
    const target = skuTargetRef.current
    if (!target) return
    if (skuSelected.length === 0) {
      message.warning('请至少勾选一个 SKU')
      return
    }
    const selected = skuItems.filter(e => skuSelected.includes(`${e.pp_id}_${e.store_id}`))
    const noAsin = selected.filter(e => !e.has_asin)
    if (noAsin.length > 0) {
      message.error(`以下 SKU 未绑定 ASIN，无法上传：${noAsin.map(e => e.sku || `#${e.pp_id}`).join('、')}，请取消勾选后重试`)
      return
    }
    setSkuSubmitting(true)
    try {
      const res = await videoBindingApi.uploadSkuToFeishu({
        product_id: target.productId,
        video_url: target.videoUrl,
        items: selected.map(e => ({ pp_id: e.pp_id, store_id: e.store_id })),
      })
      if (res.data.success) {
        message.success(res.data.message || 'SKU 视频已上传飞书')
        const failed = res.data.data?.failed || 0
        const errors = res.data.data?.errors || []
        if (failed > 0) {
          Modal.warning({
            title: `有 ${failed} 条记录上传失败`,
            content: (
              <div>
                {errors.slice(0, 5).map((t, i) => (
                  <div key={i}>{t}</div>
                ))}
              </div>
            ),
          })
        }
        setSkuModalOpen(false)
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '上传失败')
    } finally {
      setSkuSubmitting(false)
    }
  }

  const handleApprove = async (record: VideoBindingRequest) => {
    setActingId(record.id)
    try {
      const res = await videoBindingApi.approve(record.id)
      if (res.data.success) {
        message.success(res.data.message || '已通过并绑定至产品详情')
        loadRequests()
        // 首条视频（此前产品无任何视频）：弹出 SKU 直传确认弹窗
        const videos = res.data.data?.videos
        if (res.data.data?.is_first_video && videos && videos.length > 0) {
          openSkuModal(record.product_id, videos[videos.length - 1])
        }
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '操作失败')
    } finally {
      setActingId(null)
    }
  }

  const handleRejectSubmit = async () => {
    if (!rejectTarget) return
    setRejectSubmitting(true)
    try {
      const res = await videoBindingApi.reject(rejectTarget.id, rejectReason.trim() || undefined)
      if (res.data.success) {
        message.success(res.data.message || '已拒绝该绑定申请')
        setRejectTarget(null)
        setRejectReason('')
        loadRequests()
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '操作失败')
    } finally {
      setRejectSubmitting(false)
    }
  }

  if (!hasPermission('ai_video:approve')) {
    return (
      <div style={{ padding: 24 }}>
        <Card>
          <Empty description="暂无访问权限（需要「审批视频绑定」权限）" />
        </Card>
      </div>
    )
  }

  const columns = [
    {
      title: '媒体',
      key: 'media',
      width: 200,
      render: (_: any, record: VideoBindingRequest) => {
        const src = record.media_url || record.video_url
        const style = {
          width: 176,
          height: 99,
          background: '#000',
          borderRadius: 6,
          display: 'block',
          cursor: 'pointer',
          objectFit: 'cover' as const,
        }
        if (record.media_type === 'image') {
          return (
            <img
              src={src}
              alt={record.task_title || '生成图片'}
              onClick={() => setPreviewItem(record)}
              style={{ ...style, objectFit: 'contain', background: '#f5f5f5' }}
            />
          )
        }
        return (
          <video
            src={src}
            muted
            loop
            playsInline
            preload="metadata"
            onMouseEnter={(e) => {
              e.currentTarget.play().catch(() => {})
            }}
            onMouseLeave={(e) => {
              e.currentTarget.pause()
            }}
            onClick={() => setPreviewItem(record)}
            style={style}
          />
        )
      },
    },
    {
      title: '目标产品',
      key: 'product',
      width: 240,
      render: (_: any, record: VideoBindingRequest) => (
        <div>
          <div style={{ fontWeight: 500 }}>{record.product_name || `产品#${record.product_id}`}</div>
          <div style={{ fontSize: 12, color: '#999' }}>{record.product_code || '-'}</div>
        </div>
      ),
    },
    {
      title: '来源',
      key: 'source',
      width: 160,
      render: (_: any, record: VideoBindingRequest) => {
        const meta = sourceMeta[record.source] || sourceMeta.ai_video
        return (
          <Tooltip title={record.task_title || ''}>
            <Space size={4}>
              <Tag color={meta.color}>{meta.label}</Tag>
              {record.task_title && (
                <span style={{ fontSize: 12, color: '#999', maxWidth: 90, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block' }}>
                  {record.task_title}
                </span>
              )}
            </Space>
          </Tooltip>
        )
      },
    },
    { title: '提交人', dataIndex: 'requester_name', key: 'requester_name', width: 100, render: (v: string) => v || '-' },
    { title: '提交时间', dataIndex: 'created_at', key: 'created_at', width: 160 },
    {
      title: '状态',
      key: 'status',
      width: 100,
      render: (_: any, record: VideoBindingRequest) => {
        const meta = statusMeta[record.status] || statusMeta.pending
        return <Tag color={meta.color}>{meta.label}</Tag>
      },
    },
    {
      title: '审核人',
      key: 'reviewer',
      width: 100,
      render: (_: any, record: VideoBindingRequest) => {
        if (record.status === 'pending') return <span style={{ color: '#999' }}>-</span>
        return record.reviewer_name || '-'
      },
    },
    {
      title: '审核时间',
      key: 'reviewed_at',
      width: 160,
      render: (_: any, record: VideoBindingRequest) => {
        if (record.status === 'pending') return <span style={{ color: '#999' }}>-</span>
        return record.reviewed_at || '-'
      },
    },
    {
      title: '拒绝原因',
      key: 'reject_reason',
      width: 180,
      render: (_: any, record: VideoBindingRequest) => {
        if (record.status !== 'rejected' || !record.reject_reason) return <span style={{ color: '#999' }}>-</span>
        return (
          <Tooltip title={record.reject_reason}>
            <div style={{ color: '#cf1322', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {record.reject_reason}
            </div>
          </Tooltip>
        )
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 160,
      render: (_: any, record: VideoBindingRequest) => {
        if (record.status !== 'pending') return <span style={{ color: '#999' }}>-</span>
        return (
          <Space size={8}>
            <Button
              type="primary"
              size="small"
              icon={<CheckOutlined />}
              loading={actingId === record.id}
              onClick={() => handleApprove(record)}
            >
              通过
            </Button>
            <Button
              danger
              size="small"
              icon={<CloseOutlined />}
              onClick={() => {
                setRejectTarget(record)
                setRejectReason('')
              }}
            >
              拒绝
            </Button>
          </Space>
        )
      },
    },
  ]

  return (
    <div style={{ padding: 24, height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Card
        title="媒体绑定审核"
        style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
        styles={{ body: { padding: 16, flex: 1, display: 'flex', flexDirection: 'column' } }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
          <Radio.Group
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            optionType="button"
            buttonStyle="solid"
            options={[
              { label: `全部 (${statusCounts.all})`, value: 'all' },
              { label: `待审核 (${statusCounts.pending})`, value: 'pending' },
              { label: `已通过 (${statusCounts.approved})`, value: 'approved' },
              { label: `已拒绝 (${statusCounts.rejected})`, value: 'rejected' },
            ]}
          />
          <Space>
            <Input
              allowClear
              prefix={<SearchOutlined style={{ color: '#999' }} />}
              placeholder="搜索产品编码 / 品名 / 提交人"
              style={{ width: 240 }}
              value={searchInput}
              onChange={(e) => handleSearchChange(e.target.value)}
              onPressEnter={handleSearchEnter}
            />
            <Button icon={<ReloadOutlined />} onClick={() => loadRequests()}>刷新</Button>
          </Space>
        </div>

        <Table
          dataSource={items}
          columns={columns as any}
          rowKey="id"
          loading={loading}
          pagination={false}
          scroll={{ x: 1560 }}
          locale={{ emptyText: <Empty description={statusFilter === 'pending' ? '暂无待审核的绑定申请' : '暂无数据'} /> }}
        />
      </Card>
      <div style={{ marginTop: 16, display: 'flex', justifyContent: 'flex-end' }}>
        <Pagination
          current={pagination.current}
          pageSize={pagination.pageSize}
          total={pagination.total}
          showSizeChanger
          showQuickJumper
          pageSizeOptions={PAGE_SIZE_OPTIONS}
          showTotal={(total) => `共 ${total} 条`}
          onChange={(page, pageSize) => loadRequests(page, pageSize)}
        />
      </div>

      {/* 视频全屏预览：居中弹窗播放 */}
      <Modal
        open={!!previewItem}
        onCancel={() => setPreviewItem(null)}
        footer={null}
        width="90vw"
        centered
        destroyOnClose
        styles={{ body: { padding: 0, background: '#000' } }}
        title={previewItem?.product_name || (previewItem?.media_type === 'image' ? '图片预览' : '视频预览')}
      >
        {previewItem && previewItem.media_type === 'image' && (
          <img
            src={previewItem.media_url || previewItem.video_url}
            alt={previewItem.task_title || '生成图片'}
            style={{ width: '100%', height: '78vh', objectFit: 'contain', display: 'block', background: '#111' }}
          />
        )}
        {previewItem && previewItem.media_type !== 'image' && (
          <video
            src={previewItem.media_url || previewItem.video_url}
            controls
            autoPlay
            playsInline
            style={{ width: '100%', height: '78vh', objectFit: 'contain', display: 'block', background: '#000' }}
          />
        )}
      </Modal>

      <Modal
        open={!!rejectTarget}
        title={`拒绝绑定申请 - ${rejectTarget?.product_name || ''}`}
        okText="确认拒绝"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        confirmLoading={rejectSubmitting}
        onOk={handleRejectSubmit}
        onCancel={() => setRejectTarget(null)}
      >
        <div style={{ marginBottom: 8, color: '#666' }}>拒绝原因（选填）：</div>
        <Input.TextArea
          rows={3}
          maxLength={500}
          placeholder="请输入拒绝原因"
          value={rejectReason}
          onChange={(e) => setRejectReason(e.target.value)}
        />
      </Modal>

      {/* 首条视频直传飞书 SKU：默认只勾选有 ASIN 的项，勾选未绑定 ASIN 的项确定时报错 */}
      <Modal
        open={skuModalOpen}
        title="首条视频上传 SKU"
        okText="确定上传"
        cancelText="取消"
        confirmLoading={skuSubmitting}
        onOk={handleSkuUploadSubmit}
        onCancel={() => setSkuModalOpen(false)}
        width={560}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <span>该视频为该产品的首条视频，是否直接上传至以下SKU：</span>
          {!skuLoading && skuItems.length > 0 && (
            <Checkbox
              checked={skuSelected.length > 0 && skuSelected.length === skuItems.length}
              indeterminate={skuSelected.length > 0 && skuSelected.length < skuItems.length}
              onChange={(e) => setSkuSelected(e.target.checked ? skuItems.map(e => `${e.pp_id}_${e.store_id}`) : [])}
            >
              全选
            </Checkbox>
          )}
        </div>
        {skuLoading ? (
          <div style={{ textAlign: 'center', padding: '24px 0' }}>
            <Spin />
          </div>
        ) : skuItems.length === 0 ? (
          <Empty description="该产品暂无 AMAZON 平台 SKU" />
        ) : (
          <>
            <div style={{ maxHeight: 360, overflowY: 'auto', border: '1px solid #f0f0f0', borderRadius: 6, padding: '8px 12px' }}>
              <Checkbox.Group
                value={skuSelected}
                onChange={(vals) => setSkuSelected(vals as string[])}
                style={{ display: 'flex', flexDirection: 'column', gap: 8, width: '100%' }}
              >
                {skuItems.map(e => {
                  const key = `${e.pp_id}_${e.store_id}`
                  return (
                    <Checkbox key={key} value={key}>
                      <span>
                        {e.sku || `#${e.pp_id}`}
                        {!e.has_asin && <span style={{ color: '#999' }}>（未绑定ASIN）</span>}
                        {(e.site || e.store_name) && (
                          <span style={{ color: '#999', marginLeft: 8 }}>{e.site || e.store_name}</span>
                        )}
                      </span>
                    </Checkbox>
                  )
                })}
              </Checkbox.Group>
            </div>
            <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
              共 {skuItems.length} 个 SKU，已选 {skuSelected.length} 个；确定后视频将写入飞书多维表
            </div>
          </>
        )}
      </Modal>
    </div>
  )
}

export default VideoBindingReview
