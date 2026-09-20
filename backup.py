import os
import re
import base64
import time
import requests
from pathlib import Path
from nacl.public import PublicKey, SealedBox


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


def update_github_secret(new_refresh_token):
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2026-03-10",
    }

    owner, repo = GITHUB_REPOSITORY.split("/", 1)

    # GitHub Actions Secret用の公開鍵を取得
    response = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}/actions/secrets/public-key",
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()

    public_key_data = response.json()

    public_key = PublicKey(
        base64.b64decode(public_key_data["key"])
    )

    # LibSodium sealed boxで暗号化
    sealed_box = SealedBox(public_key)

    encrypted = sealed_box.encrypt(
        new_refresh_token.encode("utf-8")
    )

    encrypted_value = base64.b64encode(encrypted).decode("utf-8")

    # GitHub Secretを更新
    response = requests.put(
        f"https://api.github.com/repos/{owner}/{repo}/actions/secrets/ATWIKI_REFRESH_TOKEN",
        headers=headers,
        json={
            "encrypted_value": encrypted_value,
            "key_id": public_key_data["key_id"],
        },
        timeout=30,
    )

    response.raise_for_status()

    print("GitHub refresh token secret updated.")


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

    # 429対策：最大5回まで再試行
    for attempt in range(5):
        response = requests.get(
            f"{API_BASE}/pages/{page_id}",
            headers=headers,
            timeout=30,
        )

        if response.status_code == 429:
            wait_time = 10 * (attempt + 1)

            print(
                f"Rate limit reached. "
                f"Waiting {wait_time} seconds..."
            )

            import time
            time.sleep(wait_time)

            continue

        response.raise_for_status()

        return response.json()["source"]

    raise Exception(
        f"API rate limit: page_id={page_id}"
    )


def safe_filename(name):
    name = re.sub(r'[<>:"/\\|?*]', "＿", name)
    name = name.strip().rstrip(". ")

    return name or "_"


def main():
    print("STGR Wiki Backup Start")

    # アットウィキのアクセストークンを更新
    token_data = refresh_access_token()

    access_token = token_data["access_token"]

    # 新しいrefresh tokenが発行された場合はGitHub Secretを更新
    new_refresh_token = token_data.get("refresh_token")

    if new_refresh_token:
        update_github_secret(new_refresh_token)

    # 全ページ取得
    pages = get_all_pages(access_token)

    print(f"Pages: {len(pages)}")

    # 各ページを保存
    for page in pages:
        page_name = page["pagename"]
        page_id = page["pageid"]

        source = get_page_source(
            access_token,
            page_id
        )

        directory = Path(
            safe_filename(page_name)
        )

        directory.mkdir(
            parents=True,
            exist_ok=True
        )

        file_path = directory / "本文.txt"

        file_path.write_text(
            source,
            encoding="utf-8"
        )

        print(f"Saved: {page_name}")

        # APIへの連続アクセスを避ける
        time.sleep(0.2)

    print("Backup complete")


if __name__ == "__main__":
    main()
