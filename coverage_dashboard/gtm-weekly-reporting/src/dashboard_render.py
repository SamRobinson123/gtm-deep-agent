"""Inject dashboard JSON into the React handoff template (preview_dashboard_v3.html per CURSOR_PROMPT_v3_shadcn.md)."""

from __future__ import annotations

import base64
import json
import re
import shutil
from pathlib import Path

_DASH_JSON_PATTERN = re.compile(
    r'(<script[^>]+id="dashboard-data"[^>]*>)(.*?)(</script>)',
    re.DOTALL,
)

_MIN_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def render_dashboard_html(
    data: dict,
    template_path: Path,
    out_path: Path,
) -> None:
    """
    Reads the React template, replaces the contents of
    <script type="application/json" id="dashboard-data">…</script>
    with json.dumps(data, default=str, allow_nan=False), and writes out_path.

    Copies handoff assets next to out_path (same names as in ``handoff/``; see CURSOR_PROMPT_v3_shadcn.md):
      ``tweaks-panel.jsx``, ``tricentis-favicon.ico``, ``tricentis-mark.png``.

    If the template references ``favicon.ico`` (e.g. <link rel="icon" href="favicon.ico"/>),
    tricentis-favicon.ico is also copied to favicon.ico so the link resolves.
    """
    handoff_dir = template_path.parent
    root = handoff_dir.parent
    template_html = template_path.read_text(encoding="utf-8")
    payload = json.dumps(data, default=str, allow_nan=False)
    new_html, n_sub = _DASH_JSON_PATTERN.subn(
        lambda m: m.group(1) + payload + m.group(3),
        template_html,
        count=1,
    )
    if n_sub != 1:
        raise ValueError(
            f"Expected exactly one <script id=\"dashboard-data\"> block in {template_path}"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(new_html, encoding="utf-8")

    dest_dir = out_path.parent

    jsx_src = handoff_dir / "tweaks-panel.jsx"
    if jsx_src.is_file():
        shutil.copy2(jsx_src, dest_dir / "tweaks-panel.jsx")

    fav_src = handoff_dir / "tricentis-favicon.ico"
    if fav_src.is_file():
        shutil.copy2(fav_src, dest_dir / "tricentis-favicon.ico")
        if "favicon.ico" in template_html:
            shutil.copy2(fav_src, dest_dir / "favicon.ico")
    else:
        fb = root / "favicon.ico"
        if fb.is_file() and "favicon.ico" in template_html:
            shutil.copy2(fb, dest_dir / "favicon.ico")

    png_src = handoff_dir / "tricentis-mark.png"
    if png_src.is_file():
        shutil.copy2(png_src, dest_dir / "tricentis-mark.png")
    else:
        (dest_dir / "tricentis-mark.png").write_bytes(_MIN_PNG)
