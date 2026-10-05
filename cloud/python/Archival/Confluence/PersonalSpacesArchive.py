import csv
import os
import threading
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from requests.auth import HTTPBasicAuth


# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_URL = "https://example.atlassian.net/"

EMAIL = "user@example.com"
API_TOKEN = "TOKEN"


# True:
#   Detect candidates and create the CSV report only.
#
# False:
#   Detect candidates, create the CSV report, and archive candidates.
DRY_RUN = True

MAX_WORKERS = 10
REQUEST_TIMEOUT = 60

# Spaces younger than this are excluded from archiving.
MIN_SPACE_AGE_DAYS = 90

CSV_OUTPUT_FILE = "../unused_personal_spaces_report.csv"

# If True, a missing or invalid creation date prevents archiving.
REQUIRE_CREATION_DATE = True

# If True, a missing owner ID prevents archiving.
REQUIRE_OWNER_ID = True

# An owner with one of these statuses makes the space a candidate,
# regardless of how many pages the space contains.
INACTIVE_OWNER_STATUSES = {
    "inactive",
    "closed",
}

# If the user endpoint cannot return an owner, treat the owner as unknown.
# For safety, unknown owners are not treated as inactive.
ARCHIVE_WHEN_OWNER_NOT_FOUND = False

# Optional owner inclusion filter.
# Empty means that all owners are eligible.
INCLUDE_OWNER_ACCOUNT_IDS = {
    # "712020:example-account-id",
}

# Owners that must never have their spaces archived.
EXCLUDE_OWNER_ACCOUNT_IDS = {
    # "712020:protected-account-id",
}

# Space keys that must never be archived.
EXCLUDE_SPACE_KEYS = {
    # "~712020protected",
}

# Default page titles are matched case-insensitively.
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
    Send a request with retry handling for rate limits and temporary failures.
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

    raise RuntimeError(
        f"Request failed after {MAX_RETRIES} attempts: "
        f"{method} {url} | "
        f"{last_response.status_code} {last_response.text}"
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
        BASE_URL,
        next_link,
    )


# =============================================================================
# DATE HELPERS
# =============================================================================

def parse_atlassian_datetime(value):
    """
    Parse an Atlassian ISO timestamp.
    """

    if not value:
        return None

    normalized = value.strip()

    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    parsed = datetime.fromisoformat(normalized)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


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

