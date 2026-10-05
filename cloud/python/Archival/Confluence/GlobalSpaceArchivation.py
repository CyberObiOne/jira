import csv
import threading
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote, urljoin

import requests
from requests.auth import HTTPBasicAuth


# =============================================================================
# CONFIGURATION
# =============================================================================

# A trailing slash is allowed because it is removed automatically below.
BASE_URL = "https://example.atlassian.net"

EMAIL = "user@example.com"
API_TOKEN = "TOKEN"


# True:
#   Analyze spaces and create the CSV report only.
#
# False:
#   Analyze spaces, create the report, and archive candidates.
DRY_RUN = True

MAX_WORKERS = 10
REQUEST_TIMEOUT = 60

# Spaces younger than this are not archive candidates.
MIN_SPACE_AGE_DAYS = 90

CSV_OUTPUT_FILE = "../CleanUP/unused_global_spaces_report.csv"

# Missing metadata safety controls.
REQUIRE_CREATION_DATE = True
REQUIRE_OWNER_ID = False

# An owner with one of these statuses makes an eligible global space
# a candidate regardless of how many pages it contains.
INACTIVE_OWNER_STATUSES = {
    "inactive",
    "closed",
}

# For safety, an owner that cannot be retrieved is not automatically
# treated as inactive.
ARCHIVE_WHEN_OWNER_NOT_FOUND = False

# Optional owner filters.
#
# Empty INCLUDE_OWNER_ACCOUNT_IDS means all owners are eligible.
INCLUDE_OWNER_ACCOUNT_IDS = {
    # "712020:example-account-id",
}

# Owners whose spaces must never be archived.
EXCLUDE_OWNER_ACCOUNT_IDS = {
    # "712020:protected-account-id",
}

# Global spaces that must never be archived.
#
# Add system, administration, migration, legal, audit, or otherwise protected
# space keys here before APPLY mode.
EXCLUDE_SPACE_KEYS = {
    # "SYS",
    # "ADMIN",
    # "LEGAL",
}

# Default two-page pattern.
#
# Comparison is:
#   - case-insensitive
#   - whitespace-normalized
#   - exact
#
# Unlike the single-homepage rule, this pattern does not require version 1.
# Your personal-space output showed that Confluence can update these generated
# pages while the space still contains only the two default pages.
DEFAULT_TWO_PAGE_TITLES = {
    "overview",
    "getting started in confluence",
}

# Retry configuration.
MAX_RETRIES = 5

RETRYABLE_STATUS_CODES = {
    429,
    500,
    502,
    503,
    504,
}


# =============================================================================
# THREAD-LOCAL HTTP SESSION
# =============================================================================

_thread_local = threading.local()


def get_session():
    """
    Return one requests.Session per worker thread.

    A Session is not shared concurrently between worker threads.
    """

    if not hasattr(_thread_local, "session"):
        session = requests.Session()

        session.auth = HTTPBasicAuth(
            EMAIL,
            API_TOKEN,
        )

        session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

        _thread_local.session = session

    return _thread_local.session


# =============================================================================
# HTTP HELPERS
# =============================================================================

def request(method, url, **kwargs):
    """
    Send a request with retry handling for rate limiting and temporary
    server failures.
    """

    session = get_session()

    kwargs.setdefault(
        "timeout",
        REQUEST_TIMEOUT,
    )

    last_response = None

    for attempt in range(1, MAX_RETRIES + 1):
        response = session.request(
            method,
            url,
            **kwargs,
        )

        last_response = response

        if response.status_code not in RETRYABLE_STATUS_CODES:
            response.raise_for_status()
            return response

        retry_after = response.headers.get("Retry-After")

        if retry_after:
            try:
                sleep_seconds = float(retry_after)
            except ValueError:
                sleep_seconds = min(2 ** attempt, 30)
        else:
            sleep_seconds = min(2 ** attempt, 30)

        print(
            f"[RETRY] {method} {url} returned "
            f"{response.status_code}. "
            f"Sleeping {sleep_seconds} seconds."
        )

        time.sleep(sleep_seconds)

    response_text = (
        last_response.text
        if last_response is not None
        else "<no response>"
    )

    response_status = (
        last_response.status_code
        if last_response is not None
        else "<no status>"
    )

    raise RuntimeError(
        f"Request failed after {MAX_RETRIES} attempts: "
        f"{method} {url} | "
        f"{response_status} {response_text}"
    )


