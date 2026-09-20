"""Document parsing and chunking.

parse_file()  -> unstructured partitions the file (PDF, Word, PowerPoint, Excel, Markdown, text,
                 photos via Tesseract OCR) and the elements are rebuilt as Markdown sections.
chunk()       -> sections are split into ~380-token chunks (tiktoken cl100k_base, 60-token overlap);
                 every chunk starts with "<document title> - <section heading>" so it embeds with context.

Parsing is CPU-bound and runs in a worker thread. Results are cached by file hash under
storage/.cache/parsed so restarts do not re-parse unchanged seed files.
"""

import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from io import StringIO
from pathlib import Path

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

from back_end.core.logging import get_logger

log = get_logger(__name__)

PARSER_VERSION = "3"
_ENC = None


def encoder():
    global _ENC
    if _ENC is None:
        _ENC = tiktoken.get_encoding("cl100k_base")
    return _ENC


def count_tokens(text: str) -> int:
    return len(encoder().encode(text))


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


@dataclass(slots=True)
class Section:
    heading: str
    text: str


@dataclass(slots=True)
class Parsed:
    markdown: str
    sections: list[Section]
    pages: int | None = None
    ocr_text: str = ""
    parser: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "Parsed":
        d = json.loads(raw)
        d["sections"] = [Section(**s) for s in d["sections"]]
        return cls(**d)


@dataclass(slots=True)
class Chunk:
    index: int
    heading: str
    text: str
    tokens: int


# ---------------------------------------------------------------------------- parsing


def _table_markdown(html: str | None, fallback: str) -> str:
    if not html:
        return fallback
    try:
        import pandas as pd

        frames = pd.read_html(StringIO(html))
    except Exception:  # noqa: BLE001
        return fallback
    if not frames:
        return fallback
    df = frames[0].fillna("")
    header = [str(c) if not str(c).startswith("Unnamed") else "" for c in df.columns]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(str(v).replace("|", "/") for v in row) + " |")
    return "\n".join(lines)


def _elements_to_sections(elements, title: str) -> tuple[str, list[Section]]:
    md_lines: list[str] = []
    sections: list[Section] = []
    heading = title
    buf: list[str] = []
    first_title = True

    def flush():
        text = "\n".join(buf).strip()
        if text:
            sections.append(Section(heading, text))
        buf.clear()

    for el in elements:
        cat = getattr(el, "category", "")
        text = (getattr(el, "text", "") or "").strip()
        if not text:
            continue
        if cat == "Title":
            depth = getattr(el.metadata, "category_depth", None)
            level = 1 if first_title and not md_lines else min(3, 2 + (depth or 0))
            first_title = False
            if len(text) > 120:  # long "titles" are usually bold paragraphs
                md_lines.append(text)
                buf.append(text)
                continue
            flush()
            heading = text
            md_lines.append(f"{'#' * level} {text}")
        elif cat == "ListItem":
            md_lines.append(f"- {text}")
            buf.append(f"- {text}")
        elif cat == "Table":
            table = _table_markdown(getattr(el.metadata, "text_as_html", None), text)
            md_lines.append(table)
            buf.append(table)
        elif cat in ("Header", "Footer", "PageNumber"):
            continue
        else:
            md_lines.append(text)
            buf.append(text)
        md_lines.append("")
    flush()
    return "\n".join(md_lines).strip(), sections


def _markdown_sections(text: str, title: str) -> list[Section]:
    sections: list[Section] = []
    heading = title
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            body = "\n".join(buf).strip()
            if body:
                sections.append(Section(heading, body))
            buf = []
            heading = m.group(2).strip()
            continue
        buf.append(line)
    body = "\n".join(buf).strip()
    if body:
        sections.append(Section(heading, body))
    return sections


def parse_file(path: Path, kind: str, title: str) -> Parsed:
    if kind in ("md", "txt"):
        text = path.read_text(encoding="utf-8", errors="replace")
        return Parsed(markdown=text, sections=_markdown_sections(text, title), parser=f"text/{kind}")

    if kind in ("png", "jpg"):
        ocr = ""
        warnings: list[str] = []
        if tesseract_available():
            try:
                from unstructured.partition.image import partition_image

                els = partition_image(filename=str(path), strategy="ocr_only", languages=["eng"])
                ocr = "\n".join(e.text for e in els if getattr(e, "text", "").strip())
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"OCR failed: {exc}")
        else:
            warnings.append("Tesseract is not installed; photo text was not extracted")
        body = f"Photo: {title}" + (f"\n\nText visible in the photo:\n\n{ocr}" if ocr.strip() else "")
        return Parsed(markdown=body, sections=[Section(title, body)], ocr_text=ocr, parser="image/ocr", warnings=warnings)

    if kind == "pdf":
        from unstructured.partition.pdf import partition_pdf

        els = partition_pdf(filename=str(path), strategy="fast")
    elif kind == "docx":
        from unstructured.partition.docx import partition_docx

        els = partition_docx(filename=str(path), infer_table_structure=True)
    elif kind == "pptx":
        from unstructured.partition.pptx import partition_pptx

        els = partition_pptx(filename=str(path), infer_table_structure=True)
    elif kind == "xlsx":
        from unstructured.partition.xlsx import partition_xlsx

        els = partition_xlsx(filename=str(path), infer_table_structure=True)
    else:
        raise ValueError(f"Unsupported kind {kind}")
    pages = max((getattr(e.metadata, "page_number", None) or 0 for e in els), default=0) or None
    markdown, sections = _elements_to_sections(els, title)
    if not sections:
        sections = [Section(title, markdown or title)]
    return Parsed(markdown=markdown, sections=sections, pages=pages, parser=f"unstructured/{kind}")


def parse_cached(path: Path, kind: str, title: str, cache_dir: Path) -> Parsed:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    cache = cache_dir / f"{digest}-{kind}-p{PARSER_VERSION}.json"
    if cache.exists():
        try:
            return Parsed.from_json(cache.read_text())
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    parsed = parse_file(path, kind, title)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(parsed.to_json())
    return parsed


# ---------------------------------------------------------------------------- chunking


def chunk(sections: list[Section], title: str, *, chunk_tokens: int = 380, overlap: int = 60) -> list[Chunk]:
    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name="cl100k_base",
        chunk_size=chunk_tokens,
        chunk_overlap=overlap,
        separators=["\n\n", "\n- ", "\n", ". ", " ", ""],
    )
    out: list[Chunk] = []
    for sec in sections:
        prefix = title if sec.heading == title else f"{title} - {sec.heading}"
        for piece in splitter.split_text(sec.text):
            piece = piece.strip()
            if len(piece) < 12:
                continue
            text = f"{prefix}\n{piece}"
            out.append(Chunk(index=len(out), heading=sec.heading, text=text, tokens=count_tokens(text)))
    return out


def summary_from(markdown: str, limit: int = 240) -> str:
    for block in re.split(r"\n\s*\n", markdown):
        line = block.strip()
        if not line or line.startswith(("#", "|", "-", ">", "Photo:")):
            continue
        line = re.sub(r"[*_`]", "", line)
        return line[: limit - 1] + "…" if len(line) > limit else line
    return ""
