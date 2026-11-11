"""Extracted servers -> authenticated loopback TLS mock; never Perplexity."""
import base64
import argparse
import http.server
import io
import json
import os
from pathlib import Path
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time

import httpx
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_perplexity_adapter.image_resize import image_parts
from scripts.work_budget_fixtures import many_images

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build/qualification-0.1.15"

def run(test_app=None):
    with tempfile.TemporaryDirectory(prefix="resize-tls-") as folder:
        tmp = Path(folder)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", str(tmp / "key.pem"), "-out", str(tmp / "cert.pem"), "-subj", "/CN=api.perplexity.ai", "-addext", "subjectAltName=DNS:api.perplexity.ai"], check=True, capture_output=True)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(tmp / "cert.pem", tmp / "key.pem")
        seen = []
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                line = self.rfile.readline()
                if line != b"CONNECT api.perplexity.ai:443 HTTP/1.1\r\n":
                    raise AssertionError("Unexpected proxy destination")
                while self.rfile.readline() != b"\r\n": pass
                self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n"); self.wfile.flush()
                with context.wrap_socket(self.connection, server_side=True) as connection:
                    reader = connection.makefile("rb")
                    first = reader.readline(); headers = {}
                    while True:
                        line = reader.readline()
                        if line == b"\r\n": break
                        key, value = line.decode().split(":", 1); headers[key.lower()] = value.strip()
                    assert first.startswith(b"POST /v1/responses ")
                    assert headers["authorization"] == "Bearer synthetic-package-key"
                    body = reader.read(int(headers["content-length"])); p = json.loads(body)
                    images = [part["image_url"] for part in image_parts(p)]
                    sizes = []
                    for url in images:
                        with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as im: sizes.append(im.size)
                    seen.append({"bytes": len(body), "sizes": sizes, "stream": bool(p.get("stream"))})
                    obj = {"id": "resp_packaged", "status": "completed", "output": []}
                    stream = bool(p.get("stream"))
                    response = ("data: " + json.dumps({"type": "response.completed", "response": obj}) + "\n\n").encode() if stream else json.dumps(obj).encode()
                    mime = "text/event-stream" if stream else "application/json"
                    connection.sendall(f"HTTP/1.1 200 OK\r\nContent-Type: {mime}\r\nContent-Length: {len(response)}\r\nConnection: close\r\n\r\n".encode() + response)
        proxy = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True); thread.start()
        reports = []
        try:
            url = "data:image/jpeg;base64," + base64.b64encode((OUT / "form-original.jpg").read_bytes()).decode()
            for arch in (("arm64",) if test_app else ("arm64", "x86_64")):
                executable = (test_app if test_app else OUT / "extracted" / arch / "Codex Perplexity Adapter.app") / "Contents/Resources/adapter-server"
                sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
                env = dict(os.environ, PERPLEXITY_API_KEY="synthetic-package-key", HTTPS_PROXY=f"http://127.0.0.1:{proxy.server_address[1]}", NO_PROXY="127.0.0.1,localhost", SSL_CERT_FILE=str(tmp / "cert.pem"))
                command = (["arch", "-x86_64"] if arch == "x86_64" else []) + [str(executable), "--port", str(port), "--resize-images-on-limit", "--log-level", "info"]
                with (OUT / f"package-{arch}.log").open("w") as log:
                    process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
                    try:
                        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=90) as c:
                            for _ in range(200):
                                try:
                                    if c.get("/health").status_code == 200: break
                                except httpx.HTTPError: pass
                                if process.poll() is not None: raise RuntimeError("packaged server exited")
                                time.sleep(.1)
                            assert c.get("/v1/adapter/status").status_code == 401
                            headers = {"Authorization": "Bearer local-adapter-token"}
                            start = len(seen)
                            for stream in (False, True):
                                p = many_images(); p["stream"] = stream
                                r = c.post("/v1/responses", headers=headers, json=p); assert r.status_code == 200, r.text
                                assert "completed" in r.text
                                before = len(seen)
                                r = c.post("/v1/responses", headers=headers, json={"model": "gpt-6.1-sol", "stream": stream, "input": "x" * 19_000_000})
                                assert "context_length_exceeded" in r.text and len(seen) == before
                            stats = c.get("/v1/adapter/status", headers=headers).json()
                            assert stats["image_resize_requests"] == 2 and stats["size_failures"] == 2
                            records = seen[start:]
                            assert len(records) == 2 and all(r["bytes"] <= 18_000_000 for r in records)
                            reports.append({"arch": arch, "mock_tls": True, "requests": records, "stats": stats})
                    finally: process.terminate(); process.wait(timeout=10)
        finally: proxy.shutdown(); proxy.server_close()
    (OUT / "packaged-resize.json").write_text(json.dumps(reports, indent=2) + "\n")
    print(json.dumps([{"arch": r["arch"], "bytes": [p["bytes"] for p in r["requests"]], "passed": True} for r in reports]))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument('--app', type=Path)
    run(parser.parse_args().app)
