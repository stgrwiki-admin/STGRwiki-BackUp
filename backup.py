import os
import re
import base64
import requests
from pathlib import Path


WIKI_ID = "streamergta5"
API_BASE = f"https://w.atwiki.jp/_api/v1/wikis/{WIKI_ID}"

CLIENT_ID = os.environ["ATWIKI_CLIENT_ID"]
CLIENT_SECRET = os.environ["ATWIKI_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["ATWIKI_REFRESH_TOKEN"]

GITHUB_TOKEN = os.environ["STGR_GITHUB_PAT"]
GITHUB_REPOSITORY = os.environ["GITHUB_REPOSITORY"]


def refresh_access_token():
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

    return response.json()


def update_github_refresh_token(new_refresh_token):
    # GitHub Secret API用の処理は次の段階で実装します。
    # 現時点では新しいrefresh_tokenを取得するところまで行います。
    return new_refresh_token


def get_all_pages(access_token):
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    pages = []
    cursor = None

    while True:
        params = {"limit": 100}

        if cursor:
            params["cursor"] = cursor

        response = requests.get(
            f"{API_BASE}/pages",
            headers=headers,
            params=params,
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()
        pages.extend(data.get("items", []))

        cursor = data.get("next_cursor")

        if not cursor:
            break

    return pages


def get_page_source(access_token, page_id):
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(
        f"{API_BASE}/pages/{page_id}",
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()

    return response.json()["source"]


def safe_filename(name):
    name = re.sub(r'[<>:"/\\|?*]', "＿", name)
    name = name.strip().rstrip(". ")

    return name or "_"


def main():
    print("STGR Wiki Backup Start")

    token_data = refresh_access_token()

    access_token = token_data["access_token"]
    new_refresh_token = token_data.get("refresh_token")

    if new_refresh_token:
        update_github_refresh_token(new_refresh_token)

    pages = get_all_pages(access_token)

    print(f"Pages: {len(pages)}")

    for page in pages:
        page_name = page["pagename"]
        page_id = page["pageid"]

        source = get_page_source(access_token, page_id)

        directory = Path(safe_filename(page_name))
        directory.mkdir(parents=True, exist_ok=True)

        file_path = directory / "本文.txt"
        file_path.write_text(source, encoding="utf-8")

        print(f"Saved: {page_name}")

    print("Backup complete")


if __name__ == "__main__":
    main()
