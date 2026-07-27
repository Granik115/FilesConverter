import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from filesconverter.core import (
    BookMetadata,
    ConversionError,
    convert_many,
    fb2_metadata,
    fb2_text,
    fb2_to_txt,
    merge_files,
    txt_to_fb2,
)

SAMPLE_TEXT = """Барьер Ориона

Глава 1

Первый абзац с русским текстом.

Второй абзац.

Глава 2

Продолжение книги.
"""


def test_txt_to_fb2_and_back(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text(SAMPLE_TEXT, encoding="utf-8")
    fb2 = tmp_path / "book.fb2"

    txt_to_fb2(
        source,
        fb2,
        BookMetadata(title="Тестовая книга", author="Иван Иванов", language="ru"),
    )

    root = ET.parse(fb2).getroot()
    assert root.tag.endswith("FictionBook")
    assert fb2_metadata(fb2).title == "Тестовая книга"
    extracted = fb2_text(fb2)
    assert "Глава 1" in extracted
    assert "Первый абзац с русским текстом." in extracted
    assert extracted.index("Глава 1") < extracted.index("Глава 2")

    restored = tmp_path / "restored.txt"
    fb2_to_txt(fb2, restored)
    assert "Продолжение книги." in restored.read_text(encoding="utf-8")


def test_reads_windows_1251(tmp_path: Path) -> None:
    source = tmp_path / "cp1251.txt"
    source.write_bytes("Глава 1\n\nТекст в старой кодировке.".encode("cp1251"))
    destination = tmp_path / "cp1251.fb2"
    txt_to_fb2(source, destination)
    assert "Текст в старой кодировке." in fb2_text(destination)


def test_fb2_extraction_keeps_inline_formatting(tmp_path: Path) -> None:
    source = tmp_path / "inline.fb2"
    source.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
  <description><title-info><genre>prose</genre><author><nickname>A</nickname></author>
  <book-title>Книга</book-title><lang>ru</lang></title-info></description>
  <body><section><title><p>Глава 1</p></title>
  <p>Обычный <emphasis>важный</emphasis> текст.</p></section></body>
</FictionBook>""",
        encoding="utf-8",
    )
    assert fb2_text(source) == "Глава 1\n\nОбычный важный текст."


def test_merge_txt_respects_order(tmp_path: Path) -> None:
    first = tmp_path / "one.txt"
    second = tmp_path / "two.txt"
    first.write_text("Первый файл", encoding="utf-8")
    second.write_text("Второй файл", encoding="utf-8")
    destination = tmp_path / "joined.txt"

    merge_files([second, first], destination, add_source_titles=False)

    result = destination.read_text(encoding="utf-8")
    assert result.index("Второй файл") < result.index("Первый файл")
    assert "* * *" in result


def test_merge_to_fb2_creates_book_sections(tmp_path: Path) -> None:
    first = tmp_path / "one.txt"
    second = tmp_path / "two.txt"
    first.write_text("Глава 1\n\nОдин.", encoding="utf-8")
    second.write_text("Глава 2\n\nДва.", encoding="utf-8")
    destination = tmp_path / "joined.fb2"

    merge_files(
        [first, second],
        destination,
        metadata=BookMetadata(title="Сборник", author="Автор Тестов"),
    )

    assert fb2_metadata(destination).title == "Сборник"
    result = fb2_text(destination)
    assert result.index("one") < result.index("two")
    assert result.index("Один.") < result.index("Два.")


def test_batch_conversion_avoids_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("Текст", encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    (output / "input.fb2").write_text("занято", encoding="utf-8")

    results = convert_many([source], output, overwrite=False)

    assert results[0].destination.name == "input (1).fb2"
    assert results[0].destination.exists()


def test_rejects_doctype(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.fb2"
    source.write_text(
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY boom "x">]><FictionBook/>',
        encoding="utf-8",
    )
    with pytest.raises(ConversionError, match="Небезопасная"):
        fb2_text(source)
