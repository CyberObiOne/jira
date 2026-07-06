import requests
import time
import html
from collections import defaultdict

# ============================================================
# CONFIG
# ============================================================

JIRA_URL = "https://example.atlassian.net/"
EMAIL = "username@example.com"
API_TOKEN = "API_TOKEN"


FIELD_ID = "customfield_64039"
CONTEXT_ID = "80569"

DRY_RUN = False
BATCH_SIZE = 100
MAX_RESULTS = 100

# ============================================================
# DATA: Parent -> Children
# ============================================================

DATA = {
    "API Management Framework": [
        "API Gateway",
        "API Query Language",
        "API Specification"
    ],
    "Application Security": [
        "AntiVirus Scan",
        "CAPTCHA",
        "Code Analysis",
        "Container Security",
        "Cookie Compliance",
        "File Management",
        "SSL Certificates Management",
        "Web Application Firewall"
    ]
}

# ============================================================
# HELPERS
# ============================================================

auth = (EMAIL, API_TOKEN)

headers = {
    "Accept": "application/json",
    "Content-Type": "application/json"
}

OPTIONS_URL = (
    f"{JIRA_URL}/rest/api/3/field/"
    f"{FIELD_ID}/context/{CONTEXT_ID}/option"
)


def normalize(value: str) -> str:
    """
    Normalizes values before sending to Jira.
    - Converts HTML entities, e.g. &amp; -> &
    - Trims whitespace
    """
    return html.unescape(str(value)).strip()


def key(value: str) -> str:
    """
    Case-insensitive comparison key.
    Jira option values are generally best treated as case-insensitive
    for duplicate-prevention purposes.
    """
    return normalize(value).casefold()


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def jira_request(method, url, **kwargs):
    """
    Small wrapper with basic retry handling for rate limits / temporary errors.
    """
    for attempt in range(1, 6):
        response = requests.request(
            method,
            url,
            auth=auth,
            headers=headers,
            **kwargs
        )

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "5"))
            print(f"Rate limited. Sleeping {retry_after}s...")
            time.sleep(retry_after)
            continue

        if response.status_code in (500, 502, 503, 504):
            sleep_seconds = attempt * 3
            print(
                f"Temporary Jira error {response.status_code}. "
                f"Retry {attempt}/5 after {sleep_seconds}s..."
            )
            time.sleep(sleep_seconds)
            continue

        return response

    return response


# ============================================================
# JIRA OPTION FUNCTIONS
# ============================================================

def get_existing_options():
    """
    Gets all options from the field context.

    Parent options do NOT have optionId.
    Child options DO have optionId, which points to the parent option ID.
    """
    print("\n=== Fetching existing Jira options ===")

    all_options = []
    start_at = 0

    while True:
        params = {
            "startAt": start_at,
            "maxResults": MAX_RESULTS
        }

        response = jira_request("GET", OPTIONS_URL, params=params)

        if response.status_code != 200:
            raise RuntimeError(
                f"Failed to fetch existing options: "
                f"{response.status_code} {response.text}"
            )

        data = response.json()
        values = data.get("values", [])
        all_options.extend(values)

        print(f"Fetched {len(values)} options from startAt={start_at}")

        if data.get("isLast", True):
            break

        start_at += data.get("maxResults", MAX_RESULTS)

    print(f"Total existing options fetched: {len(all_options)}")
    return all_options


def build_existing_maps(existing_options):
    """
    Builds lookup maps:
    - parent_by_key: parent name -> parent option object
    - children_by_parent_id: parent option ID -> set(child names)
    """
    parent_by_key = {}
    children_by_parent_id = defaultdict(set)

    for option in existing_options:
        value = normalize(option.get("value", ""))
        option_id = str(option.get("id"))

        parent_id = option.get("optionId")

        if parent_id:
            children_by_parent_id[str(parent_id)].add(key(value))
        else:
            parent_by_key[key(value)] = {
                "id": option_id,
                "value": value
            }

    return parent_by_key, children_by_parent_id


