from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
import json, base64

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)

# Sauvegarde le token en JSON
token_data = {
    "token":         creds.token,
    "refresh_token": creds.refresh_token,
    "token_uri":     creds.token_uri,
    "client_id":     creds.client_id,
    "client_secret": creds.client_secret,
    "scopes":        creds.scopes,
}

with open("token.json", "w") as f:
    json.dump(token_data, f)

# Affiche aussi la version base64 pour Render
b64 = base64.b64encode(json.dumps(token_data).encode()).decode()
print("\n✅ Token généré !")
print("\n📋 Copie cette valeur dans Render (GOOGLE_TOKEN_B64) :\n")
print(b64)