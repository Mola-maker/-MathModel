"""PDF → Markdown agent.

Strategy
--------
1. Find the running paddleocr-vl container automatically (via `docker ps`).
2. Copy the PDF into the container's /tmp workspace.
3. Run a self-contained OCR script inside the container that:
   - Renders each PDF page to an image with pypdfium2 (200 DPI).
   - Runs PaddleOCR 3.x on CPU (GPU arch may be incompatible with paddle binary).
   - Writes raw OCR lines to stdout as JSON.
4. Post-process the JSON lines with a small LLM call to produce clean Markdown.
5. Save to translation/<stem>.md and return the path.

Fallback chain
--------------
If PaddleOCR extraction yields < 100 chars → raise ValueError so caller knows.
If LLM formatting fails → save raw OCR text as Markdown (best-effort).
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.orchestrator import call_model
from agents.utils import docker_cp, docker_exec, vol_host

_BASE = Path(__file__).resolve().parent.parent
QUESTION_DIR = Path(os.getenv("QUESTIONTEST_DIR", str(_BASE / "questiontest")))
TRANSLATION_DIR = Path(os.getenv("TRANSLATION_DIR", str(_BASE / "translation")))

# ---------------------------------------------------------------------------
# OCR script injected into the container at runtime
# ---------------------------------------------------------------------------
_OCR_SCRIPT = textwrap.dedent("""\
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
""")

# ---------------------------------------------------------------------------
# LLM system prompt for Markdown formatting
# ---------------------------------------------------------------------------
_SYSTEM_MD_CLEAN = """\
You are a math contest document formatter.
Convert raw OCR text lines into clean, structured Markdown.
Requirements:
- Preserve all mathematical formulas; wrap inline math in $...$ and block math in $$...$$.
- Use ## headings for major sections (e.g. 问题1, 问题2).
- Reconstruct tables in Markdown table format where obvious.
- Remove garbled fragments (OCR artifacts, page numbers, footers).
- Output Markdown only — no extra commentary.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_paddleocr_container() -> str:
    """Return the name of the running paddleocr-vl container."""
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"],
            capture_output=True, text=True, check=True,
            encoding="utf-8", errors="replace",
        ).stdout
        for line in out.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2 and "paddleocr" in parts[1].lower():
                return parts[0].strip()
    except Exception:
        pass

    configured = os.getenv("PADDLEOCR_CONTAINER", "").strip()
    if configured:
        return configured

    raise RuntimeError(
        "No running paddleocr-vl container found. "
        "Start the container and set PADDLEOCR_CONTAINER env var if needed."
    )


def _run_ocr_in_container(pdf_host_path: Path) -> list[dict]:
    """Copy PDF + OCR script into container, run, return parsed JSON."""
    container = _find_paddleocr_container()

    # Use ASCII temp names to avoid docker cp issues with non-ASCII filenames
    scripts_dir = vol_host() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    host_pdf_copy = scripts_dir / "_ocr_input.pdf"
    host_pdf_copy.write_bytes(pdf_host_path.read_bytes())

    host_script = scripts_dir / "_paddleocr_extract.py"
    host_script.write_text(_OCR_SCRIPT, encoding="utf-8")

    container_pdf = "/tmp/_ocr_input.pdf"
    container_script = "/tmp/_paddleocr_extract.py"

    docker_cp(str(host_pdf_copy), container, container_pdf)
    docker_cp(str(host_script), container, container_script)

    exit_code, stdout, stderr = docker_exec(
        container,
        f"PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True python3 {container_script} {container_pdf}",
        timeout=600,
    )
    if exit_code != 0:
        raise RuntimeError(f"OCR container exec failed (exit {exit_code}):\n{stderr[:800]}")

    # stdout may contain log lines before the JSON; find the JSON array
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("["):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass

    raise RuntimeError(f"Cannot find JSON in OCR output. stdout[:500]:\n{stdout[:500]}")


def _ocr_pages_to_raw_text(pages: list[dict]) -> str:
    """Flatten OCR pages into plain text with page separators."""
    parts = []
    for page in pages:
        lines = [item["text"] for item in page.get("lines", [])]
        if lines:
            parts.append(f"[第 {page['page']} 页]\n" + "\n".join(lines))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert_pdf_to_markdown(pdf_path: Path) -> Path:
    """Convert one PDF into one Markdown file under translation/."""
    TRANSLATION_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[PDF-Agent] Extracting (PaddleOCR): {pdf_path.name}")

    pages = _run_ocr_in_container(pdf_path)
    total_lines = sum(len(p["lines"]) for p in pages)
    print(f"[PDF-Agent] Recognized {total_lines} text lines across {len(pages)} page(s)")

    raw_text = _ocr_pages_to_raw_text(pages)
    if len(raw_text.strip()) < 100:
        raise ValueError(
            f"OCR content too short ({len(raw_text)} chars) — "
            "PDF may be blank or unreadable."
        )

    # LLM formatting (chunk to stay within context limits)
    chunks = [raw_text[i: i + 6000] for i in range(0, len(raw_text), 6000)]
    md_parts: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        print(f"[PDF-Agent] LLM formatting chunk {i}/{len(chunks)}")
        try:
            md_part = call_model(
                _SYSTEM_MD_CLEAN,
                f"Raw OCR text (chunk {i}):\n{chunk}",
                task="extraction",
            )
        except Exception as exc:
            print(f"[PDF-Agent] LLM failed on chunk {i}, using raw text: {exc}")
            md_part = f"## OCR 原文 (第 {i} 段)\n\n{chunk}"
        md_parts.append(md_part)

    md_content = "\n\n".join(md_parts)
    out_path = TRANSLATION_DIR / f"{pdf_path.stem}.md"
    out_path.write_text(md_content, encoding="utf-8")
    print(f"[PDF-Agent] Done → {out_path}")
    return out_path


def run() -> list[Path]:
    """Process all problem PDFs under questiontest/ recursively."""
    QUESTION_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = [p for p in QUESTION_DIR.rglob("*.pdf") if "附件" not in p.as_posix()]

    if not pdfs:
        print(f"[PDF-Agent] No PDFs in {QUESTION_DIR}. Put contest PDF there first.")
        return []

    results: list[Path] = []
    for pdf in pdfs:
        try:
            results.append(convert_pdf_to_markdown(pdf))
        except Exception as exc:
            print(f"[PDF-Agent] Failed on {pdf.name}: {exc}")
    return results


class PdfAgent:
    """Class wrapper kept for compatibility with main.py imports."""

    def convert_pdf_to_markdown(self, pdf_path: Path) -> Path:
        return convert_pdf_to_markdown(pdf_path)

    def run(self) -> list[Path]:
        return run()


if __name__ == "__main__":
    run()
