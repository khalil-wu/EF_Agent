"""Worker local-only guard: mirrors gateway/internal/server/local_security.go."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from eegagent.worker.local_security import LocalOnlyMiddleware


@pytest.fixture()
def client():
    inner = FastAPI()
    calls = {"n": 0}

    @inner.get("/health")
    def health():
        calls["n"] += 1
        return {"ok": True}

    @inner.post("/write")
    def write():
        calls["n"] += 1
        return {"written": True}

    inner.add_middleware(LocalOnlyMiddleware)
    return TestClient(inner, base_url="http://127.0.0.1"), calls


def test_local_requests_pass_and_security_headers_set(client):
    c, _ = client
    r = c.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert (r.headers["x-content-type-options"], r.headers["referrer-policy"],
            r.headers["x-frame-options"]) == ("nosniff", "no-referrer", "DENY")
    assert c.post("/write").status_code == 200


@pytest.mark.parametrize("host", ["127.0.0.1:8100", "localhost:8100", "localhost.", "[::1]:8100"])
def test_valid_local_hosts_accepted(client, host):
    c, calls = client
    assert c.get("/health", headers={"Host": host}).status_code == 200
    assert calls["n"] == 1


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "10.0.0.5:8100",
                                  "127.0.0.1:abc", "127.0.0.1:0", "127.0.0.1:65536",
                                  "127.0.0.1:99999", "127.0.0.1:", "::1", "localhost:9:9"])
def test_foreign_and_malformed_hosts_rejected_before_handler(client, host):
    c, calls = client
    r = c.post("/write", headers={"Host": host})
    assert r.status_code == 403 and r.json() == {"error": "local host only"}
    assert (r.headers["x-content-type-options"], r.headers["x-frame-options"]) == ("nosniff", "DENY")
    assert calls["n"] == 0  # no side effects


@pytest.mark.parametrize("origin,expected", [
    ("http://127.0.0.1", 200),
    ("http://127.0.0.1:8100", 403),          # same hostname, different port
    ("https://evil.example", 403),
    ("null", 403),
    ("http://user@127.0.0.1", 403),
    ("http://127.0.0.1/path", 403),
    ("http://127.0.0.1 http://evil.example", 403),
    ("http://127.0.0.1:0", 403),             # port 0 is invalid, not "default"
    ("http://127.0.0.1:99999", 403),
    ("http://127.0.0.1:", 403),
])
def test_origin_policy(client, origin, expected):
    c, calls = client
    before = calls["n"]
    assert c.post("/write", headers={"Origin": origin}).status_code == expected
    assert calls["n"] == before + (expected == 200)


@pytest.mark.parametrize("origin", ["http://localhost:08100", "http://localhost.:8100"])
def test_normalized_same_origin_accepted(origin):
    inner = FastAPI()

    @inner.get("/health")
    def health():
        return {"ok": True}

    inner.add_middleware(LocalOnlyMiddleware)
    c = TestClient(inner, base_url="http://localhost:8100")
    assert c.get("/health", headers={"Origin": origin}).status_code == 200


def test_distinct_local_hostnames_not_equated(client):
    c, calls = client
    assert c.post("/write", headers={"Host": "localhost:8100",
                                     "Origin": "http://127.0.0.1:8100"}).status_code == 403
    assert calls["n"] == 0


def test_duplicate_security_headers_rejected(client):
    c, calls = client
    r = c.post("/write", headers=[("Origin", "http://127.0.0.1"), ("Origin", "http://127.0.0.1")])
    assert r.status_code == 403 and calls["n"] == 0
    r = c.post("/write", headers=[("Sec-Fetch-Site", "same-origin"), ("Sec-Fetch-Site", "same-origin")])
    assert r.status_code == 403 and calls["n"] == 0
    r = c.post("/write", headers=[("Origin", "http://127.0.0.1"), ("ORIGIN", "http://127.0.0.1")])
    assert r.status_code == 403 and calls["n"] == 0


def test_cross_site_fetch_rejected_and_other_modes_allowed(client):
    c, calls = client
    assert c.post("/write", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert calls["n"] == 0
    for mode in ("same-origin", "same-site", "none"):
        assert c.get("/health", headers={"Sec-Fetch-Site": mode}).status_code == 200


def test_port_parser_rejects_non_ascii_and_out_of_range():
    from eegagent.worker.local_security import _port_num, _split_host
    for suffix in (":８０８０", ":٨٠", ":0", ":65536", ":99999", ":", ":", ":abc", ":-1",
                   ":" + "8" * 5000):  # exceeds int() conversion limit; must return None not raise
        assert _port_num(suffix) is None
    assert _port_num(":08080") == 8080 and _split_host("127.0.0.1:８０８０") is None


def test_middleware_does_not_whitelist_testserver(client):
    c, calls = client
    r = c.get("/health", headers={"Host": "testserver"})
    assert r.status_code == 403 and calls["n"] == 0
