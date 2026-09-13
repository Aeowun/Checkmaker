# CheckMaker: A surgical Markdown checklist editor.
# This tool updates markdown checkboxes directly on disk via binary seeks.
# By modifying only the target byte, we preserve file encoding, line endings,
# and prevent unrelated formatting changes.

import os
import re
import json
import webview
import logging
import tempfile
import uuid
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Union

# --- Data Models ---

@dataclass
class FileRevision:
    """Fingerprint of a file to detect external changes."""
    size: int
    mtime_ns: int

    @classmethod
    def from_path(cls, path: Path):
        stat = path.stat()
        return cls(size=stat.st_size, mtime_ns=stat.st_mtime_ns)

@dataclass
class TabState:
    """Working data for an open Markdown file."""
    id: str
    path: str
    title: str
    revision: FileRevision
    content: str
    byte_positions: List[int]
    stale: bool = False

# --- Setup & Helpers ---

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("CheckMaker")

def resource_path(relative_path: str) -> Path:
    """Get absolute path to resource, handles dev mode and PyInstaller exe."""
    try:
        base_path = Path(sys._MEIPASS)
    except Exception:
        base_path = Path(__file__).parent.absolute()
    return base_path / relative_path

# --- File Operations ---

class ChecklistProcessor:
    """Heavy lifting for reading and writing to disk."""

    @staticmethod
    def scan_for_checkboxes(raw_bytes: bytes) -> List[int]:
        """Find the exact byte offset of the character inside every [ ]."""
        # This implementation uses a stateful scanner to accurately match the behavior
        # of the bundled Marked.js renderer, handling fences, blockquotes, and lists.
        positions = []
        lines = raw_bytes.splitlines(keepends=True)
        offset = 0
        
        in_fence = False
        f_char, f_len = None, 0
        in_indented_code = False
        in_list = False
        
        for line in lines:
            line_len = len(line)
            stripped = line.strip()
            
            # 1. Fence Detection (priority)
            # Handles optional blockquote prefix and up to 3 spaces of indentation.
            fence_m = re.search(rb'^(?P<pre>(?:[ \t]*>)*[ \t]{0,3})(?P<fence>[`~]{3,})', line)
            
            if not in_fence:
                if fence_m:
                    in_fence = True
                    f_char = fence_m.group('fence')[:1]
                    f_len = len(fence_m.group('fence'))
                    in_indented_code = False
                elif not stripped:
                    # Blank lines continue indented blocks but don't reset list state
                    pass
                elif line.startswith(b'    ') or line.startswith(b'\t'):
                    # Potential indented code block
                    if not in_list:
                        in_indented_code = True
                else:
                    in_indented_code = False
                    # Check for list start to handle nested task indentation correctly
                    if re.search(rb'^(?:[ \t]*>)*[ \t]{0,3}(?:[*+-]|\d+[.)])\s+', line):
                        in_list = True
                    elif stripped and not stripped.startswith(b'>'):
                        # Non-empty, non-blockquote, non-list line breaks the list context
                        if not line.startswith(b' '):
                            in_list = False

                # 2. Task Detection
                if not in_fence and not in_indented_code:
                    # GFM: List item starts with bullet, then space, then checkbox.
                    match = re.search(rb'^(?P<pre>(?:[ \t]*>)*[ \t]*)(?P<bul>[*+-]|\d+[.)])\s+(?P<cb>\[[ xX]\])', line)
                    if match:
                        # Mask inline code on the content part of the line to avoid false positives.
                        content_after_bullet = line[match.start('cb'):]
                        # Simplified masking for inline backticks
                        masked = re.sub(rb'(`+)(?:(?!\1).)+?\1', lambda m: b' ' * len(m.group(0)), content_after_bullet)
                        if masked.startswith(b'['):
                            positions.append(offset + match.start('cb') + 1)
                            in_list = True
            else:
                # Inside fence, look for closing fence (same char, length >= opening)
                cp = rb'^(?:[ \t]*>)*[ \t]{0,3}' + re.escape(f_char) * f_len + rb'+'
                if re.search(cp, line):
                    in_fence = False
            
            offset += line_len
            
        return positions

    @staticmethod
    def verify_safety(path: Path, expected_rev: FileRevision) -> bool:
        """Ensure file on disk matches our loaded revision."""
        if not path.exists():
            return False
        current = FileRevision.from_path(path)
        return (current.mtime_ns == expected_rev.mtime_ns and 
                current.size == expected_rev.size)

    @staticmethod
    def surgical_write(path: Path, pos: int) -> str:
        """Teleport to a byte offset and flip the checkbox state."""
        with open(path, "rb+") as f:
            # Verify we are actually looking at a checkbox structure
            f.seek(pos - 1)
            block = f.read(3)
            if not (block.startswith(b"[") and block.endswith(b"]")):
                 raise ValueError("Checkbox structure mismatch on disk.")
            
            # Determine new state
            current_char = block[1:2]
            if current_char not in [b" ", b"x", b"X"]:
                 raise ValueError("Not a valid checkbox character.")

            new_char = b"x" if current_char == b" " else b" "
            
            # Perform the overwrite
            f.seek(pos)
            f.write(new_char)
            f.flush()
            os.fsync(f.fileno()) # Force write to physical media
            
            return new_char.decode()

