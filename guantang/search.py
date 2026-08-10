import html
from html.parser import HTMLParser

import httpx

SEARCH_TIMEOUT = 15
DEFAULT_BING = "https://www.bing.com/search"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _format_results(results: list[dict], max_results: int) -> str:
    lines = []
    for i, item in enumerate(results[:max_results], 1):
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        content = ((item.get("content") or item.get("snippet") or "")).strip()
        if content:
            content = content[:200]
        lines.append(f"{i}. {title}\n   链接：{url}\n   摘要：{content}")
    return "\n\n".join(lines) or "搜索完成，但没有找到相关结果。"


async def search_tavily(query: str, api_key: str = "", max_results: int = 5) -> str:
    if not api_key:
        return "搜索失败：未配置 Tavily API 密钥"
    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "query": query,
                    "max_results": max_results,
                    "topic": "general",
                    "search_depth": "basic",
                },
            )
            if resp.status_code != 200:
                return f"搜索失败：Tavily 返回 HTTP {resp.status_code}"
            data = resp.json()
            results = data.get("results") or []
            return _format_results(results, max_results)
    except Exception as e:
        return f"搜索失败：{e}"


class _BingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self._in_algo = False
        self._in_h2 = False
        self._in_link = False
        self._in_p = False
        self._buf = []
        self._cur = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "li" and "b_algo" in classes:
            self._in_algo = True
            self._cur = {"title": "", "url": "", "content": ""}
            return
        if not self._in_algo or not self._cur:
            return
        if tag == "h2":
            self._in_h2 = True
        elif tag == "a" and self._in_h2:
            self._in_link = True
            self._cur["url"] = attrs.get("href", "")
        elif tag == "p":
            self._in_p = True
            self._buf = []

    def handle_data(self, data):
        if self._in_link:
            self._cur["title"] += data
        elif self._in_p:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "li" and self._in_algo:
            self._in_algo = False
            if self._cur and (self._cur["title"].strip() or self._cur["url"]):
                self.results.append(self._cur)
            self._cur = None
        elif tag == "h2":
            self._in_h2 = False
            self._in_link = False
        elif tag == "p" and self._in_p:
            self._cur["content"] = "".join(self._buf).strip()
            self._in_p = False


async def search_bing(query: str, max_results: int = 5, base_url: str = "") -> str:
    try:
        headers = {"User-Agent": BROWSER_UA, "Accept-Language": "zh-CN,zh;q=0.9"}
        params = {"q": query, "mkt": "zh-CN", "count": str(max_results)}
        async with httpx.AsyncClient(
            timeout=SEARCH_TIMEOUT, headers=headers, follow_redirects=True
        ) as client:
            resp = await client.get(base_url or DEFAULT_BING, params=params)
            if resp.status_code != 200:
                return f"搜索失败：必应返回 HTTP {resp.status_code}"
            parser = _BingParser()
            parser.feed(resp.text)
            results = []
            for item in parser.results:
                results.append(
                    {
                        "title": html.unescape(item["title"]).strip(),
                        "url": item["url"],
                        "content": html.unescape(item["content"]).strip(),
                    }
                )
            return _format_results(results, max_results)
    except Exception as e:
        return f"搜索失败：{e}"


async def search(provider: str, query: str, api_key: str = "", base_url: str = "", max_results: int = 5) -> str:
    name = (provider or "tavily").lower()
    if name == "tavily":
        return await search_tavily(query, api_key, max_results)
    if name == "bing":
        return await search_bing(query, max_results, base_url)
    return f"搜索失败：未知搜索服务商 {name}"
