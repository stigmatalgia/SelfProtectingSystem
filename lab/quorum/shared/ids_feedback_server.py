import http.server
import json
import logging

import sys

if len(sys.argv) < 2:
    print("Usage: ids_feedback_server.py <log_file>")
    sys.exit(1)

LOG_FILE = sys.argv[1]
PORT = 6000

class FeedbackHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
            attack_type = data.get("attack_type")
            if attack_type:
                # Scriviamo direttamente sul file log che alert_forwarder sta seguendo
                with open(LOG_FILE, "a") as f:
                    f.write(f"NEGATIVE ALERT: {attack_type}\n")
                    f.flush()
                self.send_response(200)
                self.end_headers()
                return
        except Exception as e:
            print(f"Feedback error: {e}")
        self.send_response(400)
        self.end_headers()

if __name__ == "__main__":
    print(f"Feedback server starting on port {PORT}, monitoring {LOG_FILE}")
    server = http.server.HTTPServer(("0.0.0.0", PORT), FeedbackHandler)
    server.serve_forever()
