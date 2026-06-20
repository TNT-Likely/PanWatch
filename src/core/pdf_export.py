"""详情报告导出 PDF —— 纯后端生成,不依赖 Chromium。

markdown → HTML(python-markdown)→ PDF(xhtml2pdf / reportlab)。
中文用 reportlab 内置 STSong-Light CID 字体,无需打包 TTF、无系统库依赖。
(Chromium/page.pdf 可作为将来的高保真备选。)
"""

from __future__ import annotations

import io
import logging
from html import escape

import markdown as _markdown
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from xhtml2pdf import pisa

logger = logging.getLogger(__name__)

_CJK_FONT = "STSong-Light"  # reportlab 内置简体中文 CID 字体
_font_ready = False


def _ensure_font() -> None:
    global _font_ready
    if not _font_ready:
        pdfmetrics.registerFont(UnicodeCIDFont(_CJK_FONT))
        _font_ready = True


_CSS = """
@page { size: A4; margin: 1.7cm 1.5cm; }
body { font-family: STSong-Light; font-size: 10.5pt; line-height: 1.6; color: #1f2937; }
.doc-title { font-size: 17pt; font-weight: bold; color: #111827;
             border-bottom: 1.5pt solid #d1d5db; padding-bottom: 6pt; margin-bottom: 12pt; }
h1 { font-size: 15pt; margin: 14pt 0 6pt; color: #111827; }
h2 { font-size: 13pt; margin: 12pt 0 5pt; color: #1f2937; }
h3 { font-size: 11.5pt; margin: 10pt 0 4pt; color: #374151; }
p  { margin: 4pt 0; }
ul, ol { margin: 4pt 0 4pt 6pt; }
li { margin: 2pt 0; }
strong, b { font-weight: bold; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0; }
th, td { border: 0.5pt solid #d1d5db; padding: 4pt 6pt; font-size: 9.5pt; }
th { background: #f3f4f6; }
hr { border: none; border-top: 0.5pt solid #e5e7eb; margin: 10pt 0; }
a { color: #2563eb; }
.footer { margin-top: 16pt; padding-top: 6pt; border-top: 0.5pt solid #e5e7eb;
          font-size: 8.5pt; color: #9ca3af; }
"""


def render_analysis_pdf(title: str, markdown_text: str) -> bytes:
    """把分析报告 markdown 渲染成 PDF 字节(中文矢量、可复制、可选中)。"""
    _ensure_font()
    body_html = _markdown.markdown(
        markdown_text or "",
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    doc = (
        '<html><head><meta charset="utf-8"><style>' + _CSS + "</style></head><body>"
        + f'<div class="doc-title">{escape((title or "深度分析").strip())}</div>'
        + body_html
        + '<div class="footer">本报告由 AI 生成,仅供参考,不构成投资建议。</div>'
        + "</body></html>"
    )
    buf = io.BytesIO()
    result = pisa.CreatePDF(src=doc, dest=buf, encoding="utf-8")
    if result.err:
        logger.warning("[PDF导出] 渲染存在告警/错误: err=%s", result.err)
    return buf.getvalue()
