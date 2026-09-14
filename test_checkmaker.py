from pathlib import Path

import os
from pathlib import Path

import pytest

from checkmaker import CheckMakerApi, ChecklistProcessor, FileRevision, TabState


def make_tab(api: CheckMakerApi, path: Path, raw: bytes) -> TabState:
    path.write_bytes(raw)

    tab = TabState(
        id="tab-1",
        path=str(path),
        title=path.name,
        revision=FileRevision.from_path(path),
        content=raw.decode("utf-8"),
        byte_positions=ChecklistProcessor.scan_for_checkboxes(raw),
    )

    api.tabs[tab.id] = tab
    api.active_tab_id = tab.id
    return tab


def test_scan_finds_supported_task_checkboxes_and_ignores_code():
    raw = (
        b"- [ ] open\n"
        b"* [x] done\n"
        b"1. [X] ordered\n"
        b"> - [ ] quoted\n"
        b"```markdown\n"
        b"- [ ] inside fence\n"
        b"```\n"
        b"    - [ ] indented code\n"
        b"- ` [ ] inline code`\n"
        b"- [ ] second\n"
    )

    positions = ChecklistProcessor.scan_for_checkboxes(raw)

    assert [raw[pos - 1 : pos + 2] for pos in positions] == [
        b"[ ]",
        b"[x]",
        b"[X]",
        b"[ ]",
        b"[ ]",
    ]


def test_surgical_write_changes_exactly_one_byte(tmp_path: Path):
    path = tmp_path / "todo.md"
    before = b"- [ ] first\r\n- [x] second\r\n"
    path.write_bytes(before)

    positions = ChecklistProcessor.scan_for_checkboxes(before)
    changed = ChecklistProcessor.surgical_write(path, positions[0])
    after = path.read_bytes()

    assert changed == "x"
    assert len(after) == len(before)
    assert sum(a != b for a, b in zip(before, after)) == 1
    assert after == b"- [x] first\r\n- [x] second\r\n"


def test_surgical_write_rejects_target_mismatch(tmp_path: Path):
    path = tmp_path / "todo.md"
    path.write_bytes(b"- [ ] first\n")

    with pytest.raises(ValueError, match="Checkbox structure mismatch"):
        ChecklistProcessor.surgical_write(path, 4)


def test_file_revision_changes_when_file_changes(tmp_path: Path):
    path = tmp_path / "todo.md"
    path.write_bytes(b"- [ ] first\n")

    original = FileRevision.from_path(path)

    stat = path.stat()
    path.write_bytes(b"- [x] first\n")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    changed = FileRevision.from_path(path)

    assert changed.size == original.size
    assert changed.mtime_ns != original.mtime_ns


def test_toggle_checkbox_fails_closed_on_file_drift(tmp_path: Path):
    path = tmp_path / "todo.md"
    raw = b"- [ ] first\n"

    api = CheckMakerApi()
    tab = make_tab(api, path, raw)

    stat = path.stat()
    path.write_bytes(b"- [x] first\n")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    result = api.toggle_checkbox(tab.id, 0)

    assert result == {
        "error": "File drift detected. Reloading required for safety."
    }
    assert tab.stale is True
    assert path.read_bytes() == b"- [x] first\n"


def test_toggle_checkbox_updates_revision_after_success(tmp_path: Path):
    path = tmp_path / "todo.md"
    raw = b"- [ ] first\n"

    api = CheckMakerApi()
    tab = make_tab(api, path, raw)

    result = api.toggle_checkbox(tab.id, 0)

    assert result == {"success": True, "char": "x"}
    assert path.read_bytes() == b"- [x] first\n"
    assert tab.revision == FileRevision.from_path(path)
    assert tab.stale is False


def test_toggle_checkbox_rejects_invalid_index(tmp_path: Path):
    path = tmp_path / "todo.md"
    raw = b"- [ ] first\n"

    api = CheckMakerApi()
    make_tab(api, path, raw)

    assert api.toggle_checkbox("tab-1", 1) == {
        "error": "Invalid interaction state."
    }


def test_toggle_checkbox_rejects_missing_tab():
    api = CheckMakerApi()

    assert api.toggle_checkbox("missing", 0) == {
        "error": "Invalid interaction state."
    }