# --- Web Bridge (API) ---

class CheckMakerApi:
    """Exposes Python logic to the JavaScript frontend."""
    
    def __init__(self):
        self.tabs: Dict[str, TabState] = {}
        self.active_tab_id: Optional[str] = None
        self._window = None
        self.history_path = resource_path("history.json")

    def set_window(self, window):
        """Link API to the webview window."""
        self._window = window

    # --- History Management ---

    def _load_history(self) -> List[str]:
        if self.history_path.exists():
            try:
                with open(self.history_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return [p for p in data if os.path.exists(p)][:10]
            except Exception:
                return []
        return []

    def _save_history(self, path: str):
        history = self._load_history()
        abs_path = str(Path(path).absolute())
        if abs_path in history: history.remove(abs_path)
        history.insert(0, abs_path)
        
        try:
            with tempfile.NamedTemporaryFile('w', dir=self.history_path.parent, delete=False, encoding="utf-8") as tf:
                json.dump(history[:10], tf)
                tempname = tf.name
            os.replace(tempname, self.history_path)
        except Exception as e:
            logger.error(f"History save failed: {e}")

    def get_recent_files(self) -> List[str]:
        return self._load_history()

    # --- Tab & File Management ---

    def select_file(self):
        """Trigger native OS dialog."""
        file_types = ('Markdown files (*.md)', 'Text files (*.txt)', 'All files (*.*)')
        result = self._window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=file_types)
        if result: return self.load_path(result[0])
        return None

    def load_path(self, path: str):
        """Entry point for opening files."""
        path = path.strip().replace('file:///', '').replace('%20', ' ')
        if not path: return {"error": "No path provided."}
        
        p = Path(path).absolute()
        if not p.exists() or not p.is_file():
            return {"error": "Invalid file path."}
            
        # Check if tab is already open
        for tid, tab in self.tabs.items():
            if Path(tab.path).absolute() == p:
                self.active_tab_id = tid
                return self.get_tab_info(tid)

        try:
            # Read file and index checkboxes
            revision = FileRevision.from_path(p)
            with open(p, "rb") as f: raw_bytes = f.read()
            positions = ChecklistProcessor.scan_for_checkboxes(raw_bytes)
            
            # Store tab state
            tid = str(uuid.uuid4())
            tab = TabState(
                id=tid, path=str(p), title=p.name, revision=revision,
                content=raw_bytes.decode("utf-8", errors="replace"),
                byte_positions=positions
            )
            self.tabs[tid] = tab
            self.active_tab_id = tid
            self._save_history(str(p))
            return self.get_tab_info(tid)
        except Exception as e:
            logger.exception("File load failed")
            return {"error": str(e)}

    def get_tab_info(self, tab_id: str):
        tab = self.tabs.get(tab_id)
        if not tab: return {"error": "Tab not found"}
        return {"id": tab.id, "path": tab.path, "title": tab.title, "content": tab.content, "stale": tab.stale}

    def close_tab(self, tab_id: str):
        if tab_id in self.tabs:
            del self.tabs[tab_id]
            if self.active_tab_id == tab_id:
                self.active_tab_id = list(self.tabs.keys())[-1] if self.tabs else None
        return self.get_session_state()

    def switch_tab(self, tab_id: str):
        if tab_id in self.tabs: self.active_tab_id = tab_id
        return self.get_tab_info(tab_id)

    def get_session_state(self):
        return {
            "tabs": [{"id": t.id, "title": t.title, "stale": t.stale, "active": t.id == self.active_tab_id} for t in self.tabs.values()],
            "active_tab_id": self.active_tab_id
        }

    def refresh_tab(self, tab_id: str):
        """Full reload of a tab from disk."""
        tab = self.tabs.get(tab_id)
        if not tab: return {"error": "Tab not found"}
        
        p = Path(tab.path)
        try:
            revision = FileRevision.from_path(p)
            with open(p, "rb") as f: raw_bytes = f.read()
            tab.revision = revision
            tab.content = raw_bytes.decode("utf-8", errors="replace")
            tab.byte_positions = ChecklistProcessor.scan_for_checkboxes(raw_bytes)
            tab.stale = False
            return self.get_tab_info(tab_id)
        except Exception as e:
            return {"error": str(e)}

    def poll_status(self):
        """Health check for open tabs."""
        updates = {}
        for tid, tab in self.tabs.items():
            p = Path(tab.path)
            try:
                if not p.exists():
                    tab.stale = True
                    updates[tid] = "deleted"
                elif not ChecklistProcessor.verify_safety(p, tab.revision):
                    tab.stale = True
                    updates[tid] = "changed"
            except Exception:
                tab.stale = True
                updates[tid] = "error"
        return updates

    def toggle_checkbox(self, tab_id: str, index: int):
        """Validate integrity before performing surgical write."""
        tab = self.tabs.get(tab_id)
        if not tab or index >= len(tab.byte_positions):
            return {"error": "Invalid interaction state."}
            
        path = Path(tab.path)
        pos = tab.byte_positions[index]
        
        try:
            # SAFETY GATE: Verify file drift
            if not ChecklistProcessor.verify_safety(path, tab.revision):
                tab.stale = True
                return {"error": "File drift detected. Reloading required for safety."}

            # PERFORM: The surgical strike
            new_char = ChecklistProcessor.surgical_write(path, pos)

            # UPDATE: Refresh the revision to include our change
            tab.revision = FileRevision.from_path(path)
            return {"success": True, "char": new_char}

        except Exception as e:
            logger.exception("Checkbox toggle failed")
            return {"error": str(e)}

def load_ui():
    """Load the HTML UI from assets and inject dynamic sources."""
    html_path = resource_path("assets/index.html")
    html_src = html_path.read_text(encoding="utf-8")
    
    # Load styling and rendering assets (fallback to CDN if missing)
    marked_js = resource_path("assets/marked.min.js")
    marked_src = marked_js.read_text(encoding="utf-8") if marked_js.exists() else "https://cdn.jsdelivr.net/npm/marked/marked.min.js"

    css_file = resource_path("assets/github-markdown-dark.min.css")
    css_src = css_file.read_text(encoding="utf-8") if css_file.exists() else "https://cdnjs.cloudflare.com/ajax/libs/github-markdown-css/5.2.0/github-markdown-dark.min.css"
    
    return html_src.replace("[[CSS_SRC]]", css_src).replace("[[MARKED_SRC]]", marked_src)

# --- Main Entry ---

if __name__ == "__main__":
    api = CheckMakerApi()
    window = webview.create_window(
        'CheckMaker', 
        html=load_ui(), 
        js_api=api,
        width=900,
        height=900
    )
    api.set_window(window)
    webview.start()
