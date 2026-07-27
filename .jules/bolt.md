## 2024-05-24 - Pre-compiling Regex in Beautiful Soup iterations
**Learning:** Pre-compiling regular expressions using `re.compile` at the module level instead of inline in functions (especially functions called frequently during HTML processing or beautiful soup searches) results in a noticeable performance improvement by saving regex compilation overhead.
**Action:** Always pre-compile frequently used regular expressions at the module level rather than inside functions.