def get_all_personal_spaces():
    """
    Retrieve all current personal spaces.
    """

    spaces = []

    url = (
        f"{BASE_URL}/wiki/api/v2/spaces"
        f"?type=personal"
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
    Retrieve complete details for one space.
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
    Retrieve all current pages for a space.

    Full pagination is required because total_page_count is written to CSV
    and inactive-owner spaces may contain any number of pages.
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
    Retrieve owner details.

    Returns fields including:
      - accountId
      - displayName
      - publicName
      - accountStatus
      - accountType

    If the owner is not found and ARCHIVE_WHEN_OWNER_NOT_FOUND is False,
    the status is returned as 'unknown'.
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

    url = (
        f"{BASE_URL}/wiki/rest/api/user"
        f"?accountId={account_id}"
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
            "account_id": user.get("accountId") or account_id,
            "display_name": user.get("displayName"),
            "public_name": user.get("publicName"),
            "account_status": (
                user.get("accountStatus") or "unknown"
            ).lower(),
            "account_type": user.get("accountType"),
            "found": True,
        }

    except requests.HTTPError as error:
        response = error.response

        if response is not None and response.status_code == 404:
            status = (
                "inactive"
                if ARCHIVE_WHEN_OWNER_NOT_FOUND
                else "unknown"
            )

            return {
                "account_id": account_id,
                "display_name": None,
                "public_name": None,
                "account_status": status,
                "account_type": None,
                "found": False,
            }

        raise


# =============================================================================
# ARCHIVE API
# =============================================================================

def archive_space(space_key):
    """
    Archive a personal space.
    """

    url = (
        f"{BASE_URL}/wiki/rest/api/space/"
        f"{space_key}"
    )

    payload = {
        "type": "personal",
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
# FILTERS
# =============================================================================

def get_owner_id(space):
    """
    Prefer spaceOwnerId and use authorId only as a fallback.
    """

    return (
        space.get("spaceOwnerId")
        or space.get("authorId")
    )


def evaluate_owner_scope_filter(owner_id):
    """
    Apply configured owner include/exclude filters.
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

    except ValueError as error:
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
    Normalize a page title for case-insensitive comparison.
    """

    return " ".join(
        (title or "").strip().casefold().split()
    )


def page_is_version_one(page):
    """
    Return True only when the page's current version number is 1.
    """

    return (
        page.get("version", {}).get("number")
        == 1
    )


def evaluate_default_content(space, pages):
    """
    Evaluate whether a space contains only default content.

    Candidate patterns:

    1. No current pages.

    2. Exactly one page:
       - It is the configured homepage.
       - Its version number is 1.

    3. Exactly two pages:
       - Their normalized titles are exactly:
           Overview
           Getting started in Confluence
       - Both pages remain at version 1.

    The two-page pattern intentionally requires an exact title set.
    Spaces containing any additional current page do not match it.
    """

    total_page_count = len(pages)

    if total_page_count == 0:
        return (
            True,
            "EMPTY_SPACE",
            "No current pages",
        )

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
            "SINGLE_NON_DEFAULT_PAGE",
            (
                "Space contains one page, but it is "
                "not an unchanged configured homepage"
            ),
        )

    if total_page_count == 2:
        normalized_titles = {
            normalize_title(page.get("title"))
            for page in pages
        }

        all_pages_are_version_one = all(
            page_is_version_one(page)
            for page in pages
        )

        if (
            normalized_titles
            == DEFAULT_TWO_PAGE_TITLES
            and all_pages_are_version_one
        ):
            return (
                True,
                "DEFAULT_TWO_PAGES",
                (
                    "Space contains only unchanged "
                    "'Overview' and "
                    "'Getting started in Confluence from Jira' pages"
                ),
            )

        actual_titles = sorted(
            page.get("title") or "<untitled>"
            for page in pages
        )

        return (
            False,
            "TWO_NON_DEFAULT_PAGES",
            (
                "Two pages do not match the unchanged "
                f"default-page pattern: {actual_titles}"
            ),
        )

    return (
        False,
        "HAS_ADDITIONAL_CONTENT",
        (
            f"Space contains {total_page_count} "
            f"current pages"
        ),
    )


# =============================================================================
# SPACE EVALUATION
# =============================================================================

def evaluate_space(space_summary):
    """
    Evaluate one personal space.

    Candidate precedence:

    1. Explicit safety filters
    2. Minimum-age filter
    3. Inactive/closed owner, regardless of page count
    4. Default-content detection
    """

    result = {
        "id": space_summary.get("id"),
        "key": space_summary.get(
            "key",
            "<missing-key>",
        ),
        "name": space_summary.get(
            "name",
            "<missing-name>",
        ),
        "owner_id": None,
        "owner_display_name": None,
        "owner_public_name": None,
        "owner_account_status": "unknown",
        "owner_account_type": None,
        "owner_found": False,
        "created_at": None,
        "age_days": None,
        "total_page_count": None,
        "page_titles": None,
        "candidate": False,
        "candidate_type": None,
        "reason": None,
        "error": None,
        "space_url": None,
        "archive_attempted": False,
        "archive_result": None,
    }

    try:
        space_id = space_summary.get("id")

        if not space_id:
            result["reason"] = "Space ID is missing"
            return result

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
        result["created_at"] = space.get(
            "createdAt"
        )
        result["owner_id"] = get_owner_id(
            space
        )

        result["space_url"] = (
            f"{BASE_URL}/wiki/spaces/"
            f"{result['key']}"
        )

        # ---------------------------------------------------------------------
        # Validate space type and status
        # ---------------------------------------------------------------------

        if space.get("type") != "personal":
            result["reason"] = (
                f"Space type is "
                f"{space.get('type')!r}, "
                f"not 'personal'"
            )
            return result

        if space.get("status") != "current":
            result["reason"] = (
                f"Space status is "
                f"{space.get('status')!r}, "
                f"not 'current'"
            )
            return result

        # ---------------------------------------------------------------------
        # Explicit exclusions
        # ---------------------------------------------------------------------

        if result["key"] in EXCLUDE_SPACE_KEYS:
            result["reason"] = (
                "Space key is explicitly excluded"
            )
            return result

        # ---------------------------------------------------------------------
        # Owner scope filters
        # ---------------------------------------------------------------------

        owner_allowed, owner_filter_reason = (
            evaluate_owner_scope_filter(
                result["owner_id"]
            )
        )

        if not owner_allowed:
            result["reason"] = owner_filter_reason
            return result

        # ---------------------------------------------------------------------
        # Creation date and minimum age
        # ---------------------------------------------------------------------

        (
            creation_allowed,
            space_age_days,
            creation_reason,
        ) = evaluate_creation_date_filter(
            result["created_at"]
        )

        result["age_days"] = space_age_days

        if not creation_allowed:
            result["reason"] = creation_reason
            return result

        # ---------------------------------------------------------------------
        # Owner lifecycle status
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
        # Retrieve all pages for both total count and content detection
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
        # Rule 1: inactive/closed owner, regardless of page count
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
                f"page count does not affect candidacy"
            )
            return result

        # ---------------------------------------------------------------------
        # Rule 2: default or empty content
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
        result["reason"] = content_reason

        return result

    except Exception as error:
        result["error"] = str(error)
        result["reason"] = (
            f"ERROR: {error}"
        )
        return result


