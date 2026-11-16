"""密码哈希服务（PBKDF2-HMAC-SHA256，标准库实现）。

为什么用 PBKDF2
--------------
本项目**不引入** bcrypt / argon2 等第三方依赖：
- ``backend/requirements.txt`` 刻意保持精简（生产环境少一个依赖少一个供应链风险）；
- Python 3.12 标准库的 ``hashlib.pbkdf2_hmac`` 是 **OWASP 认可的 KDF**，
  参数按 OWASP 2023 建议配置（SHA-256 / 600,000 轮 / 16 字节盐）。

哈希串格式（自描述，便于将来升级参数或迁移到 bcrypt 时平滑兼容）::

    pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>

四个字段用 ``$`` 分隔。解析时按字段数分支，因此
「以后加参数位」不会破坏已有数据 —— 老串仍能按老规则校验。

安全要点
--------
1. **每次哈希都用新随机盐**（``secrets.token_bytes``），
   同一密码哈希两次结果不同 → 彩虹表无效。
2. **校验用恒定时间比较**（``hmac.compare_digest``），
   避免时序侧信道泄露前缀信息。
3. **校验失败不抛异常**，统一返回 ``False`` —— 调用方不区分
   「用户不存在」与「密码错误」，避免账号枚举。
4. ``hash_password`` 对**空密码**直接拒绝 —— 不允许把空密码写进库。

⚠️ 存量兼容
----------
历史上所有员工的密码是**明文 ``"123456"``**，库里没有哈希列。
本模块提供 :func:`needs_rehash` 与 :func:`hash_password`，
由路由层决定「明文兜底登录 → 登录成功后立即补写哈希」。
这样可以**不停机、不锁死用户**地平滑迁移。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# ── 参数（OWASP 2023 建议：SHA-256 + 600,000 轮）──────────────────────────
ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 600_000
SALT_BYTES = 16
# 派生密钥长度：SHA-256 的输出长度 = 32 字节
HASH_BYTES = 32

# 判定「哈希参数过时、需要重新哈希」的阈值。
# 例如未来把轮数提到 1,000,000，老的 600,000 串会被标记为需重算。
MIN_ACCEPTABLE_ITERATIONS = 600_000

# 密码长度限制。
#
# ⚠️ 8–32 位是**与前端表单规则对齐**的值，不是随手选的：
#    frontend/src/views/ChangePassword.vue 与
#    frontend/src/views/profile/ChangePasswordDialog.vue 的校验规则都是
#    "8 至 32 位"。后端必须用同一套数字，否则会出现
#    「前端放行、后端拒绝」或「前端拒绝、后端放行」的口径分裂。
#
#    下限 8：NIST SP 800-63B 建议长度 ≥ 8（单因素场景）。
#    上限 32：既是前端上限，也给 PBKDF2 设定计算量上界，
#    避免超长输入拖垮服务（哈希是 O(1) 但 pre-hash 阶段不是）。
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 32


class PasswordStrengthError(ValueError):
    """密码不满足强度要求。

    名字不叫 ``PasswordTooShortError``，因为现在校验的不止长度，
    还包括"与旧密码相同"等其它规则。
    """


# 保留旧名做向后兼容（万一别处已 import）。
PasswordTooShortError = PasswordStrengthError


def _b64encode(raw: bytes) -> str:
    """标准 Base64（带 padding），便于 `base64.b64decode` 直接还原。"""
    return base64.b64encode(raw).decode("ascii")


def _b64decode(text: str) -> bytes:
    """解码 Base64，容错缺失的 padding。"""
    padded = text + "=" * (-len(text) % 4)
    return base64.b64decode(padded)


def validate_password_strength(password: str) -> None:
    """校验密码强度，不满足则抛 :class:`PasswordStrengthError`。

    ⚠️ 这里**刻意不做**「必须含大小写+数字+符号」这类组合复杂度规则 ——
    组合复杂度会逼用户写出 ``P@ssw0rd123!`` 这种自己都记不住的密码，
    反而降低安全性。NIST SP 800-63B 的建议是**长度 + 泄露库比对**，
    本项目只强制长度。

    上限 32 是为了给 PBKDF2 设定计算量上界，避免超长输入拖垮服务。
    """
    if password is None:
        raise PasswordStrengthError("密码不能为空")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordStrengthError(
            f"密码长度不能少于 {MIN_PASSWORD_LENGTH} 位"
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordStrengthError(
            f"密码长度不能超过 {MAX_PASSWORD_LENGTH} 位"
        )


def hash_password(
    password: str,
    *,
    iterations: int = DEFAULT_ITERATIONS,
    enforce_strength: bool = True,
) -> str:
    """计算密码哈希，返回自描述哈希串。

    Args:
        password: 明文密码。
        iterations: PBKDF2 迭代轮数。
        enforce_strength: 是否强制长度下限。
            - ``True``（默认）：用于**用户新设**的密码，必须满足强度规则。
            - ``False``：用于**存量弱口令迁移** —— 把历史上
              明文 ``"123456"`` 这类 6 位口令也哈希掉。

            ⚠️ BUG-R4-01 修复：原先 ``hash_password`` 无条件跑
            ``validate_password_strength``，而 ``MIN_PASSWORD_LENGTH = 8``，
            于是存量弱口令 ``"123456"``（6 位）**直接抛 PasswordStrengthError**。
            ``_upgrade_legacy_password`` 把该异常吞掉并 rollback →
            **哈希没写进去、force_change_password 也没置位**，
            结果是弱口令永久可用、强制首次改密形同虚设。

    Returns:
        形如 ``pbkdf2_sha256$600000$<salt_b64>$<hash_b64>`` 的字符串。

    Raises:
        PasswordStrengthError: 密码为空、（enforce_strength=True 时）长度不达标。
    """
    if enforce_strength:
        validate_password_strength(password)
    elif not password:
        raise PasswordStrengthError("密码不能为空")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=HASH_BYTES
    )
    return f"{ALGORITHM}${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    """校验明文密码是否匹配存储的哈希。

    ⚠️ 任何异常（格式不对、算法不认识、解码失败）都**按校验失败处理**，
    绝不向上抛 —— 调用方不应因为一条脏数据而 500。

    Args:
        password: 待校验的明文密码。
        stored_hash: 库里的哈希串；``None`` / 空串一律视为不匹配。

    Returns:
        匹配返回 ``True``，否则 ``False``。
    """
    if not password or not stored_hash:
        return False
    try:
        algorithm, iterations_text, salt_b64, digest_b64 = stored_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != ALGORITHM:
        return False
    try:
        iterations = int(iterations_text)
        salt = _b64decode(salt_b64)
        expected = _b64decode(digest_b64)
    except (ValueError, TypeError):
        return False
    if iterations <= 0 or not salt:
        return False
    try:
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    # 恒定时间比较，避免时序侧信道。
    return hmac.compare_digest(actual, expected)


def needs_rehash(stored_hash: str | None) -> bool:
    """判断已存哈希是否应当用当前参数重新计算。

    用于「用户登录成功时顺手把哈希升级到新参数」的静默轮换。
    """
    if not stored_hash:
        return True
    try:
        algorithm, iterations_text, _, _ = stored_hash.split("$", 3)
    except ValueError:
        return True
    if algorithm != ALGORITHM:
        return True
    try:
        return int(iterations_text) < MIN_ACCEPTABLE_ITERATIONS
    except (ValueError, TypeError):
        return True
