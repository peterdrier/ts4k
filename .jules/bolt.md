## 2026-07-25 - Regex Compilation and Parser Initialization Bottleneck
**Learning:** Frequent, dynamic compilation of identical regex patterns within functions (`re.compile()` calls during execution) and redundant instantiation of stateful text parsers (like `html2text.HTML2Text()`) added significant execution time to the core normalization routine.
**Action:** Always pre-compile standard regex patterns at the module level. Use `threading.local()` to safely cache and reuse instances of heavy stateful objects per-thread to avoid instantiation overhead while maintaining thread safety.
