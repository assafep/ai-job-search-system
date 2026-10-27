"""Stateless JSON-RPC MCP gateway. No application processing on import."""
from __future__ import annotations
import hmac
import os
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field
from gdtq import service

app=FastAPI(title="AJSS MCP",docs_url=None,redoc_url=None,openapi_url=None)
VERSION="2025-06-18"
TOOLS=[
    {"name":"lookup_posting","description":"Read-only historical Neon deduplication lookup by URL and/or company and role.",
     "inputSchema":{"type":"object","properties":{
         "url":{"type":"string"},"company":{"type":"string"},"role":{"type":"string"},
         "source_id":{"type":"string"}},"additionalProperties":False}},
    {"name":"database_status","description":"Read-only counts for Neon identity index, leases and checkpoints.",
     "inputSchema":{"type":"object","properties":{},"additionalProperties":False}},
    {"name":"queue_summary","description":"Read-only Google document tab and queue sizes.",
     "inputSchema":{"type":"object","properties":{},"additionalProperties":False}},
    {"name":"application_events","description":"Read one application tab and its accumulated processing event history.",
     "inputSchema":{"type":"object","properties":{"item_id":{"type":"string","minLength":1}},
                    "required":["item_id"],"additionalProperties":False}},
]
WRITE_TOOL={"name":"dispatch_once","description":"Process at most one GDTQ item synchronously; requires explicit deployment opt-in and Google credentials.",
 "inputSchema":{"type":"object","properties":{},"additionalProperties":False}}

def tools_available():
    result=list(TOOLS)
    if os.getenv("AJSS_ENABLE_DISPATCH")=="1":result.append(WRITE_TOOL)
    return result

def _authorized(header,expected):
    if not expected:return False
    prefix="Bearer "
    return bool(header and header.startswith(prefix) and
                hmac.compare_digest(header[len(prefix):],expected))

def _mcp_auth(request):
    if not _authorized(request.headers.get("Authorization"),os.getenv("AJSS_MCP_TOKEN")):
        raise HTTPException(status_code=401,detail="Unauthorized")

def invoke_tool(name,args):
    if not isinstance(args,dict):raise ValueError("Tool arguments must be an object")
    names={t["name"]:t for t in tools_available()}
    if name not in names:raise ValueError("Unknown or disabled tool")
    import jsonschema
    jsonschema.validate(args,names[name]["inputSchema"])
    if name=="database_status":return service.database_status()
    if name=="lookup_posting":return service.identity_lookup(**args)
    if name=="queue_summary":return service.queue_summary()
    if name=="application_events":return service.application_events(**args)
    if name=="dispatch_once":return service.dispatch_once()
    raise ValueError("Unknown tool")

def rpc_response(body):
    req_id=body.get("id")
    method=body.get("method")
    params=body.get("params") or {}
    if method=="initialize":
        return {"jsonrpc":"2.0","id":req_id,"result":{
            "protocolVersion":VERSION,"capabilities":{"tools":{"listChanged":False}},
            "serverInfo":{"name":"ajss-gdtq","version":"0.1.0"}}}
    if method=="ping":
        return {"jsonrpc":"2.0","id":req_id,"result":{}}
    if method=="tools/list":
        return {"jsonrpc":"2.0","id":req_id,"result":{"tools":tools_available()}}
    if method=="tools/call":
        name=params.get("name")
        try:
            result=invoke_tool(name,params.get("arguments") or {})
            import json
            return {"jsonrpc":"2.0","id":req_id,"result":{
                "content":[{"type":"text","text":json.dumps(result,default=str)}],
                "structuredContent":result,"isError":False}}
        except Exception as error:
            # Never echo database credentials, external messages, or tracebacks.
            return {"jsonrpc":"2.0","id":req_id,"result":{"content":[{
                "type":"text","text":"AJSS tool failed ("+type(error).__name__+")."}],
                "isError":True}}
    return {"jsonrpc":"2.0","id":req_id,"error":{"code":-32601,"message":"Method not found"}}

@app.get("/api/health")
@app.get("/health")
def health():
    return {"service":"ajss-gdtq","status":"ready","dispatch_enabled":os.getenv("AJSS_ENABLE_DISPATCH")=="1"}

@app.post("/api/mcp")
@app.post("/mcp")
async def mcp(request:Request):
    _mcp_auth(request)
    try:
        body=await request.json()
    except ValueError:
        return JSONResponse({"error":"Invalid JSON"},status_code=400)
    if not isinstance(body,dict) or body.get("jsonrpc")!="2.0":
        return JSONResponse({"error":"Invalid JSON-RPC envelope"},status_code=400)
    if body.get("method")=="notifications/initialized":return JSONResponse({},status_code=202)
    return rpc_response(body)

@app.post("/api/cron/dispatch")
@app.post("/cron/dispatch")
async def cron_dispatch(request:Request):
    if not _authorized(request.headers.get("Authorization"),os.getenv("CRON_SECRET")):
        raise HTTPException(status_code=401,detail="Unauthorized")
    if os.getenv("AJSS_ENABLE_DISPATCH")!="1":
        raise HTTPException(status_code=503,detail="Dispatch disabled")
    try:return service.dispatch_once()
    except RuntimeError:
        raise HTTPException(status_code=503,detail="Dispatch unavailable")
