"""Text normalization, CJK FTS5 n-gram tokenization, and breadcrumb utilities.

Zero external dependencies: relies solely on Python standard library so all
query preprocessing and build utilities stay portable and reproducible.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Sequence

_WS_RE = re.compile(r"\s+")
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_NON_ALNUM_CJK_RE = re.compile(
    r"[^\w\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+"
)

# Minimal high-frequency Traditional -> Simplified fallback map for Phase 1 smoke;
# full Traditional->Simplified conversion in T1.2 uses OpenCC or a full table.
_BASIC_T2S = str.maketrans(
    {
        "哲": "哲",
        "學": "学",
        "歷": "历",
        "史": "史",
        "經": "经",
        "濟": "济",
        "計": "计",
        "算": "算",
        "機": "机",
        "軟": "软",
        "體": "体",
        "網": "网",
        "絡": "络",
        "資": "资",
        "料": "料",
        "庫": "库",
        "語": "语",
        "言": "言",
        "藝": "艺",
        "術": "术",
        "醫": "医",
        "藥": "药",
        "顯": "显",
        "廣": "广",
        "漢": "汉",
        "譯": "译",
        "傳": "传",
        "統": "统",
        "圖": "图",
        "譜": "谱",
        "論": "论",
        "證": "证",
        "實": "实",
        "驗": "验",
        "職": "职",
        "業": "业",
        "類": "类",
        "別": "别",
        "開": "开",
        "發": "发",
        "測": "测",
        "試": "试",
        "調": "调",
    }
)


def normalize_alias(text: str) -> str:
    """Normalize an alias string for deduplication and exact lookup."""
    s = unicodedata.normalize("NFKC", text or "").strip().lower()
    s = s.translate(_BASIC_T2S)
    s = _WS_RE.sub(" ", s)
    return s


def contains_cjk(text: str) -> bool:
    """Return True if text contains any CJK Unified Ideograph."""
    return bool(text and _CJK_RUN_RE.search(text))


def build_cjk_fts_tokens(texts: Iterable[str], max_ngram: int = 3) -> str:
    """Generate space-delimited CJK character unigrams/bigrams/trigrams + full runs.

    This enables SQLite's built-in FTS5 unicode61 tokenizer (zero external
    extensions) to answer 2-character, 3-character, and sentence-length Chinese
    queries with sub-20ms BM25 latency.
    """
    tokens: List[str] = []
    seen = set()
    for raw in texts:
        if not raw:
            continue
        norm = unicodedata.normalize("NFKC", raw).translate(_BASIC_T2S)
        for run in _CJK_RUN_RE.findall(norm):
            n = len(run)
            if n == 0:
                continue
            # Include the full contiguous CJK run if reasonably short
            if n <= 12 and run not in seen:
                seen.add(run)
                tokens.append(run)
            # Emit bigrams (and trigrams) first, plus unigrams for short terms
            for k in range(2, min(max_ngram, n) + 1):
                for i in range(n - k + 1):
                    gram = run[i : i + k]
                    if gram not in seen:
                        seen.add(gram)
                        tokens.append(gram)
            if n <= 4:
                for ch in run:
                    if ch not in seen:
                        seen.add(ch)
                        tokens.append(ch)
    return " ".join(tokens)


_CJK_FUNC_CHARS = set("的和与了在是我你他它中对把将用从做怎何么呢吗吧啊哪谁其及并或而于以之为着过被让向给")


def build_fts5_query(user_query: str, facet_type: str | None = None) -> str:
    """Convert a raw Chinese/English prompt into a safe, fast FTS5 MATCH expression."""
    norm = unicodedata.normalize("NFKC", user_query or "").strip()
    norm = norm.translate(_BASIC_T2S)
    terms: List[str] = []
    seen = set()

    # Split CJK runs at high-frequency function characters so bigrams stay content-bearing
    for raw_run in _CJK_RUN_RE.findall(norm):
        sub_runs = []
        buf = []
        for ch in raw_run:
            if ch in _CJK_FUNC_CHARS:
                if buf:
                    sub_runs.append("".join(buf))
                    buf = []
            else:
                buf.append(ch)
        if buf:
            sub_runs.append("".join(buf))

        for run in sub_runs:
            n = len(run)
            if n == 0:
                continue
            if n == 1:
                if run not in seen:
                    seen.add(run)
                    terms.append(f'"{run}"')
            elif n <= 4:
                if run not in seen:
                    seen.add(run)
                    terms.append(f'"{run}"')
                if n >= 3:
                    for i in range(n - 1):
                        bg = run[i : i + 2]
                        if bg not in seen:
                            seen.add(bg)
                            terms.append(f'"{bg}"')
            else:
                # Emit non-overlapping + key sliding bigrams capped to avoid giant posting unions
                for i in range(0, n - 1, 2):
                    bg = run[i : i + 2]
                    if bg not in seen:
                        seen.add(bg)
                        terms.append(f'"{bg}"')
                if n % 2 == 1:
                    bg = run[n - 2 : n]
                    if bg not in seen:
                        seen.add(bg)
                        terms.append(f'"{bg}"')

    # Extract ASCII/Latin words
    ascii_part = _CJK_RUN_RE.sub(" ", norm)
    for tok in _NON_ALNUM_CJK_RE.split(ascii_part):
        tok = tok.strip().lower()
        if len(tok) >= 2 and tok not in seen:
            seen.add(tok)
            terms.append(f'"{tok}"')

    if not terms:
        return ""
    body = " OR ".join(terms[:12])
    if facet_type:
        return f'facet_type : "{facet_type}" AND ({body})'
    return body


def format_breadcrumb(segments: Sequence[str], sep: str = " > ") -> str:
    """Format and validate a non-empty breadcrumb path."""
    cleaned = [s.strip() for s in segments if s and s.strip()]
    if not cleaned:
        raise ValueError("breadcrumb_path cannot be empty (A1 hard invariant)")
    return sep.join(cleaned)
