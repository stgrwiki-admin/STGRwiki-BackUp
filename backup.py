import os
import requests

WIKI_ID = "streamergta5"
API_BASE = f"https://w.atwiki.jp/_api/v1/wikis/{WIKI_ID}"

CLIENT_ID = os.environ["ATWIKI_CLIENT_ID"]
CLIENT_SECRET = os.environ["ATWIKI_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["ATWIKI_REFRESH_TOKEN"]


print("1. OAuth token refresh")

response = requests.post(
    "https://auth.atwiki.jp/oauth2/token",
    auth=(CLIENT_ID, CLIENT_SECRET),
    data={
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
    },
    timeout=30,
)

response.raise_for_status()

token_data = response.json()

access_token = token_data["access_token"]

print("2. OAuth OK")


print("3. Request page list")

response = requests.get(
    f"{API_BASE}/pages",
    headers={
        "Authorization": f"Bearer {access_token}"
    },
    params={
        "limit": 10
    },
    timeout=30,
)

print(f"4. HTTP Status: {response.status_code}")

print(response.text[:1000])
