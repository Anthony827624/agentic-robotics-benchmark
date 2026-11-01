"""Minimal MCP stdio bridge; no shell, arbitrary paths, code execution, or reset."""
import json
import sys
from client import Client

TOOLS = [
    {"name": "robot_observe", "description": "Observe the single forward-facing onboard RGB camera and remaining budgets.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "robot_act", "description": "Apply one bounded action. It consumes simulation budget.",
     "inputSchema": {"type": "object", "properties": {"action": {"type": "array", "minItems": 4, "maxItems": 4,
                     "items": {"type": "number", "minimum": -1, "maximum": 1}}},
                     "required": ["action"], "additionalProperties": False}},
]


def main():
    client = Client()
    for line in sys.stdin:
        req = json.loads(line)
        if "id" not in req:
            continue
        try:
            method = req["method"]
            if method == "initialize":
                result = {"protocolVersion": req.get("params", {}).get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "visual_robotics", "version": "1.0"}}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                name = req["params"]["name"]
                if name not in {t["name"] for t in TOOLS}:
                    raise ValueError("unknown tool")
                obs = client.request(name.removeprefix("robot_"), **req["params"].get("arguments", {}))
                images = obs.pop("images", [])
                result = {"content": [{"type": "text", "text": json.dumps(obs)}] +
                          [{"type": "image", "data": x, "mimeType": "image/png"} for x in images]}
            elif method == "ping":
                result = {}
            else:
                raise ValueError("unsupported method")
            reply = {"jsonrpc": "2.0", "id": req["id"], "result": result}
        except (ValueError, KeyError, OSError) as exc:
            reply = {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32602, "message": str(exc)}}
        print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    main()
