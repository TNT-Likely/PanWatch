"""详情报告导出 PDF —— HTML 保真排版。

markdown → HTML(python-markdown)→ PDF。
- 主引擎 **WeasyPrint**:真 CSS 排版引擎,自动换行/分页/页码,中文走系统字体,排版接近网页。
- WeasyPrint 不可用(缺系统库 pango 等)时回退 **xhtml2pdf**(纯库、排版朴素但保底,
  中文用 reportlab 内置 STSong-Light CID 字体)。
(Chromium/page.pdf 可作为将来更高保真的备选,但需安装浏览器,这里不默认依赖。)
"""

from __future__ import annotations

import io
import logging
from html import escape

import markdown as _markdown

logger = logging.getLogger(__name__)


# ---- WeasyPrint(主)----

_REPORT_CSS = """
@page {
  size: A4; margin: 1.7cm 1.5cm;
  @bottom-center { content: "仅供参考,不构成投资建议 · 第 " counter(page) " / " counter(pages) " 页";
                   font-size: 8pt; color: #9ca3af; }
}
body { font-family: "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", "Hiragino Sans GB", sans-serif;
       font-size: 10.5pt; line-height: 1.75; color: #1f2937; }
.doc-title { font-size: 18pt; font-weight: 700; color: #0f172a;
             border-bottom: 2pt solid #e11d48; padding-bottom: 8pt; margin: 0 0 14pt; }
h1 { font-size: 14.5pt; color: #0f172a; margin: 16pt 0 6pt; padding-left: 8pt;
     border-left: 3pt solid #2563eb; page-break-after: avoid; }
h2 { font-size: 12.5pt; color: #1e293b; margin: 13pt 0 5pt; page-break-after: avoid; }
h3 { font-size: 11.5pt; color: #334155; margin: 10pt 0 4pt; page-break-after: avoid; }
p { margin: 5pt 0; }
ul, ol { margin: 5pt 0 5pt 16pt; }
li { margin: 2.5pt 0; }
strong, b { font-weight: 700; color: #0f172a; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; }
th, td { border: 0.5pt solid #d1d5db; padding: 5pt 8pt; font-size: 9.5pt; text-align: left;
         vertical-align: top; }
th { background: #f1f5f9; font-weight: 600; }
hr { border: none; border-top: 0.5pt solid #e5e7eb; margin: 12pt 0; }
blockquote { border-left: 3pt solid #cbd5e1; margin: 6pt 0; padding: 2pt 0 2pt 10pt; color: #475569; }
code { background: #f1f5f9; padding: 1pt 3pt; border-radius: 2pt; }
a { color: #2563eb; text-decoration: none; }
"""


def _md_to_html(markdown_text: str) -> str:
    return _markdown.markdown(
        markdown_text or "", extensions=["tables", "fenced_code", "sane_lists"]
    )


def _render_weasyprint(title: str, body_html: str) -> bytes:
    from weasyprint import HTML

    doc = (
        '<html><head><meta charset="utf-8"><style>' + _REPORT_CSS + "</style></head><body>"
        + f'<div class="doc-title">{escape((title or "深度分析").strip())}</div>'
        + body_html
        + "</body></html>"
    )
    return HTML(string=doc).write_pdf()


# ---- xhtml2pdf(回退,纯库无系统依赖)----

_FALLBACK_CSS = """
@page { size: A4; margin: 1.6cm 1.5cm; }
body { font-family: STSong-Light; font-size: 10.5pt; line-height: 1.6; color: #1f2937; }
.doc-title { font-size: 16pt; font-weight: bold; border-bottom: 1.5pt solid #d1d5db;
             padding-bottom: 6pt; margin-bottom: 12pt; }
h1 { font-size: 14pt; margin: 12pt 0 5pt; } h2 { font-size: 12pt; margin: 10pt 0 4pt; }
h3 { font-size: 11pt; margin: 8pt 0 3pt; } p { margin: 4pt 0; }
ul, ol { margin: 4pt 0 4pt 6pt; } li { margin: 2pt 0; }
table { border-collapse: collapse; width: 100%; } th, td { border: 0.5pt solid #d1d5db; padding: 4pt 6pt; }
"""


