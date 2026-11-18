"""
mec/mec_sandbox_client.py -- client for the ETSI GS MEC 011 application-enablement (Mp1) REST API:
  * MEC application support : POST /mec_app_support/v1/applications/{appInstanceId}/confirm_ready
                              POST /mec_app_support/v1/applications/{appInstanceId}/confirm_termination
                              GET  /mec_app_support/v1/timing/{timing_caps|current_time}
  * MEC service management  : POST   /mec_service_mgmt/v1/applications/{appInstanceId}/services
                              GET    /mec_service_mgmt/v1/services[?ser_name=..&ser_instance_id=..&ser_category_id=..]
                              GET|PUT|DELETE /mec_service_mgmt/v1/applications/{appInstanceId}/services/{serInstanceId}
                              GET    /mec_service_mgmt/v1/transports

Designed for the ETSI MEC Sandbox, where an *external* MEC application running on your machine talks to
`<sandbox-url>/<sandbox-name>/mec_app_support/v1` and `.../mec_service_mgmt/v1`. It has been verified
against a strict local MEC 011 test double (mec/mec_mock_platform.py) -- NOT yet against the live sandbox,
which was not reachable from the development environment. Paths and ServiceInfo fields follow the ETSI
MEC 011 OpenAPI files (MecAppSupportApi / MecServiceMgmtApi).

Safety/robustness properties (all covered by tests/test_mec_sandbox_client.py):
  * arguments are validated BEFORE any network I/O (ids cannot escape the URL path);
  * only http(s) URLs; TLS verification on by default; redirects are never followed (the Authorization
    header could otherwise leak to another host);
  * bounded timeouts; limited retries with exponential back-off for connection errors and 502/503/504;
  * non-idempotent POST (service registration) is de-duplicated after an ambiguous failure;
  * bearer token is never included in repr()/str()/exception text;
  * MecAppSession always deregisters services and confirms termination, even when the body raises.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

APP_SUPPORT = "mec_app_support/v1"
SERVICE_MGMT = "mec_service_mgmt/v1"
_ID_RE = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")
SERVICE_STATES = {"ACTIVE", "INACTIVE", "SUSPENDED"}
SERIALIZERS = {"JSON", "XML", "PROTOBUF3"}
SCOPES = {"NFVI_NODE", "ZONE", "ZONE_GROUP", "NFVI_POP", "MEC_HOST", "MEC_SYSTEM"}
TRANSPORT_TYPES = {"REST_HTTP", "MB_TOPIC_BASED", "MB_ROUTING", "MB_PUBSUB", "RPC", "RPC_STREAMING", "WEBSOCKET"}


class MecSandboxError(Exception):
    """The platform answered with an error (or an unusable answer)."""

    def __init__(self, message: str, status: Optional[int] = None, problem: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.problem = problem or {}


class MecSandboxUnavailable(MecSandboxError):
    """No usable answer: connection refused/reset, DNS failure, or timeout (after retries)."""


def _check_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ValueError(f"invalid {what} {value!r}: must match {_ID_RE.pattern}")
    return value


@dataclass
class ServiceInfo:
    """MEC 011 ServiceInfo (the subset this project needs)."""
    ser_name: str
    version: str
    transport_uri: str
    state: str = "ACTIVE"
    serializer: str = "JSON"
    scope_of_locality: str = "MEC_SYSTEM"
    consumed_local_only: bool = False
    transport_type: str = "REST_HTTP"
    ser_instance_id: Optional[str] = None

    def validate(self) -> None:
        if not self.ser_name or not isinstance(self.ser_name, str) or len(self.ser_name) > 128:
            raise ValueError("ser_name must be a non-empty string (<= 128 chars)")
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("version must be a non-empty string")
        if self.state not in SERVICE_STATES:
            raise ValueError(f"state must be one of {sorted(SERVICE_STATES)}")
        if self.serializer not in SERIALIZERS:
            raise ValueError(f"serializer must be one of {sorted(SERIALIZERS)}")
        if self.scope_of_locality not in SCOPES:
            raise ValueError(f"scope_of_locality must be one of {sorted(SCOPES)}")
        if self.transport_type not in TRANSPORT_TYPES:
            raise ValueError(f"transport_type must be one of {sorted(TRANSPORT_TYPES)}")
        u = urllib.parse.urlparse(self.transport_uri or "")
        if u.scheme not in ("http", "https") or not u.netloc:
            raise ValueError("transport_uri must be an absolute http(s) URL")

    def to_json(self) -> dict:
        self.validate()
        return {
            "serName": self.ser_name,
            "version": self.version,
            "state": self.state,
            "transportInfo": {
                "id": "hexa-transport-1", "name": "REST", "type": self.transport_type,
                "protocol": "HTTP", "version": "2.0",
                "endpoint": {"uri": self.transport_uri}, "security": {},
            },
            "serializer": self.serializer,
            "scopeOfLocality": self.scope_of_locality,
            "consumedLocalOnly": self.consumed_local_only,
        }

    @classmethod
    def from_json(cls, d: dict) -> "ServiceInfo":
        try:
            t = d["transportInfo"]
            return cls(ser_name=d["serName"], version=d["version"], state=d["state"],
                       transport_uri=t["endpoint"]["uri"], serializer=d["serializer"],
                       scope_of_locality=d.get("scopeOfLocality", "MEC_SYSTEM"),
                       consumed_local_only=bool(d.get("consumedLocalOnly", False)),
                       transport_type=t["type"], ser_instance_id=d.get("serInstanceId"))
        except (KeyError, TypeError) as e:
            raise MecSandboxError(f"malformed ServiceInfo from platform: {e!r}") from e


class MecSandboxClient:
    def __init__(self, base_url: str, auth_token: Optional[str] = None, timeout=(3.05, 10.0),
                 retries: int = 2, backoff_s: float = 0.2, verify_tls: bool = True,
                 session: Optional[requests.Session] = None):
        u = urllib.parse.urlparse(base_url or "")
        if u.scheme not in ("http", "https") or not u.netloc:
            raise ValueError("base_url must be an absolute http(s) URL")
        if u.query or u.fragment:
            raise ValueError("base_url must not contain a query or fragment")
        if retries < 0 or retries > 10:
            raise ValueError("retries must be in 0..10")
        self.base_url = base_url.rstrip("/")
        self._token = auth_token
        self.timeout, self.retries, self.backoff_s, self.verify_tls = timeout, retries, backoff_s, verify_tls
        self._s = session or requests.Session()

    def __repr__(self):                       # never leak the token
        return f"MecSandboxClient(base_url={self.base_url!r}, auth={'yes' if self._token else 'no'})"

    # ------------------------------------------------------------------ transport
    def _request(self, method: str, path: str, body: Any = None, params: Optional[dict] = None,
                 ok=(200,), retry: bool = True) -> requests.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        attempts = 1 + (self.retries if retry else 0)
        last: Optional[Exception] = None
        for i in range(attempts):
            try:
                r = self._s.request(method, url, params=params, headers=headers, timeout=self.timeout,
                                    data=None if body is None else json.dumps(body),
                                    verify=self.verify_tls, allow_redirects=False)
            except (requests.ConnectionError, requests.Timeout) as e:
                last = e
            else:
                if r.status_code in (502, 503, 504) and i < attempts - 1:
                    last = MecSandboxError(f"HTTP {r.status_code}", r.status_code)
                else:
                    return self._check(r, ok)
            if i < attempts - 1:
                time.sleep(self.backoff_s * (2 ** i))
        if isinstance(last, MecSandboxError):
            raise last
        raise MecSandboxUnavailable(f"{method} {path}: platform unreachable ({type(last).__name__})") from last

    @staticmethod
    def _check(r: requests.Response, ok) -> requests.Response:
        if r.status_code in ok:
            return r
        problem = {}
        try:
            j = r.json()
            problem = j if isinstance(j, dict) else {}
        except ValueError:
            pass
        detail = problem.get("detail") or problem.get("title") or r.reason or ""
        raise MecSandboxError(f"HTTP {r.status_code}: {str(detail)[:200]}", r.status_code, problem)

    @staticmethod
    def _json(r: requests.Response):
        try:
            return r.json()
        except ValueError as e:
            raise MecSandboxError("platform returned a non-JSON body", r.status_code) from e

    # ------------------------------------------------------------------ MEC application support
    def confirm_ready(self, app_instance_id: str) -> None:
        _check_id(app_instance_id, "app_instance_id")
        self._request("POST", f"{APP_SUPPORT}/applications/{app_instance_id}/confirm_ready",
                      body={"indication": "READY"}, ok=(204,))

    def confirm_termination(self, app_instance_id: str, operation_action: str = "TERMINATING") -> None:
        _check_id(app_instance_id, "app_instance_id")
        if operation_action not in ("TERMINATING", "STOPPING"):
            raise ValueError("operation_action must be TERMINATING or STOPPING")
        self._request("POST", f"{APP_SUPPORT}/applications/{app_instance_id}/confirm_termination",
                      body={"operationAction": operation_action}, ok=(204,))

    def timing_caps(self) -> dict:
        return self._json(self._request("GET", f"{APP_SUPPORT}/timing/timing_caps"))

    def current_time(self) -> dict:
        return self._json(self._request("GET", f"{APP_SUPPORT}/timing/current_time"))

    # ------------------------------------------------------------------ MEC service management
    def register_service(self, app_instance_id: str, info: ServiceInfo) -> ServiceInfo:
        """POST once (non-idempotent: no blind retry); after an ambiguous failure see ensure_service()."""
        _check_id(app_instance_id, "app_instance_id")
        r = self._request("POST", f"{SERVICE_MGMT}/applications/{app_instance_id}/services",
                          body=info.to_json(), ok=(201,), retry=False)
        out = ServiceInfo.from_json(self._json(r))
        if not out.ser_instance_id:
            raise MecSandboxError("platform did not return serInstanceId", r.status_code)
        return out

    def ensure_service(self, app_instance_id: str, info: ServiceInfo) -> ServiceInfo:
        """Idempotent registration: if the same (app, serName) already exists reuse it, else register.
        Safe to call again after a timeout whose outcome is unknown."""
        for s in self.discover_services(ser_name=info.ser_name):
            if s.ser_name == info.ser_name and s.ser_instance_id and self._owned_by(app_instance_id, s):
                return s
        return self.register_service(app_instance_id, info)

    def _owned_by(self, app_instance_id: str, s: ServiceInfo) -> bool:
        try:
            self.get_service(app_instance_id, s.ser_instance_id)
            return True
        except MecSandboxError as e:
            if e.status in (403, 404):
                return False
            raise

    def discover_services(self, ser_name: Optional[str] = None, ser_instance_id: Optional[str] = None,
                          ser_category_id: Optional[str] = None) -> List[ServiceInfo]:
        params = {k: v for k, v in (("ser_name", ser_name), ("ser_instance_id", ser_instance_id),
                                    ("ser_category_id", ser_category_id)) if v is not None}
        data = self._json(self._request("GET", f"{SERVICE_MGMT}/services", params=params))
        if not isinstance(data, list):
            raise MecSandboxError("GET /services did not return a JSON array", 200)
        return [ServiceInfo.from_json(d) for d in data]

    def get_service(self, app_instance_id: str, ser_instance_id: str) -> ServiceInfo:
        _check_id(app_instance_id, "app_instance_id"); _check_id(ser_instance_id, "ser_instance_id")
        r = self._request("GET", f"{SERVICE_MGMT}/applications/{app_instance_id}/services/{ser_instance_id}")
        return ServiceInfo.from_json(self._json(r))

    def update_service(self, app_instance_id: str, info: ServiceInfo) -> ServiceInfo:
        _check_id(app_instance_id, "app_instance_id")
        if not info.ser_instance_id:
            raise ValueError("update_service needs ser_instance_id")
        _check_id(info.ser_instance_id, "ser_instance_id")
        body = info.to_json(); body["serInstanceId"] = info.ser_instance_id
        r = self._request("PUT", f"{SERVICE_MGMT}/applications/{app_instance_id}/services/{info.ser_instance_id}",
                          body=body)
        return ServiceInfo.from_json(self._json(r))

    def deregister_service(self, app_instance_id: str, ser_instance_id: str, ignore_missing: bool = False) -> None:
        _check_id(app_instance_id, "app_instance_id"); _check_id(ser_instance_id, "ser_instance_id")
        try:
            self._request("DELETE", f"{SERVICE_MGMT}/applications/{app_instance_id}/services/{ser_instance_id}",
                          ok=(204,))
        except MecSandboxError as e:
            if not (ignore_missing and e.status == 404):
                raise

    def transports(self) -> list:
        data = self._json(self._request("GET", f"{SERVICE_MGMT}/transports"))
        if not isinstance(data, list):
            raise MecSandboxError("GET /transports did not return a JSON array", 200)
        return data


class MecAppSession:
    """Context manager = the lifecycle of one MEC application instance:
         enter : confirm_ready, then register the declared services
         exit  : deregister every registered service, then confirm_termination (even on exceptions).
    """

    def __init__(self, client: MecSandboxClient, app_instance_id: str, services: Optional[List[ServiceInfo]] = None):
        _check_id(app_instance_id, "app_instance_id")
        for s in services or []:
            s.validate()
        self.client, self.app_instance_id = client, app_instance_id
        self.declared = list(services or [])
        self.registered: List[ServiceInfo] = []
        self.cleanup_errors: List[str] = []
        self._terminated = False

    def __enter__(self) -> "MecAppSession":
        self.client.confirm_ready(self.app_instance_id)
        try:
            for s in self.declared:
                self.registered.append(self.client.ensure_service(self.app_instance_id, s))
        except Exception:
            self.close()
            raise
        return self

    def close(self) -> None:
        if self._terminated:
            return
        self._terminated = True
        for s in reversed(self.registered):
            try:
                self.client.deregister_service(self.app_instance_id, s.ser_instance_id, ignore_missing=True)
            except MecSandboxError as e:
                self.cleanup_errors.append(f"deregister {s.ser_name}: {e}")
        try:
            self.client.confirm_termination(self.app_instance_id)
        except MecSandboxError as e:
            self.cleanup_errors.append(f"confirm_termination: {e}")

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


def hexa_services(base_http_url: str = "http://127.0.0.1:9000") -> List[ServiceInfo]:
    """The two MEC services this project offers (one per application), as MEC 011 ServiceInfo."""
    return [ServiceInfo("hexa-aviation-cognitive", "1.0.0", f"{base_http_url}/aviation/v1"),
            ServiceInfo("hexa-water-cognitive", "1.0.0", f"{base_http_url}/water/v1")]
