# Server in ascolto sull'attuatore, ponte per la logica di response 
# Ascolta richieste HTTP di tipo POST sulla porta 5000
# Ritira il campo action e ne domanda l'esecuzione tramite ssh sul servizio vulnerabile (Juice_shop)
import http.server
import json
import subprocess
import logging
import urllib.request
from datetime import datetime

#Costanti del caso
PORT           = 5000
JUICE_SHOP_IP  = "10.0.0.80"
SSH_KEY        = "/root/.ssh/id_ed25519"
SSH_OPTS       = ["-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", "-i", SSH_KEY]
LOG_FILE       = "/var/log/actuator_actions.log"
IDS_IPS        = ["10.0.100.11", "10.0.100.12", "10.0.100.13"]
ATTACK_MAP     = {
    "SQL Injection": "SQL_INJECTION",
    "XSS Attack": "XSS_ATTACK",
    "Path Traversal": "PATH_TRAVERSAL",
    "Command Injection": "COMMAND_INJECTION"
}

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s.%(msecs)03d] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger()


class ActuatorHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass 

    def do_POST(self):
        if self.path != "/action":
            self.send_error(404, "Not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)

        # Ritira action come parametro + controllo sulla sua esistenza che non fa mai male.
        try:
            data   = json.loads(body)
            action = data.get("action", "").strip()
        except (json.JSONDecodeError, AttributeError):
            self.send_error(400, "Invalid JSON")
            return

        if not action:
            self.send_error(400, "Missing 'action' field")
            return

        log.info(f"RECEIVED action: {action}")

        # Invio ad ssh!
        result = subprocess.run(
            ["ssh", *SSH_OPTS, f"root@{JUICE_SHOP_IP}", action],
            capture_output=True, text=True, timeout=10, encoding='utf-8'
        )

        # I dettagli della richiesta vengono stampati poi su file di log in LOG_FILE
        log.info(f"SSH exit={result.returncode} stdout={result.stdout.strip()} stderr={result.stderr.strip()}")
        for key, val in ATTACK_MAP.items():
            if key in action:
                for ip in IDS_IPS:
                    try:
                        req = urllib.request.Request(f"http://{ip}:6000/", 
                            data=json.dumps({"attack_type": val}).encode(),
                            headers={'Content-Type': 'application/json'})
                        urllib.request.urlopen(req, timeout=1)
                    except: pass

        resp = json.dumps({"status": "executed", "exit": result.returncode}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(resp))
        self.end_headers()
        self.wfile.write(resp)


import socketserver

class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    pass

if __name__ == "__main__":
    log.info(f"Actuator server starting on port {PORT}")
    server = ThreadedHTTPServer(("0.0.0.0", PORT), ActuatorHandler)
    server.serve_forever()
