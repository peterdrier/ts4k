"""Tests for ts4k normalizer — the core value proposition.

Each test uses realistic inline fixtures modeled on actual email patterns.
The composite test at the end measures byte reduction and asserts >= 70%.
"""

import pytest
from ts4k.core.normalize import normalize, normalize_headers


# ---------------------------------------------------------------------------
# 1. Basic HTML stripping
# ---------------------------------------------------------------------------

class TestBasicHtmlStripping:
    def test_paragraph_tags(self):
        html = "<p>Hello world.</p><p>Second paragraph.</p>"
        result = normalize(html)
        assert "Hello world." in result
        assert "Second paragraph." in result
        # Should not contain HTML tags
        assert "<p>" not in result

    def test_br_to_newline(self):
        html = "Line one<br>Line two<br/>Line three"
        result = normalize(html)
        assert "Line one" in result
        assert "Line two" in result
        assert "Line three" in result
        assert "<br" not in result

    def test_div_and_span(self):
        html = "<div><span>Important text</span> and <span>more text</span></div>"
        result = normalize(html)
        assert "Important text" in result
        assert "more text" in result
        assert "<div>" not in result
        assert "<span>" not in result

    def test_nested_tags(self):
        html = """
        <div>
            <table><tr><td>
                <p><strong>Bold</strong> and <em>italic</em> content.</p>
            </td></tr></table>
        </div>
        """
        result = normalize(html)
        assert "Bold" in result
        assert "italic" in result
        assert "content." in result

    def test_style_and_script_removed(self):
        html = """
        <html>
        <head><style>.foo { color: red; }</style></head>
        <body>
        <script>alert('xss')</script>
        <p>Real content here.</p>
        </body>
        </html>
        """
        result = normalize(html)
        assert "Real content here." in result
        assert "color: red" not in result
        assert "alert" not in result


# ---------------------------------------------------------------------------
# 2. Link preservation
# ---------------------------------------------------------------------------

class TestLinkPreservation:
    def test_meaningful_link_preserved(self):
        html = '<p>See the <a href="https://example.com/report">quarterly report</a> for details.</p>'
        result = normalize(html)
        assert "quarterly report" in result
        assert "https://example.com/report" in result

    def test_mailto_duplicate_suppressed(self):
        html = '<p>Contact <a href="mailto:alice@example.com">alice@example.com</a></p>'
        result = normalize(html)
        assert "alice@example.com" in result
        # Should NOT show both the email and a mailto: URL
        assert "mailto:" not in result

    def test_tracking_link_text_only(self):
        html = '<p>Click <a href="https://click.email-service.example.com/track/abc123">here</a> for info.</p>'
        result = normalize(html)
        assert "here" in result
        # Tracking URL should be stripped
        assert "click.email-service" not in result

    def test_url_as_link_text(self):
        """When the link text IS the URL, just show it once."""
        html = '<p>Visit <a href="https://example.com">https://example.com</a></p>'
        result = normalize(html)
        assert "https://example.com" in result
        # Should not appear twice
        count = result.count("https://example.com")
        assert count == 1


# ---------------------------------------------------------------------------
# 3. Tracking pixel removal
# ---------------------------------------------------------------------------

class TestTrackingPixelRemoval:
    def test_1x1_img(self):
        html = '''
        <p>Hello!</p>
        <img src="https://tracker.example.com/pixel.gif" width="1" height="1" />
        <p>Goodbye!</p>
        '''
        result = normalize(html)
        assert "Hello!" in result
        assert "Goodbye!" in result
        assert "tracker.example.com" not in result
        assert "pixel.gif" not in result

    def test_hidden_tracking_pixel(self):
        html = '''
        <p>Content here.</p>
        <img src="https://mail.example.com/o.gif?id=abc" style="display:none" width="1" height="1" />
        '''
        result = normalize(html)
        assert "Content here." in result
        assert "o.gif" not in result

    def test_beacon_image(self):
        html = '''
        <p>Real message.</p>
        <img src="https://beacon.example.com/track?msg=123" width="0" height="0" />
        '''
        result = normalize(html)
        assert "Real message." in result
        assert "beacon" not in result

    def test_css_hidden_pixel(self):
        html = '''
        <p>Hello.</p>
        <img src="https://mailtrack.io/trace/mail/open123" style="width:1px;height:1px" />
        '''
        result = normalize(html)
        assert "Hello." in result
        assert "mailtrack" not in result


