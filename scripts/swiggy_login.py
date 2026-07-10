#!/usr/bin/env python3
"""Swiggy MCP login — OAuth 2.1 PKCE, writes ~/.langrobo/mcp_tokens.json.

Run this wherever a browser can reach the machine's localhost:
  on a desktop:  python3 scripts/swiggy_login.py
  headless Pi:   ssh -L 8976:localhost:8976 <robot>   (from your laptop)
                 python3 scripts/swiggy_login.py --no-browser   (on the Pi)
                 → open the printed URL in the laptop browser; the localhost
                   redirect tunnels back to the Pi's callback server.

The access token lasts ~5 days and there is no refresh flow — when it expires
the robot degrades gracefully and Telegram-nudges you to re-run this script.
A running brain picks up the new token automatically (header hot-reload); only
a NEVER-configured brain needs one restart after the first login.

Standalone on purpose: stdlib + httpx only, never imports langrobo_core.
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

AUTH_BASE = os.getenv("SWIGGY_MCP_BASE", "https://mcp.swiggy.com")
SCOPE = "mcp:tools mcp:resources mcp:prompts"
DOMAIN = "swiggy"
MCP_SERVERS = {
    "food": f"{AUTH_BASE}/food",
    "instamart": f"{AUTH_BASE}/im",
    "dineout": f"{AUTH_BASE}/dineout",
}
CALLBACK_TIMEOUT_S = 300


def discover_endpoints() -> dict:
    fallback = {
        "authorization_endpoint": f"{AUTH_BASE}/auth/authorize",
        "token_endpoint": f"{AUTH_BASE}/auth/token",
        "registration_endpoint": f"{AUTH_BASE}/auth/register",
    }
    try:
        r = httpx.get(f"{AUTH_BASE}/.well-known/oauth-authorization-server", timeout=10)
        r.raise_for_status()
        meta = r.json()
        return {k: meta.get(k, v) for k, v in fallback.items()}
    except Exception as e:
        print(f"  (metadata discovery failed — using known endpoints: {e})")
        return fallback


def load_token_file(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": 1, "domains": {}}


def save_token_file(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def register_client(endpoints: dict, redirect_uri: str, existing: str | None) -> str:
    if existing:
        print(f"• Reusing registered client_id {existing[:8]}…")
        return existing
    print("• Registering OAuth client (RFC 7591 dynamic registration)…")
    r = httpx.post(endpoints["registration_endpoint"], json={
        "client_name": "langrobo robot brain",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": SCOPE,
    }, timeout=15)
    if r.status_code not in (200, 201):
        sys.exit(f"Client registration failed ({r.status_code}): {r.text[:300]}")
    client_id = r.json()["client_id"]
    print(f"  client_id: {client_id[:8]}…")
    return client_id


class _Callback(BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        _Callback.result = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        ok = "code" in _Callback.result
        self.wfile.write((
            "<html><body style='font-family:sans-serif;text-align:center;padding-top:20vh'>"
            + ("<h2>✅ Logged in — you can close this tab.</h2>"
               if ok else "<h2>❌ Login failed — check the terminal.</h2>")
            + "</body></html>").encode())

    def log_message(self, *_):
        pass


def wait_for_code(port: int, expected_state: str) -> str:
    server = HTTPServer(("127.0.0.1", port), _Callback)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    deadline = time.time() + CALLBACK_TIMEOUT_S
    try:
        while time.time() < deadline:
            if _Callback.result:
                res = _Callback.result
                if res.get("state") != expected_state:
                    sys.exit("State mismatch on callback — aborting (possible CSRF).")
                if "error" in res:
                    sys.exit(f"Authorization failed: {res.get('error')}: "
                             f"{res.get('error_description', '')}")
                return res["code"]
            time.sleep(0.2)
        sys.exit(f"Timed out after {CALLBACK_TIMEOUT_S}s waiting for the browser callback.")
    finally:
        server.shutdown()


def exchange_code(endpoints: dict, client_id: str, code: str,
                  verifier: str, redirect_uri: str) -> dict:
    r = httpx.post(endpoints["token_endpoint"], data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }, timeout=15)
    if r.status_code != 200:
        body = r.text[:300]
        if "invalid_grant" in body:
            sys.exit("Token exchange failed: the authorization code expired "
                     "(it lives 120 seconds). Re-run the script and finish the "
                     "browser step promptly.")
        sys.exit(f"Token exchange failed ({r.status_code}): {body}")
    return r.json()


def verify_servers(token: str) -> None:
    """Minimal streamable-HTTP initialize + tools/list against each server."""
    print("\n• Verifying token against the three MCP servers…")
    for name, url in MCP_SERVERS.items():
        try:
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            with httpx.Client(timeout=20) as client:
                init = client.post(url, headers=headers, json={
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26",
                               "capabilities": {},
                               "clientInfo": {"name": "swiggy_login", "version": "1.0"}},
                })
                init.raise_for_status()
                session = init.headers.get("mcp-session-id", "")
                if session:
                    headers["Mcp-Session-Id"] = session
                client.post(url, headers=headers, json={
                    "jsonrpc": "2.0", "method": "notifications/initialized"})
                listed = client.post(url, headers=headers, json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                listed.raise_for_status()
                text = listed.text
                if text.startswith("event:") or "\ndata:" in text or text.startswith("data:"):
                    data_lines = [ln[5:].strip() for ln in text.splitlines()
                                  if ln.startswith("data:")]
                    payload = json.loads(data_lines[-1]) if data_lines else {}
                else:
                    payload = listed.json()
                tools = payload.get("result", {}).get("tools", [])
                print(f"  {name:10s}: {len(tools)} tools")
        except Exception as e:
            print(f"  {name:10s}: FAILED — {e}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Swiggy MCP OAuth login")
    ap.add_argument("--port", type=int, default=8976)
    ap.add_argument("--token-file", default="~/.langrobo/mcp_tokens.json")
    ap.add_argument("--no-browser", action="store_true",
                    help="print the URL instead of opening a browser")
    ap.add_argument("--verify", action="store_true",
                    help="after login, list tools on all three MCP servers")
    args = ap.parse_args()

    token_path = os.path.expanduser(args.token_file)
    redirect_uri = f"http://localhost:{args.port}/callback"
    store = load_token_file(token_path)
    domain_entry = store["domains"].get(DOMAIN, {})

    endpoints = discover_endpoints()
    client_id = register_client(endpoints, redirect_uri, domain_entry.get("client_id"))

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)

    auth_url = endpoints["authorization_endpoint"] + "?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })

    print(f"\n• Waiting for login on {redirect_uri} (5 min)…")
    opened = False
    if not args.no_browser:
        opened = webbrowser.open(auth_url)
    if not opened:
        print("\nOpen this URL in a browser (headless robot: first run "
              f"`ssh -L {args.port}:localhost:{args.port} <robot>` from your "
              "laptop, then open it there):\n")
        print(f"  {auth_url}\n")

    code = wait_for_code(args.port, state)
    print("• Got authorization code — exchanging (120s window)…")
    tok = exchange_code(endpoints, client_id, code, verifier, redirect_uri)

    now = int(time.time())
    expires_in = int(tok.get("expires_in", 5 * 86400))
    store["domains"][DOMAIN] = {
        "client_id": client_id,
        "access_token": tok["access_token"],
        "token_type": tok.get("token_type", "Bearer"),
        "scope": tok.get("scope", SCOPE),
        "issued_at": now,
        "expires_at": now + expires_in,
        "refresh_token": tok.get("refresh_token"),
    }
    save_token_file(token_path, store)

    expiry = time.strftime("%a %d %b %H:%M", time.localtime(now + expires_in))
    print(f"\n✅ Token saved to {token_path} (expires {expiry}).")
    print("   A running brain picks this up automatically on the next Swiggy "
          "request. If Swiggy was never configured before, restart the brain "
          "once: sudo systemctl restart langrobo-brain")

    if args.verify:
        verify_servers(tok["access_token"])


if __name__ == "__main__":
    main()
