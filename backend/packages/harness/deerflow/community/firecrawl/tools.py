import hashlib
import json
import threading
import time

from firecrawl import FirecrawlApp
from langchain.tools import tool
from requests.exceptions import RequestException

from deerflow.community.ddg_search import tools as ddgs_tools
from deerflow.config import get_app_config


# Per-process cooldown; changing credentials immediately allows a new attempt.
_cooldown: tuple[str, float, str] | None = None
_cooldown_lock = threading.Lock()


def _failure_reason(error: Exception) -> tuple[str, int]:
    # Some SDK versions wrap transport failures while handling a missing response.
    seen = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (RequestException, TimeoutError)):
            status = getattr(getattr(current, "response", None), "status_code", None)
            if status is None:
                return "connection_failed", 60
        current = current.__cause__ or current.__context__
    status = getattr(error, "status_code", None)
    if status == 402:
        return "quota_exhausted", 3600
    if status in (401, 403):
        return "access_denied", 300
    if status == 429:
        return "rate_limited", 60
    if status == 408 or (isinstance(status, int) and status >= 500):
        return "service_unavailable", 60
    return "request_failed", 0


def _result(query: str, provider: str, results: list[dict], **details) -> str:
    return json.dumps({"query": query, "provider": provider,
                       "total_results": len(results), "results": results, **details}, ensure_ascii=False)


def _fallback_search(query: str, limit: int, options: dict, reason: str) -> str:
    try:
        results = ddgs_tools._search_text(
            query, max_results=limit, region=options.get("region", "wt-wt"),
            safesearch=options.get("safesearch", "moderate"),
            backend=options.get("backend", "auto"), raise_errors=True,
        )
    except Exception:
        return _result(query, "ddgs", [], fallback_reason=reason,
                       error="DDGS search failed after Firecrawl was unavailable. Try again later.")
    return _result(query, "ddgs", [
        {"title": item.get("title", ""), "url": item.get("href", item.get("link", "")),
         "snippet": item.get("body", item.get("snippet", ""))} for item in results
    ], fallback_reason=reason)


def _get_firecrawl_client(tool_name: str = "web_search") -> FirecrawlApp:
    config = get_app_config().get_tool_config(tool_name)
    api_key = None
    if config is not None and "api_key" in config.model_extra:
        api_key = config.model_extra.get("api_key")
    if tool_name == "web_search":
        return FirecrawlApp(api_key=api_key, timeout=20, max_retries=0)
    return FirecrawlApp(api_key=api_key)  # type: ignore[arg-type]


@tool("web_search", parse_docstring=True)
def web_search_tool(query: str) -> str:
    """Search the web for current information, documentation, news and reference links.
    Returns titles, URLs and snippets with the actual search provider. When configured,
    DDGS handles searches without a configured API key and takes over if Firecrawl
    is unavailable. The API key and fallback are configured in config.yaml.
    Use web_fetch to read full pages.

    Args:
        query: The query to search for.
    """
    global _cooldown
    config = get_app_config().get_tool_config("web_search")
    options = config.model_extra if config is not None else {}
    max_results = options.get("max_results", 5)
    fallback = options.get("fallback") == "ddgs"
    if not options.get("api_key"):
        if fallback:
            return _fallback_search(query, max_results, options, "not_configured")
        return _result(query, "firecrawl", [], error="Configure the Firecrawl API key or fallback: ddgs in config.yaml.")
    # Do not retain credentials in cooldown state or return raw provider errors.
    identity = hashlib.sha256(str(options.get("api_key", "")).encode()).hexdigest()
    with _cooldown_lock:
        reason = _cooldown[2] if _cooldown and _cooldown[0] == identity and _cooldown[1] > time.monotonic() else None
    if fallback and reason:
        return _fallback_search(query, max_results, options, reason)

    try:
        client = _get_firecrawl_client("web_search")
        result = client.search(query, limit=max_results)

        # result.web contains list of SearchResultWeb objects
        web_results = result.web or []
        normalized_results = [
            {
                "title": getattr(item, "title", "") or "",
                "url": getattr(item, "url", "") or "",
                "snippet": getattr(item, "description", "") or "",
            }
            for item in web_results
        ]
        return _result(query, "firecrawl", normalized_results)
    except Exception as e:
        reason, cooldown_seconds = _failure_reason(e)
        if fallback and cooldown_seconds:
            with _cooldown_lock:
                _cooldown = (identity, time.monotonic() + cooldown_seconds, reason)
            return _fallback_search(query, max_results, options, reason)
        return _result(query, "firecrawl", [], error=f"Firecrawl search failed ({reason}).")


@tool("web_fetch", parse_docstring=True)
def web_fetch_tool(url: str) -> str:
    """Fetch the contents of a web page at a given URL.
    Only fetch EXACT URLs that have been provided directly by the user or have been returned in results from the web_search and web_fetch tools.
    This tool can NOT access content that requires authentication, such as private Google Docs or pages behind login walls.
    Do NOT add www. to URLs that do NOT have them.
    URLs must include the schema: https://example.com is a valid URL while example.com is an invalid URL.

    Args:
        url: The URL to fetch the contents of.
    """
    try:
        client = _get_firecrawl_client("web_fetch")
        result = client.scrape(url, formats=["markdown"])

        markdown_content = result.markdown or ""
        metadata = result.metadata
        title = metadata.title if metadata and metadata.title else "Untitled"

        if not markdown_content:
            return "Error: No content found"
    except Exception as e:
        return f"Error: {str(e)}"

    return f"# {title}\n\n{markdown_content[:4096]}"
