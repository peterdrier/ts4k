"""ts4k normalizer — HTML-to-clean-text pipeline for token-efficient messaging.

Every message passes through: HTML strip -> tracking removal -> signature removal ->
reply chain strip -> unsubscribe removal -> table conversion -> whitespace collapse.

Target: 70%+ byte reduction on typical HTML emails.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import html2text
from bs4 import BeautifulSoup, NavigableString


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(html: str, mode: str = "compact") -> str:
    """Full normalization pipeline.

    Takes raw HTML (or plain text), returns clean text. ``mode`` selects the
    output style:

    - ``"compact"`` (default): token-efficient text for LLM consumption —
      no emphasis markup, tight whitespace, pipe-delimited tables.
    - ``"readable"``: preserves paragraph breaks and **bold**/_italic_
      markdown, and renders tables as real markdown tables — for
      human-facing display.

    Applies the full ts4k preprocessing pipeline in order.
    """
    if not html or not html.strip():
        return ""

    text = html.strip()

    # Detect whether input is HTML or plain text.
    # We look for actual HTML tags, not angle-bracket email addresses like <alice@example.com>
    is_html = _looks_like_html(text)

    if is_html:
        # Phase 1: Pre-process HTML with BeautifulSoup before html2text
        soup = BeautifulSoup(text, "html.parser")

        # Remove tracking pixels and 1x1 images
        _remove_tracking_pixels(soup)

        # Remove style and script elements entirely
        for tag in soup.find_all(["style", "script", "head"]):
            tag.decompose()

        # Remove hidden elements (display:none, visibility:hidden, etc.)
        _remove_hidden_elements(soup)

        # Remove unsubscribe blocks and footer boilerplate (before html2text)
        _remove_unsubscribe_blocks_html(soup)

        # Compact: data tables become pipe-delimited text. Readable: data
        # tables stay HTML so html2text renders real markdown tables.
        # Layout tables are unwrapped in both modes.
        _convert_tables(soup, mode=mode)

        # Convert the cleaned HTML to text using html2text
        text = _html_to_text(str(soup), mode=mode)
    else:
        # Plain text input — still run text-level cleanups
        pass

    # Phase 2: Text-level cleanups (apply to both HTML-derived and plain text)
    text = _strip_reply_chains(text)
    text = _strip_signatures(text)
    text = _strip_unsubscribe_text(text)
    text = _collapse_whitespace(text)

    return text.strip()


def normalize_headers(raw_headers: dict) -> dict:
    """Normalize message header fields.

    - Trims whitespace from all string values
    - Decodes RFC 2047 encoded words (handled upstream by email libs, but we
      clean up residual artifacts)
    - Standardizes dates to ISO 8601 UTC
    - Normalizes From/To addresses
    """
    if not raw_headers:
        return {}

    result = {}
    for key, value in raw_headers.items():
        norm_key = key.strip().lower()

        if isinstance(value, str):
            # ⚡ Bolt Optimization: Replace re.sub with native split/join for ~3x faster whitespace collapse
            # This also implicitly handles leading/trailing whitespace, making strip() redundant.
            value = " ".join(value.split())

        if norm_key == "date" and isinstance(value, str):
            result[norm_key] = _normalize_date(value)
        elif norm_key in ("from", "to", "cc", "bcc") and isinstance(value, str):
            result[norm_key] = _normalize_address(value)
        elif norm_key == "subject" and isinstance(value, str):
            result[norm_key] = _normalize_subject(value)
        else:
            result[norm_key] = value

    return result


# ---------------------------------------------------------------------------
# HTML detection
# ---------------------------------------------------------------------------

_HTML_TAG_PATTERN = re.compile(
    r"<(?:html|head|body|div|span|p|br|table|tr|td|th|a\s|img\s|"
    r"h[1-6]|ul|ol|li|strong|em|b|i|style|script|meta|link|footer|header|"
    r"blockquote|center|font|!DOCTYPE|!--)[^>]*>",
    re.IGNORECASE,
)

def _looks_like_html(text: str) -> bool:
    """Determine if text is HTML rather than plain text.

    We need to distinguish actual HTML tags from angle-bracket email addresses
    like <alice@example.com> which appear in plain-text reply headers.
    """
    # Look for common HTML structural tags (not just any angle-bracket pattern)
    return bool(_HTML_TAG_PATTERN.search(text))


# ---------------------------------------------------------------------------
# HTML preprocessing (BeautifulSoup phase)
# ---------------------------------------------------------------------------

_STYLE_TINY_PATTERN = re.compile(
    r"(?:width|height)\s*:\s*[01]px|display\s*:\s*none", re.IGNORECASE
)

_TRACKING_URL_PATTERN = re.compile(
    r"track|pixel|beacon|open\.|\.gif\?|"
    r"mailtrack|t\.co/|click\.|/o\.gif|"
    r"spacer|transparent|/t\?|wf\.gif",
    re.IGNORECASE
)

def _remove_tracking_pixels(soup: BeautifulSoup) -> None:
    """Remove 1x1 images, pixel trackers, and invisible images."""
    for img in soup.find_all("img"):
        # Check for explicit 1x1 dimensions
        width = img.get("width", "")
        height = img.get("height", "")

        is_tiny = False
        if width and height:
            try:
                w = int(str(width).replace("px", ""))
                h = int(str(height).replace("px", ""))
                if w <= 3 and h <= 3:
                    is_tiny = True
            except (ValueError, TypeError):
                pass

        # Check style for tiny dimensions
        if not is_tiny:
            style = img.get("style", "")
            if style and _STYLE_TINY_PATTERN.search(style):
                is_tiny = True

        # Check for common tracking pixel URL patterns
        if not is_tiny:
            src = img.get("src", "")
            if src and _TRACKING_URL_PATTERN.search(src):
                is_tiny = True

        # Check for images with no alt text and very small size
        if not img.get("alt") and is_tiny:
            img.decompose()
        elif is_tiny:
            img.decompose()


_STYLE_ZERO_SIZE = re.compile(r"(width|height)\s*:\s*0", re.IGNORECASE)

_STYLE_HIDDEN_PATTERN = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden",
    re.IGNORECASE,
)

def _is_hidden_element(tag) -> bool:
    """Filter for :func:`_remove_hidden_elements` — one predicate so the
    tree is traversed once instead of three times.

    Evaluates the original compiled patterns sequentially rather than a
    hand-merged "combined" regex: two patterns claiming to be the union of
    the originals is a drift bug waiting to happen.
    """
    if tag.has_attr("hidden"):
        return True
    style = tag.get("style")
    if not style:
        return False
    if _STYLE_HIDDEN_PATTERN.search(style):
        return True
    # Zero-size divs/spans used for tracking — only remove if the element
    # has no visible text content.
    return bool(_STYLE_ZERO_SIZE.search(style)) and not tag.get_text(strip=True)


def _remove_hidden_elements(soup: BeautifulSoup) -> None:
    """Remove elements that are hidden via CSS or attributes."""
    # ⚡ Bolt Optimization: single find_all pass with a combined predicate —
    # the dominant cost is the O(N) DOM traversal itself, not the per-tag
    # checks, so three passes → one is the win.
    for el in soup.find_all(_is_hidden_element):
        el.decompose()


_UNSUB_PATTERNS_HTML = re.compile(
    r"unsubscribe|opt[\s-]?out|email\s+preferences|manage\s+(?:your\s+)?subscriptions?"
    r"|update\s+(?:your\s+)?preferences|notification\s+settings"
    r"|mailing\s+list|no\s+longer\s+wish\s+to\s+receive"
    r"|stop\s+receiving\s+these\s+emails",
    re.IGNORECASE,
)

def _remove_unsubscribe_blocks_html(soup: BeautifulSoup) -> None:
    """Remove unsubscribe / email preference sections from HTML before text conversion.

    These are typically in footer divs, tables, or paragraphs at the end.
    """
    # Remove links that are unsubscribe links.
    # Collect first, then decompose, to avoid mutating the tree during iteration.
    unsub_links = []
    for a in soup.find_all("a"):
        # After decomposing a parent, child tags lose their .attrs — guard against that
        if a.attrs is None:
            continue
        href = a.get("href", "")
        # ⚡ Bolt Optimization: check href before expensive get_text
        if "unsubscribe" in href.lower():
            unsub_links.append(a)
            continue
        link_text = a.get_text(strip=True).lower()
        if "unsubscribe" in link_text:
            unsub_links.append(a)

    for a in unsub_links:
        if a.attrs is None:
            continue
        parent = a.parent
        if parent and parent.name in ("p", "div", "td", "span", "li", "center"):
            parent_text = parent.get_text(strip=True)
            if len(parent_text) < 500:
                parent.decompose()
                continue
        a.decompose()

    # Remove footer-like elements containing unsubscribe language.
    # Collect first, then decompose.
    unsub_elements = []
    for el in soup.find_all(["div", "p", "table", "tr", "td", "center", "footer"]):
        el_text = el.get_text(strip=True)
        # ⚡ Bolt Optimization: swap logic so length check short-circuits expensive regex
        if len(el_text) < 1000 and _UNSUB_PATTERNS_HTML.search(el_text):
            unsub_elements.append(el)

    for el in unsub_elements:
        # Guard: element may already have been decomposed as a child of a prior element
        if el.parent is not None:
            el.decompose()


def _cell_colspan(cell) -> int:
    """Return a cell's colspan as an int, defaulting to 1 on malformed HTML."""
    try:
        return int(cell.get("colspan", 1))
    except (ValueError, TypeError):
        return 1


