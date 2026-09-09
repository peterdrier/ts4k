from bs4 import BeautifulSoup
import re

_UNSUB_PATTERNS_HTML = re.compile(
    r"unsubscribe|opt[\s-]?out|email\s+preferences|manage\s+(?:your\s+)?subscriptions?"
    r"|update\s+(?:your\s+)?preferences|notification\s+settings"
    r"|mailing\s+list|no\s+longer\s+wish\s+to\s+receive"
    r"|stop\s+receiving\s+these\s+emails",
    re.IGNORECASE,
)

html_content = """
<html>
<body>
  <div>
    <p>This is highly relevant valid content that we absolutely need to keep. It talks about important things.</p>
    <p>And here is a small unsubscribe link at the bottom of the content container.</p>
    <a href="http://example.com/unsubscribe">Click here to unsubscribe</a>
  </div>
</body>
</html>
"""

def original(html):
    soup = BeautifulSoup(html, "html.parser")
    unsub_links = []
    for a in soup.find_all("a"):
        if a.attrs is None: continue
        href = a.get("href", "")
        link_text = a.get_text(strip=True).lower()
        if "unsubscribe" in href.lower() or "unsubscribe" in link_text:
            unsub_links.append(a)

    for a in unsub_links:
        if a.attrs is None: continue
        parent = a.parent
        if parent and parent.name in ("p", "div", "td", "span", "li", "center"):
            parent_text = parent.get_text(strip=True)
            if len(parent_text) < 500:
                parent.decompose()
                continue
        a.decompose()

    unsub_elements = []
    for el in soup.find_all(["div", "p", "table", "tr", "td", "center", "footer"]):
        el_text = el.get_text(strip=True)
        if _UNSUB_PATTERNS_HTML.search(el_text) and len(el_text) < 1000:
            unsub_elements.append(el)

    for el in unsub_elements:
        if el.parent is not None:
            el.decompose()
    return str(soup)

def new(html):
    soup = BeautifulSoup(html, "html.parser")
    unsub_links = []
    unsub_elements = []

    for el in soup.find_all(["a", "div", "p", "table", "tr", "td", "center", "footer"]):
        if el.name == "a":
            if el.attrs is None: continue
            href = el.get("href", "")
            if "unsubscribe" in href.lower():
                unsub_links.append(el)
            else:
                link_text = el.get_text(strip=True).lower()
                if "unsubscribe" in link_text:
                    unsub_links.append(el)
        else:
            el_text = el.get_text(strip=True)
            if len(el_text) < 1000 and _UNSUB_PATTERNS_HTML.search(el_text):
                unsub_elements.append(el)

    for a in unsub_links:
        if a.attrs is None: continue
        parent = a.parent
        if parent and parent.name in ("p", "div", "td", "span", "li", "center"):
            parent_text = parent.get_text(strip=True)
            if len(parent_text) < 500:
                parent.decompose()
                continue
        a.decompose()

    for el in unsub_elements:
        if el.parent is not None:
            el.decompose()
    return str(soup)

print("Original:")
print(original(html_content))
print("\nNew:")
print(new(html_content))