def normalize_next_url(next_link):
    """
    Convert a relative Confluence pagination URL into an absolute URL.
    """

    if not next_link:
        return None

    if next_link.startswith("http://"):
        return next_link

    if next_link.startswith("https://"):
        return next_link

    return urljoin(
        f"{BASE_URL}/",
        next_link.lstrip("/"),
    )


# =============================================================================
# DATE HELPERS
# =============================================================================

def parse_atlassian_datetime(value):
    """
    Parse an Atlassian ISO timestamp.

    Supported examples:
      2026-05-01T12:34:56.789Z
      2026-05-01T12:34:56Z
      2026-05-01T12:34:56+00:00
    """

    if not value:
        return None

    normalized = value.strip()

    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    parsed = datetime.fromisoformat(normalized)

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(
        timezone.utc
    )


def calculate_age_days(created_at):
    """
    Return the completed age of a space in days.
    """

    created_datetime = parse_atlassian_datetime(
        created_at
    )

    if not created_datetime:
        return None

    current_datetime = datetime.now(
        timezone.utc
    )

    return (
        current_datetime - created_datetime
    ).days


# =============================================================================
# CONFLUENCE SPACE API
# =============================================================================

def get_all_global_spaces():
    """
    Retrieve all current global spaces.

    GET /wiki/api/v2/spaces
      type=global
      status=current
    """

    spaces = []

    url = (
        f"{BASE_URL}/wiki/api/v2/spaces"
        f"?type=global"
        f"&status=current"
        f"&limit=250"
    )

    while url:
        response = request(
            "GET",
            url,
        )

        data = response.json()

        spaces.extend(
            data.get("results", [])
        )

        url = normalize_next_url(
            data.get("_links", {}).get("next")
        )

    return spaces


def get_space_details(space_id):
    """
    Retrieve complete details for one global space.
    """

    url = (
        f"{BASE_URL}/wiki/api/v2/spaces/"
        f"{space_id}"
    )

    response = request(
        "GET",
        url,
    )

    return response.json()


# =============================================================================
# CONFLUENCE PAGE API
# =============================================================================

def get_all_current_pages(space_id):
    """
    Retrieve every current page in a space.

    Full pagination is required because:
      - total_page_count must be accurate for every processed space
      - an inactive-owner space may contain any number of pages
      - default-content detection requires the exact page set
    """

    pages = []

    url = (
        f"{BASE_URL}/wiki/api/v2/spaces/"
        f"{space_id}/pages"
        f"?status=current"
        f"&limit=250"
    )

    while url:
        response = request(
            "GET",
            url,
        )

        data = response.json()

        pages.extend(
            data.get("results", [])
        )

        url = normalize_next_url(
            data.get("_links", {}).get("next")
        )

    return pages


# =============================================================================
# CONFLUENCE USER API
# =============================================================================

def get_owner_details(account_id):
    """
    Retrieve the space owner's user details.

    Returns a normalized dictionary even if no user can be found.
    """

    if not account_id:
        return {
            "account_id": None,
            "display_name": None,
            "public_name": None,
            "account_status": "unknown",
            "account_type": None,
            "found": False,
        }

    encoded_account_id = quote(
        account_id,
        safe="",
    )

    url = (
        f"{BASE_URL}/wiki/rest/api/user"
        f"?accountId={encoded_account_id}"
    )

    try:
        response = request(
            "GET",
            url,
        )

        user = response.json()

        if not user:
            return {
                "account_id": account_id,
                "display_name": None,
                "public_name": None,
                "account_status": "unknown",
                "account_type": None,
                "found": False,
            }

        return {
            "account_id": (
                user.get("accountId")
                or account_id
            ),
            "display_name": user.get(
                "displayName"
            ),
            "public_name": user.get(
                "publicName"
            ),
            "account_status": (
                user.get("accountStatus")
                or "unknown"
            ).strip().lower(),
            "account_type": user.get(
                "accountType"
            ),
            "found": True,
        }

    except requests.HTTPError as error:
        response = error.response

        if (
            response is not None
            and response.status_code == 404
        ):
            fallback_status = (
                "inactive"
                if ARCHIVE_WHEN_OWNER_NOT_FOUND
                else "unknown"
            )

            return {
                "account_id": account_id,
                "display_name": None,
                "public_name": None,
                "account_status": fallback_status,
                "account_type": None,
                "found": False,
            }

        raise


