import requests
from requests.auth import HTTPBasicAuth
from concurrent.futures import ThreadPoolExecutor, as_completed

# =============================================================================
# CONFIG
# =============================================================================

JIRA_URL = "https://example.atlassian.net/"

EMAIL = "example@example.com"
API_TOKEN = "TOKEN"


#SOURCE_FIELD = "customfield_18105"
#TARGET_FIELD = "customfield_17900"

SOURCE_FIELD = "customfield_18106"
TARGET_FIELD = "customfield_17901"



JQL = """
cf[18106]  is not EMPTY and cf[17901] is  EMPTY
"""

DRY_RUN = False      # False = perform updates
WORKERS = 20

# =============================================================================

auth = HTTPBasicAuth(EMAIL, API_TOKEN)

HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json"
}


def get_all_issues():
    issues = []
    next_token = None

    while True:
        payload = {
            "jql": JQL,
            "fields": [
                SOURCE_FIELD,
                TARGET_FIELD
            ],
            "maxResults": 100
        }

        if next_token:
            payload["nextPageToken"] = next_token

        r = requests.post(
            f"{JIRA_URL}/rest/api/3/search/jql",
            headers=HEADERS,
            auth=auth,
            json=payload
        )

        r.raise_for_status()

        data = r.json()

        batch = data.get("issues", [])
        issues.extend(batch)

        print(
            f"Fetched batch={len(batch)} "
            f"total={len(issues)}"
        )

        next_token = data.get("nextPageToken")

        if not next_token:
            break

    return issues


def update_issue(issue):
    key = issue["key"]

    source_value = issue["fields"].get(SOURCE_FIELD)
    target_value = issue["fields"].get(TARGET_FIELD)

    if not source_value:
        return f"SKIP {key} | source empty"

    if source_value == target_value:
        return f"SKIP {key} | already copied"

    if DRY_RUN:
        return (
            f"DRY_RUN {key} | "
            f"{TARGET_FIELD}: {target_value} -> {source_value}"
        )

    payload = {
        "fields": {
            TARGET_FIELD: source_value
        }
    }

    r = requests.put(
        f"{JIRA_URL}/rest/api/3/issue/{key}?notifyUsers=false",
        headers=HEADERS,
        auth=auth,
        json=payload
    )

    if r.status_code == 204:
        return f"UPDATED {key} -> {source_value}"

    return f"FAILED {key} | {r.status_code} | {r.text}"


def main():
    issues = get_all_issues()

    print()
    print("=" * 80)
    print(f"ISSUES FOUND: {len(issues)}")
    print("=" * 80)
    print()

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = [
            executor.submit(update_issue, issue)
            for issue in issues
        ]

        for future in as_completed(futures):
            print(future.result())

    print("\nFinished")


if __name__ == "__main__":
    main()
