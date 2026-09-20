import os
import re
import base64
import random
import time
import json
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

# 途中再開用
STATE_FILE = BACKUP_ROOT / ".backup_state.json"

# ページ一覧取得設定
PAGE_LIST_MAX_RETRIES = 10

# 本文取得設定
SOURCE_MAX_RETRIES = 8

# APIへの連続アクセスを避ける
PAGE_LIST_DELAY = 3.0
SOURCE_DELAY = 0.5

# 429で待つ最大時間
MAX_RETRY_WAIT = 60

# Retry-Afterがこれを超えたら、その回は終了して次回に回す
MAX_ACCEPTABLE_RETRY_AFTER = 300


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

    sealed_box = SealedBox(public_key)

    encrypted = sealed_box.encrypt(
        new_refresh_token.encode("utf-8")
    )

    encrypted_value = base64.b64encode(
        encrypted
    ).decode("utf-8")

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


def load_state():
    if not STATE_FILE.exists():
        print("No backup state found. Starting fresh.")
        return {}

    try:
        state = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        print(
            f"Backup state loaded: "
            f"{len(state)} pages"
        )

        return state

    except Exception as e:
        print(
            f"Warning: failed to load backup state: {e}"
        )

        return {}


def save_state(state):
    BACKUP_ROOT.mkdir(
        parents=True,
        exist_ok=True
    )

    temporary_file = (
        BACKUP_ROOT / ".backup_state.tmp"
    )

    temporary_file.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    temporary_file.replace(STATE_FILE)


def calculate_wait_time(
    attempt,
    response=None,
    base_wait=10
):
    """
    Retry-Afterを確認する。
    ただし長すぎる待機はそのまま使用しない。
    """

    retry_after = None

    if response is not None:
        retry_after = response.headers.get(
            "Retry-After"
        )

    if retry_after:
        try:
            retry_after = float(
                retry_after
            )

            print(
                f"   Retry-After reported: "
                f"{retry_after:.1f} seconds"
            )

            # 長すぎる場合は呼び出し側で終了判断
            if retry_after > MAX_ACCEPTABLE_RETRY_AFTER:
                return retry_after

            return min(
                retry_after,
                MAX_RETRY_WAIT
            )

        except ValueError:
            pass

    # 10, 20, 40, 80...
    wait_time = base_wait * (
        2 ** attempt
    )

    wait_time = min(
        wait_time,
        MAX_RETRY_WAIT
    )

    jitter = random.uniform(
        0,
        3
    )

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

        for attempt in range(
            PAGE_LIST_MAX_RETRIES
        ):

            print(
                f"   Attempt "
                f"{attempt + 1}/"
                f"{PAGE_LIST_MAX_RETRIES}: "
                f"requesting page list..."
            )

            response = requests.get(
                f"{API_BASE}/pages",
                headers=headers,
                params=params,
                timeout=30,
            )

            print(
                f"   HTTP Status: "
                f"{response.status_code}"
            )

            if response.status_code == 429:

                wait_time = (
                    calculate_wait_time(
                        attempt,
                        response,
                        base_wait=10,
                    )
                )

                # 長すぎる場合は異常終了
                # ではなく、その回の処理を止める
                if wait_time > MAX_ACCEPTABLE_RETRY_AFTER:

                    print(
                        "   429 Retry-After is too long: "
                        f"{wait_time:.1f} seconds"
                    )

                    print(
                        "   Page list retrieval will "
                        "stop for this run."
                    )

                    return None

                print(
                    f"   Rate limit reached. "
                    f"Waiting "
                    f"{wait_time:.1f} seconds..."
                )

                time.sleep(
                    wait_time
                )

                continue

            response.raise_for_status()

            success = True

            break

        if not success:

            print(
                "Page list retrieval "
                "could not continue."
            )

            return None

        data = response.json()

        items = data.get(
            "items",
            []
        )

        pages.extend(items)

        print(
            f"   Page list progress: "
            f"{len(pages)} pages retrieved"
        )

        cursor = data.get(
            "next_cursor"
        )

        if not cursor:
            break

        print(
            "   Next cursor received."
        )

        time.sleep(
            PAGE_LIST_DELAY
        )

    print(
        f"4. Page list complete: "
        f"{len(pages)} pages"
    )

    return pages


