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
from bs4 import BeautifulSoup, Tag


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(html: str) -> str:
    """Full normalization pipeline.

    Takes raw HTML (or plain text), returns clean text optimized for LLM
    consumption. Applies the full ts4k preprocessing pipeline in order.
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

        # Convert tables to pipe-delimited format before html2text gets them
        _convert_tables(soup)

        # Convert the cleaned HTML to text using html2text
        text = _html_to_text(str(soup))
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
            value = value.strip()
            # Collapse internal whitespace in header values (folded headers)
            value = re.sub(r"\s+", " ", value)

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

def _looks_like_html(text: str) -> bool:
    """Determine if text is HTML rather than plain text.

    We need to distinguish actual HTML tags from angle-bracket email addresses
    like <alice@example.com> which appear in plain-text reply headers.
    """
    # Look for common HTML structural tags (not just any angle-bracket pattern)
    html_tag_pattern = re.compile(
        r"<(?:html|head|body|div|span|p|br|table|tr|td|th|a\s|img\s|"
        r"h[1-6]|ul|ol|li|strong|em|b|i|style|script|meta|link|footer|header|"
        r"blockquote|center|font|!DOCTYPE|!--)[^>]*>",
        re.IGNORECASE,
    )
    return bool(html_tag_pattern.search(text))


# ---------------------------------------------------------------------------
# HTML preprocessing (BeautifulSoup phase)
# ---------------------------------------------------------------------------

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
        style = img.get("style", "")
        if style:
            if re.search(r"width\s*:\s*[01]px", style) or \
               re.search(r"height\s*:\s*[01]px", style) or \
               re.search(r"display\s*:\s*none", style):
                is_tiny = True

        # Check for common tracking pixel URL patterns
        src = img.get("src", "")
        tracking_patterns = [
            r"track", r"pixel", r"beacon", r"open\.", r"\.gif\?",
            r"mailtrack", r"t\.co/", r"click\.", r"/o\.gif",
            r"spacer", r"transparent", r"/t\?", r"wf\.gif",
        ]
        if src and any(re.search(p, src, re.IGNORECASE) for p in tracking_patterns):
            is_tiny = True

        # Check for images with no alt text and very small size
        if not img.get("alt") and is_tiny:
            img.decompose()
        elif is_tiny:
            img.decompose()


def _remove_hidden_elements(soup: BeautifulSoup) -> None:
    """Remove elements that are hidden via CSS or attributes."""
    for el in soup.find_all(style=re.compile(r"display\s*:\s*none", re.IGNORECASE)):
        el.decompose()
    for el in soup.find_all(style=re.compile(r"visibility\s*:\s*hidden", re.IGNORECASE)):
        el.decompose()
    for el in soup.find_all(attrs={"hidden": True}):
        el.decompose()
    # Zero-size divs/spans used for tracking
    for el in soup.find_all(style=re.compile(r"(width|height)\s*:\s*0", re.IGNORECASE)):
        # Only remove if element has no visible text content
        if not el.get_text(strip=True):
            el.decompose()


def _remove_unsubscribe_blocks_html(soup: BeautifulSoup) -> None:
    """Remove unsubscribe / email preference sections from HTML before text conversion.

    These are typically in footer divs, tables, or paragraphs at the end.
    """
    unsub_patterns = re.compile(
        r"unsubscribe|opt[\s-]?out|email\s+preferences|manage\s+(?:your\s+)?subscriptions?"
        r"|update\s+(?:your\s+)?preferences|notification\s+settings"
        r"|mailing\s+list|no\s+longer\s+wish\s+to\s+receive"
        r"|stop\s+receiving\s+these\s+emails",
        re.IGNORECASE,
    )

    # Remove links that are unsubscribe links.
    # Collect first, then decompose, to avoid mutating the tree during iteration.
    unsub_links = []
    for a in soup.find_all("a"):
        # After decomposing a parent, child tags lose their .attrs — guard against that
        if a.attrs is None:
            continue
        href = a.get("href", "")
        link_text = a.get_text(strip=True).lower()
        if "unsubscribe" in href.lower() or "unsubscribe" in link_text:
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
        if unsub_patterns.search(el_text) and len(el_text) < 1000:
            unsub_elements.append(el)

    for el in unsub_elements:
        # Guard: element may already have been decomposed as a child of a prior element
        if el.parent is not None:
            el.decompose()


def _convert_tables(soup: BeautifulSoup) -> None:
    """Convert HTML tables to pipe-delimited text.

    Only converts tables that look like data tables (not layout tables).
    Layout tables (single column, single row, or deeply nested) are unwrapped.
    """
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            # Empty table, remove
            table.decompose()
            continue

        # Extract cell data
        table_data: list[list[str]] = []
        max_cols = 0

        for row in rows:
            cells = row.find_all(["th", "td"])
            cell_texts = [c.get_text(strip=True) for c in cells]
            if any(cell_texts):  # skip entirely empty rows
                table_data.append(cell_texts)
                max_cols = max(max_cols, len(cell_texts))

        if not table_data or max_cols == 0:
            table.decompose()
            continue

        # Heuristic: if it's a single-column table or a single-row table,
        # it's probably a layout table — just unwrap the text
        if max_cols <= 1:
            text_content = "\n".join(
                " ".join(row) for row in table_data if any(row)
            )
            table.replace_with(text_content)
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

def _html_to_text(html: str) -> str:
    """Convert HTML to plain text using html2text with LLM-friendly settings."""
    h = html2text.HTML2Text()
    h.body_width = 0  # No line wrapping (LLMs don't need it)
    h.ignore_images = True  # Already handled tracking pixels, skip remainder
    h.ignore_emphasis = False  # Preserve **bold** and _italic_ for readability
    h.ignore_links = False  # We want to keep meaningful links
    h.protect_links = True  # Don't wrap links
    h.single_line_break = False  # Preserve paragraph breaks
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

        # Skip if link text IS the URL (html2text sometimes does this)
        if link_text == url or link_text == url.rstrip("/"):
            return url

        # Skip mailto: links where the text is the email address
        if url.startswith("mailto:") and url[7:] == link_text:
            return link_text

        # Skip tracking/click-through URLs
        tracking_indicators = [
            "click.", "track.", "trk.", "redirect.", "go.", "link.",
            "mailchi.mp", "list-manage", "sendgrid", "mailgun",
        ]
        if any(ind in url.lower() for ind in tracking_indicators):
            return link_text

        return f"{link_text} ({url})"

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _simplify_link, text)

    # Clean up residual html2text artifacts
    # Remove (<mailto:addr>) when the address is already visible in text
    text = re.sub(r"\s*\(<mailto:[^)]+>\)", "", text)
    # Remove bare <mailto:addr> links
    text = re.sub(r"<mailto:[^>]+>", "", text)

    return text


# ---------------------------------------------------------------------------
# Text-level cleanup
# ---------------------------------------------------------------------------

_REPLY_HEADER_PATTERNS = [
    # "On Mon, Feb 19, 2026 at 10:00 AM Alice <alice@...> wrote:"
    re.compile(
        r"^On\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun).*\bwrote:\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    # "On 2026-02-19, Alice wrote:" / "On February 19, 2026, Alice wrote:"
    re.compile(
        r"^On\s+\d.*\bwrote:\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    # "On Feb 19, 2026 at 10:00 AM, Alice wrote:"
    re.compile(
        r"^On\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b.*\bwrote:\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    # "--- Original Message ---" / "---------- Forwarded message ----------"
    re.compile(
        r"^-{2,}\s*(?:Original|Forwarded)\s+[Mm]essage\s*-{2,}\s*$",
        re.MULTILINE,
    ),
    # "From: ... Sent: ... To: ... Subject: ..." (Outlook-style)
    re.compile(
        r"^From:\s+.+\nSent:\s+.+\nTo:\s+.+\nSubject:\s+.+",
        re.MULTILINE | re.IGNORECASE,
    ),
]


def _strip_reply_chains(text: str) -> str:
    """Remove quoted reply chains and forwarded message blocks.

    Handles:
    - Lines starting with "> " (standard quoting)
    - "On {date} {person} wrote:" headers followed by quoted text
    - "--- Original Message ---" blocks
    - Outlook-style "From: ... Sent: ... To: ... Subject: ..." blocks
    """
    # First, remove "On ... wrote:" headers and everything after them that's quoted
    for pattern in _REPLY_HEADER_PATTERNS:
        match = pattern.search(text)
        if match:
            # Truncate at the reply header
            text = text[:match.start()].rstrip()

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


# Signature patterns — compiled once at module level
_SIGNATURE_TRIGGERS = [
    # Explicit delimiters
    re.compile(r"^--\s*$"),  # "-- " standard sig delimiter
    re.compile(r"^---+\s*$"),  # "---" or longer
    re.compile(r"^_{3,}\s*$"),  # "___" or longer

    # Mobile signatures
    re.compile(r"^Sent from (?:my )?\w", re.IGNORECASE),
    re.compile(r"^Sent via\b", re.IGNORECASE),
    re.compile(r"^Get Outlook for\b", re.IGNORECASE),
    re.compile(r"^Sent from Mail for\b", re.IGNORECASE),

    # Closing phrases that typically start signatures
    re.compile(r"^(?:Best|Kind|Warm)\s+regards?\s*[,.]?\s*$", re.IGNORECASE),
    re.compile(r"^(?:Thanks|Thank you|Cheers|Regards|Sincerely|Yours truly)\s*[,.]?\s*$", re.IGNORECASE),
    re.compile(r"^(?:With appreciation|All the best|Talk soon)\s*[,.]?\s*$", re.IGNORECASE),
]

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

        # Check explicit signature triggers
        for pattern in _SIGNATURE_TRIGGERS:
            if pattern.match(stripped):
                sig_start = i
                break

        if sig_start is not None:
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


def _collapse_whitespace(text: str) -> str:
    """Collapse multiple blank lines, trailing spaces, normalize whitespace."""
    # Strip trailing whitespace from each line
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)

    # Collapse 3+ consecutive newlines down to 2 (one blank line)
    text = re.sub(r"\n{3,}", "\n\n", text)

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
    # Handle "Display Name <email@domain.com>" format
    m = re.match(r".*<([^>]+)>", addr)
    if m:
        email = m.group(1).strip()
    else:
        email = addr.strip()

    # Lowercase the domain part
    parts = email.split("@")
    if len(parts) == 2:
        return f"{parts[0]}@{parts[1].lower()}"

    return email


def _normalize_subject(subject: str) -> str:
    """Clean up subject lines — strip redundant Re:/Fwd: prefixes."""
    # Remove repeated Re: / Fwd: prefixes, keeping at most one
    cleaned = re.sub(r"^((?:Re|Fwd?)\s*:\s*)+", "", subject, flags=re.IGNORECASE).strip()

    # Check if original had Re: or Fwd: — add back a single one
    had_re = re.match(r"(?:Re\s*:\s*)+", subject, re.IGNORECASE)
    had_fwd = re.match(r"(?:Fwd?\s*:\s*)+", subject, re.IGNORECASE)

    if had_fwd:
        cleaned = f"Fwd: {cleaned}"
    elif had_re:
        cleaned = f"Re: {cleaned}"

    return cleaned
