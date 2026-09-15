#!/usr/bin/env python3

import requests
from requests.auth import HTTPBasicAuth

# ============================================================
# CONFIG
# ============================================================

JIRA_URL = "https://example.atlassian.net/"

EMAIL = "example@example.com"
API_TOKEN = "TOKEN"


DRY_RUN = False

TARGET_DATE = "Migrated on 2 Sep 2026"

# ============================================================
# SESSION
# ============================================================

session = requests.Session()
session.auth = HTTPBasicAuth(EMAIL, API_TOKEN)

session.headers.update({
    "Accept": "application/json",
    "Content-Type": "application/json"
})

# ============================================================
# FIND FIELDS
# ============================================================

def find_fields():
    start_at = 0
    max_results = 50

    matched = []

    while True:

        response = session.get(
            f"{JIRA_URL}/rest/api/3/field/search",
            params={
                "searchQuery": TARGET_DATE,
                "startAt": start_at,
                "maxResults": max_results
            }
        )

        response.raise_for_status()

        data = response.json()

        for field in data.get("values", []):

            description = field.get("description") or ""

            if TARGET_DATE in description:

                matched.append({
                    "id": field["id"],
                    "name": field["name"]
                })

        if data.get("isLast", True):
            break

        start_at += max_results

    return matched


# ============================================================
# trash
# ============================================================

def trash_field(field_id):

    response = session.post(
        f"{JIRA_URL}/rest/api/3/field/{field_id}/trash"
    )

    if response.status_code in (200, 204):
        print(f"✅ Moved to trash {field_id}")
    else:
        print(
            f"❌ Failed {field_id}: "
            f"{response.status_code} {response.text}"
        )


# ============================================================
# MAIN
# ============================================================

fields = find_fields()

print("\nMatched fields:\n")

for field in sorted(fields, key=lambda x: x["name"].lower()):
    print(f'{field["id"]} | {field["name"]}')

print(f"\nTotal: {len(fields)}")

if DRY_RUN:

    print("\nDRY RUN ENABLED - NOTHING DELETED")

else:

    print("\nMoved to trash fields...\n")

    for field in fields:

        print(
            f'Moved to trash {field["id"]} | {field["name"]}'
        )

        trash_field(field["id"])

    print("\nDone.")
