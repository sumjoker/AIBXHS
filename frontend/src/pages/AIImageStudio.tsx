import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  Card,
  Button,
  Tag,
  Typography,
  Space,
  message,
  Empty,
  Select,
  Row,
  Col,
  Input,
  InputNumber,
  Tooltip,
  Image,
  Popconfirm,
  Spin,
  Badge,
  Modal,
  Radio,
  Pagination,
  Checkbox,
  Switch,
  theme,
} from 'antd'
import {
  UploadOutlined,
  PictureOutlined,
  ReloadOutlined,
  DeleteOutlined,
  ThunderboltOutlined,
  DownloadOutlined,
  BulbOutlined,
  AppstoreOutlined,
  FormOutlined,
  SearchOutlined,
  MinusOutlined,
  PlusOutlined,
  LeftOutlined,
  RightOutlined,
  RedoOutlined,
  StopOutlined,
  LinkOutlined,
  QuestionCircleOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons'
import type { UploadFile } from 'antd/es/upload'
import BindProductModal from '../components/BindProductModal'
import AIImageLayoutPicker from '../components/AIImageLayoutPicker'
import {
  aiImageApi,
  aiCreationApi,
  productsApi,
  AIImageTask,
  AIImageResultItem,
  AIImageLayout,
  AmazonProductAnalysisResult,
} from '../api'

const { Text } = Typography
const { TextArea } = Input

const DEFAULT_MAX_IMAGES = 8      // 受 PE 节点通道数约束，服务端 /options 会覆盖
const DEFAULT_MAX_SHEETS = 12     // 单任务生成张数上限，服务端 /options 会覆盖
const PRODUCT_PAGE_SIZE = 8

/** 比例卡片顺序（实际展示以 /options 返回的可用项为准） */
const ALL_RATIO_KEYS = ['1:1', '2:3', '3:2', '3:4', '4:3', '9:16', '16:9', '21:9']

interface ProductOptionItem {
  id: number
  product_code: string
  name: string
  images: string[]
}

/** A+ 类型卡片（标签 / 尺寸 / 说明全部来自 /options 的 aplus_types，前端不再写死） */
interface AplusOption {
  value: string
  label: string
  size: string
  desc: string
}

/** 场景图父项 + 两条子路径（来自 /options 的 scene_group） */
interface SceneGroup {
  key: string
  label: string
  hint: string
  variants: { key: string; label: string; hint: string }[]
}

/** 兼容 v1 旧记录的类型标签（现代类型标签走 /options.main_types） */
const LEGACY_TYPE_LABELS: Record<string, string> = { aplus: 'A+图' }

const STATUS_COLORS: Record<string, string> = {
  排队中: 'default',
  生成中: 'processing',
  已完成: 'success',
  部分完成: 'warning',
  失败: 'error',
  已取消: 'default',
}

/** 品牌设置初始值：服务端 /options.brand_defaults 到达后覆盖前三项 */
const BRAND_DEFAULTS = {
  country: 'US 美国',
  language: '英语',
  platform: '亚马逊',
  // A+ 画面文字：no_text=画面不出现任何文字（默认，文案后期叠加）/ with_text=允许 PE 排版英文文案。
  // 无字时后端会同时剔除「画面文字语言」——该字段会把 PE 整段输出切成英文（2026-10-28 实测）。
  text_mode: 'no_text',
}

type BrandSettings = typeof BRAND_DEFAULTS

/** AI 分析结果里的"模型自述不确定性"句子，喂进提示词会污染画面描述，直接丢弃 */
const UNCERTAIN_FACT = /无法(确认|判断|识别)|不能确定|不确定|无法看清|看不清|图片中(未|没有)/

/** 事实条目清洗：按分号拆分 → 去首尾标点 → 丢弃空项与元话语（避免出现「白色、、红色」） */
const cleanFacts = (arr?: (string | undefined | null)[]): string[] =>
  (arr || [])
    .flatMap((s) => String(s ?? '').split(/[；;]/))
    .map((s) => s.trim().replace(/^[、，,。\s]+|[、，,。\s]+$/g, ''))
    .filter((s) => s.length >= 2 && !UNCERTAIN_FACT.test(s))

/** AI 分析结构化结果 → 商品事实文本（供提示词模板的【商品事实】段使用） */
const analysisToText = (r: AmazonProductAnalysisResult): string => {
  const name = (r.product_name_cn || r.product_name_en || '').trim()
  const groups: [string, (string | undefined | null)[] | undefined][] = [
    ['类型', [r.product_type]],
    ['材质', r.material],
    ['颜色', r.colors],
    ['核心卖点', r.selling_points],
    ['使用场景', r.usage_scenarios],
    ['目标人群', r.target_audience],
  ]
  const lines = [name ? `品名：${name}` : '']
  for (const [label, values] of groups) {
    const list = cleanFacts(values)
    if (list.length) lines.push(`${label}：${list.join('、')}`)
  }
  return lines.filter(Boolean).join('\n')
}

const formatCost = (sec: number | null): string => {
  if (!sec) return '-'
  if (sec < 60) return `${sec}秒`
  return `${Math.floor(sec / 60)}分${sec % 60}秒`
}

/** 从 URL 推断扩展名（回传图可能是 png 或 jpg，下载时保持真实后缀） */
const fileExt = (url?: string | null): string => {
  const m = (url || '').split('?')[0].match(/\.([a-z0-9]{2,5})$/i)
  return (m?.[1] || 'png').toLowerCase()
}

const AIImageStudio: React.FC = () => {
  // 主题色：上传区/类型卡片等自绘边框必须跟 antd token 走，不能写死紫色
  const { token } = theme.useToken()
  // ===== 左栏表单状态 =====
  const [fileList, setFileList] = useState<UploadFile[]>([])
  const [maxImages, setMaxImages] = useState(DEFAULT_MAX_IMAGES)
  const [maxSheets, setMaxSheets] = useState(DEFAULT_MAX_SHEETS)
  const maxLayouts = 8   // 与后端 IMAGE_MAX_LAYOUTS_PER_TASK / 官方交互一致
  /** 生成通道：local=本地 Qwen Image 2.1 / relay=中转站 Nano Banana 2 */
  const [provider, setProvider] = useState<'local' | 'relay'>('local')
  const [providers, setProviders] = useState<
    { value: string; label: string; desc?: string; configured?: string }[]
  >([])
  const [dragging, setDragging] = useState(false)
  const [availableRatios, setAvailableRatios] = useState<string[]>(ALL_RATIO_KEYS)
  // 服务端下发的选项：A+ 类型、主副图标签/说明、场景图父子结构、品牌下拉
  const [aplusOptions, setAplusOptions] = useState<AplusOption[]>([])
  const [mainTypeLabels, setMainTypeLabels] = useState<Record<string, string>>({})
  const [mainTypeDescriptions, setMainTypeDescriptions] = useState<Record<string, string>>({})
  const [sceneGroup, setSceneGroup] = useState<SceneGroup | null>(null)
  const [brandOptions, setBrandOptions] = useState<{
    countries: string[]
    languages: string[]
    platforms: string[]
    font_styles: string[]
  }>({ countries: [], languages: [], platforms: [], font_styles: [] })
  const [analyzing, setAnalyzing] = useState(false)
  const [productInfo, setProductInfo] = useState('')
  // v2 分组：A+（单选，空=不生成）与主副图（多选）独立设置
  const [aplusType, setAplusType] = useState<string>('')
  const [aplusCount, setAplusCount] = useState(1)
  // 品牌A+横幅：品牌名为画面唯一文字，区分大小写（AI 艺术字直出，不走版式模板）
  const [brandName, setBrandName] = useState('')
  // A+ 版式：选中的排版（每种生成一张；空=后端随机官方版式）
  const [layouts, setLayouts] = useState<AIImageLayout[]>([])
  const [layoutOpen, setLayoutOpen] = useState(false)
  const [mainEnabled, setMainEnabled] = useState(true)
  const [selectedMainTypes, setSelectedMainTypes] = useState<string[]>([
    'white_background',
    'scene',
  ])
  const [mainCounts, setMainCounts] = useState<Record<string, number>>({
    white_background: 1,
    scene: 1,
    scene_person: 1,
  })
  const [mainAspectRatio, setMainAspectRatio] = useState('1:1')
  const [brand, setBrand] = useState<BrandSettings>({ ...BRAND_DEFAULTS })
  const [extraPrompt, setExtraPrompt] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [resultDetail, setResultDetail] = useState<{ task: AIImageTask; index: number } | null>(null)
  /** 待绑定至产品的图片（打开 BindProductModal） */
  const [bindTarget, setBindTarget] = useState<{ url: string; title?: string } | null>(null)
  const [h3BaseUrl, setH3BaseUrl] = useState('')
  const [comfyPrompt, setComfyPrompt] = useState<{ original: string; enhanced: string } | null>(null)
  const [comfyLoading, setComfyLoading] = useState(false)
  const comfyCacheRef = useRef<Map<string, { original: string; enhanced: string }>>(new Map())
  const [hoverId, setHoverId] = useState<number | null>(null)
  const [activeHistoryId, setActiveHistoryId] = useState<number | null>(null)

  // ===== 上传区（本地上传 + 产品库选择，对齐 AI 生视频） =====
  const localInputRef = useRef<HTMLInputElement>(null)
  const [uploadAreaHover, setUploadAreaHover] = useState(false)
  const [productModalOpen, setProductModalOpen] = useState(false)
  const [productSearchText, setProductSearchText] = useState('')
  const [productKeyword, setProductKeyword] = useState('')
  const [productOptions, setProductOptions] = useState<ProductOptionItem[]>([])
  const [productTotal, setProductTotal] = useState(0)
  const [productPage, setProductPage] = useState(1)
  const [productLoading, setProductLoading] = useState(false)
  const [selectedImageUrls, setSelectedImageUrls] = useState<string[]>([])
  // 从产品库选图时记录来源产品（编码/品名随任务落库）
  const [selectedProducts, setSelectedProducts] = useState<ProductOptionItem[]>([])

  // ===== 右栏生成记录状态 =====
  const [tasks, setTasks] = useState<AIImageTask[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState<string>('')
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** 进行中的任务 id（轮询用 ref，避免定时器闭包读到旧列表） */
  const activeIdsRef = useRef<number[]>([])

  const pageSize = 10

  const fetchTasks = useCallback(
    async (silent = false, override?: { page?: number; keyword?: string; status?: string }) => {
      if (!silent) setLoading(true)
      try {
        const { data } = await aiImageApi.listTasks(
          override?.page ?? page,
          pageSize,
          override?.keyword !== undefined ? override.keyword : keyword,
          override?.status !== undefined ? override.status : statusFilter,
        )
        setTasks(data.items || [])
        setTotal(data.total || 0)
      } catch (e: any) {
        if (!silent) message.error(e?.response?.data?.detail || '获取任务列表失败')
      } finally {
        if (!silent) setLoading(false)
      }
    },
    [page, keyword, statusFilter],
  )

  useEffect(() => {
    fetchTasks()
  }, [fetchTasks])

  // 一次性拉取服务端能力：ComfyUI 地址、参考图/张数上限、真实可用比例、
  // A+/主副图类型与品牌下拉（类型标签与尺寸文案以服务端为准，前端不再各存一份）
  useEffect(() => {
    aiImageApi
      .getOptions()
      .then(({ data }) => {
        setH3BaseUrl(data?.h3_base_url || '')
        if (data?.max_ref_images) setMaxImages(data.max_ref_images)
        if (data?.max_sheets_per_task) setMaxSheets(data.max_sheets_per_task)
        if (data?.providers?.length) setProviders(data.providers)
        const keys = Object.keys(data?.aspect_ratios || {})
        if (keys.length) {
          const ordered = ALL_RATIO_KEYS.filter((k) => keys.includes(k))
          setAvailableRatios(ordered.length ? ordered : keys)
        }
        if (data?.aplus_types) {
          setAplusOptions(
            Object.entries(data.aplus_types).map(([value, o]) => ({
              value,
              label: o.label,
              // 老版本服务端只有 target_size，按它拼展示文案，避免出现空尺寸
              size:
                o.size ||
                (Array.isArray(o.target_size) && o.target_size.length === 2
                  ? `${o.target_size[0]}×${o.target_size[1]}`
                  : ''),
              desc: o.desc || '',
            })),
          )
        }
        if (data?.main_types) setMainTypeLabels(data.main_types)
        if (data?.main_type_descriptions) setMainTypeDescriptions(data.main_type_descriptions)
        if (data?.scene_group?.variants?.length) setSceneGroup(data.scene_group)
        if (data?.brand_options) setBrandOptions(data.brand_options)
        // 默认国家/语言/平台同样以服务端为准（与提示词里的市场语境保持一致）
        const bd = data?.brand_defaults
        if (bd) {
          setBrand((b) => ({
            ...b,
            country: bd.country || b.country,
            language: bd.language || b.language,
            platform: bd.platform || b.platform,
          }))
        }
      })
      .catch(() => setH3BaseUrl(''))
  }, [])

  // 当前选中比例若不在服务端可用列表内，回落到第一个可用项（避免提交必失败）
  useEffect(() => {
    if (availableRatios.length && !availableRatios.includes(mainAspectRatio)) {
      setMainAspectRatio(availableRatios[0])
    }
  }, [availableRatios, mainAspectRatio])

  // ===== 主副图卡片结构（服务端下发） =====
  // 场景图在 UI 上是「父项 + 两条子路径」：父项 key 复用 scene，
  // 子路径 scene / scene_person 不单独占一行，故从行列表里剔除。
  const sceneVariants = sceneGroup?.variants || []
  const sceneVariantKeys = sceneVariants.map((v) => v.key)
  const sceneParentKey = sceneGroup?.key || ''
  const mainTypeRows = Object.entries(mainTypeLabels).filter(
    ([key]) => (sceneParentKey ? key === sceneParentKey || !sceneVariantKeys.includes(key) : true),
  )

  // 当前详情子项的 ComfyUI prompt_id（新记录取子项字段，旧记录回退任务级列表按下标取）
  const currentPromptId = resultDetail
    ? resultDetail.task.result_images?.[resultDetail.index]?.prompt_id ||
      resultDetail.task.comfy_prompt_ids?.[resultDetail.index] ||
      ''
    : ''

  // 打开详情时优先用库里落的提示词（ComfyUI /history 会过期，过期后查不到）；
  // 老记录没有落库字段时才回退到 /comfy-history，按 prompt_id 缓存避免翻张重复请求
  useEffect(() => {
    if (!resultDetail) {
      setComfyPrompt(null)
      return
    }
    const it = resultDetail.task.result_images?.[resultDetail.index]
    const dbOriginal = (it?.final_prompt || '').trim()
    const dbEnhanced = (it?.enhanced_prompt || '').trim()

    // 库里两条都有 → 完全不打扰 ComfyUI
    if (dbOriginal && dbEnhanced) {
      setComfyPrompt({ original: dbOriginal, enhanced: dbEnhanced })
      setComfyLoading(false)
      return
    }
    const dbPartial = dbOriginal || dbEnhanced
    setComfyPrompt(dbPartial ? { original: dbOriginal, enhanced: dbEnhanced } : null)

    if (!currentPromptId) {
      setComfyLoading(false)
      return
    }
    const cached = comfyCacheRef.current.get(currentPromptId)
    if (cached) {
      setComfyPrompt({
        original: dbOriginal || cached.original,
        enhanced: dbEnhanced || cached.enhanced,
      })
      setComfyLoading(false)
      return
    }
    let cancelled = false
    setComfyLoading(true)
    aiImageApi
      .getComfyHistory(currentPromptId)
      .then(({ data }) => {
        const info = {
          original: dbOriginal || data?.original_prompt || '',
          enhanced: dbEnhanced || data?.enhanced_prompt || '',
        }
        comfyCacheRef.current.set(currentPromptId, info)
        if (!cancelled) setComfyPrompt(info)
      })
      .catch(() => {
        if (!cancelled) {
          setComfyPrompt(dbPartial ? { original: dbOriginal, enhanced: dbEnhanced } : { original: '', enhanced: '' })
        }
      })
      .finally(() => {
        if (!cancelled) setComfyLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPromptId, resultDetail?.task.id, resultDetail?.index])

  const handleCopyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      message.success('已复制')
    } catch {
      message.error('复制失败，请手动选择复制')
    }
  }

  // 维护「进行中任务」id 列表（轮询只拉这几条，降流量）
  useEffect(() => {
    activeIdsRef.current = tasks
      .filter((t) => t.status === '排队中' || t.status === '生成中')
      .map((t) => t.id)
  }, [tasks])

  // 有进行中任务时 5 秒轮询单任务：只替换变化的那几条，不再整页重拉。
  // 页面不可见时暂停（后台标签页不必空转），切回来立即补一轮。
  useEffect(() => {
    let timer: number | undefined

    const pollOnce = async () => {
      const ids = activeIdsRef.current
      if (!ids.length) return
      try {
        const res = await Promise.all(ids.map((id) => aiImageApi.getTask(id)))
        const fresh = new Map(res.map((r) => [r.data.id, r.data]))
        setTasks((prev) => prev.map((t) => fresh.get(t.id) ?? t))
      } catch {
        // 单次失败静默，下一轮继续；避免网络抖动反复弹错
      }
    }

    const start = () => {
      if (timer !== undefined) return
      timer = window.setInterval(pollOnce, 5000)
    }
    const stop = () => {
      if (timer === undefined) return
      window.clearInterval(timer)
      timer = undefined
    }
    const onVisibilityChange = () => {
      if (document.hidden) {
        stop()
      } else {
        start()
        void pollOnce()
      }
    }

    if (!document.hidden) start()
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [])

  // ===== 上传辅助（对齐 AI 生视频） =====
  const appendFiles = (files: File[]) => {
    const images = files.filter((f) => f.type?.startsWith('image/'))
    if (images.length < files.length) {
      message.error('存在非图片文件，已自动忽略')
    }
    setFileList((prev) => {
      const remain = maxImages - prev.length
      if (images.length > remain) {
        message.warning(`最多上传 ${maxImages} 张图片`)
      }
      const accepted = images.slice(0, Math.max(0, remain))
      const items: UploadFile[] = accepted.map((f, i) => ({
        uid: `local-${Date.now()}-${i}-${f.name}`,
        name: f.name,
        status: 'done',
        thumbUrl: URL.createObjectURL(f),
        originFileObj: f as any,
      }))
      return [...prev, ...items]
    })
  }

  const handleLocalFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    if (files.length > 0) appendFiles(files)
    e.target.value = ''
  }

  const handleRemoveImage = (uid: string) => {
    setFileList((prev) => {
      const target = prev.find((f) => f.uid === uid)
      if (target?.thumbUrl?.startsWith('blob:')) URL.revokeObjectURL(target.thumbUrl)
      return prev.filter((f) => f.uid !== uid)
    })
  }

  const fetchProductOptions = async (page: number, keyword: string) => {
    setProductLoading(true)
    try {
      const res = await productsApi.getList({
        search: keyword || undefined,
        page,
        page_size: PRODUCT_PAGE_SIZE,
        product_type: 'finished',
      })
      if (res.data?.success && res.data?.data) {
        const payload: any = res.data.data
        const items: any[] = Array.isArray(payload) ? payload : payload?.items || []
        const total = Array.isArray(payload) ? (res.data.total ?? items.length) : (payload?.total ?? items.length)
        const curPage = Array.isArray(payload) ? (res.data.page ?? page) : (payload?.page ?? page)
        setProductOptions(
          items.map((prod) => ({
            id: prod.id,
            product_code: prod.product_code || '',
            name: prod.name || '',
            images: Array.from(
              new Set([...(prod.images || []), ...(prod.main_image ? [prod.main_image] : [])]),
            ).filter(Boolean) as string[],
          })),
        )
        setProductTotal(total)
        setProductPage(curPage)
      } else {
        message.error('获取产品列表失败')
      }
    } catch (err: any) {
      message.error(err.response?.data?.detail || '获取产品列表失败')
    } finally {
      setProductLoading(false)
    }
  }

  const openProductModal = () => {
    setSelectedImageUrls([])
    setProductSearchText('')
    setProductKeyword('')
    setProductModalOpen(true)
    fetchProductOptions(1, '')
  }

  const toggleProductImage = (url: string) => {
    setSelectedImageUrls((prev) =>
      prev.includes(url) ? prev.filter((u) => u !== url) : [...prev, url],
    )
  }

  const handleConfirmProductImages = () => {
    if (selectedImageUrls.length === 0) {
      message.warning('请先勾选产品图片')
      return
    }
    setFileList((prev) => {
      const existUrls = new Set(prev.map((f) => f.url || f.thumbUrl).filter(Boolean))
      const freshUrls = selectedImageUrls.filter((url) => !existUrls.has(url))
      const remain = maxImages - prev.length
      if (freshUrls.length > remain) {
        message.warning(`最多上传 ${maxImages} 张图片`)
      }
      const urls = freshUrls.slice(0, Math.max(0, remain))
      const items: UploadFile[] = urls.map((url, i) => ({
        uid: `pm-${Date.now()}-${i}`,
        name: url.split('/').pop() || 'product-image',
        status: 'done',
        url,
        thumbUrl: url,
      }))
      return [...prev, ...items]
    })
    // 记录选图来源产品（编码/品名随任务落库）
    const matched = productOptions.filter((prod) =>
      selectedImageUrls.some((u) => prod.images?.includes(u)),
    )
    setSelectedProducts((prev) => {
      const ids = new Set(prev.map((prod) => prod.id))
      return [...prev, ...matched.filter((prod) => !ids.has(prod.id))]
    })
    setSelectedImageUrls([])
    setProductModalOpen(false)
    message.success('已添加所选产品图片')
  }

  /** 从 blob 响应里抠出后端 detail（responseType=blob 时错误体也是 Blob） */
  const readBlobError = async (data: any): Promise<string> => {
    if (!data) return ''
    if (typeof data === 'string') return data
    if (data instanceof Blob) {
      try {
        const text = await data.text()
        try {
          const j = JSON.parse(text)
          return j?.detail || ''
        } catch {
          return text.slice(0, 120)
        }
      } catch {
        return ''
      }
    }
    return data?.detail || ''
  }

  const fetchImageBlob = async (
    url: string,
  ): Promise<{ blob?: Blob; error?: string }> => {
    try {
      const resp = await fetch(url)
      if (resp.ok) return { blob: await resp.blob() }
    } catch {
      // 直连失败（跨域等），尝试后端代理
    }
    try {
      const resp = await aiCreationApi.proxyImage(url)
      return { blob: resp.data }
    } catch (e: any) {
      const detail = await readBlobError(e?.response?.data)
      return { error: detail || e?.message || '图片下载失败' }
    }
  }

  // 收集所有图片为 File：本地文件直接用，产品图片 URL 需下载转 File
  // 失败项单独带出原因 —— 否则用户只会看到「请至少上传一张参考图」，真实原因被吞掉
  const collectFiles = async (): Promise<{
    files: File[]
    failed: { name: string; reason: string }[]
  }> => {
    const files: File[] = []
    const failed: { name: string; reason: string }[] = []
    for (const f of fileList) {
      if (f.originFileObj instanceof File) {
        files.push(f.originFileObj)
      } else if (f.url || f.thumbUrl) {
        const name = f.name || `image-${f.uid}.jpg`
        const { blob, error } = await fetchImageBlob(f.url || f.thumbUrl!)
        if (blob) {
          files.push(new File([blob], name, { type: blob.type || 'image/jpeg' }))
        } else {
          failed.push({ name, reason: error || '图片下载失败' })
        }
      }
    }
    return { files, failed }
  }

  // ===== AI 分析商品信息 =====
  const handleAnalyze = async () => {
    const { files, failed } = await collectFiles()
    if (!files.length) {
      if (failed.length) {
        message.error(`参考图读取失败：${failed[0].reason}`)
      } else {
        message.warning('请先上传参考图')
      }
      return
    }
    if (failed.length) message.warning(`${failed.length} 张参考图读取失败，已跳过`)
    setAnalyzing(true)
    try {
      const { data } = await aiCreationApi.analyzeProduct(files)
      if (data?.success && data.data) {
        // 后端已用同一份 _card_to_text 拼好 11 字段文本（含组件/尺寸/使用方式），
        // 直接用它，保证「AI 分析」与中转站自动分析的字段一致；旧后端无 text 时回退本地映射。
        const text = (data.text || '').trim()
        setProductInfo(text || analysisToText(data.data))
        message.success('AI 分析完成，商品信息已填充')
      } else {
        message.error('AI 分析未返回结果')
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || 'AI 分析失败')
    } finally {
      setAnalyzing(false)
    }
  }

  // ===== 提交生成任务（v2 分组参数）=====
  const activeMainTypeCount = mainEnabled ? selectedMainTypes.length : 0
  // A+ 张数：选了版式时每种版式一张（版式数即张数），未选时按张数输入；
  // 品牌横幅不使用版式，张数完全由用户自选。
  const aplusSheets = aplusType
    ? (aplusType === 'aplus_banner' ? aplusCount : (layouts.length || aplusCount))
    : 0
  const totalSheets =
    aplusSheets +
    (mainEnabled ? selectedMainTypes.reduce((a, t) => a + (mainCounts[t] || 1), 0) : 0)

  /** 张数加减控件（父项与场景图子路径共用，1~4 张/类型） */
  const renderCountStepper = (key: string) => (
    <Space size={2} onClick={(e) => e.stopPropagation()}>
      <Button
        size="small"
        type="text"
        icon={<MinusOutlined />}
        disabled={(mainCounts[key] || 1) <= 1}
        onClick={() => setMainCounts((m) => ({ ...m, [key]: Math.max(1, (m[key] || 1) - 1) }))}
      />
      <Text strong style={{ minWidth: 18, textAlign: 'center' }}>
        {mainCounts[key] || 1}
      </Text>
      <Button
        size="small"
        type="text"
        icon={<PlusOutlined />}
        disabled={(mainCounts[key] || 1) >= 4}
        onClick={() => setMainCounts((m) => ({ ...m, [key]: Math.min(4, (m[key] || 1) + 1) }))}
      />
    </Space>
  )

  const handleSubmit = async () => {
    if (submitting) return          // 双保险：loading 生效前也挡住连点
    setSubmitting(true)             // 必须最先置位：collectFiles 可能耗时数秒
    try {
      const { files, failed } = await collectFiles()
      if (!files.length) {
        if (failed.length) {
          message.error(`参考图读取失败：${failed[0].reason}`)
        } else {
          message.warning('请至少上传一张参考图')
        }
        return
      }
      if (failed.length) message.warning(`${failed.length} 张参考图读取失败，已跳过`)
      const activeMainTypes = mainEnabled ? selectedMainTypes : []
      if (!aplusType && !activeMainTypes.length) {
        message.warning('请在「A+图设置」或「主副图设置」中至少选择一种类型')
        return
      }
      if (aplusType === 'aplus_banner') {
        const name = brandName.trim()
        // 英文品牌名：2–20 字符，区分大小写，至少含一个字母
        if (
          !/^[A-Za-z0-9&'.,\- ]{2,20}$/.test(name) ||
          !/[A-Za-z]/.test(name)
        ) {
          message.warning('请填写英文品牌名（2–20 个字符，区分大小写，仅字母/数字/空格/&\'-,.）')
          return
        }
      }
      if (totalSheets > maxSheets) {
        message.warning(`单个任务最多生成 ${maxSheets} 张（当前 ${totalSheets} 张），请减少类型或张数`)
        return
      }
      const firstFile = files[0]
      const baseName = firstFile.name.replace(/\.[^.]+$/, '')
      // 标题优先用产品名（从产品库选图时文件名是 URL 末段，不可读）
      const taskTitle = selectedProducts[0]?.name || baseName || '商品套图'
      const idemKey = `aiimg-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
      await aiImageApi.createTask(
        files,
        {
          title: taskTitle,
          aplus_type: aplusType || undefined,
          product_code: selectedProducts[0]?.product_code || '',
          product_name: selectedProducts[0]?.name || '',
          main_types: activeMainTypes.length
            ? Object.fromEntries(activeMainTypes.map((t) => [t, mainCounts[t] || 1]))
            : undefined,
          main_aspect_ratio: activeMainTypes.length ? mainAspectRatio : undefined,
          image_count: aplusType ? aplusSheets : 1,
          layout_ids:
            aplusType && aplusType !== 'aplus_banner' ? layouts.map((l) => l.key) : [],
          brand_name: aplusType === 'aplus_banner' ? brandName.trim() : undefined,
          product_info: productInfo,
          prompt: extraPrompt,
          brand_settings: {
            ...Object.fromEntries(
              Object.entries(brand).filter(([, v]) => v !== ''),
            ),
          },
          provider,
        },
        idemKey,
      )
      message.success(`任务已提交（共 ${totalSheets} 张），正在后台生成`)
      setFileList([])
      setPage(1)
      fetchTasks(false, { page: 1, keyword, status: statusFilter })
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '提交任务失败')
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async (taskId: number) => {
    try {
      await aiImageApi.deleteTask(taskId)
      message.success('已删除')
      fetchTasks(true, { page, keyword, status: statusFilter })
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败')
    }
  }

  /** 取消进行中的任务（已完成的图保留） */
  const handleCancelTask = (taskId: number) => {
    Modal.confirm({
      title: '取消该任务？',
      content: '仍在排队/执行中的图片会停止生成，已完成的图片保留在记录中。',
      okText: '取消任务',
      okButtonProps: { danger: true },
      cancelText: '继续生成',
      onOk: async () => {
        try {
          await aiImageApi.cancelTask(taskId)
          message.success('已取消')
          fetchTasks(true, { page, keyword, status: statusFilter })
        } catch (e: any) {
          message.error(e?.response?.data?.detail || '取消失败')
        }
      },
    })
  }

  /** 重试失败子项（复用已上传参考图，不要求重新上传） */
  const handleRetry = async (taskId: number, indices?: number[]) => {
    try {
      const { data } = await aiImageApi.retryTask(taskId, indices)
      message.success(
        data?.retried ? `已重新提交 ${data.retried} 张，正在生成` : '已提交重试',
      )
      fetchTasks(true, { page, keyword, status: statusFilter })
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '重试失败')
    }
  }

  /** 打包下载全部已生成图片 */
  const handleDownloadAll = async (task: AIImageTask) => {
    const hide = message.loading('正在打包图片…', 0)
    try {
      const { data } = await aiImageApi.downloadZip(task.id)
      const url = URL.createObjectURL(data)
      const a = document.createElement('a')
      a.href = url
      a.download = `${task.title || `任务${task.id}`}.zip`
      a.click()
      URL.revokeObjectURL(url)
      message.success('已开始下载')
    } catch {
      message.error('打包下载失败，请稍后重试')
    } finally {
      hide()
    }
  }

  /** 一键做同款：把历史任务的参数回填到左侧表单 */
  /** 编辑（恢复历史）：参考图（URL 形态）+ 参数全部回填左侧表单 */
  const applyRestore = (task: AIImageTask) => {
    // 替换上传区图片前先释放旧的 blob 地址
    fileList.forEach((f) => {
      if (f.thumbUrl?.startsWith('blob:')) URL.revokeObjectURL(f.thumbUrl)
    })
    setFileList(
      (task.images || []).map((url, i) => ({
        uid: `history-${task.id}-${i}`,
        name: `参考图${i + 1}.jpg`,
        status: 'done' as const,
        url,
        thumbUrl: url,
      })),
    )
    if (task.aplus_type) setAplusType(task.aplus_type)
    else setAplusType('')
    setAplusCount(task.image_count || 1)
    // 版式回填：从子项里取（同 key 去重），让「编辑/再做一单」保持同一批排版
    {
      const seen = new Set<string>()
      const restored: AIImageLayout[] = []
      ;(task.result_images || []).forEach((it) => {
        const lay = it.layout
        if (!lay?.key || seen.has(lay.key)) return
        seen.add(lay.key)
        restored.push({
          key: lay.key,
          id: lay.key.replace(/^\w+:/, ''),
          name: lay.name || lay.key,
          source: lay.source === 'upload' ? 'upload' : 'preset',
          ratio_tag: lay.ratio_tag,
          image_url: lay.image_url || undefined,
        })
      })
      setLayouts(restored)
    }
    if (Array.isArray(task.main_types)) {
      const typeList = task.main_types
      setSelectedMainTypes(typeList)
      setMainCounts((m) => {
        const next = { ...m }
        for (const t of typeList) if (next[t] == null) next[t] = 1
        return next
      })
    } else if (task.main_types && typeof task.main_types === 'object') {
      const keys = Object.keys(task.main_types)
      setSelectedMainTypes(keys)
      setMainCounts((m) => {
        const next = { ...m }
        for (const [t, c] of Object.entries(Object(task.main_types) as Record<string, number>))
          next[t] = c
        return next
      })
    }
    else if (!task.aplus_type) {
      // v1 旧记录：generation_types 里除 aplus 之外都是主副图类型（不再依赖前端标签表做白名单）
      const legacy = (task.generation_types || []).filter((t) => t !== 'aplus')
      setSelectedMainTypes(legacy.length ? legacy : ['white_background', 'scene'])
    }
    setMainAspectRatio(task.main_aspect_ratio || task.aspect_ratio || '1:1')
    if (task.brand_settings) setBrand({ ...BRAND_DEFAULTS, ...task.brand_settings })
    setBrandName(task.brand_settings?.brand_name || '')
    setProductInfo(task.product_info || '')
    setExtraPrompt(task.prompt || '')
    setActiveHistoryId(task.id)
    message.success('已填入该历史的参考图、提示词与参数')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  /** 编辑前覆盖确认：左侧已有内容时先确认，避免误覆盖 */
  const handleRestore = (task: AIImageTask) => {
    const hasExisting = fileList.length > 0 || productInfo.trim().length > 0
    if (!hasExisting) {
      applyRestore(task)
      return
    }
    Modal.confirm({
      title: '覆盖当前内容？',
      content: '左侧已上传的参考图和已填写的商品信息、参数将被该历史记录覆盖，确定继续吗？',
      okText: '覆盖',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => applyRestore(task),
    })
  }

  const handleDownload = async (url: string, name: string) => {
    try {
      const { data } = await aiCreationApi.proxyImage(url)
      const blobUrl = URL.createObjectURL(data)
      const a = document.createElement('a')
      a.href = blobUrl
      a.download = name
      a.click()
      URL.revokeObjectURL(blobUrl)
    } catch {
      window.open(url, '_blank')
    }
  }


  // ===== 渲染：结果图（大图网格，点击图片开单张详情）=====
  const renderResultItem = (item: AIImageResultItem, task: AIImageTask, index: number) => {
    const name = `${task.title || '套图'}-${item.label}${item.seq > 0 ? `-${item.seq + 1}` : ''}`
    if (item.status === '已完成' && item.url) {
      return (
        <div key={`${item.type}-${item.seq}`} style={{ position: 'relative' }}>
          <img
            src={item.url}
            alt={name}
            loading="lazy"
            onClick={() => setResultDetail({ task, index })}
            style={{
              borderRadius: 6,
              objectFit: 'cover',
              width: '100%',
              height: 150,
              display: 'block',
              cursor: 'zoom-in',
              background: token.colorFillQuaternary,
            }}
          />
          <Space style={{ position: 'absolute', top: 6, right: 6 }} size={4}>
            <Tooltip title="绑定到产品（提交审核）">
              <Button
                size="small"
                icon={<LinkOutlined />}
                onClick={() => setBindTarget({ url: item.url!, title: task.title })}
              />
            </Tooltip>
            <Tooltip title="下载">
              <Button
                size="small"
                icon={<DownloadOutlined />}
                onClick={() => handleDownload(item.url!, `${name}.${fileExt(item.url)}`)}
              />
            </Tooltip>
          </Space>
          <Text style={{ display: 'block', fontSize: 12, marginTop: 4 }}>
            {item.label}
            {item.layout?.name ? ` · ${item.layout.name}` : ''}
            {item.seq > 0 ? ` #${item.seq + 1}` : ''}
          </Text>
        </div>
      )
    }
    if (item.status === '失败') {
      return (
        <div
          key={`${item.type}-${item.seq}`}
          style={{
            border: '1px dashed #ffccc7',
            borderRadius: 6,
            padding: 16,
            textAlign: 'center',
            background: '#fff1f0',
            minHeight: 150,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <Tag color="error">失败</Tag>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {item.error || '生成失败'}
          </Text>
          <Button size="small" icon={<RedoOutlined />} onClick={() => handleRetry(task.id, [index])}>
            重试此张
          </Button>
        </div>
      )
    }
    if (item.status === '已取消') {
      return (
        <div
          key={`${item.type}-${item.seq}`}
          style={{
            border: `1px dashed ${token.colorBorder}`,
            borderRadius: 6,
            padding: 16,
            textAlign: 'center',
            background: token.colorFillQuaternary,
            minHeight: 150,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <Tag>已取消</Tag>
          <Text type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
            {item.label}
            {item.layout?.name ? ` · ${item.layout.name}` : ''}
            {item.seq > 0 ? ` #${item.seq + 1}` : ''}
          </Text>
          <Button
            size="small"
            icon={<RedoOutlined />}
            onClick={() => handleRetry(task.id, [index])}
          >
            重试此张
          </Button>
        </div>
      )
    }
    return (
      <div
        key={`${item.type}-${item.seq}`}
        style={{
          border: `1px dashed ${token.colorBorder}`,
          borderRadius: 6,
          padding: 16,
          textAlign: 'center',
          minHeight: 150,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          gap: 8,
        }}
      >
        <Spin size="small" />
        <Tag color={item.status === '生成中' ? 'processing' : 'default'}>
          {item.label}
          {item.layout?.name ? ` · ${item.layout.name}` : ''}
          {item.seq > 0 ? ` #${item.seq + 1}` : ''} · {item.status}
        </Tag>
      </div>
    )
  }

  /** 是否存在可重试子项（失败 / 已取消）——按钮口径与后端一致 */
  const hasRetryable = (task: AIImageTask) =>
    (task.result_images || []).some(
      (i) => i.status === '失败' || i.status === '已取消',
    )

  /** 历史卡片的类型标签（v2 分组 + v1 兼容；标签取服务端 /options） */
  const renderTypeTags = (task: AIImageTask) => {
    const tags: string[] = []
    if (task.aplus_type) {
      const a = aplusOptions.find((o) => o.value === task.aplus_type)
      tags.push(a ? a.label : task.aplus_type)
    }
    if (Array.isArray(task.main_types) && task.main_types.length) {
      tags.push(...task.main_types.map((t) => mainTypeLabels[t] || t))
    } else if (task.main_types && typeof task.main_types === 'object') {
      tags.push(
        ...Object.keys(task.main_types).map((t) => mainTypeLabels[t] || t),
      )
    }
    if (!tags.length) {
      tags.push(...(task.generation_types || []).map((t) => LEGACY_TYPE_LABELS[t] || t))
    }
    return tags.map((t) => <Tag key={t}>{t}</Tag>)
  }

  /** 历史卡片的品牌设置摘要 */
  return (
    <>
    <style>
      {`
        /* 左栏固定宽度：切换生成通道时（描述文案长短不同）栏宽必须不变。
           Col 默认 min-width:auto，长文案会把 flex item 撑开，故四项一起锁死。
           388 → 416：按实测内容区偏窄 28px 追加。 */
        .aiimg-left-col {
          flex: 0 0 416px !important;
          width: 416px !important;
          min-width: 416px !important;
          max-width: 416px !important;
        }
        @media (max-width: 900px) {
          .aiimg-left-col {
            flex: 1 1 100% !important;
            width: 100% !important;
            min-width: 0 !important;
            max-width: 100% !important;
          }
        }
      `}
    </style>
    <div className="ai-thin-scrollbar" style={{ height: '100%', overflow: 'hidden', padding: 24, boxSizing: 'border-box' }}>
      <Row gutter={16} style={{ height: '100%', alignItems: 'stretch' }}>
        {/* 左栏：生成表单（栏内独立滚动，AICreationCenter 同模式） */}
        <Col
          xs={24}
          lg={9}
          className="aiimg-left-col"
          style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
        >
        <div style={{ flex: 1, minWidth: 0, minHeight: 0, overflowY: 'auto', overflowX: 'hidden', paddingBottom: 4 }}>
          <Card
            title={
              <Space>
                <PictureOutlined />
                <span>商品套图生成</span>
              </Space>
            }
            styles={{ body: { padding: '16px 16px 6px' } }}
          >
            <Space direction="vertical" className="aiimg-form-space" style={{ width: '100%' }} size={14}>
              <div>
                <Text strong style={{ display: 'block', marginBottom: 8 }}>生成模型</Text>
                <Select<string>
                  value={provider}
                  onChange={(v) => setProvider(v as 'local' | 'relay')}
                  style={{ width: '100%' }}
                  disabled={submitting}
                  options={(providers.length
                    ? providers
                    : [
                        { value: 'local', label: 'Qwen Image 2.1（本地）', desc: '细节保真高，串行排队', configured: '1' },
                        { value: 'relay', label: 'Nano Banana 2（中转站）', desc: '看图理解强，可并行', configured: '0' },
                      ]
                  ).map((p) => ({
                    value: p.value,
                    disabled: p.configured === '0',
                    // 选中态只显示模型名：388 宽度下把说明拼进来会被截断成一串省略号
                    label: p.value === 'relay' ? `🍌 ${p.label}` : p.label,
                    desc: p.desc || '',
                  }))}
                  optionRender={(opt) => {
                    const d = opt.data as unknown as { label: string; desc?: string }
                    return (
                      <div style={{ lineHeight: 1.4 }}>
                        <div>{d.label}</div>
                        {d.desc && (
                          <div style={{ color: token.colorTextTertiary, fontSize: 12 }}>{d.desc}</div>
                        )}
                      </div>
                    )
                  }}
                />
              </div>
              <div>
                <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                  <Text strong>参考图（1~{maxImages} 张）</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    已上传 {fileList.length}/{maxImages}
                  </Text>
                </Space>
                <div
                  onMouseEnter={() => setUploadAreaHover(true)}
                  onMouseLeave={() => setUploadAreaHover(false)}
                  onDragOver={(e) => {
                    e.preventDefault()
                    if (fileList.length < maxImages) setDragging(true)
                  }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={(e) => {
                    e.preventDefault()
                    setDragging(false)
                    if (fileList.length >= maxImages) {
                      message.warning(`最多上传 ${maxImages} 张图片`)
                      return
                    }
                    appendFiles(Array.from(e.dataTransfer.files || []))
                  }}
                  style={{
                    marginTop: 8,
                    border: `1px dashed ${dragging ? token.colorPrimary : uploadAreaHover ? token.colorPrimaryHover : token.colorPrimaryBorder}`,
                    borderRadius: 8,
                    padding: 16,
                    textAlign: 'center',
                    cursor: 'pointer',
                    minHeight: 120,
                    background: dragging ? token.colorPrimaryBg : undefined,
                    display: 'flex',
                    flexDirection: 'column',
                    justifyContent: 'center',
                  }}
                  onClick={() => {
                    if (fileList.length < maxImages) localInputRef.current?.click()
                  }}
                >
                  {fileList.length === 0 ? (
                    <>
                      <div
                        style={{
                          width: 48,
                          height: 48,
                          borderRadius: 10,
                          background: token.colorPrimaryBg,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          margin: '0 auto',
                        }}
                      >
                        <PictureOutlined style={{ fontSize: 22, color: token.colorPrimary }} />
                      </div>
                      <div style={{ marginTop: 8, color: token.colorTextSecondary, fontSize: 13 }}>
                        上传商品图片（点击或拖拽）
                      </div>
                      <div style={{ marginTop: 2, color: token.colorTextTertiary, fontSize: 12 }}>
                        第 1 张为主图（决定商品主体形态），后续图片作为细节/材质/场景的辅助参考
                      </div>
                    </>
                  ) : (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center' }}>
                      {fileList.map((f, idx) => (
                        <div key={f.uid} style={{ position: 'relative', width: 80, height: 80 }}>
                          <img
                            alt={f.name}
                            src={f.thumbUrl || f.url}
                            style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: 6, border: `1px solid ${token.colorBorderSecondary}` }}
                          />
                          <div
                            style={{
                              position: 'absolute',
                              left: 2,
                              bottom: 2,
                              padding: '0 5px',
                              height: 16,
                              lineHeight: '16px',
                              borderRadius: 8,
                              fontSize: 10,
                              color: '#fff',
                              background: idx === 0 ? token.colorPrimary : 'rgba(0,0,0,0.45)',
                            }}
                          >
                            {idx === 0 ? '主图' : `参考${idx}`}
                          </div>
                          <Button
                            danger
                            size="small"
                            shape="circle"
                            icon={<DeleteOutlined style={{ fontSize: 10 }} />}
                            style={{ position: 'absolute', top: -8, right: -8, width: 20, height: 20, minWidth: 20 }}
                            onClick={(e) => {
                              e.stopPropagation()
                              handleRemoveImage(f.uid)
                            }}
                          />
                        </div>
                      ))}
                    </div>
                  )}
                  <div style={{ height: 32, marginTop: 6, display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
                    {fileList.length < maxImages && (
                      <Space size={12}>
                        <Button
                          type="primary"
                          size="small"
                          icon={<UploadOutlined />}
                          onClick={(e) => {
                            e.stopPropagation()
                            localInputRef.current?.click()
                          }}
                        >
                          本地上传
                        </Button>
                        <Button
                          size="small"
                          icon={<AppstoreOutlined />}
                          onClick={(e) => {
                            e.stopPropagation()
                            openProductModal()
                          }}
                        >
                          从产品管理选择
                        </Button>
                      </Space>
                    )}
                  </div>
                </div>
                <input
                  ref={localInputRef}
                  type="file"
                  accept="image/*"
                  multiple
                  style={{ display: 'none' }}
                  onChange={handleLocalFileSelect}
                />
              </div>

              <div>
                <Space>
                  <Text strong>商品信息</Text>
                  <Button
                    size="small"
                    icon={<BulbOutlined />}
                    loading={analyzing}
                    onClick={handleAnalyze}
                  >
                    AI 分析
                  </Button>
                </Space>
                <TextArea
                  rows={4}
                  maxLength={2500}
                  showCount
                  value={productInfo}
                  onChange={(e) => setProductInfo(e.target.value)}
                  placeholder="填写商品卖点、材质、适用场景等；或点击「AI 分析」从参考图自动提取"
                  style={{ marginTop: 8 }}
                />
              </div>

              <Row gutter={12}>
                <Col span={8}>
                  <Text type="secondary" style={{ fontSize: 12 }}>销售国家/地区</Text>
                  <Select
                    style={{ width: '100%', marginTop: 4 }}
                    size="small"
                    value={brand.country}
                    onChange={(v) => setBrand((b) => ({ ...b, country: v }))}
                    showSearch
                    options={brandOptions.countries.map((s) => ({ value: s, label: s }))}
                  />
                </Col>
                <Col span={8}>
                  <Text type="secondary" style={{ fontSize: 12 }}>生成图片语言</Text>
                  <Select
                    style={{ width: '100%', marginTop: 4 }}
                    size="small"
                    value={brand.language}
                    onChange={(v) => setBrand((b) => ({ ...b, language: v }))}
                    showSearch
                    options={brandOptions.languages.map((s) => ({ value: s, label: s }))}
                  />
                </Col>
                <Col span={8}>
                  <Text type="secondary" style={{ fontSize: 12 }}>发布平台</Text>
                  <Select
                    style={{ width: '100%', marginTop: 4 }}
                    size="small"
                    value={brand.platform}
                    onChange={(v) => setBrand((b) => ({ ...b, platform: v }))}
                    options={brandOptions.platforms.map((s) => ({ value: s, label: s }))}
                  />
                </Col>
              </Row>

              {/* ===== A+图设置（单选） ===== */}
              <Card
                size="small"
                type="inner"
                title="A+图设置"
                extra={
                  <Radio.Group
                    size="small"
                    value={aplusType || 'none'}
                    onChange={(e) => setAplusType(e.target.value === 'none' ? '' : e.target.value)}
                  >
                    <Radio.Button value="none">不生成</Radio.Button>
                  </Radio.Group>
                }
              >
                <Radio.Group
                  value={aplusType}
                  onChange={(e) => setAplusType(e.target.value)}
                  style={{ width: '100%' }}
                >
                  <Space direction="vertical" style={{ width: '100%' }} size={6}>
                    {aplusOptions.length ? (
                      aplusOptions.map((o) => (
                        <Radio key={o.value} value={o.value} style={{ width: '100%' }}>
                          <Space size={6}>
                            <Text strong={aplusType === o.value}>{o.label}</Text>
                            {o.size && <Tag style={{ fontSize: 12 }}>{o.size}</Tag>}
                          </Space>
                          {o.desc && (
                            <div style={{ paddingLeft: 24 }}>
                              <Text type="secondary" style={{ fontSize: 12 }}>{o.desc}</Text>
                            </div>
                          )}
                        </Radio>
                      ))
                    ) : (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        加载 A+ 类型选项失败，请刷新页面重试
                      </Text>
                    )}
                  </Space>
                </Radio.Group>
                {aplusType && (
                  <div style={{ marginTop: 8 }}>
                    {aplusType === 'aplus_banner' ? (
                      <>
                        {/* 品牌A+横幅：画面只有品牌名，AI 艺术字直出，不使用排版模板 */}
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          品牌名（画面唯一文字，区分大小写）
                        </Text>
                        <Input
                          value={brandName}
                          onChange={(e) => setBrandName(e.target.value)}
                          placeholder="例如 LaVenty"
                          maxLength={20}
                          showCount
                          style={{ marginTop: 4 }}
                        />
                        <Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 4 }}>
                          仅英文品牌名（2–20 个字符）；背景、花纹、配色与字体气质跟随商品主图，画面不出现商品。
                        </Text>
                        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 10 }}>
                          生成张数
                        </Text>
                        <InputNumber
                          min={1}
                          max={4}
                          value={aplusCount}
                          onChange={(v) => setAplusCount(v || 1)}
                          style={{ width: '100%', marginTop: 4 }}
                        />
                      </>
                    ) : (
                      <>
                    {/* 版式：每种排版生成一张（对齐官方交互）。选了版式后张数由版式数决定 */}
                    <div
                      onClick={() => setLayoutOpen(true)}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer',
                        padding: '8px 10px', borderRadius: 6, marginBottom: 10,
                        border: `1px solid ${layouts.length ? token.colorPrimaryBorder : token.colorBorderSecondary}`,
                        background: token.colorFillQuaternary,
                      }}
                    >
                      <div
                        style={{
                          width: 26, height: 26, borderRadius: 5, flex: '0 0 26px',
                          background: token.colorPrimaryBg, display: 'flex',
                          alignItems: 'center', justifyContent: 'center',
                        }}
                      >
                        <AppstoreOutlined style={{ color: token.colorPrimary, fontSize: 14 }} />
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <Text style={{ fontSize: 12 }}>选择排版</Text>
                        <div>
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            {layouts.length
                              ? `已选 ${layouts.length} 个 · 每种排版生成一张`
                              : '未选 · 按官方排版随机生成'}
                          </Text>
                        </div>
                      </div>
                      <RightOutlined style={{ fontSize: 11, color: token.colorTextTertiary }} />
                    </div>
                    {layouts.length > 0 && (
                      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
                        {layouts.map((l) => (
                          <Tag
                            key={l.key}
                            closable
                            onClose={(e) => {
                              e.preventDefault()
                              setLayouts(layouts.filter((x) => x.key !== l.key))
                            }}
                            style={{ margin: 0, fontSize: 11 }}
                          >
                            {l.name}
                          </Tag>
                        ))}
                      </div>
                    )}
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      生成张数{layouts.length ? `（随版式，共 ${layouts.length} 张）` : ''}
                    </Text>
                    <InputNumber
                      min={1}
                      max={4}
                      value={layouts.length || aplusCount}
                      disabled={layouts.length > 0}
                      onChange={(v) => setAplusCount(v || 1)}
                      style={{ width: '100%', marginTop: 4 }}
                    />
                    {/* 无文字模式：默认开启。关闭后 PE 会自行排版英文文案（拼写/字重不可控，需人工复核） */}
                    <div
                      style={{
                        marginTop: 10,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '6px 10px',
                        borderRadius: 6,
                        border: `1px solid ${token.colorBorderSecondary}`,
                        background: token.colorFillQuaternary,
                      }}
                    >
                      <Space size={4}>
                        <Text style={{ fontSize: 12 }}>无文字模式</Text>
                        <Tooltip
                          title="开启：画面内不出现任何文字，文案请在后期设计工具叠加（推荐，拼写与字体可控）。关闭：允许 AI 自行排版英文文案，需人工核对拼写。"
                        >
                          <QuestionCircleOutlined style={{ fontSize: 12, color: token.colorTextTertiary }} />
                        </Tooltip>
                      </Space>
                      <Switch
                        size="small"
                        checked={brand.text_mode !== 'with_text'}
                        onChange={(checked) =>
                          setBrand((b) => ({ ...b, text_mode: checked ? 'no_text' : 'with_text' }))
                        }
                      />
                    </div>
                      </>
                    )}
                  </div>
                )}
              </Card>

              {/* ===== 主副图设置（开关 + 行式勾选 + 每类型独立张数 + 比例按钮组） ===== */}
              <Card
                size="small"
                type="inner"
                title={
                  <Space>
                    <span>主副图设置</span>
                    <Tooltip title="不生成主副图时可关闭此区块">
                      <Switch
                        size="small"
                        checked={mainEnabled}
                        onChange={(v) => setMainEnabled(v)}
                      />
                    </Tooltip>
                  </Space>
                }
              >
                {mainEnabled && (
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>生成类型（多选）</Text>
                    <div style={{ marginTop: 8 }}>
                      {mainTypeRows.length === 0 && (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          加载生成类型选项失败，请刷新页面重试
                        </Text>
                      )}
                      {mainTypeRows.map(([key, typeLabel]) => {
                        const isScene = !!sceneParentKey && key === sceneParentKey
                        // 场景图父项只承载两条子路径，标签/说明由服务端下发
                        const label = isScene ? sceneGroup?.label || typeLabel : typeLabel
                        // 场景图：父项是否"开启"取决于两个子路径里有没有选中的
                        const sceneSubs = isScene
                          ? sceneVariants.filter((v) => selectedMainTypes.includes(v.key))
                          : []
                        const checked = isScene ? sceneSubs.length > 0 : selectedMainTypes.includes(key)
                        const toggleRow = () => {
                          if (isScene) {
                            // 未开启 → 默认勾「不带人物」；已开启 → 收起并清空两条子路径
                            setSelectedMainTypes((prev) =>
                              checked
                                ? prev.filter((t) => !sceneVariantKeys.includes(t))
                                : [...prev.filter((t) => !sceneVariantKeys.includes(t)), 'scene'],
                            )
                            return
                          }
                          setSelectedMainTypes((prev) =>
                            checked ? prev.filter((t) => t !== key) : [...prev, key],
                          )
                        }
                        return (
                          <div key={key} style={{ marginBottom: 4 }}>
                            <div
                              style={{
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                padding: '6px 10px',
                                borderRadius: 6,
                                background: checked ? token.colorPrimaryBg : 'transparent',
                                border: checked ? `1px solid ${token.colorPrimaryBorder}` : '1px solid transparent',
                                cursor: 'pointer',
                              }}
                              onClick={toggleRow}
                            >
                              <Space size={8}>
                                <Checkbox checked={checked} />
                                <Tooltip title={isScene ? sceneGroup?.hint : mainTypeDescriptions[key]}>
                                  <Text strong={checked}>{label}</Text>
                                </Tooltip>
                                {isScene && (
                                  <Text type="secondary" style={{ fontSize: 11 }}>
                                    （展开选带/不带人物）
                                  </Text>
                                )}
                              </Space>
                              {checked &&
                                (isScene ? (
                                  <Text type="secondary" style={{ fontSize: 12 }}>
                                    共 {sceneSubs.reduce((a, v) => a + (mainCounts[v.key] || 1), 0)} 张
                                  </Text>
                                ) : (
                                  renderCountStepper(key)
                                ))}
                            </div>

                            {/* 场景图的两条子路径：各自独立勾选与张数 */}
                            {isScene && checked && (
                              <div
                                style={{
                                  marginLeft: 22,
                                  marginTop: 4,
                                  paddingLeft: 10,
                                  borderLeft: `2px solid ${token.colorPrimaryBorder}`,
                                }}
                              >
                                {sceneVariants.map((v) => {
                                  const sub = selectedMainTypes.includes(v.key)
                                  return (
                                    <div
                                      key={v.key}
                                      style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'space-between',
                                        padding: '4px 8px',
                                        borderRadius: 6,
                                        marginBottom: 3,
                                        background: sub ? token.colorPrimaryBg : 'transparent',
                                        cursor: 'pointer',
                                      }}
                                      onClick={(e) => {
                                        e.stopPropagation()
                                        setSelectedMainTypes((prev) =>
                                          sub ? prev.filter((t) => t !== v.key) : [...prev, v.key],
                                        )
                                      }}
                                    >
                                      <Space size={6}>
                                        <Checkbox checked={sub} />
                                        <Tooltip title={v.hint}>
                                          <Text style={{ fontSize: 12 }} strong={sub}>
                                            {v.label}
                                          </Text>
                                        </Tooltip>
                                      </Space>
                                      {sub && renderCountStepper(v.key)}
                                    </div>
                                  )
                                })}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                    <div style={{ marginTop: 10 }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>生成比例</Text>
                      <div
                        style={{
                          marginTop: 6,
                          display: 'grid',
                          gridTemplateColumns: 'repeat(4, 1fr)',
                          gap: 8,
                        }}
                      >
                        {[
                          '1:1', '2:3', '3:2', '3:4', '4:3', '9:16', '16:9', '21:9',
                        ]
                          .filter((r) => availableRatios.includes(r))
                          .map((value) => {
                          const o = { value, label: value, shape: value }
                          const checked = mainAspectRatio === o.value
                          // 形状图标：按比例绘制的小矩形（36x28 内）
                          const shapeMap: Record<string, [number, number]> = {
                            '2:3': [16, 24],
                            '3:2': [26, 17],
                            '9:16': [13, 24],
                            '16:9': [30, 17],
                            '1:1': [20, 20],
                            '4:3': [26, 20],
                            '3:4': [20, 26],
                            '21:9': [32, 14],
                          }
                          const [w, h] = shapeMap[o.shape] || [20, 20]
                          return (
                            <div
                              key={o.value}
                              onClick={() => setMainAspectRatio(o.value)}
                              style={{
                                cursor: 'pointer',
                                textAlign: 'center',
                                padding: '8px 0 6px',
                                borderRadius: 8,
                                border: checked ? `1.5px solid ${token.colorPrimary}` : `1px solid ${token.colorBorderSecondary}`,
                                background: checked ? token.colorPrimaryBg : token.colorBgContainer,
                              }}
                            >
                              <div
                                style={{
                                  height: 30,
                                  display: 'flex',
                                  alignItems: 'center',
                                  justifyContent: 'center',
                                }}
                              >
                                {o.shape === 'auto' ? (
                                  <div
                                    style={{
                                      width: 22,
                                      height: 22,
                                      border: '1.5px dashed #bfbfbf',
                                      borderRadius: 4,
                                    }}
                                  />
                                ) : (
                                  <div
                                    style={{
                                      width: w,
                                      height: h,
                                      border: checked
                                        ? `1.5px solid ${token.colorPrimary}`
                                        : '1.5px solid #8c8c8c',
                                      borderRadius: 3,
                                      background: checked ? token.colorPrimaryBgHover : token.colorFillQuaternary,
                                    }}
                                  />
                                )}
                              </div>
                              <Text
                                style={{ fontSize: 12 }}
                                type={checked ? undefined : 'secondary'}
                                strong={checked}
                              >
                                {o.label}
                              </Text>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  </div>
                )}
              </Card>

              <div>
                <Text strong>附加要求（可选）</Text>
                <TextArea
                  rows={2}
                  maxLength={500}
                  value={extraPrompt}
                  onChange={(e) => setExtraPrompt(e.target.value)}
                  placeholder="如：圣诞居家主题场景、杯身朝向镜头等"
                  style={{ marginTop: 8 }}
                />
              </div>

            </Space>
          </Card>
        </div>
        <div style={{ marginTop: 8, flexShrink: 0 }}>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={submitting}
            onClick={handleSubmit}
            block
            size="large"
            disabled={totalSheets === 0}
          >
            {totalSheets > 0
              ? `立即生成（共 ${totalSheets} 张：${
                  [
                    aplusType && `A+×${aplusSheets}`,
                    mainEnabled && activeMainTypeCount > 0 && `主副图×${totalSheets - (aplusType ? aplusCount : 0)}`,
                  ]
                    .filter(Boolean)
                    .join(' + ')
                }）`
              : '立即生成（请先选择生成类型）'}
          </Button>
          {totalSheets > maxSheets && (
            <div style={{ marginTop: 4 }}>
              <Text type="danger" style={{ fontSize: 12 }}>
                单个任务最多 {maxSheets} 张（当前 {totalSheets} 张），请减少类型或张数
              </Text>
            </div>
          )}
        </div>
        </Col>

        {/* 右栏：生成记录（maxWidth:none 抵消 antd lg 栅格的 62.5% 上限，填满剩余宽度） */}
        <Col xs={24} lg={15} style={{ height: '100%', flex: 1, minWidth: 0, maxWidth: 'none' }}>
          <Card
            style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
            styles={{ body: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' } }}
            title={
              <Space>
                <span>生成记录</span>
                {total > 0 && <Text type="secondary">共 {total} 条</Text>}
              </Space>
            }
            extra={
              <Space>
                <Input
                  size="small"
                  allowClear
                  prefix={<SearchOutlined />}
                  placeholder="搜索产品名 / 编码"
                  style={{ width: 180 }}
                  value={keyword}
                  onChange={(e) => {
                    const v = e.target.value
                    setKeyword(v)
                    if (searchTimerRef.current) clearTimeout(searchTimerRef.current)
                    searchTimerRef.current = setTimeout(() => {
                      setPage(1)
                      // 显式传 keyword：定时器闭包里的 fetchTasks 仍持有旧 keyword
                      fetchTasks(true, { page: 1, keyword: v, status: statusFilter })
                    }, 300)
                  }}
                />
                <Select
                  size="small"
                  style={{ width: 110 }}
                  value={statusFilter || 'all'}
                  onChange={(v) => {
                    const next = v === 'all' ? '' : v
                    setStatusFilter(next)
                    setPage(1)
                    fetchTasks(true, { page: 1, keyword, status: next })
                  }}
                  options={[
                    { value: 'all', label: '全部状态' },
                    ...Object.keys(STATUS_COLORS).map((s) => ({ value: s, label: s })),
                  ]}
                />
                <Tooltip title="刷新（回到第 1 页拉取最新记录）">
                  <Button
                    icon={<ReloadOutlined />}
                    loading={loading}
                    onClick={() => {
                      setPage(1)
                      fetchTasks(false, { page: 1, keyword, status: statusFilter })
                    }}
                    size="small"
                  />
                </Tooltip>
              </Space>
            }
          >
            {/* 列表滚动区：分页固定在卡片底部，对齐 AI 生视频布局 */}
            <div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
            {loading && !tasks.length ? (
              <div style={{ textAlign: 'center', padding: 48 }}>
                <Spin />
              </div>
            ) : !tasks.length ? (
              <Empty description="暂无生成记录，先在左侧提交一个任务吧" />
            ) : (
              <Space direction="vertical" style={{ width: '100%' }} size={16}>
                {tasks.map((task) => (
                    <Card
                      key={task.id}
                      size="small"
                      onMouseEnter={() => setHoverId(task.id)}
                      onMouseLeave={() => setHoverId(null)}
                      style={{
                        background: hoverId === task.id ? token.colorFillQuaternary : undefined,
                        border: activeHistoryId === task.id ? `1px solid ${token.colorPrimary}` : undefined,
                      }}
                      title={
                        <Space wrap>
                          <Badge
                            status={
                              task.status === '已完成'
                                ? 'success'
                                : task.status === '失败'
                                  ? 'error'
                                  : task.status === '部分完成'
                                    ? 'warning'
                                    : task.status === '已取消'
                                      ? 'default'
                                      : 'processing'
                            }
                            text={<Text strong>{task.title || `任务 #${task.id}`}</Text>}
                          />
                          <Tag color={STATUS_COLORS[task.status] || 'default'}>
                            {task.status}
                          </Tag>
                          <Tag color={task.provider === 'relay' ? 'gold' : 'default'}>
                            {task.provider_label
                              || (task.provider === 'relay' ? 'Nano Banana 2' : 'Qwen Image 2.1')}
                          </Tag>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {task.aplus_type || task.main_types?.length
                              ? ''
                              : `${task.aspect_ratio} · `}
                            {task.image_count}/类 · {task.created_at}
                            {task.creator_name ? ` · ${task.creator_name}` : ''}
                          </Text>
                        </Space>
                      }
                      extra={
                        <Space size={4}>
                          {(task.status === '排队中' || task.status === '生成中') && (
                            <Tooltip title="取消任务（已完成的图保留）">
                              <Button
                                size="small"
                                danger
                                icon={<StopOutlined />}
                                onClick={() => handleCancelTask(task.id)}
                              >
                                取消
                              </Button>
                            </Tooltip>
                          )}
                          {hasRetryable(task) && (
                            <Tooltip title="重新生成失败或已取消的图片（复用已上传参考图，无需重传）">
                              <Button
                                size="small"
                                icon={<RedoOutlined />}
                                onClick={() => handleRetry(task.id)}
                              >
                                重试未完成
                              </Button>
                            </Tooltip>
                          )}
                          {(task.result_images || []).some(
                            (i) => i.status === '已完成' && i.url,
                          ) && (
                            <Tooltip title="打包下载全部图片（zip）">
                              <Button
                                size="small"
                                icon={<DownloadOutlined />}
                                onClick={() => handleDownloadAll(task)}
                              />
                            </Tooltip>
                          )}
                          <Tooltip title="编辑：已填入该历史的参考图、提示词与参数">
                            <Button
                              size="small"
                              icon={<FormOutlined />}
                              onClick={() => handleRestore(task)}
                            >
                              编辑
                            </Button>
                          </Tooltip>
                          <Popconfirm
                            title="确定删除该记录？"
                            onConfirm={() => handleDelete(task.id)}
                          >
                            <Button size="small" danger icon={<DeleteOutlined />} />
                          </Popconfirm>
                        </Space>
                      }
                    >
                      <div style={{ display: 'flex', gap: 10 }}>
                        {/* 左：原始参考图缩略条（前 3 张，点击放大预览） */}
                        {(task.images || []).length > 0 && (
                          <Image.PreviewGroup>
                            <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
                              {task.images.slice(0, 3).map((url, i) => (
                                <Image
                                  key={`${task.id}-ref-${i}`}
                                  alt={`参考图${i + 1}`}
                                  src={url}
                                  width={44}
                                  height={44}
                                  style={{ objectFit: 'cover', borderRadius: 4, border: `1px solid ${token.colorBorderSecondary}`, cursor: 'zoom-in' }}
                                  preview={{ mask: false }}
                                />
                              ))}
                            </div>
                          </Image.PreviewGroup>
                        )}
                        {/* 右：标题下三行信息（类型 / 状态或耗时 / 参数与摘要） */}
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <Space wrap size={4}>
                            {renderTypeTags(task)}
                          </Space>
                          {task.status === '排队中' || task.status === '生成中' ? (
                            <div style={{ marginTop: 2 }}>
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                {task.status} ·{' '}
                                {(task.result_images || []).filter((i) => i.status === '已完成').length}
                                /{(task.result_images || []).length} 已完成…
                              </Text>
                              {/* 排队排位：ComfyUI 与超分/视频共用串行队列，不给提示会像卡死 */}
                              {task.queue?.hint && (
                                <div style={{ marginTop: 2 }}>
                                  <Text type="secondary" style={{ fontSize: 11 }}>
                                    <ClockCircleOutlined style={{ marginRight: 4 }} />
                                    {task.queue.hint}
                                  </Text>
                                </div>
                              )}
                            </div>
                          ) : task.status === '失败' ? (
                            <div style={{ marginTop: 2 }}>
                              <Tag color="error" style={{ marginRight: 0 }}>失败</Tag>
                              {task.error_message && (
                                <Text type="danger" style={{ fontSize: 12 }}>{task.error_message}</Text>
                              )}
                            </div>
                          ) : task.cost_seconds != null ? (
                            <div style={{ marginTop: 2 }}>
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                耗时 {formatCost(task.cost_seconds)}
                              </Text>
                            </div>
                          ) : null}
                          <Tooltip
                            title={
                              (task.product_info || task.prompt)
                                ? `${task.product_info ? `商品信息：${task.product_info}` : ''}${task.prompt ? `${task.product_info ? '\n' : ''}附加要求：${task.prompt}` : ''}`
                                : undefined
                            }
                            styles={{
                              root: { maxWidth: 560 },
                              body: {
                                maxWidth: 560,
                                width: 560,
                                maxHeight: 380,
                                overflowY: 'auto',
                                whiteSpace: 'pre-wrap',
                                fontSize: 12,
                                lineHeight: 1.7,
                              },
                            }}
                          >
                            <Text
                              type="secondary"
                              style={{
                                fontSize: 12,
                                display: 'block',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                              }}
                            >
                              {task.created_at}
                              {task.creator_name ? ` · ${task.creator_name}` : ''}
                              {task.brand_settings?.country ? ` · ${task.brand_settings.country}` : ''}
                              {task.brand_settings?.language ? ` · ${task.brand_settings.language}` : ''}
                              {task.brand_settings?.platform ? ` · ${task.brand_settings.platform}` : ''}
                              {` · ${task.main_aspect_ratio || task.aspect_ratio || ''}`}
                              {Array.isArray(task.main_types) && task.main_types.length
                                ? ` · ${task.main_types.length} 类 ${Object.values(task.main_types).reduce((a: number, b: any) => a + Number(b || 1), 0)} 张`
                                : task.main_types && typeof task.main_types === 'object'
                                  ? ` · ${Object.keys(task.main_types).length} 类 ${Object.values(task.main_types).reduce((a: number, b: any) => a + Number(b || 1), 0)} 张`
                                  : ''}
                              {task.aplus_type ? ` · A+×${task.image_count}` : ''}
                              {task.product_info ? ` · ${task.product_info}` : ''}
                            </Text>
                          </Tooltip>
                        </div>
                      </div>
                      {/* 结果大图网格：逐张 label+seq+下载，点击图片开单张详情 */}
                      {(task.result_images || []).length > 0 && (
                        <div
                          style={{
                            marginTop: 10,
                            display: 'grid',
                            gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
                            gap: 12,
                          }}
                        >
                          {(task.result_images || []).map((item, idx) =>
                            renderResultItem(item, task, idx),
                          )}
                        </div>
                      )}
                    </Card>
                  ))}
              </Space>
            )}
            </div>
            {total > pageSize && (
              <div style={{ flexShrink: 0, marginTop: 8, display: 'flex', justifyContent: 'flex-end' }}>
                <Pagination
                  size="small"
                  current={page}
                  pageSize={pageSize}
                  total={total}
                  onChange={(p: number) => setPage(p)}
                  showSizeChanger={false}
                />
              </div>
            )}
          </Card>
        </Col>
      </Row>

      {/* ===== 从产品管理选择图片 ===== */}
      <Modal
        title="从产品管理选择图片"
        open={productModalOpen}
        wrapClassName="ai-thin-scrollbar"
        onCancel={() => setProductModalOpen(false)}
        width={860}
        footer={[
          <Button key="cancel" onClick={() => setProductModalOpen(false)}>
            取消
          </Button>,
          <Button
            key="ok"
            type="primary"
            disabled={selectedImageUrls.length === 0}
            onClick={handleConfirmProductImages}
          >
            确认{selectedImageUrls.length > 0 ? `（已选 ${selectedImageUrls.length} 张）` : ''}
          </Button>,
        ]}
      >
        <Input.Search
          placeholder="搜索产品编码 / 品名"
          allowClear
          enterButton
          value={productSearchText}
          onChange={(e) => setProductSearchText(e.target.value)}
          onSearch={(v) => {
            setProductKeyword(v)
            fetchProductOptions(1, v)
          }}
          style={{ marginBottom: 12 }}
        />
        {productLoading ? (
          <div style={{ textAlign: 'center', padding: '40px 0' }}>
            <Spin />
          </div>
        ) : productOptions.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未找到相关产品" />
        ) : (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: '52vh', overflowY: 'auto' }}>
              {productOptions.map((prod) => (
                <div key={prod.id} style={{ border: `1px solid ${token.colorBorderSecondary}`, borderRadius: 8, padding: 12 }}>
                  <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                    <Text strong style={{ fontSize: 13, flexShrink: 0 }}>{prod.product_code}</Text>
                    <Text style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {prod.name}
                    </Text>
                  </div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {prod.images.length === 0 ? (
                      <Text type="secondary" style={{ fontSize: 12 }}>该产品暂无图片</Text>
                    ) : (
                      prod.images.map((url) => {
                        const checked = selectedImageUrls.includes(url)
                        return (
                          <div
                            key={url}
                            onClick={() => toggleProductImage(url)}
                            style={{
                              position: 'relative',
                              width: 64,
                              height: 64,
                              cursor: 'pointer',
                              border: `2px solid ${checked ? token.colorPrimary : token.colorBorderSecondary}`,
                              borderRadius: 6,
                              overflow: 'hidden',
                            }}
                          >
                            <img alt="" src={url} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                            {checked && (
                              <div
                                style={{
                                  position: 'absolute',
                                  top: 0,
                                  right: 0,
                                  width: 18,
                                  height: 18,
                                  background: token.colorPrimary,
                                  color: '#fff',
                                  fontSize: 11,
                                  display: 'flex',
                                  alignItems: 'center',
                                  justifyContent: 'center',
                                  borderBottomLeftRadius: 6,
                                }}
                              >
                                ✓
                              </div>
                            )}
                          </div>
                        )
                      })
                    )}
                  </div>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
              <Pagination
                size="small"
                current={productPage}
                total={productTotal}
                pageSize={PRODUCT_PAGE_SIZE}
                onChange={(pg: number) => fetchProductOptions(pg, productKeyword)}
              />
            </div>
          </>
        )}
      </Modal>
      {/* ===== 结果图单张详情（对齐 AI 生视频详情：左大图 + 右参数面板）===== */}
      <Modal
        title={
          resultDetail
            ? `${resultDetail.task.title || `任务 #${resultDetail.task.id}`}（${resultDetail.index + 1}/${(resultDetail.task.result_images || []).length}）`
            : ''
        }
        open={!!resultDetail}
        wrapClassName="ai-thin-scrollbar"
        onCancel={() => setResultDetail(null)}
        footer={null}
        width="90vw"
        centered
        destroyOnHidden
        styles={{ body: { padding: 0 } }}
      >
        {resultDetail &&
          (() => {
            const { task, index } = resultDetail
            const item: AIImageResultItem | undefined = (task.result_images || [])[index]
            if (!item) return null
            const name = `${task.title || '套图'}-${item.label}${item.seq > 0 ? `-${item.seq + 1}` : ''}`
            const promptId = item.prompt_id || task.comfy_prompt_ids?.[index] || ''
            const isRelay = (task.provider || 'local') === 'relay'
            const sizeText =
              Array.isArray(item.target_size) && item.target_size.length === 2
                ? `${item.target_size[0]}×${item.target_size[1]}`
                : ''
            return (
              <div style={{ display: 'flex', height: '78vh' }}>
                {/* 左：结果大图（黑底 contain，对齐生视频播放区） */}
                <div
                  style={{
                    flex: 1,
                    minWidth: 0,
                    background: '#000',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    position: 'relative',
                    padding: 16,
                  }}
                >
                  {item.status === '已完成' && item.url ? (
                    <img
                      src={item.url}
                      alt={name}
                      style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
                    />
                  ) : item.status === '失败' ? (
                    <div style={{ textAlign: 'center' }}>
                      <Tag color="error">生成失败</Tag>
                      {item.error && (
                        <Text
                          type="secondary"
                          style={{ display: 'block', marginTop: 8, padding: '0 24px', wordBreak: 'break-all' }}
                        >
                          {item.error}
                        </Text>
                      )}
                    </div>
                  ) : item.status === '已取消' ? (
                    <div style={{ textAlign: 'center' }}>
                      <Tag>已取消</Tag>
                      <Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
                        点击卡片上的「重试」可重新生成这张
                      </Text>
                    </div>
                  ) : (
                    <div style={{ textAlign: 'center' }}>
                      <Spin />
                      <Tag style={{ marginLeft: 8 }}>{item.status}</Tag>
                    </div>
                  )}
                  {item.status === '已完成' && item.url && (
                    <Button
                      icon={<DownloadOutlined />}
                      style={{ position: 'absolute', left: 16, bottom: 16 }}
                      onClick={() => handleDownload(item.url!, `${name}.${fileExt(item.url)}`)}
                    >
                      下载
                    </Button>
                  )}
                  {item.status === '已完成' && item.url && (
                    <Button
                      icon={<LinkOutlined />}
                      style={{ position: 'absolute', left: 108, bottom: 16 }}
                      onClick={() => setBindTarget({ url: item.url!, title: task.title })}
                    >
                      绑定到产品
                    </Button>
                  )}
                </div>
                {/* 右：参数面板（字段与提示词双源，对齐生视频详情） */}
                <div
                  style={{
                    width: 360,
                    flexShrink: 0,
                    overflowY: 'auto',
                    padding: '20px 24px',
                    borderLeft: `1px solid ${token.colorBorderSecondary}`,
                    background: token.colorBgContainer,
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      marginBottom: 16,
                    }}
                  >
                    {promptId && h3BaseUrl ? (
                      <Button
                        size="small"
                        type="link"
                        style={{ padding: 0 }}
                        href={`${h3BaseUrl}/history/${promptId}`}
                        target="_blank"
                        title="ComfyUI 执行历史会被定期清理，链接可能已失效；提示词已存本地"
                      >
                        在 ComfyUI 中查看
                      </Button>
                    ) : (
                      <span />
                    )}
                    <Space size={4}>
                      <Button
                        size="small"
                        icon={<LeftOutlined />}
                        disabled={index <= 0}
                        onClick={() => setResultDetail({ task, index: index - 1 })}
                      />
                      <Button
                        size="small"
                        icon={<RightOutlined />}
                        disabled={index >= (task.result_images || []).length - 1}
                        onClick={() => setResultDetail({ task, index: index + 1 })}
                      />
                    </Space>
                  </div>

                  {[
                    { label: '任务ID：', value: promptId || `#${task.id}` },
                    {
                      label: '生成通道：',
                      value:
                        task.provider_label ||
                        (isRelay ? 'Nano Banana 2（中转站）' : 'Qwen Image 2.1（本地）'),
                    },
                    { label: '生成状态：', value: item.status },
                    { label: '生成者：', value: task.creator_name || '-' },
                    { label: '生成类型：', value: item.label || '-' },
                    ...(item.layout?.name
                      ? [{ label: '版式：', value: `${item.layout.name}${item.layout.source === 'upload' ? '（上传）' : ''}` }]
                      : []),
                    {
                      label: '生成比例：',
                      value: sizeText || task.main_aspect_ratio || task.aspect_ratio || '-',
                    },
                    { label: '销售国家/地区：', value: task.brand_settings?.country || '未设置' },
                    { label: '生成图片语言：', value: task.brand_settings?.language || '未设置' },
                    { label: '发布平台：', value: task.brand_settings?.platform || '未设置' },
                    ...(task.aplus_type
                      ? [{
                          label: '画面文字：',
                          value: task.brand_settings?.text_mode === 'with_text'
                            ? '允许 AI 排版文案（需复核拼写）'
                            : '无文字（后期设计工具叠加）',
                        }]
                      : []),
                    { label: '商品信息：', value: task.product_info || '-' },
                  ].map((row) => (
                    <div key={row.label} style={{ marginBottom: 16 }}>
                      <Text strong style={{ fontSize: 13, display: 'block' }}>
                        {row.label}
                      </Text>
                      <Text style={{ fontSize: 13, wordBreak: 'break-all', whiteSpace: 'pre-wrap' }}>
                        {row.value}
                      </Text>
                    </div>
                  ))}

                  {(task.images || []).length > 0 && (
                    <div style={{ marginBottom: 16 }}>
                      <Text strong style={{ fontSize: 13, display: 'block' }}>原始参考图：</Text>
                      <Image.PreviewGroup>
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
                          {task.images.map((url, i) => (
                            <Image
                              key={`ref-${task.id}-${i}`}
                              alt={`参考图${i + 1}`}
                              src={url}
                              width={40}
                              height={40}
                              style={{ objectFit: 'cover', borderRadius: 4, border: `1px solid ${token.colorBorderSecondary}` }}
                              preview={{ mask: false }}
                            />
                          ))}
                        </div>
                      </Image.PreviewGroup>
                    </div>
                  )}

                  {(item.inventory || []).length > 0 && (
                    <div style={{ marginBottom: 16 }}>
                      <Space size={6}>
                        <Text strong style={{ fontSize: 13 }}>件数清单：</Text>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          AI 从参考图清点，出图须与之逐项一致
                        </Text>
                      </Space>
                      <div style={{ marginTop: 6, fontSize: 12, lineHeight: 1.8 }}>
                        {(item.inventory || []).map((x, i) => (
                          <div key={`inv-${i}`} style={{ color: token.colorTextSecondary }}>
                            · {x}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  <div style={{ marginBottom: 16 }}>
                    <Text strong style={{ fontSize: 13, display: 'block' }}>
                      {isRelay ? '提示词（实际提交 · 英文）：' : '提示词描述（原始）：'}
                    </Text>
                    {isRelay && (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        视觉模型按该产品与图类型即时设计，无本地 PE 二次优化
                      </Text>
                    )}
                    <div
                      style={{
                        marginTop: 6,
                        fontSize: 12,
                        lineHeight: 1.7,
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-all',
                        maxHeight: '26vh',
                        overflowY: 'auto',
                      }}
                    >
                      {comfyLoading ? (
                        <Spin size="small" />
                      ) : (
                        comfyPrompt?.original ||
                        task.prompt ||
                        (isRelay
                          ? '—（该记录未保存提示词，仅早期任务会这样）'
                          : '—（该记录未保存提示词；ComfyUI 历史已过期后无法再取回）')
                      )}
                    </div>
                  </div>

                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <Space size={6}>
                        <Text strong style={{ fontSize: 13 }}>
                          {isRelay ? '中文对照（仅供阅读）：' : 'Qwen 提示词（优化后）：'}
                        </Text>
                        {isRelay && item.prompt_cn && (
                          <Tag color={item.prompt_designed ? 'purple' : 'default'}>
                            {item.prompt_designed ? 'AI 定制' : '模板回退'}
                          </Tag>
                        )}
                      </Space>
                      {(isRelay ? item.prompt_cn : comfyPrompt?.enhanced) && (
                        <Button
                          type="link"
                          size="small"
                          style={{ padding: 0 }}
                          onClick={() =>
                            handleCopyText(
                              (isRelay ? item.prompt_cn : comfyPrompt?.enhanced) || '',
                            )
                          }
                        >
                          复制
                        </Button>
                      )}
                    </div>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {isRelay
                        ? '中文翻译仅供参考，不参与出图；实际提交的是上方英文提示词'
                        : '本地官方 PE 按原始提示词二次优化，实际提交给 Qwen Image 2.1'}
                    </Text>
                    <div
                      style={{
                        marginTop: 6,
                        fontSize: 12,
                        lineHeight: 1.7,
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        maxHeight: '30vh',
                        overflowY: 'auto',
                        background: token.colorFillQuaternary,
                        border: `1px solid ${token.colorBorderSecondary}`,
                        borderRadius: 4,
                        padding: 8,
                      }}
                    >
                      {isRelay ? (
                        item.prompt_cn || '—（该记录未保存中文对照，早期任务会这样）'
                      ) : comfyLoading ? (
                        <Spin size="small" />
                      ) : (
                        comfyPrompt?.enhanced || '-'
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )
          })()}
      </Modal>

      {/* 绑定至产品：复用生视频的审核流程，影像类型为 image（审核通过后写入产品 images） */}
      <BindProductModal
        open={!!bindTarget}
        mediaUrl={bindTarget?.url}
        mediaType="image"
        source="ai_image"
        taskTitle={bindTarget?.title}
        onClose={() => setBindTarget(null)}
      />

      <AIImageLayoutPicker
        open={layoutOpen}
        value={layouts.map((l) => l.key)}
        limit={maxLayouts}
        onClose={() => setLayoutOpen(false)}
        onConfirm={(_keys, items) => setLayouts(items)}
      />
    </div>
    </>
  )
}

export default AIImageStudio
