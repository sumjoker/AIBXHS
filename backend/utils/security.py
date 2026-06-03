"""
安全工具函数模块
提供输入验证、清理、速率限制、数据脱敏等安全功能
"""

import re
import html
import hashlib
import hmac
import secrets
from typing import Optional, Dict, List, Any, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import logging

# 配置日志
logger = logging.getLogger(__name__)


# =============================================================================
# 输入验证正则表达式模式
# =============================================================================

class INPUT_PATTERNS:
    """安全检测正则表达式模式"""

    # SQL注入检测模式
    SQL_INJECTION = re.compile(
        r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|EXEC|EXECUTE|UNION|"
        r"WHERE|FROM|TABLE|DATABASE|SCHEMA|GRANT|REVOKE|DECLARE|CAST|CONVERT|"
        r"CHAR|NCHAR|VARCHAR|NVARCHAR|INTO|LOAD_FILE|OUTFILE|DUMPFILE)\b)|"
        r"('|--|;|/\*|\*/|xp_|sp_|0x[0-9a-fA-F]+)",
        re.IGNORECASE | re.MULTILINE
    )

    # XSS攻击检测模式
    XSS_ATTACK = re.compile(
        r"(<script|</script|javascript:|on\w+\s*=|<iframe|<object|<embed|"
        r"<form|<input|<textarea|<svg|xlink:href|data:text/html|"
        r"expression\s*\(|url\s*\()",
        re.IGNORECASE | re.MULTILINE
    )

    # 路径遍历检测模式
    PATH_TRAVERSAL = re.compile(
        r"(\.\./|\.\.\\|%2e%2e%2f|%2e%2e/|%2e%2e%5c|%252e%252e%252f|"
        r"\.\.//|\.\.\\\\|/etc/passwd|/etc/shadow|/proc/|/sys/|"
        r"C:\\Windows\\|C:\\Program\s+Files|cmd\.exe|command\.com)",
        re.IGNORECASE | re.MULTILINE
    )

    # 命令注入检测模式
    COMMAND_INJECTION = re.compile(
        r"[;&|`$(){}[\]\\\n\r]|(\|\||&&|;\s*\n)|"
        r"\b(bash|sh|cmd|powershell|python|perl|ruby|nc|netcat|wget|curl|"
        r"ping|telnet|ssh|ftp|scp|sftp)\b",
        re.IGNORECASE | re.MULTILINE
    )

    # 敏感信息检测模式（身份证号、手机号、银行卡号等）
    SENSITIVE_PII = re.compile(
        r"(\d{18}|\d{17}[Xx])|"  # 身份证号
        r"(1[3-9]\d{9})|"  # 手机号
        r"(\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4})|"  # 银行卡号
        r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})",  # 邮箱
        re.MULTILINE
    )

    # 危险字符检测
    DANGEROUS_CHARS = re.compile(
        r"[<>\"'&;`$(){}[\]\\|%\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
    )

    # 有效的用户名/ID格式
    VALID_USERNAME = re.compile(r"^[a-zA-Z0-9_\-\.]{3,50}$")

    # 有效的邮箱格式
    VALID_EMAIL = re.compile(
        r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    )


# =============================================================================
# 输入清理函数
# =============================================================================

def sanitize_input(
    input_data: str,
    max_length: int = 1000,
    allow_html: bool = False,
    remove_dangerous: bool = True,
    strip_whitespace: bool = True
) -> str:
    """
    清理用户输入，移除或转义危险字符

    Args:
        input_data: 原始输入字符串
        max_length: 最大允许长度
        allow_html: 是否允许HTML标签
        remove_dangerous: 是否移除危险字符（而非转义）
        strip_whitespace: 是否去除首尾空白

    Returns:
        清理后的安全字符串
    """
    if not isinstance(input_data, str):
        input_data = str(input_data) if input_data is not None else ""

    # 去除首尾空白
    if strip_whitespace:
        input_data = input_data.strip()

    # 长度限制
    if len(input_data) > max_length:
        input_data = input_data[:max_length]
        logger.warning(f"Input truncated to {max_length} characters")

    # 处理HTML
    if not allow_html:
        # 转义HTML特殊字符
        input_data = html.escape(input_data)
    else:
        # 如果允许HTML，需要更严格的清理
        # 移除危险的HTML标签和属性
        input_data = remove_dangerous_html(input_data)

    # 移除或转义危险字符
    if remove_dangerous:
        input_data = INPUT_PATTERNS.DANGEROUS_CHARS.sub("", input_data)
    else:
        # 转义剩余的危险字符
        input_data = html.escape(input_data)

    # 移除 null 字节
    input_data = input_data.replace("\x00", "")

    return input_data


