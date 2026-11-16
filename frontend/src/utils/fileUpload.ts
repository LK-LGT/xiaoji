/**
 * 文件上传相关的共享工具。
 *
 * ⚠️ BUG-R6-19：前后端「允许的文件类型」口径曾长期分裂 ——
 *    后端返回 **MIME**（`application/pdf`），前端按 **扩展名** 校验
 *    （`pdf` / `docx` / `png`），而拼 `<input accept="...">` 时
 *    又统一加了前导点 → 得到 `.application/pdf` 这种**无效**的 accept。
 *
 *    后果：
 *      1. 文件选择器不按类型过滤（用户能选到任意文件）；
 *      2. 前端 `validateFile` 拿扩展名去比对 MIME 列表 →
 *         **合法 PDF 也被判「文件类型不支持」**，根本传不上去。
 *
 * 本模块提供统一口径：
 *   - `normalizeAcceptedExtensions` —— 把后端给的类型列表统一成
 *     **小写扩展名**（`application/pdf` → `pdf`，`pdf` → `pdf`）；
 *   - `buildFileAcceptAttribute` —— 生成合法的 `accept` 字符串。
 */

/** MIME → 扩展名映射（覆盖后端 `ACCEPTED_FILE_TYPES` 的全部条目）。 */
const MIME_TO_EXTENSION: Record<string, string> = {
  'application/pdf': 'pdf',
  'application/msword': 'doc',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
  'application/vnd.ms-excel': 'xls',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
  'application/zip': 'zip',
  'application/x-zip-compressed': 'zip',
  'text/plain': 'txt',
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/webp': 'webp',
}

/** 取文件名的小写扩展名（不含点）。无扩展名时返回空串。 */
export function fileExtension(fileName: string): string {
  const base = (fileName || '').split(/[\\/]/).pop() ?? ''
  const dot = base.lastIndexOf('.')
  if (dot < 0 || dot === base.length - 1) return ''
  return base.slice(dot + 1).toLowerCase()
}

/**
 * 把后端返回的「允许类型」列表统一成小写扩展名数组。
 *
 * 同时兼容两种输入：
 *   - MIME：`application/pdf` → `pdf`
 *   - 扩展名：`pdf`（或 `.pdf`）→ `pdf`
 */
export function normalizeAcceptedExtensions(types: readonly string[] | undefined): string[] {
  if (!Array.isArray(types)) return []
  const out: string[] = []
  for (const raw of types) {
    const value = String(raw ?? '').trim().toLowerCase()
    if (!value) continue
    if (value.includes('/')) {
      const mapped = MIME_TO_EXTENSION[value]
      if (mapped) out.push(mapped)
      continue
    }
    out.push(value.startsWith('.') ? value.slice(1) : value)
  }
  return [...new Set(out)]
}

/**
 * 生成 `<input type="file" accept="...">` 的合法属性值。
 *
 * 统一产出**扩展名**形式（`.pdf,.docx,…`）—— 浏览器的 `accept`
 * 既认扩展名也认 MIME，但扩展名在 Windows / macOS 上更稳。
 */
export function buildFileAcceptAttribute(types: readonly string[] | undefined): string {
  return normalizeAcceptedExtensions(types)
    .map((ext) => `.${ext}`)
    .join(',')
}

/**
 * 判断文件是否在允许列表内（按**扩展名**）。
 */
export function isAcceptedFile(
  fileName: string,
  types: readonly string[] | undefined,
): boolean {
  const accepted = normalizeAcceptedExtensions(types)
  if (accepted.length === 0) return true
  return accepted.includes(fileExtension(fileName))
}
