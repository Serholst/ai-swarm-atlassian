#!/usr/bin/env python3
"""
Update SDLC Rules page in Confluence from markdown file.

Usage:
    python scripts/update_confluence_sdlc.py [--dry-run]

    --dry-run   Show converted HTML without uploading
"""

import os
import re
import sys
import json
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

import requests

# ─── Configuration ───────────────────────────────────────────────────────────
PAGE_ID = "2916354"
SPACE_KEY = "web3"
MARKDOWN_FILE = PROJECT_ROOT / "outputs" / "sdlc_rules_updated.md"

CONFLUENCE_BASE = os.getenv("CONFLUENCE_URL") or os.getenv("ATLASSIAN_URL", "")
EMAIL = os.getenv("ATLASSIAN_BOT_EMAIL") or os.getenv("ATLASSIAN_ADMIN_EMAIL", "")
TOKEN = os.getenv("ATLASSIAN_BOT_API_TOKEN") or os.getenv("ATLASSIAN_ADMIN_API_TOKEN", "")


# ─── Markdown → Confluence Storage Format (XHTML) ───────────────────────────


def md_to_confluence(md: str) -> str:
    """Convert markdown to Confluence storage format (XHTML subset)."""
    lines = md.split("\n")
    html_parts: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # ── Blank line ──
        if not line.strip():
            i += 1
            continue

        # ── Horizontal rule ──
        if re.match(r"^-{3,}$", line.strip()):
            html_parts.append("<hr />")
            i += 1
            continue

        # ── Headings ──
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            level = len(heading_match.group(1))
            text = _inline_format(heading_match.group(2).strip())
            html_parts.append(f"<h{level}>{text}</h{level}>")
            i += 1
            continue

        # ── Table ──
        if "|" in line and not line.startswith(">"):
            table_lines = []
            while i < len(lines) and "|" in lines[i]:
                table_lines.append(lines[i])
                i += 1
            html_parts.append(_convert_table(table_lines))
            continue

        # ── Unordered list / checkbox list ──
        if re.match(r"^\s*[-*]\s", line):
            list_lines = []
            while i < len(lines) and (
                re.match(r"^\s*[-*]\s", lines[i]) or (lines[i].startswith("  ") and list_lines)
            ):
                list_lines.append(lines[i])
                i += 1
            html_parts.append(_convert_list(list_lines))
            continue

        # ── Ordered list ──
        if re.match(r"^\s*\d+\.\s", line):
            list_lines = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s", lines[i]):
                list_lines.append(lines[i])
                i += 1
            html_parts.append(_convert_ordered_list(list_lines))
            continue

        # ── Blockquote ──
        if line.startswith(">"):
            quote_lines = []
            while i < len(lines) and lines[i].startswith(">"):
                quote_lines.append(re.sub(r"^>\s?", "", lines[i]))
                i += 1
            inner = md_to_confluence("\n".join(quote_lines))
            html_parts.append(f"<blockquote>{inner}</blockquote>")
            continue

        # ── Paragraph ──
        para_lines = []
        while i < len(lines) and lines[i].strip() and not _is_block_start(lines[i]):
            para_lines.append(lines[i])
            i += 1
        text = _inline_format(" ".join(para_lines))
        html_parts.append(f"<p>{text}</p>")

    return "\n".join(html_parts)


def _is_block_start(line: str) -> bool:
    """Check if line starts a block element."""
    if re.match(r"^#{1,6}\s", line):
        return True
    if re.match(r"^-{3,}$", line.strip()):
        return True
    if re.match(r"^\s*[-*]\s", line):
        return True
    if re.match(r"^\s*\d+\.\s", line):
        return True
    if line.startswith(">"):
        return True
    return False


def _inline_format(text: str) -> str:
    """Apply inline formatting: bold, italic, code, links."""
    # Code inline: `text`
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Bold+italic: ***text*** or ___text___
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text)
    # Bold: **text**
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic: *text*
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Strikethrough: ~~text~~
    text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", text)
    # Links: [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def _convert_table(lines: list[str]) -> str:
    """Convert markdown table to Confluence HTML table."""
    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)

    if len(rows) < 2:
        return ""

    # Skip separator row (row with dashes: |---|---|)
    header = rows[0]
    data_rows = [r for r in rows[1:] if not all(re.match(r"^[-:]+$", c) for c in r)]

    html = '<table class="wrapped"><colgroup>'
    for _ in header:
        html += "<col />"
    html += "</colgroup><thead><tr>"

    for cell in header:
        html += f"<th>{_inline_format(cell)}</th>"
    html += "</tr></thead><tbody>"

    for row in data_rows:
        html += "<tr>"
        for cell in row:
            html += f"<td>{_inline_format(cell)}</td>"
        html += "</tr>"

    html += "</tbody></table>"
    return html


