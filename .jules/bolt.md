## 2025-02-24 - Precompiled Regex Performance
**Learning:** Compiling regex dynamically within tight loops (like scraping large DOMs using BeautifulSoup's `find_all` loops with multiple `re.compile()` calls) is an anti-pattern that creates measurable latency for the parser.
**Action:** Always extract regex expressions into module-level constant pre-compiled variables, even if initially constructed programmatically.
