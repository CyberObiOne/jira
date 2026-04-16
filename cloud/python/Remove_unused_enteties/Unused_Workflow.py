import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "https://domain.atlassian.net"
AUTH = ("email", "api_key")
HEADERS = {"Accept": "application/json"}

MAX_WORKERS = 10
REQUEST_TIMEOUT = 15
APPLY = False   # ❗ CHANGE TO True TO DELETE


session = requests.Session()
session.auth = AUTH
session.headers.update(HEADERS)


def get_all_workflows():
    workflows = []
    start_at = 0
    max_results = 50

    while True:
        resp = session.get(
            f"{BASE_URL}/rest/api/3/workflow/search",
            params={"startAt": start_at, "maxResults": max_results},
            timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()

        data = resp.json()
        workflows.extend(data["values"])

        if start_at + max_results >= data["total"]:
            break

        start_at += max_results

    return workflows


def workflow_id(wf):
    return wf["id"]["entityId"]


def workflow_name(wf):
    return wf["id"].get("name", "<no-name>")


def is_orphaned_workflow(wf):
    wf_id = workflow_id(wf)

    resp = session.get(
        f"{BASE_URL}/rest/api/3/workflow/{wf_id}/workflowSchemes",
        timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()

    schemes = resp.json().get("workflowSchemes", {}).get("values", [])
    return not schemes, wf


def delete_workflow(wf):
    wf_id = workflow_id(wf)
    name = workflow_name(wf)

    if not APPLY:
        print(f"[DRY-RUN] Would delete workflow: {wf_id} | {name}")
        return

    resp = session.delete(
        f"{BASE_URL}/rest/api/3/workflow/{wf_id}",
        timeout=REQUEST_TIMEOUT
    )

    if resp.status_code == 204:
        print(f"[DELETED] {wf_id} | {name}")
    else:
        print(f"[FAILED] {wf_id} | {name} (HTTP {resp.status_code})")


def main():
    workflows = get_all_workflows()
    total = len(workflows)

    print(f"Total workflows fetched: {total}")

    orphaned = []
    completed = 0

    # ✅ Phase 1: identify orphaned workflows (concurrent)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(is_orphaned_workflow, wf)
            for wf in workflows
        ]

        for future in as_completed(futures):
            completed += 1
            is_orphaned, wf = future.result()

            if is_orphaned:
                orphaned.append(wf)

            if completed % 50 == 0 or completed == total:
                print(f"Checked {completed}/{total} workflows...")

    print(f"\nOrphaned workflows found: {len(orphaned)}\n")

    # ✅ Phase 2: delete (or dry-run)
    for wf in orphaned:
        delete_workflow(wf)


if __name__ == "__main__":
    main()
