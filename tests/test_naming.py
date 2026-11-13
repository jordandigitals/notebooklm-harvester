"""Naming per D-013."""

from nlh.naming import safe_stem, unique_path


def test_illegal_characters_removed():
    assert safe_stem('A/B\\C:D*E?F"G<H>I|J') == "ABCDEFGHIJ"


def test_trailing_dots_and_spaces_trimmed():
    assert safe_stem("Title. . ") == "Title"


def test_reserved_windows_names():
    assert safe_stem("CON") == "CON (file)"
    assert safe_stem("lpt1") == "lpt1 (file)"


def test_empty_title_falls_back():
    assert safe_stem("???") == "Audio Overview"


def test_long_title_capped():
    assert len(safe_stem("x" * 400)) == 150


def test_clash_gets_suffix_case_insensitive(tmp_path):
    (tmp_path / "how ai works.mp3").write_bytes(b"x")
    assert unique_path(tmp_path, "How AI Works").name == "How AI Works (2).mp3"


def test_pending_part_counts_as_taken(tmp_path):
    (tmp_path / "Title.mp3.part").write_bytes(b"x")
    (tmp_path / "Title (2).mp3").write_bytes(b"x")
    assert unique_path(tmp_path, "Title").name == "Title (3).mp3"