def get_page_source(
    access_token,
    page_id
):
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    for attempt in range(
        SOURCE_MAX_RETRIES
    ):

        response = requests.get(
            f"{API_BASE}/pages/{page_id}",
            headers=headers,
            timeout=30,
        )

        if response.status_code == 429:

            wait_time = (
                calculate_wait_time(
                    attempt,
                    response,
                    base_wait=10,
                )
            )

            # 長すぎるRetry-Afterなら
            # 無理に待たずに次回へ回す
            if wait_time > MAX_ACCEPTABLE_RETRY_AFTER:

                print(
                    f"   429 for page {page_id}. "
                    f"Retry-After is too long: "
                    f"{wait_time:.1f} seconds."
                )

                print(
                    "   This page will be retried "
                    "on the next workflow run."
                )

                return None

            print(
                f"   429 for page {page_id}. "
                f"Waiting "
                f"{wait_time:.1f} seconds..."
            )

            time.sleep(
                wait_time
            )

            continue

        response.raise_for_status()

        data = response.json()

        return data["source"]

    print(
        f"   Page {page_id}: "
        f"maximum retries reached."
    )

    return None


def safe_filename(name):
    name = re.sub(
        r'[<>:"/\\|?*]',
        "＿",
        name,
    )

    name = name.strip().rstrip(
        ". "
    )

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


def save_page(
    page,
    source
):
    page_name = page["pagename"]

    safe_name = safe_filename(
        page_name
    )

    directory = (
        BACKUP_ROOT / safe_name
    )

    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_path = (
        directory / "本文.txt"
    )

    file_path.write_text(
        source,
        encoding="utf-8",
    )

    return file_path


def page_is_already_backed_up(
    state,
    page
):
    page_id = str(
        page["pageid"]
    )

    current_updated_at = page.get(
        "updated_at"
    )

    saved_updated_at = state.get(
        page_id
    )

    if (
        saved_updated_at
        and current_updated_at
        and saved_updated_at
        == current_updated_at
    ):
        return True

    return False


def main():

    print(
        "================================"
    )

    print(
        "STGR Wiki Backup Start"
    )

    print(
        "================================"
    )

    # --------------------------------
    # OAuth
    # --------------------------------

    token_data = (
        refresh_access_token()
    )

    access_token = (
        token_data["access_token"]
    )

    # Refresh Token Rotation
    new_refresh_token = (
        token_data.get(
            "refresh_token"
        )
    )

    if new_refresh_token:

        print(
            "New refresh token received."
        )

        update_github_secret(
            new_refresh_token
        )

    # --------------------------------
    # State
    # --------------------------------

    state = load_state()

    # --------------------------------
    # ページ一覧
    # --------------------------------

    pages = get_all_pages(
        access_token
    )

    if pages is None:

        print(
            "================================"
        )

        print(
            "Page list retrieval paused."
        )

        print(
            "This workflow run will end."
        )

        print(
            "================================"
        )

        return

    print(
        f"Total pages to backup: "
        f"{len(pages)}"
    )

    # --------------------------------
    # 本文取得
    # --------------------------------

    print(
        "5. Starting page source backup"
    )

    saved_count = 0
    skipped_count = 0

    interrupted = False

    for index, page in enumerate(
        pages,
        start=1,
    ):

        page_name = page[
            "pagename"
        ]

        page_id = page[
            "pageid"
        ]

        # 既に同じ更新日時のページなら
        # APIアクセスしない
        if page_is_already_backed_up(
            state,
            page
        ):

            skipped_count += 1

            print(
                f"[{index}/{len(pages)}] "
                f"Skipped: "
                f"{page_name}"
            )

            continue

        print(
            f"[{index}/{len(pages)}] "
            f"Fetching: "
            f"{page_name} "
            f"(page_id={page_id})"
        )

        source = get_page_source(
            access_token,
            page_id,
        )

        # 429等で今回は取得できなかった
        if source is None:

            print(
                "================================"
            )

            print(
                f"Backup paused at "
                f"{index}/{len(pages)}"
            )

            print(
                f"Page: {page_name}"
            )

            print(
                "The next workflow run "
                "will continue."
            )

            print(
                "================================"
            )

            interrupted = True

            break

        file_path = save_page(
            page,
            source,
        )

        # 成功したページを即座に記録
        state[
            str(page_id)
        ] = page.get(
            "updated_at"
        )

        save_state(
            state
        )

        saved_count += 1

        print(
            f"   Saved: {file_path}"
        )

        time.sleep(
            SOURCE_DELAY
        )

    # --------------------------------
    # 結果
    # --------------------------------

    print(
        "================================"
    )

    if interrupted:

        print(
            "Backup paused"
        )

        print(
            f"New pages saved: "
            f"{saved_count}"
        )

        print(
            f"Pages skipped: "
            f"{skipped_count}"
        )

        print(
            "Progress has been saved."
        )

    else:

        print(
            "Backup complete"
        )

        print(
            f"New/updated pages saved: "
            f"{saved_count}"
        )

        print(
            f"Pages skipped: "
            f"{skipped_count}"
        )

    print(
        "================================"
    )


if __name__ == "__main__":
    main()
