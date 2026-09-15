#!/usr/bin/env python3

"""
Create project-archiving approval requests in ETA.

Source:
    inactive_projects.csv

Target:
    Jira project: ETA - ETS Tools Administration
    Service Desk ID: 1
    Request Type ID: 33
    Request Type: General Assistance
    Expected Issue Type: Jira Development
    Approvers field: customfield_12817

Processing rules:
    - Only rows with candidate=YES are processed.
    - Project lead accountId becomes the Approver.
    - Duplicate tickets are prevented.
    - The source project is not changed or archived.
    - DRY_RUN=True makes no Jira changes.

Required CSV columns:
    project_key
    project_name
    project_lead
    project_lead_accountid
    total_issues
    last_issue_created
    inactive_days
    candidate

Optional CSV columns:
    project_type
    project_created
    project_age_days
    last_issue_key
    candidate_reason

Install:
    pip install requests tqdm

Environment variables:
    JIRA_BASE_URL
    JIRA_EMAIL
    JIRA_API_TOKEN
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry


# =============================================================================
# CONFIGURATION
# =============================================================================

JIRA_BASE_URL = "https://example.atlassian.net/"
JIRA_EMAIL = "example@example.com"
JIRA_API_TOKEN = "Token"



# -----------------------------------------------------------------------------
# Input and output
# -----------------------------------------------------------------------------

INPUT_CSV = "inactive_projects.csv"

TIMESTAMP = datetime.now(
    timezone.utc
).strftime("%Y%m%d_%H%M%S")

OUTPUT_CSV = (
    f"eta_archive_ticket_results_{TIMESTAMP}.csv"
)

LOG_FILE = (
    f"eta_archive_ticket_creation_{TIMESTAMP}.log"
)


# -----------------------------------------------------------------------------
# Safety controls
# -----------------------------------------------------------------------------

# True:
#   Validate input, users, fields, request type, and duplicates.
#   Do not create or update tickets.
#
# False:
#   Create requests and update Approvers and labels.
DRY_RUN = False


# Optional limit for initial testing.
#
# None:
#   Process all candidate projects.
#
# 1:
#   Process only the first candidate.
MAX_PROJECTS_TO_PROCESS: Optional[int] = None


# Optional exact source project filter.
#
# Example:
#     ONLY_PROJECT_KEYS = {"ABC"}
#
# Empty set:
#     Process all candidates.
ONLY_PROJECT_KEYS: set[str] = set()


# Stop the execution after the first error.
# False is normally preferable for bulk reporting.
STOP_ON_FIRST_ERROR = False


# -----------------------------------------------------------------------------
# Jira Service Management configuration
# -----------------------------------------------------------------------------

TARGET_PROJECT_KEY = "ETA"

SERVICE_DESK_ID = "1"

REQUEST_TYPE_ID = "33"
EXPECTED_REQUEST_TYPE_NAME = "General Assistance"

EXPECTED_ISSUE_TYPE_NAME = "Jira Development"

APPROVERS_FIELD_ID = "customfield_12817"


# -----------------------------------------------------------------------------
# Candidate rules
# -----------------------------------------------------------------------------

CANDIDATE_VALUES = {
    "yes",
    "true",
    "1",
    "candidate",
}

REQUIRE_PROJECT_LEAD = True
REQUIRE_ACTIVE_PROJECT_LEAD = True

EXCLUDED_PROJECT_KEYS = {
    TARGET_PROJECT_KEY,

    # Add additional exclusions here:
    # "TEST",
    # "SANDBOX",
}


# -----------------------------------------------------------------------------
# Duplicate protection
# -----------------------------------------------------------------------------

MANAGED_LABEL = "jira-project-archive-review"
CANDIDATE_LABEL = "inactive-project-candidate"

CHECK_SUMMARY_FOR_DUPLICATES = True


# -----------------------------------------------------------------------------
# Description settings
# -----------------------------------------------------------------------------

HYGIENE_PAGE_URL = (
    "https://corelogic.atlassian.net/wiki/"
    "spaces/GOV/pages/3751816272"
)

HYGIENE_PAGE_TITLE = (
    "Jira Instance Hygiene and Lifecycle Management"
)


# -----------------------------------------------------------------------------
# Request behavior
# -----------------------------------------------------------------------------

REQUEST_TIMEOUT_SECONDS = 60
MAX_RETRIES = 5
REQUEST_DELAY_SECONDS = 0.20


# =============================================================================
# LOGGING
# =============================================================================

logger = logging.getLogger(
    "eta_archive_ticket_creator"
)

logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s"
)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

file_handler = logging.FileHandler(
    LOG_FILE,
    encoding="utf-8",
)

file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


# =============================================================================
# GENERAL HELPERS
# =============================================================================

def clean(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip()


def escape_jql(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )


def is_candidate(row: Dict[str, str]) -> bool:
    return (
        clean(row.get("candidate")).casefold()
        in CANDIDATE_VALUES
    )


def project_tracking_label(project_key: str) -> str:
    """
    Create a stable label for the source project.

    A hash avoids problems with spaces or unsupported characters in labels.
    """

    digest = hashlib.sha256(
        project_key.upper().encode("utf-8")
    ).hexdigest()[:16]

    return f"archive-project-{digest}"


def build_project_url(project_key: str) -> str:
    return (
        f"{JIRA_BASE_URL}/browse/"
        f"{quote(project_key, safe='')}"
    )


def build_ticket_url(ticket_key: str) -> str:
    return (
        f"{JIRA_BASE_URL}/browse/"
        f"{quote(ticket_key, safe='')}"
    )


def validate_configuration() -> None:
    errors: List[str] = []

    if not JIRA_BASE_URL:
        errors.append("JIRA_BASE_URL is missing")

    if not JIRA_EMAIL:
        errors.append("JIRA_EMAIL is missing")

    if not JIRA_API_TOKEN:
        errors.append("JIRA_API_TOKEN is missing")

    if not TARGET_PROJECT_KEY:
        errors.append("TARGET_PROJECT_KEY is missing")

    if not SERVICE_DESK_ID:
        errors.append("SERVICE_DESK_ID is missing")

    if not REQUEST_TYPE_ID:
        errors.append("REQUEST_TYPE_ID is missing")

    if not APPROVERS_FIELD_ID:
        errors.append("APPROVERS_FIELD_ID is missing")

    if (
        MAX_PROJECTS_TO_PROCESS is not None
        and MAX_PROJECTS_TO_PROCESS <= 0
    ):
        errors.append(
            "MAX_PROJECTS_TO_PROCESS must be greater than zero or None"
        )

    if errors:
        raise ValueError(
            "Configuration errors:\n- "
            + "\n- ".join(errors)
        )


# =============================================================================
# ATLASSIAN DOCUMENT FORMAT
# =============================================================================

def adf_text(
    value: Any,
    *,
    marks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:

    node: Dict[str, Any] = {
        "type": "text",
        "text": str(value),
    }

    if marks:
        node["marks"] = marks

    return node


def adf_paragraph(
    content: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:

    return {
        "type": "paragraph",
        "content": content or [],
    }


def adf_heading(
    value: str,
    level: int = 2,
) -> Dict[str, Any]:

    return {
        "type": "heading",
        "attrs": {
            "level": level,
        },
        "content": [
            adf_text(value),
        ],
    }


def adf_list_item(
    label: str,
    value: Any,
    *,
    link: Optional[str] = None,
) -> Dict[str, Any]:

    displayed_value = clean(value) or "Unavailable"

    content: List[Dict[str, Any]] = [
        adf_text(
            f"{label}: ",
            marks=[
                {
                    "type": "strong",
                }
            ],
        )
    ]

    if link:
        content.append(
            adf_text(
                displayed_value,
                marks=[
                    {
                        "type": "link",
                        "attrs": {
                            "href": link,
                        },
                    }
                ],
            )
        )
    else:
        content.append(
            adf_text(displayed_value)
        )

    return {
        "type": "listItem",
        "content": [
            adf_paragraph(content),
        ],
    }


def build_description(
    row: Dict[str, str],
) -> Dict[str, Any]:

    project_key = clean(
        row.get("project_key")
    )

    project_name = clean(
        row.get("project_name")
    )

    project_lead = (
        clean(row.get("project_lead"))
        or "No project lead"
    )

    total_issues = (
        clean(row.get("total_issues"))
        or "0"
    )

    last_issue_created = (
        clean(row.get("last_issue_created"))
        or "No work items found"
    )

    inactive_days = (
        clean(row.get("inactive_days"))
        or "Not applicable"
    )

    project_url = build_project_url(
        project_key
    )

    return {
        "version": 1,
        "type": "doc",
        "content": [
            adf_heading(
                "Project details",
                level=2,
            ),
            {
                "type": "bulletList",
                "content": [
                    adf_list_item(
                        "Project lead",
                        project_lead,
                    ),
                    adf_list_item(
                        "Project name",
                        project_name,
                    ),
                    adf_list_item(
                        "Project key",
                        project_key,
                    ),
                    adf_list_item(
                        "URL",
                        project_url,
                        link=project_url,
                    ),
                    adf_list_item(
                        "Total work items",
                        total_issues,
                    ),
                    adf_list_item(
                        "Last work item created",
                        last_issue_created,
                    ),
                    adf_list_item(
                        "Last created, days ago",
                        inactive_days,
                    ),
                ],
            },
            adf_paragraph(
                [
                    adf_text(
                        "As part of our regular maintenance and "
                        "to keep our Jira environment organized, "
                        "we plan to archive this project."
                    )
                ]
            ),
            adf_paragraph(
                [
                    adf_text(
                        "What “archival” means: "
                    ),
                    adf_text(
                        HYGIENE_PAGE_TITLE,
                        marks=[
                            {
                                "type": "link",
                                "attrs": {
                                    "href": HYGIENE_PAGE_URL,
                                },
                            }
                        ],
                    ),
                ]
            ),
            adf_paragraph(
                [
                    adf_text(
                        "Please approve archiving this project "
                        "or provide a reason why it should remain active."
                    )
                ]
            ),
        ],
    }


# =============================================================================
# JIRA CLIENT
# =============================================================================

class JiraClient:

    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
    ) -> None:

        self.base_url = base_url.rstrip("/")

        self.session = requests.Session()

        self.session.auth = (
            email,
            api_token,
        )

        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": (
                    "eta-archive-ticket-creator/2.0"
                ),
            }
        )

        retry = Retry(
            total=MAX_RETRIES,
            connect=MAX_RETRIES,
            read=MAX_RETRIES,
            status=MAX_RETRIES,
            backoff_factor=1.5,
            status_forcelist=(
                429,
                500,
                502,
                503,
                504,
            ),
            allowed_methods=frozenset(
                {
                    "GET",
                    "POST",
                    "PUT",
                    "DELETE",
                }
            ),
            respect_retry_after_header=True,
            raise_on_status=False,
        )

        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=10,
            pool_maxsize=10,
        )

        self.session.mount(
            "https://",
            adapter,
        )

        self.session.mount(
            "http://",
            adapter,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        expected_statuses: tuple[int, ...] = (200,),
    ) -> Any:

        url = f"{self.base_url}{path}"

        response = self.session.request(
            method=method,
            url=url,
            params=params,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        if response.status_code not in expected_statuses:

            request_id = (
                response.headers.get("X-AREQUESTID")
                or response.headers.get("Atl-Traceid")
                or ""
            )

            try:
                response_body = json.dumps(
                    response.json(),
                    ensure_ascii=False,
                )
            except ValueError:
                response_body = response.text

            raise RuntimeError(
                f"{method} {path} failed with HTTP "
                f"{response.status_code}. "
                f"Request ID: {request_id}. "
                f"Response: {response_body[:4000]}"
            )

        if (
            response.status_code == 204
            or not response.content
        ):
            return None

        try:
            return response.json()
        except ValueError:
            return response.text

    def get_myself(self) -> Dict[str, Any]:

        return self.request(
            "GET",
            "/rest/api/3/myself",
            expected_statuses=(200,),
        )

    def get_fields(self) -> List[Dict[str, Any]]:

        return self.request(
            "GET",
            "/rest/api/3/field",
            expected_statuses=(200,),
        )

    def get_user(
        self,
        account_id: str,
    ) -> Dict[str, Any]:

        return self.request(
            "GET",
            "/rest/api/3/user",
            params={
                "accountId": account_id,
            },
            expected_statuses=(200,),
        )

    def get_request_type(
        self,
    ) -> Dict[str, Any]:

        return self.request(
            "GET",
            (
                "/rest/servicedeskapi/servicedesk/"
                f"{quote(SERVICE_DESK_ID, safe='')}"
                "/requesttype/"
                f"{quote(REQUEST_TYPE_ID, safe='')}"
            ),
            expected_statuses=(200,),
        )

    def get_request_type_fields(
        self,
    ) -> Dict[str, Any]:

        return self.request(
            "GET",
            (
                "/rest/servicedeskapi/servicedesk/"
                f"{quote(SERVICE_DESK_ID, safe='')}"
                "/requesttype/"
                f"{quote(REQUEST_TYPE_ID, safe='')}"
                "/field"
            ),
            expected_statuses=(200,),
        )

    def find_existing_ticket_by_label(
        self,
        tracking_label: str,
    ) -> Optional[Dict[str, Any]]:

        jql = (
            f'project = "{escape_jql(TARGET_PROJECT_KEY)}" '
            f'AND labels = "{escape_jql(tracking_label)}" '
            "ORDER BY created DESC"
        )

        data = self.request(
            "GET",
            "/rest/api/3/search/jql",
            params={
                "jql": jql,
                "maxResults": 10,
                "fields": (
                    "summary,status,labels,created,"
                    f"{APPROVERS_FIELD_ID}"
                ),
            },
            expected_statuses=(200,),
        )

        issues = data.get("issues", [])

        if len(issues) > 1:
            logger.warning(
                "Multiple ETA tickets found with label %s. "
                "The newest ticket will be used.",
                tracking_label,
            )

        return issues[0] if issues else None

    def find_existing_ticket_by_summary(
        self,
        expected_summary: str,
    ) -> Optional[Dict[str, Any]]:

        words = [
            word
            for word in expected_summary.split()
            if word
        ]

        if not words:
            return None

        search_phrase = escape_jql(
            expected_summary
        )

        jql = (
            f'project = "{escape_jql(TARGET_PROJECT_KEY)}" '
            f'AND summary ~ "\\"{search_phrase}\\"" '
            "ORDER BY created DESC"
        )

        data = self.request(
            "GET",
            "/rest/api/3/search/jql",
            params={
                "jql": jql,
                "maxResults": 50,
                "fields": "summary,status,labels,created",
            },
            expected_statuses=(200,),
        )

        for issue in data.get("issues", []):

            actual_summary = clean(
                issue.get("fields", {}).get(
                    "summary"
                )
            )

            if (
                actual_summary.casefold()
                == expected_summary.casefold()
            ):
                return issue

        return None

    def create_customer_request(
        self,
        summary: str,
        description: Dict[str, Any],
    ) -> Dict[str, Any]:

        payload = {
            "serviceDeskId": SERVICE_DESK_ID,
            "requestTypeId": REQUEST_TYPE_ID,
            "isAdfRequest": True,
            "requestFieldValues": {
                "summary": summary,
                "description": description,
            },
        }

        return self.request(
            "POST",
            "/rest/servicedeskapi/request",
            payload=payload,
            expected_statuses=(201,),
        )

    def get_issue(
        self,
        issue_key: str,
    ) -> Dict[str, Any]:

        return self.request(
            "GET",
            (
                "/rest/api/3/issue/"
                f"{quote(issue_key, safe='')}"
            ),
            params={
                "fields": (
                    "summary,status,issuetype,labels,"
                    f"{APPROVERS_FIELD_ID}"
                ),
            },
            expected_statuses=(200,),
        )

    def update_approver_and_labels(
        self,
        issue_key: str,
        lead_account_id: str,
        labels_to_add: List[str],
    ) -> None:

        issue = self.get_issue(
            issue_key
        )

        existing_labels = (
            issue.get("fields", {}).get("labels")
            or []
        )

        merged_labels = sorted(
            set(existing_labels).union(
                labels_to_add
            )
        )

        payload = {
            "fields": {
                APPROVERS_FIELD_ID: [
                    {
                        "accountId": lead_account_id,
                    }
                ],
                "labels": merged_labels,
            }
        }

        self.request(
            "PUT",
            (
                "/rest/api/3/issue/"
                f"{quote(issue_key, safe='')}"
            ),
            payload=payload,
            expected_statuses=(204,),
        )


# =============================================================================
# INPUT CSV
# =============================================================================

def load_candidates(
    filename: str,
) -> List[Dict[str, str]]:

    if not os.path.isfile(filename):
        raise FileNotFoundError(
            f"Input CSV was not found: {filename}"
        )

    with open(
        filename,
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as csv_file:

        reader = csv.DictReader(csv_file)

        required_columns = {
            "project_key",
            "project_name",
            "project_lead",
            "project_lead_accountid",
            "total_issues",
            "last_issue_created",
            "inactive_days",
            "candidate",
        }

        actual_columns = {
            clean(column)
            for column in (reader.fieldnames or [])
        }

        missing_columns = (
            required_columns - actual_columns
        )

        if missing_columns:
            raise ValueError(
                "Input CSV is missing required columns: "
                + ", ".join(
                    sorted(missing_columns)
                )
            )

        rows = list(reader)

    candidates: List[Dict[str, str]] = []

    for row in rows:

        if not is_candidate(row):
            continue

        project_key = clean(
            row.get("project_key")
        ).upper()

        if (
            ONLY_PROJECT_KEYS
            and project_key not in {
                key.upper()
                for key in ONLY_PROJECT_KEYS
            }
        ):
            continue

        candidates.append(row)

    if MAX_PROJECTS_TO_PROCESS is not None:
        candidates = candidates[
            :MAX_PROJECTS_TO_PROCESS
        ]

    return candidates


# =============================================================================
# VALIDATION
# =============================================================================

def validate_request_type(
    client: JiraClient,
) -> None:

    request_type = client.get_request_type()

    actual_id = clean(
        request_type.get("id")
    )

    actual_name = clean(
        request_type.get("name")
    )

    if actual_id != REQUEST_TYPE_ID:
        raise RuntimeError(
            f"Expected request type ID "
            f"{REQUEST_TYPE_ID}, but Jira returned "
            f"{actual_id!r}."
        )

    if (
        actual_name.casefold()
        != EXPECTED_REQUEST_TYPE_NAME.casefold()
    ):
        raise RuntimeError(
            f"Expected request type "
            f"{EXPECTED_REQUEST_TYPE_NAME!r}, "
            f"but Jira returned {actual_name!r}."
        )

    logger.info(
        "Request type validated | "
        "serviceDeskId=%s | requestTypeId=%s | name=%s",
        SERVICE_DESK_ID,
        REQUEST_TYPE_ID,
        actual_name,
    )


def inspect_request_type_fields(
    client: JiraClient,
) -> None:
    """
    Log request-type fields.

    This function does not reject unknown required fields automatically
    because JSM can populate certain fields internally or through defaults.
    The create request remains Jira's authoritative validation.
    """

    data = client.get_request_type_fields()

    configured_fields = data.get(
        "requestTypeFields",
        []
    )

    if not configured_fields:
        logger.warning(
            "No requestTypeFields were returned for request type %s",
            REQUEST_TYPE_ID,
        )
        return

    for field in configured_fields:

        logger.info(
            "Request type field | id=%s | name=%s | required=%s",
            clean(field.get("fieldId")),
            clean(field.get("name")),
            bool(field.get("required")),
        )


def validate_approvers_field(
    client: JiraClient,
) -> None:

    all_fields = client.get_fields()

    approvers_field = next(
        (
            field
            for field in all_fields
            if field.get("id")
            == APPROVERS_FIELD_ID
        ),
        None,
    )

    if not approvers_field:
        raise RuntimeError(
            f"{APPROVERS_FIELD_ID} was not returned by "
            "GET /rest/api/3/field. Check the field ID, "
            "context, and permissions."
        )

    schema = (
        approvers_field.get("schema")
        or {}
    )

    logger.info(
        "Approvers field | name=%s | schema=%s",
        approvers_field.get("name"),
        json.dumps(
            schema,
            ensure_ascii=False,
        ),
    )

    if schema.get("type") != "array":
        raise RuntimeError(
            f"{APPROVERS_FIELD_ID} does not appear "
            "to be a multi-value field. Jira returned: "
            f"{json.dumps(schema, ensure_ascii=False)}"
        )


def validate_project_lead(
    client: JiraClient,
    account_id: str,
) -> Dict[str, Any]:

    user = client.get_user(
        account_id
    )

    if (
        REQUIRE_ACTIVE_PROJECT_LEAD
        and user.get("active") is not True
    ):
        raise RuntimeError(
            "Project lead Jira account is inactive"
        )

    return user


def verify_ticket(
    client: JiraClient,
    issue_key: str,
    lead_account_id: str,
    required_tracking_label: str,
) -> None:

    issue = client.get_issue(
        issue_key
    )

    fields = issue.get(
        "fields",
        {}
    )

    actual_issue_type = clean(
        (fields.get("issuetype") or {}).get(
            "name"
        )
    )

    if (
        actual_issue_type.casefold()
        != EXPECTED_ISSUE_TYPE_NAME.casefold()
    ):
        raise RuntimeError(
            f"{issue_key} has issue type "
            f"{actual_issue_type!r}. Expected "
            f"{EXPECTED_ISSUE_TYPE_NAME!r}."
        )

    labels = set(
        fields.get("labels") or []
    )

    if required_tracking_label not in labels:
        raise RuntimeError(
            f"Tracking label {required_tracking_label!r} "
            f"was not stored on {issue_key}."
        )

    approvers = (
        fields.get(APPROVERS_FIELD_ID)
        or []
    )

    approver_account_ids = {
        clean(approver.get("accountId"))
        for approver in approvers
        if isinstance(approver, dict)
    }

    if lead_account_id not in approver_account_ids:
        raise RuntimeError(
            f"The project lead was not stored in "
            f"{APPROVERS_FIELD_ID} on {issue_key}."
        )


# =============================================================================
# RESULT OUTPUT
# =============================================================================

RESULT_COLUMNS = [
    "project_key",
    "project_name",
    "project_type",
    "project_lead",
    "project_lead_accountid",
    "project_created",
    "project_age_days",
    "total_issues",
    "last_issue_key",
    "last_issue_created",
    "inactive_days",
    "candidate",
    "candidate_reason",
    "eta_ticket_key",
    "eta_ticket_url",
    "service_desk_id",
    "request_type_id",
    "request_type_name",
    "issue_type_name",
    "result",
    "details",
]


def write_results(
    rows: List[Dict[str, Any]],
    filename: str,
) -> None:

    with open(
        filename,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as csv_file:

        writer = csv.DictWriter(
            csv_file,
            fieldnames=RESULT_COLUMNS,
            extrasaction="ignore",
        )

        writer.writeheader()
        writer.writerows(rows)


# =============================================================================
# MAIN
# =============================================================================

def main() -> int:

    validate_configuration()

    logger.info("=" * 78)
    logger.info("ETA archive request creation started")
    logger.info("Jira site: %s", JIRA_BASE_URL)
    logger.info("Input CSV: %s", INPUT_CSV)
    logger.info("DRY_RUN: %s", DRY_RUN)
    logger.info("Target project: %s", TARGET_PROJECT_KEY)
    logger.info("Service Desk ID: %s", SERVICE_DESK_ID)
    logger.info("Request Type ID: %s", REQUEST_TYPE_ID)
    logger.info(
        "Request Type: %s",
        EXPECTED_REQUEST_TYPE_NAME,
    )
    logger.info(
        "Expected Issue Type: %s",
        EXPECTED_ISSUE_TYPE_NAME,
    )
    logger.info(
        "Approvers field: %s",
        APPROVERS_FIELD_ID,
    )
    logger.info("=" * 78)

    client = JiraClient(
        base_url=JIRA_BASE_URL,
        email=JIRA_EMAIL,
        api_token=JIRA_API_TOKEN,
    )

    current_user = client.get_myself()

    logger.info(
        "Authenticated as %s (%s)",
        current_user.get("displayName"),
        current_user.get("accountId"),
    )

    validate_request_type(client)
    inspect_request_type_fields(client)
    validate_approvers_field(client)

    candidates = load_candidates(
        INPUT_CSV
    )

    logger.info(
        "Candidate rows loaded: %s",
        len(candidates),
    )

    results: List[Dict[str, Any]] = []

    for row in tqdm(
        candidates,
        desc="Processing archive candidates",
        unit="project",
    ):

        project_key = clean(
            row.get("project_key")
        ).upper()

        project_name = clean(
            row.get("project_name")
        )

        csv_project_lead = clean(
            row.get("project_lead")
        )

        lead_account_id = clean(
            row.get("project_lead_accountid")
        )

        result_row: Dict[str, Any] = {
            **row,
            "eta_ticket_key": "",
            "eta_ticket_url": "",
            "service_desk_id": SERVICE_DESK_ID,
            "request_type_id": REQUEST_TYPE_ID,
            "request_type_name": (
                EXPECTED_REQUEST_TYPE_NAME
            ),
            "issue_type_name": (
                EXPECTED_ISSUE_TYPE_NAME
            ),
            "result": "",
            "details": "",
        }

        try:

            if not project_key:
                raise ValueError(
                    "Project key is missing"
                )

            if not project_name:
                raise ValueError(
                    "Project name is missing"
                )

            if project_key in {
                key.upper()
                for key in EXCLUDED_PROJECT_KEYS
            }:
                result_row["result"] = "SKIPPED"
                result_row["details"] = (
                    "Project key is excluded"
                )
                results.append(result_row)
                continue

            if (
                REQUIRE_PROJECT_LEAD
                and not lead_account_id
            ):
                result_row["result"] = "SKIPPED"
                result_row["details"] = (
                    "Project lead accountId is missing"
                )
                results.append(result_row)
                continue

            jira_lead = validate_project_lead(
                client,
                lead_account_id,
            )

            actual_lead_name = (
                clean(jira_lead.get("displayName"))
                or csv_project_lead
            )

            expected_summary = (
                f"Archiving {project_name}"
            )

            tracking_label = (
                project_tracking_label(
                    project_key
                )
            )

            labels_to_add = [
                MANAGED_LABEL,
                CANDIDATE_LABEL,
                tracking_label,
            ]

            logger.info(
                "%s | Checking | project=%s | approver=%s",
                project_key,
                project_name,
                actual_lead_name,
            )

            existing_ticket = (
                client.find_existing_ticket_by_label(
                    tracking_label
                )
            )

            if (
                existing_ticket is None
                and CHECK_SUMMARY_FOR_DUPLICATES
            ):
                existing_ticket = (
                    client.find_existing_ticket_by_summary(
                        expected_summary
                    )
                )

            if existing_ticket is not None:

                existing_key = clean(
                    existing_ticket.get("key")
                )

                existing_status = clean(
                    existing_ticket
                    .get("fields", {})
                    .get("status", {})
                    .get("name")
                )

                result_row["eta_ticket_key"] = (
                    existing_key
                )

                result_row["eta_ticket_url"] = (
                    build_ticket_url(existing_key)
                )

                if DRY_RUN:

                    result_row["result"] = (
                        "DUPLICATE_SKIPPED"
                    )

                    result_row["details"] = (
                        "Existing ETA ticket found; "
                        f"status={existing_status}"
                    )

                    results.append(result_row)
                    continue

                # Recovery behavior:
                # If an earlier execution created the request but failed
                # before applying Approvers or labels, repair it now.
                client.update_approver_and_labels(
                    issue_key=existing_key,
                    lead_account_id=lead_account_id,
                    labels_to_add=labels_to_add,
                )

                verify_ticket(
                    client=client,
                    issue_key=existing_key,
                    lead_account_id=lead_account_id,
                    required_tracking_label=tracking_label,
                )

                result_row["result"] = (
                    "EXISTING_VERIFIED"
                )

                result_row["details"] = (
                    "Existing ETA ticket found. "
                    "Approver, labels, request mapping, "
                    "and issue type were verified."
                )

                results.append(result_row)
                continue

            description = build_description(
                row
            )

            if DRY_RUN:

                result_row["result"] = "DRY_RUN"

                result_row["details"] = (
                    "Validated successfully. Would create "
                    "a General Assistance request in ETA "
                    f"and set {actual_lead_name} as Approver."
                )

                logger.info(
                    "%s | DRY RUN | Would create %s",
                    project_key,
                    expected_summary,
                )

                results.append(result_row)
                continue

            # Step 1: create the JSM customer request.
            created_request = (
                client.create_customer_request(
                    summary=expected_summary,
                    description=description,
                )
            )

            ticket_key = clean(
                created_request.get("issueKey")
                or created_request.get("key")
            )

            if not ticket_key:
                raise RuntimeError(
                    "JSM returned success but did not return "
                    "an issue key. Response: "
                    + json.dumps(
                        created_request,
                        ensure_ascii=False,
                    )
                )

            result_row["eta_ticket_key"] = (
                ticket_key
            )

            result_row["eta_ticket_url"] = (
                build_ticket_url(ticket_key)
            )

            logger.info(
                "%s | Request created | %s",
                project_key,
                ticket_key,
            )

            # Step 2: apply Approver and tracking labels.
            client.update_approver_and_labels(
                issue_key=ticket_key,
                lead_account_id=lead_account_id,
                labels_to_add=labels_to_add,
            )

            # Step 3: retrieve and verify the stored values.
            verify_ticket(
                client=client,
                issue_key=ticket_key,
                lead_account_id=lead_account_id,
                required_tracking_label=tracking_label,
            )

            result_row["result"] = "CREATED"

            result_row["details"] = (
                "General Assistance request created. "
                f"Issue type verified as "
                f"{EXPECTED_ISSUE_TYPE_NAME}. "
                f"Approver set to {actual_lead_name}. "
                "Tracking labels verified."
            )

            logger.info(
                "%s | Completed | %s | approver=%s",
                project_key,
                ticket_key,
                actual_lead_name,
            )

        except KeyboardInterrupt:

            result_row["result"] = "INTERRUPTED"
            result_row["details"] = (
                "Execution interrupted by user"
            )

            results.append(result_row)
            logger.warning("Execution interrupted by user")
            break

        except Exception as exc:

            logger.exception(
                "%s | Processing failed",
                project_key or "<missing-key>",
            )

            if result_row.get("eta_ticket_key"):

                result_row["result"] = (
                    "CREATED_UPDATE_FAILED"
                )

                result_row["details"] = (
                    "The ETA request was created, but a later "
                    "update or verification failed. The next run "
                    "will locate and repair the existing ticket. "
                    f"Error: {exc}"
                )

            else:

                result_row["result"] = "ERROR"
                result_row["details"] = str(exc)

            results.append(result_row)

            if STOP_ON_FIRST_ERROR:
                break

            continue

        time.sleep(
            REQUEST_DELAY_SECONDS
        )

    write_results(
        results,
        OUTPUT_CSV,
    )

    result_counts: Dict[str, int] = {}

    for row in results:
        result_name = clean(
            row.get("result")
        ) or "UNKNOWN"

        result_counts[result_name] = (
            result_counts.get(result_name, 0)
            + 1
        )

    logger.info("=" * 78)
    logger.info("Execution completed")
    logger.info(
        "Candidate rows processed: %s",
        len(results),
    )

    for result_name in sorted(result_counts):
        logger.info(
            "%s: %s",
            result_name,
            result_counts[result_name],
        )

    logger.info(
        "Result CSV: %s",
        OUTPUT_CSV,
    )

    logger.info(
        "Log file: %s",
        LOG_FILE,
    )

    logger.info("=" * 78)

    failure_statuses = {
        "ERROR",
        "CREATED_UPDATE_FAILED",
        "INTERRUPTED",
    }

    has_failures = any(
        clean(row.get("result"))
        in failure_statuses
        for row in results
    )

    return 1 if has_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
