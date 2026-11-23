"""网盘工作量规则引擎（BP-2 / P0）。

把「一个网盘文件」判定成「几条工作量」，全部规则集中在这里，纯函数、无 IO。

⚠️ **口径来源：2026-11-21 真实网盘实测（526 个文件）**
    见 ``docs/短视频剪辑工作量统计-实现方案.md`` §〇·一·二

三条实测铁律（违反任何一条，工作量会直接算错）：

1. **只数 mp4** —— mp4 与 png 是同一条内容的两种格式（封面），
   都数 ⇒ 工作量翻倍。
2. **``-2`` / ``-1`` / ``(1)`` 不是「双条」标记** —— 实测 27 组
   ``X-N`` 与 ``X`` 的 md5 **全部不同**、大小差 20~90MB，
   是「当天第 N 版」的**不同内容**，各算 1 条。
3. **业务日期以目录为准** —— 实测 190 个带日期前缀的文件里 6 个
   与目录日期冲突（如 ``/11.14/11.17-智能体.mp4``），
   文件名日期是随手写的，目录才是归档依据。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date

from .baidu_pan_client import PanFile, parse_period_from_path

# ─────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────

#: **计入工作量的扩展名** —— 刻意**不含** png/jpg（封面图，见铁律 1）
DEFAULT_ALLOWED_EXTENSIONS: tuple[str, ...] = ("mp4", "mov", "m4v", "avi", "mkv", "flv", "wmv")

#: 排除关键词（草稿/工程/返工）
DEFAULT_EXCLUDE_KEYWORDS: tuple[str, ...] = (
    "草稿", "工程", "返工", "废片", "废弃", "作废",
    "rework", "_v2", "_v3", "副本", "prj", "aep",
)

#: 排除的目录路径段
#:
#: ⚠️ ``之前的成品``（2026-11-21 加入）—— ``/口播成品/之前的成品``
#: 是**历史归档目录**（110 个 mp4），不属于当期产出 ⇒ 排除。
DEFAULT_EXCLUDE_DIR_SEGMENTS: tuple[str, ...] = (
    "草稿", "工程", "prj", "aep", "project", "源文件", "素材", "之前的成品",
)

#: 默认标记字典 —— **业务已确认（2026-11-21）：智能体 / OOTD 各算 2 条**
DEFAULT_MARKER_RULES: dict[str, dict] = {
    "智能体": {"weight": 2, "patterns": ["智能体"], "enabled": True},
    "OOTD": {"weight": 2, "patterns": ["ootd"], "enabled": True},
}

#: 文件名里的日期前缀（``11.19 xxx`` / ``11-19-xxx`` / ``11月19日``）
_NAME_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(\d{1,2})[.\-_](\d{1,2})(?!\d)"),
    re.compile(r"^(\d{1,2})月(\d{1,2})日"),
)

#: 序号后缀（``-2`` / ``(1)`` / ``（6）``）—— 用于识别「版本号」而非标记
_VERSION_SUFFIX = re.compile(r"(?:[-_]\d+|\(\d+\)|（\d+）)$")

#: ⚠️ **无法解析业务日期时的显式占位日期**
#:
#: 为什么需要它：``aggregate_daily`` 会跳过 ``business_date`` 为空的行
#: ⇒ 那些文件**永远进不了日汇总** ⇒ 工作量**静默少计**，
#: 而且差额**对不上却查不出原因**（机器判定值 1346 vs 聚合值 1345）。
#:
#: ⇒ 用一个**不可能存在的日期**占位：既不与真实业务日期混淆，
#:    又能在日汇总里**占一行**，让主管看得见、能人工复核。
UNKNOWN_DATE = "0001-01-01"


@dataclass(slots=True)
class MarkerHit:
    """标记命中结果。"""

    #: 命中的规则名列表（如 ``["智能体"]``）
    names: list[str] = field(default_factory=list)
    #: 折算条数（取最高权重，不叠加）
    weight: int = 1
    #: 命中时的规则来源（``default`` / ``by_folder``），用于审计
    source: str = "default"


@dataclass(slots=True)
class FileVerdict:
    """单个文件的判定结果 —— **不丢弃任何文件**。

    ⚠️ 无论是被过滤还是无法解析，都要有 ``exclude_reason`` / ``bind_type``，
    便于面板上高亮「待人工确认」，避免**静默少计**。
    """

    #: 是否计入工作量
    counted: bool
    #: 折算条数（未计入时为 0）
    count_weight: int = 0
    #: 业务日期 YYYY-MM-DD（空 = 无法解析）
    business_date: str = ""
    #: 命中的标记
    markers: MarkerHit = field(default_factory=MarkerHit)
    #: 被排除的原因（``ext_not_allowed`` / ``keyword:xxx`` / ``draft_dir`` …）
    exclude_reason: str = ""
    #: 异常标记（``name_date_conflict``）
    anomaly_flags: list[str] = field(default_factory=list)
    #: 内容指纹（md5，缺失时用 ``name:size`` 兜底）
    content_digest: str = ""


def resolve_marker_rules(
    rules: dict | None,
    folder_path: str = "",
) -> tuple[dict, str]:
    """取出某个目录适用的标记规则。

    ⚠️ **两级结构 + 最长前缀匹配**
    实测发现两人的命名风格差异很大（``11.13 智能体`` vs ``11.14-智能体``），
    一条全局正则会漏配 ⇒ 规则必须「全局默认 + 按目录覆盖」。

    ⚠️ 用**最长前缀**而不是最长键 —— ``/成品`` 会抢走 ``/成品-小瑶`` 的规则。

    Args:
        rules: ``marker_rules`` JSON，形如
            ``{"default": {...}, "by_folder": {"/成品-小瑶": {...}}}``
        folder_path: 当前文件所在的人员目录（如 ``/成品-小瑶``）

    Returns:
        ``(规则字典, 来源标识)``
    """
    if not rules:
        return dict(DEFAULT_MARKER_RULES), "default"
    default = rules.get("default") or dict(DEFAULT_MARKER_RULES)
    by_folder = rules.get("by_folder") or {}
    if not folder_path:
        return dict(default), "default"
    best_key = ""
    for key in by_folder:
        if folder_path.startswith(key) and len(key) > len(best_key):
            best_key = key
    if best_key:
        return dict(by_folder[best_key]), "by_folder"
    return dict(default), "default"


def match_markers(file_name: str, rules: dict | None = None) -> MarkerHit:
    """识别文件标记 → 命中结果。

    ⚠️ **命中多条时取最高权重，不叠加**
    例：文件名同时含 ``智能体`` 和 ``OOTD`` → 2 条，不是 4 条。
    ⇒ 避免一个文件被多个标记重复加权。

    ⚠️ **大小写不敏感** —— ``OOTD`` / ``ootd`` / ``Ootd`` 都算命中。

    ⚠️⚠️ **绝不把 ``-2`` / ``(1)`` 当作「双条」标记**（铁律 2）
    实测 27 组配对的 md5 全部不同 ⇒ 是不同版本，各算 1 条。
    """
    if rules is None:
        rules = DEFAULT_MARKER_RULES
    haystack = (file_name or "").lower()
    hits: list[str] = []
    weight = 1
    for name, rule in rules.items():
        if not rule.get("enabled", True):
            continue
        patterns = rule.get("patterns") or []
        if any(p and p.lower() in haystack for p in patterns):
            hits.append(name)
            weight = max(weight, int(rule.get("weight", 1)))
    return MarkerHit(names=hits, weight=weight)


def _extract_name_date(file_name: str) -> tuple[int, int] | None:
    """从文件名提取 ``(月, 日)`` —— 没有则返回 None。"""
    name = file_name or ""
    for pattern in _NAME_DATE_PATTERNS:
        match = pattern.match(name)
        if match:
            try:
                month, day = int(match.group(1)), int(match.group(2))
            except ValueError:
                continue
            if 1 <= month <= 12 and 1 <= day <= 31:
                return (month, day)
    return None


def resolve_business_date(
    path: str,
    file_name: str = "",
    *,
    default_year: int = 0,
) -> tuple[str, list[str]]:
    """判定业务日期 → ``(YYYY-MM-DD, 异常标记列表)``。

    ⚠️ **以目录日期为准**（铁律 3）
    实测 190 个带日期前缀的文件里 6 个与目录冲突
    （如 ``/成品-小雨/11月/11.14/11.17-智能体.mp4``），
    文件名日期是随手写的，目录才是归档依据。
    ⇒ 冲突时仍用目录日期，但记 ``name_date_conflict`` 供人工核查。

    ⚠️ 目录解析不出时**降级**到上传时间（由调用方补 ctime），
    并标 ``date_unparsed``。
    """
    anomalies: list[str] = []
    year, month, day = parse_period_from_path(path, default_year=default_year)
    if year and month:
        try:
            dir_date = date(year, month, day)
        except ValueError:
            dir_date = None
        if dir_date:
            name_date = _extract_name_date(file_name)
            if name_date and name_date != (month, day):
                anomalies.append("name_date_conflict")
            return dir_date.isoformat(), anomalies
    anomalies.append("date_unparsed")
    return "", anomalies


def resolve_file_business_date(
    file: PanFile,
    *,
    default_year: int = 0,
) -> tuple[str, list[str]]:
    """解析文件的**最终业务日期** → ``(YYYY-MM-DD, 异常标记列表)``。

    ⚠️⚠️ **绝不返回空字符串** —— 空日期会让 ``aggregate_daily``
    静默跳过该文件 ⇒ 工作量**无声少计**（差额查不出来）。

    ⇒ **降级链**（逐级降级，每级都留痕）：

    1. **目录日期**（``/11月/11.14/xxx.mp4``）—— 首选，铁律 3
    2. **文件名日期**（``11.14-xxx.mp4``）—— 次选
    3. **上传时间**（``ctime``）—— 降级，会标 ``date_from_ctime``
    4. **显式占位**（``0001-01-01``）—— 最后兜底，标 ``date_unknown``

    ⚠️ 第 3 级是**真正的降级** —— 上传时间比制作日期晚
    （实测 11.5 的片子 11.6 才上传）⇒ 统计会错位。
    ⇒ 必须标 ``date_from_ctime`` 让主管知道这天的数据**不完全可信**。

    ⚠️ 第 4 级不会与真实日期混淆，但**在日汇总里占一行**
    ⇒ 主管能看见「这天有文件但日期定不出来」，进而人工复核。

    回归点（2026-11-21，绑定 ``/口播成品`` 后暴露）：
    ``/口播成品/灯笼裤.mp4`` 与 ``/口播成品/6月18日.mp4`` 直接挂在根目录，
    **目录名里没有日期** ⇒ 原实现返回空 ⇒ 计入判定成立但聚合时被丢弃
    ⇒ 「1346 条」在面板上只显示「1345 条」。
    """
    day, anomalies = resolve_business_date(
        file.path or "", file.name or "", default_year=default_year
    )
    if day:
        return day, anomalies

    anomalies = list(anomalies)

    # ── 第 2 级：文件名日期 ──
    name_date = _extract_name_date(file.name or "")
    if name_date:
        month, day_of_month = name_date
        year = default_year or time.localtime().tm_year
        try:
            resolved = date(year, month, day_of_month)
        except ValueError:
            resolved = None
        if resolved:
            anomalies.append("date_from_name")
            return resolved.isoformat(), anomalies

    # ── 第 3 级：上传时间（降级）──
    if getattr(file, "ctime", 0):
        anomalies.append("date_from_ctime")
        return (
            time.strftime("%Y-%m-%d", time.localtime(file.ctime)),
            anomalies,
        )

    # ── 第 4 级：显式占位（绝不静默丢弃）──
    anomalies.append("date_unknown")
    return UNKNOWN_DATE, anomalies


def compute_content_digest(file: PanFile) -> str:
    """内容指纹 —— 去重锚点。

    优先 md5（网盘返回的云端哈希），缺失时退回 ``name:size``。
    """
    md5 = getattr(file, "md5", "") or ""
    if md5:
        return f"md5:{md5}"
    return f"ns:{file.name}:{file.size}"


def strip_version_suffix(file_name: str) -> str:
    """剥掉序号后缀 → ``11.13 智能体-2.mp4`` ⇒ ``11.13 智能体.mp4``。

    ⚠️ 仅用于**展示与人工核对**，不参与计数逻辑。
    """
    stem, dot, ext = (file_name or "").rpartition(".")
    if not dot:
        return file_name or ""
    return f"{_VERSION_SUFFIX.sub('', stem)}.{ext}"


def judge_file(
    file: PanFile,
    *,
    rules: dict | None = None,
    allowed_extensions: tuple[str, ...] = DEFAULT_ALLOWED_EXTENSIONS,
    exclude_keywords: tuple[str, ...] = DEFAULT_EXCLUDE_KEYWORDS,
    exclude_dir_segments: tuple[str, ...] = DEFAULT_EXCLUDE_DIR_SEGMENTS,
    default_year: int = 0,
) -> FileVerdict:
    """判定单个文件是否计入工作量、计几条。

    ⚠️ **本函数不做归属判定** —— 归属由目录路径决定，
    见 :func:`resolve_owner`。

    ⚠️ **不丢弃任何文件** —— 被排除的也返回 verdict（带 ``exclude_reason``）。
    """
    name = file.name or ""
    path = file.path or ""
    lower_name = name.lower()

    # ── 1. 扩展名白名单（铁律 1：png/jpg 是封面，必须排除）──
    ext = file.ext
    if ext not in allowed_extensions:
        return FileVerdict(
            counted=False,
            exclude_reason=f"ext_not_allowed:{ext or 'none'}",
            content_digest=compute_content_digest(file),
        )

    # ── 2. 关键词排除 ──
    for keyword in exclude_keywords:
        if keyword.lower() in lower_name:
            return FileVerdict(
                counted=False,
                exclude_reason=f"keyword:{keyword}",
                content_digest=compute_content_digest(file),
            )

    # ── 3. 目录段排除（草稿/工程）──
    segments = [s.lower() for s in path.split("/") if s]
    for segment in segments:
        for bad in exclude_dir_segments:
            if bad.lower() == segment or bad.lower() in segment:
                return FileVerdict(
                    counted=False,
                    exclude_reason=f"draft_dir:{segment}",
                    content_digest=compute_content_digest(file),
                )

    # ── 4. 业务日期（目录为准）──
    business_date, anomalies = resolve_business_date(
        path, name, default_year=default_year
    )

    # ── 5. 标记识别 ──
    folder = "/" + "/".join(segments[:2]) if len(segments) >= 2 else ""
    effective_rules, source = resolve_marker_rules(rules, folder)
    marker = match_markers(name, effective_rules)
    marker.source = source

    return FileVerdict(
        counted=True,
        count_weight=marker.weight,
        business_date=business_date,
        markers=marker,
        anomaly_flags=anomalies,
        content_digest=compute_content_digest(file),
    )


def resolve_owner(
    file_path: str,
    bindings: list[dict],
) -> tuple[str | None, str, str | None]:
    """判定文件归属 → ``(employee_id, bind_type, 异常说明)``。

    ⚠️ **只用路径**（2026-11-21 实测结论）
    实测：公司共享网盘下 ``oper_id`` **232/232 条全部等于公司账号 uk**，
    共享目录**不暴露真实上传者** ⇒ ``oper_id`` 无区分度，不能用于拆人。

    ⇒ 归属 = **顶层目录路径前缀**匹配：
        ``/成品-小瑶/...``  →  小瑶
        ``/成品-小雨/...``  →  小雨

    Args:
        file_path: 文件绝对路径
        bindings: 绑定列表，每项含 ``folder_path`` / ``employee_id`` / ``active``

    Returns:
        ``(employee_id, bind_type, note)``，
        ``bind_type`` ∈ ``path`` / ``unbound`` / ``ambiguous``
    """
    hits = [
        b for b in bindings
        if b.get("active", True) and (file_path or "").startswith(b.get("folder_path") or "")
    ]
    if len(hits) == 1:
        return hits[0].get("employee_id"), "path", None
    if len(hits) > 1:
        # ⚠️ 绑定表配置有歧义（如 /成品 与 /成品-小瑶 同时存在）
        #   宁可标 ambiguous 让 HR 修，也不要静默算错（可能算给两个人）
        return None, "ambiguous", f"命中 {len(hits)} 条绑定"
    return None, "unbound", None


def validate_bindings(bindings: list[dict]) -> list[str]:
    """校验绑定表前缀互不冲突 → 返回错误列表（空 = 通过）。

    ⚠️ ``/成品`` 与 ``/成品-小瑶`` **互为前缀** ⇒ 必须禁止同时配置，
    否则一个文件会命中两条绑定（``ambiguous``），
    而自动取第一条会导致**同一文件算给两个人**。

    ⚠️ 同一 ``folder_path`` 绑不同员工也要拦住。
    """
    errors: list[str] = []
    active = [b for b in bindings if b.get("active", True)]
    for i, a in enumerate(active):
        pa = (a.get("folder_path") or "").rstrip("/")
        for b in active[i + 1:]:
            pb = (b.get("folder_path") or "").rstrip("/")
            if not pa or not pb:
                continue
            if pa == pb:
                if a.get("employee_id") != b.get("employee_id"):
                    errors.append(
                        f"路径 {pa} 绑给了两个不同员工："
                        f"{a.get('employee_id')} 与 {b.get('employee_id')}"
                    )
                continue
            if pa.startswith(pb + "/") or pb.startswith(pa + "/"):
                errors.append(f"路径前缀冲突：{pa} 与 {pb} 互为前缀")
                continue
            # ⚠️ **非「/」分隔的前缀也要拦**（实测易漏点）
            #   ``/成品`` 是 ``/成品-小瑶`` 的前缀 —— 路径匹配用的是
            #   ``file_path.startswith(folder_path)``，不带「/」也能命中
            #   ⇒ ``/成品-小瑶/11月/x.mp4`` 会同时命中两条绑定 → ambiguous。
            #   而 ``/成品`` 与 ``/成品-小瑶``、``/成品-小雨`` 都不是彼此的前缀，
            #   所以只拦「真前缀」，不会误伤兄弟目录。
            if pa.startswith(pb) or pb.startswith(pa):
                errors.append(
                    f"路径前缀冲突：{pa} 与 {pb} 互为前缀"
                    f"（前缀匹配会同时命中两者）"
                )
    return errors


def summarize_daily(verdicts: list[tuple[str, FileVerdict]]) -> dict[str, int]:
    """按业务日期汇总条数。

    Args:
        verdicts: ``(employee_id, verdict)`` 列表

    Returns:
        ``{YYYY-MM-DD: 条数}``
    """
    daily: dict[str, int] = {}
    for _, verdict in verdicts:
        if not verdict.counted:
            continue
        day = verdict.business_date or ""
        daily[day] = daily.get(day, 0) + verdict.count_weight
    return daily


def current_year() -> int:
    """当前年份 —— 用于补全路径里没有年份的目录名。"""
    return time.localtime().tm_year