# =============================================================================
# ARCHIVE API
# =============================================================================

def archive_space(space_key):
    """
    Archive a global space.

    PUT /wiki/rest/api/space/{spaceKey}
    {
        "status": "archived"
    }
    """

    encoded_space_key = quote(
        space_key,
        safe="",
    )

    url = (
        f"{BASE_URL}/wiki/rest/api/space/"
        f"{encoded_space_key}"
    )

    payload = {
        "status": "archived",
    }

    response = request(
        "PUT",
        url,
        json=payload,
    )

    if response.status_code not in {
        200,
        202,
        204,
    }:
        raise RuntimeError(
            f"Archive failed for {space_key}: "
            f"{response.status_code} "
            f"{response.text}"
        )

    return response.status_code


# =============================================================================
# OWNER AND AGE FILTERS
# =============================================================================

def get_owner_id(space):
    """
    Prefer the current space owner.

    authorId is used only as a fallback when spaceOwnerId is unavailable.
    """

    return (
        space.get("spaceOwnerId")
        or space.get("authorId")
    )


def evaluate_owner_scope_filter(owner_id):
    """
    Apply the configured owner include and exclude filters.
    """

    if not owner_id:
        if REQUIRE_OWNER_ID:
            return (
                False,
                "Owner ID is missing",
            )

        return (
            True,
            "Owner ID is missing but allowed",
        )

    if owner_id in EXCLUDE_OWNER_ACCOUNT_IDS:
        return (
            False,
            "Owner is explicitly excluded",
        )

    if (
        INCLUDE_OWNER_ACCOUNT_IDS
        and owner_id not in INCLUDE_OWNER_ACCOUNT_IDS
    ):
        return (
            False,
            "Owner is not in the inclusion list",
        )

    return (
        True,
        "Owner scope filter passed",
    )


def evaluate_creation_date_filter(created_at):
    """
    Apply the creation-date and minimum-age safeguards.
    """

    if not created_at:
        if REQUIRE_CREATION_DATE:
            return (
                False,
                None,
                "Creation date is missing",
            )

        return (
            True,
            None,
            "Creation date is missing but allowed",
        )

    try:
        space_age_days = calculate_age_days(
            created_at
        )

    except (TypeError, ValueError) as error:
        return (
            False,
            None,
            f"Invalid creation date: {error}",
        )

    if space_age_days is None:
        return (
            False,
            None,
            "Could not calculate space age",
        )

    if space_age_days < 0:
        return (
            False,
            space_age_days,
            "Creation date is in the future",
        )

    if space_age_days < MIN_SPACE_AGE_DAYS:
        return (
            False,
            space_age_days,
            (
                f"Space is newer than "
                f"{MIN_SPACE_AGE_DAYS} days"
            ),
        )

    return (
        True,
        space_age_days,
        "Creation-date filter passed",
    )


# =============================================================================
# DEFAULT CONTENT DETECTION
# =============================================================================

def normalize_title(title):
    """
    Normalize a page title for reliable comparison.

    Example:
      '  Getting   Started in Confluence '
      becomes:
      'getting started in confluence'
    """

    return " ".join(
        (title or "")
        .strip()
        .casefold()
        .split()
    )


def page_is_version_one(page):
    """
    Return True when the current page version is exactly 1.
    """

    return (
        page.get("version", {}).get("number")
        == 1
    )


