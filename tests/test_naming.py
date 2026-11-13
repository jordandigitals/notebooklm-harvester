"""Naming per D-013."""

import os

import pytest

from nlh import naming, paths
from nlh.naming import notebook_folder, safe_stem, unique_path


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


def _deep(tmp_path, monkeypatch, total):
    """An output folder that behaves as if its absolute path were total characters long.

    The real temp folder can itself be long (CI runners), so instead of building a
    folder of exactly that length, the cap is shifted by the real length.
    """
    out = tmp_path.resolve() / "out"
    out.mkdir()
    monkeypatch.setattr(naming, "WIN_MAX_PATH", 240 - total + len(str(out)))
    return out


def test_windows_cap_keeps_full_path_under_240(tmp_path, monkeypatch):
    """D-023: deep folder + long notebook + long overview title stays under 240 on Windows."""
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    out = _deep(tmp_path, monkeypatch, 100)
    folder = notebook_folder(out, "N" * 126)
    assert len(folder.name) == naming.MAX_FOLDER
    path = unique_path(folder, "Overview " + "O" * 176)
    assert len(os.path.abspath(path)) + naming.SUFFIX_ROOM < naming.WIN_MAX_PATH
    assert len(path.stem) >= naming.WIN_MIN_STEM and path.suffix == ".mp3"
    assert path.stem.startswith("Overview O")


def test_windows_cap_trims_trailing_dots_and_spaces(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    out = _deep(tmp_path, monkeypatch, 100)
    folder = notebook_folder(out, "N" * 80)
    room = naming.stem_room(folder)
    title = "A" * (room - 2) + " . " + "B" * 50  # the cut lands on " ."
    stem = unique_path(folder, title).stem
    assert not stem.endswith((" ", ".")) and stem


def test_windows_too_deep_folder_refused_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    out = _deep(tmp_path, monkeypatch, 200)
    with pytest.raises(naming.PathTooLong) as err:
        notebook_folder(out, "N" * 80)
    assert err.value.message == naming.TOO_LONG_MESSAGE
    assert list(out.iterdir()) == []  # the notebook subfolder was not created


def test_windows_cap_clash_suffix_still_works(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    out = _deep(tmp_path, monkeypatch, 100)
    folder = notebook_folder(out, "N" * 80)
    first = unique_path(folder, "T" * 200)
    first.write_bytes(b"x")
    second = unique_path(folder, "t" * 200)  # case-only clash
    assert second.stem == first.stem.lower() + " (2)" or second.stem == first.stem + " (2)"
    assert second.name.lower() != first.name.lower()
    assert len(os.path.abspath(second)) < naming.WIN_MAX_PATH


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_mac_and_linux_names_unchanged(tmp_path, monkeypatch, platform):
    """No cap off Windows: the overview stem keeps its full 150 characters."""
    monkeypatch.setattr(paths, "is_windows", lambda: platform == "win32")
    out = _deep(tmp_path, monkeypatch, 200)
    folder = notebook_folder(out, "N" * 126)
    assert unique_path(folder, "O" * 185).stem == "O" * naming.MAX_STEM


def test_windows_cap_counts_utf16_units(tmp_path, monkeypatch):
    """Emoji count twice toward Windows' limit; the cut never splits one."""
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    out = _deep(tmp_path, monkeypatch, 100)
    folder = notebook_folder(out, "N" * 80)
    path = unique_path(folder, "\U0001f3a7" * 120)  # headphones emoji, 2 UTF-16 units each
    full = os.path.abspath(path)
    assert naming._u16(full) + naming.SUFFIX_ROOM < naming.WIN_MAX_PATH
    assert set(path.stem) == {"\U0001f3a7"}  # whole emoji only
