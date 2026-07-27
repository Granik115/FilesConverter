"""TXT/FB2 conversion and merging without GUI dependencies."""

from __future__ import annotations

import os
import re
import tempfile
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from filesconverter import __version__

FB2_NS = "http://www.gribuser.ru/xml/fictionbook/2.0"
XLINK_NS = "http://www.w3.org/1999/xlink"
SUPPORTED_SUFFIXES = {".txt", ".fb2"}
CHAPTER_RE = re.compile(
    r"^(?:глава|часть|книга|пролог|эпилог|chapter|part|book|prologue|epilogue)"
    r"(?:\s+|$)",
    re.IGNORECASE,
)
XML_FORBIDDEN_RE = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]"
)

ET.register_namespace("", FB2_NS)
ET.register_namespace("l", XLINK_NS)


class ConversionError(RuntimeError):
    """A user-facing conversion error."""


@dataclass(frozen=True)
class BookMetadata:
    title: str = ""
    author: str = ""
    language: str = "ru"


@dataclass(frozen=True)
class ConversionResult:
    source: Path
    destination: Path


def qname(tag: str) -> str:
    return f"{{{FB2_NS}}}{tag}"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_txt(path: str | Path) -> str:
    """Read a text file using common Russian ebook encodings."""

    source = Path(path)
    data = source.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")

    for encoding in ("utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ConversionError(
        f"Не удалось определить кодировку TXT-файла «{source.name}». "
        "Поддерживаются UTF-8, UTF-16 и Windows-1251."
    )


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    text = XML_FORBIDDEN_RE.sub("", text)
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def text_blocks(text: str) -> list[str]:
    """Turn blank-line-separated text into paragraphs without preserving noise."""

    normalized = normalize_text(text)
    if not normalized:
        return []
    chunks = re.split(r"\n[ \t]*\n+", normalized)
    blocks: list[str] = []
    for chunk in chunks:
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if lines:
            blocks.append(" ".join(lines))
    return blocks


def split_author(author: str) -> tuple[str, str]:
    parts = author.strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return "", parts[0]
    return " ".join(parts[:-1]), parts[-1]


def _add_author(parent: ET.Element, author: str, fallback: str = "FilesConverter") -> None:
    author_node = ET.SubElement(parent, qname("author"))
    first_name, last_name = split_author(author)
    if first_name or last_name:
        ET.SubElement(author_node, qname("first-name")).text = first_name
        ET.SubElement(author_node, qname("last-name")).text = last_name
    else:
        ET.SubElement(author_node, qname("nickname")).text = fallback


def _new_fb2(metadata: BookMetadata, fallback_title: str) -> tuple[ET.Element, ET.Element]:
    root = ET.Element(qname("FictionBook"))
    description = ET.SubElement(root, qname("description"))
    title_info = ET.SubElement(description, qname("title-info"))
    ET.SubElement(title_info, qname("genre")).text = "prose_contemporary"
    _add_author(title_info, metadata.author)
    ET.SubElement(title_info, qname("book-title")).text = metadata.title.strip() or fallback_title
    ET.SubElement(title_info, qname("lang")).text = metadata.language.strip() or "ru"

    document_info = ET.SubElement(description, qname("document-info"))
    _add_author(document_info, metadata.author)
    ET.SubElement(document_info, qname("program-used")).text = (
        f"FilesConverter {__version__}"
    )
    today = datetime.now(timezone.utc).date()
    date_node = ET.SubElement(document_info, qname("date"))
    date_node.set("value", today.isoformat())
    date_node.text = today.strftime("%d.%m.%Y")
    ET.SubElement(document_info, qname("id")).text = str(uuid.uuid4())
    ET.SubElement(document_info, qname("version")).text = "1.0"
    return root, ET.SubElement(root, qname("body"))


def _append_text_sections(parent: ET.Element, text: str) -> None:
    blocks = text_blocks(text)
    current = ET.SubElement(parent, qname("section"))
    for block in blocks:
        if CHAPTER_RE.match(block):
            if len(current) == 0:
                title = ET.SubElement(current, qname("title"))
            else:
                current = ET.SubElement(parent, qname("section"))
                title = ET.SubElement(current, qname("title"))
            ET.SubElement(title, qname("p")).text = block
        else:
            ET.SubElement(current, qname("p")).text = block


