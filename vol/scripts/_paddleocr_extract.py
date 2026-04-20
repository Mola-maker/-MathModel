import sys, json, os
import numpy as np
import pypdfium2 as pdfium
from paddleocr import PaddleOCR

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

pdf_path = sys.argv[1]
DPI = 200

# ------------------------------------------------------------------
# Build OCR engine (CPU; GPU arch incompatible with paddle binary)
# ------------------------------------------------------------------
ocr = PaddleOCR(
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    lang="ch",
    device="cpu",
)

# ------------------------------------------------------------------
# Render PDF pages and run OCR
# ------------------------------------------------------------------
pdf = pdfium.PdfDocument(pdf_path)
all_pages = []
for page_idx in range(len(pdf)):
    page = pdf[page_idx]
    bitmap = page.render(scale=DPI / 72.0, rotation=0)
    img = np.array(bitmap.to_pil().convert("RGB"))
    result = ocr.predict(img)
    lines = []
    if result:
        for item in result:
            texts = item.get("rec_texts", item.get("rec_text", []))
            scores = item.get("rec_scores", item.get("rec_score", []))
            for txt, score in zip(texts, scores):
                if txt.strip():
                    lines.append({"text": txt.strip(), "score": round(float(score), 4)})
    all_pages.append({"page": page_idx + 1, "lines": lines})

print(json.dumps(all_pages, ensure_ascii=False))