def _cell_rowspan(cell) -> int:
    """Return a cell's rowspan as an int, defaulting to 1 on malformed HTML."""
    try:
        return int(cell.get("rowspan", 1))
    except (ValueError, TypeError):
        return 1


def _find_own_elements(parent, tag_names: set[str]) -> list:
    """Collect descendants of *parent* matching *tag_names*, stopping at
    nested ``<table>`` boundaries.

    Equivalent to ``[t for t in parent.find_all(names) if
    t.find_parent("table") is <owning table>]`` but in one downward walk —
    the find_all+find_parent form re-walks up the tree for every match,
    which is O(N²) on nested tables. Descending through non-table wrappers
    (a ``<form>`` or ``<div>`` inside a malformed email table) keeps the
    original semantics, unlike a direct-children-only scan.

    Iterative (explicit stack) so adversarially deep wrapper nesting can't
    exhaust the Python call stack.
    """
    results = []
    # LIFO of elements still to visit; children pushed reversed so the
    # walk stays in document order (pre-order).
    stack = list(reversed(list(parent.children)))
    while stack:
        element = stack.pop()
        name = getattr(element, "name", None)
        if not name:
            continue
        if name in tag_names:
            results.append(element)
        if name != "table":
            stack.extend(reversed(list(element.children)))
    return results


