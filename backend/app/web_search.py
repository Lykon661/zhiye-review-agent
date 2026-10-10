"""Read public search results and bounded page excerpts; never execute web content."""
from hashlib import sha256
from html.parser import HTMLParser
import ipaddress
import re
import socket
from time import monotonic
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx

from .lexical import lexical_score, query_terms
from .llm import StructuredOutputError
from .models import now


class PageText(HTMLParser):
    ignored = {"script", "style", "nav", "header", "footer", "aside", "svg", "canvas", "form", "noscript"}
    breaks = {"p", "div", "li", "br", "section", "article", "h1", "h2", "h3", "h4", "tr", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.ignored:
            self.skip.append(tag)
        elif tag in self.breaks and not self.skip:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.skip:
            self.skip = self.skip[:self.skip.index(tag)]
        elif tag in self.breaks and not self.skip:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        return "\n".join(line for part in "".join(self.parts).splitlines()
                         if (line := re.sub(r"\s+", " ", part).strip()))


def public_url(value):
    """Reject credentials, local hosts, non-web schemes and unusual ports."""
    if not isinstance(value, str) or len(value) > 3000 or any(ord(c) < 32 for c in value):
        raise ValueError("网页地址无效")
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 80, 443)):
        raise ValueError("只读取公开 HTTP/HTTPS 网页")
    host = parsed.hostname.lower().rstrip(".")
    if host in ("localhost", "localhost.localdomain") or host.endswith((".local", ".localhost", ".internal")):
        raise ValueError("不读取本机或内网地址")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None  # Domains are resolved and checked before each request.
    if address is not None and not address.is_global:
        raise ValueError("不读取本机或内网地址")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def read_public(client, url, deadline, max_bytes=500000):
    for _ in range(4):
        url = public_url(url)
        address = httpx.URL(url)
        host = address.raw_host.decode("ascii")
        seconds = deadline - monotonic()
        if seconds <= 0:
            raise ValueError("联网读取超时")
        resolved = {record[4][0] for record in socket.getaddrinfo(host, address.port or (443 if address.scheme == "https" else 80), type=socket.SOCK_STREAM)}
        if not resolved or any(not ipaddress.ip_address(ip).is_global for ip in resolved):
            raise ValueError("网页域名指向本机或内网，已跳过")
        ip = sorted(resolved, key=lambda item: (":" in item, item))[0]
        # Pin the checked IP, while preserving Host and TLS certificate hostname.
        # Each redirect is resolved and checked again before making its request.
        target = address.copy_with(host=ip)
        client.cookies.clear()
        with client.stream("GET", target, headers={"Host": host},
                           extensions={"sni_hostname": host}, timeout=min(8, seconds)) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise ValueError("网页重定向缺少地址")
                url = urljoin(url, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if not any(kind in content_type for kind in ("text/", "xml", "html")):
                raise ValueError("该结果不是可读取的文字网页")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > max_bytes or monotonic() > deadline:
                    raise ValueError("网页过大或读取超时，已跳过")
                chunks.append(chunk)
            data = b"".join(chunks)
            encoding = response.encoding or "utf-8"
            if "charset=" not in content_type:
                charset = re.search(rb'charset\s*=\s*["\x27]?([\w-]+)', data[:4000], re.I)
                if charset:
                    encoding = charset[1].decode("ascii")
            try:
                return data.decode(encoding, errors="replace"), url, content_type
            except LookupError:
                return data.decode("utf-8", errors="replace"), url, content_type
    raise ValueError("网页重定向次数过多")


def plain_html(value):
    parser = PageText()
    parser.feed(value)
    return parser.text()


def excerpt(value, query, limit=1800):
    paragraphs = [part.strip() for part in value.splitlines() if len(part.strip()) >= 20]
    if not paragraphs:
        return value[:limit]
    terms, _ = query_terms(query)
    ranked = sorted(range(len(paragraphs)), key=lambda i: lexical_score(paragraphs[i], terms), reverse=True)
    chosen = set()
    for index in ranked[:3]:
        chosen.update(range(max(0, index - 1), min(len(paragraphs), index + 2)))
    return "\n".join(paragraphs[index] for index in sorted(chosen))[:limit]


def search_web(query):
    query = re.sub(r"\s+", " ", str(query)).strip()[:180]
    if len(query) < 2:
        raise StructuredOutputError("请使用具体知识点作为联网检索词")
    deadline = monotonic() + 35
    try:
        with httpx.Client(follow_redirects=False, trust_env=False, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ZhiyeReview/1.0)", "Accept-Encoding": "identity",
        }) as client:
            xml, _, _ = read_public(client, "https://www.bing.com/search?" + urlencode({"q": query, "format": "rss"}), deadline, 150000)
            if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", xml, re.I):
                raise ValueError("搜索结果格式无效")
            root = ElementTree.fromstring(xml)
            results, seen = [], set()
            for item in root.findall(".//item")[:6]:
                try:
                    url = public_url(item.findtext("link", ""))
                except ValueError:
                    continue
                if url in seen:
                    continue
                seen.add(url)
                title = plain_html(item.findtext("title", ""))[:180] or urlsplit(url).hostname
                text = plain_html(item.findtext("description", ""))[:1000]
                content_kind = "search_snippet"
                if monotonic() < deadline:
                    try:
                        page, actual_url, content_type = read_public(client, url, deadline)
                        content = plain_html(page) if "html" in content_type else page
                        if len(content.strip()) >= 120:
                            text, url, content_kind = excerpt(content, query), actual_url, "page_excerpt"
                    except (ValueError, OSError, httpx.HTTPError):
                        pass  # A snippet is explicitly labelled; never claim the page was read.
                if not text.strip():
                    continue
                identity = "web:" + sha256(url.encode()).hexdigest()[:24]
                results.append({"id": identity, "chunk_id": identity, "source_id": identity,
                    "kind": "web", "external": True, "title": title, "filename": title,
                    "url": url, "text": text, "year": None, "chapter_id": None,
                    "locator": {"label": "网页摘录" if content_kind == "page_excerpt" else "搜索结果摘要"},
                    "content_kind": content_kind, "retrieved_at": now()})
                if len(results) >= 3 or monotonic() >= deadline:
                    break
            if not results:
                raise ValueError("未找到可读取的搜索结果")
            return results
    except (ValueError, OSError, httpx.HTTPError, ElementTree.ParseError) as exc:
        raise StructuredOutputError("联网搜索暂未取得可用内容，可换一个通用知识点检索词或稍后重试。") from exc
