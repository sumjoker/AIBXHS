import React, { useEffect, useRef, useState } from 'react'
import { Modal, Select, Spin, message } from 'antd'
import { productsApi, videoBindingApi } from '../api'

interface ProductOption {
  id: number
  product_code: string
  name: string
}

const PAGE_SIZE = 50

/** 绑定媒体（视频/图片）至产品详情，选择产品后提交绑定申请，审核通过才真正写入产品 */
const BindProductModal: React.FC<{
  open: boolean
  /** 兼容旧调用：视频地址 */
  videoUrl?: string
  /** 待绑定的媒体地址（图片或视频），优先于 videoUrl */
  mediaUrl?: string
  /** video=写 products.videos（上限 6）/ image=写 products.images（上限 9） */
  mediaType?: 'video' | 'image'
  source?: 'ai_video' | 'upscale' | 'ai_image'
  taskTitle?: string
  onClose: () => void
}> = ({ open, videoUrl = '', mediaUrl, mediaType = 'video', source = 'ai_video', taskTitle, onClose }) => {
  const targetUrl = (mediaUrl || videoUrl || '').trim()
  const isImage = mediaType === 'image'
  const [products, setProducts] = useState<ProductOption[]>([])
  const [productId, setProductId] = useState<number | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  // 懒加载游标与请求序号：序号用于丢弃过期请求（搜索会打断进行中的翻页加载）
  const keywordRef = useRef('')
  const pageRef = useRef(1)
  const seqRef = useRef(0)
  const searchTimer = useRef<number | null>(null)

  const fetchProducts = async (kw: string, page = 1) => {
    const seq = ++seqRef.current
    setLoading(true)
    try {
      const res = await productsApi.getList({
        search: kw || undefined,
        page,
        page_size: PAGE_SIZE,
        product_type: 'finished',
      })
      if (seq !== seqRef.current) return
      const items: any[] = res.data?.data || []
      const totalCount: number = res.data?.total || 0
      keywordRef.current = kw
      pageRef.current = page
      setTotal(totalCount)
      const mapped = items.map((p) => ({ id: p.id, product_code: p.product_code || '', name: p.name || '' }))
      setProducts((prev) => {
        if (page === 1) return mapped
        // 追加下一页，按 id 去重，避免重复请求时出现重复选项
        const seen = new Set(prev.map((p) => p.id))
        return [...prev, ...mapped.filter((p) => !seen.has(p.id))]
      })
    } catch {
      if (seq === seqRef.current) message.error('获取产品列表失败')
    } finally {
      if (seq === seqRef.current) setLoading(false)
    }
  }

  useEffect(() => {
    if (open) {
      setProductId(null)
      setProducts([])
      setTotal(0)
      fetchProducts('')
    }
  }, [open])

  const handleSearch = (kw: string) => {
    if (searchTimer.current) window.clearTimeout(searchTimer.current)
    searchTimer.current = window.setTimeout(() => fetchProducts(kw), 300)
  }

  // 下拉滚动到距底部 40px 内时加载下一页
  const handlePopupScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget
    if (el.scrollHeight - el.scrollTop - el.clientHeight > 40) return
    if (loading || products.length === 0 || products.length >= total) return
    fetchProducts(keywordRef.current, pageRef.current + 1)
  }

  const handleOk = async () => {
    if (!productId || !targetUrl) {
      message.warning('请先选择产品')
      return
    }
    setSubmitting(true)
    try {
      await videoBindingApi.submit({
        product_id: productId,
        video_url: targetUrl,
        media_type: mediaType,
        source,
        task_title: taskTitle,
      })
      message.success('绑定申请已提交，审核通过后生效')
      onClose()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '提交绑定申请失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      open={open}
      title={isImage ? '绑定图片至产品' : '绑定视频至产品'}
      okText="确定"
      cancelText="取消"
      centered
      confirmLoading={submitting}
      okButtonProps={{ disabled: !productId }}
      onOk={handleOk}
      onCancel={onClose}
    >
      <Select
        showSearch
        allowClear
        filterOption={false}
        placeholder="搜索产品编码 / 品名"
        style={{ width: '100%' }}
        value={productId ?? undefined}
        onSearch={handleSearch}
        onPopupScroll={handlePopupScroll}
        onChange={(v) => setProductId(v ?? null)}
        loading={loading && products.length > 0}
        notFoundContent={loading ? <Spin size="small" /> : '无匹配产品'}
        options={products.map((p) => ({
          value: p.id,
          label: p.product_code ? `[${p.product_code}] ${p.name}` : p.name,
        }))}
      />
    </Modal>
  )
}

export default BindProductModal