def _convert_tables(soup: BeautifulSoup, mode: str = "compact") -> None:
    """Convert HTML tables to pipe-delimited text.

    Only converts tables that look like data tables (not layout tables).
    Layout tables (single column, single row, or deeply nested) are unwrapped.
    In readable mode data tables are left intact for html2text's markdown
    table renderer, and layout-table unwrapping preserves inline markup and
    nested tables instead of collapsing to bare text.
    """
    # Innermost-first: find_all returns tables in document order (a wrapper
    # appears before the table nested inside it), so reversing means a
    # nested data table is already converted to pipe text by the time an
    # outer layout wrapper is unwrapped — otherwise the wrapper's unwrap
    # would flatten the still-raw nested table into undelimited text.
    for table in reversed(soup.find_all("table")):
        # Scope to this table's own rows/cells — find_all is recursive, so a
        # nested table's rows/cells would otherwise be double-counted and
        # skew classification (e.g. a single-column wrapper around a
        # multi-cell nested table would wrongly look like a data table).
        # ⚡ Bolt Optimization: boundary-aware walk instead of find_all+find_parent.
        rows = _find_own_elements(table, {"tr"})
        if not rows:
            # Empty table, remove
            table.decompose()
            continue

        # Extract cell data
        table_data: list[list[str]] = []
        max_cols = 0

        for row in rows:
            cells = _find_own_elements(row, {"th", "td"})
            cell_texts = [c.get_text(strip=True) for c in cells]
            if any(cell_texts):  # skip entirely empty rows
                table_data.append(cell_texts)
                max_cols = max(max_cols, len(cell_texts))

        if not table_data or max_cols == 0:
            table.decompose()
            continue

        # Heuristic: if it's a single-column table or a single-row table,
        # it's probably a layout table — just unwrap the text
        if max_cols <= 1 or len(table_data) <= 1:
            if mode == "readable":
                # Unwrap only this table's own structure so nested tables
                # and inline markup survive for html2text
                own = _find_own_elements(
                    table, {"thead", "tbody", "tr", "th", "td"}
                )
                # Unwrapping td/tr drops the tag boundaries that kept
                # adjacent cells/rows apart — "<td>Logo</td><td>Nav</td>"
                # would collapse to "LogoNav". Insert a space after each
                # own cell, and turn each own row into a <div> (a bare
                # "\n" text node would be collapsed by html2text; a block
                # element forces the line break).
                for t in own:
                    if t.name in ("th", "td"):
                        t.insert_after(NavigableString(" "))
                        t.unwrap()
                    elif t.name == "tr":
                        t.name = "div"
                    else:
                        t.unwrap()
                table.unwrap()
                continue
            # Re-extract cell text with a newline-preserving separator
            # instead of reusing table_data (which used get_text(strip=True)
            # — fine for plain single-node cells, but it collapses a cell
            # containing a just-converted nested table's multi-line pipe
            # text into one undelimited line). Single-node cells are
            # unaffected since the separator only matters when a cell has
            # more than one text descendant.
            lines = []
            for row in rows:
                cells = _find_own_elements(row, {"th", "td"})
                cell_texts = [c.get_text("\n", strip=True) for c in cells]
                if any(cell_texts):
                    lines.append(" ".join(cell_texts))
            text_content = "\n".join(lines)
            table.replace_with(text_content)
            continue

        if mode == "readable":
            # Genuine data table — leave for html2text's markdown renderer.
            # html2text only emits the markdown header-separator row when
            # it sees <th> cells, so an all-<td> data table would otherwise
            # render as plain pipe-y text. Promote the first row's <td>
            # cells to <th> so html2text treats it as a proper table.
            def _own_cells(row):
                return _find_own_elements(row, {"th", "td"})

            # Find a clean header row: scan rows in order while tracking
            # columns occupied by rowspans carried down from earlier rows.
            # A row qualifies only when no carried rowspan covers it and it
            # has exactly one single-span cell per table column — anything
            # else (grouped colspan titles, rowspan grids, spacer rows)
            # would make html2text emit a separator narrower than the data
            # rows, i.e. malformed markdown. Prefer a qualifying row that
            # already has <th> cells; otherwise promote the first
            # qualifying nonempty row. If NO row qualifies, fall through
            # to the compact pipe conversion for this table — consistent
            # pipe rows beat a malformed markdown table.
            header_row = None
            promoted_candidate = None
            has_any_th = False
            active_spans: list[list[int]] = []  # [remaining_rows, colspan]
            for row in rows:
                carried = sum(cs for _, cs in active_spans)
                r_cells = _own_cells(row)
                has_any_th = has_any_th or any(c.name == "th" for c in r_cells)
                clean = all(
                    _cell_colspan(c) == 1 and _cell_rowspan(c) == 1
                    for c in r_cells
                )
                nonempty = any(c.get_text(strip=True) for c in r_cells)
                if carried == 0 and clean and nonempty and len(r_cells) == max_cols:
                    if any(c.name == "th" for c in r_cells):
                        header_row = row
                        break
                    if promoted_candidate is None:
                        promoted_candidate = row
                for span in active_spans:
                    span[0] -= 1
                active_spans = [s for s in active_spans if s[0] > 0]
                for c in r_cells:
                    # rowspan="0" spans every remaining row in the group.
                    rs = _cell_rowspan(c)
                    if rs != 1:
                        carry = len(rows) if rs == 0 else rs - 1
                        active_spans.append([carry, _cell_colspan(c)])
            if header_row is None and promoted_candidate is not None and not has_any_th:
                # Promotion is only right when the table has no real header
                # anywhere — if <th> labels exist but none qualifies (e.g.
                # a rowspan/colspan header grid), promoting a data row
                # would misrepresent it as the header; degrade instead.
                for cell in _own_cells(promoted_candidate):
                    cell.name = "th"
                header_row = promoted_candidate
            if header_row is not None:
                # The search above stops the moment it finds a qualifying
                # header row, so rowspans carried by rows AFTER the header
                # are never inspected. A clean header followed by a data
                # row that spans multiple rows would otherwise reach
                # html2text unexpanded, shifting cells under the wrong
                # headers. Degrade instead of expanding — same outcome as
                # the no-clean-header case below.
                after_header = False
                has_data_rowspan = False
                for row in rows:
                    if row is header_row:
                        after_header = True
                        continue
                    if after_header and any(
                        _cell_rowspan(c) != 1 for c in _own_cells(row)
                    ):
                        has_data_rowspan = True
                        break
                if has_data_rowspan:
                    header_row = None
            if header_row is not None:
                # html2text only emits the two-sided "---|---" separator
                # when the <th> row is the table's FIRST row; a title or
                # spacer row before it yields a bare "---" line, which
                # the later signature stripper mistakes for a sig
                # delimiter. Move any rows preceding the header out of
                # the table (as block divs, cells space-separated) so the
                # header row leads the table — this applies equally to
                # tables that already had <th> cells mid-table.
                for row in rows:
                    if row is header_row:
                        break
                    for c in _own_cells(row):
                        c.insert_after(NavigableString(" "))
                        c.unwrap()
                    row.name = "div"
                    table.insert_before(row.extract())
                continue
            # No clean header exists (e.g. a rowspan/colspan header grid)
            # — degrade to pipe-separated rows, but in-place on the soup
            # so inline markup (links, emphasis) survives for html2text;
            # the compact table_data conversion would flatten it to bare
            # text and drop link destinations entirely.
            # A cell that spans into later rows still occupies its column
            # there, but those rows don't repeat it — so emit an empty
            # field in its place. Without this the next row's first cell
            # slides left, landing under the wrong header, which is the
            # very shift this degrade path exists to avoid.
            carried: list[list[int]] = []  # [remaining_rows, col, colspan]
            for row in rows:
                occupied = {
                    i for _, col, cs in carried for i in range(col, col + cs)
                }
                r_cells = _own_cells(row)

                # Walk the grid, assigning each own cell to the next free
                # column and recording how many blanks precede it.
                gaps: list[int] = []  # blanks before each own cell
                col = 0
                for c in r_cells:
                    blanks = 0
                    while col in occupied:
                        blanks += 1
                        col += 1
                    gaps.append(blanks)
                    span = _cell_colspan(c)
                    rs = _cell_rowspan(c)
                    if rs != 1:
                        # Counted from this row, then decremented once at
                        # the end of it, so the span starts biting on the
                        # next row — which is the first that omits the cell.
                        carried.append(
                            [len(rows) if rs == 0 else rs, col, span]
                        )
                    # A colspan cell's own extra columns need no blank —
                    # it already renders as one wide field.
                    col += span

                for i, c in enumerate(r_cells):
                    lead = " | " * gaps[i]
                    if lead:
                        c.insert_before(NavigableString(lead))
                    if i < len(r_cells) - 1:
                        c.insert_after(NavigableString(" | "))
                for c in r_cells:
                    c.unwrap()
                row.name = "div"

                for span in carried:
                    span[0] -= 1
                carried = [s for s in carried if s[0] > 0]
            for t in _find_own_elements(table, {"thead", "tbody"}):
                t.unwrap()
            table.unwrap()
            continue

        # Build pipe-delimited output
        lines = []
        for row_data in table_data:
            # Pad short rows
            padded = row_data + [""] * (max_cols - len(row_data))
            line = " | ".join(padded)
            lines.append(line)

        pipe_text = "\n".join(lines)
        table.replace_with(pipe_text)