def evaluate_default_content(space, pages):
    """
    Determine whether a global space contains only default content.

    Candidate patterns:

    1. No current pages.

    2. Exactly one current page:
       - The page is the configured homepage.
       - The current page version is 1.

    3. Exactly two current pages:
       - The exact normalized title set is:
           Overview
           Getting started in Confluence

    For pattern 3, page version is intentionally not checked because
    generated default pages may have their versions changed automatically.
    """

    total_page_count = len(pages)

    # -------------------------------------------------------------------------
    # Pattern 1: empty space
    # -------------------------------------------------------------------------

    if total_page_count == 0:
        return (
            True,
            "EMPTY_SPACE",
            "No current pages",
        )

    # -------------------------------------------------------------------------
    # Pattern 2: unchanged configured homepage only
    # -------------------------------------------------------------------------

    if total_page_count == 1:
        page = pages[0]

        homepage_id = str(
            space.get("homepageId") or ""
        )

        page_id = str(
            page.get("id") or ""
        )

        page_title = (
            page.get("title")
            or "<untitled>"
        )

        page_version = (
            page.get("version", {}).get("number")
        )

        if (
            homepage_id
            and page_id == homepage_id
            and page_is_version_one(page)
        ):
            return (
                True,
                "DEFAULT_HOMEPAGE_ONLY",
                (
                    f"Only configured homepage "
                    f"'{page_title}' exists and "
                    f"remains at version 1"
                ),
            )

        return (
            False,
            None,
            (
                f"Space contains one page, but it is "
                f"not an unchanged configured homepage; "
                f"title='{page_title}', "
                f"version={page_version}"
            ),
        )

    # -------------------------------------------------------------------------
    # Pattern 3: exact default two-page set
    # -------------------------------------------------------------------------

    if total_page_count == 2:
        normalized_titles = {
            normalize_title(
                page.get("title")
            )
            for page in pages
        }

        if normalized_titles == DEFAULT_TWO_PAGE_TITLES:
            versions = sorted(
                str(
                    page.get(
                        "version",
                        {},
                    ).get(
                        "number",
                        "unknown",
                    )
                )
                for page in pages
            )

            return (
                True,
                "DEFAULT_TWO_PAGES",
                (
                    "Space contains only "
                    "'Overview' and "
                    "'Getting started in Confluence'; "
                    f"page versions={versions}"
                ),
            )

        actual_titles = sorted(
            page.get("title")
            or "<untitled>"
            for page in pages
        )

        return (
            False,
            None,
            (
                "Two pages do not match the exact "
                f"default-page title set: {actual_titles}"
            ),
        )

    # -------------------------------------------------------------------------
    # More than two pages
    # -------------------------------------------------------------------------

    return (
        False,
        None,
        (
            f"Space contains {total_page_count} "
            f"current pages"
        ),
    )


# =============================================================================
# RESULT TEMPLATE
# =============================================================================

def create_result(space_summary):
    """
    Create a consistent result object for successful and failed evaluations.
    """

    return {
        "id": space_summary.get("id"),
        "key": space_summary.get(
            "key",
            "<missing-key>",
        ),
        "name": space_summary.get(
            "name",
            "<missing-name>",
        ),
        "space_type": space_summary.get("type"),
        "space_status": space_summary.get("status"),
        "owner_id": None,
        "owner_display_name": None,
        "owner_public_name": None,
        "owner_account_status": "unknown",
        "owner_account_type": None,
        "owner_found": False,
        "created_at": None,
        "age_days": None,
        "homepage_id": None,
        "total_page_count": None,
        "page_titles": None,
        "candidate": False,
        "candidate_type": None,
        "reason": None,
        "error": None,
        "archive_attempted": False,
        "archive_result": None,
        "space_url": None,
    }


# =============================================================================
# GLOBAL SPACE EVALUATION
# =============================================================================