# ---------------------------------------------------------------------------
# 4. Signature removal
# ---------------------------------------------------------------------------

class TestSignatureRemoval:
    def test_dash_delimiter(self):
        text = "Please review the attached.\n\n--\nAlice Smith\nVP Engineering\nAcme Corp\n+1 555 0100"
        result = normalize(text)
        assert "Please review the attached." in result
        assert "VP Engineering" not in result
        assert "555 0100" not in result

    def test_triple_dash(self):
        text = "Let me know your thoughts.\n\n---\nBob Jones\nSenior Developer"
        result = normalize(text)
        assert "Let me know your thoughts." in result
        assert "Bob Jones" not in result

    def test_underscore_delimiter(self):
        text = "Let me know your thoughts.\n\n___\nBob Jones\nSenior Developer"
        result = normalize(text)
        assert "Let me know your thoughts." in result
        assert "Bob Jones" not in result

    def test_sent_from_iphone(self):
        text = "Sure, sounds good.\n\nSent from my iPhone"
        result = normalize(text)
        assert "Sure, sounds good." in result
        assert "iPhone" not in result

    def test_sent_from_outlook(self):
        text = "I'll check on Monday.\n\nGet Outlook for iOS"
        result = normalize(text)
        assert "I'll check on Monday." in result
        assert "Outlook" not in result

    def test_best_regards_signature(self):
        text = (
            "Let's proceed with option B.\n\n"
            "Best regards,\n"
            "Alice Smith\n"
            "Director of Engineering\n"
            "Acme Corporation\n"
            "alice@acme.com\n"
            "+1 (555) 012-3456"
        )
        result = normalize(text)
        assert "Let's proceed with option B." in result
        assert "Director of Engineering" not in result

    def test_confidentiality_notice(self):
        text = (
            "Here is the update you requested.\n\n"
            "CONFIDENTIALITY NOTICE: This email message is intended only for the named "
            "recipient(s) and may contain confidential, proprietary, or privileged information. "
            "If you are not the intended recipient, please notify the sender immediately "
            "and delete this message."
        )
        result = normalize(text)
        assert "Here is the update you requested." in result
        assert "CONFIDENTIALITY NOTICE" not in result
        assert "proprietary" not in result


# ---------------------------------------------------------------------------
# 5. Reply chain stripping
# ---------------------------------------------------------------------------

class TestReplyChainStripping:
    def test_standard_quoted_reply(self):
        text = (
            "Yes, I agree with that approach.\n\n"
            "On Mon, Feb 19, 2026 at 10:00 AM Alice <alice@example.com> wrote:\n"
            "> I think we should go with option A.\n"
            "> It has the best ROI.\n"
            ">\n"
            "> On Sun, Feb 18, 2026 at 3:00 PM Bob wrote:\n"
            ">> What do you think about the options?\n"
        )
        result = normalize(text)
        assert "Yes, I agree with that approach." in result
        assert "option A" not in result
        assert "best ROI" not in result
        assert ">" not in result

    def test_outlook_style_reply(self):
        text = (
            "Thanks, will do.\n\n"
            "From: Alice Smith\n"
            "Sent: Monday, February 19, 2026 10:00 AM\n"
            "To: Bob Jones\n"
            "Subject: Meeting Tomorrow\n\n"
            "Bob, can you prepare the slides?\n"
        )
        result = normalize(text)
        assert "Thanks, will do." in result
        assert "can you prepare the slides?" not in result

    def test_forwarded_message(self):
        text = (
            "FYI see below.\n\n"
            "---------- Forwarded message ----------\n"
            "From: Charlie <charlie@example.com>\n"
            "Date: Feb 18, 2026\n"
            "Subject: Invoice #1234\n\n"
            "Please find attached the invoice.\n"
        )
        result = normalize(text)
        assert "FYI see below." in result
        assert "Invoice #1234" not in result

    def test_angle_bracket_quoting(self):
        text = (
            "Good point.\n\n"
            "> Original message content\n"
            "> More original content\n"
            "> Even more\n"
        )
        result = normalize(text)
        assert "Good point." in result
        assert "Original message content" not in result


