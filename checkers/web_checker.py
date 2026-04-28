import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re

KEYWORDS = ["涉密", "秘密", "机密", "绝密", "保密", "泄密"]
PATTERNS = [re.compile(r'[\s\.\-_]*?'.join(re.escape(c) for c in kw), re.IGNORECASE) for kw in KEYWORDS]

def check_page_text(text):
    lines = text.splitlines()
    results = []
    for i, line in enumerate(lines, 1):
        matched = []
        for kw, pat in zip(KEYWORDS, PATTERNS):
            if pat.search(line):
                matched.append(kw)
        if matched:
            results.append((i, line.strip(), matched))
    return results

def check_website(url):
    visited = set()
    to_visit = {url}
    details = []

    while to_visit:
        page_url = to_visit.pop()
        if page_url in visited:
            continue
        visited.add(page_url)
        try:
            resp = requests.get(page_url, timeout=10)
            resp.encoding = resp.apparent_encoding
            text = resp.text
        except:
            continue
        found = check_page_text(text)
        if found:
            details.append({"url": page_url, "lines": found})
        try:
            soup = BeautifulSoup(text, 'html.parser')
            domain = urlparse(url).netloc
            for a in soup.find_all('a', href=True):
                href = urljoin(page_url, a['href'])
                if urlparse(href).netloc == domain and href not in visited:
                    to_visit.add(href)
        except:
            pass

    return {
        "checked_pages": len(visited),
        "secret_pages": len(details),
        "details": details
    }