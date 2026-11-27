"""📄 **知识文档解析与切块**（★ 2026-11-27 新增）。

⚠️ **为什么需要这个模块**
------------------------------------------------------------------
前端「文档入库」页面明明白白写着：

    上传文档后自动完成内容解析、分段和向量写入。

但后端**从来没有实现过**这一步 ⇒ 上传的文件被直接丢弃。
⇒ 本模块提供**真正**的解析 + 切块能力。

⚠️ **支持格式与降级策略**
------------------------------------------------------------------
============  ==========================================
扩展名        处理方式
============  ==========================================
``.docx``     ``python-docx`` 读段落 + 标题层级
``.doc``      ⚠️ 老二进制格式 ⇒ 尽力抽取可读文本
``.md``       按 Markdown 标题层级切块
``.markdown`` 同 ``.md``
``.txt``      纯文本，按空行分段
============  ==========================================

⚠️ **缺依赖时必须降级，不能崩**
    ``python-docx`` 没装 ⇒ ``.docx`` 退化成「按 XML 抽文本」；
    实在抽不出 ⇒ 抛 :class:`KnowledgeParseError`，
    由路由层转成 **422**（前端会显示「解析失败」），
    **绝不**静默返回「成功但 0 段」。

⚠️ **切块规则**
------------------------------------------------------------------
- **有标题层级**（Word 标题样式 / Markdown ``#``）⇒ 按标题切，
  段落里保留「标题路径」（``一级 / 二级``）⇒ 检索命中时能显示上下文；
- **无标题层级** ⇒ 按**空行**分段，超长段落再按**句号边界**二次切；
- 单块上限 ``max_chars``（默认 1200）—— 太长的块会让 BM25 分数失真。
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field

#: 单块最大字符数（超过则再切）。
DEFAULT_MAX_CHARS = 1200

#: 单块最小字符数（太短的块没有检索价值，直接并入上一块）。
DEFAULT_MIN_CHARS = 40


class KnowledgeParseError(ValueError):
    """文档解析失败（由路由层转成 422）。"""


@dataclass
class ParsedChunk:
    """一个知识分块。"""

    chunk_no: int
    heading_path: str
    content: str
    checksum: str = ""
    term_freq: dict[str, int] = field(default_factory=dict)
    content_length: int = 0

    def finalize(self) -> ParsedChunk:
        """算校验值、词频、长度（**必须**在内容定稿后调用）。"""
        self.content = self.content.strip()
        self.checksum = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        self.term_freq = tokenize(self.content)
        self.content_length = len(self.content)
        return self


# ---------------------------------------------------------------------------
# 分词
# ---------------------------------------------------------------------------

#: 中文停用词（检索时剔除 —— 这些词到处都是，命中它们没有信息量）。
_CN_STOPWORDS = frozenset({
    "的", "了", "和", "是", "在", "我", "有", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有",
    "看", "好", "自己", "这", "那", "与", "及", "或", "对", "以", "为", "并",
})

#: 英文停用词。
_EN_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "be",
    "for", "on", "with", "at", "by", "from", "as", "it", "that", "this",
})

#: 连续的拉丁字母/数字（英文单词、指标码、版本号 …）。
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*|\d+(?:\.\d+)?|[\u4e00-\u9fff]")

#: 连续中文串。
_CN_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")

#: **有信息量的最小词长**。
#:
#: ⚠️ **为什么单字不算**
#: 中文单字（``文`` / ``的`` / ``规``）在任意文档里都能命中 ⇒
#: 「完全不相关的量子力学引文」会因为「的」「引」命中知识库
#: ⇒ **假阳性**，前端会把不相关的片段当依据展示 ⇒ 制造幻觉。
#:
#: ⇒ 单字**只能作为双字的补充**，不能独立构成命中。
_MIN_INFORMATIVE_LEN = 2

#: 英文词的最小命中长度（``a`` / ``i`` 之类的没意义）。
_MIN_LATIN_LEN = 3


def is_informative(term: str) -> bool:
    """这个词能不能**独立**作为命中依据。

    ⚠️ 单个中文字 / 过短的英文词 ⇒ 不可。
    """
    if not term:
        return False
    if term[0].isascii():
        return len(term) >= _MIN_LATIN_LEN
    return len(term) >= _MIN_INFORMATIVE_LEN


#: 兼容别名（内部曾用私有名）。
_is_informative = is_informative


def tokenize(text: str) -> dict[str, int]:
    """分词 → 词频表。

    ⚠️ **中文按「双字 + 整串」切**
    没有 ``jieba`` 依赖（生产环境不保证装了）⇒ 用「双字 + 整串」策略：
    - ``双字``（``扣分`` / ``上限``）⇒ 精度来源；
    - ``整串``（``扣分上限``，≤6 字）⇒ 整句命中的强信号；
    - ``单字``⇒ **只作为召回补充**，独立命中不算数（见 :func:`_is_informative`）。

    ⚠️ 返回的是**计数**而不是集合 —— BM25 需要词频。
    """
    counts: dict[str, int] = {}
    lowered = (text or "").lower()

    # 拉丁字母 / 数字
    for match in _TOKEN_RE.finditer(lowered):
        token = match.group(0)
        if token[0].isascii():
            if token in _EN_STOPWORDS or len(token) < _MIN_LATIN_LEN:
                continue
            counts[token] = counts.get(token, 0) + 1

    # 中文：整串（≤6 字）+ 双字 + 单字
    for run in _CN_RUN_RE.findall(lowered):
        if len(run) <= 6:
            counts[run] = counts.get(run, 0) + 1
        for index in range(len(run) - 1):
            bigram = run[index:index + 2]
            if bigram in _CN_STOPWORDS:
                continue
            counts[bigram] = counts.get(bigram, 0) + 1
        if len(run) == 1 and run not in _CN_STOPWORDS:
            counts[run] = counts.get(run, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Markdown 解析
# ---------------------------------------------------------------------------

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def parse_markdown(text: str) -> list[tuple[str, str]]:
    """Markdown → ``[(heading_path, content)]``。

    ⚠️ **标题路径是「面包屑」**
    ``# A`` / ``## B`` / ``### C`` ⇒ 面包屑 ``"A / B / C"``。
    """
    lines = (text or "").splitlines()
    # (level, title, content_lines) 的栈
    stack: list[tuple[int, str]] = []
    blocks: list[tuple[str, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        content = "\n".join(buffer).strip()
        if content:
            path = " / ".join(title for _level, title in stack)
            blocks.append((path, content))
        buffer.clear()

    for line in lines:
        match = _MD_HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            # 弹出同级及更高级的标题
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            continue
        buffer.append(line)
    flush()

    if not blocks:
        stripped = (text or "").strip()
        if stripped:
            blocks.append(("", stripped))
    return blocks


# ---------------------------------------------------------------------------
# Word (.docx) 解析
# ---------------------------------------------------------------------------


def _docx_text_blocks(data: bytes) -> list[tuple[str, str]]:
    """``.docx`` → ``[(heading_path, content)]``（有标题层级时）。

    ⚠️ **优先用 ``python-docx``**
    它能识别「标题 1 / 标题 2」样式 ⇒ 能拿到真实层级。
    没装时降级到「从 ``document.xml`` 抽文本」，此时**没有层级**
    （面包屑为空）⇒ 切块退化成「按空行分段」。
    """
    try:
        import docx  # type: ignore[import-not-found]
    except Exception:
        return _docx_fallback_blocks(data)

    try:
        document = docx.Document(io_bytes(data))
    except Exception as exc:  # pragma: no cover - 损坏文件
        raise KnowledgeParseError(f"Word 文档无法解析：{exc}") from exc

    blocks: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        content = "\n".join(buffer).strip()
        if content:
            path = " / ".join(title for _level, title in stack)
            blocks.append((path, content))
        buffer.clear()

    for paragraph in document.paragraphs:
        text = (paragraph.text or "").strip()
        style_name = ""
        try:
            style_name = (paragraph.style.name or "").lower()
        except Exception:
            style_name = ""

        heading_level = _word_heading_level(style_name)
        if heading_level and text:
            flush()
            while stack and stack[-1][0] >= heading_level:
                stack.pop()
            stack.append((heading_level, text))
            continue
        if text:
            buffer.append(text)
    flush()

    if not blocks:
        fallback = _docx_fallback_blocks(data)
        if fallback:
            return fallback
    return blocks


def _word_heading_level(style_name: str) -> int:
    """Word 样式名 → 标题层级（0 表示不是标题）。"""
    if not style_name:
        return 0
    # 中文「标题 1」/ 英文「Heading 1」
    match = re.search(r"(?:heading|标题)\s*(\d+)", style_name)
    if match:
        return int(match.group(1))
    if style_name in {"title", "标题"}:
        return 1
    return 0


def _docx_fallback_blocks(data: bytes) -> list[tuple[str, str]]:
    """没有 ``python-docx`` 时的降级：直接从 ``word/document.xml`` 抽文本。"""
    try:
        with zipfile.ZipFile(io_bytes(data)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    except Exception as exc:
        raise KnowledgeParseError(f"Word 文档无法解析：{exc}") from exc

    # ``</w:p>`` 是段落边界
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml, re.DOTALL)
    lines: list[str] = []
    for paragraph in paragraphs:
        texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", paragraph, re.DOTALL)
        joined = "".join(texts)
        joined = (
            joined.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&apos;", "'")
        )
        joined = joined.strip()
        if joined:
            lines.append(joined)
    if not lines:
        raise KnowledgeParseError("Word 文档里没有读到任何文字")
    return _split_by_blank_lines("\n".join(lines))


# ---------------------------------------------------------------------------
# 纯文本 / 老 .doc
# ---------------------------------------------------------------------------


def _split_by_blank_lines(text: str) -> list[tuple[str, str]]:
    """按空行分段（无标题层级时的通用切法）。"""
    blocks: list[tuple[str, str]] = []
    for raw in re.split(r"\n\s*\n", text or ""):
        content = raw.strip()
        if content:
            blocks.append(("", content))
    return blocks


def _parse_legacy_doc(data: bytes) -> list[tuple[str, str]]:
    """老式 ``.doc``（OLE 复合文档）—— 尽力抽取可读文本。

    ⚠️ **为什么不接 ``antiword`` / ``libreoffice``**
    服务器上不保证装 ⇒ 引入外部进程依赖会让部署变复杂。
    ⇒ 这里的策略是「**尽力**」：抽到就用，抽不到就明确报错，
    让用户改存 ``.docx`` 或 ``.md``。

    ⚠️ OLE2 里正文通常以 UTF-16LE 连续存放 ⇒ 用启发式扫描。
    """
    # 去掉明显的二进制噪声，按 UTF-16LE 解码
    text = ""
    for encoding in ("utf-16-le", "utf-8", "gbk"):
        try:
            candidate = data.decode(encoding, errors="ignore")
        except Exception:
            continue
        # 取「可打印字符占比最高」的那次解码
        printable = sum(1 for ch in candidate if ch.isprintable() or ch in "\r\n\t")
        if printable / max(len(candidate), 1) > 0.85:
            text = candidate
            break
    cleaned = "\n".join(
        line.strip()
        for line in re.split(r"[\r\n]+", text)
        if line.strip() and sum(1 for ch in line if ch.isprintable()) / max(len(line), 1) > 0.85
    )
    if len(cleaned) < 20:
        raise KnowledgeParseError(
            "旧版 .doc 文档无法解析，请另存为 .docx 或 .md 后再上传"
        )
    return _split_by_blank_lines(cleaned)


def io_bytes(data: bytes):
    """把 bytes 包成 ``io.BytesIO``（``python-docx`` 需要文件对象）。"""
    import io

    return io.BytesIO(data)


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def parse_document(filename: str, data: bytes) -> list[tuple[str, str]]:
    """按扩展名解析文档 → ``[(heading_path, content)]``。

    ⚠️ **抛 :class:`KnowledgeParseError` 表示解析失败**
    路由层据此返回 422 ⇒ 前端能显示「解析失败」，
    而不是「上传成功但 0 段」。
    """
    if not data:
        raise KnowledgeParseError("文件内容为空")

    lower = (filename or "").lower()
    if lower.endswith(".docx"):
        return _docx_text_blocks(data)
    if lower.endswith(".doc"):
        return _parse_legacy_doc(data)
    if lower.endswith(".md") or lower.endswith(".markdown"):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        return parse_markdown(text)
    if lower.endswith(".txt"):
        text = data.decode("utf-8", errors="replace")
        return _split_by_blank_lines(text)
    raise KnowledgeParseError(
        f"暂不支持的文件格式：{lower or '(无扩展名)'}，请上传 .docx / .doc / .md / .markdown"
    )


def chunk_blocks(
    blocks: list[tuple[str, str]],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_chars: int = DEFAULT_MIN_CHARS,
) -> list[ParsedChunk]:
    """把 ``[(heading_path, content)]`` 切成最终分块。

    ⚠️ **超长块按句号边界二次切**
    切在句子中间会破坏语义 ⇒ 优先在 ``。！？；.!?;`` 后切；
    实在没有边界（超长无标点）才硬切。
    """
    chunks: list[ParsedChunk] = []

    def push(path: str, content: str) -> None:
        text = content.strip()
        if not text:
            return
        chunks.append(
            ParsedChunk(
                chunk_no=len(chunks) + 1,
                heading_path=path,
                content=text,
            ).finalize()
        )

    for path, content in blocks:
        content = content.strip()
        if not content:
            continue
        if len(content) <= max_chars:
            # 短块：并入上一块（同标题路径）还是独立成块？
            # ⚠️ 独立成块 —— 检索命中时能精确指到「哪一段」，
            #    拼在一起会让定位失真。
            if chunks and chunks[-1].heading_path == path and (
                chunks[-1].content_length + len(content) + 1 <= max_chars
                and chunks[-1].content_length < min_chars
            ):
                chunks[-1].content = (chunks[-1].content + "\n" + content).strip()
                chunks[-1].finalize()
                continue
            push(path, content)
            continue

        # 超长 ⇒ 按句号边界切
        for piece in _split_by_sentence(content, max_chars):
            push(path, piece)

    return chunks


def _split_by_sentence(text: str, max_chars: int) -> list[str]:
    """按句子边界切长文本。"""
    sentences = re.split(r"(?<=[。！？；.!?;])\s*", text)
    pieces: list[str] = []
    buffer = ""
    for sentence in sentences:
        if not sentence:
            continue
        if len(buffer) + len(sentence) > max_chars and buffer:
            pieces.append(buffer.strip())
            buffer = ""
        if len(sentence) > max_chars:
            # 单句超长 ⇒ 硬切
            for start in range(0, len(sentence), max_chars):
                pieces.append(sentence[start:start + max_chars])
            buffer = ""
            continue
        buffer += sentence
    if buffer.strip():
        pieces.append(buffer.strip())
    return pieces


def extract_title(filename: str, fallback: str = "") -> str:
    """从文件名推导标题（去掉扩展名）。"""
    name = (filename or "").strip()
    for suffix in (".markdown", ".docx", ".doc", ".md", ".txt"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)] or fallback
    return name or fallback


def file_checksum(data: bytes) -> str:
    """文件内容 SHA-256。"""
    return hashlib.sha256(data).hexdigest()


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MIN_CHARS",
    "KnowledgeParseError",
    "ParsedChunk",
    "chunk_blocks",
    "extract_title",
    "file_checksum",
    "is_informative",
    "parse_document",
    "parse_markdown",
    "tokenize",
]
