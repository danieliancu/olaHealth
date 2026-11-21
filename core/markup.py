"""A tiny, safe text format for Learn articles written in the admin.

Everything is HTML-escaped first, so no markup typed by an editor ever reaches the page as HTML.
Supported: blank line = paragraph · "## " / "### " headings · "- " bullets · "1. " numbered
items · **bold** · *italic* · [text](/path) or [text](https://…) links.
"""

from __future__ import annotations

import re

from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\(((?:/|https://)[^)\s]*)\)")


def _inline(text: str) -> str:
    out = escape(text)
    out = _LINK.sub(
        lambda m: (
            f'<a href="{m.group(2)}" rel="noopener">{m.group(1)}</a>'
            if m.group(2).startswith("https://")
            else f'<a href="{m.group(2)}">{m.group(1)}</a>'
        ),
        out,
    )
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    return _ITALIC.sub(r"<em>\1</em>", out)


def render_halves(text: str) -> tuple[SafeString, SafeString]:
    """The article in two parts, split at a block boundary near the middle (for a pull quote
    placed between them on phones). A heading never ends the first part."""
    newline = "\n"
    blocks = str(render(text)).split(newline)
    if len(blocks) < 2:
        return mark_safe(newline.join(blocks)), mark_safe("")  # noqa: S308 - already escaped
    cut = max(1, len(blocks) // 2)
    while cut > 1 and blocks[cut - 1].startswith(("<h2>", "<h3>")):
        cut -= 1
    first = newline.join(blocks[:cut])
    rest = newline.join(blocks[cut:])
    return mark_safe(first), mark_safe(rest)  # noqa: S308 - already escaped


def render(text: str) -> SafeString:
    html: list[str] = []
    for block in re.split(r"\n\s*\n", (text or "").strip().replace("\r\n", "\n")):
        lines = [ln.rstrip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        first = lines[0]
        if first.startswith("### "):
            html.append(f"<h3>{_inline(first[4:])}</h3>")
            lines = lines[1:]
        elif first.startswith("## "):
            html.append(f"<h2>{_inline(first[3:])}</h2>")
            lines = lines[1:]
        if not lines:
            continue
        if all(ln.lstrip().startswith("- ") for ln in lines):
            items = "".join(f"<li>{_inline(ln.lstrip()[2:])}</li>" for ln in lines)
            html.append(f"<ul>{items}</ul>")
        elif all(re.match(r"\s*\d+\.\s", ln) for ln in lines):
            items = "".join(f"<li>{_inline(re.sub(r'^\s*\d+\.\s', '', ln))}</li>" for ln in lines)
            html.append(f"<ol>{items}</ol>")
        else:
            html.append(f"<p>{_inline(' '.join(lines))}</p>")
    return mark_safe("\n".join(html))  # noqa: S308 - every piece of text was escaped above