def evaluate_space(space_summary):
    """
    Evaluate one global space.

    Evaluation sequence:

    1. Retrieve space details.
    2. Retrieve owner details.
    3. Retrieve all pages and calculate total_page_count.
    4. Apply space-key, owner-scope, and minimum-age safeguards.
    5. Mark eligible inactive-owner spaces as candidates regardless of pages.
    6. Otherwise apply empty/default-content patterns.

    Page retrieval happens before candidate filtering so total_page_count is
    populated for every accessible space in the CSV report.
    """

    result = create_result(
        space_summary
    )

    try:
        space_id = space_summary.get("id")

        if not space_id:
            result["reason"] = "Space ID is missing"
            return result

        # ---------------------------------------------------------------------
        # Retrieve authoritative space details
        # ---------------------------------------------------------------------

        space = get_space_details(
            space_id
        )

        result["id"] = space.get("id")
        result["key"] = (
            space.get("key")
            or result["key"]
        )
        result["name"] = (
            space.get("name")
            or result["name"]
        )
        result["space_type"] = space.get(
            "type"
        )
        result["space_status"] = space.get(
            "status"
        )
        result["created_at"] = space.get(
            "createdAt"
        )
        result["homepage_id"] = space.get(
            "homepageId"
        )
        result["owner_id"] = get_owner_id(
            space
        )

        encoded_space_key_for_ui = quote(
            result["key"],
            safe="~",
        )

        result["space_url"] = (
            f"{BASE_URL}/wiki/spaces/"
            f"{encoded_space_key_for_ui}"
        )

        # ---------------------------------------------------------------------
        # Retrieve owner metadata
        # ---------------------------------------------------------------------

        owner = get_owner_details(
            result["owner_id"]
        )

        result["owner_display_name"] = (
            owner["display_name"]
        )
        result["owner_public_name"] = (
            owner["public_name"]
        )
        result["owner_account_status"] = (
            owner["account_status"]
        )
        result["owner_account_type"] = (
            owner["account_type"]
        )
        result["owner_found"] = (
            owner["found"]
        )

        # ---------------------------------------------------------------------
        # Retrieve every page before filtering
        # ---------------------------------------------------------------------

        pages = get_all_current_pages(
            space_id
        )

        result["total_page_count"] = len(
            pages
        )

        result["page_titles"] = " | ".join(
            sorted(
                (
                    page.get("title")
                    or "<untitled>"
                )
                for page in pages
            )
        )

        # ---------------------------------------------------------------------
        # Calculate age before filtering
        # ---------------------------------------------------------------------

        try:
            result["age_days"] = (
                calculate_age_days(
                    result["created_at"]
                )
            )
        except (TypeError, ValueError):
            result["age_days"] = None

        # ---------------------------------------------------------------------
        # Validate type and status
        # ---------------------------------------------------------------------

        if result["space_type"] != "global":
            result["reason"] = (
                f"Space type is "
                f"{result['space_type']!r}, "
                f"not 'global'"
            )
            return result

        if result["space_status"] != "current":
            result["reason"] = (
                f"Space status is "
                f"{result['space_status']!r}, "
                f"not 'current'"
            )
            return result

        # ---------------------------------------------------------------------
        # Explicit space exclusion
        # ---------------------------------------------------------------------

        if result["key"] in EXCLUDE_SPACE_KEYS:
            result["reason"] = (
                "Space key is explicitly excluded"
            )
            return result

        # ---------------------------------------------------------------------
        # Owner inclusion and exclusion filters
        # ---------------------------------------------------------------------

        (
            owner_allowed,
            owner_filter_reason,
        ) = evaluate_owner_scope_filter(
            result["owner_id"]
        )

        if not owner_allowed:
            result["reason"] = (
                owner_filter_reason
            )
            return result

        # ---------------------------------------------------------------------
        # Creation-date and minimum-age safeguards
        # ---------------------------------------------------------------------

        (
            creation_allowed,
            space_age_days,
            creation_reason,
        ) = evaluate_creation_date_filter(
            result["created_at"]
        )

        result["age_days"] = (
            space_age_days
        )

        if not creation_allowed:
            result["reason"] = (
                creation_reason
            )
            return result

        # ---------------------------------------------------------------------
        # Candidate rule 1:
        # Inactive/closed owner regardless of page count
        # ---------------------------------------------------------------------

        if (
            result["owner_account_status"]
            in INACTIVE_OWNER_STATUSES
        ):
            result["candidate"] = True
            result["candidate_type"] = (
                "INACTIVE_OWNER"
            )
            result["reason"] = (
                f"Owner account status is "
                f"'{result['owner_account_status']}'; "
                f"space contains "
                f"{result['total_page_count']} pages"
            )
            return result

        # ---------------------------------------------------------------------
        # Candidate rules 2-4:
        # Empty or default-only content
        # ---------------------------------------------------------------------

        (
            default_candidate,
            candidate_type,
            content_reason,
        ) = evaluate_default_content(
            space,
            pages,
        )

        result["candidate"] = (
            default_candidate
        )

        result["candidate_type"] = (
            candidate_type
            if default_candidate
            else None
        )

        result["reason"] = (
            content_reason
        )

        return result

    except Exception as error:
        result["error"] = str(error)
        result["reason"] = (
            f"ERROR: {type(error).__name__}: "
            f"{error}"
        )

        return result


# =============================================================================
# CSV REPORT
# =============================================================================