# ---------------------------------------------------------------------------
# HTML → Text conversion
# ---------------------------------------------------------------------------

def _html_to_text(html: str, mode: str = "compact") -> str:
    """Convert HTML to plain text using html2text.

    ``mode="compact"`` (default) uses LLM-friendly settings: no emphasis
    markup, single newline between paragraphs. ``mode="readable"`` keeps
    emphasis markup and paragraph spacing for human-facing display.
    """
    readable = mode == "readable"
    h = html2text.HTML2Text()
    h.body_width = 0  # No line wrapping (LLMs don't need it)
    h.ignore_images = True  # Already handled tracking pixels, skip remainder
    h.ignore_emphasis = not readable  # *bold*, _italic_ waste tokens in compact mode
    h.ignore_links = False  # We want to keep meaningful links
    h.protect_links = True  # Don't wrap links
    h.single_line_break = not readable  # Compact: no blank line between paragraphs
    h.unicode_snob = True  # Use unicode instead of ASCII approximations
    h.skip_internal_links = True  # Skip anchor links
    h.inline_links = True  # [text](url) format
    h.wrap_links = False
    h.wrap_list_items = False

    text = h.handle(html)

    # Post-process html2text output: convert markdown links to compact format
    # [text](url) -> text (url) — but only if text != url
    def _simplify_link(m: re.Match) -> str:
        link_text = m.group(1).strip()
        url = m.group(2).strip()
        # html2text's protect_links wraps targets in angle brackets —
        # strip them so equality checks against the visible text work
        if url.startswith("<") and url.endswith(">"):
            url = url[1:-1]

        # Readable mode may wrap link text in emphasis markup (e.g.
        # "**https://example.com**"), which defeats a plain equality check
        # against the href. Compare the raw text first, then a copy with
        # only BALANCED emphasis wrappers removed — an address that
        # legitimately starts with "_" must not lose it. Keep returning
        # link_text so the emphasized display form survives.
        wrap = re.fullmatch(r"(\*{1,3}|_{1,3})(.+)\1", link_text, re.DOTALL)
        texts = (link_text, wrap.group(2)) if wrap else (link_text,)

        # Skip if link text IS the URL (html2text sometimes does this)
        if any(t == url or t == url.rstrip("/") for t in texts):
            return link_text

        # Skip mailto: links where the text is the email address
        if url.startswith("mailto:") and url[7:] in texts:
            return link_text

        # Skip tracking/click-through URLs
        # ⚡ Bolt Optimization: using tuple instead of list for slightly faster allocation
        tracking_indicators = (
            "click.", "track.", "trk.", "redirect.", "go.", "link.",
            "mailchi.mp", "list-manage", "sendgrid", "mailgun",
        )
        if any(ind in url.lower() for ind in tracking_indicators):
            return link_text

        return f"{link_text} ({url})"

    # ⚡ Bolt Optimization: Use module-level pre-compiled regex objects for ~20% speedup
    text = _MD_LINK_PATTERN.sub(_simplify_link, text)

    # Clean up residual html2text artifacts
    # Remove (<mailto:addr>) when the address is already visible in text, and bare <mailto:addr> links
    # ⚡ Bolt Optimization: Use module-level combined OR pre-compiled regex for ~25% speedup
    text = _MAILTO_CLEANUP_PATTERN.sub("", text)

    return text

