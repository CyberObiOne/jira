import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "https://domain.atlassian.net"
AUTH = ("email", "api_key")
HEADERS = {"Accept": "application/json"}

REQUEST_TIMEOUT = 15
MAX_WORKERS = 8
APPLY = False   # ❗ SET True TO DELETE


session = requests.Session()
session.auth = AUTH
session.headers.update(HEADERS)


# -----------------------------
# Get ALL statuses via SEARCH (paginated)
# -----------------------------
def get_all_statuses():
    statuses = []
    start_at = 0
    max_results = 50

    while True:
        resp = session.get(
            f"{BASE_URL}/rest/api/3/statuses/search",
            params={
                "startAt": start_at,
                "maxResults": max_results
            },
            timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()

        data = resp.json()
        statuses.extend(data.get("values", []))

        if start_at + max_results >= data.get("total", 0):
            break

        start_at += max_results

    return statuses


# -----------------------------
# Determine if status is unused
# -----------------------------
def is_unused_status(status):
    # ✅ Skip PROJECT scoped statuses
    scope_type = status.get("scope", {}).get("type")
    if scope_type == "PROJECT":
        return False, status

    status_id = status["id"]

    resp = session.get(
        f"{BASE_URL}/rest/api/3/statuses/{status_id}/workflowUsages",
        params={"maxResults": 1},
        timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()

    data = resp.json()
    workflows = data.get("workflows", {})
    values = workflows.get("values", [])
    next_page = workflows.get("nextPageToken")

    # ✅ UNUSED only if absolutely no usages
    is_unused = (len(values) == 0 and next_page is None)
    return is_unused, status


# -----------------------------
# Delete or DRY-RUN
# -----------------------------
def delete_status(status):
    status_id = status["id"]
    name = status["name"]

    if not APPLY:
        print(f"[DRY-RUN] Would delete status: {status_id} | {name}")
        return

    resp = session.delete(
        f"{BASE_URL}/rest/api/3/status/{status_id}",
        timeout=REQUEST_TIMEOUT
    )

    if resp.status_code == 204:
        print(f"[DELETED] {status_id} | {name}")
    else:
        print(f"[FAILED] {status_id} | {name} (HTTP {resp.status_code})")


# -----------------------------
# Main
# -----------------------------
def main():
    statuses = get_all_statuses()
    total = len(statuses)

    print(f"Total statuses fetched (search): {total}")

    unused_statuses = []
    completed = 0

    # ✅ Concurrent detection
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(is_unused_status, s)
            for s in statuses
        ]

        for future in as_completed(futures):
            completed += 1
            is_unused, status = future.result()

            if is_unused:
                unused_statuses.append(status)

            if completed % 25 == 0 or completed == total:
                print(f"Checked {completed}/{total} statuses...")

    print(f"\nUnused GLOBAL statuses found: {len(unused_statuses)}\n")

    # ✅ Sequential delete / dry-run
    for status in unused_statuses:
        delete_status(status)


if __name__ == "__main__":
    main()
