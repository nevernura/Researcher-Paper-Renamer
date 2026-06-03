"""
core.py — PDF renaming logic, independent of Streamlit.

Everything here is plain Python so it can be unit-tested and reused.
Heavy/optional dependencies (PyMuPDF, requests, google-generativeai) are
imported lazily inside the functions that need them, so the pure string
logic can be imported and tested without those packages installed.
"""

import re
import json
import unicodedata
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration defaults (overridable from the UI)
# ---------------------------------------------------------------------------
DEFAULT_MAX_TITLE_WORDS = 10
DEFAULT_MAX_FILENAME_LEN = 150          # characters, before the .pdf extension
GEMINI_MODEL = "gemini-2.5-flash-lite"  # free-tier model; adjust if Google renames it
CROSSREF_BASE = "https://api.crossref.org/works/"

# DOIs look like: 10.1234/some.suffix  (suffix is fairly permissive)
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)

# A 4-digit year in a plausible publishing range
_THIS_YEAR = datetime.now().year
YEAR_RE = re.compile(r"(?<!\d)(1[5-9]\d{2}|20\d{2}|21\d{2})(?!\d)")
_YEAR_HINT_RE = re.compile(
    r"(copyright|©|\(c\)|published|accepted|received|\bissn\b|vol\.|volume)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pure helpers: filename construction
# ---------------------------------------------------------------------------
def strip_accents(text: str) -> str:
    """Turn accented characters into their closest ASCII equivalents."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def camel_case_title(title: str, max_words: int = DEFAULT_MAX_TITLE_WORDS) -> str:
    """
    Convert a title to CamelCase, keeping at most `max_words` words and
    stripping anything that isn't a letter or digit.

    'Attention Is All You Need' -> 'AttentionIsAllYouNeed'
    Acronyms are preserved (BERT stays BERT) by leaving non-initial
    characters untouched.
    """
    if not title:
        return ""
    cleaned = strip_accents(title)
    words = re.findall(r"[A-Za-z0-9]+", cleaned)
    words = words[:max_words]
    return "".join(w[:1].upper() + w[1:] for w in words)


def sanitize_year(year) -> str:
    """Return a clean 4-digit year string, or '' if not plausible."""
    if year is None:
        return ""
    m = re.search(r"\d{4}", str(year))
    if not m:
        return ""
    y = m.group(0)
    if 1500 <= int(y) <= _THIS_YEAR + 1:
        return y
    return ""


def build_basename(
    year,
    title,
    max_words: int = DEFAULT_MAX_TITLE_WORDS,
    max_len: int = DEFAULT_MAX_FILENAME_LEN,
) -> str:
    """Build the filename stem (no extension) as Year_TitleInCamelCase."""
    camel = camel_case_title(title, max_words)
    y = sanitize_year(year)
    if y and camel:
        base = f"{y}_{camel}"
    elif camel:
        base = camel
    elif y:
        base = y
    else:
        base = "untitled"
    return base[:max_len]


def dedupe_name(base: str, used: set, ext: str = ".pdf") -> str:
    """
    Return a filename (base + ext) that isn't already in `used`, appending
    (2), (3)... on collision. Mutates `used` to include the chosen name.
    """
    candidate = base + ext
    n = 2
    while candidate.lower() in used:
        candidate = f"{base}({n}){ext}"
        n += 1
    used.add(candidate.lower())
    return candidate


# ---------------------------------------------------------------------------
# Pure helpers: parsing
# ---------------------------------------------------------------------------
def extract_doi(text: str):
    """Find the first DOI in the text, trimmed of trailing punctuation."""
    if not text:
        return None
    m = DOI_RE.search(text)
    if not m:
        return None
    return m.group(0).rstrip(".,;)]}>'\"")


def parse_crossref_message(message: dict):
    """
    Pull (title, year) out of a CrossRef /works `message` object.
    Returns ('', '') if nothing usable is found.
    """
    title = ""
    titles = message.get("title") or []
    if titles and isinstance(titles, list):
        title = (titles[0] or "").strip()

    year = ""
    for key in ("published-print", "published-online", "issued",
                "published", "created"):
        node = message.get(key)
        if node and isinstance(node, dict):
            parts = node.get("date-parts")
            if parts and parts[0]:
                year = str(parts[0][0])
                break
    return title, year


def parse_gemini_json(raw: str):
    """
    Parse the model's reply, which should be JSON like
    {"title": "...", "year": 2021}. Tolerates ```json fences and
    surrounding prose. Returns (title, year) as strings.
    """
    if not raw:
        return "", ""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    # Grab the first {...} block if there's extra prose around it.
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        text = brace.group(0)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return "", ""
    title = str(data.get("title", "") or "").strip()
    year = str(data.get("year", "") or "").strip()
    return title, year


def heuristic_extract(text: str):
    """
    Last-resort guess when there's no DOI and no Gemini key.
    Title  = first substantial line of the first page.
    Year   = a plausible year, preferring lines with publishing hints.
    Returns (title, year).
    """
    if not text:
        return "", ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    title = ""
    for ln in lines:
        # Skip obvious non-title lines (urls, very short fragments, pure numbers)
        if len(ln) < 8:
            continue
        if ln.lower().startswith(("http", "www.", "doi", "arxiv")):
            continue
        if re.fullmatch(r"[\d\s.\-]+", ln):
            continue
        title = ln
        break

    year = ""
    hinted = [ln for ln in lines if _YEAR_HINT_RE.search(ln)]
    for ln in hinted:
        m = YEAR_RE.search(ln)
        if m:
            year = m.group(0)
            break
    if not year:
        m = YEAR_RE.search(text)
        if m:
            year = m.group(0)
    return title, year


# ---------------------------------------------------------------------------
# I/O-bound pieces (lazy imports so the module loads without these installed)
# ---------------------------------------------------------------------------
def first_page_text(pdf_source) -> str:
    """
    Extract text from page 1.
    `pdf_source` may be a filesystem path (str) or raw bytes.
    Returns '' for image-only / scanned PDFs (the caller flags those).
    """
    import fitz  # PyMuPDF

    if isinstance(pdf_source, (bytes, bytearray)):
        doc = fitz.open(stream=pdf_source, filetype="pdf")
    else:
        doc = fitz.open(pdf_source)
    try:
        if doc.page_count == 0:
            return ""
        return doc.load_page(0).get_text("text") or ""
    finally:
        doc.close()


def crossref_lookup(doi: str, contact_email: str = ""):
    """
    Query CrossRef for a DOI. Returns (title, year) or ('', '').
    Includes a polite User-Agent with a contact email when provided.
    """
    import requests

    ua = "PDF-Renamer/1.0"
    if contact_email:
        ua += f" (mailto:{contact_email})"
    try:
        resp = requests.get(
            CROSSREF_BASE + requests.utils.quote(doi),
            headers={"User-Agent": ua},
            timeout=15,
        )
        if resp.status_code != 200:
            return "", ""
        message = resp.json().get("message", {})
        return parse_crossref_message(message)
    except Exception:
        return "", ""


def gemini_extract(text: str, api_key: str, model: str = GEMINI_MODEL):
    """
    Ask Gemini for the title + year of the paper. Returns (title, year).
    Only the first ~6000 characters of page text are sent (plenty for a
    title page, keeps token use tiny).
    """
    if not api_key or not text:
        return "", ""
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    prompt = (
        "You are given the raw text of the FIRST PAGE of an academic paper. "
        "Identify the paper's title and its publication year. "
        "Prefer the publication/copyright year over 'received' or 'accepted' "
        "dates. Respond with ONLY a JSON object and nothing else, in the form: "
        '{"title": "<exact title>", "year": <4-digit-year or null>}\n\n'
        "FIRST PAGE TEXT:\n" + text[:6000]
    )
    try:
        model_obj = genai.GenerativeModel(model)
        resp = model_obj.generate_content(prompt)
        return parse_gemini_json(getattr(resp, "text", ""))
    except Exception:
        return "", ""


# ---------------------------------------------------------------------------
# Orchestration: the hybrid pipeline
# ---------------------------------------------------------------------------
def extract_metadata(text: str, gemini_key: str = "", contact_email: str = ""):
    """
    Hybrid extraction for one paper's first-page text.

    Order: DOI -> CrossRef  ->  Gemini (if key)  ->  heuristic guess.
    Returns a dict: {title, year, source, needs_review, doi}
    where `source` is one of: crossref / gemini / guess / none.
    """
    result = {"title": "", "year": "", "source": "none",
              "needs_review": True, "doi": ""}

    if not text or not text.strip():
        result["source"] = "no_text"          # scanned / image-only PDF
        return result

    doi = extract_doi(text)
    if doi:
        result["doi"] = doi
        title, year = crossref_lookup(doi, contact_email)
        if title:
            result.update(title=title, year=year, source="crossref",
                          needs_review=False)
            return result

    if gemini_key:
        title, year = gemini_extract(text, gemini_key)
        if title:
            result.update(title=title, year=year, source="gemini",
                          needs_review=False)
            return result

    title, year = heuristic_extract(text)
    result.update(title=title, year=year, source="guess",
                  needs_review=True)      # always invite a check on guesses
    return result
