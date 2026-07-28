import json
import threading
import webbrowser
import requests
import sys
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# Escopo desejado
SCOPE = "https://www.googleapis.com/auth/youtube.upload"

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        if "code" in query:
            self.server.auth_code = query["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Autenticado com sucesso. Pode fechar essa janela.</h1>")
        else:
            self.send_error(400, "Código de autorização não encontrado.")

    def log_message(self, format, *args):
        return  # Silencia logs

def start_http_server(port):
    server = HTTPServer(("localhost", port), OAuthCallbackHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    return server

def main():
    if len(sys.argv) != 2:
        print("Uso: python oauth2_flow.py /caminho/para/client_secrets.json")
        sys.exit(1)

    secrets_file = sys.argv[1]

    if not os.path.isfile(secrets_file):
        print(f"Arquivo {secrets_file} não encontrado.")
        sys.exit(1)

    secrets_dir = os.path.dirname(secrets_file)
    token_file = os.path.join(secrets_dir, "tokens.json")

    # Lê e extrai os dados do client_secrets.json
    with open(secrets_file, "r") as f:
        data = json.load(f)
        secrets = data["web"]
        client_id = secrets["client_id"]
        client_secret = secrets["client_secret"]
        auth_uri = secrets["auth_uri"]
        token_uri = secrets["token_uri"]
        redirect_uri = secrets["redirect_uris"][0]

    parsed_uri = urlparse(redirect_uri)
    port = parsed_uri.port or 8050  # fallback

    # Inicia o servidor local
    server = start_http_server(port)

    # Monta URL de autorização
    auth_url = (
        f"{auth_uri}?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={SCOPE}"
        f"&access_type=offline"
        f"&prompt=consent"
    )

    print("Abrindo navegador para autenticação...")
    webbrowser.open(auth_url)

    while not hasattr(server, "auth_code"):
        pass

    auth_code = server.auth_code
    server.shutdown()

    # Solicita os tokens
    data = {
        "code": auth_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    response = requests.post(token_uri, data=data)
    if response.ok:
        tokens = response.json()
        with open(token_file, "w") as f:
            json.dump(tokens, f, indent=2)
        print(f"Tokens salvos em: {token_file}")
    else:
        print("Erro ao obter tokens:")
        print(response.text)

if __name__ == "__main__":
    main()
