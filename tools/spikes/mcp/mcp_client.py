"""Minimal MCP streamable-HTTP client for the BeamNG spike. Saves raw responses to OUT."""
import base64, json, os, sys, time
import requests

URL = "http://127.0.0.1:29292/mcp"
OUT = r"H:\BelfastData\spikes\mcp"
_id = 0


def rpc(method, params=None, notify=False, save=None):
    global _id
    body = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    if not notify:
        _id += 1
        body["id"] = _id
    t = time.time()
    r = requests.post(URL, json=body, timeout=60,
                      headers={"Accept": "application/json, text/event-stream"})
    dt = time.time() - t
    if save:
        with open(os.path.join(OUT, save), "w", encoding="utf-8") as f:
            f.write(f"HTTP {r.status_code}\n{dict(r.headers)}\n\n{r.text}")
    return r, dt


def call(name, args=None, save=None):
    r, dt = rpc("tools/call", {"name": name, "arguments": args or {}}, save=save)
    return r.json()["result"], dt


def wait_ready(timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            rpc("ping")
            return time.time() - t0
        except requests.RequestException:
            time.sleep(1)
    raise TimeoutError


if __name__ == "__main__":
    print("ready after", wait_ready(), "s of polling")
    r, dt = rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                               "clientInfo": {"name": "spike", "version": "0"}}, save="initialize.txt")
    print(r.status_code, dict(r.headers), r.text)
    r, _ = rpc("notifications/initialized", notify=True, save="initialized.txt")
    print("initialized ->", r.status_code, repr(r.text))
    r, _ = rpc("tools/list", save="tools_list.txt")
    tools = r.json()["result"]["tools"]
    json.dump(tools, open(os.path.join(OUT, "tools_list.json"), "w"), indent=1)
    print(len(tools), "tools")
    res, dt = call("get_status", save="get_status.txt")
    print("get_status", dt, json.dumps(res)[:3000])
    res, dt = call("screenshot_image", save="screenshot_image.txt")
    c = res["content"][0]
    print("screenshot", dt, c["type"], c.get("mimeType"), len(c.get("data", "")))
    if c["type"] == "image":
        ext = c["mimeType"].split("/")[-1]
        open(os.path.join(OUT, f"screenshot.{ext}"), "wb").write(base64.b64decode(c["data"]))