# ---------------------------------------------------------------------------
# 6. Whitespace collapse
# ---------------------------------------------------------------------------

class TestWhitespaceCollapse:
    def test_multiple_blank_lines(self):
        text = "First paragraph.\n\n\n\n\nSecond paragraph."
        result = normalize(text)
        # Multiple blank lines should collapse to at most one
        assert "\n\n\n" not in result
        assert "First paragraph." in result
        assert "Second paragraph." in result

    def test_trailing_spaces(self):
        text = "Hello there.   \n  World.  "
        result = normalize(text)
        # No trailing spaces
        for line in result.split("\n"):
            assert line == line.rstrip(), f"Line has trailing whitespace: {line!r}"

    def test_leading_trailing_newlines(self):
        text = "\n\n\nContent here.\n\n\n"
        result = normalize(text)
        assert result == "Content here."

    def test_mixed_whitespace(self):
        html = "<p>  Hello  </p>\n\n\n\n<p>  World  </p>"
        result = normalize(html)
        assert "\n\n\n" not in result
        assert "Hello" in result
        assert "World" in result


# ---------------------------------------------------------------------------
# 7. Unsubscribe block removal
# ---------------------------------------------------------------------------

class TestUnsubscribeRemoval:
    def test_html_unsubscribe_footer(self):
        html = """
        <div>
            <p>Here is your weekly digest.</p>
        </div>
        <div style="font-size:11px;color:#999">
            <p>You are receiving this because you subscribed to our updates.</p>
            <p><a href="https://example.com/unsubscribe?id=abc123">Unsubscribe</a>
            | <a href="https://example.com/preferences">Update your preferences</a></p>
            <p>123 Main St, San Francisco, CA 94102</p>
        </div>
        """
        result = normalize(html)
        assert "weekly digest" in result
        assert "unsubscribe" not in result.lower()
        assert "preferences" not in result.lower()

    def test_text_unsubscribe_line(self):
        text = (
            "Check out our latest products.\n\n"
            "To unsubscribe from this mailing list, click here.\n"
            "Update your email preferences."
        )
        result = normalize(text)
        assert "latest products." in result
        assert "unsubscribe" not in result.lower()

    def test_no_longer_wish(self):
        html = """
        <p>Important meeting notes from today.</p>
        <div>
            <small>If you no longer wish to receive these emails,
            you can manage your notification settings here.</small>
        </div>
        """
        result = normalize(html)
        assert "meeting notes" in result
        assert "no longer wish" not in result.lower()


# ---------------------------------------------------------------------------
# 8. Table -> pipe-delimited
# ---------------------------------------------------------------------------

class TestTableConversion:
    def test_simple_data_table(self):
        html = """
        <table>
            <tr><th>Name</th><th>Amount</th><th>Status</th></tr>
            <tr><td>Alice</td><td>$500</td><td>Paid</td></tr>
            <tr><td>Bob</td><td>$750</td><td>Pending</td></tr>
        </table>
        """
        result = normalize(html)
        assert "Name | Amount | Status" in result
        assert "Alice | $500 | Paid" in result
        assert "Bob | $750 | Pending" in result
        assert "<table>" not in result

    def test_table_with_missing_cells(self):
        html = """
        <table>
            <tr><td>A</td><td>B</td><td>C</td></tr>
            <tr><td>1</td><td>2</td></tr>
        </table>
        """
        result = normalize(html)
        assert "A | B | C" in result
        # Short row should be padded
        assert "1 | 2 |" in result

    def test_single_column_layout_table(self):
        """Single-column tables are layout tables, not data — unwrap them."""
        html = """
        <table>
            <tr><td>Header image</td></tr>
            <tr><td>This is the main email content.</td></tr>
            <tr><td>Footer</td></tr>
        </table>
        """
        result = normalize(html)
        assert "main email content" in result
        # Should NOT be pipe-delimited since it's single-column
        assert "|" not in result

    def test_single_row_layout_table(self):
        """Single-row multi-column tables are layout wrappers too — unwrap them."""
        html = """
        <table>
            <tr><td>Logo</td><td>Spacer</td><td>Nav links</td></tr>
        </table>
        <p>Actual message content.</p>
        """
        result = normalize(html)
        assert "Actual message content" in result
        assert "Logo" in result
        # Should NOT be pipe-delimited since it's a single row
        assert "|" not in result

    def test_compact_data_table_inside_layout_wrapper_survives(self):
        """A data table nested in a single-cell layout wrapper must still
        convert to pipe-delimited rows, not get flattened into undelimited
        text by the wrapper's unwrap."""
        html = """
        <table>
            <tr><td>
                <table>
                    <tr><th>Item</th><th>Price</th></tr>
                    <tr><td>Widget</td><td>$9</td></tr>
                </table>
            </td></tr>
        </table>
        """
        result = normalize(html)
        assert "Item | Price" in result
        assert "Widget | $9" in result


