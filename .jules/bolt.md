## 2026-07-20 - HTML2Text Instantiation Overhead
**Learning:** Instantiating `html2text.HTML2Text` and configuring its attributes on every HTML conversion is a major performance bottleneck, slowing down normalization by up to 5x. Furthermore, recompiling the `_looks_like_html` regex on every check adds measurable overhead.
**Action:** Use a `threading.local()` cache to reuse a configured `HTML2Text` instance safely in concurrent environments. Pre-compile the `_HTML_TAG_PATTERN` regex at the module level.
