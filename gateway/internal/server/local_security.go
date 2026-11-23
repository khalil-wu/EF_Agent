package server

import (
	"net"
	"net/http"
	"net/url"
	"strings"
)

// LocalOnly guards the loopback app against browser cross-origin calls and DNS
// rebinding: only localhost/loopback Host headers pass, Origin must match the
// request scheme+authority exactly, and cross-site fetches are refused.
// It is not authentication: a same-user process is out of scope.
func LocalOnly(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("X-Frame-Options", "DENY")
		host, _, ok := splitHost(r.Host)
		if !ok || !isLocalHostname(host) || !singleHeaders(r) || !originAllowed(r) ||
			r.Header.Get("Sec-Fetch-Site") == "cross-site" {
			writeJSON(w, 403, map[string]string{"error": "local host only"})
			return
		}
		next.ServeHTTP(w, r)
	})
}

// singleHeaders rejects requests carrying a repeated security-relevant header;
// net/http parses Host itself, so it is checked against r.Header too.
func singleHeaders(r *http.Request) bool {
	for _, name := range []string{"Host", "Origin", "Sec-Fetch-Site"} {
		if len(r.Header.Values(name)) > 1 {
			return false
		}
	}
	return true
}

func isLocalHostname(host string) bool {
	if normalizeHostname(host) == "localhost" {
		return true
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback()
	}
	return false
}

// normalizeHostname lowercases and removes one optional trailing dot.
func normalizeHostname(host string) string {
	return strings.ToLower(strings.TrimSuffix(host, "."))
}

// splitHost parses a Host header value into hostname and port; port is 0 when
// absent. Anything malformed is rejected.
func splitHost(h string) (host string, port int, ok bool) {
	if h == "" {
		return "", 0, false
	}
	if strings.HasPrefix(h, "[") {
		end := strings.Index(h, "]")
		if end < 0 {
			return "", 0, false
		}
		rest := h[end+1:]
		if rest == "" {
			return h[1:end], 0, true
		}
		n, valid := validPort(rest)
		return h[1:end], n, valid
	}
	if i := strings.Index(h, ":"); i >= 0 {
		if i != strings.LastIndex(h, ":") {
			return "", 0, false // unbracketed IPv6
		}
		n, valid := validPort(h[i:])
		return h[:i], n, valid
	}
	return h, 0, true
}

// validPort parses ":<digits>" into a port; ASCII digits only, 1..65535.
func validPort(s string) (int, bool) {
	if len(s) < 2 || len(s) > 6 || s[0] != ':' {
		return 0, false
	}
	n := 0
	for _, c := range s[1:] {
		if c < '0' || c > '9' {
			return 0, false
		}
		n = n*10 + int(c-'0')
	}
	if n < 1 || n > 65535 {
		return 0, false
	}
	return n, true
}

func originAllowed(r *http.Request) bool {
	o := r.Header.Get("Origin")
	if o == "" {
		return true
	}
	if o == "null" || strings.ContainsAny(o, " \t,") {
		return false // opaque origin and multiple origins
	}
	u, err := url.Parse(o)
	if err != nil || u.User != nil || u.Opaque != "" || u.Path != "" || u.RawQuery != "" || u.Fragment != "" ||
		strings.HasSuffix(u.Host, ":") {
		return false
	}
	scheme := "http"
	if r.TLS != nil {
		scheme = "https"
	}
	if u.Scheme != scheme {
		return false
	}
	oPort := 0
	if p := u.Port(); p != "" {
		var valid bool
		if oPort, valid = validPort(":" + p); !valid {
			return false
		}
	}
	reqHost, reqPort, ok := splitHost(r.Host)
	if !ok || normalizeHostname(u.Hostname()) != normalizeHostname(reqHost) {
		return false
	}
	return effectivePort(oPort, scheme) == effectivePort(reqPort, scheme)
}

func effectivePort(port int, scheme string) int {
	if port != 0 {
		return port
	}
	if scheme == "https" {
		return 443
	}
	return 80
}