def _convert_list(lines: list[str]) -> str:
    """Convert unordered/checkbox list to HTML."""
    items = []
    for line in lines:
        # Remove bullet marker
        text = re.sub(r"^\s*[-*]\s+", "", line)
        # Handle checkboxes
        if text.startswith("[ ] "):
            text = "☐ " + text[4:]
        elif text.startswith("[x] ") or text.startswith("[X] "):
            text = "☑ " + text[4:]
        items.append(f"<li>{_inline_format(text)}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def _convert_ordered_list(lines: list[str]) -> str:
    """Convert ordered list to HTML."""
    items = []
    for line in lines:
        text = re.sub(r"^\s*\d+\.\s+", "", line)
        items.append(f"<li>{_inline_format(text)}</li>")
    return "<ol>" + "".join(items) + "</ol>"


# ─── Confluence API ──────────────────────────────────────────────────────────


def get_page(page_id: str) -> dict:
    """Fetch current page data (for version number)."""
    url = f"{CONFLUENCE_BASE}/rest/api/content/{page_id}?expand=version,body.storage"
    resp = requests.get(url, auth=(EMAIL, TOKEN))
    resp.raise_for_status()
    return resp.json()


def update_page(page_id: str, title: str, body_html: str, version: int) -> dict:
    """Update page content."""
    url = f"{CONFLUENCE_BASE}/rest/api/content/{page_id}"
    payload = {
        "id": page_id,
        "type": "page",
        "title": title,
        "space": {"key": SPACE_KEY},
        "version": {"number": version},
        "body": {"storage": {"value": body_html, "representation": "storage"}},
    }
    resp = requests.put(url, json=payload, auth=(EMAIL, TOKEN))
    resp.raise_for_status()
    return resp.json()


# ─── Main ────────────────────────────────────────────────────────────────────


def main():
    dry_run = "--dry-run" in sys.argv

    # Read markdown
    if not MARKDOWN_FILE.exists():
        print(f"ERROR: File not found: {MARKDOWN_FILE}")
        sys.exit(1)

    md_content = MARKDOWN_FILE.read_text(encoding="utf-8")
    print(f"Read {len(md_content)} chars from {MARKDOWN_FILE.name}")

    # Convert to Confluence storage format
    html_content = md_to_confluence(md_content)
    print(f"Converted to {len(html_content)} chars of Confluence HTML")

    if dry_run:
        output_path = PROJECT_ROOT / "outputs" / "sdlc_rules_confluence.html"
        output_path.write_text(html_content, encoding="utf-8")
        print(f"\n--dry-run: HTML saved to {output_path}")
        print("Open it in browser to preview, then run without --dry-run to upload.")
        return

    # Validate credentials
    if not all([CONFLUENCE_BASE, EMAIL, TOKEN]):
        print("ERROR: Missing Confluence credentials in .env")
        print(f"  CONFLUENCE_URL: {'set' if CONFLUENCE_BASE else 'MISSING'}")
        print(f"  EMAIL: {'set' if EMAIL else 'MISSING'}")
        print(f"  TOKEN: {'set' if TOKEN else 'MISSING'}")
        sys.exit(1)

    # Get current page version
    print(f"\nFetching page {PAGE_ID} from {CONFLUENCE_BASE}...")
    page = get_page(PAGE_ID)
    current_version = page["version"]["number"]
    title = page["title"]
    print(f"  Title: {title}")
    print(f"  Current version: {current_version}")

    # Update
    print(f"\nUpdating to version {current_version + 1}...")
    result = update_page(PAGE_ID, title, html_content, current_version + 1)
    new_version = result["version"]["number"]
    page_url = f"{CONFLUENCE_BASE}/spaces/{SPACE_KEY}/pages/{PAGE_ID}"
    print(f"  Updated to version {new_version}")
    print(f"  URL: {page_url}")
    print("\nDone!")


if __name__ == "__main__":
    main()