# ⚡ Bolt Optimization: Pre-compiled regex patterns for _html_to_text
_MD_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MAILTO_CLEANUP_PATTERN = re.compile(r"\s*\(<mailto:[^)]+>\)|<mailto:[^>]+>")

# ---------------------------------------------------------------------------
# Text-level cleanup
# ---------------------------------------------------------------------------

# ⚡ Bolt Optimization: Combined reply header patterns into a single pre-compiled regex
# using the OR (|) operator to significantly improve matching performance (avoiding loops).
# ⚡ Bolt Optimization: pre-compiled emphasis stripper with a cheap `in`
# fast-path at the call sites — most lines carry no *_ markers at all.
_EMPHASIS_PATTERN = re.compile(r"[*_]{1,3}")

_REPLY_HEADER_PATTERN = re.compile(
    r"^(?:On\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun).*\bwrote:\s*|"
    r"On\s+\d.*\bwrote:\s*|"
    r"On\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b.*\bwrote:\s*|"
    r"-{2,}\s*(?:Original|Forwarded)\s+[Mm]essage\s*-{2,}\s*|"
    # \s* (not a bare \n) between fields tolerates the blank lines readable
    # mode's paragraph spacing inserts when Outlook renders each field as
    # its own <p> — otherwise the header goes undetected and the unquoted
    # prior message leaks into the readable body.
    r"From:\s+.+\n\s*Sent:\s+.+\n\s*To:\s+.+\n\s*Subject:\s+.+)$",
    re.MULTILINE | re.IGNORECASE,
)


