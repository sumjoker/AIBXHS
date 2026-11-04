"""火山引擎 TOS 对象存储服务

封装 TOS 文件上传、删除等操作。配置项见 backend/.env（TOS_ACCESS_KEY 等）。
"""
import logging
import os
import uuid
from datetime import datetime
from typing import Optional

from config import get_settings

logger = logging.getLogger(__name__)

# 全局缓存 TOS 客户端实例（按需懒加载）
_tos_client = None


def _get_tos_client():
    """获取 TOS 客户端实例（懒加载，全局复用）"""
    global _tos_client
    if _tos_client is not None:
        return _tos_client

    settings = get_settings()
    if not all([settings.TOS_ACCESS_KEY, settings.TOS_SECRET_KEY,
                settings.TOS_ENDPOINT, settings.TOS_REGION, settings.TOS_BUCKET]):
        raise RuntimeError(
            "TOS 配置不完整，请在 backend/.env 中填写 TOS_ACCESS_KEY、TOS_SECRET_KEY、"
            "TOS_ENDPOINT、TOS_REGION、TOS_BUCKET"
        )

    try:
        import tos
    except ImportError:
        raise RuntimeError(
            "未安装 tos SDK，请运行: pip install tos==2.9.2"
        )

    _tos_client = tos.TosClientV2(
        settings.TOS_ACCESS_KEY,
        settings.TOS_SECRET_KEY,
        settings.TOS_ENDPOINT,
        settings.TOS_REGION,
        dns_cache_time=0,  # 关闭 SDK 的全局 DNS 缓存 hook：默认会替换 urllib3 的 create_connection，
                           # 导致进程内所有 requests 连接（如 H3 轮询）每 15 秒打一行 tos INFO 日志
    )
    logger.info("TOS 客户端已初始化: bucket=%s, endpoint=%s",
                settings.TOS_BUCKET, settings.TOS_ENDPOINT)
    return _tos_client


def _build_object_key(file_name: str, subdir: str = "", custom_name: str = "") -> str:
    """生成 TOS 中的对象 key。

    :param custom_name: 自定义文件名（不含扩展名），如产品编码。
                        若提供则使用 {custom_name}{ext}，否则使用 UUID。
    """
    settings = get_settings()
    prefix = settings.TOS_PREFIX or ""
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    if subdir:
        subdir = subdir.strip("/") + "/"

    # 保留原扩展名
    _, ext = os.path.splitext(file_name)
    ext = ext.lower()
    if not ext:
        ext = ".bin"

    if custom_name:
        # 自定义名：用 custom_name 替代 UUID
        object_name = f"{custom_name}{ext}"
    else:
        object_name = f"{uuid.uuid4().hex}{ext}"
    return f"{prefix}{subdir}{object_name}"


def _build_public_url(object_key: str) -> str:
    """根据对象 key 拼接公网访问 URL"""
    settings = get_settings()
    if settings.TOS_CUSTOM_DOMAIN:
        domain = settings.TOS_CUSTOM_DOMAIN.strip("/")
        return f"https://{domain}/{object_key}"
    # 默认形式: https://{bucket}.{endpoint}/{key}
    bucket = settings.TOS_BUCKET
    endpoint = settings.TOS_ENDPOINT.strip("/")
    return f"https://{bucket}.{endpoint}/{object_key}"


def build_thumbnail_url(url: str, width: int = 128) -> str:
    """为 TOS 图片 URL 追加缩略图处理参数（x-tos-process=img/resize），非本桶 URL 原样返回。

    :param url: 原图公网 URL
    :param width: 缩略图宽度（像素）
    :return: 缩略图 URL；Bucket 未开通图片处理能力时该 URL 会加载失败，调用方需回退原图
    """
    if not url or width <= 0:
        return url or ""
    settings = get_settings()
    bucket = (settings.TOS_BUCKET or "").strip().lower()
    endpoint = (settings.TOS_ENDPOINT or "").strip("/").lower()
    custom_domain = (settings.TOS_CUSTOM_DOMAIN or "").strip("/").lower()
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except Exception:
        return url
    tos_hosts = set()
    if bucket and endpoint:
        tos_hosts.add(f"{bucket}.{endpoint}")
    if custom_domain:
        tos_hosts.add(custom_domain)
    if not host or host not in tos_hosts:
        return url
    sep = "&" if parsed.query else "?"
    return f"{url}{sep}x-tos-process=img/resize,w_{width}"


def upload_file(file_bytes: bytes, file_name: str,
                content_type: Optional[str] = None,
                subdir: str = "",
                custom_name: str = "") -> str:
    """上传文件到 TOS，返回公网访问 URL。

    :param file_bytes: 文件二进制内容
    :param file_name: 原始文件名（用于推断扩展名）
    :param content_type: MIME 类型（如 image/jpeg、video/mp4）
    :param subdir: 子目录（如 images / videos）
    :param custom_name: 自定义文件名（不含扩展名），如产品编码
    :return: 公网可访问的 URL
    """
    client = _get_tos_client()
    settings = get_settings()

    object_key = _build_object_key(file_name, subdir, custom_name=custom_name)

    try:
        import tos
    except ImportError:
        raise RuntimeError("未安装 tos SDK，请运行: pip install tos==2.9.2")

    # 构建上传参数（按 TOS SDK 官方 API 签名）
    put_kwargs = {
        "bucket": settings.TOS_BUCKET,
        "key": object_key,
        "content": file_bytes,
    }

    # 设置 ACL 为公共读，使对象可通过 URL 直接访问
    try:
        put_kwargs["acl"] = tos.ACLType.ACL_Public_Read
    except AttributeError:
        # 兼容不同 SDK 版本
        put_kwargs["acl"] = "public-read"

    # 设置 Content-Type
    if content_type:
        try:
            put_kwargs["content_type"] = content_type
        except Exception:
            pass

    try:
        client.put_object(**put_kwargs)
        url = _build_public_url(object_key)
        logger.info("TOS 上传成功: %s -> %s", file_name, url)
        return url
    except Exception as e:
        logger.error("TOS 上传失败: %s, file=%s, err=%s",
                     object_key, file_name, e)
        raise RuntimeError(f"文件上传到 TOS 失败: {e}")


