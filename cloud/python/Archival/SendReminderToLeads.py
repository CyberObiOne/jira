import json
from urllib.parse import quote

import requests

# ============================================================
# CONFIGURATION
# ============================================================

JIRA_URL = "https://example.atlassian.net/"
EMAIL = "user@example.com"
API_TOKEN = "Token"


DRY_RUN = False

APPROVERS_FIELD = "customfield_12817"

JQL = (
    'project = ETA '
    'AND labels = "jira-project-archive-review" '
    'AND statusCategory != Done'
)

COMMENT_TEXT = (
    "Final Notice – Project Archiving "
    "We have sent multiple notifications requesting your review and approval of this archive request and have not received a response."
    "As no business justification has been provided to keep this project active, we will proceed with archiving the project."
    "What happens next:"
    "• The project will be archived in the current Jira instance."
    "• The project will remain available in archived status for the next 180 days."
    "• During this period, the project can be restored at any time upon request."
    "• After 180 days, the project will be migrated to the archive Jira instance where it will remain available in read-only mode for historical and audit purposes.\n\n"
    "If you believe this project should remain active, please comment on this request immediately and provide the business justification. \n\n"
    "Thank you."
)

# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.auth = (
    EMAIL,
    API_TOKEN,
)

session.headers.update({
    "Accept": "application/json",
    "Content-Type": "application/json",
})


# ============================================================
# HELPERS
# ============================================================

def jira_get(url, **kwargs):
    response = session.get(url, **kwargs)
    response.raise_for_status()
    return response.json()


def jira_post(url, payload):
    response = session.post(
        url,
        json=payload,
    )
    response.raise_for_status()
    return response.json()


# ============================================================
# SEARCH TICKETS
# ============================================================

def get_archive_tickets():

    issues = []
    next_page_token = None

    while True:

        params = {
            "jql": JQL,
            "maxResults": 100,
            "fields": (
                "summary,"
                "status,"
                f"{APPROVERS_FIELD}"
            )
        }

        if next_page_token:
            params["nextPageToken"] = next_page_token

        data = jira_get(
            f"{JIRA_URL}/rest/api/3/search/jql",
            params=params,
        )

        issues.extend(
            data.get("issues", [])
        )

        next_page_token = data.get(
            "nextPageToken"
        )

        if not next_page_token:
            break

    return issues


# ============================================================
# COMMENT BUILDER
# ============================================================

def build_comment(approvers):

    paragraph_content = []

    for approver in approvers:

        paragraph_content.append({
            "type": "mention",
            "attrs": {
                "id": approver["accountId"]
            }
        })

        paragraph_content.append({
            "type": "text",
            "text": " "
        })

    paragraph_content.append({
        "type": "text",
        "text": COMMENT_TEXT
    })

    return {
        "body": {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": paragraph_content
                }

            ]
        }
    }



# ============================================================
# MAIN
# ============================================================

issues = get_archive_tickets()

print(
    f"Found {len(issues)} archive tickets."
)

for issue in issues:

    key = issue["key"]

    fields = issue["fields"]

    summary = fields.get(
        "summary",
        ""
    )

    status = (
        fields.get("status", {})
        .get("name", "")
    )

    approvers = (
        fields.get(APPROVERS_FIELD)
        or []
    )

    print(
        f"\n{key}"
    )

    print(
        f"Summary: {summary}"
    )

    print(
        f"Status: {status}"
    )

    # DEBUG OUTPUT
    print(
        "Approvers field:"
    )

    print(
        json.dumps(
            approvers,
            indent=2
        )
    )

    if not approvers:

        print(
            "Skipping: no approvers found."
        )

        continue

    if DRY_RUN:

        print(
            f"DRY RUN: would notify "
            f"{len(approvers)} approver(s)."
        )

        continue

    payload = build_comment(
        approvers
    )

    jira_post(
        (
            f"{JIRA_URL}"
            f"/rest/api/3/issue/"
            f"{quote(key)}/comment"
        ),
        payload,
    )

    print(
        "Reminder comment added."
    )

print("\nDone.")