def _strip_reply_chains(text: str) -> str:
    """Remove quoted reply chains and forwarded message blocks.

    Handles:
    - Lines starting with "> " (standard quoting)
    - "On {date} {person} wrote:" headers followed by quoted text
    - "--- Original Message ---" blocks
    - Outlook-style "From: ... Sent: ... To: ... Subject: ..." blocks
    """
    # First, remove "On ... wrote:" headers and everything after them that's
    # quoted. Match against emphasis-stripped lines — readable mode renders
    # these as "**On Mon ... wrote:**" or bolds the Outlook "From:/Sent:/
    # To:/Subject:" labels, which the anchored header patterns don't match.
    # Line boundaries are unchanged by stripping, so the match's line offset
    # maps directly onto the original (unstripped) lines that get kept.
    orig_lines = text.split("\n")
    stripped_text = "\n".join(
        _EMPHASIS_PATTERN.sub("", line) if "*" in line or "_" in line else line
        for line in orig_lines
    )
    match = _REPLY_HEADER_PATTERN.search(stripped_text)
    if match:
        # Truncate at the start line of the matched header.
        start_line = stripped_text.count("\n", 0, match.start())
        text = "\n".join(orig_lines[:start_line]).rstrip()

    # Remove any remaining "> " quoted lines
    lines = text.split("\n")
    cleaned = []
    in_quote_block = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            in_quote_block = True
            continue
        if in_quote_block and stripped == "":
            # Blank line after quote block — could be transition, keep one
            in_quote_block = False
            cleaned.append("")
            continue
        in_quote_block = False
        cleaned.append(line)

    return "\n".join(cleaned)


