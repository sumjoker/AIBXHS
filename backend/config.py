from pydantic_settings import BaseSettings
from functools import lru_cache
import urllib.parse
import os


class Settings(BaseSettings):
    """应用配置"""
    PORT: int = 8000
    
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = "Root@123456"
    DB_NAME: str = "baoxinhuasheng"

    # Demo 模式配置
    DEMO_MODE: bool = False
    DEMO_DB_HOST: str = "localhost"
    DEMO_DB_PORT: int = 3306
    DEMO_DB_USER: str = "root"
    DEMO_DB_PASSWORD: str = "123456"
    DEMO_DB_NAME: str = "bxhs_ai_assistance_demo"
    
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_INVENTORY_BASE_TOKEN: str = ""
    FEISHU_INVENTORY_TABLE_ID: str = ""
    FEISHU_REVIEW_BASE_TOKEN: str = ""
    FEISHU_REVIEW_TABLE_ID: str = ""
    # 开发机器人：新品多维表（回传/美工图片附件）
    FEISHU_DESIGN_BASE_TOKEN: str = ""
    FEISHU_DESIGN_TABLE_ID: str = ""
    # 飞书多维表同步服务（独立应用 + 数据表）
    FEISHU_SYNC_APP_ID: str = ""
    FEISHU_SYNC_APP_SECRET: str = ""
    FEISHU_SYNC_BASE_TOKEN: str = ""
    FEISHU_SYNC_TABLE_ID: str = ""
    # 飞书多维表 - 海外仓库存表（复用 FBA 同步应用的 APP_ID/APP_SECRET，仅需另配表 ID）
    FEISHU_OVERSEAS_BASE_TOKEN: str = ""
    FEISHU_OVERSEAS_TABLE_ID: str = ""
    # 海外仓库存每日自动同步时刻（小时，默认 8 点；与 FBA 同步共用后台任务与状态机）
    OVERSEAS_SYNC_HOUR: int = 8
    # 飞书多维表 - AI视频首绑SKU上传表（产品首条视频绑定后可选直传SKU）
    FEISHU_VIDEO_BASE_TOKEN: str = ""
    FEISHU_VIDEO_TABLE_ID: str = ""
    # 填报人（人员字段）飞书 open_id，留空自动尝试取应用机器人自身身份
    FEISHU_VIDEO_REPORTER_OPEN_ID: str = ""
    # 紫鸟启动名→紫鸟登录账号映射（JSON 字符串，按顺序取登录账号命中的第一个启动名）
    ZINIAO_ACCOUNT_MAP: str = ""
    # 飞书 webhook - 邮件机器人自动化
    FEISHU_EMAIL_WEBHOOK_URL: str = ""
    # 飞书 webhook - 产品页信息自动化
    FEISHU_PAGE_INFO_WEBHOOK_URL: str = ""
    # 飞书 webhook - 开广告提交地址
    FEISHU_AD_WEBHOOK_URL: str = ""
    # 美工图片 TOS 自动迁移开关（false 关闭启动时自动迁移）
    DESIGNER_IMAGE_AUTO_MIGRATE: str = "true"
    # 六面图 TOS 自动迁移开关（false 关闭启动时自动迁移）
    SIXFACE_IMAGE_AUTO_MIGRATE: str = "true"
    # 飞书 webhook - 编辑基础信息提交地址
    FEISHU_EDIT_WEBHOOK_URL: str = ""
    # 飞书 webhook - 一键优化提交地址
    FEISHU_OPTIMIZE_WEBHOOK_URL: str = ""
    # 飞书 webhook - 邮件回复通知地址
    FEISHU_EMAIL_WEBHOOK_URL: str = ""
    
    OPENAI_API_KEY: str = ""
    OPENAI_API_BASE: str = "https://yunwu.ai/v1"
    OPENAI_MODEL: str = "deepseek-v4-flash"
    # AI 调用参数（可按需在 .env 调整）
    OPENAI_TEMPERATURE: float = 0.7
    OPENAI_TIMEOUT: int = 60
    OPENAI_VISION_MODEL: str = ""
    OPENAI_VISION_RESPONSE_FORMAT: str = ""
    # 图片代理白名单追加项（逗号分隔，后缀匹配）。默认已含 volces.com / bxhsrpa.com /
    # media-amazon.com，换 CDN 或新增图源时在此追加，无需改代码
    PROXY_IMAGE_HOST_SUFFIXES: str = ""

    # MiniMax H3 本地视频生成（ComfyUI API，需 Basic Auth 账号密码）
    
    H3_BASE_URL: str = ""
    H3_AUTH_USER: str = ""
    H3_AUTH_PASSWORD: str = ""
    # 工作流模板默认取工程内文件（跨平台部署可用），需要时用环境变量 H3_WORKFLOW_PATH 覆盖
    H3_WORKFLOW_PATH: str = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "workflows", "h3_workflow_template.json"
    )
    H3_POLL_INTERVAL: int = 15          # 轮询任务状态间隔（秒）
    H3_MAX_WAIT_MINUTES: int = 240      # 单条视频最长等待时间（分钟）

    # 视频超分（Topaz 星光 2.6）工作流模板，与 H3 共用同一台 ComfyUI
    UPSCALE_WORKFLOW_PATH: str = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "workflows", "upscale_workflow_template.json"
    )

    # H3 提示词二次优化：按官方 h3-prompt-writing 指南把中文方案改写为 Ref2VA 六段式英文提示词
    H3_PROMPT_OPTIMIZE: bool = True     # 关闭后直接提交原始方案文本
    H3_PROMPT_MODEL: str = ""           # 优化用文本模型，留空则使用 OPENAI_MODEL
    H3_PROMPT_GUIDE_DIR: str = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "prompts"
    )

    # AI 商品套图（Qwen-Image-2.1 图生图，与 H3 共用同一台 ComfyUI，Basic Auth 复用 H3_*）
    IMAGE_WORKFLOW_PATH: str = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "workflows", "qwen_image_workflow.json"
    )
    IMAGE_PROMPT_TEMPLATES_PATH: str = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "prompts", "image_prompt_templates.json"
    )
    # A+ 提示词通道实验（2026-10-29）：空=走现有链路（PE→497）；
    # direct=A+ 专用工作流（497 直连 571，跳过 PE）；
    # append=A+ 专用工作流（PE 输出后再强制拼接版式契约节点 572，PE 改写不到）。
    # 只影响本地 ComfyUI 的 A+ 子任务；主副图与中转站通道不受影响。
    IMAGE_APLUS_PROMPT_MODE: str = ""
    # 品牌A+横幅（970×300）专用工作流：自定义 latent 约 3.2:1，参考图只进 PE、
    # 不进入 497 图像条件（画面只出现品牌名，不出现商品）。
    IMAGE_BRAND_BANNER_WORKFLOW_PATH: str = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "workflows", "qwen_image_workflow_banner.json"
    )
    # A+ 版式预设库（内置「官方推荐」；用户上传/收藏落 ai_image_layouts 表）
    IMAGE_LAYOUT_PRESETS_PATH: str = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "prompts", "aplus_layouts.json"
    )
    IMAGE_MAX_LAYOUTS_PER_TASK: int = 8  # 单任务最多选几个版式（每个版式生成一张，对齐官方交互）
    IMAGE_POLL_INTERVAL: int = 2        # 轮询生成进度间隔（秒）：首轮立即查，之后按此间隔
    IMAGE_MAX_WAIT_MINUTES: int = 30    # 单任务最长等待时间（分钟），PE 首载 GGUF 可能 1~3 分钟
    IMAGE_MAX_CONCURRENT_TASKS: int = 2 # 单实例同时轮询的最大任务数（ComfyUI 串行队列，与生视频互让）
    # 提交背压：ComfyUI 队列（running+pending）超过该深度时先等位，避免多用户并发把队列灌满
    IMAGE_MAX_QUEUE_DEPTH: int = 8
    IMAGE_QUEUE_WAIT_SECONDS: int = 60   # 单张提交前最长等待队列回落的秒数（12 张累计不超过整单超时）
    # 生图优先于超分/视频入队（ComfyUI 无优先级接口，只能在入队前让路）：
    # false = 完全退回 FIFO；等待上限保证视频不会被生图无限期饿死。
    COMFY_IMAGE_PRIORITY: bool = True
    COMFY_IMAGE_PRIORITY_WAIT_SECONDS: int = 180

    # 生图中转站：Nano Banana 2（gemini-3.1-flash-image，OpenAI 兼容 /chat/completions）
    IMAGE_RELAY_API_BASE: str = ""
    IMAGE_RELAY_API_KEY: str = ""
    IMAGE_RELAY_MODEL: str = "gemini-3.1-flash-image"
    IMAGE_RELAY_LABEL: str = "Nano Banana 2"
    IMAGE_RELAY_CONCURRENCY: int = 4    # 并行生成数（中转站无排队）
    IMAGE_RELAY_TIMEOUT: int = 240
    # 提示词设计单次调用的超时（输出较长，但 240s 会拖垮整单；失败会自动重试一次再降级模板）
    IMAGE_RELAY_DESIGN_TIMEOUT: int = 180
    # 设计输出的 token 上限。1600 在多组件商品（气球套装、餐具套装）上会截断：
    # JSON 不完整 → 解析失败 → 静默降级模板。重试时会自动放大到 1.5 倍。
    IMAGE_RELAY_DESIGN_MAX_TOKENS: int = 2600
    # ===== 本地 ComfyUI 提示词硬化（2026-10-26）=====
    # True = 用 prompts/image_prompt_templates.json（约束写死版，业务红线交给 PE 保留）
    # False = 回退 v3 轻量版 prompts/image_prompt_templates_v3.json（画面语言完全交给 PE）
    IMAGE_LOCAL_HARDENED_PROMPT: bool = True
    # PE 节点输出语言：本地链路由 PE 二次优化，中文全链路一致（与已验证的工作流一致）
    IMAGE_LOCAL_PE_LANGUAGE: str = "中文"
    # PE 生成后是否卸载模型：False 常驻可省每次冷加载几秒，代价是占显存
    IMAGE_LOCAL_PE_UNLOAD: bool = True
    IMAGE_RELAY_ANALYZE: bool = True    # 生成前是否先看图补充商品事实（同一张图只分析一次）
    # 看图分析模型：留空 = 与生视频（AI 创作中心）完全一致，
    # 走 OPENAI_API_BASE / OPENAI_API_KEY / OPENAI_VISION_MODEL 这一套通道。
    # （出图模型 *-image 做纯文本分析会超时，所以分析与出图必须分开两个模型）
    IMAGE_RELAY_ANALYZE_MODEL: str = ""

    # 火山引擎 TOS 对象存储配置
    # 获取方式：
    # 1. AK/SK: 在火山引擎控制台「访问密钥」中创建
    #    https://console.volcengine.com/iam/keymanage/
    # 2. Endpoint/Region/Bucket: 在 TOS 控制台创建桶后获取
    #    https://console.volcengine.com/tos
    TOS_ACCESS_KEY: str = ""
    TOS_SECRET_KEY: str = ""
    TOS_ENDPOINT: str = ""            # 如: tos-cn-beijing.volces.com
    TOS_REGION: str = ""              # 如: cn-beijing
    TOS_BUCKET: str = ""              # 桶名称
    TOS_PREFIX: str = "uploads/"     # 对象前缀（目录）
    # 桶访问域名（用于拼接公网访问URL，若为空则使用 https://{bucket}.{endpoint} 形式）
    TOS_CUSTOM_DOMAIN: str = ""

    # JWT配置
    SECRET_KEY: str = "your-secret-key-change-this-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

    # 星拓 MCP Server（标签系统 R2 数据源；密钥资产，不入代码库）
    STARTOP_MCP_URL: str = "https://openmcp.startop.com/mcp-servers/startop-mcp"
    STARTOP_MCP_KEY: str = ""         # X-Mcp-Key，星拓后台申请
    STARTOP_PULL_HOUR: int = 2        # 每日定时拉取时刻（Q-3 裁决：每日定时独立拉取，可配置）
    STARTOP_PAGE_SIZE: int = 100      # 搜索词报表分页大小
    
    @property
    def DATABASE_URL(self) -> str:
        """构建数据库连接URL，DEMO_MODE=true 时连接本地 Demo 库"""
        if self.DEMO_MODE:
            host = self.DEMO_DB_HOST
            port = self.DEMO_DB_PORT
            user = self.DEMO_DB_USER
            pwd = self.DEMO_DB_PASSWORD
            db = self.DEMO_DB_NAME
        else:
            host = self.DB_HOST
            port = self.DB_PORT
            user = self.DB_USER
            pwd = self.DB_PASSWORD
            db = self.DB_NAME
        encoded_password = urllib.parse.quote_plus(pwd)
        return f"mysql+pymysql://{user}:{encoded_password}@{host}:{port}/{db}?charset=utf8mb4"
    
    # extra="ignore"：.env 中存在一些由其他模块直接 os.getenv 读取的键
    # （image_relay_* / feishu_sync_* / ziniao_account_map 等），
    # 它们不在本 Settings 中定义。pydantic-settings v2 的 BaseSettings
    # 默认 extra="forbid"，一旦 .env 新增这类键就会在启动时抛
    # ValidationError（13 项 extra_forbidden）导致服务起不来。
    model_config = {
        "env_file": [
            os.path.join(os.path.dirname(__file__), ".env"),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        ],
        # 必须用 utf-8-sig：.env 被记事本/Excel 编辑后常带 UTF-8 BOM，
        # 用 "utf-8" 读会把首行键名变成 "\ufeffPORT"，导致该键静默失效
        # 回落到默认值（2026-11-03 实测：PORT=8002 读不到，后端起在 8000，
        # 前端 dev 代理指向 8002 → 登录全部失败）。utf-8-sig 对无 BOM 文件同样适用。
        "env_file_encoding": "utf-8-sig",
        "extra": "ignore",
    }


def get_settings() -> Settings:
    return Settings()
