"""飞书机器人事件与卡片回调 API（FS-3）。

端点
----
1. ``POST /v1/feishu/bot/events`` —— 机器人事件接收端点。
   飞书把两类事件都推到这里：
   - ``im.message.receive_v1`` —— 员工发消息（意图路由 → 回卡片）
   - ``card.action.trigger`` —— 员工点卡片（写草稿 → 回执）

2. ``POST /v1/feishu/bot/card/actions`` —— 卡片回调端点。
   ⚠️ 飞书后台可以把「卡片回调 URL」配成**独立的地址**，
   也可以让它复用事件订阅地址。两种都要支持。

来源校验
--------
与 A8 审批 webhook **同一套机制**（约束 C1）：

- **明文模式**（未配 Encrypt Key）→ 比对 ``header.token`` == 应用 Verification Token
- **加密模式**（配了 Encrypt Key）→ 先验签（``X-Lark-Signature``）再解密

⚠️ **Verification Token 是「应用级」的 —— 只能共用**（约束 C2 的修正）
--------------------------------------------------------------
飞书官方原文（开放平台「将事件发送至开发者服务器」）：

    「Verification Token 是**应用的验证标识**。开发者后台会为应用自动生成
     Verification Token」
    「**每个应用只能配置一个请求地址，该应用订阅的所有事件都会发送到
     该请求地址。**」

⇒ 一个应用只有一个 Token、一个请求地址。
⇒ 审批 / 通讯录 / 机器人**必然共用**同一个 Token —— 这是飞书的机制，
   不是配置问题。

⇒ 所以**隔离靠「端点路径 + 事件类型白名单」，不靠 Token**：
   本模块只处理 ``im.message.receive_v1`` 与 ``card.action.trigger``，
   其余事件类型**一律忽略**（返回 ``{"code": 0}``）。

⇒ Token 取值：``FEISHU_BOT_EVENT_TOKEN`` 优先，留空则**回退**用
   ``FEISHU_APPROVAL_WEBHOOK_TOKEN``（同一应用下二者本就相同，
   避免「配了一个漏配另一个」导致 503）。

握手
----
飞书保存回调地址时会 POST 一个带 ``challenge`` 的请求，
**必须在 1 秒内**原样返回 ``{"challenge": "<值>"}``。
⚠️ 握手**必须早于 Token 校验** —— 飞书保存地址时 body 里没有业务 token，
   先校验 Token 会直接 401，飞书就**保存不了地址**。

安全边界
--------
**未配置 Token → 503，绝不放行**。
否则任何人都能伪造卡片回调把草稿写进别人名下。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..domain.bot_models import BotSession
from ..domain.models import Employee
from ..services import feishu_bot, feishu_card, feishu_event, feishu_intent
from ..services.access_control import PermissionAction
from ..services.feishu_client import FeishuClient
from ..services.feishu_identity_service import FeishuIdentityService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/feishu/bot", tags=["feishu-bot"])


# ===========================================================================
# 常量
# ===========================================================================

#: 员工消息事件
EVENT_MESSAGE_RECEIVE = "im.message.receive_v1"
#: 卡片交互事件
EVENT_CARD_ACTION = "card.action.trigger"

#: ⚠️ **本端点处理的事件类型白名单**。
#:
#: 飞书的 Verification Token 是**应用级**的，且「每个应用只能配置一个请求地址，
#: 该应用订阅的所有事件都会发送到该请求地址」——
#: 所以审批 / 通讯录 / 机器人**共用**一个 Token，**隔离靠的是事件类型**。
#:
#: 本端点只处理下面两种事件，其余一律忽略（返回 code 0，不报错 ——
#: 报错会让飞书反复重推）。审批与通讯录的事件落到各自端点。
BOT_EVENT_TYPES = frozenset({EVENT_MESSAGE_RECEIVE, EVENT_CARD_ACTION})


# ===========================================================================
# 依赖
# ===========================================================================


def _client() -> FeishuClient:
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise HTTPException(status_code=503, detail="飞书应用配置未完成")
    return FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        tenant_id=settings.feishu_tenant_id,
    )


def _require_bot_token() -> str:
    """取机器人事件的 Verification Token —— 没有可用 Token 就 503。

    ⚠️ **Verification Token 是「应用级」的**（飞书官方）：
    「Verification Token 是应用的验证标识。开发者后台会为应用自动生成
    Verification Token」—— 一个应用只有一个，
    且「每个应用只能配置一个请求地址，该应用订阅的所有事件都会发送到
    该请求地址」。

    ⇒ 同一个应用里，审批 / 通讯录 / 机器人**必然共用**一个 Token。
    ⇒ 所以这里**回退**到 `feishu_approval_webhook_token`：
    运维时配了一个 Token 却漏配另一个，不该导致 503。

    ⇒ **隔离靠端点路径 + 事件类型白名单，不靠 Token**：
    本端点只处理 `im.message.receive_v1` 与 `card.action.trigger`，
    其余事件类型一律忽略（见 `_handle_message_event` / `_handle_card_action`）。

    独立部署（机器人单独一个飞书应用）时，填 `FEISHU_BOT_EVENT_TOKEN` 即可。
    """
    token = settings.feishu_bot_event_token or settings.feishu_approval_webhook_token
    if not token:
        logger.error(
            "飞书机器人 webhook 收到事件但未配置 "
            "FEISHU_BOT_EVENT_TOKEN / FEISHU_APPROVAL_WEBHOOK_TOKEN"
        )
        raise HTTPException(
            status_code=503,
            detail="飞书机器人 webhook 未配置 Verification Token",
        )
    return token


# ===========================================================================
# 报文解析
# ===========================================================================


def extract_event_type(body: dict[str, Any]) -> str:
    """取事件类型。

    v2.0 信封从 ``header.event_type`` 取；
    卡片回调的 v1.0 形态从 ``event.type`` 取。
    """
    header = body.get("header")
    header = header if isinstance(header, dict) else {}
    event_type = header.get("event_type") or body.get("event_type") or ""
    if event_type:
        return str(event_type).strip()

    event = body.get("event")
    if isinstance(event, dict):
        nested = event.get("type") or event.get("event_type") or ""
        if nested:
            return str(nested).strip()
    return ""


def extract_card_action(body: dict[str, Any]) -> dict[str, Any]:
    """从卡片回调报文里抽出动作信息。

    飞书卡片回调的报文形态（``card.action.trigger``，官方 v2.0 信封）::

        {
          "schema": "2.0",
          "header": {
            "event_id": "f7984f25108f8137722bb63c*****",
            "event_type": "card.action.trigger",
            "token": "066zT6pS4QCbgj5Do145GfDbbag*****"
          },
          "event": {
            "operator": {
              "open_id": "ou_3c14f3a59eaf2825dbe25359f15*****",
              "user_id": "867*****",
              "union_id": "on_cad4860e7af114fb4ff6c5d496d*****",
              "token": "c-295ee57216a5dc9de90fefd0aadb4b1d7d******"
            },
            "action": {
              "value": {"action": "submit_draft", "session_id": "..."},
              "form_value": {"task_name": "..."},
              "tag": "button",
              "name": "Button_lvkepfu3"
            },
            "host": "im_message",
            "context": {"open_message_id": "om_...", "open_chat_id": "oc_..."}
          }
        }

    ⚠️ **两个官方文档里明确写到的坑**
    ----------------------------------
    1. ``action.value`` 的类型是 **string 或 object**（官方原文：
       "对应组件中的 value 属性。类型为 string 或 object"）。
       只按 dict 解析 → 拿不到 ``action`` 字段 → 按钮全都不生效。
    2. ``form_value`` 的值**可能是数组**（多选组件回传的是数组）。
       按纯字符串解析 → 值变成 ``"['a', 'b']"``。

    Returns:
        ``{"action": ..., "value": {...}, "form_value": {...},
        "open_id": ..., "user_id": ..., "token": ...}``
    """
    event = body.get("event")
    event = event if isinstance(event, dict) else {}

    action = event.get("action")
    action = action if isinstance(action, dict) else {}

    # ⚠️ value 可能是 string（官方：类型为 string 或 object）
    raw_value = action.get("value")
    if isinstance(raw_value, dict):
        value: dict[str, Any] = dict(raw_value)
    elif isinstance(raw_value, str):
        # 字符串形态 —— 尝试按 JSON 解析，失败就当整体值
        try:
            parsed = json.loads(raw_value)
            value = parsed if isinstance(parsed, dict) else {"value": raw_value}
        except (TypeError, ValueError):
            value = {"value": raw_value}
    else:
        value = {}

    form_value = action.get("form_value")
    form_value = form_value if isinstance(form_value, dict) else {}

    operator = event.get("operator")
    operator = operator if isinstance(operator, dict) else {}

    return {
        "action": str(value.get("action") or action.get("action") or "").strip(),
        "value": value,
        "form_value": form_value,
        "open_id": str(operator.get("open_id") or "").strip(),
        "user_id": str(operator.get("user_id") or "").strip(),
        "union_id": str(operator.get("union_id") or "").strip(),
        "token": str(event.get("token") or operator.get("token") or "").strip(),
    }


def extract_message_event(body: dict[str, Any]) -> dict[str, Any]:
    """从消息事件报文里抽出消息内容与发送者。

    ``im.message.receive_v1`` 的事件对象（官方报文）::

        {
          "sender": {
            "sender_id": {"union_id": "...", "user_id": "e33ggbyz", "open_id": "ou_..."},
            "sender_type": "user",
            "tenant_key": "..."
          },
          "message": {
            "message_id": "om_...",
            "chat_id": "oc_...",
            "chat_type": "group",
            "message_type": "text",
            "content": "{\\"text\\":\\"@_user_1 hello\\"}",
            "mentions": [
              {"key": "@_user_1", "id": {...}, "mentioned_type": "user", "name": "Tom"}
            ]
          }
        }

    ⚠️ **两个必须处理的细节**
    ------------------------
    1. ``content`` 是**JSON 字符串**，需要再解析一次。
    2. ⚠️ **群聊里文本带 @ 占位符** —— 官方报文就是
       ``"content": "{\\"text\\":\\"@_user_1 hello\\"}"``。
       ``@_user_1`` 是 ``mentions[].key``，**不是**用户输入的一部分。
       不去掉的话，意图识别就变成在匹配 ``"@_user_1 填月计划"`` ——
       关键词虽然还在，但一旦关键词被拆开就匹配不到了。
       所以这里按 ``mentions[].key`` **精确剥掉**（不是粗暴的正则去 @）。
    """
    event = body.get("event")
    event = event if isinstance(event, dict) else {}

    message = event.get("message")
    message = message if isinstance(message, dict) else {}

    sender = event.get("sender")
    sender = sender if isinstance(sender, dict) else {}
    sender_id = sender.get("sender_id")
    sender_id = sender_id if isinstance(sender_id, dict) else {}

    text = ""
    raw_content = message.get("content")
    if isinstance(raw_content, str) and raw_content.strip():
        try:
            parsed = json.loads(raw_content)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            text = str(parsed.get("text") or parsed.get("title") or "")
    elif isinstance(raw_content, dict):
        text = str(raw_content.get("text") or raw_content.get("title") or "")

    # ── 剥掉 @ 占位符 ─────────────────────────────────────────
    mentions = message.get("mentions")
    if isinstance(mentions, list):
        for mention in mentions:
            if not isinstance(mention, dict):
                continue
            key = str(mention.get("key") or "").strip()
            if key:
                text = text.replace(key, "")

    return {
        "message_id": str(message.get("message_id") or "").strip(),
        "message_type": str(message.get("message_type") or "text").strip(),
        "text": text,
        "open_id": str(sender_id.get("open_id") or "").strip(),
        "user_id": str(sender_id.get("user_id") or "").strip(),
        "chat_id": str(message.get("chat_id") or "").strip(),
        **extract_media_refs(message, raw_content),
    }


def extract_media_refs(
    message: dict[str, Any], raw_content: Any
) -> dict[str, Any]:
    """抽出消息里的**媒体引用**（图片 / 文件）。

    ⚠️ **为什么消息里没有字节，只有 key**
    飞书的 ``im.message.receive_v1`` 事件**不携带文件内容** ——
    图片/文件只给一个 ``image_key`` / ``file_key``，
    真正取字节要走 ``GET /im/v1/messages/{message_id}/resources/{type}``。

    ⇒ 这里只负责**把 key 抽出来**，取字节交给
    ``feishu_docs.fetch_message_resource``。

    ⚠️ **各消息类型的 content 结构不同**
    ========================== ====================================
    ``message_type``          ``content`` 里的字段
    ========================== ====================================
    ``image``                 ``image_key``
    ``file``                  ``file_key``
    ``audio``                 ``file_key``
    ``media``                 ``file_key`` + ``image_key``（封面图）
    ``sticker``               ``file_key``
    ========================== ====================================

    ⚠️ **post（富文本）里的图片**
    富文本 ``post`` 的 content 是嵌套结构，图片藏在
    ``content.post.zh_cn.content[].tag == "img"`` 的 ``img_key``。
    ⇒ 一并抽出来，否则员工在富文本里贴的截图收不到。
    """
    image_keys: list[str] = []
    file_keys: list[str] = []

    def _add_image(key: Any) -> None:
        text = str(key or "").strip()
        if text and text not in image_keys:
            image_keys.append(text)

    def _add_file(key: Any) -> None:
        text = str(key or "").strip()
        if text and text not in file_keys:
            file_keys.append(text)

    parsed: Any = None
    if isinstance(raw_content, str) and raw_content.strip():
        try:
            parsed = json.loads(raw_content)
        except (TypeError, ValueError):
            parsed = None
    elif isinstance(raw_content, dict):
        parsed = raw_content

    if isinstance(parsed, dict):
        _add_image(parsed.get("image_key"))
        _add_file(parsed.get("file_key"))

        # ⚠️ 富文本里的图片（post / zh_cn）
        post = parsed.get("post")
        if isinstance(post, dict):
            for lang_body in post.values():
                if not isinstance(lang_body, dict):
                    continue
                blocks = lang_body.get("content")
                if not isinstance(blocks, list):
                    continue
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    if block.get("tag") == "img":
                        _add_image(block.get("img_key"))
                    elif block.get("tag") == "media":
                        _add_file(block.get("file_key"))
                        _add_image(block.get("image_key"))

    return {
        "image_keys": image_keys,
        "file_keys": file_keys,
        "media_type": "image" if image_keys else ("file" if file_keys else ""),
    }


# ===========================================================================
# 来源校验（与 A8 同一套机制）
# ===========================================================================


async def _read_verified_body(request: Request) -> dict[str, Any]:
    """取原始 body → 验签 → 解密 → 校验 Verification Token。

    与审批 webhook 的流程一致，**但用机器人自己的 Token**（约束 C2）。

    Raises:
        HTTPException: 任何一步失败都拒绝，**不支持降级放行**。
    """
    raw_body = await request.body()
    if not raw_body:
        raise HTTPException(status_code=400, detail="请求体为空")

    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON") from exc

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体不是 JSON 对象")

    encrypt_key = settings.feishu_encrypt_key

    # ── 加密模式：先验签，再解密 ──────────────────────────────
    if isinstance(body.get("encrypt"), str) and body["encrypt"]:
        if not encrypt_key:
            logger.error("飞书机器人 webhook 收到加密事件但未配置 FEISHU_ENCRYPT_KEY")
            raise HTTPException(status_code=503, detail="未配置 Encrypt Key，无法解密事件")

        timestamp = request.headers.get(feishu_event.HEADER_TIMESTAMP, "")
        nonce = request.headers.get(feishu_event.HEADER_NONCE, "")
        signature = request.headers.get(feishu_event.HEADER_SIGNATURE, "")
        if not feishu_event.verify_signature(
            encrypt_key=encrypt_key,
            timestamp=timestamp,
            nonce=nonce,
            signature=signature,
            raw_body=raw_body,
        ):
            logger.warning("飞书机器人 webhook 签名校验失败")
            raise HTTPException(status_code=401, detail="签名校验失败")

        try:
            plain = feishu_event.decrypt_event(encrypt_key, body["encrypt"])
        except feishu_event.FeishuEventError as exc:
            logger.warning("飞书机器人 webhook 解密失败：%s", exc)
            raise HTTPException(status_code=400, detail=f"事件解密失败：{exc}") from exc

        try:
            body = json.loads(plain)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="解密结果不是合法 JSON") from exc

    # ── 握手：飞书保存回调地址时的 url_verification ──────────
    # ⚠️ 握手**必须**在 Token 校验之前处理 —— 飞书保存回调地址时
    #    带的 body 里没有业务 token，若先校验 Token 会直接 401，
    #    飞书就**保存不了回调地址**（A8 踩过同样的坑）。
    if feishu_event.is_url_verification(body):
        challenge = feishu_event.extract_challenge(body)
        if not challenge:
            raise HTTPException(status_code=400, detail="握手请求缺少 challenge")
        logger.info("飞书机器人回调地址校验握手通过")
        return body

    # ── 校验 Verification Token ───────────────────────────────
    expected_token = _require_bot_token()
    event = feishu_event.parse_event(body)
    token = event.token or str(body.get("token") or "")
    if not token or token != expected_token:
        logger.warning("飞书机器人 webhook token 校验失败")
        raise HTTPException(status_code=401, detail="Verification Token 校验失败")

    return body


# ===========================================================================
# 事件处理
# ===========================================================================


#: ⚠️ P0-9：写操作被拦截时的**统一文案**。
#:
#: ⚠️ **值必须与 ``qa_chat_service.QA_WRITE_DENIED_MESSAGE`` 一致**
#:    这里的比对是**文案嗅探** —— 服务层抛的是 403 + 这句话，
#:    路由层靠它区分「写操作被拦」与「范围不可见」两类 403。
#:
#: ⚠️ **为什么用文案嗅探而不是自定义异常**
#:    抛自定义异常要多改一处（服务层 + 路由层 + 中间层），
#:    而 403 的 detail 已经是**稳定契约**（网页端也在用）。
#:    ⇒ 复用 detail 更简单，也更容易保持两端一致。
QA_WRITE_DENIED_MESSAGE = "AI 问答不提供绩效修改功能。"


def _qa_error_card(exc: HTTPException) -> dict[str, Any]:
    """把问答链路的异常变成**合适的卡片**（P0-9）。

    ⚠️ **为什么不能一律用「操作未完成」**
    ------------------------------------
    403（无权查看）与 500（系统故障）对用户的意义完全不同：
    - 403 —— 是**范围问题**，换个问法也没用 ⇒ 必须说清"为什么"和"下一步"；
    - 404 —— 是**参数问题**（月份/任务不对）⇒ 说清怎么改；
    - 其他 —— 确实是故障 ⇒ 「操作未完成」就够。

    ⚠️ **403 的文案不能泄露目标人信息**
    ------------------------------
    服务层抛出的 403 detail 已经是**统一口径**的文案
    （``qa_authorization`` 保证不区分跨部门/跨级）⇒ 直接用。

    ⚠️ **写操作拦截要单独一张卡**
    ----------------------------
    「AI 问答不提供绩效修改功能」和「不在可见范围内」是**两回事**：
    前者是**能力边界**（去后台就能做），后者是**权限范围**（谁都做不了）。
    ⇒ 前者给红色 + 后台引导，后者给橙色 + 组织范围说明。
    """
    detail = str(exc.detail or "")
    if exc.status_code == 403:
        # ⚠️ **写操作拦截** —— 靠文案嗅探区分。
        if QA_WRITE_DENIED_MESSAGE in detail:
            return feishu_card.build_qa_write_denied_card(message=detail)
        return feishu_card.build_qa_denied_card(
            message=detail or "该数据不在可见范围内。",
            guidance=(
                "绩效数据按组织范围隔离：同部门同级别同事可互查，"
                "跨部门、跨级或上级不在此范围内。"
            ),
        )
    return feishu_card.build_error_card(detail or "操作未完成。")


async def _handle_message_event(
    db: Session,
    client: FeishuClient,
    body: dict[str, Any],
) -> dict[str, Any]:
    """处理员工消息：意图路由 → 回卡片。

    ⚠️ **async 的原因**：`performance_qa` 走 AI 问答，
    内部要等 L3（最长 35 秒）。同步调用会**堵住事件循环** ——
    整个后端（包括其他人的请求）都卡住。
    """
    message = extract_message_event(body)
    text = message["text"]
    open_id = message["open_id"]

    if not open_id:
        logger.warning("飞书机器人消息事件缺少发送者 open_id，跳过")
        return {"code": 0, "message": "缺少发送者，已忽略"}

    session = feishu_bot.get_or_create_session(
        db,
        open_id=open_id,
        app_id=settings.feishu_app_id,
        user_id=message["user_id"],
    )

    intent = feishu_bot.detect_intent(text)
    if intent == "cancel":
        session.state = feishu_bot.STATE_IDLE
        session.intent = feishu_bot.INTENT_NONE
        session.collected = {}
        db.flush()
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card("已取消本次填表。"),
        )
        return {"code": 0, "message": "已取消"}

    from ..services import feishu_scheme

    # ⚠️ **未命中关键词 ≠ 拒绝** ——
    #    员工的说法千变万化（「我表现怎么样」「分数怎么算出来的」
    #    「上个月我拿了多少分」），关键词不可能穷举。
    #
    #    早期版本在这里回一张「请发送 xxx」的提示卡，
    #    用户收到后只会觉得**机器人坏了** —— 那是最差的体验。
    #
    #    ⇒ 现在改成：没命中也**照样进问答**，
    #      让 L3 模型自己判断「这个问题我能不能答」。
    #
    #    ⚠️ **放宽的是识别，不是权限**：
    #      真正的范围收敛在 ``qa_permission.resolve_scope``，
    #      按员工身份解析 ``allowed_tools``；问答链路**只读**。
    #
    #    ⚠️ **写操作意图优先级更高**（见上），
    #      「填月计划时顺便看看我的绩效」不会被兜底抢走。
    if intent == feishu_bot.INTENT_NONE:
        # 只记意图与长度，**不记原文** ——
        # 员工可能顺手问些隐私内容，日志会长期留存。
        logger.info(
            "飞书消息未命中关键词，走模型兜底：intent=%s 文本长度=%d 疑似绩效相关=%s",
            intent,
            len(text),
            feishu_intent.is_performance_related(text),
        )
        intent = feishu_bot.INTENT_PERFORMANCE_QA
    elif intent not in (
        feishu_bot.INTENT_MONTH_PLAN,
        feishu_bot.INTENT_MANUAL_METRIC,
        feishu_scheme.INTENT,
        feishu_bot.INTENT_PERFORMANCE_QA,
    ):
        # ⚠️ **兜底之后不该再有这个分支** ——
        #    保留它是为了「将来新增意图时忘了加进上面的名单」，
        #    那种情况下宁可回提示卡，也不要**静默**当成问答处理。
        logger.warning(
            "飞书消息命中未处理的意图 %s，回提示卡（文本长度=%d）",
            intent,
            len(text),
        )
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_prompt_card(
                session_id=session.id,
                plan_month=feishu_bot._current_month(),
                hint="发送「考核填报」填写部门考核方案；发送「填月计划」填写月计划；直接问绩效相关问题（如「我的分数怎么算的」）即可。",
            ),
        )
        return {"code": 0, "message": "未处理的意图，已给出提示"}

    # ── 绑定员工 ─────────────────────────────────────────────
    employee = FeishuIdentityService(db).resolve_employee(
            open_id=open_id,
            user_id=message["user_id"],
    )
    if employee is None:
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(
                "找不到你的账号。\n\n"
                "请先让 HR 在系统里完成飞书账号绑定，再来填表。"
            ),
        )
        return {"code": 0, "message": "未绑定员工"}

    # ── ★ BP-2 / P3：飞书端人工填报 ──────────────────────────
    # ⚠️ **放在 QA 之前** —— 填报是**写操作**，优先级高于只读问答。
    if intent == feishu_bot.INTENT_MANUAL_METRIC:
        from ..services import feishu_manual_metric as fmm
        from ..services.department_modules import (
            PAN_WORKLOAD_MODULE,
            has_module,
            module_guard_message,
        )

        # ⚠️⚠️ **板块隔离（2026-11-21 补）**
        # 之前**只在前端菜单**做了部门隔离，后端 HTTP 端点有守卫
        # （``pan_workload`` router 级依赖），但**飞书端完全没有**。
        # ⇒ 后果：仓储部 / 客服部的人在飞书发一句「完播率 35%」，
        #   照样能写进 ``pan_manual_metric`` ⇒ **隔离形同虚设**。
        # ⇒ 「看不见入口但照样能写数据」比「完全没做隔离」更危险。
        if not has_module(db, employee, PAN_WORKLOAD_MODULE):
            feishu_bot.send_card(
                client,
                receive_id=open_id,
                card=feishu_card.build_error_card(
                    module_guard_message(PAN_WORKLOAD_MODULE)
                ),
            )
            return {"code": 0, "message": "当前部门未开通该板块"}

        try:
            outcome = fmm.submit_from_text(
                db,
                employee_id=employee.id,
                text=text,
                open_id=open_id,
                chat_id=message["chat_id"],
                message_id=message["message_id"],
            )
            db.commit()
        except fmm.FeishuManualMetricError as exc:
            feishu_bot.send_card(
                client,
                receive_id=open_id,
                card=feishu_card.build_error_card(exc.message),
            )
            return {"code": 0, "message": "填报失败，已给出提示"}

        names = {
            "video_completion_rate": "完播率",
            "video_retention_rate": "视频留存率",
        }
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_card(
                title="数据已记录",
                elements=[
                    feishu_card.build_manual_metric_saved_card(
                        metric_name=names.get(outcome["metric_code"], outcome["metric_code"]),
                        value=outcome["value"],
                        metric_date=outcome["date"],
                    )
                ],
            ),
        )
        return {"code": 0, "message": "已记录"}

    # ── ★ BP-2 / P3：直接发截图（最自然的通路）────────────────
    # ⚠️ **截图本身不构成指标** —— 只作证据。
    #    截图里的数字**不自动识别**（识别错就是错误绩效数据）。
    #    ⇒ 存下截图，提示员工补文字说明数值。
    if message.get("image_keys") or message.get("file_keys"):
        from ..services import feishu_manual_metric as fmm
        from ..services.department_modules import (
            PAN_WORKLOAD_MODULE,
            has_module,
            module_guard_message,
        )
        from ..services.feishu_docs import FeishuDocsClient

        # ⚠️ **截图存档同样要过板块守卫** ——
        # 它会写 ``pan_manual_metric_artifact``（挂在人工指标上）
        # ⇒ 同样是「剪辑工作量」板块的数据。
        # ⇒ 不能只在文字填报那一处拦，否则截图这条路是漏的。
        if not has_module(db, employee, PAN_WORKLOAD_MODULE):
            feishu_bot.send_card(
                client,
                receive_id=open_id,
                card=feishu_card.build_error_card(
                    module_guard_message(PAN_WORKLOAD_MODULE)
                ),
            )
            return {"code": 0, "message": "当前部门未开通该板块"}

        image_key = (message.get("image_keys") or [""])[0]
        file_key = (message.get("file_keys") or [""])[0]
        resource_type = "image" if image_key else "file"
        key = image_key or file_key
        try:
            docs = FeishuDocsClient(client)
            content, filename = docs.fetch_message_resource(
                message["message_id"], key, resource_type=resource_type
            )
            outcome = fmm.store_evidence(
                db,
                employee_id=employee.id,
                message_id=message["message_id"],
                image_key=image_key,
                file_key=file_key,
                content=content,
                filename=filename,
                open_id=open_id,
                chat_id=message["chat_id"],
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("飞书截图存档失败：%s", exc)
            feishu_bot.send_card(
                client,
                receive_id=open_id,
                card=feishu_card.build_error_card(
                    "截图收到了，但保存失败，请稍后重试或改用网页端上传。"
                ),
            )
            return {"code": 0, "message": "截图存档失败"}

        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_card(
                title="截图已收到",
                elements=[
                    feishu_card.build_evidence_saved_card(
                        has_metric=not outcome.get("pending"),
                        metric_date="",
                    )
                ],
            ),
        )
        return {"code": 0, "message": "截图已存档"}

    if intent == feishu_bot.INTENT_PERFORMANCE_QA:
        from ..services import conversation as conversation_mod
        from ..services import feishu_qa_ai, qa_subject_matcher

        try:
            # ── P0-5/P0-6：读出会话上下文 ──────────────────────
            # ⚠️ **先读上下文，再决定周期** ——
            #    用户说「那扣分原因呢」时，消息里**没有**月份，
            #    要从上一轮的上下文继承。
            # ⚠️ ``load_state`` 内部会做**空闲清空** ——
            #    隔了半小时再问，周期与主体都不继承。
            state = conversation_mod.load_state(session, asker_id=employee.id)

            period = (
                feishu_bot.extract_plan_month(text)
                or state.period
                or feishu_bot._current_month()
            )

            # ── ★ P0-8：同事名候选匹配 ─────────────────────────
            # ⚠️ **候选集先按权限收窄，再匹配名字** ——
            #    越权的人**根本不在候选集里**，
            #    所以「没找到」与「无权查看」在用户看来完全一样，
            #    不泄露任何越权信息。
            match = qa_subject_matcher.match_subject(db, employee, text)

            if match.ambiguous:
                # ⚠️ **多个候选 → 让用户选**，绝不猜。
                #    猜错就是答错人的绩效。
                # ⚠️ question 要**跟着卡片走** —— 用户点完按钮后，
                #    我们要用当初那个问题继续问。
                card = feishu_card.build_qa_subject_picker_card(
                    candidates=[c.to_dict() for c in match.candidates],
                    question=text,
                    period=period,
                )
                feishu_bot.send_card(client, receive_id=open_id, card=card)
                return {"code": 0, "message": "已发送查询对象选择卡片"}

            # ⚠️ 单个候选 ⇒ 直接用它；无候选 ⇒ 查自己。
            subject_id = match.single.employee_id if match.single else None
            outcome = await feishu_qa_ai.answer_ai(
                db, employee, text, period,
                subject_id=subject_id,
                conversation=state,
            )
            # ── P0-6：写回上下文（周期 + 任务）──────────────
            # ⚠️ P0-8：主体也写回 —— 用户下一轮说「他的扣分原因呢」
            #    时，能接着查同一个人。
            conversation_mod.save_state(
                session, state, task_id=outcome.task_id, period=outcome.period,
                subject_id=outcome.subject_id,
                subject_name=outcome.subject_name,
            )
            if outcome.candidates:
                # ⚠️ 同月多个任务 → **让用户选**，绝不猜。
                #    ⚠️ question 要**跟着卡片走** —— 用户点完按钮后，
                #    我们要用当初那个问题继续问。
                card = feishu_card.build_qa_task_picker_card(
                    candidates=outcome.candidates,
                    period=period,
                    question=text,
                )
            else:
                card = feishu_card.build_qa_ai_card(
                    answer=outcome.text,
                    sources=outcome.sources,
                    citation_count=outcome.citation_count,
                    degraded=outcome.degraded,
                    dropped_citations=outcome.dropped_citations,
                    period=period,
                )
        except HTTPException as exc:
            card = feishu_card.build_error_card(str(exc.detail))
        feishu_bot.send_card(client, receive_id=open_id, card=card)
        return {"code": 0, "message": "已发送绩效查询结果"}

    feishu_bot.bind_employee(db, session, employee)

    if intent == feishu_scheme.INTENT:
        session.expires_at = feishu_bot._now() + feishu_bot._session_ttl()
        try:
            card = feishu_scheme.start(db, session, employee, text)
        except HTTPException as exc:
            db.rollback()
            card = feishu_card.build_error_card(str(exc.detail))
        feishu_bot.send_card(client, receive_id=open_id, card=card)
        return {"code": 0, "message": "已发送考核填报卡片"}

    plan_month = feishu_bot.extract_plan_month(text) or feishu_bot._current_month()
    session.intent = feishu_bot.INTENT_MONTH_PLAN
    session.plan_id = None
    session.collected = {}
    session.state = feishu_bot.STATE_COLLECTING
    session.plan_month = plan_month
    session.last_message_id = message["message_id"] or None
    session.expires_at = feishu_bot._now() + feishu_bot._session_ttl()
    db.flush()

    card = feishu_bot.build_month_plan_card_for_session(session, plan_month=plan_month)
    feishu_bot.send_card(client, receive_id=open_id, card=card)

    return {"code": 0, "message": "已发送填表卡片"}


async def _handle_qa_select_task(
    db: Session,
    client: FeishuClient,
    action: dict[str, Any],
) -> dict[str, Any]:
    """处理「选择要查询的考核任务」按钮。

    ⚠️ **归属校验换成「任务归属校验」**
    ------------------------------
    填表卡片靠 ``session_id`` 校验归属；问答选择卡没有会话，
    所以必须**用点击者自己的员工身份**去校验：

    ① 解析点击者 → 员工；
    ② 用**该员工**去解析任务范围（``qa_permission.resolve_scope``）；
    ③ 权限不够 → 403 卡片。

    ⇒ 卡片被转发给 B，B 点下去查到的也是 **B 有权查的任务**，
    不会泄露 A 的绩效。

    ⚠️ **question 从卡片载荷里带过来**
    -------------------------------
    用户先问了问题 → 系统发现多任务 → 给选择卡 →
    点某个按钮 → 用**当初那个问题**继续问。
    所以按钮的 value 里必须带着 question。
    """
    from ..services import conversation as conversation_mod
    from ..services import feishu_qa_ai

    open_id = action["open_id"]
    task_id = str(action["value"].get("task_id") or "").strip()
    period = str(action["value"].get("period") or "").strip()
    question = str(action["value"].get("question") or "").strip()

    if not open_id:
        logger.warning("飞书绩效问答选择任务回调缺少 open_id，已忽略")
        return {"code": 0, "message": "回调参数不完整，已忽略"}

    if not task_id:
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card("未指定考核任务，请重新提问。"),
        )
        return {"code": 0, "message": "未指定任务"}

    employee = FeishuIdentityService(db).resolve_employee(
        open_id=open_id,
        user_id=action.get("user_id") or "",
    )
    if employee is None:
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(
                "找不到你的账号。\n\n请先让 HR 在系统里完成飞书账号绑定。"
            ),
        )
        return {"code": 0, "message": "未绑定员工"}

    try:
        # ⚠️ P0-5：这条路径也要带上下文 ——
        #    用户选完任务后继续问「那扣分原因呢」，上下文不能丢。
        bot_session = feishu_bot.get_or_create_session(
            db, open_id=open_id, app_id=settings.feishu_app_id,
        )
        state = conversation_mod.load_state(bot_session, asker_id=employee.id)
        outcome = await feishu_qa_ai.answer_ai(
            db,
            employee,
            question or "我的绩效是多少分？",
            period,
            task_id=task_id,
            conversation=state,
        )
        conversation_mod.save_state(
            bot_session, state, task_id=outcome.task_id or task_id,
            period=outcome.period or period,
        )
        card = feishu_card.build_qa_ai_card(
            answer=outcome.text,
            sources=outcome.sources,
            citation_count=outcome.citation_count,
            degraded=outcome.degraded,
            dropped_citations=outcome.dropped_citations,
            period=period,
        )
    except HTTPException as exc:
        card = _qa_error_card(exc)

    feishu_bot.send_card(client, receive_id=open_id, card=card)
    return {"code": 0, "message": "已发送绩效查询结果"}


async def _handle_qa_select_subject(
    db: Session,
    client: FeishuClient,
    action: dict[str, Any],
) -> dict[str, Any]:
    """处理「选择要查询的同事」按钮（P0-8）。

    ⚠️ **归属校验换成「目标人授权校验」**
    ----------------------------------
    卡片可以转发 —— 必须用**点击者自己的员工身份**去校验目标人：

    ① 解析点击者 → 员工；
    ② 用该员工去判定「他能不能查这个人」
       （``qa_authorization.authorize_qa_subject`` 硬闸门）；
    ③ 不通过 → 403 卡片（统一文案，不泄露目标人信息）。

    ⇒ 卡片被转发给 B，B 点下去查到的也是 **B 有权查的人**，
    不会泄露 A 能查但 B 不能查的同事绩效。

    ⚠️ **question 从卡片载荷里带过来**
    --------------------------------
    用户先问了问题 → 系统发现多个候选 → 给选择卡 →
    点某个按钮 → 用**当初那个问题**继续问。
    所以按钮的 value 里必须带着 question。
    """
    from ..services import conversation as conversation_mod
    from ..services import feishu_qa_ai

    open_id = action["open_id"]
    subject_id = str(action["value"].get("subject_id") or "").strip()
    subject_name = str(action["value"].get("subject_name") or "").strip()
    period = str(action["value"].get("period") or "").strip()
    question = str(action["value"].get("question") or "").strip()

    if not open_id:
        logger.warning("飞书绩效问答选择同事回调缺少 open_id，已忽略")
        return {"code": 0, "message": "回调参数不完整，已忽略"}

    if not subject_id:
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card("未指定查询对象，请重新提问。"),
        )
        return {"code": 0, "message": "未指定查询对象"}

    employee = FeishuIdentityService(db).resolve_employee(
        open_id=open_id,
        user_id=action.get("user_id") or "",
    )
    if employee is None:
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(
                "找不到你的账号。\n\n请先让 HR 在系统里完成飞书账号绑定。"
            ),
        )
        return {"code": 0, "message": "未绑定员工"}

    try:
        # ⚠️ ★ P0-8：**先判定点击者能不能查这个人**，再进问答。
        #
        # ⚠️ **为什么要在这一层就拦**
        # --------------------------
        # 卡片可以转发 —— 员工甲的候选卡被转发给员工丁，
        # 员工丁点「员工丙」按钮时，载荷里的 subject_id 依然是员工丙。
        #
        # 真正的硬闸门在 ``qa_authorization.authorize_qa_subject``（服务层），
        # 它一定会拒绝越权 —— **但**那已经在问答链路里了。
        # ⇒ 在这里**提前**拦一道，是**纵深防御**：
        #   ① 越权请求**不进**问答链路（不生成任何 AI 调用）；
        #   ② 错误文案统一，**不泄露**目标人存在与否；
        #   ③ 万一下游判定被改坏，这里仍是最后一道闸。
        from ..domain.models import Employee as _Employee
        from ..services import qa_authorization

        subject = db.get(_Employee, subject_id)
        if subject is None:
            # ⚠️ **「查无此人」与「无权查看」给同一张卡** ——
            #    分开会让用户通过文案差异推断出「此人存在但我不能看」。
            feishu_bot.send_card(
                client,
                receive_id=open_id,
                card=feishu_card.build_qa_denied_card(
                    message="该查询对象不在可见范围内。",
                    guidance=(
                        "绩效数据按组织范围隔离：同部门同级别同事可互查，"
                        "跨部门、跨级或上级不在此范围内。"
                    ),
                ),
            )
            return {"code": 0, "message": "查询对象不可见"}

        decision = qa_authorization.authorize_qa_subject(db, employee, subject)
        if not decision.allowed:
            # ⚠️ 文案**统一**为「不在可见范围内」——
            #    不能说"你无权查他"（泄露存在）或"他不存在"（也泄露）。
            logger.warning(
                "飞书问答选同事被拒：点击者=%s 目标=%s 级别=%s",
                employee.id, subject.id, decision.audit_level,
            )
            # ⚠️ P0-10：**越权尝试落库审计** ——
            #    这条路径在问答链路**之前**就拦下了，
            #    ⇒ ``qa_authorization.resolve_scope`` 里的审计**不会**触发。
            #    ⇒ 必须在这里单独记一条，否则这条路径是审计盲区。
            from ..services import qa_audit

            qa_audit.write_qa_denial_audit(
                db,
                actor_id=employee.id,
                subject_id=subject.id,
                reason="out_of_scope",
                period=period,
            )
            feishu_bot.send_card(
                client,
                receive_id=open_id,
                card=feishu_card.build_qa_denied_card(
                    message=decision.user_message or "该查询对象不在可见范围内。",
                    guidance=decision.guidance,
                ),
            )
            return {"code": 0, "message": "查询对象不可见"}

        # ⚠️ P0-8：这条路径也要带上下文 ——
        #    用户选完同事后继续问「他的扣分原因呢」，上下文不能丢。
        bot_session = feishu_bot.get_or_create_session(
            db, open_id=open_id, app_id=settings.feishu_app_id,
        )
        state = conversation_mod.load_state(bot_session, asker_id=employee.id)
        outcome = await feishu_qa_ai.answer_ai(
            db,
            employee,
            question or "他的绩效是多少分？",
            period,
            subject_id=subject_id,
            conversation=state,
        )
        conversation_mod.save_state(
            bot_session, state, task_id=outcome.task_id,
            period=outcome.period or period,
            # ⚠️ 主体写回 —— 下一轮「他的扣分原因呢」能接着查同一个人。
            subject_id=outcome.subject_id or subject_id,
            subject_name=outcome.subject_name or subject_name,
        )
        if outcome.candidates:
            # ⚠️ 目标人同月多个任务 → 仍然让用户选任务，绝不猜。
            card = feishu_card.build_qa_task_picker_card(
                candidates=outcome.candidates,
                period=period,
                question=question,
            )
        else:
            card = feishu_card.build_qa_ai_card(
                answer=outcome.text,
                sources=outcome.sources,
                citation_count=outcome.citation_count,
                degraded=outcome.degraded,
                dropped_citations=outcome.dropped_citations,
                period=period,
            )
    except HTTPException as exc:
        card = _qa_error_card(exc)

    feishu_bot.send_card(client, receive_id=open_id, card=card)
    return {"code": 0, "message": "已发送绩效查询结果"}


async def _handle_qa_select_subject(
    db: Session,
    client: FeishuClient,
    action: dict[str, Any],
) -> dict[str, Any]:
    """处理「选择要查询的同事」按钮（P0-8）。

    ⚠️ **归属校验换成「目标人授权校验」**
    ----------------------------------
    卡片可以转发 —— 必须用**点击者自己的员工身份**去校验目标人：

    ① 解析点击者 → 员工；
    ② 用该员工去判定「他能不能查这个人」
       （``qa_authorization.authorize_qa_subject`` 硬闸门）；
    ③ 不通过 → 403 卡片（统一文案，不泄露目标人信息）。

    ⇒ 卡片被转发给 B，B 点下去查到的也是 **B 有权查的人**，
    不会泄露 A 能查但 B 不能查的同事绩效。

    ⚠️ **question 从卡片载荷里带过来**
    --------------------------------
    用户先问了问题 → 系统发现多个候选 → 给选择卡 →
    点某个按钮 → 用**当初那个问题**继续问。
    所以按钮的 value 里必须带着 question。
    """
    from ..services import conversation as conversation_mod
    from ..services import feishu_qa_ai

    open_id = action["open_id"]
    subject_id = str(action["value"].get("subject_id") or "").strip()
    subject_name = str(action["value"].get("subject_name") or "").strip()
    period = str(action["value"].get("period") or "").strip()
    question = str(action["value"].get("question") or "").strip()

    if not open_id:
        logger.warning("飞书绩效问答选择同事回调缺少 open_id，已忽略")
        return {"code": 0, "message": "回调参数不完整，已忽略"}

    if not subject_id:
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card("未指定查询对象，请重新提问。"),
        )
        return {"code": 0, "message": "未指定查询对象"}

    employee = FeishuIdentityService(db).resolve_employee(
        open_id=open_id,
        user_id=action.get("user_id") or "",
    )
    if employee is None:
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(
                "找不到你的账号。\n\n请先让 HR 在系统里完成飞书账号绑定。"
            ),
        )
        return {"code": 0, "message": "未绑定员工"}

    try:
        # ⚠️ ★ P0-8：**先判定点击者能不能查这个人**，再进问答。
        #
        # ⚠️ **为什么要在这一层就拦**
        # --------------------------
        # 卡片可以转发 —— 员工甲的候选卡被转发给员工丁，
        # 员工丁点「员工丙」按钮时，载荷里的 subject_id 依然是员工丙。
        #
        # 真正的硬闸门在 ``qa_authorization.authorize_qa_subject``（服务层），
        # 它一定会拒绝越权 —— **但**那已经在问答链路里了。
        # ⇒ 在这里**提前**拦一道，是**纵深防御**：
        #   ① 越权请求**不进**问答链路（不生成任何 AI 调用）；
        #   ② 错误文案统一，**不泄露**目标人存在与否；
        #   ③ 万一下游判定被改坏，这里仍是最后一道闸。
        from ..domain.models import Employee as _Employee
        from ..services import qa_authorization

        subject = db.get(_Employee, subject_id)
        if subject is None:
            feishu_bot.send_card(
                client,
                receive_id=open_id,
                card=feishu_card.build_error_card("该查询对象不在可见范围内。"),
            )
            return {"code": 0, "message": "查询对象不可见"}

        decision = qa_authorization.authorize_qa_subject(db, employee, subject)
        if not decision.allowed:
            # ⚠️ 文案**统一**为「不在可见范围内」——
            #    不能说"你无权查他"（泄露存在）或"他不存在"（也泄露）。
            logger.warning(
                "飞书问答选同事被拒：点击者=%s 目标=%s 级别=%s",
                employee.id, subject.id, decision.audit_level,
            )
            feishu_bot.send_card(
                client,
                receive_id=open_id,
                card=feishu_card.build_error_card("该查询对象不在可见范围内。"),
            )
            return {"code": 0, "message": "查询对象不可见"}

        # ⚠️ P0-8：这条路径也要带上下文 ——
        #    用户选完同事后继续问「他的扣分原因呢」，上下文不能丢。
        bot_session = feishu_bot.get_or_create_session(
            db, open_id=open_id, app_id=settings.feishu_app_id,
        )
        state = conversation_mod.load_state(bot_session, asker_id=employee.id)
        outcome = await feishu_qa_ai.answer_ai(
            db,
            employee,
            question or "他的绩效是多少分？",
            period,
            subject_id=subject_id,
            conversation=state,
        )
        conversation_mod.save_state(
            bot_session, state, task_id=outcome.task_id,
            period=outcome.period or period,
            # ⚠️ 主体写回 —— 下一轮「他的扣分原因呢」能接着查同一个人。
            subject_id=outcome.subject_id or subject_id,
            subject_name=outcome.subject_name or subject_name,
        )
        if outcome.candidates:
            # ⚠️ 目标人同月多个任务 → 仍然让用户选任务，绝不猜。
            card = feishu_card.build_qa_task_picker_card(
                candidates=outcome.candidates,
                period=period,
                question=question,
            )
        else:
            card = feishu_card.build_qa_ai_card(
                answer=outcome.text,
                sources=outcome.sources,
                citation_count=outcome.citation_count,
                degraded=outcome.degraded,
                dropped_citations=outcome.dropped_citations,
                period=period,
            )
    except HTTPException as exc:
        card = feishu_card.build_error_card(str(exc.detail))

    feishu_bot.send_card(client, receive_id=open_id, card=card)
    return {"code": 0, "message": "已发送绩效查询结果"}


async def _handle_daily_metric_submit(
    db: Session,
    client: FeishuClient,
    action: dict[str, Any],
) -> dict[str, Any]:
    """处理「每日填报表单」的提交（2026-11-23）。

    ⚠️ **归属校验是本函数的核心**
    --------------------------
    这张卡是**系统主动推送**的，可以被转发。必须确认：

    1. **点击者能解析出员工** —— 解析不出就是没绑账号；
    2. **点击者 == 卡片上的目标人** —— 否则「B 替 A 填」；
    3. **点击者的部门开通了「剪辑工作量」板块** —— 否则是外部人乱填；
    4. **数值能解析** —— 解析不出**绝不猜**（猜错就是错误绩效数据）。

    ⚠️ **部分填报也接受**
    ------------------
    员工可能只填了完播率、没填留存率 ⇒ 已填的照存，
    没填的下次提醒再问。**不因为「不完整」而丢弃已填的数据**。
    """
    from ..services.department_modules import (
        PAN_WORKLOAD_MODULE,
        has_module,
        module_guard_message,
    )
    from ..services.feishu_daily_metric_push import METRIC_LABELS
    from ..services.feishu_manual_metric import parse_count, parse_rate
    from ..services.pan_manual_metric_service import (
        COUNT_METRIC_CODES,
        SOURCE_FEISHU,
        PanManualMetricError,
        PanManualMetricService,
    )

    open_id = action["open_id"]
    value = action["value"]
    form_value = action["form_value"]

    target_employee_id = str(value.get("employee_id") or "").strip()
    metric_date = str(value.get("metric_date") or "").strip()

    if not open_id:
        return {"code": 0, "message": "回调参数不完整，已忽略"}

    employee = FeishuIdentityService(db).resolve_employee(
        open_id=open_id,
        user_id=action.get("user_id") or "",
    )
    if employee is None:
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(
                "找不到你的账号。\n\n请先让 HR 在系统里完成飞书账号绑定。"
            ),
        )
        return {"code": 0, "message": "未绑定员工"}

    # ⚠️ **归属校验** —— 卡片可以被转发，点的人必须就是被填的人。
    #
    # ⚠️ **为什么排在板块校验之前**
    #   仓储部的人点了剪辑的卡，**真正的原因是「这张卡不是你的」**，
    #   而不是「你的部门没开通板块」。
    #   ⇒ 先报更精确的那个，用户才知道该怎么做（用自己账号填）。
    if target_employee_id and target_employee_id != employee.id:
        logger.warning(
            "飞书每日填报回调归属不匹配：target=%s clicker=%s",
            target_employee_id, employee.id,
        )
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(
                "这张填报卡不能替别人提交。\n\n请在系统里用**自己的**账号填报。"
            ),
        )
        return {"code": 0, "message": "归属不匹配，已忽略"}

    # ⚠️ **板块隔离** —— 别的部门的人不能填剪辑的数据。
    #
    # ⚠️ **为什么排在归属校验之后**
    #   归属对了但板块没开 ⇒ 是「你所在的部门没这个业务」，
    #   与「卡不是你的」是两回事 ⇒ 提示语必须分开。
    if not has_module(db, employee, PAN_WORKLOAD_MODULE):
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(
                module_guard_message(PAN_WORKLOAD_MODULE)
            ),
        )
        return {"code": 0, "message": "当前部门未开通该板块"}

    if not metric_date:
        metric_date = date.today().isoformat()

    # ⚠️⚠️ **该填哪些指标由岗位口径决定**（2026-11-23 角色倒转）
    # ------------------------------------------------------
    # 剪辑填**数量**：普通视频 / OOTD / 智能体。
    # ⇒ **不能写死指标列表** —— 口径表配什么就填什么。
    from ..services.pan_workload_policy import reporter_metric_codes

    allowed_codes = reporter_metric_codes(db, employee)
    if not allowed_codes:
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(
                "你的岗位在这块业务里没有填报项。\n\n"
                "如果这不对，请让 HR 检查岗位口径配置。"
            ),
        )
        return {"code": 0, "message": "该岗位无填报项"}

    # ⚠️ **逐个指标解析** —— 认不出就跳过该指标，不猜。
    svc = PanManualMetricService(db)
    #: ⚠️ **带上指标码** —— 回执要按类型格式化（条数 vs 比率）
    saved: list[tuple[str, float, str]] = []
    errors: list[str] = []

    # ⚠️ **备注**（2026-11-23）—— **选填**，存在**第一条**指标上。
    #
    # ⚠️ **为什么不单独建表**
    # 备注是「这次填报的补充说明」，与指标同生共死 ⇒
    # 挂在指标行的 ``remark`` 字段上（该字段已存在）。
    # ⇒ 只挂在**第一条**上，避免重复存 N 遍。
    #
    # ⚠️ **先 strip 再判空** —— 只打空格 = 没填 ⇒ 留着占位文案没意义。
    remark_text = str(form_value.get("remark") or "").strip()[:255]

    for code in allowed_codes:
        label = METRIC_LABELS.get(code, code)
        raw = form_value.get(code)
        if raw is None or str(raw).strip() == "":
            continue
        if code in COUNT_METRIC_CODES:
            parsed = parse_count(str(raw))
            if parsed is None:
                errors.append(f"{label}：没认出条数（填整数，例如 8）")
                continue
        else:
            parsed = parse_rate(str(raw))
            if parsed is None:
                errors.append(f"{label}：没认出数值（写成 35% 或 0.35）")
                continue
        try:
            svc.submit(
                employee_id=employee.id,
                metric_date=metric_date,
                metric_code=code,
                metric_value=parsed,
                scope="day",
                source=SOURCE_FEISHU,
                created_by=employee.id,
                # ⚠️ **备注挂在第一条上** —— 后面几行只记「来自飞书表单」
                remark=remark_text if not saved else "",
                feishu_open_id=open_id,
            )
        except PanManualMetricError as exc:
            errors.append(f"{label}：{exc.message}")
            continue
        saved.append((label, parsed, code))

    db.flush()

    # ⚠️ **一个都没存进去** ⇒ 报错，不发「已提交」回执。
    if not saved:
        detail = "\n".join(f"· {item}" for item in errors) or "表单里没有可识别的数值。"
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(f"没保存成功：\n{detail}"),
        )
        return {"code": 0, "message": "填报失败"}

    # ⚠️ **部分失败** ⇒ 已存的照存，但要说清哪些没存。
    if errors:
        from ..services.feishu_daily_metric_push import day_label, format_metric

        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(
                "部分已保存：\n"
                + "\n".join(
                    f"· {label}：{format_metric(code, value)}"
                    for label, value, code in saved
                )
                + "\n\n没存成功：\n"
                + "\n".join(f"· {item}" for item in errors)
            ),
        )
        return {"code": 0, "message": "部分已保存"}

    from ..services.feishu_daily_metric_push import day_label

    # ⚠️⚠️⚠️ **先提交，再发回执**（2026-11-23 真机踩坑后修正）
    # ---------------------------------------------------------
    # ⚠️ **原来的顺序会丢数据**
    #    ``send_card`` 在 ``db.commit()`` **之前**调用
    #    ⇒ 回执卡一旦发送失败（结构错 / 网络问题）就抛异常
    #    ⇒ 外层 ``db.rollback()`` ⇒ **已经存进去的数据全没了**
    #    ⇒ 用户看到「显示出错了重试」，重试又失败 ⇒ 永远填不进去。
    #
    # ⚠️ **实测**：回执卡用了非法的 ``plain_text`` body 元素
    #    ⇒ 数据存了但回执发不出去 ⇒ 异常 ⇒ 回滚 ⇒ 库里空空如也。
    #
    # ⇒ **数据是主，回执是次** —— 先把数据落库，再尽力发回执。
    # ⇒ 回执失败只**记日志**，不能让它推翻已完成的业务动作。
    db.commit()

    try:
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_daily_metric_saved_card(
                day_text=day_label(metric_date),
                saved=[(label, value) for label, value, _ in saved],
                remark=remark_text,
                # ⚠️ **数量类指标显示「8 条」**，不是「8.0%」
                kind="count" if all(code in COUNT_METRIC_CODES for _, _, code in saved) else "rate",
            ),
        )
    except Exception:  # noqa: BLE001
        # ⚠️ **回执发不出去 ≠ 填报失败** —— 数据已经落库了。
        logger.warning("每日填报回执卡发送失败（数据已保存）", exc_info=True)

    return {"code": 0, "message": "今日数据已提交"}


async def _handle_leader_metric_submit(
    db: Session,
    client: FeishuClient,
    action: dict[str, Any],
) -> dict[str, Any]:
    """处理「主管提交效果指标」（2026-11-23 角色倒转）。

    ⚠️ **为什么主管也要能提交**
    --------------------------
    业务确认：**剪辑只填数量**（普通视频 / OOTD / 智能体），
    **完播率 / 视频留存率由主管填** ⇒
    ⇒ 这一栏如果没人填，整条效果指标链路是空的。

    ⚠️ **主管填的是「效果指标」，不是替下属填数量**
    --------------------------------------------
    ⚠️ **主管不能替剪辑填条数** —— 那正是「A 的产出记到 B 头上」
    ⇒ ⇒ 这里**只接受比率类指标**（按主管岗位口径取）。

    ⚠️ **归属校验（三重）**
    --------------------
    1. **点击者能解析出员工**；
    2. **点击者的岗位口径是「审阅人」** —— 否则不该收到这张卡；
    3. **目标剪辑在点击者的管理范围内** —— 否则就是越权填别人的绩效。

    ⚠️ **目标人靠姓名/工号识别**（飞书表单没有下拉控件）
    ⚠️ **认不出就拒绝** —— 绝不瞎猜一个目标人。
    """
    from ..services.department_modules import (
        PAN_WORKLOAD_MODULE,
        has_module,
        module_guard_message,
    )
    from ..services.feishu_daily_metric_push import METRIC_LABELS
    from ..services.feishu_manual_metric import parse_rate
    from ..services.pan_manual_metric_service import (
        SOURCE_FEISHU,
        PanManualMetricError,
        PanManualMetricService,
    )
    from ..services.pan_workload_policy import reviewer_metric_codes

    open_id = action["open_id"]
    value = action["value"]
    form_value = action["form_value"]

    metric_date = str(value.get("metric_date") or "").strip() or date.today().isoformat()

    # ── ① 点击者身份 ────────────────────────────────────
    employee = FeishuIdentityService(db).resolve_employee(
        open_id=open_id,
        user_id=action.get("user_id") or "",
    )
    if employee is None:
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(
                "找不到你的账号。\n\n请先让 HR 在系统里完成飞书账号绑定。"
            ),
        )
        return {"code": 0, "message": "未绑定员工"}

    # ── ② 板块隔离 ──────────────────────────────────────
    if not has_module(db, employee, PAN_WORKLOAD_MODULE):
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(
                module_guard_message(PAN_WORKLOAD_MODULE)
            ),
        )
        return {"code": 0, "message": "当前部门未开通该板块"}

    # ── ③ 岗位口径必须是「审阅人」 ──────────────────────
    allowed_codes = reviewer_metric_codes(db, employee)
    if not allowed_codes:
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(
                "你的岗位在这块业务里没有审核填报项。\n\n"
                "如果这不对，请让 HR 检查岗位口径配置。"
            ),
        )
        return {"code": 0, "message": "该岗位无审核填报项"}

    # ── ④ 目标剪辑：靠姓名/工号识别 ────────────────────
    target_key = str(form_value.get("target_employee") or "").strip()
    if not target_key:
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card("请填「填给哪位剪辑」。"),
        )
        return {"code": 0, "message": "缺少目标人"}

    target = _find_employee_by_name_or_id(db, target_key)
    if target is None:
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(
                f"没找到「{target_key}」。\n\n请填管理范围内的剪辑姓名或工号。"
            ),
        )
        return {"code": 0, "message": "目标人认不出"}

    # ── ⑤ ★ 目标人必须在管理范围内 ─────────────────────
    scope = _leader_scope_employee_ids(db, employee)
    if scope is not None and target.id not in scope:
        logger.warning(
            "飞书主管效果指标回调越权：leader=%s target=%s",
            employee.id, target.id,
        )
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(
                "这张卡只能给你管理范围内的剪辑填指标。\n\n"
                f"「{target.name}」不在你的管理范围内。"
            ),
        )
        return {"code": 0, "message": "目标人超出管理范围"}

    # ── ⑥ 逐个指标解析 ─────────────────────────────────
    svc = PanManualMetricService(db)
    saved: list[tuple[str, float]] = []
    errors: list[str] = []

    for code in allowed_codes:
        label = METRIC_LABELS.get(code, code)
        raw = form_value.get(code)
        if raw is None or str(raw).strip() == "":
            continue
        parsed = parse_rate(str(raw))
        if parsed is None:
            errors.append(f"{label}：没认出数值（写成 35% 或 0.35）")
            continue
        try:
            svc.submit(
                employee_id=target.id,
                metric_date=metric_date,
                metric_code=code,
                metric_value=parsed,
                scope="day",
                source=SOURCE_FEISHU,
                created_by=employee.id,
                remark=f"主管 {employee.name or employee.id} 填写效果指标",
                feishu_open_id=open_id,
            )
        except PanManualMetricError as exc:
            errors.append(f"{label}：{exc.message}")
            continue
        saved.append((label, parsed))

    db.flush()

    if not saved:
        detail = "\n".join(f"· {item}" for item in errors) or "表单里没有可识别的数值。"
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(f"没保存成功：\n{detail}"),
        )
        return {"code": 0, "message": "填报失败"}

    if errors:
        from ..services.feishu_daily_metric_push import day_label, format_metric

        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(
                "部分已保存：\n"
                + "\n".join(
                    f"· {label}：{format_metric(code, value)}"
                    for label, value in saved
                )
                + "\n\n没存成功：\n"
                + "\n".join(f"· {item}" for item in errors)
            ),
        )
        return {"code": 0, "message": "部分已保存"}

    from ..services.feishu_daily_metric_push import day_label

    # ⚠️⚠️⚠️ **先提交，再发回执** —— 与每日填报同一处坑。
    #    回执失败不能让已落库的数据被外层 ``db.rollback()`` 推翻。
    db.commit()

    try:
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_daily_metric_saved_card(
                day_text=f"{day_label(metric_date)} · {target.name or target.id}",
                saved=saved,
                kind="rate",
            ),
        )
    except Exception:  # noqa: BLE001
        # ⚠️ **回执发不出去 ≠ 填报失败** —— 数据已经落库了。
        logger.warning("主管效果指标回执卡发送失败（数据已保存）", exc_info=True)

    return {"code": 0, "message": "效果指标已提交"}


def _find_employee_by_name_or_id(db: Session, key: str) -> Employee | None:
    """按姓名或工号找员工。

    ⚠️ **认不出返回 ``None``** —— 绝不瞎猜一个目标人
    （猜错 = 把 A 的绩效写到 B 头上）。

    ⚠️ **重名怎么办** —— 返回 ``None``，让调用方回错误卡让主管写清楚。
    """
    from sqlalchemy import or_

    rows = db.scalars(
        select(Employee).where(
            or_(Employee.name == key, Employee.login_account == key, Employee.id == key)
        )
    ).all()
    if len(rows) != 1:
        # ⚠️ **0 个 = 认不出；多个 = 重名 ⇒ 都不猜**
        return None
    return rows[0]


def _leader_scope_employee_ids(db: Session, leader: Employee) -> list[str] | None:
    """算出主管**能填指标**的对象范围。

    ⚠️ **返回 ``None`` = 不限**（HR / 管理员）。
    ⚠️ **范围 = 直属下属 + 汇报线全链下属 + 代理授权 + 所管部门成员**。
    """
    from ..services import access_control

    ctx = access_control.build_access_context(db, leader)
    if ctx.has_permission(PermissionAction.VIEW_ALL_SCORES):
        return None

    allowed: set[str] = {leader.id}
    allowed.update(getattr(ctx, "direct_report_ids", None) or set())
    allowed.update(getattr(ctx, "reporting_line_ids", None) or set())
    allowed.update(getattr(ctx, "delegated_employee_ids", None) or set())

    managed = set(getattr(ctx, "managed_department_ids", None) or [])
    if managed:
        rows = db.scalars(
            select(Employee.id).where(Employee.department_id.in_(managed))
        ).all()
        allowed.update(rows)

    return sorted(allowed)


async def _handle_card_action(
    db: Session,
    client: FeishuClient,
    body: dict[str, Any],
    *,
    event_id: str,
) -> dict[str, Any]:
    """处理卡片交互：校验归属 → 写草稿 → 回执。

    ⚠️ **约束 C3**：``open_id`` 必须与会话记录一致。
   卡片可以转发 —— 不校验就是「B 点了卡片，草稿写进了 A 的名下」。
    """
    action = extract_card_action(body)
    open_id = action["open_id"]
    session_id = str(action["value"].get("session_id") or "").strip()
    action_name = action["action"]

    # ── 绩效问答「选择任务」：**不走会话** ────────────────────
    # ⚠️ 必须**在会话校验之前**处理 ——
    #    问答任务选择卡没有 session_id（它不属于填表会话），
    #    走下面的会话校验会被当成「参数不完整」直接丢掉。
    #
    # ⚠️ 归属校验在这里**换成任务归属校验**：
    #    卡片可以转发 —— 必须确认「点按钮的人」确实有权查这个任务，
    #    否则就是「B 点了卡片，查到了 A 的绩效」。
    if action_name == feishu_card.ACTION_SELECT_TASK:
        return await _handle_qa_select_task(db, client, action)

    # ── 绩效问答「选择同事」（P0-8）──────────────────────────
    # ⚠️ 同样**不走会话** —— 选择卡没有 session_id。
    # ⚠️ **归属校验换成「目标人授权校验」** ——
    #    卡片可以转发，必须用点击者自己的身份重新判定
    #    「他能不能查这个人」。
    if action_name == feishu_card.ACTION_SELECT_SUBJECT:
        return await _handle_qa_select_subject(db, client, action)

    # ── ★ 每日填报表单（2026-11-23）─────────────────────────────
    # ⚠️ **必须在会话校验之前处理** ——
    #    这张卡是**系统主动推送**的，它的 ``session_id`` 是推送时建/复用的会话，
    #    而点击者**未必**是那个会话的主人（卡片可以被转发）。
    #
    # ⚠️ **归属校验在这里换成了「点击者 == 目标人」**：
    #    卡片里的 ``employee_id`` 是**被填的人**；
    #    点击者解析出的员工必须**就是他本人** ——
    #    否则「B 替 A 填」就直接把数据写歪了。
    if action_name == feishu_card.ACTION_SUBMIT_DAILY_METRIC:
        return await _handle_daily_metric_submit(db, client, action)

    # ── ★ 主管提交效果指标（2026-11-23 角色倒转）─────────────
    # ⚠️ **同样必须在会话校验之前处理** —— 系统主动推送的卡，
    #    点击者未必是那个会话的主人（卡片可以被转发）。
    #
    # ⚠️ **归属校验换成「点击者是审阅人 + 目标人在其管理范围内」**
    #    —— 主管**可以**给管理范围内的剪辑填效果指标，
    #    但**不能**替不相关的人填。
    if action_name == feishu_card.ACTION_SUBMIT_LEADER_METRIC:
        return await _handle_leader_metric_submit(db, client, action)

    if not open_id or not session_id:
        event = body.get("event") or {}
        raw_action = event.get("action") or {}
        logger.warning(
            "飞书机器人卡片回调参数缺失：has_open_id=%s has_session_id=%s "
            "event_keys=%s action_keys=%s value_keys=%s form_keys=%s",
            bool(open_id), bool(session_id), sorted(event), sorted(raw_action),
            sorted(action["value"]), sorted(action["form_value"]),
        )
        return {"code": 0, "message": "回调参数不完整，已忽略"}

    # Serialize callbacks so two clicks cannot advance the same card twice.
    from sqlalchemy import update
    db.execute(update(BotSession).where(BotSession.id == session_id)
               .values(updated_at=BotSession.updated_at))
    session = db.get(BotSession, session_id)
    if session is not None:
        db.refresh(session)
    if session is None:
        logger.warning("飞书机器人卡片回调的会话不存在：%s", session_id)
        return {"code": 0, "message": "会话不存在，已忽略"}

    # ⚠️ 约束 C3：归属校验
    if session.open_id != open_id:
        logger.warning(
            "飞书机器人卡片回调 open_id 与会话不一致：session=%s open_id=%s",
            session_id,
            open_id,
        )
        return {"code": 0, "message": "会话归属不匹配，已忽略"}

    generation = action["value"].get("generation")
    if session.last_message_id and generation != session.last_message_id:
        return {"code": 0, "message": "卡片已失效，请使用最新卡片"}

    if action_name == feishu_card.ACTION_CANCEL:
        session.state = feishu_bot.STATE_IDLE
        session.intent = feishu_bot.INTENT_NONE
        session.collected = {}
        db.flush()
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card("已取消本次填表。"),
        )
        return {"code": 0, "message": "已取消"}

    from ..services import feishu_scheme

    if action_name == feishu_scheme.ACTION:
        try:
            card = feishu_scheme.handle(db, session, action["value"], action["form_value"])
        except HTTPException as exc:
            db.rollback()
            card = feishu_card.build_error_card(str(exc.detail))
            feishu_bot.send_card(client, receive_id=open_id, card=card)
            return {"code": 0, "message": str(exc.detail)}
        feishu_bot.send_card(client, receive_id=open_id, card=card)
        return {"code": 0, "message": "考核填报已处理"}

    if action_name != feishu_card.ACTION_SUBMIT_DRAFT:
        # ⚠️ 未知动作**忽略而不报错** —— 否则飞书会反复重推。
        return {"code": 0, "message": "未知动作，已忽略"}

    try:
        result = feishu_bot.apply_card_form_to_draft(db, session, action["form_value"])
    except feishu_bot.FeishuBotError as exc:
        db.rollback()
        logger.warning("飞书机器人写草稿失败：%s", exc)
        feishu_bot.send_card(
            client,
            receive_id=open_id,
            card=feishu_card.build_error_card(str(exc)),
        )
        return {"code": 0, "message": "写草稿失败"}

    receipt = feishu_card.build_draft_saved_card(
        plan_month=result["plan_month"],
        plan_id=result["plan_id"],
        item_count=result["item_count"],
    )
    feishu_bot.send_card(client, receive_id=open_id, card=receipt)

    return {"code": 0, "message": "草稿已保存"}


# ===========================================================================
# 端点
# ===========================================================================


@router.post("/events")
async def receive_bot_event(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """接收飞书机器人事件。

    **无需登录态**（调用方是飞书服务器），但必须通过来源校验。

    响应体遵循飞书 webhook 约定：返回 ``{"code": 0}`` 表示成功。
    """
    body = await _read_verified_body(request)

    # ⚠️ 握手：必须**原样**返回 challenge，否则飞书保存不了回调地址。
    if feishu_event.is_url_verification(body):
        return {"challenge": feishu_event.extract_challenge(body)}

    return await receive_bot_event_body(db, body)


async def receive_bot_event_body(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    """处理**已通过来源校验**的机器人事件体。

    ⚠️ **为什么抽成独立函数**（2026-11-17）：

    飞书「一个应用只有一个请求地址」⇒ 机器人事件与审批事件会打到**同一个**
    端点。所以统一入口（``/approval/events``）需要把机器人事件**转交**到这里。

    ⇒ **复用**而不是复制 —— 复制一份会导致两边行为漂移
    （比如幂等键规则改了只改一处 ⇒ 出现「有的事件被处理两次」）。

    ⚠️ **调用方必须已完成来源校验**（Verification Token / 验签 / 解密）。
       本函数**不再**重复校验 —— 统一入口已经校验过了。

    ⚠️ **async 的原因**：AI 问答要等 L3（最长 35 秒）。
       同步等待会堵住事件循环。
       同步调用方（WebSocket 接收器）请用 `receive_bot_event_body_sync`。
    """
    # ⚠️ 应急开关：机器人关闭时**只握手不处理业务**。
    #   返回 code 0 而不是报错 —— 报错会让飞书反复重推。
    if not settings.feishu_bot_enabled:
        logger.info("飞书机器人已关闭（FEISHU_BOT_ENABLED=false），事件已忽略")
        return {"code": 0, "message": "机器人已关闭"}

    event_type = extract_event_type(body)
    header = body.get("header")
    header = header if isinstance(header, dict) else {}
    event_id = str(header.get("event_id") or body.get("event_id") or "").strip()

    # ── 幂等：同一事件不处理两次 ──────────────────────────────
    # ⚠️ 官方文档明确提醒：消息事件要**用 message_id 去重**，
    #    「do not rely on event_id」—— 飞书重推时 event_id 可能变，
    #    用 message_id 才挡得住「同一条消息被处理两次 → 两条草稿」。
    dedup_key = event_id
    if event_type == EVENT_MESSAGE_RECEIVE:
        message_id = extract_message_event(body)["message_id"]
        if message_id:
            dedup_key = f"msg:{message_id}"

    if dedup_key and feishu_bot.is_duplicate(db, dedup_key):
        logger.info("飞书机器人事件已处理过，跳过：%s", dedup_key)
        return {"code": 0, "message": "事件已处理"}

    client = _client()

    try:
        if event_type == EVENT_CARD_ACTION:
            result = await _handle_card_action(db, client, body, event_id=event_id)
        elif event_type == EVENT_MESSAGE_RECEIVE:
            result = await _handle_message_event(db, client, body)
        else:
            # ⚠️ 其他事件类型**返回成功并忽略** —— 报错会让飞书反复重推。
            logger.info("飞书机器人事件类型未处理：%s", event_type or "-")
            result = {"code": 0, "message": "事件类型未处理"}
    except Exception:
        db.rollback()
        logger.exception("飞书机器人事件处理异常")
        raise HTTPException(status_code=500, detail="机器人事件处理失败") from None

    # ── 记流水（幂等键）──────────────────────────────────────
    if dedup_key:
        feishu_bot.record_message(
            db,
            dedup_key=dedup_key,
            direction="inbound",
            message_type=event_type or "unknown",
            payload={"event_type": event_type},
        )

    db.commit()
    return result


def receive_bot_event_body_sync(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    """同步版 `receive_bot_event_body`（给 WebSocket 接收器用）。

    ⚠️ **为什么需要它**
    ------------------
    ``backend/scripts/run_feishu_ws.py`` 的 SDK 回调是**同步**的
    （飞书 SDK 的 ``on_event`` 不支持 async）。

    ⇒ 这里用 ``asyncio.run`` 在**当前线程**跑一遍事件循环。

    ⚠️ **调用方限制**
    --------------
    只能在**没有正在运行的事件循环**的线程里调用
    （WebSocket 接收器是独立进程/线程，满足）。
    在 FastAPI 的 async 端点里调用会抛
    ``RuntimeError: asyncio.run() cannot be called from a running event loop`` ——
    那种场景请直接 ``await receive_bot_event_body(...)``。
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 没有运行中的事件循环 —— 可以安全地起一个新的。
        return asyncio.run(receive_bot_event_body(db, body))

    # ⚠️ 已经有事件循环在跑（比如在 async 上下文里误调了同步版）。
    #   不能嵌套 asyncio.run —— 只能丢到独立线程里跑。
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(
            asyncio.run, receive_bot_event_body(db, body)
        ).result()