def write_csv_report(results):
    """
    Export all evaluated global spaces to one CSV file.

    utf-8-sig is used so Excel detects UTF-8 correctly.
    """

    fieldnames = [
        "space_id",
        "space_key",
        "space_name",
        "space_type",
        "space_status",
        "owner_id",
        "owner_display_name",
        "owner_public_name",
        "owner_account_status",
        "owner_account_type",
        "owner_found",
        "created_at",
        "age_days",
        "homepage_id",
        "total_page_count",
        "page_titles",
        "candidate",
        "candidate_type",
        "reason",
        "error",
        "archive_attempted",
        "archive_result",
        "space_url",
    ]

    with open(
        CSV_OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )

        writer.writeheader()

        for result in sorted(
            results,
            key=lambda item: (
                item.get("key") or ""
            ).casefold(),
        ):
            writer.writerow({
                "space_id": result.get("id"),
                "space_key": result.get("key"),
                "space_name": result.get("name"),
                "space_type": result.get(
                    "space_type"
                ),
                "space_status": result.get(
                    "space_status"
                ),
                "owner_id": result.get(
                    "owner_id"
                ),
                "owner_display_name": result.get(
                    "owner_display_name"
                ),
                "owner_public_name": result.get(
                    "owner_public_name"
                ),
                "owner_account_status": result.get(
                    "owner_account_status"
                ),
                "owner_account_type": result.get(
                    "owner_account_type"
                ),
                "owner_found": result.get(
                    "owner_found"
                ),
                "created_at": result.get(
                    "created_at"
                ),
                "age_days": result.get(
                    "age_days"
                ),
                "homepage_id": result.get(
                    "homepage_id"
                ),
                "total_page_count": result.get(
                    "total_page_count"
                ),
                "page_titles": result.get(
                    "page_titles"
                ),
                "candidate": result.get(
                    "candidate"
                ),
                "candidate_type": result.get(
                    "candidate_type"
                ),
                "reason": result.get(
                    "reason"
                ),
                "error": result.get(
                    "error"
                ),
                "archive_attempted": result.get(
                    "archive_attempted"
                ),
                "archive_result": result.get(
                    "archive_result"
                ),
                "space_url": result.get(
                    "space_url"
                ),
            })

    print(
        f"\nCSV report written: "
        f"{CSV_OUTPUT_FILE}"
    )


# =============================================================================
# CONSOLE OUTPUT
# =============================================================================

def print_result(prefix, result):
    """
    Print a concise evaluation result.
    """

    owner_value = (
        result.get("owner_display_name")
        or result.get("owner_id")
        or "UNKNOWN"
    )

    print(
        f"{prefix:<13} "
        f"{result.get('key')} | "
        f"{result.get('name')} | "
        f"owner={owner_value} | "
        f"owner_status="
        f"{result.get('owner_account_status')} | "
        f"pages="
        f"{result.get('total_page_count')} | "
        f"age="
        f"{result.get('age_days')} | "
        f"type="
        f"{result.get('candidate_type') or '-'} | "
        f"{result.get('reason')}"
    )


# =============================================================================
# CONFIGURATION VALIDATION
# =============================================================================

def validate_config():
    """
    Validate safety-critical configuration.
    """

    if not BASE_URL.startswith("https://"):
        raise ValueError(
            "BASE_URL must start with https://"
        )

    if BASE_URL.endswith("/"):
        raise ValueError(
            "Internal error: BASE_URL still has "
            "a trailing slash"
        )

    if not EMAIL:
        raise ValueError(
            "CONFLUENCE_EMAIL environment variable "
            "is not set"
        )

    if not API_TOKEN:
        raise ValueError(
            "CONFLUENCE_API_TOKEN environment variable "
            "is not set"
        )

    if MIN_SPACE_AGE_DAYS < 0:
        raise ValueError(
            "MIN_SPACE_AGE_DAYS cannot be negative"
        )

    if MAX_WORKERS < 1:
        raise ValueError(
            "MAX_WORKERS must be at least 1"
        )



    if not INACTIVE_OWNER_STATUSES:
        raise ValueError(
            "INACTIVE_OWNER_STATUSES cannot be empty"
        )


# =============================================================================
# MAIN
# =============================================================================

