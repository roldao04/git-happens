#!/usr/bin/env python3
"""OAuth helper for the Connected Home MCP server.

Claude Code's built-in OAuth always sends redirect_uri=http://localhost:<port>/callback,
which the hackathon Keycloak rejects (403). Keycloak only accepts
http://mcp-redirect.futurelabsinnovations.com:<port>/callback (that hostname resolves
to 127.0.0.1), so this script runs the login itself.

  python3 tools/connected_home_auth.py login     # one-time browser sign-in
  python3 tools/connected_home_auth.py headers   # used by .mcp.json headersHelper
"""
import base64, hashlib, http.server, json, os, secrets, sys, time, urllib.parse, urllib.request, webbrowser

REALM = "https://cognitive-home.futurelabsinnovations.com/realms/smarthome-prd-beta-app/protocol/openid-connect"
CLIENT_ID = "mcp-client"
RESOURCE = "https://cognitive-home.futurelabsinnovations.com/"
PORT = int(os.environ.get("CGH_CALLBACK_PORT", "5435"))
REDIRECT = f"http://mcp-redirect.futurelabsinnovations.com:{PORT}/callback"
TOKEN_FILE = os.path.expanduser("~/.config/connected-home/tokens.json")


def post_token(data):
    req = urllib.request.Request(f"{REALM}/token", data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": "connected-home-mcp-auth/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        tok = json.load(r)
    tok["obtained_at"] = int(time.time())
    os.makedirs(os.path.dirname(TOKEN_FILE), mode=0o700, exist_ok=True)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(tok, f)
    return tok


def login():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    url = f"{REALM}/auth?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "scope": "openid offline_access", "resource": RESOURCE,
    })
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if not self.path.startswith("/callback"):
                self.send_response(404); self.end_headers(); return
            if q.get("state", [""])[0] != state:
                result["error"] = "state mismatch"
            elif "code" in q:
                result["code"] = q["code"][0]
            else:
                result["error"] = q.get("error_description", q.get("error", ["unknown"]))[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Signed in. You can close this tab." if "code" in result else f"Login failed: {result.get('error')}"
            self.wfile.write(f"<h2>{msg}</h2>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    print("Open this URL to sign in:\n\n" + url + "\n", flush=True)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    while not result:
        srv.handle_request()
    srv.server_close()
    if "error" in result:
        sys.exit(f"Login failed: {result['error']}")
    tok = post_token({"grant_type": "authorization_code", "client_id": CLIENT_ID, "code": result["code"],
                      "redirect_uri": REDIRECT, "code_verifier": verifier})
    print(f"Login OK. Tokens saved to {TOKEN_FILE} (refresh token: {'yes' if tok.get('refresh_token') else 'no'})")


def headers():
    try:
        with open(TOKEN_FILE) as f:
            tok = json.load(f)
    except FileNotFoundError:
        sys.exit("Not logged in: run python3 tools/connected_home_auth.py login")
    if time.time() > tok["obtained_at"] + tok.get("expires_in", 300) - 60:
        if not tok.get("refresh_token"):
            sys.exit("Token expired: run python3 tools/connected_home_auth.py login")
        old_refresh = tok["refresh_token"]
        tok = post_token({"grant_type": "refresh_token", "client_id": CLIENT_ID,
                          "refresh_token": old_refresh})
        if not tok.get("refresh_token"):  # keep the old one if the server didn't rotate it
            tok["refresh_token"] = old_refresh
            with open(TOKEN_FILE, "w") as f:
                json.dump(tok, f)
    print(json.dumps({"Authorization": f"Bearer {tok['access_token']}"}))


if __name__ == "__main__":
    {"login": login, "headers": headers}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