def remove_dangerous_html(html_content: str) -> str:
    """
    移除HTML中的危险标签和属性

    Args:
        html_content: HTML内容

    Returns:
        清理后的HTML
    """
    # 危险标签列表
    dangerous_tags = [
        "script", "iframe", "object", "embed", "form", "input",
        "textarea", "button", "select", "style", "link", "meta",
        "base", "frame", "frameset", "applet"
    ]

    # 危险属性列表
    dangerous_attrs = [
        "onerror", "onload", "onclick", "onmouseover", "onmouseout",
        "onmousedown", "onmouseup", "onmousemove", "onkeydown",
        "onkeyup", "onkeypress", "onsubmit", "onchange", "onfocus",
        "onblur", "onselect", "javascript:", "data:", "vbscript:"
    ]

    # 移除危险标签（包括内容）
    for tag in dangerous_tags:
        pattern = re.compile(
            f"<{tag}[^>]*>.*?</{tag}>",
            re.IGNORECASE | re.DOTALL
        )
        html_content = pattern.sub("", html_content)
        # 移除自闭合标签
        pattern = re.compile(f"<{tag}[^>]*/?>", re.IGNORECASE)
        html_content = pattern.sub("", html_content)

    # 移除危险属性
    for attr in dangerous_attrs:
        pattern = re.compile(
            f"\\s{re.escape(attr)}\\s*=\\s*['\"][^'\"]*['\"]",
            re.IGNORECASE
        )
        html_content = pattern.sub("", html_content)

    return html_content


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """
    清理文件名，防止路径遍历攻击

    Args:
        filename: 原始文件名
        max_length: 最大文件名长度

    Returns:
        安全的文件名
    """
    if not filename:
        return "unnamed_file"

    # 移除路径分隔符
    filename = re.sub(r"[\\/]", "_", filename)

    # 移除危险字符
    filename = re.sub(r"[<>:\"|?*\x00-\x1f]", "_", filename)

    # 移除路径遍历模式
    filename = re.sub(r"\.{2,}", "_", filename)

    # 长度限制
    if len(filename) > max_length:
        name, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")
        filename = name[:max_length - len(ext) - 1] + "." + ext if ext else name[:max_length]

    return filename.strip()


# =============================================================================
# 输入验证函数
# =============================================================================

