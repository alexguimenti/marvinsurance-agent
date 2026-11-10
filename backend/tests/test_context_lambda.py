"""The context layer on Lambda: Function URL events, with a Function URL's host, twice in one process."""
import json

from context.server import handler

HOST = "abc123.lambda-url.us-east-1.on.aws"


def function_url_event(rpc: dict) -> dict:
    """What a Function URL sends (payload format 2.0) for a POST of one MCP message to /mcp."""
    body = json.dumps(rpc)
    return {
        "version": "2.0", "routeKey": "$default", "rawPath": "/mcp", "rawQueryString": "",
        "headers": {"host": HOST, "content-type": "application/json", "accept": "application/json, text/event-stream",
                    "content-length": str(len(body))},
        "requestContext": {"http": {"method": "POST", "path": "/mcp", "sourceIp": "10.0.0.1", "protocol": "HTTP/1.1"}, "stage": "$default"},
        "body": body, "isBase64Encoded": False,
    }


def test_the_handler_lists_the_tools_then_calls_one_twice_in_one_process():
    listed = handler(function_url_event({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}), None)
    assert listed["statusCode"] == 200
    names = {t["name"] for t in json.loads(listed["body"])["result"]["tools"]}
    assert {"lookup_term", "read_section", "get_plan"} <= names
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "lookup_term", "arguments": {"term": "carrier"}}}
    for _ in range(2):  # a server reused across requests fails on its second
        answer = handler(function_url_event(call), None)
        assert answer["statusCode"] == 200
        result = json.loads(answer["body"])["result"]
        assert not result.get("isError") and "Carrier" in json.dumps(result)


def test_a_refusal_comes_back_as_a_tool_error_not_a_crash():
    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_section", "arguments": {"document": "claims_manual", "number": 99}}}
    answer = handler(function_url_event(call), None)
    result = json.loads(answer["body"])["result"]
    assert answer["statusCode"] == 200 and result["isError"] is True and "no section 99" in json.dumps(result)