# ---------------------------------------------------------------------------
# 9. Real-world composite — byte reduction test
# ---------------------------------------------------------------------------

class TestRealWorldComposite:
    """A realistic HTML email combining multiple noise sources.

    Modeled on a typical business email received from a colleague,
    with all the usual corporate email cruft.
    """

    REALISTIC_EMAIL = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Re: Q3 Budget Review</title>
<style>
body { font-family: Arial, sans-serif; font-size: 14px; color: #333; }
.header { background-color: #f5f5f5; padding: 20px; }
.content { padding: 20px; }
.footer { font-size: 11px; color: #999; padding: 20px; border-top: 1px solid #eee; }
.signature { margin-top: 20px; font-size: 12px; color: #666; }
blockquote { border-left: 2px solid #ccc; padding-left: 10px; color: #666; }
</style>
</head>
<body>
<div class="header">
<img src="https://company.com/logo.png" alt="Acme Corp" width="150" height="50">
</div>

<div class="content">
<p>Hi Peter,</p>

<p>I've reviewed the Q3 budget numbers and I think we need to adjust the
infrastructure line item. The cloud costs came in 15% over projection
last quarter, and with the new ML pipeline going into production, I'd
expect another 20% increase.</p>

<p>Can we schedule 30 minutes this week to discuss? I have availability
Wednesday afternoon or Thursday morning.</p>

<p>Also, here are the current numbers:</p>

<table border="1" cellpadding="5">
<tr><th>Category</th><th>Q2 Actual</th><th>Q3 Budget</th><th>Q3 Revised</th></tr>
<tr><td>Cloud Infrastructure</td><td>$45,000</td><td>$48,000</td><td>$57,600</td></tr>
<tr><td>Software Licenses</td><td>$12,000</td><td>$12,000</td><td>$12,000</td></tr>
<tr><td>Contractor Fees</td><td>$25,000</td><td>$30,000</td><td>$30,000</td></tr>
<tr><td>Total</td><td>$82,000</td><td>$90,000</td><td>$99,600</td></tr>
</table>

<p>Let me know what you think.</p>

<div class="signature">
<p>Best regards,</p>
<p><strong>Alice Chen</strong><br>
VP of Engineering<br>
Acme Corporation<br>
<a href="mailto:alice.chen@acme.com">alice.chen@acme.com</a><br>
+1 (555) 234-5678<br>
<a href="https://www.acme.com">www.acme.com</a></p>
</div>

<div style="margin-top:30px; padding-top:10px; border-top:1px solid #ccc;">
<p>On Tue, Feb 18, 2026 at 3:42 PM Test User &lt;peter@example.com&gt; wrote:</p>
<blockquote>
<p>Alice,</p>
<p>Can you take a look at the Q3 budget spreadsheet I shared yesterday?
I want to make sure the infrastructure estimates are realistic before
the board meeting next month.</p>
<p>The ML team is asking for a significant compute bump and I want your
take on whether those numbers are justified.</p>
<p>Thanks,<br>Peter</p>
</blockquote>
</div>

<div style="margin-top:20px; padding-top:10px; border-top:1px solid #ccc;">
<p>On Mon, Feb 17, 2026 at 9:15 AM Alice Chen &lt;alice@acme.com&gt; wrote:</p>
<blockquote>
<p>Peter, let's review the budget together this week. I have some concerns
about the cloud cost trajectory.</p>
<p>I'll pull together the latest numbers from our AWS dashboard.</p>
<p>Best,<br>Alice</p>
</blockquote>
</div>
</div>

<div class="footer">
<img src="https://tracking.acme.com/pixel.gif?campaign=internal&uid=abc123" width="1" height="1" style="display:none" />
<p>This email and any attachments are confidential and intended solely for the
use of the individual or entity to whom they are addressed. If you have received
this email in error, please notify the sender immediately and delete this message.
Unauthorized disclosure, copying, or distribution of this email is strictly prohibited.</p>
<p>Acme Corporation | 1234 Innovation Blvd, San Francisco, CA 94105</p>
<p><a href="https://company.com/unsubscribe?uid=abc123">Unsubscribe</a> |
<a href="https://company.com/preferences">Email Preferences</a></p>
</div>

</body>
</html>"""

    def test_byte_reduction(self):
        """Core metric: must achieve >= 70% byte reduction."""
        raw_bytes = len(self.REALISTIC_EMAIL.encode("utf-8"))
        result = normalize(self.REALISTIC_EMAIL)
        result_bytes = len(result.encode("utf-8"))
        reduction = 1.0 - (result_bytes / raw_bytes)

        # Log for visibility
        print(f"\n  Raw:    {raw_bytes:,} bytes")
        print(f"  Clean:  {result_bytes:,} bytes")
        print(f"  Reduction: {reduction:.1%}")
        print(f"  Result:\n---\n{result}\n---")

        assert reduction >= 0.70, (
            f"Byte reduction was only {reduction:.1%}, need >= 70%. "
            f"Raw={raw_bytes}, Clean={result_bytes}"
        )

    def test_meaningful_content_preserved(self):
        """The actual message content must survive normalization."""
        result = normalize(self.REALISTIC_EMAIL)

        # Greeting and body
        assert "Hi Peter" in result
        assert "Q3 budget numbers" in result
        assert "infrastructure line item" in result
        assert "cloud costs came in 15% over" in result
        assert "20% increase" in result
        assert "Wednesday afternoon" in result

        # Table should be pipe-delimited
        assert "Cloud Infrastructure" in result
        assert "$57,600" in result
        assert "|" in result

        # Call to action preserved
        assert "Let me know what you think" in result

    def test_noise_removed(self):
        """All the noise sources should be gone."""
        result = normalize(self.REALISTIC_EMAIL)
        result_lower = result.lower()

        # Signature
        assert "VP of Engineering" not in result
        assert "+1 (555) 234-5678" not in result

        # Reply chain
        assert "Can you take a look at the Q3 budget spreadsheet" not in result
        assert "cloud cost trajectory" not in result

        # Tracking pixel
        assert "pixel.gif" not in result
        assert "tracking.acme.com" not in result

        # Confidentiality notice
        assert "confidential" not in result_lower
        assert "unauthorized" not in result_lower

        # Unsubscribe
        assert "unsubscribe" not in result_lower

        # HTML artifacts
        assert "<" not in result or result.count("<") == 0
        assert "font-family" not in result
        assert "class=" not in result


# ---------------------------------------------------------------------------
# 10. Plain text passthrough
# ---------------------------------------------------------------------------

class TestPlainTextPassthrough:
    def test_clean_text_unchanged(self):
        text = "Hi Peter,\n\nJust confirming our meeting at 3pm.\n\nThanks,\nAlice"
        result = normalize(text)
        assert "Hi Peter" in result
        assert "confirming our meeting at 3pm" in result

    def test_plain_text_with_noise(self):
        """Plain text with signatures and quotes should still be cleaned."""
        text = (
            "Sounds good, let's go with option B.\n\n"
            "On Mon, Feb 19, 2026 at 10:00 AM Alice <alice@example.com> wrote:\n"
            "> What about option A?\n"
            "> It seems simpler.\n"
        )
        result = normalize(text)
        assert "option B" in result
        assert "option A" not in result

    def test_empty_input(self):
        assert normalize("") == ""
        assert normalize("   ") == ""
        assert normalize(None) == ""


# ---------------------------------------------------------------------------
# 11. Header normalization
# ---------------------------------------------------------------------------

class TestReadableMode:
    """``mode='readable'`` preserves structure for human display."""

    def test_default_mode_is_compact(self):
        """Omitting mode= must produce identical output to mode='compact'."""
        html = "<p>Hello world.</p><p><strong>Bold</strong> text.</p>"
        assert normalize(html) == normalize(html, mode="compact")

    def test_compact_strips_emphasis(self):
        """Regression check: compact mode's existing emphasis-stripping is unchanged."""
        html = "<p><strong>Bold</strong> and <em>italic</em> text.</p>"
        result = normalize(html)
        assert "**" not in result
        assert "_italic_" not in result
        assert "Bold" in result
        assert "italic" in result

    def test_compact_no_paragraph_blank_line(self):
        """Regression check: compact mode's existing tight paragraph spacing is unchanged."""
        html = "<p>First paragraph.</p><p>Second paragraph.</p>"
        result = normalize(html)
        assert "\n\n" not in result

    def test_readable_preserves_paragraph_breaks(self):
        html = "<p>First paragraph.</p><p>Second paragraph.</p>"
        result = normalize(html, mode="readable")
        assert "First paragraph." in result
        assert "Second paragraph." in result
        assert "\n\n" in result

    def test_readable_preserves_emphasis(self):
        html = "<p><strong>Bold</strong> and <em>italic</em> text.</p>"
        result = normalize(html, mode="readable")
        assert "**Bold**" in result
        assert "_italic_" in result

    def test_readable_layout_table_is_unwrapped(self):
        """Single-column layout wrappers must not render as markdown tables."""
        html = """
        <table>
            <tr><td><p>Welcome to our <b>newsletter</b>.</p></td></tr>
            <tr><td><p>Second paragraph of content.</p></td></tr>
        </table>
        """
        result = normalize(html, mode="readable")
        assert "Welcome to our" in result
        assert "Second paragraph" in result
        # No markdown table artifacts for a layout wrapper
        assert "|" not in result
        assert "---" not in result
        # Inline markup survives the unwrap
        assert "**newsletter**" in result

    def test_readable_single_row_layout_table_is_unwrapped(self):
        """A single-row multi-column table is a layout wrapper — unwrap markup-preserving."""
        html = """
        <table>
            <tr><td><b>Logo</b></td><td>Nav link</td></tr>
        </table>
        """
        result = normalize(html, mode="readable")
        assert "**Logo**" in result
        assert "Nav link" in result
        # No markdown table artifacts for a layout wrapper
        assert "|" not in result
        assert "---" not in result

    def test_readable_data_table_inside_layout_wrapper_survives(self):
        """A data table nested in a layout wrapper still renders as markdown."""
        html = """
        <table>
            <tr><td>
                <table>
                    <tr><th>Item</th><th>Price</th></tr>
                    <tr><td>Widget</td><td>$9</td></tr>
                </table>
            </td></tr>
        </table>
        """
        result = normalize(html, mode="readable")
        assert "Widget" in result
        assert "$9" in result
        assert "---" in result

    def test_readable_table_is_markdown(self):
        html = """
        <table>
            <tr><th>Name</th><th>Amount</th></tr>
            <tr><td>Alice</td><td>$500</td></tr>
            <tr><td>Bob</td><td>$750</td></tr>
        </table>
        """
        result = normalize(html, mode="readable")
        assert "Alice" in result
        assert "$500" in result
        assert "Bob" in result
        assert "$750" in result
        # A markdown header-separator row (e.g. "---|---") must be present.
        assert "---" in result
        # Rows must be on separate lines, not pipe-collapsed onto one line.
        lines = [line for line in result.split("\n") if line.strip()]
        alice_line = next(line for line in lines if "Alice" in line)
        bob_line = next(line for line in lines if "Bob" in line)
        assert alice_line != bob_line
        assert "Bob" not in alice_line

    def test_readable_td_only_table_is_markdown(self):
        """A genuine data table using only <td> cells (no <th>) still renders as markdown."""
        html = """
        <table>
            <tr><td>Name</td><td>Amount</td></tr>
            <tr><td>Alice</td><td>$500</td></tr>
            <tr><td>Bob</td><td>$750</td></tr>
        </table>
        """
        result = normalize(html, mode="readable")
        # A markdown header-separator row (e.g. "---|---") must be present.
        assert "---" in result
        # Rows must be on separate lines, not pipe-collapsed onto one line.
        lines = [line for line in result.split("\n") if line.strip()]
        alice_line = next(line for line in lines if "Alice" in line)
        bob_line = next(line for line in lines if "Bob" in line)
        assert alice_line != bob_line
        assert "Bob" not in alice_line

    def test_readable_td_only_table_with_empty_first_row_still_gets_separator(self):
        """An empty spacer <tr> as the first row must not block
        th-promotion — the first row WITH data must be promoted, not
        rows[0] itself."""
        html = """
        <table>
            <tr><td></td><td></td></tr>
            <tr><td>Name</td><td>Amount</td></tr>
            <tr><td>Alice</td><td>$500</td></tr>
        </table>
        """
        result = normalize(html, mode="readable")
        assert "---" in result
        assert "Alice" in result
        assert "$500" in result

    def test_readable_td_only_table_colspan_title_row_not_promoted(self):
        """A title row spanning the full table width via colspan must not
        be promoted to <th> — it has only one cell element, so promoting
        it would give html2text a one-column header against the 2-column
        data rows below it, producing a mismatched separator."""
        html = """
        <table>
            <tr><td colspan="2">Sales</td></tr>
            <tr><td>Q1</td><td>$100</td></tr>
            <tr><td>Q2</td><td>$200</td></tr>
        </table>
        """
        result = normalize(html, mode="readable")
        # Title text is not lost, just not promoted to the header.
        assert "Sales" in result
        assert "Q1" in result
        assert "$100" in result
        assert "Q2" in result
        assert "$200" in result
        # The separator row must have 2 columns, not 1.
        lines = [line for line in result.split("\n") if line.strip()]
        sep_line = next(
            line
            for line in lines
            if "-" in line and set(line.strip()) <= set("-|: ")
        )
        cols = [c for c in sep_line.split("|") if c.strip()]
        assert len(cols) == 2

    def test_readable_layout_row_cells_are_space_separated(self):
        """Unwrapping a layout row's cells must not merge adjacent text —
        "<td>Logo</td><td>Nav</td>" must not collapse to "LogoNav"."""
        html = "<table><tr><td>Logo</td><td>Nav</td></tr></table>"
        result = normalize(html, mode="readable")
        assert "LogoNav" not in result
        assert "Logo Nav" in result

    def test_readable_layout_rows_stay_on_separate_lines(self):
        """Unwrapping a single-column layout table's rows must not merge
        them onto the same line."""
        html = "<table><tr><td>First</td></tr><tr><td>Second</td></tr></table>"
        result = normalize(html, mode="readable")
        lines = [line.strip() for line in result.split("\n") if line.strip()]
        first_line = next(line for line in lines if "First" in line)
        second_line = next(line for line in lines if "Second" in line)
        assert first_line != second_line
        assert "Second" not in first_line

    def test_readable_still_strips_tracking_pixel(self):
        html = """
        <p>Content here.</p>
        <img src="https://tracker.example.com/pixel.gif" width="1" height="1" />
        """
        result = normalize(html, mode="readable")
        assert "Content here." in result
        assert "tracker.example.com" not in result

    def test_readable_still_strips_signature(self):
        text = "Please review the attached.\n\n--\nAlice Smith\nVP Engineering"
        result = normalize(text, mode="readable")
        assert "Please review the attached." in result
        assert "VP Engineering" not in result

    def test_readable_strips_emphasized_signature(self):
        """readable mode renders "Thanks," as "**Thanks,**" — must still match."""
        html = "<p>Please review the attached.</p><p><strong>Thanks,</strong></p><p>Alice</p>"
        result = normalize(html, mode="readable")
        assert "Please review the attached." in result
        assert "Alice" not in result
        assert "Thanks" not in result

    def test_readable_strips_signature_after_underscore_delimiter(self):
        """Regression: emphasis-stripping "___" to "" must not defeat the raw match."""
        text = "Please review the attached.\n\n___\nAlice Smith\nVP Engineering"
        result = normalize(text, mode="readable")
        assert "Please review the attached." in result
        assert "VP Engineering" not in result

    def test_readable_still_strips_reply_chain(self):
        text = (
            "Yes, I agree with that approach.\n\n"
            "On Mon, Feb 19, 2026 at 10:00 AM Alice <alice@example.com> wrote:\n"
            "> I think we should go with option A.\n"
        )
        result = normalize(text, mode="readable")
        assert "Yes, I agree with that approach." in result
        assert "option A" not in result

    def test_readable_strips_emphasized_reply_header(self):
        """readable mode renders "On Mon ... wrote:" as "**On Mon ... wrote:**" — must still match."""
        html = (
            "<p>Yes, I agree with that approach.</p>"
            "<p><strong>On Mon, Feb 19, 2026 at 10:00 AM Alice wrote:</strong></p>"
            "<blockquote><p>I think we should go with option A.</p></blockquote>"
        )
        result = normalize(html, mode="readable")
        assert "Yes, I agree with that approach." in result
        assert "option A" not in result

    def test_readable_strips_emphasized_outlook_header(self):
        """readable mode bolds Outlook "From:/Sent:/To:/Subject:" labels — must still match."""
        html = (
            "<p>Thanks, will do.</p>"
            "<p><strong>From:</strong> Alice Smith<br>"
            "<strong>Sent:</strong> Monday, February 19, 2026 10:00 AM<br>"
            "<strong>To:</strong> Bob Jones<br>"
            "<strong>Subject:</strong> Meeting Tomorrow</p>"
            "<p>Bob, can you prepare the slides?</p>"
        )
        result = normalize(html, mode="readable")
        assert "Thanks, will do." in result
        assert "can you prepare the slides?" not in result

    def test_readable_still_strips_unsubscribe(self):
        html = """
        <div><p>Here is your weekly digest.</p></div>
        <div style="font-size:11px;color:#999">
            <p><a href="https://example.com/unsubscribe?id=abc123">Unsubscribe</a></p>
        </div>
        """
        result = normalize(html, mode="readable")
        assert "weekly digest" in result
        assert "unsubscribe" not in result.lower()


class TestHeaderNormalization:
    def test_basic_normalization(self):
        headers = {
            "From": "  Alice Chen <alice@ACME.COM>  ",
            "To": "Test User <peter@EXAMPLE.COM>",
            "Subject": "  Meeting tomorrow  ",
            "Date": "Tue, 18 Feb 2026 15:42:00 -0800",
        }
        result = normalize_headers(headers)

        assert result["from"] == "alice@acme.com"
        assert result["to"] == "peter@example.com"
        assert result["subject"] == "Meeting tomorrow"
        assert result["date"] == "2026-02-18T23:42:00Z"

    def test_repeated_re_prefix(self):
        headers = {"Subject": "Re: Re: Re: Budget Review"}
        result = normalize_headers(headers)
        assert result["subject"] == "Re: Budget Review"

    def test_fwd_prefix(self):
        headers = {"Subject": "Fwd: Fw: Fwd: Invoice"}
        result = normalize_headers(headers)
        assert result["subject"] == "Fwd: Invoice"

    def test_iso_date_passthrough(self):
        headers = {"Date": "2026-02-18T23:42:00Z"}
        result = normalize_headers(headers)
        assert result["date"] == "2026-02-18T23:42:00Z"

    def test_empty_headers(self):
        assert normalize_headers({}) == {}
        assert normalize_headers(None) == {}

    def test_folded_header(self):
        """Headers with line folding (multi-line values) should be collapsed."""
        headers = {"Subject": "Very long\n    subject line\n    continues here"}
        result = normalize_headers(headers)
        assert result["subject"] == "Very long subject line continues here"

    def test_unknown_headers_preserved(self):
        headers = {"X-Custom-Header": "  some value  ", "Message-ID": "<abc@example.com>"}
        result = normalize_headers(headers)
        assert result["x-custom-header"] == "some value"
        assert result["message-id"] == "<abc@example.com>"