def validate_input(
    input_data: str,
    check_sql: bool = True,
    check_xss: bool = True,
    check_path: bool = True,
    check_command: bool = True,
    custom_patterns: Optional[List[Tuple[str, str]]] = None
) -> Dict[str, Any]:
    """
    验证输入是否包含恶意内容

    Args:
        input_data: 待验证的输入字符串
        check_sql: 是否检查SQL注入
        check_xss: 是否检查XSS攻击
        check_path: 是否检查路径遍历
        check_command: 是否检查命令注入
        custom_patterns: 自定义验证模式列表 [(name, pattern), ...]

    Returns:
        验证结果字典 {
            "is_valid": bool,
            "threats": List[str],
            "details": Dict[str, Any]
        }
    """
    result = {
        "is_valid": True,
        "threats": [],
        "details": {}
    }

    if not input_data:
        return result

    # SQL注入检查
    if check_sql:
        sql_matches = INPUT_PATTERNS.SQL_INJECTION.findall(input_data)
        if sql_matches:
            result["is_valid"] = False
            result["threats"].append("SQL_INJECTION")
            result["details"]["sql_injection"] = {
                "matches": list(set(str(m) for m in sql_matches if m))[:5],
                "risk_level": "HIGH"
            }
            logger.warning(f"SQL injection detected: {input_data[:100]}")

    # XSS攻击检查
    if check_xss:
        xss_matches = INPUT_PATTERNS.XSS_ATTACK.findall(input_data)
        if xss_matches:
            result["is_valid"] = False
            result["threats"].append("XSS_ATTACK")
            result["details"]["xss_attack"] = {
                "matches": list(set(str(m) for m in xss_matches if m))[:5],
                "risk_level": "HIGH"
            }
            logger.warning(f"XSS attack detected: {input_data[:100]}")

    # 路径遍历检查
    if check_path:
        path_matches = INPUT_PATTERNS.PATH_TRAVERSAL.findall(input_data)
        if path_matches:
            result["is_valid"] = False
            result["threats"].append("PATH_TRAVERSAL")
            result["details"]["path_traversal"] = {
                "matches": list(set(str(m) for m in path_matches if m))[:5],
                "risk_level": "HIGH"
            }
            logger.warning(f"Path traversal detected: {input_data[:100]}")

    # 命令注入检查
    if check_command:
        cmd_matches = INPUT_PATTERNS.COMMAND_INJECTION.findall(input_data)
        if cmd_matches:
            result["is_valid"] = False
            result["threats"].append("COMMAND_INJECTION")
            result["details"]["command_injection"] = {
                "matches": list(set(str(m) for m in cmd_matches if m))[:5],
                "risk_level": "CRITICAL"
            }
            logger.warning(f"Command injection detected: {input_data[:100]}")

    # 自定义模式检查
    if custom_patterns:
        for name, pattern in custom_patterns:
            compiled_pattern = re.compile(pattern, re.IGNORECASE)
            matches = compiled_pattern.findall(input_data)
            if matches:
                result["is_valid"] = False
                result["threats"].append(f"CUSTOM:{name}")
                result["details"][f"custom_{name}"] = {
                    "matches": matches[:5],
                    "risk_level": "MEDIUM"
                }

    return result


def is_safe_string(
    input_data: str,
    min_length: int = 1,
    max_length: int = 1000,
    allowed_chars: Optional[str] = None
) -> bool:
    """
    检查字符串是否符合安全要求

    Args:
        input_data: 待检查的字符串
        min_length: 最小长度
        max_length: 最大长度
        allowed_chars: 允许的字符集（正则表达式）

    Returns:
        是否安全
    """
    if not isinstance(input_data, str):
        return False

    if len(input_data) < min_length or len(input_data) > max_length:
        return False

    if allowed_chars:
        if not re.match(f"^[{re.escape(allowed_chars)}]+$", input_data):
            return False

    # 检查是否包含威胁
    validation = validate_input(input_data)
    return validation["is_valid"]


# =============================================================================
# 速率限制
# =============================================================================