def upload_image(file_bytes: bytes, file_name: str, custom_name: str = "") -> str:
    """上传图片到 TOS（images 子目录）"""
    # 根据扩展名推断 content_type
    _, ext = os.path.splitext(file_name)
    ext = ext.lower()
    ct_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }
    content_type = ct_map.get(ext, "image/jpeg")
    return upload_file(file_bytes, file_name, content_type=content_type, subdir="images", custom_name=custom_name)


def upload_video(file_bytes: bytes, file_name: str, custom_name: str = "") -> str:
    """上传视频到 TOS（videos 子目录）"""
    _, ext = os.path.splitext(file_name)
    ext = ext.lower()
    ct_map = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".wmv": "video/x-ms-wmv",
        ".flv": "video/x-flv",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
    }
    content_type = ct_map.get(ext, "video/mp4")
    return upload_file(file_bytes, file_name, content_type=content_type, subdir="videos", custom_name=custom_name)


def bound_video_target_name(product_code: str, seq: int) -> str:
    """绑定视频的 TOS 目标名「产品编码_序号_时间戳」。

    时间戳成分保证删除视频后重新绑定时 URL 不与历史重复，
    避免 CDN/浏览器缓存按同名 URL 返回旧视频内容。
    """
    return f"{product_code}_{seq}_{int(datetime.now().timestamp())}"


def rename_file(src_url: str, new_name: str, subdir: str = "videos") -> str:
    """服务端复制对象为新名称（保留原对象），返回新公网 URL。

    用于 AI视频/高清处理/AI生图 结果绑定产品时改名为「产品编码_序号」：
    仅在桶内复制（CopyObject），不下载上传大文件，也不删除源对象
    （AI 任务历史仍引用源地址）。复制失败抛异常，由调用方决定回退。

    :param subdir: 目标目录，video 用 "videos"、image 用 "images"（与产品直传命名保持一致）
    """
    from urllib.parse import urlparse, unquote

    settings = get_settings()
    client = _get_tos_client()

    src_key = unquote(urlparse(src_url).path.lstrip("/"))
    _, ext = os.path.splitext(src_key)
    ext = ext.lower() or (".jpg" if subdir == "images" else ".mp4")

    prefix = (settings.TOS_PREFIX or "").strip("/")
    new_key = f"{prefix}/{subdir}/{new_name}{ext}" if prefix else f"{subdir}/{new_name}{ext}"

    try:
        import tos
    except ImportError:
        raise RuntimeError("未安装 tos SDK，请运行: pip install tos==2.9.2")

    copy_kwargs = {
        "bucket": settings.TOS_BUCKET,
        "key": new_key,
        "src_bucket": settings.TOS_BUCKET,
        "src_key": src_key,
    }
    try:
        # 新版 SDK 支持 copy 时携带 ACL
        copy_kwargs["acl"] = tos.ACLType.ACL_Public_Read
        client.copy_object(**copy_kwargs)
    except (AttributeError, TypeError):
        # 旧版 SDK 签名不同，先复制再补 ACL
        copy_kwargs.pop("acl", None)
        client.copy_object(**copy_kwargs)
        try:
            client.put_object_acl(
                bucket=settings.TOS_BUCKET, key=new_key, acl=tos.ACLType.ACL_Public_Read
            )
        except Exception:
            pass

    new_url = _build_public_url(new_key)
    logger.info("TOS 复制改名: %s -> %s", src_key, new_key)
    return new_url


def delete_file(file_url: str) -> bool:
    """根据公网 URL 删除 TOS 中的对象（可选清理用）"""
    try:
        client = _get_tos_client()
        settings = get_settings()

        # 从 URL 中解析出 object_key
        if settings.TOS_CUSTOM_DOMAIN:
            domain = settings.TOS_CUSTOM_DOMAIN.strip("/")
            prefix = f"https://{domain}/"
        else:
            bucket = settings.TOS_BUCKET
            endpoint = settings.TOS_ENDPOINT.strip("/")
            prefix = f"https://{bucket}.{endpoint}/"

        if not file_url.startswith(prefix):
            logger.warning("URL 不属于当前 TOS 桶，跳过删除: %s", file_url)
            return False

        object_key = file_url[len(prefix):]
        client.delete_object(bucket=settings.TOS_BUCKET, key=object_key)
        logger.info("TOS 删除成功: %s", object_key)
        return True
    except Exception as e:
        logger.error("TOS 删除失败: %s, err=%s", file_url, e)
        return False
