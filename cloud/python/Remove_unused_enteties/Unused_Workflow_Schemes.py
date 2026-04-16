import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "https://domain.atlassian.net"
AUTH = ("email", "api_key")
HEADERS = {"Accept": "application/json"}

MAX_WORKERS = 8      
REQUEST_TIMEOUT = 15
APPLY = False          # ❗ CHANGE TO True TO DELETE


def get_all_workflow_schemes():
    schemes = []
    start_at = 0
    max_results = 50

    while True:
        resp = requests.get(f"{BASE_URL}/rest/api/3/workflowscheme", auth=AUTH, headers=HEADERS, params={"startAt": start_at, "maxResults": max_results},
            timeout=REQUEST_TIMEOUT )
        resp.raise_for_status()

        data = resp.json()
        schemes.extend(data["values"])

        if start_at + max_results >= data["total"]:
            break

        start_at += max_results

    return schemes


def is_orphaned_scheme(scheme):
    resp = requests.get(f"{BASE_URL}/rest/api/3/workflowscheme/{scheme['id']}/projectUsages", auth=AUTH, headers=HEADERS, params={"maxResults": 1},timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    projects = resp.json().get("projects", {}).get("values", [])
    return not projects, scheme


def delete_scheme(scheme):
    if not APPLY:
        print(f"[DRY‑RUN] Would delete scheme: {scheme['id']} | {scheme['name']}")
        return

    resp = requests.delete(
        f"{BASE_URL}/rest/api/3/workflowscheme/{scheme['id']}",
        auth=AUTH,
        timeout=REQUEST_TIMEOUT
    )

    if resp.status_code == 204:
        print(f"[DELETED] {scheme['id']} | {scheme['name']}")
    else:
        print(
            f"[FAILED] {scheme['id']} | {scheme['name']} "
            f"(HTTP {resp.status_code})"
        )


def main():
    schemes = get_all_workflow_schemes()
    total = len(schemes)
    print(f"Total workflow schemes fetched: {total}")

    orphaned = []

    # ✅ Phase 1: identify orphaned schemes (concurrent)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(is_orphaned_scheme, s) for s in schemes]

        for idx, future in enumerate(as_completed(futures), 1):
            is_orphaned, scheme = future.result()
            if is_orphaned:
                orphaned.append(scheme)

            if idx % 50 == 0 or idx == total:
                print(f"Checked {idx}/{total} schemes...")

    print(f"\nOrphaned workflow schemes found: {len(orphaned)}\n")

    # ✅ Phase 2: delete orphaned schemes (sequential on purpose)
    for scheme in orphaned:
        delete_scheme(scheme)


if __name__ == "__main__":
    main()
