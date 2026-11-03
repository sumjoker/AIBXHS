import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Button, Empty, Input, Modal, Select, Space, Spin, Tabs, Tag, Tooltip, Typography, Upload, message, theme,
} from 'antd'
import {
  BulbOutlined, CheckCircleFilled, DeleteOutlined, LeftOutlined, PlusOutlined,
  QuestionCircleOutlined, RightOutlined,
  StarFilled, StarOutlined, UploadOutlined, ZoomInOutlined,
} from '@ant-design/icons'
import { aiImageApi, AIImageLayout } from '../api'

const { Text } = Typography

/** 版式缩略图：官方模板/上传素材显示真实图（懒加载）；内置结构版式用 zones 画分区示意
 *
 * aspect 给定时用「按宽算高」（宽高比固定），否则用固定高度。
 * 按画幅筛选时用 aspect 让框贴合该画幅 —— 否则竖版图挤在 210×116 的横框里只剩一条窄条。
 */
const LayoutThumb: React.FC<{ layout: AIImageLayout; height?: number; aspect?: number }> = ({
  layout, height = 96, aspect,
}) => {
  const { token } = theme.useToken()
  const zones = layout.zones || []
  const cover = layout.cover_url || layout.image_url
  const boxStyle: React.CSSProperties = aspect
    ? { aspectRatio: String(aspect), width: '100%' }
    : { height: height as number }
  if (cover) {
    return (
      <div
        style={{
          ...boxStyle, borderRadius: 4, overflow: 'hidden', background: token.colorFillQuaternary,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}
      >
        {/* contain 不裁切：版式看的是分区结构，裁掉一半就判断不了 */}
        <img
          src={cover}
          alt={layout.name}
          loading="lazy"
          decoding="async"
          style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
        />
      </div>
    )
  }
  const w = layout.ratio_tag === 'vertical' ? 64 : layout.ratio_tag === 'square' ? 78 : 110
  return (
    <div style={{ ...boxStyle, display: 'flex', alignItems: 'center', justifyContent: 'center', background: token.colorFillQuaternary, borderRadius: 4, overflow: 'hidden' }}>
      <svg width={aspect ? '94%' : '100%'} height={aspect ? '94%' : Math.min(height - 8, w * (layout.ratio_tag === 'vertical' ? 1.33 : layout.ratio_tag === 'square' ? 1 : 0.5625))}
        viewBox="0 0 100 100" preserveAspectRatio="none" style={{ borderRadius: 3, display: 'block' }}>
        <rect x="0" y="0" width="100" height="100" fill={token.colorBgContainer} />
        {zones.map((z, i) => {
          const rx = z.x * 100, ry = z.y * 100, rw = z.w * 100, rh = z.h * 100
          if (z.t === 'gap') {
            return <rect key={i} x={rx + 0.6} y={ry + 0.6} width={Math.max(0, rw - 1.2)} height={Math.max(0, rh - 1.2)}
              fill="none" stroke={token.colorBorder} strokeDasharray="3 3" strokeWidth="0.8" />
          }
          // 图区用主题色淡块（暗示照片），文案区用浅灰块 + 文字线，色带用实色条 —— 三者一眼可分
          const isImg = z.t === 'img'
          const isBand = z.t === 'band'
          return (
            <g key={i}>
              <rect x={rx + 0.45} y={ry + 0.45} width={Math.max(0, rw - 0.9)} height={Math.max(0, rh - 0.9)}
                fill={isImg ? token.colorPrimary : isBand ? token.colorPrimary : token.colorFillSecondary}
                opacity={isImg ? 0.22 : isBand ? 0.82 : 1} rx="1" />
              {!isImg && Array.from({ length: Math.max(1, Math.min(3, Math.floor(rh / 12))) }).map((_, k) => (
                <rect key={k} x={rx + rw * 0.1} y={ry + 9 + k * 9} width={rw * (k === 0 ? 0.6 : 0.42)} height="2.2"
                  fill={isBand ? '#fff' : token.colorTextQuaternary} opacity={isBand ? 0.85 : 0.9} rx="1" />
              ))}
            </g>
          )
        })}
      </svg>
    </div>
  )
}

interface Props {
  open: boolean
  value: string[]
  limit: number
  onClose: () => void
  onConfirm: (keys: string[], items: AIImageLayout[]) => void
}

const AIImageLayoutPicker: React.FC<Props> = ({ open, value, limit, onClose, onConfirm }) => {
  const { token } = theme.useToken()
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState('preset')
  const [ratioFilter, setRatioFilter] = useState('all')
  const [presets, setPresets] = useState<AIImageLayout[]>([])
  const [mine, setMine] = useState<AIImageLayout[]>([])
  const [favorites, setFavorites] = useState<AIImageLayout[]>([])
  const [apiLimit, setApiLimit] = useState(limit)
  const [counts, setCounts] = useState<{ official?: number; structural?: number; mine?: number }>({})
  const [selected, setSelected] = useState<string[]>(value)
  const [uploading, setUploading] = useState(false)
  const [hoverKey, setHoverKey] = useState('')
  const [preview, setPreview] = useState<AIImageLayout | null>(null)
  // 版式结构描述（分区文字约束）的编辑草稿
  const [draft, setDraft] = useState('')
  const [suggesting, setSuggesting] = useState(false)
  const [saving, setSaving] = useState(false)

  const openPreview = (item: AIImageLayout) => {
    setPreview(item)
    setDraft(item.structure || '')
  }

  /** 把保存后的描述同步回三份列表，卡片/再次打开都能看到 */
  const patchStructure = (key: string, structure: string) => {
    const patch = (list: AIImageLayout[]) =>
      list.map((x) => (x.key === key ? { ...x, structure } : x))
    setPresets(patch)
    setMine(patch)
    setFavorites(patch)
    setPreview((p) => (p && p.key === key ? { ...p, structure } : p))
  }

  const doSuggest = async () => {
    if (!preview) return
    const id = Number(preview.id)
    setSuggesting(true)
    try {
      const { data } = await aiImageApi.suggestLayoutStructure(id)
      setDraft(data.structure || '')
      message.success('已生成草稿，请核对后再保存')
    } catch (e: any) {
      message.error(e?.response?.data?.detail || 'AI 生成失败')
    } finally {
      setSuggesting(false)
    }
  }

  const doSave = async () => {
    if (!preview) return
    const id = Number(preview.id)
    setSaving(true)
    try {
      const { data } = await aiImageApi.saveLayoutStructure(id, draft)
      patchStructure(preview.key, data.structure || '')
      message.success(data.structure ? '结构描述已保存' : '已清空，将只靠参考图生成')
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await aiImageApi.getLayouts()
      setPresets(data.presets || [])
      setMine(data.mine || [])
      setFavorites(data.favorites || [])
      setApiLimit(data.limit || limit)
      setCounts((data as any).counts || {})
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '读取版式库失败')
    } finally {
      setLoading(false)
    }
  }, [limit])

  useEffect(() => {
    if (open) {
      setSelected(value)
      setTab('preset')
      setRatioFilter('all')
      load()
    }
  }, [open])   // eslint-disable-line react-hooks/exhaustive-deps

  const allByKey = useMemo(() => {
    const map: Record<string, AIImageLayout> = {}
    ;[...presets, ...mine].forEach((p) => { map[p.key] = p })
    return map
  }, [presets, mine])

  const toggle = (item: AIImageLayout) => {
    if (selected.includes(item.key)) {
      setSelected(selected.filter((k) => k !== item.key))
      return
    }
    if (selected.length >= apiLimit) {
      message.warning(`最多选择 ${apiLimit} 个排版`)
      return
    }
    setSelected([...selected, item.key])
  }

  const toggleFavorite = async (item: AIImageLayout, e?: React.MouseEvent) => {
    e?.stopPropagation()
    const next = !(item.favorited ?? item.favorite)
    try {
      await aiImageApi.favoriteLayout(item.key, next)
      await load()
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '操作失败')
    }
  }

  const removeMine = async (item: AIImageLayout, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await aiImageApi.deleteLayout(Number(item.id))
      setSelected(selected.filter((k) => k !== item.key))
      await load()
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '删除失败')
    }
  }

  const doUpload = async (file: File) => {
    setUploading(true)
    try {
      const { data: created } = await aiImageApi.uploadLayout(file, file.name.replace(/\.[^.]+$/, ''))
      await load()
      setTab('mine')
      if (selected.length < apiLimit) setSelected([...selected, created.key])
      message.success('版式已上传')
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '上传失败')
    } finally {
      setUploading(false)
    }
    return false
  }

  const filter = (list: AIImageLayout[]) =>
    ratioFilter === 'all' ? list : list.filter((x) => x.ratio_tag === ratioFilter)

  const activeList = tab === 'preset' ? filter(presets) : tab === 'mine' ? filter(mine) : filter(favorites)

  // 卡片框形状（官方模板实测宽高比：横 2.27~2.46 / 竖 0.75~0.81 / 方 1.00）
  // 「全部」也用竖版框：统一竖向单元格，竖版图整格填满，横版图在框内居中适应（不裁切结构）
  const boxAspect = ratioFilter === 'square' ? 1
    : ratioFilter === 'horizontal' ? 2.36
      : 0.75
  // 横版单图最矮（2.36 比例下 4 列只有 86px 高），筛选横版时降为 3 列把框放大
  const COLS = ratioFilter === 'horizontal' ? 3 : 4

  const card = (item: AIImageLayout) => {
    const on = selected.includes(item.key)
    const fav = item.favorited ?? item.favorite
    const hovered = hoverKey === item.key
    return (
      <div
        key={item.key}
        onClick={() => toggle(item)}
        onMouseEnter={() => setHoverKey(item.key)}
        onMouseLeave={() => setHoverKey((k) => (k === item.key ? '' : k))}
        style={{
          position: 'relative', cursor: 'pointer', padding: 4, borderRadius: 8,
          border: `${on ? 2 : 1}px solid ${on ? token.colorPrimary : token.colorBorderSecondary}`,
          background: token.colorBgContainer, transition: 'border-color .15s',
        }}
      >
        <LayoutThumb layout={item} height={116} aspect={boxAspect} />
        <Tooltip title={item.desc || item.name}>
          <div style={{ position: 'absolute', left: 8, top: 8, display: 'flex', gap: 4, opacity: hovered ? 1 : 0.75 }}>
            <Tag style={{ margin: 0, fontSize: 10, lineHeight: '14px', padding: '0 4px' }}>
              {item.ratio_tag === 'vertical' ? '竖' : item.ratio_tag === 'square' ? '方' : '横'}
            </Tag>
            {item.source === 'upload' && (
              <Tag color="blue" style={{ margin: 0, fontSize: 10, lineHeight: '14px', padding: '0 4px' }}>我的</Tag>
            )}
            {item.source === 'preset' && (
              <Tag color="purple" style={{ margin: 0, fontSize: 10, lineHeight: '14px', padding: '0 4px' }}>结构</Tag>
            )}
          </div>
        </Tooltip>
        {/* 右上角操作：放大预览 / 收藏 / 删除（悬停才全亮，避免压住画面） */}
        <div
          style={{
            position: 'absolute', right: 6, top: 6, display: 'flex', gap: 2,
            opacity: hovered ? 1 : 0.55, transition: 'opacity .15s',
          }}
        >
          <Tooltip title="放大查看">
            <span
              onClick={(e) => { e.stopPropagation(); openPreview(item) }}
              style={{ padding: 3, lineHeight: 1, background: token.colorBgContainer, borderRadius: 4, cursor: 'zoom-in' }}
            >
              <ZoomInOutlined style={{ color: token.colorText, fontSize: 13 }} />
            </span>
          </Tooltip>
          <span onClick={(e) => toggleFavorite(item, e)} style={{ padding: 3, lineHeight: 1, background: token.colorBgContainer, borderRadius: 4 }}>
            {fav ? <StarFilled style={{ color: token.colorWarning, fontSize: 13 }} />
              : <StarOutlined style={{ color: token.colorTextTertiary, fontSize: 13 }} />}
          </span>
          {item.source === 'upload' && (
            <span onClick={(e) => removeMine(item, e)} style={{ padding: 3, lineHeight: 1, background: token.colorBgContainer, borderRadius: 4 }}>
              <DeleteOutlined style={{ color: token.colorTextTertiary, fontSize: 13 }} />
            </span>
          )}
        </div>
        {item.structure ? (
          <Tooltip title="已写结构描述">
            <span
              style={{
                position: 'absolute', left: 8, bottom: 8, width: 7, height: 7,
                borderRadius: '50%', background: token.colorSuccess,
              }}
            />
          </Tooltip>
        ) : null}
        {on && (
          <CheckCircleFilled style={{ position: 'absolute', right: 6, bottom: 6, color: token.colorPrimary, fontSize: 18, background: token.colorBgContainer, borderRadius: '50%' }} />
        )}
      </div>
    )
  }

  const uploadBox = (
    <Upload
      accept="image/*" showUploadList={false} beforeUpload={doUpload} disabled={uploading}
      style={{ display: 'block', width: '100%' }}
    >
      <div
        style={{
          cursor: 'pointer', borderRadius: 8, padding: 6,
          border: `1px dashed ${token.colorPrimaryBorder}`,
          background: token.colorFillQuaternary,
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          ...(boxAspect ? { aspectRatio: String(boxAspect), width: '100%' } : { minHeight: 126 }),
        }}
      >
        <Spin spinning={uploading} size="small">
          <PlusOutlined style={{ fontSize: 18, color: token.colorPrimary }} />
        </Spin>
        <div style={{ marginTop: 6, fontSize: 12, color: token.colorTextSecondary, textAlign: 'center' }}>
          上传我的排版
        </div>
      </div>
    </Upload>
  )

  return (
    <Modal
      open={open}
      onCancel={onClose}
      width={1180}
      title="设置版式参考图"
      destroyOnClose
      styles={{ body: { paddingTop: 8 } }}
      footer={
        <Space>
          <Text type="secondary" style={{ fontSize: 12 }}>每种排版生成一张</Text>
          <Button onClick={onClose}>取消</Button>
          <Button
            type="primary"
            disabled={selected.length === 0}
            onClick={() => {
              onConfirm(selected, selected.map((k) => allByKey[k]).filter(Boolean))
              onClose()
            }}
          >
            确认
          </Button>
        </Space>
      }
    >
      <div style={{ display: 'flex', gap: 12, minHeight: 420 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Tabs
            activeKey={tab}
            onChange={setTab}
            items={[
              { key: 'preset', label: `官方推荐${presets.length ? `(${presets.length})` : ''}` },
              { key: 'mine', label: `我的${mine.length ? `(${mine.length})` : ''}` },
              { key: 'favorite', label: `收藏${favorites.length ? `(${favorites.length})` : ''}` },
            ]}
            tabBarExtraContent={
              <Select
                size="small"
                value={ratioFilter}
                onChange={setRatioFilter}
                style={{ width: 96 }}
                options={[
                  { value: 'all', label: '全部画幅' },
                  { value: 'horizontal', label: '横版' },
                  { value: 'vertical', label: '竖版' },
                  { value: 'square', label: '方版' },
                ]}
              />
            }
          />
          <div
            style={{
              background: token.colorWarningBg, border: `1px solid ${token.colorWarningBorder}`,
              borderRadius: 6, padding: '4px 8px', marginBottom: 8, fontSize: 12,
              color: token.colorWarningText,
            }}
          >
            Tips：最多可选 {apiLimit} 个排版
            {counts.official ? `（官方模板 ${counts.official} 个` : '（'}
            {counts.structural ? ` · 结构版式 ${counts.structural} 个）` : '）'}
            ，最终横/竖方向以「A+ 类型」的画幅为准
          </div>
          <Spin spinning={loading}>
            <div style={{ display: 'grid', gridTemplateColumns: `repeat(${COLS}, 1fr)`, gap: 10, maxHeight: 520, overflowY: 'auto', paddingRight: 4 }}>
              {tab === 'preset' && uploadBox}
              {activeList.length === 0 && tab !== 'preset' ? (
                <div style={{ gridColumn: '1 / -1' }}>
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={tab === 'mine' ? '还没有上传过排版' : '还没有收藏排版'}
                  />
                </div>
              ) : (
                activeList.map(card)
              )}
            </div>
          </Spin>
        </div>

        <div style={{ width: 230, borderLeft: `1px solid ${token.colorBorderSecondary}`, paddingLeft: 12 }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>
            已选版式 ({selected.length}/{apiLimit})
          </div>
          {selected.length === 0 ? (
            <div style={{ textAlign: 'center', paddingTop: 80, color: token.colorTextTertiary }}>
              <div style={{ fontSize: 32, marginBottom: 8 }}><UploadOutlined /></div>
              <Text type="secondary" style={{ fontSize: 12 }}>暂无已选版式</Text>
              <div style={{ marginTop: 8, fontSize: 11, color: token.colorTextQuaternary }}>
                不选则按官方版式随机生成
              </div>
            </div>
          ) : (
            <Space direction="vertical" size={6} style={{ width: '100%' }}>
              {selected.map((k) => {
                const it = allByKey[k]
                return (
                  <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    {it ? <div style={{ width: 56, flex: '0 0 56px' }}><LayoutThumb layout={it} height={32} /></div> : null}
                    <Text style={{ fontSize: 12, flex: 1 }} ellipsis>{it?.name || k}</Text>
                    <Button
                      type="text" size="small" danger
                      onClick={() => setSelected(selected.filter((x) => x !== k))}
                    >
                      移除
                    </Button>
                  </div>
                )
              })}
            </Space>
          )}
        </div>
      </div>

      {/* 放大预览：卡片框只有 104px 高，判断版式结构必须能看大图 */}
      <Modal
        open={!!preview}
        onCancel={() => setPreview(null)}
        width={1000}
        title={preview?.name || '预览版式'}
        destroyOnClose
        zIndex={1200}
        footer={
          preview ? (
            <Space>
              <Button
                onClick={() => { void toggleFavorite(preview) }}
              >
                {(preview.favorited ?? preview.favorite) ? '取消收藏' : '收藏'}
              </Button>
              <Button
                type={selected.includes(preview.key) ? 'default' : 'primary'}
                onClick={() => { toggle(preview); setPreview(null) }}
              >
                {selected.includes(preview.key) ? '取消选择' : '选择此版式'}
              </Button>
            </Space>
          ) : null
        }
      >
        {preview && (
          <div style={{ position: 'relative', background: token.colorFillQuaternary, borderRadius: 6 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 'min(46vh, 380px)' }}>
              {preview.cover_url || preview.image_url ? (
                <img
                  src={preview.image_url || preview.cover_url}
                  alt={preview.name}
                  style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', display: 'block' }}
                />
              ) : (
                <div style={{ width: '70%' }}><LayoutThumb layout={preview} height={420} /></div>
              )}
            </div>
            {(() => {
              const list = activeList
              const i = list.findIndex((x) => x.key === preview.key)
              if (i < 0 || list.length < 2) return null
              // 切换图片必须同步换草稿：否则下一张显示的是上一张的分区描述，
              // 看似「描述没切」，实际是把 A 的描述存到 B 上
              const go = (d: number) => openPreview(list[(i + d + list.length) % list.length])
              const btn = (dir: number, icon: React.ReactNode, off: number) => (
                <Button
                  shape="circle" type="text" icon={icon} onClick={() => go(dir)}
                  style={{
                    position: 'absolute', top: '50%', transform: 'translateY(-50%)',
                    [off < 0 ? 'left' : 'right']: 8, background: token.colorBgContainer, opacity: 0.85,
                  } as React.CSSProperties}
                />
              )
              return (
                <>
                  {btn(-1, <LeftOutlined />, -1)}
                  {btn(1, <RightOutlined />, 1)}
                  <div style={{ position: 'absolute', right: 12, bottom: 8, fontSize: 12, color: token.colorTextTertiary }}>
                    {i + 1} / {list.length}
                  </div>
                </>
              )
            })()}
          </div>
        )}
        {preview && (
          <div style={{ marginTop: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
              <Space size={4}>
                <Text strong style={{ fontSize: 13 }}>版式结构描述</Text>
                <Tooltip title="写进提示词的分区约束（例如「左侧40%文案区、右侧60%商品图」）。留空则只靠这张参考图，模型自己猜分区。">
                  <QuestionCircleOutlined style={{ fontSize: 12, color: token.colorTextTertiary }} />
                </Tooltip>
                {draft ? null : <Text type="secondary" style={{ fontSize: 12 }}>留空则只靠参考图</Text>}
              </Space>
              <Space size={8}>
                <Text type="secondary" style={{ fontSize: 12 }}>{draft ? `${draft.length} 字` : ''}</Text>
                <Button size="small" icon={<BulbOutlined />} loading={suggesting} onClick={doSuggest}>
                  AI 帮我写
                </Button>
                <Button size="small" type="primary" loading={saving} onClick={doSave}>
                  保存
                </Button>
              </Space>
            </div>
            <Input.TextArea
              rows={3}
              value={draft}
              maxLength={500}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="例：左侧约40%为文案区，自上而下主标题与两行副文案；右侧约60%为商品大图，商品为画面最亮主体。"
            />
          </div>
        )}
      </Modal>
    </Modal>
  )
}

export default AIImageLayoutPicker
