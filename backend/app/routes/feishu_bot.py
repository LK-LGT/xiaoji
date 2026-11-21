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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..domain.bot_models import BotSession
from ..services import feishu_bot, feishu_card, feishu_event, feishu_intent
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
        from ..services.feishu_docs import FeishuDocsClient

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
        from ..services import feishu_qa_ai

        try:
            period = feishu_bot.extract_plan_month(text) or feishu_bot._current_month()
            outcome = await feishu_qa_ai.answer_ai(db, employee, text, period)
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
        outcome = await feishu_qa_ai.answer_ai(
            db,
            employee,
            question or "我的绩效是多少分？",
            period,
            task_id=task_id,
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
        card = feishu_card.build_error_card(str(exc.detail))

    feishu_bot.send_card(client, receive_id=open_id, card=card)
    return {"code": 0, "message": "已发送绩效查询结果"}


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
