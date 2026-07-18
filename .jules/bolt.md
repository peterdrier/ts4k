## 2026-07-18 - Stateful parsers and Thread Safety
**Learning:** `html2text.HTML2Text` is stateful (it inherits from `html.parser.HTMLParser` and maintains internal buffers and parsing state across calls). Caching a single instance globally (`_GLOBAL_H2T`) makes the `_html_to_text` function fundamentally thread-unsafe. In concurrent environments, this causes severe race conditions. Reinstantiating and configuring it each time was taking significant time though.
**Action:** Extracting the configuration to a factory function `_get_h2t_instance` instead of global caching is safer, and profiling showed that `html2text.HTML2Text()` actually instantiates quite fast while other string concatenations were the real bottleneck. Always avoid global caching of stateful objects without thread-local storage or object pools.

## 2026-07-18 - Raw string escape sequences in Regex
**Learning:** In a raw string `r""`, `\\` translates to a literal backslash. Using `r"\\.gif\?"` changes the pattern to search for `\<any_char>gif?` instead of `.gif?`.
**Action:** When migrating lists of regex strings to a combined `|` string, use non-capturing groups `(?:...)` or properly handle raw string escaping (e.g. `r"(?:\.gif\?)"`) to avoid breaking the regex.
