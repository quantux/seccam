import json
import requests
from config import CLIENT_SECRETS_FILE, TOKENS_FILE

def check_access_token_validity(access_token):
    url = "https://www.googleapis.com/oauth2/v3/tokeninfo"
    params = {"access_token": access_token}
    response = requests.get(url, params=params)
    return response.status_code == 200

def refresh_access_token():
    with open(CLIENT_SECRETS_FILE, "r") as f:
        keys = json.load(f)
    with open(TOKENS_FILE, "r") as f:
        tokens = json.load(f)

    if check_access_token_validity(tokens.get("access_token", "")):
        print("Access token válido.")
        return

    print("Access token inválido, atualizando...")
    url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": keys["web"]["client_id"],
        "client_secret": keys["web"]["client_secret"],
        "refresh_token": tokens.get("refresh_token", ""),
        "grant_type": "refresh_token"
    }
    response = requests.post(url, data=data)
    if response.status_code == 200:
        tokens["access_token"] = response.json().get("access_token")
        with open(TOKENS_FILE, "w") as f:
            json.dump(tokens, f, indent=4)
        print("Novo access_token salvo.")
    else:
        print("Erro ao atualizar access_token.")
