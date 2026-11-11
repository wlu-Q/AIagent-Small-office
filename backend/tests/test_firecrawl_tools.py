"""Unit tests for the Firecrawl community tools."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from firecrawl.v2.utils.error_handler import FirecrawlError
from requests.exceptions import Timeout

from deerflow.community.firecrawl import tools


@pytest.fixture(autouse=True)
def reset_cooldown(monkeypatch):
    monkeypatch.setattr(tools, "_cooldown", None)


class TestWebSearchTool:
    @patch("deerflow.community.firecrawl.tools.FirecrawlApp")
    @patch("deerflow.community.firecrawl.tools.get_app_config")
    def test_search_uses_web_search_config(self, mock_get_app_config, mock_firecrawl_cls):
        search_config = MagicMock()
        search_config.model_extra = {"api_key": "firecrawl-search-key", "max_results": 7}
        mock_get_app_config.return_value.get_tool_config.return_value = search_config

        mock_result = MagicMock()
        mock_result.web = [
            MagicMock(title="Result", url="https://example.com", description="Snippet"),
        ]
        mock_firecrawl_cls.return_value.search.return_value = mock_result

        from deerflow.community.firecrawl.tools import web_search_tool

        result = web_search_tool.invoke({"query": "test query"})

        parsed = json.loads(result)
        assert parsed["provider"] == "firecrawl"
        assert parsed["results"] == [
            {
                "title": "Result",
                "url": "https://example.com",
                "snippet": "Snippet",
            }
        ]
        mock_get_app_config.return_value.get_tool_config.assert_called_with("web_search")
        mock_firecrawl_cls.assert_called_once_with(api_key="firecrawl-search-key", timeout=20, max_retries=0)
        mock_firecrawl_cls.return_value.search.assert_called_once_with("test query", limit=7)


@pytest.fixture
def search_environment(monkeypatch):
    options = {"api_key": "test-secret", "max_results": 3, "fallback": "ddgs"}
    app = MagicMock()
    app.get_tool_config.return_value = SimpleNamespace(model_extra=options)
    monkeypatch.setattr(tools, "get_app_config", lambda: app)
    client = MagicMock()
    client.search.return_value = SimpleNamespace(web=[])
    monkeypatch.setattr(tools, "_get_firecrawl_client", lambda name: client)
    fallback = MagicMock(return_value=[{"title": "DDG", "href": "https://example.com", "body": "Text"}])
    monkeypatch.setattr(tools.ddgs_tools, "_search_text", fallback)
    return options, client, fallback


@pytest.mark.parametrize("status,reason", [(402, "quota_exhausted"), (429, "rate_limited"),
    (401, "access_denied"), (403, "access_denied"), (408, "service_unavailable"),
    (500, "service_unavailable"), (503, "service_unavailable")])
def test_provider_errors_fall_back_and_cool_down(search_environment, status, reason):
    _, client, fallback = search_environment
    client.search.side_effect = FirecrawlError("test-secret must never appear", status)
    for query in ("first query", "next query"):
        raw = tools.web_search_tool.invoke({"query": query})
        result = json.loads(raw)
        assert result["provider"] == "ddgs"
        assert result["fallback_reason"] == reason
        assert result["results"][0]["snippet"] == "Text"
        assert "test-secret" not in raw
    assert client.search.call_count == 1
    assert fallback.call_count == 2
    assert fallback.call_args.kwargs["raise_errors"] is True


def test_cooldown_expires_and_changed_key_can_retry(search_environment, monkeypatch):
    options, client, fallback = search_environment
    now = [100.0]
    monkeypatch.setattr(tools.time, "monotonic", lambda: now[0])
    client.search.side_effect = FirecrawlError("quota", 402)
    tools.web_search_tool.invoke({"query": "first"})
    now[0] += 3601
    client.search.side_effect = None
    assert json.loads(tools.web_search_tool.invoke({"query": "retry"}))["provider"] == "firecrawl"
    client.search.side_effect = FirecrawlError("quota", 402)
    tools.web_search_tool.invoke({"query": "quota again"})
    options["api_key"] = "replacement-key"
    client.search.side_effect = None
    assert json.loads(tools.web_search_tool.invoke({"query": "new key"}))["provider"] == "firecrawl"


def test_no_key_uses_existing_ddgs_fallback_without_calling_firecrawl(search_environment, monkeypatch):
    options, client, fallback = search_environment
    options.pop('api_key')
    # Do not accidentally pick up credentials from the SDK's environment.
    monkeypatch.setenv('FIRECRAWL_API_KEY', 'unrelated-environment-key')
    result = json.loads(tools.web_search_tool.invoke({'query': 'test'}))
    assert result['provider'] == 'ddgs'
    assert result['fallback_reason'] == 'not_configured'
    assert result['results'][0]['snippet'] == 'Text'
    client.search.assert_not_called()
    fallback.assert_called_once()


def test_missing_key_with_fallback_disabled_is_explicit(search_environment):
    options, client, fallback = search_environment
    options.pop('api_key')
    options.pop('fallback')
    result = json.loads(tools.web_search_tool.invoke({'query': 'test'}))
    assert result['provider'] == 'firecrawl'
    assert 'config.yaml' in result['error']
    client.search.assert_not_called()
    fallback.assert_not_called()


def test_both_providers_fail_without_exposing_raw_errors(search_environment):
    _, client, fallback = search_environment
    client.search.side_effect = Timeout("test-secret")
    fallback.side_effect = RuntimeError("test-secret")
    raw = tools.web_search_tool.invoke({"query": "test"})
    assert "error" in json.loads(raw)
    assert "test-secret" not in raw


@pytest.mark.parametrize("mode", ["empty", "bad_request", "disabled"])
def test_no_unnecessary_fallback(search_environment, mode):
    options, client, fallback = search_environment
    if mode == "bad_request":
        client.search.side_effect = FirecrawlError("invalid input", 400)
    elif mode == "disabled":
        options.pop("fallback")
        client.search.side_effect = FirecrawlError("quota", 402)
    result = json.loads(tools.web_search_tool.invoke({"query": "test"}))
    assert result["provider"] == "firecrawl"
    assert ("error" in result) == (mode != "empty")
    fallback.assert_not_called()


def test_sdk_transport_wrapper_still_falls_back(search_environment, monkeypatch):
    from firecrawl import FirecrawlApp
    from firecrawl.v2.utils.http_client import HttpClient

    _, _, fallback = search_environment
    monkeypatch.setattr(HttpClient, "post", MagicMock(side_effect=Timeout("test-secret")))
    monkeypatch.setattr(tools, "_get_firecrawl_client", lambda name: FirecrawlApp(api_key="test-key", max_retries=0))
    result = json.loads(tools.web_search_tool.invoke({"query": "test"}))
    assert result["provider"] == "ddgs"
    assert result["fallback_reason"] == "connection_failed"
    fallback.assert_called_once()


class TestWebFetchTool:
    @patch("deerflow.community.firecrawl.tools.FirecrawlApp")
    @patch("deerflow.community.firecrawl.tools.get_app_config")
    def test_fetch_uses_web_fetch_config(self, mock_get_app_config, mock_firecrawl_cls):
        fetch_config = MagicMock()
        fetch_config.model_extra = {"api_key": "firecrawl-fetch-key"}

        def get_tool_config(name):
            if name == "web_fetch":
                return fetch_config
            return None

        mock_get_app_config.return_value.get_tool_config.side_effect = get_tool_config

        mock_scrape_result = MagicMock()
        mock_scrape_result.markdown = "Fetched markdown"
        mock_scrape_result.metadata = MagicMock(title="Fetched Page")
        mock_firecrawl_cls.return_value.scrape.return_value = mock_scrape_result

        from deerflow.community.firecrawl.tools import web_fetch_tool

        result = web_fetch_tool.invoke({"url": "https://example.com"})

        assert result == "# Fetched Page\n\nFetched markdown"
        mock_get_app_config.return_value.get_tool_config.assert_any_call("web_fetch")
        mock_firecrawl_cls.assert_called_once_with(api_key="firecrawl-fetch-key")
        mock_firecrawl_cls.return_value.scrape.assert_called_once_with(
            "https://example.com",
            formats=["markdown"],
        )
