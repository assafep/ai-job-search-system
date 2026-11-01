"""MCP protocol and gateway guards without remote account connections."""
import os
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from api.index import app,rpc_response,tools_available
from gdtq.postgres_index import historical_keys,canonical_legacy_url

def test_init_and_tools_available_by_default(monkeypatch):
    monkeypatch.delenv("AJSS_ENABLE_DISPATCH",raising=False)
    result=rpc_response({"jsonrpc":"2.0","id":1,"method":"initialize"})
    assert result["result"]["protocolVersion"]=="2025-06-18"
    names=[x["name"] for x in tools_available()]
    assert names==["lookup_posting","database_status","queue_summary","application_events"]

def test_authentication_fails_closed(monkeypatch):
    monkeypatch.delenv("AJSS_MCP_TOKEN",raising=False)
    c=TestClient(app)
    assert c.post("/api/mcp",json={"jsonrpc":"2.0","method":"tools/list","id":1}).status_code==401
    monkeypatch.setenv("AJSS_MCP_TOKEN","test-only-token")
    assert c.post("/api/mcp",headers={"Authorization":"Bearer incorrect"},
        json={"jsonrpc":"2.0","method":"tools/list","id":1}).status_code==401
    r=c.post("/api/mcp",headers={"Authorization":"Bearer test-only-token"},
        json={"jsonrpc":"2.0","method":"tools/list","id":1})
    assert r.status_code==200

def test_tool_validation_and_safe_error(monkeypatch):
    monkeypatch.setenv("AJSS_MCP_TOKEN","test-only-token")
    with patch("api.index.service.identity_lookup",return_value={"duplicate":True,"matches":[]}):
        c=TestClient(app)
        r=c.post("/api/mcp",headers={"Authorization":"Bearer test-only-token"},
          json={"jsonrpc":"2.0","id":5,"method":"tools/call",
                "params":{"name":"lookup_posting","arguments":{"company":"Acme","role":"Engineer"}}})
        assert r.json()["result"]["structuredContent"]["duplicate"]
    unknown=rpc_response({"jsonrpc":"2.0","id":6,"method":"tools/call","params":{
        "name":"not_a_tool","arguments":{}}})
    assert unknown["result"]["isError"]

def test_dispatch_disabled_even_with_mcp_auth(monkeypatch):
    monkeypatch.delenv("AJSS_ENABLE_DISPATCH",raising=False)
    monkeypatch.setenv("CRON_SECRET","cron-test-token")
    c=TestClient(app)
    assert c.post("/api/cron/dispatch",headers={"Authorization":"Bearer cron-test-token"}).status_code==503
    assert c.post("/api/cron/dispatch",headers={"Authorization":"Bearer wrong"}).status_code==401

def test_normalization_for_historical_lookup():
    assert ("company_role","acme / engineer") in historical_keys(" Acme ","Engineer")
    assert canonical_legacy_url("https://example.com/job/?utm_source=x&gh_jid=1")=="https://example.com/job?gh_jid=1"
