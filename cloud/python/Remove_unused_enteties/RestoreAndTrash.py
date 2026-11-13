"""Restore and trash Jira Cloud projects archived over 180 days."""

import base64
import csv
import getpass
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import local
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from dotenv import load_dotenv


# IDE: keep APPLY=False for a preview; True also requires typed confirmation.
ARCHIVE_DAYS = 180
APPLY = True
REQUEST_TIMEOUT = 60
OUTPUT_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)


class CleanupError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JiraClient:
    def __init__(self, base_url, email, token):
        parts = urlsplit(base_url.strip())
        if (
            parts.scheme != "https"
            or not parts.hostname
            or not parts.hostname.endswith(".atlassian.net")
            or parts.username
            or parts.password
            or parts.port not in (None, 443)
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
        ):
            raise CleanupError("Use a Jira Cloud URL: https://your-site.atlassian.net")
        if not email.strip() or not token.strip():
            raise CleanupError("Email and API token must not be empty.")
        self.base_url = f"https://{parts.hostname}"
        credentials = base64.b64encode(
            f"{email.strip()}:{token.strip()}".encode("utf-8")
        ).decode("ascii")
        self.headers = {
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
        }
        self._thread_state = local()

    def request(self, method, path, params=None, expected=200):
        url = self.base_url + "/rest/api/3" + path
        if params:
            url += "?" + urlencode(params, doseq=True)
        request = Request(url, headers=self.headers, method=method)
        if not hasattr(self._thread_state, "opener"):
            self._thread_state.opener = build_opener(NoRedirect())
        try:
            with self._thread_state.opener.open(request, timeout=REQUEST_TIMEOUT) as response:
                if response.status != expected:
                    raise CleanupError(
                        f"{method} {path}: unexpected HTTP {response.status}."
                    )
                if expected == 204:
                    return None
                try:
                    data = json.load(response)
                except (ValueError, UnicodeError) as exc:
                    raise CleanupError(f"{method} {path}: invalid JSON response.") from exc
                if not isinstance(data, dict):
                    raise CleanupError(f"{method} {path}: expected a JSON object.")
                return data
        except HTTPError as exc:
            exc.close()
            raise CleanupError(
                f"{method} {path}: HTTP {exc.code}. Check permissions, credentials "
                "and rate limits. No automatic retry of destructive requests."
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise CleanupError(
                f"{method} {path}: network failure. If this was a write request, "
                "its outcome is unknown; inspect the project in Jira before retrying."
            ) from exc

    def authenticate(self):
        user = self.request("GET", "/myself")
        if not user.get("accountId") or user.get("active") is not True:
            raise CleanupError("Authentication did not return an active Jira account.")
        permissions = self.request(
            "GET", "/mypermissions", {"permissions": "ADMINISTER"}
        )
        if (
            permissions.get("permissions", {})
            .get("ADMINISTER", {})
            .get("havePermission")
            is not True
        ):
            raise CleanupError("The account must have the Administer Jira permission.")
        logger.info("Authenticated as %s on %s", user["accountId"], self.base_url)

    def projects(self, statuses, project_id=None):
        start = 0
        projects = []
        seen = set()
        while True:
            params = {
                "status": statuses,
                "startAt": start,
                "maxResults": 100,
                "orderBy": "key",
            }
            if project_id is not None:
                params["id"] = project_id
            page = self.request("GET", "/project/search", params)
            values = page.get("values")
            if not isinstance(values, list) or not isinstance(page.get("isLast"), bool):
                raise CleanupError("Invalid project search page; stopping.")
            if page.get("startAt") != start:
                raise CleanupError("Unexpected pagination offset; stopping.")
            for project in values:
                if (
                    not isinstance(project, dict)
                    or not isinstance(project.get("id"), str)
                    or not project["id"].isascii()
                    or not project["id"].isdigit()
                    or project["id"] in seen
                    or not project.get("key")
                ):
                    raise CleanupError("Invalid or duplicate project in search results.")
                if project_id is not None and project["id"] != project_id:
                    raise CleanupError("Project search ignored the requested ID.")
                seen.add(project["id"])
                projects.append(project)
            if page["isLast"]:
                return projects
            if not values:
                raise CleanupError("Empty non-final project page; stopping.")
            start += len(values)


def archived_at(project):
    raw = project.get("archivedDate")
    if not isinstance(raw, str) or not raw.strip():
        raise CleanupError(f"Project {project['id']}: missing archivedDate.")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CleanupError(f"Project {project['id']}: invalid archivedDate.") from exc
    if value.tzinfo is None:
        raise CleanupError(f"Project {project['id']}: archivedDate has no timezone.")
    return value.astimezone(timezone.utc)


def find_candidates(projects, cutoff):
    candidates = []
    for project in projects:
        if project.get("archived") is not True or project.get("deleted") is True:
            raise CleanupError(f"Project {project['id']}: archive status is not confirmed.")
        if archived_at(project) < cutoff:
            candidates.append(project)
    return candidates


def save_report(projects, cutoff, output_dir):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = output_dir / f"jira_cleanup_candidates_{timestamp}.csv"
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "key", "name", "archivedDate", "cutoff_utc"])
        for project in projects:
            writer.writerow([
                project["id"], project["key"], project.get("name", ""),
                project["archivedDate"], cutoff.isoformat(),
            ])
    return path


