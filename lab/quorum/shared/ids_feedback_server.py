import http.server
import json
import logging

PORT = 6000
LOG_FILE = "/var/log/ids_feedback.log"

logging.basicConfig(level=logging.INFO, format="%(message)s", 
                    handlers=[logging.FileHandler(LOG_FILE)])

class FeedbackHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
            attack_type = data.get("attack_type")
            if attack_type:
                logging.info(f"NEGATIVE ALERT: {attack_type}")
                self.send_response(200)
                self.end_headers()
                return
        except: pass
        self.send_response(400)
        self.end_headers()

if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", PORT), FeedbackHandler)
    server.serve_forever()
