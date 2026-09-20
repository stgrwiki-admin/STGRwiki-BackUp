import os
import time
import requests


WIKI_ID = "streamergta5"
API_BASE = f"https://w.atwiki.jp/_api/v1/wikis/{WIKI_ID}"

CLIENT_ID = os.environ["ATWIKI_CLIENT_ID"]
CLIENT_SECRET = os.environ["ATWIKI_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["ATWIKI_REFRESH_TOKEN"]


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

    response.raise_for_status()

    print("2. OAuth OK")

    return response.json()["access_token"]


def test_page_list(access_token):
    print("3. Request page list")

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    params = {
        "limit": 20
    }

    max_attempts = 10

    for attempt in range(1, max_attempts + 1):

        print(
            f"   Attempt {attempt}/{max_attempts}: "
            "requesting page list..."
        )

        response = requests.get(
            f"{API_BASE}/pages",
            headers=headers,
            params=params,
            timeout=30,
        )

        print(f"   HTTP Status: {response.status_code}")

        if response.status_code == 429:

            # AtWikiからRetry-Afterが返ってきた場合は使用
            retry_after = response.headers.get("Retry-After")

            if retry_after:
                try:
                    wait_time = int(retry_after)
                except ValueError:
                    wait_time = 30 * attempt
            else:
                wait_time = 30 * attempt

            print(
                f"   Rate limit reached."
                f" Waiting {wait_time} seconds..."
            )

            time.sleep(wait_time)
            continue

        response.raise_for_status()

        data = response.json()

        items = data.get("items", [])

        print()
        print("================================")
        print("SUCCESS")
        print("================================")
        print(f"Pages retrieved: {len(items)}")
        print(f"Next cursor: {data.get('next_cursor')}")
        print()
        print("429対策テスト成功")
        print("今回はここで終了します。")
        print("本文取得やGitHub保存は行っていません。")

        return

    raise Exception(
        f"Page list API rate limit: "
        f"{max_attempts} retries failed"
    )


def main():
    print("STGR Wiki 429 TEST")
    print()

    access_token = refresh_access_token()

    test_page_list(access_token)


if __name__ == "__main__":
    main()
