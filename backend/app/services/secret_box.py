"""敏感配置的**信封加密**（★ 2026-11-26 新增）。

⚠️ **为什么需要**
------------------------------------------------------------------
AI 模型配置里有 **API Key**（供应商凭据）。
原先 ``/system/ai/configs`` 是空壳，压根没存 ⇒ 没有泄漏面。
现在要**真的存下来**，就必须回答：「明文存库行不行？」

⇒ **不行**。
``backend/data/perf.db`` 是仓库里的文件，会被备份、被复制、被误提交。
明文 API Key 落在里面 ⇒ 一次仓库泄漏 = 全部供应商凭据泄漏。

⚠️ **加密算法**
------------------------------------------------------------------
AES-256-GCM（``pycryptodome``）——
比项目里飞书事件用的 AES-CBC 好：GCM 带**认证标签**，
密文被篡改会直接解密失败，而不是悄悄解出垃圾数据。

⚠️ **密钥从哪来**
------------------------------------------------------------------
复用 ``settings.auth_token_secret``（HMAC 密钥，≥32 字符，启动时校验）。
- 不新增一个环境变量 ⇒ 少一个「忘了配」的失败模式；
- 换密钥 ⇒ 旧密文解不开（**这是期望行为**，需要重新填 API Key）。

⚠️ **为什么不自己新造一个配置项**
------------------------------------------------------------------
仓库里已有 ``auth_token_secret`` 且**强制**校验弱密钥
⇒ 复用它等于免费获得「密钥强度校验」这道闸。

⚠️ **密文格式**
------------------------------------------------------------------
``"v1:" + base64(nonce || ciphertext || tag)``
- 前缀 ``v1`` ⇒ 将来换算法时可以**并存**两套格式（按前缀分派）；
- nonce 每次随机（12 字节，GCM 推荐值）⇒ 相同明文产生不同密文。

安全边界
---------
- :func:`encrypt_secret` 返回的密文**只进数据库**；
- :func:`decrypt_secret` 只在**真的要调模型**时用；
- ⚠️ **任何接口都不许回传密文或明文** —— 对外只暴露
  「是否已配置」布尔位（``apiKeyConfigured``）。
"""

from __future__ import annotations

import base64
import hashlib
import os

from ..core.config import settings

#: 密文信封前缀（版本号）。
_PREFIX = "v1:"

#: GCM nonce 长度（字节）。
_NONCE_LEN = 12

#: GCM 认证标签长度（字节）。
_TAG_LEN = 16


def _key() -> bytes:
    """派生 32 字节 AES 密钥。

    ⚠️ **为什么要 SHA-256 派生**而不是直接用密钥字节
    ``auth_token_secret`` 可能不是 32 字节（AES-256 要求恰好 32）。
    ⇒ 统一 SHA-256 派生，长度必然正确。

    ⚠️ **为什么要加盐**
    直接 SHA-256(secret) 会让「相同密钥 ⇒ 相同派生密钥」，
    一旦某个系统泄漏派生密钥，原密钥也等于泄漏。
    加一个**固定域分隔串**能避免跨用途复用同一派生密钥。
    """
    material = settings.auth_token_secret.encode("utf-8") + b"\x00ai-model-config-v1"
    return hashlib.sha256(material).digest()


def encrypt_secret(plaintext: str) -> str:
    """加密敏感值，返回可安全入库的密文字符串。

    Args:
        plaintext: 明文（如 API Key）。空串返回空串（**不生成密文**）。

    Returns:
        ``"v1:<base64>"`` 形式的密文；明文为空时返回 ``""``。
    """
    if not plaintext:
        return ""

    try:
        from Crypto.Cipher import AES
    except ImportError as exc:  # pragma: no cover —— 依赖缺失时的兜底
        raise RuntimeError(
            "缺少 pycryptodome 依赖，无法加密敏感配置（pip install pycryptodome）"
        ) from exc

    nonce = os.urandom(_NONCE_LEN)
    cipher = AES.new(_key(), AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
    blob = nonce + ciphertext + tag
    return _PREFIX + base64.b64encode(blob).decode("ascii")


def decrypt_secret(envelope: str) -> str:
    """解密敏感值。

    Args:
        envelope: :func:`encrypt_secret` 产出的密文。

    Returns:
        明文。

    Raises:
        ValueError: 密文格式非法或认证失败（**不泄露任何细节**）。
    """
    if not envelope:
        return ""

    try:
        from Crypto.Cipher import AES
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "缺少 pycryptodome 依赖，无法解密敏感配置（pip install pycryptodome）"
        ) from exc

    if not envelope.startswith(_PREFIX):
        raise ValueError("密文格式不正确")

    try:
        blob = base64.b64decode(envelope[len(_PREFIX):], validate=True)
    except Exception as exc:
        raise ValueError("密文不是合法 base64") from exc

    if len(blob) < _NONCE_LEN + _TAG_LEN:
        raise ValueError("密文长度不足")

    nonce = blob[:_NONCE_LEN]
    ciphertext = blob[_NONCE_LEN:-_TAG_LEN]
    tag = blob[-_TAG_LEN:]

    cipher = AES.new(_key(), AES.MODE_GCM, nonce=nonce)
    try:
        plain = cipher.decrypt_and_verify(ciphertext, tag)
    except Exception as exc:
        # ⚠️ **不要把异常原文抛出去** —— 可能带内部细节。
        raise ValueError("密文校验失败（密钥可能已变更）") from exc
    return plain.decode("utf-8")


def is_encrypted(value: str) -> bool:
    """判断一个字符串是否是本模块产出的密文。"""
    return bool(value) and value.startswith(_PREFIX)


__all__ = ["decrypt_secret", "encrypt_secret", "is_encrypted"]