def _write_xml(root: ET.Element, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        tree.write(temporary, encoding="utf-8", xml_declaration=True)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _write_text(text: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(normalize_text(text))
        handle.write("\n")
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def txt_to_fb2(
    source: str | Path,
    destination: str | Path,
    metadata: BookMetadata | None = None,
) -> Path:
    source_path = Path(source)
    destination_path = Path(destination)
    text = read_txt(source_path)
    effective = metadata or BookMetadata(title=source_path.stem)
    root, body = _new_fb2(effective, source_path.stem)
    _append_text_sections(body, text)
    _write_xml(root, destination_path)
    return destination_path


def _safe_parse_fb2(path: Path) -> ET.Element:
    prefix = path.read_bytes()[:8192].upper()
    if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
        raise ConversionError(f"Небезопасная XML-разметка в файле «{path.name}».")
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ConversionError(f"Не удалось прочитать FB2-файл «{path.name}»: {exc}") from exc


def _element_text(element: ET.Element) -> str:
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip()


def _collect_blocks(element: ET.Element, output: list[str]) -> None:
    for child in element:
        name = local_name(child.tag)
        if name in {"p", "subtitle", "v", "text-author"}:
            value = _element_text(child)
            if value:
                output.append(value)
        elif name == "empty-line":
            output.append("")
        elif name == "title":
            title_lines = [
                _element_text(item)
                for item in child
                if local_name(item.tag) in {"p", "subtitle"} and _element_text(item)
            ]
            if title_lines:
                output.append("\n".join(title_lines))
        elif name == "table":
            for row in child:
                if local_name(row.tag) == "tr":
                    cells = [
                        _element_text(cell)
                        for cell in row
                        if local_name(cell.tag) in {"td", "th"}
                    ]
                    if cells:
                        output.append("\t".join(cells))
        elif name in {
            "section",
            "poem",
            "stanza",
            "cite",
            "epigraph",
            "annotation",
        }:
            _collect_blocks(child, output)


def fb2_metadata(source: str | Path) -> BookMetadata:
    source_path = Path(source)
    root = _safe_parse_fb2(source_path)
    title = root.findtext(f".//{qname('title-info')}/{qname('book-title')}") or source_path.stem
    language = root.findtext(f".//{qname('title-info')}/{qname('lang')}") or "ru"
    author_node = root.find(f".//{qname('title-info')}/{qname('author')}")
    author = ""
    if author_node is not None:
        nickname = author_node.findtext(qname("nickname")) or ""
        first = author_node.findtext(qname("first-name")) or ""
        middle = author_node.findtext(qname("middle-name")) or ""
        last = author_node.findtext(qname("last-name")) or ""
        author = nickname.strip() or " ".join(part for part in (first, middle, last) if part).strip()
    return BookMetadata(title=title.strip(), author=author, language=language.strip())


def fb2_text(source: str | Path) -> str:
    source_path = Path(source)
    root = _safe_parse_fb2(source_path)
    blocks: list[str] = []
    bodies = [node for node in root if local_name(node.tag) == "body"]
    for index, body in enumerate(bodies):
        if index and blocks and blocks[-1] != "":
            blocks.append("")
        _collect_blocks(body, blocks)

    cleaned: list[str] = []
    for block in blocks:
        if block:
            cleaned.append(block)
        elif cleaned and cleaned[-1] != "":
            cleaned.append("")
    return "\n\n".join(block for block in cleaned if block).strip()


def fb2_to_txt(source: str | Path, destination: str | Path) -> Path:
    destination_path = Path(destination)
    _write_text(fb2_text(source), destination_path)
    return destination_path


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 10000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise ConversionError(f"Не удалось подобрать свободное имя для «{path.name}».")


def convert_many(
    sources: Iterable[str | Path],
    output_directory: str | Path,
    *,
    overwrite: bool = False,
) -> list[ConversionResult]:
    output = Path(output_directory)
    results: list[ConversionResult] = []
    for item in sources:
        source = Path(item)
        suffix = source.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise ConversionError(f"Неподдерживаемый формат: «{source.name}».")
        target_suffix = ".fb2" if suffix == ".txt" else ".txt"
        destination = output / f"{source.stem}{target_suffix}"
        if not overwrite:
            destination = _unique_destination(destination)
        if suffix == ".txt":
            txt_to_fb2(source, destination)
        else:
            fb2_to_txt(source, destination)
        results.append(ConversionResult(source, destination))
    return results


def _source_text(source: Path) -> str:
    if source.suffix.lower() == ".txt":
        return read_txt(source)
    if source.suffix.lower() == ".fb2":
        return fb2_text(source)
    raise ConversionError(f"Неподдерживаемый формат: «{source.name}».")


def _source_title(source: Path) -> str:
    if source.suffix.lower() == ".fb2":
        try:
            return fb2_metadata(source).title
        except ConversionError:
            pass
    return source.stem


def merge_files(
    sources: Sequence[str | Path],
    destination: str | Path,
    *,
    output_format: str | None = None,
    metadata: BookMetadata | None = None,
    add_source_titles: bool = True,
) -> Path:
    if not sources:
        raise ConversionError("Добавьте хотя бы один файл.")
    source_paths = [Path(item) for item in sources]
    for source in source_paths:
        if source.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ConversionError(f"Неподдерживаемый формат: «{source.name}».")

    destination_path = Path(destination)
    selected_format = (output_format or destination_path.suffix.lstrip(".")).lower()
    if selected_format not in {"txt", "fb2"}:
        raise ConversionError("Формат результата должен быть TXT или FB2.")
    destination_path = destination_path.with_suffix(f".{selected_format}")

    if selected_format == "txt":
        chunks: list[str] = []
        for source in source_paths:
            content = normalize_text(_source_text(source))
            if add_source_titles:
                content = f"{_source_title(source)}\n\n{content}"
            chunks.append(content)
        _write_text("\n\n\n* * *\n\n\n".join(chunks), destination_path)
        return destination_path

    effective = metadata or BookMetadata(title=destination_path.stem)
    root, body = _new_fb2(effective, destination_path.stem)
    for source in source_paths:
        book_section = ET.SubElement(body, qname("section"))
        if add_source_titles:
            title = ET.SubElement(book_section, qname("title"))
            ET.SubElement(title, qname("p")).text = _source_title(source)
        _append_text_sections(book_section, _source_text(source))
    _write_xml(root, destination_path)
    return destination_path