def trash_candidate(client, project, cutoff):
    project_id = project["id"]
    current = client.projects(["archived"], project_id)
    if (
        len(current) != 1
        or find_candidates(current, cutoff) != current
        or archived_at(current[0]) != archived_at(project)
    ):
        raise CleanupError(f"{project_id}: archive state changed; no action taken.")

    logger.warning("RESTORING %s (%s)", project["key"], project_id)
    restored = client.request("POST", f"/project/{project_id}/restore")
    if restored.get("id") != project_id:
        raise CleanupError(f"{project_id}: unexpected restore response; deletion stopped.")
    live = client.projects(["live"], project_id)
    if len(live) != 1 or live[0].get("archived") is True or live[0].get("deleted") is True:
        raise CleanupError(f"{project_id}: live state not confirmed; deletion stopped.")

    logger.warning("MOVING TO RECYCLE BIN %s (%s)", project["key"], project_id)
    client.request(
        "DELETE", f"/project/{project_id}",
        {"enableUndo": "true"}, expected=204,
    )
    trashed = client.projects(["deleted"], project_id)
    if len(trashed) != 1 or trashed[0].get("deleted") is not True:
        raise CleanupError(f"{project_id}: recycle bin state not confirmed.")
    logger.warning("MOVED TO RECYCLE BIN %s (%s)", project["key"], project_id)


def run_cleanup(client, apply, days=ARCHIVE_DAYS, output_dir=OUTPUT_DIR, confirm=input):
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise CleanupError("ARCHIVE_DAYS must be a positive integer.")
    client.authenticate()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    candidates = find_candidates(client.projects(["archived"]), cutoff)
    report = save_report(candidates, cutoff, output_dir)
    logger.info("Cutoff (strictly older): %s; candidates: %s", cutoff.isoformat(), len(candidates))
    logger.info("Candidate report: %s", report)
    for project in candidates:
        logger.info(
            "CANDIDATE %s (%s) archived %s",
            project["key"], project["id"], project["archivedDate"],
        )
    if not apply:
        logger.info("DRY-RUN: no projects restored or moved to recycle bin. Set APPLY=True to execute.")
        return
    if not candidates:
        logger.info("No eligible projects.")
        return
    phrase = f"TRASH {len(candidates)} PROJECTS ON {client.base_url}"
    logger.warning("Projects will be restored and moved to the Jira recycle bin for review.")
    if confirm(f"Type exactly '{phrase}' to continue: ").strip() != phrase:
        raise CleanupError("Confirmation did not match; no projects changed.")
    for project in candidates:
        try:
            trash_candidate(client, project, cutoff)
        except CleanupError:
            logger.error(
                "Stopped at %s (%s). It may now be live or in the recycle bin. "
                "Check Jira and the log before any further action; no rollback attempted.",
                project["key"], project["id"],
            )
            raise


def client_from_env():
    load_dotenv(OUTPUT_DIR / ".env", override=False, interpolate=False, encoding="utf-8-sig")
    base_url = os.environ.get("JIRA_BASE_URL", "").strip() or input(
        "Jira URL (https://your-site.atlassian.net): "
    ).strip()
    email = os.environ.get("JIRA_EMAIL", "").strip() or input("Atlassian email: ").strip()
    token = os.environ.get("JIRA_API_TOKEN", "").strip()
    if not token:
        if not sys.stdin.isatty():
            raise CleanupError(
                "Secure token input is unavailable. Set JIRA_API_TOKEN in .env "
                "or enable 'Emulate terminal in output console'."
            )
        token = getpass.getpass("Atlassian API token: ")
    return JiraClient(base_url, email, token)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(OUTPUT_DIR / "jira_cleanup.log", encoding="utf-8"),
        ],
    )
    try:
        client = client_from_env()
        run_cleanup(client, APPLY)
    except (CleanupError, OSError, ValueError, EOFError) as exc:
        logger.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logger.error("Interrupted. Inspect Jira before retrying if a write was in progress.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