# =============================================================================
# CSV
# =============================================================================

def write_csv_report(results):
    """
    Export all evaluated personal spaces to a single CSV report.
    """

    fieldnames = [
        "space_id",
        "space_key",
        "space_name",
        "owner_id",
        "owner_display_name",
        "owner_public_name",
        "owner_account_status",
        "owner_account_type",
        "owner_found",
        "created_at",
        "age_days",
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
            ),
        ):
            writer.writerow({
                "space_id": result.get("id"),
                "space_key": result.get("key"),
                "space_name": result.get("name"),
                "owner_id": result.get("owner_id"),
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
# OUTPUT
# =============================================================================

def print_result(prefix, result):
    """
    Print a concise result line.
    """

    print(
        f"{prefix:<13} "
        f"{result.get('key')} | "
        f"{result.get('name')} | "
        f"owner={result.get('owner_display_name') or result.get('owner_id') or 'UNKNOWN'} | "
        f"status={result.get('owner_account_status')} | "
        f"pages={result.get('total_page_count')} | "
        f"age={result.get('age_days')} | "
        f"type={result.get('candidate_type') or '-'} | "
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




# =============================================================================
# MAIN
# =============================================================================

def main():
    validate_config()

    print("=" * 120)
    print(
        "CONFLUENCE UNUSED PERSONAL SPACE CLEANUP"
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
    print("=" * 120)

    spaces = get_all_personal_spaces()

    print(
        f"\nFound {len(spaces)} "
        f"current personal spaces.\n"
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

        for future in as_completed(future_map):
            completed += 1

            try:
                result = future.result()

            except Exception as error:
                original_space = future_map[future]

                result = {
                    "id": original_space.get("id"),
                    "key": original_space.get("key"),
                    "name": original_space.get("name"),
                    "owner_id": None,
                    "owner_display_name": None,
                    "owner_public_name": None,
                    "owner_account_status": "unknown",
                    "owner_account_type": None,
                    "owner_found": False,
                    "created_at": None,
                    "age_days": None,
                    "total_page_count": None,
                    "page_titles": None,
                    "candidate": False,
                    "candidate_type": None,
                    "reason": f"ERROR: {error}",
                    "error": str(error),
                    "space_url": None,
                    "archive_attempted": False,
                    "archive_result": None,
                }

            results.append(result)

            if result.get("error"):
                prefix = "[ERROR]"

            elif result.get("candidate"):
                prefix = "[CANDIDATE]"
                candidates.append(result)

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
        )
    )

    print("\n" + "=" * 120)
    print(
        f"TOTAL PERSONAL SPACES: "
        f"{len(spaces)}"
    )
    print(
        f"ARCHIVE CANDIDATES:    "
        f"{len(candidates)}"
    )
    print(
        f"INACTIVE OWNER:        "
        f"{sum(1 for item in candidates if item.get('candidate_type') == 'INACTIVE_OWNER')}"
    )
    print(
        f"EMPTY SPACE:           "
        f"{sum(1 for item in candidates if item.get('candidate_type') == 'EMPTY_SPACE')}"
    )
    print(
        f"DEFAULT HOMEPAGE:      "
        f"{sum(1 for item in candidates if item.get('candidate_type') == 'DEFAULT_HOMEPAGE_ONLY')}"
    )
    print(
        f"DEFAULT TWO PAGES:     "
        f"{sum(1 for item in candidates if item.get('candidate_type') == 'DEFAULT_TWO_PAGES')}"
    )
    print(
        f"ERRORS:                "
        f"{sum(1 for item in results if item.get('error'))}"
    )
    print("=" * 120)

    if DRY_RUN:
        write_csv_report(
            results
        )

        print(
            "\nDRY_RUN=True: "
            "no spaces were archived."
        )

        return

    print(
        "\nAPPLY mode enabled. "
        "Archiving candidates...\n"
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
                f"FAILED: {error}"
            )

            print(
                f"[{index}/{len(candidates)}] "
                f"[FAILED] "
                f"{candidate['key']} | "
                f"{error}"
            )

    # Write the CSV after archive processing so archive_result is included.
    write_csv_report(
        results
    )


if __name__ == "__main__":
    main()