class RateLimiter:
    """内存中的速率限制器"""

    def __init__(self):
        # 存储请求记录: {identifier: [(timestamp, count), ...]}
        self._requests: Dict[str, List[datetime]] = defaultdict(list)
        # 存储限制规则: {identifier_prefix: (max_requests, window_seconds)}
        self._limits: Dict[str, Tuple[int, int]] = {}
        # 清理时间间隔
        self._cleanup_interval = 3600  # 1小时
        self._last_cleanup = datetime.now()

    def set_limit(self, identifier_prefix: str, max_requests: int, window_seconds: int):
        """
        设置速率限制规则

        Args:
            identifier_prefix: 标识符前缀
            max_requests: 时间窗口内最大请求数
            window_seconds: 时间窗口（秒）
        """
        self._limits[identifier_prefix] = (max_requests, window_seconds)

    def check_rate_limit(
        self,
        identifier: str,
        max_requests: Optional[int] = None,
        window_seconds: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        检查是否超出速率限制

        Args:
            identifier: 客户端标识符
            max_requests: 最大请求数（覆盖默认值）
            window_seconds: 时间窗口秒数（覆盖默认值）

        Returns:
            检查结果 {
                "allowed": bool,
                "remaining": int,
                "reset_time": datetime,
                "retry_after": int
            }
        """
        now = datetime.now()

        # 定期清理过期记录
        if (now - self._last_cleanup).seconds > self._cleanup_interval:
            self._cleanup_old_records()

        # 确定限制规则
        limit_key = None
        for prefix in self._limits:
            if identifier.startswith(prefix):
                limit_key = prefix
                break

        if limit_key and max_requests is None:
            max_requests, window_seconds = self._limits[limit_key]

        max_requests = max_requests or 100
        window_seconds = window_seconds or 60

        # 获取该标识符的请求记录
        requests = self._requests[identifier]

        # 移除时间窗口外的记录
        window_start = now - timedelta(seconds=window_seconds)
        requests_in_window = [r for r in requests if r > window_start]
        self._requests[identifier] = requests_in_window

        # 检查是否超出限制
        current_count = len(requests_in_window)

        if current_count >= max_requests:
            # 计算重置时间
            oldest_request = min(requests_in_window)
            reset_time = oldest_request + timedelta(seconds=window_seconds)
            retry_after = int((reset_time - now).total_seconds())

            logger.warning(f"Rate limit exceeded for {identifier}")
            return {
                "allowed": False,
                "remaining": 0,
                "reset_time": reset_time,
                "retry_after": max(1, retry_after)
            }

        # 记录本次请求
        self._requests[identifier].append(now)

        return {
            "allowed": True,
            "remaining": max_requests - current_count - 1,
            "reset_time": now + timedelta(seconds=window_seconds),
            "retry_after": 0
        }

    def _cleanup_old_records(self):
        """清理过期的请求记录"""
        now = datetime.now()
        max_age = timedelta(hours=1)

        for identifier in list(self._requests.keys()):
            self._requests[identifier] = [
                r for r in self._requests[identifier]
                if now - r < max_age
            ]
            if not self._requests[identifier]:
                del self._requests[identifier]

        self._last_cleanup = now


# 全局速率限制器实例
_rate_limiter = RateLimiter()


def check_rate_limit(
    identifier: str,
    max_requests: Optional[int] = None,
    window_seconds: Optional[int] = None
) -> Dict[str, Any]:
    """
    检查速率限制（使用全局限制器）

    Args:
        identifier: 客户端标识符
        max_requests: 最大请求数
        window_seconds: 时间窗口秒数

    Returns:
        检查结果字典
    """
    return _rate_limiter.check_rate_limit(identifier, max_requests, window_seconds)


def set_rate_limit(identifier_prefix: str, max_requests: int, window_seconds: int):
    """
    设置速率限制规则

    Args:
        identifier_prefix: 标识符前缀
        max_requests: 最大请求数
        window_seconds: 时间窗口秒数
    """
    _rate_limiter.set_limit(identifier_prefix, max_requests, window_seconds)


# =============================================================================
# 客户端标识
# =============================================================================

def get_client_identifier(
    request_headers: Optional[Dict[str, str]] = None,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    user_id: Optional[str] = None
) -> str:
    """
    获取客户端标识符用于追踪和速率限制

    Args:
        request_headers: 请求头字典
        client_ip: 客户端IP地址
        user_agent: 用户代理字符串
        user_id: 用户ID

    Returns:
        客户端标识符字符串
    """
    parts = []

    # 用户ID优先级最高
    if user_id:
        parts.append(f"user:{user_id}")

    # IP地址
    if client_ip:
        # 处理代理链，获取真实IP
        parts.append(f"ip:{client_ip}")

    # 从请求头获取信息
    if request_headers:
        # 获取真实IP（考虑代理）
        forwarded_for = request_headers.get("X-Forwarded-For") or request_headers.get("x-forwarded-for")
        if forwarded_for and not client_ip:
            real_ip = forwarded_for.split(",")[0].strip()
            parts.append(f"ip:{real_ip}")

        # 使用User-Agent作为辅助标识
        ua = request_headers.get("User-Agent") or request_headers.get("user-agent")
        if ua and not user_agent:
            # 只取前50个字符作为标识
            user_agent = ua[:50]

    if user_agent:
        parts.append(f"ua:{hashlib.md5(user_agent.encode()).hexdigest()[:8]}")

    # 如果没有足够信息，生成匿名标识
    if not parts:
        return "anon:unknown"

    return "|".join(parts)


def get_secure_client_hash(
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    secret_key: Optional[str] = None
) -> str:
    """
    生成安全的客户端哈希（用于会话标识等）

    Args:
        client_ip: 客户端IP
        user_agent: 用户代理
        secret_key: 密钥（用于HMAC）

    Returns:
        安全哈希字符串
    """
    data_parts = []

    if client_ip:
        data_parts.append(client_ip)
    if user_agent:
        data_parts.append(user_agent)

    data = "|".join(data_parts) if data_parts else secrets.token_hex(16)

    if secret_key:
        return hmac.new(
            secret_key.encode(),
            data.encode(),
            hashlib.sha256
        ).hexdigest()[:32]
    else:
        return hashlib.sha256(data.encode()).hexdigest()[:32]


# =============================================================================
# 数据哈希和脱敏
# =============================================================================

def hash_sensitive_data(
    data: str,
    algorithm: str = "sha256",
    salt: Optional[str] = None,
    secret_key: Optional[str] = None
) -> str:
    """
    对敏感数据进行哈希处理

    Args:
        data: 待哈希的数据
        algorithm: 哈希算法 (sha256, sha512, md5)
        salt: 盐值
        secret_key: 密钥（用于HMAC）

    Returns:
        哈希值
    """
    if not data:
        return ""

    # 准备数据
    content = data.encode("utf-8")

    # 添加盐值
    if salt:
        content = salt.encode("utf-8") + content

    # 选择哈希算法
    if algorithm == "sha256":
        hasher = hashlib.sha256()
    elif algorithm == "sha512":
        hasher = hashlib.sha512()
    elif algorithm == "md5":
        hasher = hashlib.md5()
    else:
        hasher = hashlib.sha256()

    # 使用HMAC如果提供了密钥
    if secret_key:
        if algorithm == "sha256":
            return hmac.new(secret_key.encode(), content, hashlib.sha256).hexdigest()
        elif algorithm == "sha512":
            return hmac.new(secret_key.encode(), content, hashlib.sha512).hexdigest()
        else:
            return hmac.new(secret_key.encode(), content, hashlib.md5).hexdigest()

    hasher.update(content)
    return hasher.hexdigest()


def mask_sensitive_content(
    content: str,
    content_type: str = "auto",
    mask_char: str = "*",
    visible_prefix: int = 0,
    visible_suffix: int = 4
) -> str:
    """
    对敏感内容进行脱敏处理

    Args:
        content: 原始内容
        content_type: 内容类型 (auto, phone, email, id_card, bank_card, name, password)
        mask_char: 掩码字符
        visible_prefix: 前缀保留字符数
        visible_suffix: 后缀保留字符数

    Returns:
        脱敏后的内容
    """
    if not content:
        return ""

    # 自动检测内容类型
    if content_type == "auto":
        if re.match(r"^1[3-9]\d{9}$", content):
            content_type = "phone"
        elif re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", content):
            content_type = "email"
        elif re.match(r"^\d{17}[\dXx]$", content):
            content_type = "id_card"
        elif re.match(r"^\d{16,19}$", content):
            content_type = "bank_card"
        else:
            content_type = "default"

    # 根据类型脱敏
    if content_type == "phone":
        # 手机号：保留前3位和后4位
        if len(content) == 11:
            return content[:3] + mask_char * 4 + content[7:]
        return content[:3] + mask_char * max(1, len(content) - 6) + content[-3:]

    elif content_type == "email":
        # 邮箱：保留首字母和@后的域名
        parts = content.split("@")
        if len(parts) == 2:
            local = parts[0]
            domain = parts[1]
            if len(local) > 1:
                masked_local = local[0] + mask_char * (len(local) - 1)
            else:
                masked_local = mask_char
            return f"{masked_local}@{domain}"
        return content[0] + mask_char * (len(content) - 1)

    elif content_type == "id_card":
        # 身份证号：保留前6位和后4位
        if len(content) == 18:
            return content[:6] + mask_char * 8 + content[14:]
        return content[:6] + mask_char * max(1, len(content) - 10) + content[-4:]

    elif content_type == "bank_card":
        # 银行卡号：保留前6位和后4位
        if len(content) >= 10:
            return content[:6] + mask_char * (len(content) - 10) + content[-4:]
        return content[:2] + mask_char * max(1, len(content) - 4) + content[-2:]

    elif content_type == "name":
        # 姓名：保留姓氏
        if len(content) <= 1:
            return content
        elif len(content) == 2:
            return content[0] + mask_char
        else:
            return content[0] + mask_char * (len(content) - 1)

    elif content_type == "password":
        # 密码：全部掩码
        return mask_char * min(len(content), 8)

    else:
        # 默认脱敏
        if len(content) <= visible_prefix + visible_suffix:
            return content
        return (
            content[:visible_prefix] +
            mask_char * (len(content) - visible_prefix - visible_suffix) +
            content[-visible_suffix:]
        )


def mask_dict_sensitive_fields(
    data: Dict[str, Any],
    sensitive_fields: Optional[List[str]] = None,
    mask_char: str = "*"
) -> Dict[str, Any]:
    """
    对字典中的敏感字段进行脱敏

    Args:
        data: 原始数据字典
        sensitive_fields: 敏感字段名列表
        mask_char: 掩码字符

    Returns:
        脱敏后的字典
    """
    if sensitive_fields is None:
        sensitive_fields = [
            "password", "passwd", "pwd", "secret", "token",
            "api_key", "apikey", "access_token", "refresh_token",
            "credit_card", "card_number", "cvv", "ssn",
            "phone", "mobile", "email", "id_card", "id_number"
        ]

    result = {}
    for key, value in data.items():
        # 检查字段名是否敏感
        key_lower = key.lower()
        is_sensitive = any(sf.lower() in key_lower for sf in sensitive_fields)

        if is_sensitive and isinstance(value, str):
            result[key] = mask_sensitive_content(value, mask_char=mask_char)
        elif isinstance(value, dict):
            result[key] = mask_dict_sensitive_fields(value, sensitive_fields, mask_char)
        elif isinstance(value, list):
            result[key] = [
                mask_dict_sensitive_fields(item, sensitive_fields, mask_char)
                if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value

    return result


# =============================================================================
# 便捷函数
# =============================================================================

def generate_secure_token(length: int = 32) -> str:
    """生成安全的随机令牌"""
    return secrets.token_urlsafe(length)


def generate_csrf_token() -> str:
    """生成CSRF令牌"""
    return secrets.token_hex(32)


def constant_time_compare(val1: str, val2: str) -> bool:
    """
    常量时间字符串比较（防止时序攻击）

    Args:
        val1: 第一个字符串
        val2: 第二个字符串

    Returns:
        是否相等
    """
    return hmac.compare_digest(val1.encode(), val2.encode())


# 导出所有主要功能
__all__ = [
    # 正则表达式模式
    "INPUT_PATTERNS",
    # 输入清理
    "sanitize_input",
    "remove_dangerous_html",
    "sanitize_filename",
    # 输入验证
    "validate_input",
    "is_safe_string",
    # 速率限制
    "RateLimiter",
    "check_rate_limit",
    "set_rate_limit",
    # 客户端标识
    "get_client_identifier",
    "get_secure_client_hash",
    # 数据哈希和脱敏
    "hash_sensitive_data",
    "mask_sensitive_content",
    "mask_dict_sensitive_fields",
    # 便捷函数
    "generate_secure_token",
    "generate_csrf_token",
    "constant_time_compare",
]