def create_options(options_payload, label):
    """
    Creates options in Jira.
    """
    if not options_payload:
        print(f"No {label} to create.")
        return []

    created = []

    for batch_number, batch in enumerate(chunks(options_payload, BATCH_SIZE), 1):
        payload = {
            "options": batch
        }

        print(f"\n[{label} batch {batch_number}] Count: {len(batch)}")

        if DRY_RUN:
            print("DRY_RUN payload:")
            print(payload)
            continue

        response = jira_request("POST", OPTIONS_URL, json=payload)

        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Failed creating {label}: "
                f"{response.status_code} {response.text}"
            )

        response_data = response.json()
        created_batch = response_data.get("options", [])
        created.extend(created_batch)

        print(f"Created {len(created_batch)} {label}")

    return created


# ============================================================
# MAIN LOGIC
# ============================================================

def main():
    print("==============================================")
    print("Jira Cascading Select Option Loader")
    print("==============================================")
    print(f"Jira URL   : {JIRA_URL}")
    print(f"Field ID   : {FIELD_ID}")
    print(f"Context ID : {CONTEXT_ID}")
    print(f"Dry run    : {DRY_RUN}")
    print("==============================================")

    # Normalize + dedupe input
    normalized_data = {}

    for parent, children in DATA.items():
        parent_clean = normalize(parent)

        seen_children = set()
        clean_children = []

        for child in children:
            child_clean = normalize(child)

            if not child_clean:
                continue

            child_key = key(child_clean)

            if child_key in seen_children:
                print(
                    f"Input duplicate skipped under parent "
                    f"'{parent_clean}': {child_clean}"
                )
                continue

            seen_children.add(child_key)
            clean_children.append(child_clean)

        normalized_data[parent_clean] = clean_children

    total_parents = len(normalized_data)
    total_children = sum(len(v) for v in normalized_data.values())

    print(f"\nInput parents : {total_parents}")
    print(f"Input children: {total_children}")

    # Fetch current Jira state
    existing_options = get_existing_options()
    parent_by_key, children_by_parent_id = build_existing_maps(existing_options)

    # --------------------------------------------------------
    # STEP 1: CREATE MISSING PARENTS
    # --------------------------------------------------------

    parent_payload = []

    for parent in normalized_data.keys():
        if key(parent) not in parent_by_key:
            parent_payload.append({
                "value": parent,
                "disabled": False
            })

    print(f"\nMissing parents to create: {len(parent_payload)}")

    created_parents = create_options(parent_payload, "parent options")

    if DRY_RUN:
        print("\nDRY_RUN enabled, skipping child creation because real parent IDs are not available.")
        print("Set DRY_RUN = False after validating the parent payload.")
        return

    # Update parent map with newly created parents
    for parent in created_parents:
        parent_value = normalize(parent.get("value", ""))
        parent_by_key[key(parent_value)] = {
            "id": str(parent.get("id")),
            "value": parent_value
        }

    # Safety: refetch everything after parent creation
    existing_options = get_existing_options()
    parent_by_key, children_by_parent_id = build_existing_maps(existing_options)

    # --------------------------------------------------------
    # STEP 2: CREATE MISSING CHILDREN
    # --------------------------------------------------------

    child_payload = []

    for parent, children in normalized_data.items():
        parent_lookup = parent_by_key.get(key(parent))

        if not parent_lookup:
            raise RuntimeError(
                f"Parent option was not found after creation: {parent}"
            )

        parent_id = str(parent_lookup["id"])
        existing_child_keys = children_by_parent_id.get(parent_id, set())

        for child in children:
            if key(child) in existing_child_keys:
                print(f"Already exists, skipping: {parent} -> {child}")
                continue

            child_payload.append({
                "value": child,
                "optionId": parent_id,
                "disabled": False
            })

    print(f"\nMissing child options to create: {len(child_payload)}")

    create_options(child_payload, "child options")

    print("\n==============================================")
    print("Completed")
    print("==============================================")


if __name__ == "__main__":
    main()