def main():
    validate_config()

    print("=" * 120)
    print(
        "CONFLUENCE UNUSED GLOBAL SPACE CLEANUP"
    )
    print("=" * 120)
    print(
        f"Mode: "
        f"{'DRY RUN' if DRY_RUN else 'APPLY'}"
    )
    print(
        f"Minimum space age: "
        f"{MIN_SPACE_AGE_DAYS} days"
    )
    print(
        f"Inactive owner statuses: "
        f"{sorted(INACTIVE_OWNER_STATUSES)}"
    )
    print(
        f"Workers: {MAX_WORKERS}"
    )
    print(
        f"CSV output: {CSV_OUTPUT_FILE}"
    )
    print(
        f"Explicitly excluded spaces: "
        f"{len(EXCLUDE_SPACE_KEYS)}"
    )
    print("=" * 120)

    spaces = get_all_global_spaces()

    print(
        f"\nFound {len(spaces)} "
        f"current global spaces.\n"
    )

    results = []
    candidates = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:
        future_map = {
            executor.submit(
                evaluate_space,
                space,
            ): space
            for space in spaces
        }

        completed = 0

        for future in as_completed(
            future_map
        ):
            completed += 1

            original_space = future_map[
                future
            ]

            try:
                result = future.result()

            except Exception as error:
                result = create_result(
                    original_space
                )

                result["error"] = str(error)
                result["reason"] = (
                    f"ERROR: "
                    f"{type(error).__name__}: "
                    f"{error}"
                )

            results.append(
                result
            )

            if result.get("error"):
                prefix = "[ERROR]"

            elif result.get("candidate"):
                prefix = "[CANDIDATE]"

                candidates.append(
                    result
                )

            else:
                prefix = "[KEEP]"

            print(
                f"[{completed}/{len(spaces)}] ",
                end="",
            )

            print_result(
                prefix,
                result,
            )

    candidates.sort(
        key=lambda item: (
            item.get("key") or ""
        ).casefold()
    )

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------

    inactive_owner_count = sum(
        1
        for item in candidates
        if item.get("candidate_type")
        == "INACTIVE_OWNER"
    )

    empty_space_count = sum(
        1
        for item in candidates
        if item.get("candidate_type")
        == "EMPTY_SPACE"
    )

    default_homepage_count = sum(
        1
        for item in candidates
        if item.get("candidate_type")
        == "DEFAULT_HOMEPAGE_ONLY"
    )

    default_two_pages_count = sum(
        1
        for item in candidates
        if item.get("candidate_type")
        == "DEFAULT_TWO_PAGES"
    )

    error_count = sum(
        1
        for item in results
        if item.get("error")
    )

    print("\n" + "=" * 120)
    print(
        f"TOTAL GLOBAL SPACES:   "
        f"{len(spaces)}"
    )
    print(
        f"ARCHIVE CANDIDATES:    "
        f"{len(candidates)}"
    )
    print(
        f"INACTIVE OWNER:        "
        f"{inactive_owner_count}"
    )
    print(
        f"EMPTY SPACE:           "
        f"{empty_space_count}"
    )
    print(
        f"DEFAULT HOMEPAGE:      "
        f"{default_homepage_count}"
    )
    print(
        f"DEFAULT TWO PAGES:     "
        f"{default_two_pages_count}"
    )
    print(
        f"ERRORS:                "
        f"{error_count}"
    )
    print("=" * 120)

    # -------------------------------------------------------------------------
    # Dry run
    # -------------------------------------------------------------------------

    if DRY_RUN:
        write_csv_report(
            results
        )

        print(
            "\nDRY_RUN=True: "
            "no global spaces were archived."
        )

        return

    # -------------------------------------------------------------------------
    # Apply
    # -------------------------------------------------------------------------

    print(
        "\nAPPLY mode enabled. "
        "Archiving global-space candidates...\n"
    )

    for index, candidate in enumerate(
        candidates,
        start=1,
    ):
        candidate["archive_attempted"] = True

        try:
            status_code = archive_space(
                candidate["key"]
            )

            candidate["archive_result"] = (
                f"Archived; HTTP {status_code}"
            )

            print(
                f"[{index}/{len(candidates)}] "
                f"[ARCHIVED] "
                f"{candidate['key']} | "
                f"{candidate['name']} | "
                f"{candidate['candidate_type']}"
            )

        except Exception as error:
            candidate["archive_result"] = (
                f"FAILED: "
                f"{type(error).__name__}: "
                f"{error}"
            )

            print(
                f"[{index}/{len(candidates)}] "
                f"[FAILED] "
                f"{candidate['key']} | "
                f"{candidate['name']} | "
                f"{error}"
            )

    # Write after archive processing so archive outcomes are recorded.
    write_csv_report(
        results
    )


if __name__ == "__main__":
    main()
