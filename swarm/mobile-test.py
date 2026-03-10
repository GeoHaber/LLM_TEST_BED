import http.server
import socketserver
import socket

PORT = 8787


# Get local IP
def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


LOCAL_IP = get_ip()

HTML = f"""
<!DOCTYPE html>
<html>
<head>
    <title>ZenAIos Mobile Connection Test</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ font-family: sans-serif; text-align: center; padding: 50px; background: #0b0e14; color: white; }}
        .card {{ background: #1a1f2e; padding: 30px; border-radius: 20px; display: inline-block; border: 1px solid #30363d; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }}
        h1 {{ color: #58a6ff; margin-bottom: 5px; }}
        p {{ color: #8b949e; }}
        .ip {{ font-family: monospace; background: #0d1117; padding: 5px 10px; border-radius: 5px; color: #79c0ff; }}
        .success {{ color: #3fb950; font-weight: bold; font-size: 1.2rem; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>🏥 ZenAIos</h1>
        <p>Mobile Connection Test Successful</p>
        <div class="success">✅ Connected!</div>
        <hr style="border:0; border-top:1px solid #30363d; margin: 20px 0;">
        <p>Detected PC IP: <span class="ip">{LOCAL_IP}</span></p>
        <p>Current Port: <span class="ip">{PORT}</span></p>
    </div>
</body>
</html>
"""


class TestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(HTML.encode())

    # Suppress log messages to keep terminal clean
    def log_message(self, format, *args):
        if "favicon" in args[0]:
            return  # Ignore favicon logs
        print(f"[{self.date_time_string()}] {args[0]}")


class SafeTCPServer(socketserver.TCPServer):
    def handle_error(self, request, client_address):
        # Suppress "Connection reset by peer" (WinError 10054)
        import sys

        err = sys.exc_info()[1]
        if (
            isinstance(err, (ConnectionResetError, OSError))
            and getattr(err, "errno", None) == 10054
        ):
            pass
        else:
            super().handle_error(request, client_address)


print("--- ZenAIos Mobile Connection Test ---")
print(f"1. PC Access: http://localhost:{PORT}")
print(f"2. Local IP:  http://{LOCAL_IP}:{PORT}")
print("3. Tunnel:    Use your VS Code or ngrok URL")
print("---------------------------------------")
print(f"Starting server on port {PORT}...")

with SafeTCPServer(("", PORT), TestHandler) as httpd:
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        httpd.server_close()