@router.post("/card/actions")
async def receive_card_action(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """接收卡片回调（飞书后台「卡片回调 URL」配成独立地址时用）。

    与 ``/events`` 走**同一套**来源校验与处理逻辑。
    """
    body = await _read_verified_body(request)

    # ⚠️ 握手：必须**原样**返回 challenge，否则飞书保存不了回调地址。
    if feishu_event.is_url_verification(body):
        return {"challenge": feishu_event.extract_challenge(body)}

    # ⚠️ 应急开关：机器人关闭时**只握手不处理业务**。
    if not settings.feishu_bot_enabled:
        logger.info("飞书机器人已关闭（FEISHU_BOT_ENABLED=false），卡片回调已忽略")
        return {"code": 0, "message": "机器人已关闭"}

    event_type = extract_event_type(body)
    header = body.get("header")
    header = header if isinstance(header, dict) else {}
    event_id = str(header.get("event_id") or body.get("event_id") or "").strip()

    if event_id and feishu_bot.is_duplicate(db, event_id):
        logger.info("飞书机器人卡片回调已处理过，跳过：%s", event_id)
        return {"code": 0, "message": "事件已处理"}

    client = _client()

    try:
        result = await _handle_card_action(db, client, body, event_id=event_id)
    except Exception:
        db.rollback()
        logger.exception("飞书机器人卡片回调处理异常")
        raise HTTPException(status_code=500, detail="卡片回调处理失败") from None

    if event_id:
        feishu_bot.record_message(
            db,
            dedup_key=event_id,
            direction="inbound",
            message_type="card_action",
            payload={"event_type": event_type},
        )

    db.commit()
    return result


__all__ = ["router"]
