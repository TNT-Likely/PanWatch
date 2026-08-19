"""个人访问令牌(PAT)工具。

MCP 端点专用的独立鉴权体系,与登录 JWT 分流(参考 BeeCount-Cloud 模式):

- 明文前缀 ``pwmcp_``(便于用户与 secret scanner 识别,类比 GitHub ``ghp_``);
- 创建时仅返回一次明文,库里只存 sha256(``token_hash``);
- 校验用 ``hmac.compare_digest`` 常数时间比较,防 timing attack;
- 不用 bcrypt/PBKDF2 —— token 本身已是 256bit 随机熵,不像密码需抗暴破,
  且 MCP 每次 tool call 都要校验一次,sha256 + 常数时间比较又快又够安全。

分流保证(PAT 只能进 MCP 端点):
- 普通 API 走 JWT(auth.get_current_user),PAT(pwmcp_ 前缀)不是合法 JWT → 被拒;
- MCP 端点走 PAT 校验,非 pwmcp_ 前缀的 JWT → 被拒。
"""

import hashlib
import hmac
import secrets

PAT_PREFIX = "pwmcp_"
PAT_RANDOM_BYTES = 32          # token_urlsafe 后约 43 字符,256bit 熵
PAT_DISPLAY_PREFIX_LEN = 14    # 明文前 14 字符,如 pwmcp_a1b2c3d4

# MCP scope(全只读)
SCOPE_MCP_READ = "mcp:read"


def hash_token(token: str) -> str:
    """sha256 十六进制摘要。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_pat() -> tuple[str, str, str]:
    """生成一个 PAT。

    Returns:
        (plaintext, token_hash, display_prefix) —— plaintext 只在创建时返回一次。
    """
    raw = secrets.token_urlsafe(PAT_RANDOM_BYTES)
    plaintext = f"{PAT_PREFIX}{raw}"
    return plaintext, hash_token(plaintext), plaintext[:PAT_DISPLAY_PREFIX_LEN]


def looks_like_pat(token: str) -> bool:
    """按前缀判断是否 PAT(用于鉴权路由分流,避免每个请求两遍解码)。"""
    return bool(token) and token.startswith(PAT_PREFIX)


def verify_pat_hash(provided_token: str, stored_hash: str) -> bool:
    """常数时间比较 PAT 的 sha256,防 timing attack。"""
    return hmac.compare_digest(hash_token(provided_token), stored_hash)
