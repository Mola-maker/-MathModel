from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen
from dotenv import load_dotenv
BASE_DIR = Path(__file__).resolve().parent.parent

# 2. 加载环境变量
load_dotenv(BASE_DIR / ".env")

# 3. 知识库目录：优先从环境变量读取，默认指向 [项目根目录]/knowledge_base
KNOWLEDGE_BASE_DIR = Path(os.getenv("KNOWLEDGE_BASE_DIR", BASE_DIR / "knowledge_base"))
KNOWLEDGE_BASE_DIR.mkdir(parents=True, exist_ok=True)

# 4. 手动导出文献目录：同样支持环境变量覆盖 + 动态相对路径
# 这样即使在 Docker 容器里，也可以通过环境变量灵活挂载不同的文献库
MANUAL_REF_DIR = Path(os.getenv("MANUAL_REF_DIR", BASE_DIR / "reference" / "manual"))
MANUAL_REF_DIR.mkdir(parents=True, exist_ok=True)
def _http_get_json(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> Any:
    req = Request(url, headers=headers or {})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


# ── GitHub ────────────────────────────────────────────────────────────────────

def search_github_repos(query: str, top_k: int = 5) -> list[dict]:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    headers = {"User-Agent": "mathmodel-agent"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = (
        "https://api.github.com/search/repositories"
        f"?q={quote(query)}&sort=stars&order=desc&per_page={top_k}"
    )
    try:
        data = _http_get_json(url, headers=headers)
    except Exception:
        return []

    items = data.get("items", []) if isinstance(data, dict) else []
    result: list[dict] = []
    for item in items[:top_k]:
        result.append(
            {
                "name": item.get("full_name", ""),
                "url": item.get("html_url", ""),
                "stars": item.get("stargazers_count", 0),
                "description": item.get("description", ""),
                "source": "github",
            }
        )
    return result


# ── OpenAlex ──────────────────────────────────────────────────────────────────

def search_openalex_works(query: str, top_k: int = 5) -> list[dict]:
    url = (
        "https://api.openalex.org/works"
        f"?search={quote(query)}&per-page={top_k}&sort=relevance_score:desc"
    )
    try:
        data = _http_get_json(url)
    except Exception:
        return []

    works = data.get("results", []) if isinstance(data, dict) else []
    result: list[dict] = []
    for w in works[:top_k]:
        authors = []
        for a in (w.get("authorships") or [])[:3]:
            name = (a.get("author") or {}).get("display_name", "")
            if name:
                authors.append(name)
        result.append(
            {
                "title": w.get("display_name", ""),
                "year": w.get("publication_year", ""),
                "doi": (w.get("doi", "") or "").replace("https://doi.org/", ""),
                "authors": authors,
                "cited_by": w.get("cited_by_count", 0),
                "id": w.get("id", ""),
                "source": "openalex",
            }
        )
    return result


# ── Semantic Scholar ──────────────────────────────────────────────────────────

def search_semantic_scholar(query: str, top_k: int = 5) -> list[dict]:
    """
    Semantic Scholar 公开API，无需密钥，覆盖数学/CS/工程领域极好。
    文档: https://api.semanticscholar.org/graph/v1
    """
    fields = "title,year,authors,citationCount,externalIds,abstract"
    url = (
        "https://api.semanticscholar.org/graph/v1/paper/search"
        f"?query={quote(query)}&limit={top_k}&fields={fields}"
    )
    headers = {"User-Agent": "mathmodel-agent/1.0"}
    # 支持可选 API key（可在 .env 中配置 S2_API_KEY 以提升速率限制）
    s2_key = os.getenv("S2_API_KEY", "").strip()
    if s2_key:
        headers["x-api-key"] = s2_key

    try:
        data = _http_get_json(url, headers=headers, timeout=25)
    except Exception:
        return []

    papers = data.get("data", []) if isinstance(data, dict) else []
    result: list[dict] = []
    for p in papers[:top_k]:
        authors = [a.get("name", "") for a in (p.get("authors") or [])[:3]]
        ext_ids = p.get("externalIds") or {}
        doi = ext_ids.get("DOI", "")
        result.append(
            {
                "title": p.get("title", ""),
                "year": p.get("year", ""),
                "authors": authors,
                "doi": doi,
                "cited_by": p.get("citationCount", 0),
                "paper_id": p.get("paperId", ""),
                "abstract": (p.get("abstract") or "")[:300],
                "source": "semantic_scholar",
            }
        )
    return result


# ── CrossRef ──────────────────────────────────────────────────────────────────

def search_crossref(query: str, top_k: int = 5) -> list[dict]:
    """
    CrossRef 公开API，专注 DOI 验证和期刊元数据，被引量覆盖广。
    文档: https://api.crossref.org/swagger-ui/index.html
    """
    mailto = os.getenv("CROSSREF_MAILTO", "mathmodel-agent@example.com")
    url = (
        "https://api.crossref.org/works"
        f"?query={quote(query)}&rows={top_k}&sort=relevance&mailto={quote(mailto)}"
    )
    headers = {"User-Agent": f"mathmodel-agent/1.0 (mailto:{mailto})"}
    try:
        data = _http_get_json(url, headers=headers, timeout=25)
    except Exception:
        return []

    items = (data.get("message") or {}).get("items", []) if isinstance(data, dict) else []
    result: list[dict] = []
    for item in items[:top_k]:
        title_list = item.get("title") or []
        title = title_list[0] if title_list else ""
        authors = []
        for a in (item.get("author") or [])[:3]:
            given = a.get("given", "")
            family = a.get("family", "")
            authors.append(f"{given} {family}".strip())
        year = ""
        pub_date = item.get("published-print") or item.get("published-online") or {}
        dp = pub_date.get("date-parts", [[]])[0]
        if dp:
            year = str(dp[0])
        result.append(
            {
                "title": title,
                "year": year,
                "authors": authors,
                "doi": item.get("DOI", ""),
                "journal": (item.get("container-title") or [""])[0],
                "cited_by": item.get("is-referenced-by-count", 0),
                "source": "crossref",
            }
        )
    return result


# ── 合并检索（主入口）────────────────────────────────────────────────────────

def search_all_sources(query: str, top_k_each: int = 5) -> dict[str, list[dict]]:
    """
    同时调用所有可用数据库，返回各来源结果。
    已知不可用的数据库（WoS/CNKI）仅标记状态，不阻塞。
    """
    results = {
        "openalex": search_openalex_works(query, top_k=top_k_each),
        "semantic_scholar": search_semantic_scholar(query, top_k=top_k_each),
        "crossref": search_crossref(query, top_k=top_k_each),
        "github": search_github_repos(query, top_k=top_k_each),
        "manual_exports": load_manual_bibtex_entries(),  # WoS / CNKI 手动导出
    }
    return results


# ── 手动导出文献（WoS / CNKI）────────────────────────────────────────────────

def load_manual_bibtex_entries() -> list[dict]:
    """
    读取 reference/manual/ 下所有 .bib 文件。
    用户将 WoS / CNKI 导出的 BibTeX 放在这个目录，AI 自动合并。
    """
    entries: list[dict] = []
    if not MANUAL_REF_DIR.exists():
        return entries
    for bib_file in MANUAL_REF_DIR.glob("*.bib"):
        try:
            content = bib_file.read_text(encoding="utf-8", errors="replace")
            entries.append({
                "source": f"manual:{bib_file.name}",
                "bibtex_raw": content,
                "entry_count": content.count("@"),
            })
        except Exception:
            continue
    return entries


def merge_manual_bibtex_to_paper() -> Path:
    """
    将 reference/manual/*.bib 全部合并追加到 paper/references_draft.bib。
    由写作手调用，或用户手动触发。
    """
    paper_dir = Path(os.getenv("PAPER_DIR", str(BASE_DIR / "paper")))
    paper_dir.mkdir(parents=True, exist_ok=True)
    draft_bib = paper_dir / "references_draft.bib"

    existing = draft_bib.read_text(encoding="utf-8") if draft_bib.exists() else ""
    appended = 0
    for bib_file in MANUAL_REF_DIR.glob("*.bib"):
        content = bib_file.read_text(encoding="utf-8", errors="replace").strip()
        if content and content not in existing:
            existing += f"\n\n% ── 来自 {bib_file.name} ──\n{content}"
            appended += 1
    draft_bib.write_text(existing, encoding="utf-8")
    print(f"[KnowledgeBase] 合并了 {appended} 个手动导出文件 → {draft_bib}")
    return draft_bib


# ── 数据源状态 ────────────────────────────────────────────────────────────────

def data_source_status() -> dict:
    manual_count = len(list(MANUAL_REF_DIR.glob("*.bib"))) if MANUAL_REF_DIR.exists() else 0
    return {
        "cnki": {
            "status": "manual_export_supported",
            "reason": "无公开API；请手动导出BibTeX到 reference/manual/ 目录，AI会自动读取",
            "manual_dir": str(MANUAL_REF_DIR),
            "files_found": manual_count,
        },
        "web_of_science": {
            "status": "manual_export_supported",
            "reason": "需机构账号；请手动导出BibTeX到 reference/manual/ 目录，AI会自动读取",
            "manual_dir": str(MANUAL_REF_DIR),
            "files_found": manual_count,
        },
        "openalex": {
            "status": "available",
            "reason": "完全开放API，可直接检索",
        },
        "semantic_scholar": {
            "status": "available",
            "reason": "完全开放API，数学/CS/工程覆盖好，可选 S2_API_KEY 提升速率",
        },
        "crossref": {
            "status": "available",
            "reason": "完全开放API，DOI验证权威来源",
        },
        "github": {
            "status": "available",
            "reason": "可通过GitHub REST API检索公开仓库",
        },
    }


def save_knowledge_snapshot(name: str, payload: dict) -> Path:
    path = KNOWLEDGE_BASE_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
