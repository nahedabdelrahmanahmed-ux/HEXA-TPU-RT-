"""
mec/mec_mock_platform.py -- a STRICT local test double of an ETSI MEC 011 platform
(mec_app_support/v1 + mec_service_mgmt/v1). It is NOT the ETSI sandbox and proves nothing about it by
itself; its purpose is to let the client be tested offline against the documented contract:
  * validates paths, methods, Content-Type, JSON bodies, required fields and enum values;
  * replies with the documented status codes (204/201/200/400/404/403/415) and ProblemDetails bodies;
  * records every request (method, path, query, headers, body) for contract assertions;
  * fault injection: HTTP errors, dropped connections, delays, redirects.
Used by tests/test_mec_sandbox_client.py and by committee_demo.py when no real sandbox URL is given.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from mec.mec_sandbox_client import (SERIALIZERS, SERVICE_STATES, SCOPES, TRANSPORT_TYPES, _ID_RE)

PREFIX = "/sbx"          # plays the role of the sandbox name in <host>/<sandbox-name>/mec_app_support/v1


class MockMecPlatform:
    def __init__(self, require_ready: bool = True):
        self.require_ready = require_ready
        self.lock = threading.Lock()
        self.log: List[dict] = []
        self.ready: set = set()
        self.terminated: set = set()
        self.services: Dict[str, dict] = {}      # serInstanceId -> {"app":..., "info":...}
        self._faults: List[dict] = []            # consumed in order, one per request
        self._server: Optional[ThreadingHTTPServer] = None
        self.url = ""

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> "MockMecPlatform":
        plat = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):           # silence
                pass

            def _handle(self):
                plat._dispatch(self)

            do_GET = do_POST = do_PUT = do_DELETE = _handle

        class Quiet(ThreadingHTTPServer):
            def handle_error(self, request, client_address):      # clients abandoning requests is expected
                import sys
                if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
                    super().handle_error(request, client_address)

        self._server = Quiet(("127.0.0.1", 0), H)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}{PREFIX}"
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown(); self._server.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()

    # ---------------------------------------------------------------- fault injection
    def inject(self, kind: str, times: int = 1, status: int = 500, delay_s: float = 0.0,
               process_after_delay: bool = True) -> None:
        """kind: 'status' (reply with `status`), 'drop' (close the socket), 'delay' (sleep first),
        'redirect' (302 to another host), 'garbage' (200 with a non-JSON body)."""
        with self.lock:
            self._faults += [{"kind": kind, "status": status, "delay": delay_s,
                              "process": process_after_delay} for _ in range(times)]

    def requests(self, method: Optional[str] = None, contains: Optional[str] = None) -> List[dict]:
        with self.lock:
            return [r for r in self.log if (method is None or r["method"] == method)
                    and (contains is None or contains in r["path"])]

    # ---------------------------------------------------------------- plumbing
    @staticmethod
    def _send(h: BaseHTTPRequestHandler, status: int, body=None, headers: Optional[dict] = None) -> None:
        payload = b"" if body is None else json.dumps(body).encode()
        h.send_response(status)
        if payload:
            h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(payload)))
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.end_headers()
        h.wfile.write(payload)

    def _problem(self, h, status: int, title: str, detail: str) -> None:
        self._send(h, status, {"type": "about:blank", "title": title, "status": status, "detail": detail})

    def _dispatch(self, h: BaseHTTPRequestHandler) -> None:
        length = int(h.headers.get("Content-Length") or 0)
        raw = h.rfile.read(length) if length else b""
        u = urlparse(h.path)
        entry = {"method": h.command, "path": u.path, "query": parse_qs(u.query), "raw": raw,
                 "headers": {k.lower(): v for k, v in h.headers.items()}, "body": None}
        if raw:
            try:
                entry["body"] = json.loads(raw)
            except ValueError:
                entry["body"] = "<invalid json>"
        with self.lock:
            self.log.append(entry)
            fault = self._faults.pop(0) if self._faults else None
        if fault:
            if fault["kind"] == "drop":
                h.close_connection = True
                h.connection.close()
                return
            if fault["kind"] == "delay":
                time.sleep(fault["delay"])
                if not fault["process"]:
                    return
            elif fault["kind"] == "status":
                return self._problem(h, fault["status"], "injected", "injected fault")
            elif fault["kind"] == "garbage":
                raw_body = b"<html>not json</html>"
                h.send_response(200); h.send_header("Content-Type", "text/html")
                h.send_header("Content-Length", str(len(raw_body))); h.end_headers(); h.wfile.write(raw_body)
                return
            elif fault["kind"] == "redirect":
                return self._send(h, 302, None, {"Location": "http://example.invalid/steal"})
        try:
            self._route(h, entry, u)
        except Exception as e:                         # a bug in the mock must be visible, not hidden
            self._problem(h, 500, "mock-internal-error", repr(e))

    # ---------------------------------------------------------------- MEC 011 routing
    def _route(self, h, e, u) -> None:
        if not u.path.startswith(PREFIX + "/"):
            return self._problem(h, 404, "Not Found", "unknown sandbox")
        p = u.path[len(PREFIX) + 1:].split("/")
        m = h.command
        if e["body"] is not None and "application/json" not in e["headers"].get("content-type", ""):
            return self._problem(h, 415, "Unsupported Media Type", "Content-Type must be application/json")
        if e["body"] == "<invalid json>":
            return self._problem(h, 400, "Bad Request", "body is not valid JSON")

        # ---- mec_app_support/v1
        if p[:2] == ["mec_app_support", "v1"]:
            rest = p[2:]
            if len(rest) == 3 and rest[0] == "applications" and m == "POST":
                app, action = rest[1], rest[2]
                if not _ID_RE.match(app):
                    return self._problem(h, 400, "Bad Request", "invalid appInstanceId")
                b = e["body"]
                if action == "confirm_ready":
                    if not isinstance(b, dict) or b.get("indication") != "READY":
                        return self._problem(h, 400, "Bad Request", "indication must be READY")
                    with self.lock:
                        self.ready.add(app); self.terminated.discard(app)
                    return self._send(h, 204)
                if action == "confirm_termination":
                    if not isinstance(b, dict) or b.get("operationAction") not in ("TERMINATING", "STOPPING"):
                        return self._problem(h, 400, "Bad Request", "operationAction must be TERMINATING|STOPPING")
                    with self.lock:
                        self.ready.discard(app); self.terminated.add(app)
                    return self._send(h, 204)
            if rest == ["timing", "timing_caps"] and m == "GET":
                return self._send(h, 200, {"timeStamp": {"seconds": int(time.time()), "nanoSeconds": 0},
                                           "timeStampAccuracy": "PTP"})
            if rest == ["timing", "current_time"] and m == "GET":
                return self._send(h, 200, {"seconds": int(time.time()), "nanoSeconds": 0,
                                           "timeSourceStatus": "TRACEABLE"})
            return self._problem(h, 404, "Not Found", "unknown mec_app_support resource")

        # ---- mec_service_mgmt/v1
        if p[:2] == ["mec_service_mgmt", "v1"]:
            rest = p[2:]
            if rest == ["transports"] and m == "GET":
                return self._send(h, 200, [{"id": "http-1", "name": "REST", "type": "REST_HTTP", "protocol": "HTTP",
                                            "version": "2.0", "endpoint": {"uri": self.url}, "security": {}}])
            if rest == ["services"] and m == "GET":
                q = e["query"]
                with self.lock:
                    out = [dict(v["info"], serInstanceId=k) for k, v in self.services.items()
                           if all((q.get(key) is None) or v["info"].get(field) == q[key][0] or k == q[key][0]
                                  for key, field in (("ser_name", "serName"), ("ser_instance_id", "serInstanceId")))]
                return self._send(h, 200, out)
            if len(rest) >= 3 and rest[0] == "applications" and rest[2] == "services":
                app = rest[1]
                if not _ID_RE.match(app):
                    return self._problem(h, 400, "Bad Request", "invalid appInstanceId")
                if len(rest) == 3 and m == "POST":
                    if self.require_ready and app not in self.ready:
                        return self._problem(h, 403, "Forbidden", "application has not confirmed READY")
                    err = self._validate_service(e["body"])
                    if err:
                        return self._problem(h, 400, "Bad Request", err)
                    sid = uuid.uuid4().hex
                    info = dict(e["body"], serInstanceId=sid)
                    with self.lock:
                        self.services[sid] = {"app": app, "info": info}
                    return self._send(h, 201, info, {"Location": f"{self.url}/mec_service_mgmt/v1/applications/{app}/services/{sid}"})
                if len(rest) == 4:
                    sid = rest[3]
                    with self.lock:
                        rec = self.services.get(sid)
                    if rec is None or rec["app"] != app:
                        return self._problem(h, 404, "Not Found", "service instance not found for this application")
                    if m == "GET":
                        return self._send(h, 200, rec["info"])
                    if m == "PUT":
                        err = self._validate_service(e["body"])
                        if err:
                            return self._problem(h, 400, "Bad Request", err)
                        if e["body"].get("serInstanceId") != sid:
                            return self._problem(h, 400, "Bad Request", "serInstanceId in body must match the path")
                        with self.lock:
                            self.services[sid]["info"] = dict(e["body"])
                        return self._send(h, 200, e["body"])
                    if m == "DELETE":
                        with self.lock:
                            del self.services[sid]
                        return self._send(h, 204)
            return self._problem(h, 404, "Not Found", "unknown mec_service_mgmt resource")
        return self._problem(h, 404, "Not Found", "unknown API")

    @staticmethod
    def _validate_service(b) -> Optional[str]:
        if not isinstance(b, dict):
            return "body must be a JSON object"
        for k in ("serName", "version", "state", "transportInfo", "serializer"):
            if k not in b:
                return f"missing required attribute {k}"
        if not isinstance(b["serName"], str) or not b["serName"] or len(b["serName"]) > 128:
            return "serName must be a non-empty string"
        if b["state"] not in SERVICE_STATES:
            return "invalid state"
        if b["serializer"] not in SERIALIZERS:
            return "invalid serializer"
        if "scopeOfLocality" in b and b["scopeOfLocality"] not in SCOPES:
            return "invalid scopeOfLocality"
        if "consumedLocalOnly" in b and not isinstance(b["consumedLocalOnly"], bool):
            return "consumedLocalOnly must be boolean"
        t = b["transportInfo"]
        if not isinstance(t, dict):
            return "transportInfo must be an object"
        for k in ("id", "name", "type", "protocol", "version", "endpoint", "security"):
            if k not in t:
                return f"transportInfo missing {k}"
        if t["type"] not in TRANSPORT_TYPES:
            return "invalid transportInfo.type"
        if not isinstance(t["endpoint"], dict) or not (t["endpoint"].get("uri") or t["endpoint"].get("addresses")):
            return "transportInfo.endpoint needs uri or addresses"
        return None
