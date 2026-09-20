import os
import re
import base64
import random
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

# バックアップ保存先
BACKUP_ROOT = Path("backup")

# ページ一覧取得設定
PAGE_LIST_MAX_RETRIES = 10

# 本文取得設定
SOURCE_MAX_RETRIES = 8

# APIへの連続アクセスを避けるための待機時間
PAGE_LIST_DELAY = 3.0
SOURCE_DELAY = 0.5


def refresh_access_token():
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

    print(f"OAuth HTTP Status: {response.status_code}")

    if response.status_code != 200:
        print("OAuth response:")
        print(response.text)

    response.raise_for_status()

    data = response.json()

    print("2. OAuth OK")

    return data


def update_github_secret(new_refresh_token):
    print("Updating GitHub refresh token secret...")

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


def calculate_wait_time(attempt, response=None, base_wait=10):
    """
    Retry-After があればそれを優先。
    なければ段階的に待機。
    """

    if response is not None:
        retry_after = response.headers.get("Retry-After")

        if retry_after:
            try:
                return max(1, int(float(retry_after)))
            except ValueError:
                pass

    # 10, 20, 40, 80... 秒
    wait_time = base_wait * (2 ** attempt)

    # 最大5分
    wait_time = min(wait_time, 300)

    # 少しランダム化して同時アクセスを避ける
    jitter = random.uniform(0, 3)

    return wait_time + jitter


def get_all_pages(access_token):
    print("3. Request page list")

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    pages = []
    cursor = None

    while True:
        params = {
            "limit": 100
        }

        if cursor:
            params["cursor"] = cursor

        success = False

        for attempt in range(PAGE_LIST_MAX_RETRIES):
            print(
                f"   Attempt {attempt + 1}/{PAGE_LIST_MAX_RETRIES}: "
                f"requesting page list..."
            )

            response = requests.get(
                f"{API_BASE}/pages",
                headers=headers,
                params=params,
                timeout=30,
            )

            print(f"   HTTP Status: {response.status_code}")

            if response.status_code == 429:
                wait_time = calculate_wait_time(
                    attempt,
                    response,
                    base_wait=10,
                )

                print(
                    f"   Rate limit reached. "
                    f"Waiting {wait_time:.1f} seconds..."
                )

                time.sleep(wait_time)
                continue

            response.raise_for_status()

            success = True
            break

        if not success:
            raise Exception(
                "Page list API rate limit: "
                f"{PAGE_LIST_MAX_RETRIES} retries failed"
            )

        data = response.json()

        items = data.get("items", [])

        pages.extend(items)

        print(
            f"   Page list progress: "
            f"{len(pages)} pages retrieved"
        )

        cursor = data.get("next_cursor")

        if not cursor:
            break

        print("   Next cursor received.")

        # 次のページ一覧取得まで待つ
        time.sleep(PAGE_LIST_DELAY)

    print(
        f"4. Page list complete: {len(pages)} pages"
    )

    return pages


def get_page_source(access_token, page_id):
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    for attempt in range(SOURCE_MAX_RETRIES):
        response = requests.get(
            f"{API_BASE}/pages/{page_id}",
            headers=headers,
            timeout=30,
        )

        if response.status_code == 429:
            wait_time = calculate_wait_time(
                attempt,
                response,
                base_wait=10,
            )

            print(
                f"   429 for page {page_id}. "
                f"Waiting {wait_time:.1f} seconds..."
            )

            time.sleep(wait_time)
            continue

        response.raise_for_status()

        data = response.json()

        return data["source"]

    raise Exception(
        f"API rate limit: "
        f"page_id={page_id}, "
        f"{SOURCE_MAX_RETRIES} retries failed"
    )


def safe_filename(name):
    """
    Windows/GitHub上で問題になる文字を置換。
    """

    name = re.sub(
        r'[<>:"/\\|?*]',
        "＿",
        name,
    )

    name = name.strip().rstrip(". ")

    # Windows予約名対策
    reserved_names = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }

    if name.upper() in reserved_names:
        name = f"_{name}_"

    return name or "_"


def save_page(page, source):
    page_name = page["pagename"]

    safe_name = safe_filename(page_name)

    directory = BACKUP_ROOT / safe_name

    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_path = directory / "本文.txt"

    file_path.write_text(
        source,
        encoding="utf-8",
    )

    return file_path


def main():
    print("================================")
    print("STGR Wiki Backup Start")
    print("================================")

    # --------------------------------
    # OAuth
    # --------------------------------

    token_data = refresh_access_token()

    access_token = token_data["access_token"]

    # Refresh Token Rotation対応
    new_refresh_token = token_data.get("refresh_token")

    if new_refresh_token:
        print("New refresh token received.")

        update_github_secret(
            new_refresh_token
        )

    # --------------------------------
    # ページ一覧
    # --------------------------------

    pages = get_all_pages(access_token)

    print(
        f"Total pages to backup: {len(pages)}"
    )

    # --------------------------------
    # 本文取得
    # --------------------------------

    print("5. Starting page source backup")

    saved_count = 0

    for index, page in enumerate(
        pages,
        start=1,
    ):
        page_name = page["pagename"]
        page_id = page["pageid"]

        print(
            f"[{index}/{len(pages)}] "
            f"Fetching: {page_name} "
            f"(page_id={page_id})"
        )

        source = get_page_source(
            access_token,
            page_id,
        )

        file_path = save_page(
            page,
            source,
        )

        saved_count += 1

        print(
            f"   Saved: {file_path}"
        )

        # APIへの連続アクセスを避ける
        time.sleep(SOURCE_DELAY)

    print("================================")
    print("Backup complete")
    print(
        f"Pages saved: {saved_count}/{len(pages)}"
    )
    print("================================")


if __name__ == "__main__":
    main()
