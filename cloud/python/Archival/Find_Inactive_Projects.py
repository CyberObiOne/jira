import csv
from datetime import datetime, timezone
import requests

# ============================================================================
# CONFIGURATION
# ============================================================================

JIRA_URL = "https://example.atlassian.net/"
EMAIL = "example@example.com"
API_TOKEN = "Token"


INACTIVE_DAYS_THRESHOLD = 180
EMPTY_PROJECT_MIN_AGE_DAYS = 90

OUTPUT_CSV = "inactive_projects.csv"

# ============================================================================

session = requests.Session()
session.auth = (EMAIL, API_TOKEN)

session.headers.update({
    "Accept": "application/json",
    "Content-Type": "application/json"
})


def get_all_projects():
    projects = []
    start_at = 0
    max_results = 50

    while True:

        response = session.get(
            f"{JIRA_URL}/rest/api/3/project/search",
            params={
                "startAt": start_at,
                "maxResults": max_results,
                "expand": "lead"
            }
        )

        response.raise_for_status()

        data = response.json()

        projects.extend(data["values"])

        if data.get("isLast", True):
            break

        start_at += len(data["values"])

    return projects


def get_issue_count(project_key):
    try:

        response = session.post(
            f"{JIRA_URL}/rest/api/3/search/approximate-count",
            json={
                "jql": f'project = "{project_key}"'
            }
        )

        response.raise_for_status()

        return response.json().get("count", 0)

    except Exception:
        return 0


def get_last_created_issue(project_key):

    response = session.get(
        f"{JIRA_URL}/rest/api/3/search/jql",
        params={
            "jql": f'project = "{project_key}" ORDER BY created DESC',
            "maxResults": 1,
            "fields": "created"
        }
    )

    response.raise_for_status()

    issues = response.json().get("issues", [])

    if not issues:
        return None

    return issues[0]


def parse_jira_date(date_str):

    if not date_str:
        return None

    return datetime.fromisoformat(
        date_str.replace("Z", "+00:00")
    )


# ============================================================================
# MAIN
# ============================================================================

today = datetime.now(timezone.utc)

projects = get_all_projects()

report = []

print(f"Found {len(projects)} projects.")

for project in projects:

    project_key = project["key"]
    project_name = project["name"]

    print(f"Processing {project_key}")

    lead = project.get("lead", {})

    project_created = (
        project.get("insight", {})
        .get("lastIssueUpdateTime")
    )

    project_age_days = ""

    if project_created:
        try:
            created_dt = parse_jira_date(project_created)

            project_age_days = (
                today - created_dt
            ).days

        except Exception:
            pass

    total_issues = get_issue_count(project_key)

    candidate = False
    candidate_reason = ""

    last_issue_created = ""
    last_issue_key = ""
    inactive_days = ""

    last_issue = get_last_created_issue(project_key)

    # ---------------------------------------------------------------------
    # Project has issues
    # ---------------------------------------------------------------------

    if last_issue:

        last_issue_key = last_issue["key"]

        last_issue_created = (
            last_issue["fields"]["created"]
        )

        created_dt = parse_jira_date(
            last_issue_created
        )

        inactive_days = (
            today - created_dt
        ).days

        if inactive_days >= INACTIVE_DAYS_THRESHOLD:

            candidate = True

            candidate_reason = (
                f"Inactive > {INACTIVE_DAYS_THRESHOLD} days"
            )

    # ---------------------------------------------------------------------
    # Empty project
    # ---------------------------------------------------------------------

    else:

        if (
            project_age_days != ""
            and project_age_days >= EMPTY_PROJECT_MIN_AGE_DAYS
        ):

            candidate = True

            candidate_reason = (
                f"Empty project older than "
                f"{EMPTY_PROJECT_MIN_AGE_DAYS} days"
            )

        elif project_age_days != "":

            candidate_reason = (
                f"Empty project younger than "
                f"{EMPTY_PROJECT_MIN_AGE_DAYS} days"
            )

        else:

            candidate_reason = (
                "Empty project (project age unavailable)"
            )

    report.append({
        "project_key": project_key,
        "project_name": project_name,
        "project_type": project.get("projectTypeKey"),
        "project_lead": lead.get("displayName"),
        "project_lead_accountid": lead.get("accountId"),
        "project_created": project_created,
        "project_age_days": project_age_days,
        "total_issues": total_issues,
        "last_issue_key": last_issue_key,
        "last_issue_created": last_issue_created,
        "inactive_days": inactive_days,
        "candidate": "YES" if candidate else "NO",
        "candidate_reason": candidate_reason
    })

# ============================================================================
# CSV OUTPUT
# ============================================================================

with open(
    OUTPUT_CSV,
    "w",
    newline="",
    encoding="utf-8-sig"
) as csvfile:

    writer = csv.DictWriter(
        csvfile,
        fieldnames=report[0].keys()
    )

    writer.writeheader()
    writer.writerows(report)

candidate_count = sum(
    1 for r in report
    if r["candidate"] == "YES"
)

print("")
print(f"Projects scanned : {len(report)}")
print(f"Candidates found : {candidate_count}")
print(f"CSV created      : {OUTPUT_CSV}")