def _render_xhtml2pdf(title: str, body_html: str) -> bytes:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from xhtml2pdf import pisa

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    except Exception:
        pass
    doc = (
        '<html><head><meta charset="utf-8"><style>' + _FALLBACK_CSS + "</style></head><body>"
        + f'<div class="doc-title">{escape((title or "深度分析").strip())}</div>'
        + body_html
        + '<div style="margin-top:14pt;font-size:8.5pt;color:#9ca3af;">'
        + "本报告由 AI 生成,仅供参考,不构成投资建议。</div></body></html>"
    )
    buf = io.BytesIO()
    pisa.CreatePDF(src=doc, dest=buf, encoding="utf-8")
    return buf.getvalue()


_ANALYST_SECTIONS = [
    ("market", "技术分析师"),
    ("social", "情绪分析师"),
    ("news", "新闻分析师"),
    ("fundamentals", "基本面分析师"),
]


def assemble_report_markdown(raw_data: dict) -> str:
    """从 raw_data 拼出与详情页(buildAnalysisSections)同款分节的完整报告 markdown。

    顺序对齐详情页:决策摘要 → PM 决策书(+交易员)→ 4 分析师全文 → 看多看空辩论全文(+研究主管裁决)
    → 风控辩论全文(+风控裁决)。比 `content` 字段更全(content 省略了 4 分析师与辩论全文)。
    """
    rd = raw_data or {}
    sug = rd.get("suggestion") or {}
    reports = rd.get("analyst_reports") or {}
    debate = rd.get("debate_history") or {}
    risk = rd.get("risk_debate") or {}
    parts: list[str] = []

    label = sug.get("action_label") or "持有"
    head = f"**{label}**"
    conf = sug.get("confidence")
    if conf is not None:
        try:
            head += f" · 置信度 {float(conf):.1f}/10"
        except (TypeError, ValueError):
            pass
    parts.append(f"## 最终决策\n\n{head}\n")

    final_decision = (rd.get("final_decision") or "").strip()
    trader = (rd.get("trader_plan") or "").strip()
    if final_decision or trader:
        body = final_decision
        if trader:
            body = (body + "\n\n" if body else "") + f"### 💼 交易员执行计划\n\n{trader}"
        parts.append(f"## PM 最终决策书\n\n{body}\n")

    for key, title in _ANALYST_SECTIONS:
        txt = (reports.get(key) or "").strip()
        if txt:
            parts.append(f"## {title}\n\n{txt}\n")

    dh = (debate.get("history") or "").strip()
    if dh:
        seg = dh
        jd = (debate.get("judge_decision") or "").strip()
        if jd:
            seg += f"\n\n### ⚖️ 研究主管裁决\n\n{jd}"
        parts.append(f"## 看多看空辩论\n\n{seg}\n")

    rh = (risk.get("history") or "").strip()
    rjd = (rd.get("risk_judgment") or risk.get("judge_decision") or "").strip()
    if rh or rjd:
        seg = rh
        if rjd:
            seg += (("\n\n" if seg else "") + f"### 🛡️ 风控裁决\n\n{rjd}")
        parts.append(f"## 风控辩论\n\n{seg}\n")

    return "\n".join(parts).strip()


def render_analysis_pdf(title: str, markdown_text: str) -> bytes:
    """分析报告 markdown → PDF 字节(中文矢量、可复制)。WeasyPrint 优先,失败回退 xhtml2pdf。"""
    body_html = _md_to_html(markdown_text)
    try:
        return _render_weasyprint(title, body_html)
    except Exception as e:  # WeasyPrint 缺系统库/渲染异常 → 保底
        logger.warning("[PDF导出] WeasyPrint 不可用,回退 xhtml2pdf: %s", e)
        return _render_xhtml2pdf(title, body_html)
