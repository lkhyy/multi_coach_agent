from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PdfExtractResult:
    text: str
    ocr_used: bool = False
    # 若 pypdf 无字且 OCR 失败或不可用，供接口返回给前端的说明（中文）
    ocr_hint: str | None = None


def _pypdf_extract(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    parts: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            t = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            logger.warning("pdf page %s extract failed", i)
            t = ""
        t = t.strip()
        if t:
            parts.append(t)
    return "\n\n".join(parts)


def _ocr_pdf_bytes(data: bytes) -> str:
    import fitz  # PyMuPDF
    import pytesseract
    from PIL import Image

    from app.config import settings

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        n = min(doc.page_count, max(1, int(settings.rag_pdf_ocr_max_pages)))
        zoom = max(1.0, min(4.0, float(settings.rag_pdf_ocr_zoom)))
        mat = fitz.Matrix(zoom, zoom)
        lang = (settings.rag_pdf_ocr_lang or "chi_sim+eng").strip() or "eng"
        texts: list[str] = []
        for i in range(n):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            t = pytesseract.image_to_string(img, lang=lang)
            if t.strip():
                texts.append(t.strip())
        return "\n\n".join(texts)
    finally:
        doc.close()


def extract_text_from_pdf_bytes(data: bytes) -> PdfExtractResult:
    """先 pypdf 文本层；无字且开启 fallback 时用 Tesseract 对渲染页 OCR。"""
    from app.config import settings

    plain = _pypdf_extract(data).strip()
    if plain:
        return PdfExtractResult(plain, False, None)

    if not settings.rag_pdf_ocr_fallback:
        return PdfExtractResult("", False, None)

    try:
        import pytesseract
    except ImportError:
        return PdfExtractResult(
            "",
            False,
            "OCR 需要安装 pytesseract 与 Pillow，并在系统 PATH 中安装 Tesseract。",
        )

    try:
        ocr_text = _ocr_pdf_bytes(data).strip()
    except pytesseract.TesseractNotFoundError:
        return PdfExtractResult(
            "",
            False,
            "未检测到 Tesseract（扫描版 PDF 依赖 OCR）。请安装 Tesseract 并加入 PATH，"
            "并安装语言包（如 chi_sim+eng）；也可设置 RAG_PDF_OCR_FALLBACK=false。",
        )
    except ImportError as e:
        logger.warning("PDF OCR 依赖缺失: %s", e)
        return PdfExtractResult(
            "",
            False,
            "OCR 需要 PyMuPDF（pymupdf）、pytesseract、Pillow 及系统级 Tesseract。",
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("PDF OCR 失败")
        return PdfExtractResult("", False, f"OCR 失败：{e}")

    if ocr_text:
        return PdfExtractResult(ocr_text, True, None)
    return PdfExtractResult(
        "",
        False,
        "OCR 未识别到文字（可能画质过低、语言包不匹配，或整本为空白页）。",
    )