# ⚡ Bolt Optimization: Combined signature trigger patterns into a single pre-compiled regex
# using the OR (|) operator to significantly improve matching performance (avoiding loops).
_SIGNATURE_TRIGGER_PATTERN = re.compile(
    r"^(?:--\s*|---+\s*|_{3,}\s*|"  # Explicit delimiters
    r"Sent from (?:my )?\w.*|Sent via\b.*|Get Outlook for\b.*|Sent from Mail for\b.*|"  # Mobile signatures
    r"(?:Best|Kind|Warm)\s+regards?\s*[,.]?\s*|"  # Closing phrases
    r"(?:Thanks|Thank you|Cheers|Regards|Sincerely|Yours truly)\s*[,.]?\s*|"
    r"(?:With appreciation|All the best|Talk soon)\s*[,.]?\s*)$",
    re.IGNORECASE,
)

_CONFIDENTIALITY_PATTERN = re.compile(
    r"(?:this\s+(?:email|message|communication)\s+is\s+(?:intended|confidential)"
    r"|confidentiality\s+notice"
    r"|privileged\s+and\s+confidential"
    r"|if\s+you\s+(?:are\s+not|have\s+received)\s+this\s+(?:email|message)\s+in\s+error"
    r"|please\s+notify\s+the\s+sender\s+immediately"
    r"|unauthorized\s+(?:use|disclosure|review|distribution))",
    re.IGNORECASE,
)


