import json, sys, urllib.request, urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler

REAL_SERVER = "http://127.0.0.1:8002"

class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._proxy("GET")
    def do_POST(self):
        self._proxy("POST")
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def _rewrite(self, method, path, body_bytes):
        body = json.loads(body_bytes) if body_bytes else None
        new_method, new_path, new_body = method, path, body

        if path == "/adh/agent/v0/list":
            new_path = "/adh/agent/v0/engine"
        elif path == "/adh/agent/v0/default":
            new_path = "/adh/agent/v0/engine/default"
        elif path == "/adh/agent/v0/settings" and body:
            engine = body.get("engine", "default")
            new_path = f"/adh/agent/v0/engine/{engine}"
            new_method = "GET"
            new_body = None
        elif path == "/adh/agent/v0/conversation_id" and body:
            engine = body.get("engine", "default")
            settings = body.get("settings", {})
            new_path = f"/adh/agent/v0/engine/{engine}"
            new_body = {"data": settings}
        elif path == "/adh/agent/v0/infer":
            new_path = "/adh/agent/v0/engine"
            if body and "settings" in body:
                body["config"] = body.pop("settings")
            new_body = body
        elif path == "/adh/asr/v0/infer":
            new_path = "/adh/asr/v0/engine"
            if body:
                if "settings" in body:
                    body["config"] = body.pop("settings")
                if "format" in body:
                    body["type"] = body.pop("format")
            new_body = body
        elif path == "/adh/tts/v0/infer":
            new_path = "/adh/tts/v0/engine"
            if body and "settings" in body:
                body["config"] = body.pop("settings")
            new_body = body

        new_body_bytes = json.dumps(new_body).encode() if new_body else None
        return new_method, new_path, new_body_bytes

    def _transform_response(self, path, resp_bytes):
        if not resp_bytes:
            return resp_bytes
        try:
            data = json.loads(resp_bytes)
            if path == "/adh/agent/v0/list" and "data" in data:
                data["data"] = [item["name"] for item in data["data"]]
            elif path == "/adh/agent/v0/default" and "data" in data:
                if isinstance(data["data"], dict):
                    data["data"] = data["data"].get("name", "")
            elif path in ("/adh/agent/v0/settings", "/adh/asr/v0/settings", "/adh/tts/v0/settings") and "data" in data:
                # Transform ParamDesc format to old uppercase format
                data["data"] = [
                    {"NAME": item["name"], "DEFAULT": item.get("default", "")}
                    for item in data["data"]
                ]
            return json.dumps(data).encode()
        except:
            return resp_bytes

    def _proxy(self, method):
        path = self.path
        cl = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(cl) if cl > 0 else None

        new_method, new_path, new_body = self._rewrite(method, path, body)

        url = REAL_SERVER + new_path
        req = urllib.request.Request(url, data=new_body, method=new_method)
        req.add_header("Content-Type", "application/json")

        try:
            resp = urllib.request.urlopen(req, timeout=60)
            resp_data = self._transform_response(path, resp.read())
            self.send_response(resp.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(resp_data if resp_data else b"")
        except urllib.error.HTTPError as e:
            err_body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        print(f"[PROXY] {self.path}", flush=True)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    print(f"API Proxy on port {port} -> {REAL_SERVER}")
    HTTPServer(("0.0.0.0", port), ProxyHandler).serve_forever()
