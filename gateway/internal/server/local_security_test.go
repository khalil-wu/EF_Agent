package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func localGuard(t *testing.T) (http.Handler, *int) {
	calls := new(int)
	return LocalOnly(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		*calls++
		w.WriteHeader(200)
		_, _ = w.Write([]byte("ok"))
	})), calls
}

func localReq(t *testing.T, host string, hdr map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	h, _ := localGuard(t)
	r := httptest.NewRequest("GET", "/sessions", nil)
	r.Host = host
	for k, v := range hdr {
		r.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func securityHeadersPresent(w *httptest.ResponseRecorder) bool {
	return w.Header().Get("X-Content-Type-Options") == "nosniff" &&
		w.Header().Get("Referrer-Policy") == "no-referrer" &&
		w.Header().Get("X-Frame-Options") == "DENY"
}

func TestLocalOnlyAcceptsLoopbackHostsAndSetsSecurityHeaders(t *testing.T) {
	for _, host := range []string{"127.0.0.1:8080", "localhost", "LOCALHOST.:8080", "[::1]:8080", "127.0.0.1"} {
		if w := localReq(t, host, nil); w.Code != 200 {
			t.Fatalf("host %q: got %d want 200", host, w.Code)
		} else if !securityHeadersPresent(w) {
			t.Fatalf("host %q: missing security headers", host)
		}
	}
}

func TestLocalOnlyRejectsForeignAndMalformedHostsBeforeHandler(t *testing.T) {
	h, calls := localGuard(t)
	for _, host := range []string{"evil.example", "127.0.0.1.evil.example", "10.0.0.5", "127.0.0.1:abc",
		"127.0.0.1:0", "127.0.0.1:65536", "127.0.0.1:99999", "localhost:８０８０",
		"127.0.0.1:", "::1", "[]:80", "localhost:99:9", ""} {
		r := httptest.NewRequest("GET", "/", nil)
		r.Host = host
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != 403 || !strings.Contains(w.Body.String(), `"error"`) {
			t.Fatalf("host %q: got %d want JSON 403", host, w.Code)
		}
		if !securityHeadersPresent(w) {
			t.Fatalf("host %q: rejected response missing security headers", host)
		}
	}
	if *calls != 0 {
		t.Fatalf("handler ran %d times for rejected hosts", *calls)
	}
}

func TestLocalOnlyOriginPolicy(t *testing.T) {
	cases := []struct {
		host, origin string
		want         int
	}{
		{"127.0.0.1:8080", "http://127.0.0.1:8080", 200},
		{"localhost:8080", "http://localhost:8080", 200},
		{"localhost.:8080", "http://localhost:8080", 200}, // one trailing dot normalizes
		{"localhost:8080", "http://localhost:08080", 200}, // numeric port normalization
		{"[::1]:8080", "http://[::1]:8080", 200},
		{"127.0.0.1:8080", "https://evil.example", 403},
		{"127.0.0.1:8080", "null", 403},
		{"localhost:8080", "http://127.0.0.1:8080", 403},  // distinct hostnames not equated
		{"127.0.0.1:8080", "http://127.0.0.1:9090", 403},  // same host, wrong port
		{"127.0.0.1:8080", "http://127.0.0.1:99999", 403}, // invalid origin port
		{"127.0.0.1:8080", "http://127.0.0.1:8080 ", 403}, // trailing garbage
		{"127.0.0.1:8080", "http://user@127.0.0.1:8080", 403},
		{"127.0.0.1:8080", "http://127.0.0.1:8080/evil", 403},
		{"localhost", "http://localhost:", 403}, // explicit empty origin port, otherwise same default port
		{"127.0.0.1:8080", "http://127.0.0.1 http://evil.example", 403},
		{"127.0.0.1:8080", "javascript:alert(1)", 403},
	}
	h, calls := localGuard(t)
	for _, c := range cases {
		r := httptest.NewRequest("GET", "/", nil)
		r.Host = c.host
		r.Header.Set("Origin", c.origin)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != c.want {
			t.Fatalf("origin %q host %q: got %d want %d", c.origin, c.host, w.Code, c.want)
		}
	}
	if *calls != 5 {
		t.Fatalf("handler ran %d times, want 5 (the passing origins)", *calls)
	}
}

func TestLocalOnlyRejectsDuplicateSecurityHeaders(t *testing.T) {
	h, calls := localGuard(t)
	r := httptest.NewRequest("GET", "/", nil)
	r.Host = "127.0.0.1:8080"
	r.Header["Origin"] = []string{"http://127.0.0.1:8080", "http://127.0.0.1:8080"}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 403 {
		t.Fatalf("duplicate Origin: got %d want 403", w.Code)
	}
	r = httptest.NewRequest("GET", "/", nil)
	r.Host = "127.0.0.1:8080"
	r.Header["Sec-Fetch-Site"] = []string{"same-origin", "same-origin"}
	w = httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 403 {
		t.Fatalf("duplicate Sec-Fetch-Site: got %d want 403", w.Code)
	}
	if *calls != 0 {
		t.Fatalf("handler ran %d times for duplicate headers", *calls)
	}
}

func TestLocalOnlyRejectsCrossSiteFetch(t *testing.T) {
	h, calls := localGuard(t)
	r := httptest.NewRequest("GET", "/", nil)
	r.Host = "127.0.0.1:8080"
	r.Header.Set("Sec-Fetch-Site", "cross-site")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 403 || !securityHeadersPresent(w) {
		t.Fatalf("Sec-Fetch-Site cross-site: got %d want 403 with headers", w.Code)
	}
	for _, site := range []string{"same-origin", "same-site", "none"} {
		r := httptest.NewRequest("GET", "/", nil)
		r.Host = "127.0.0.1:8080"
		r.Header.Set("Sec-Fetch-Site", site)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != 200 {
			t.Fatalf("Sec-Fetch-Site %s: got %d want 200", site, w.Code)
		}
	}
	if *calls != 3 {
		t.Fatalf("handler ran %d times, want 3", *calls)
	}
}