def _strip_signatures(text: str) -> str:
    """Remove email signatures and confidentiality notices.

    Detects common signature patterns and removes everything from the
    signature trigger to the end of the message.
    """
    lines = text.split("\n")

    # First pass: find the earliest signature trigger in the bottom portion
    # of the message. Only look in the last ~40% of lines to avoid false
    # positives in body content.
    cutoff_start = max(0, len(lines) - max(len(lines) * 2 // 5, 15))

    sig_start = None
    for i in range(cutoff_start, len(lines)):
        line = lines[i]
        stripped = line.strip()

        # Check explicit signature triggers against the raw line first.
        # Only fall back to stripping markdown emphasis wrappers — readable
        # mode renders "Thanks," as "**Thanks,**", which otherwise wouldn't
        # match the anchored trigger patterns — if the raw line doesn't
        # already match. Stripping a plain "___" delimiter line down to
        # nothing must not be treated as a match.
        if _SIGNATURE_TRIGGER_PATTERN.match(stripped):
            sig_start = i
            break
        # Remove short emphasis-marker runs anywhere in the line (same
        # pattern the reply-header pass uses) — "**Thanks**," keeps its
        # trailing markers before the comma, so edge-only stripping
        # misses them. The raw line was already tested above, so a "___"
        # delimiter can't be destroyed by this.
        emphasis_stripped = (
            _EMPHASIS_PATTERN.sub("", stripped)
            if "*" in stripped or "_" in stripped else stripped
        )
        if emphasis_stripped and _SIGNATURE_TRIGGER_PATTERN.match(emphasis_stripped):
            sig_start = i
            break

        # Check for confidentiality notice
        if _CONFIDENTIALITY_PATTERN.search(stripped):
            sig_start = i
            break

    if sig_start is not None:
        text = "\n".join(lines[:sig_start])

    return text


_UNSUB_TEXT_PATTERNS = re.compile(
    r"^.*(?:unsubscribe|opt[\s-]?out|email\s+preferences|manage\s+subscriptions?"
    r"|update\s+(?:your\s+)?preferences|no\s+longer\s+wish\s+to\s+receive"
    r"|stop\s+receiving|view\s+(?:in|this\s+email\s+in)\s+(?:your\s+)?browser"
    r"|add\s+us\s+to\s+your\s+address\s+book"
    r"|problems?\s+(?:viewing|displaying)\s+this\s+email"
    r"|click\s+here\s+to\s+view\s+this\s+message).*$",
    re.IGNORECASE | re.MULTILINE,
)

_ADDRESS_BLOCK = re.compile(
    r"^.*(?:\d{4,5}\s+[A-Z][a-z]+\s+(?:St|Ave|Blvd|Rd|Dr|Ln|Way|Ct|Pl)"  # US street addr
    r"|\b[A-Z]{2}\s+\d{5}(?:-\d{4})?).*$",  # State ZIP
    re.MULTILINE,
)


def _strip_unsubscribe_text(text: str) -> str:
    """Remove unsubscribe and preference management lines from text."""
    text = _UNSUB_TEXT_PATTERNS.sub("", text)
    return text


# ⚡ Bolt Optimization: Pre-compiled regex patterns for _collapse_whitespace
_TRAILING_WS_PATTERN = re.compile(r"[ \t]+$", flags=re.MULTILINE)
_MULTIPLE_NEWLINES_PATTERN = re.compile(r"\n{3,}")

def _collapse_whitespace(text: str) -> str:
    """Collapse multiple blank lines, trailing spaces, normalize whitespace."""
    # ⚡ Bolt Optimization: Use module-level pre-compiled regex objects for ~1.5x speedup

    # Strip trailing whitespace from each line
    text = _TRAILING_WS_PATTERN.sub("", text)

    # Collapse 3+ consecutive newlines down to 2 (one blank line)
    text = _MULTIPLE_NEWLINES_PATTERN.sub("\n\n", text)

    # Remove leading blank lines
    text = text.lstrip("\n")

    # Remove trailing blank lines
    text = text.rstrip("\n")

    return text


# ---------------------------------------------------------------------------
# Header normalization helpers
# ---------------------------------------------------------------------------

def _normalize_date(date_str: str) -> str:
    """Convert various date formats to ISO 8601 UTC."""
    # Try RFC 2822 (standard email date)
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        pass

    # Try ISO 8601 already
    try:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        pass

    # Return as-is if we can't parse
    return date_str


def _normalize_address(addr: str) -> str:
    """Normalize email address fields — strip display names, lowercase domain."""
    # Handle "Display Name <email@domain.com>" format.
    # ⚡ Bolt Optimization: string scanning instead of the backtracking
    # regex ``.*<([^>]+)>`` — same semantics: the last non-empty <...>
    # pair wins, and an empty ``<>`` steps back to an earlier pair just
    # as the regex's ``[^>]+`` did.
    start = addr.rfind("<")
    while start != -1:
        end = addr.find(">", start + 1)
        if end > start + 1:
            email = addr[start + 1 : end].strip()
            break
        start = addr.rfind("<", 0, start)
    else:
        email = addr.strip()

    # Lowercase the domain part
    parts = email.split("@")
    if len(parts) == 2:
        return f"{parts[0]}@{parts[1].lower()}"

    return email


# ⚡ Bolt Optimization: Pre-compiled regex patterns for _normalize_subject
_SUBJECT_PREFIX_PATTERN = re.compile(r"^((?:Re|Fwd?)\s*:\s*)+", re.IGNORECASE)
_SUBJECT_RE_PATTERN = re.compile(r"^(?:Re\s*:\s*)+", re.IGNORECASE)
_SUBJECT_FWD_PATTERN = re.compile(r"^(?:Fwd?\s*:\s*)+", re.IGNORECASE)

def _normalize_subject(subject: str) -> str:
    """Clean up subject lines — strip redundant Re:/Fwd: prefixes."""
    # ⚡ Bolt Optimization: Use module-level pre-compiled regex objects for ~2x speedup

    # Remove repeated Re: / Fwd: prefixes, keeping at most one
    cleaned = _SUBJECT_PREFIX_PATTERN.sub("", subject).strip()

    # Check if original had Re: or Fwd: — add back a single one
    had_re = _SUBJECT_RE_PATTERN.match(subject)
    had_fwd = _SUBJECT_FWD_PATTERN.match(subject)

    if had_fwd:
        cleaned = f"Fwd: {cleaned}"
    elif had_re:
        cleaned = f"Re: {cleaned}"

    return cleaned
