import http.server
import socketserver
import json
import os
import threading
import webbrowser

AUTH_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Puter Auth - BYTE AI</title>
    <script src="https://js.puter.com/v2/"></script>
    <style>
        body { background: #000; color: #fff; font-family: sans-serif; text-align: center; margin-top: 20%; }
        button { padding: 15px 30px; font-size: 18px; cursor: pointer; border-radius: 8px; border: none; background: #6b46c1; color: white; font-weight: bold; }
        #status { margin-top: 20px; font-size: 16px; color: #ccc; }
    </style>
</head>
<body>
    <h1>BYTE AI</h1>
    <p>Please authenticate with Puter to enable Speech-to-Text.</p>
    <button id="loginBtn">Sign in with Puter</button>
    <div id="status"></div>

    <script>
        document.getElementById('loginBtn').addEventListener('click', async () => {
            try {
                const responseObj = await puter.auth.signIn();
                // signIn() returns an object containing the token
                const rawToken = responseObj.token || responseObj;
                
                document.getElementById('status').innerText = 'Authenticated! Sending token to backend...';
                
                const response = await fetch('/token', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token: rawToken })
                });
                
                if (response.ok) {
                    document.getElementById('status').innerText = 'Success! You can safely close this window.';
                    document.getElementById('loginBtn').style.display = 'none';
                }
            } catch (err) {
                console.error(err);
                document.getElementById('status').innerText = 'Error: ' + err;
            }
        });
    </script>
</body>
</html>
"""

class AuthHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass # Suppress logging

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(AUTH_HTML.encode('utf-8'))

    def do_POST(self):
        if self.path == '/token':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)
            token = data.get('token')
            
            if token:
                if isinstance(token, dict):
                    token_str = token.get('token') or json.dumps(token)
                else:
                    token_str = str(token)
                    
                # Save to .env
                env_path = os.path.join(os.path.dirname(__file__), ".env")
                # Remove existing PUTER_AUTH_TOKEN if any
                lines = []
                if os.path.exists(env_path):
                    with open(env_path, "r") as f:
                        lines = f.readlines()
                
                with open(env_path, "w") as f:
                    for line in lines:
                        if not line.startswith("PUTER_AUTH_TOKEN="):
                            f.write(line)
                    f.write(f"PUTER_AUTH_TOKEN={token_str}\n")
                
                os.environ['PUTER_AUTH_TOKEN'] = token_str
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
                
                # Shutdown the server after 1 second so it can finish sending the response
                threading.Timer(1.0, self.server.shutdown).start()
            else:
                self.send_response(400)
                self.end_headers()

def acquire_puter_token(port=0):
    print(f"Starting Puter authentication server...")
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), AuthHandler) as httpd:
        actual_port = httpd.server_address[1]
        print(f"Auth server running at http://localhost:{actual_port}")
        webbrowser.open(f"http://localhost:{actual_port}")
        httpd.serve_forever()
    print("Authentication complete. Token acquired.")
    return os.environ.get('PUTER_AUTH_TOKEN')

if __name__ == "__main__":
    acquire_puter_token()
