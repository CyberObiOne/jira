"""Collect project lead and project administrator contacts on the  sandbox.

Archived sandbox projects are temporarily restored to read their data, then
archived again.
"""

import argparse
import csv
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from delete_active_projects import (
    SANDBOX_SITE,
    Jira,
    SafetyError,
    now,
    project_record,
)


DEFAULT_WORKERS = 8
MAX_WORKERS = 16
READ_RETRIES = 3
ARCHIVE_ATTEMPTS = 4
PAGE_SIZE = 100
ADMIN_PERMISSION = "ADMINISTER_PROJECTS"
PROJECT_KEY_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*")
ACCOUNT_ID_PATTERN = re.compile(r"[A-Za-z0-9:_-]+")
EMAIL_HIDDEN = ""
STARTED = time.monotonic()
_print_lock = threading.Lock()

SUMMARY_COLUMNS = [
    "Project key",
    "Project name",
    "Sandbox status",
    "Project lead",
    "Project lead email",
    "Project lead active",
    "Administrators",
    "Administrator count",
    "Error",
]
CONTACT_COLUMNS = [
    "Project key",
    "Project name",
    "Role",
    "Name",
    "Email",
    "Active",
]


def progress(message):
    elapsed = time.monotonic() - STARTED
    with _print_lock:
        print(
            f"[{datetime.now().astimezone():%H:%M:%S} +{elapsed:.1f}s] {message}",
            flush=True,
        )


def validate_workers(workers):
    if type(workers) is not int or not 1 <= workers <= MAX_WORKERS:
        raise SafetyError(f"Workers must be between 1 and {MAX_WORKERS}.")


def read_project_list(path):
    """Read sandbox project IDs/keys from a verify_restored_projects plan."""
    with path.open(encoding="utf-8") as stream:
        plan = json.load(stream)
    if not isinstance(plan, dict) or plan.get("sandbox_site") != SANDBOX_SITE:
        raise SafetyError(f"Project list must be a verify plan for {SANDBOX_SITE}.")
    entries = plan.get("projects")
    if not isinstance(entries, list) or not entries:
        raise SafetyError("Project list contains no projects.")
    projects = []
    for entry in entries:
        sandbox = entry.get("sandbox") if isinstance(entry, dict) else None
        if not isinstance(sandbox, dict):
            raise SafetyError("Every project entry must contain a sandbox project.")
        record = project_record(sandbox)
        if not PROJECT_KEY_PATTERN.fullmatch(record["key"]):
            raise SafetyError(f"Invalid project key in list: {record['key']!r}.")
        projects.append(record)
    if len({p["id"] for p in projects}) != len(projects) or len(
        {p["key"] for p in projects}
    ) != len(projects):
        raise SafetyError("Project list contains duplicate project IDs or keys.")
    return projects


def current_states(jira, projects):
    """Return the current live/archived state of every listed sandbox project."""
    current = {}
    for status in ("live", "archived"):
        progress(f"PREFLIGHT: loading {status} sandbox projects")
        for record in jira.projects(status):
            if record["id"] in current:
                raise SafetyError("Duplicate project across live and archived listings.")
            current[record["id"]] = dict(record, status=status)
    missing = []
    changed = []
    states = []
    for project in projects:
        found = current.get(project["id"])
        if found is None:
            missing.append(project["key"])
        elif found["key"] != project["key"]:
            changed.append(f"{project['key']} (now {found['key']})")
        else:
            states.append(found)
    if missing or changed:
        raise SafetyError(
            "Sandbox projects are missing (or in trash) or changed key: "
            + ", ".join(missing + changed)
            + ". Remove them from the list or recheck the sandbox."
        )
    return states


class UserCache:
    """Thread-safe accountId -> user details cache, shared by workers."""

    def __init__(self):
        self._users = {}
        self._lock = threading.Lock()

    def seed(self, user):
        account_id = user.get("accountId")
        if isinstance(account_id, str) and user.get("emailAddress"):
            with self._lock:
                self._users.setdefault(account_id, user)

    def get(self, jira, account_id):
        if not isinstance(account_id, str) or not ACCOUNT_ID_PATTERN.fullmatch(account_id):
            raise SafetyError("Invalid Jira account ID.")
        with self._lock:
            cached = self._users.get(account_id)
        if cached is not None:
            return cached
        user = jira.request("GET", "/rest/api/3/user", accountId=account_id)
        if not isinstance(user, dict) or user.get("accountId") != account_id:
            raise SafetyError("Invalid Jira user response.")
        with self._lock:
            self._users[account_id] = user
        return user


def person(user):
    return {
        "account_id": user["accountId"],
        "name": user.get("displayName") or "",
        "email": user.get("emailAddress") or EMAIL_HIDDEN,
        "active": user.get("active") is True,
    }


def project_lead(jira, project, users):
    result = jira.request("GET", f"/rest/api/3/project/{project['id']}", expand="lead")
    if not isinstance(result, dict) or result.get("id") != project["id"]:
        raise SafetyError(f"Invalid project response for {project['key']}.")
    lead = result.get("lead")
    if not isinstance(lead, dict) or not lead.get("accountId"):
        return result.get("name") or project["name"], None
    return result.get("name") or project["name"], person(users.get(jira, lead["accountId"]))


def project_admins(jira, project, users):
    """Users with the effective ADMINISTER_PROJECTS permission; app accounts are excluded."""
    found = {}
    start = 0
    while True:
        page = jira.request(
            "GET",
            "/rest/api/3/user/permission/search",
            projectKey=project["key"],
            permissions=ADMIN_PERMISSION,
            startAt=start,
            maxResults=PAGE_SIZE,
        )
        if not isinstance(page, list):
            raise SafetyError("Invalid permission user search response.")
        for user in page:
            if not isinstance(user, dict) or not isinstance(user.get("accountId"), str):
                raise SafetyError("Invalid user in permission search response.")
            if user.get("accountType") != "atlassian":
                continue
            users.seed(user)
            found[user["accountId"]] = user
        if len(page) < PAGE_SIZE:
            break
        start += len(page)
    admins = []
    for account_id, user in found.items():
        if not user.get("emailAddress"):
            user = users.get(jira, account_id)
        admins.append(person(user))
    return sorted(admins, key=lambda admin: (admin["name"].casefold(), admin["email"]))


def collect(jira, project, users):
    name, lead = project_lead(jira, project, users)
    return {
        "key": project["key"],
        "name": name,
        "lead": lead,
        "admins": project_admins(jira, project, users),
    }


class Audit:
    def __init__(self, stream):
        self.stream = stream
        self.lock = threading.Lock()

    def write(self, event, project=None, **details):
        record = {"time": now().isoformat(), "site": SANDBOX_SITE, "event": event}
        if project is not None:
            record["project"] = {k: project[k] for k in ("id", "key", "name", "status")}
        record.update(details)
        with self.lock:
            self.stream.write(json.dumps(record, ensure_ascii=True) + "\n")
            self.stream.flush()
            os.fsync(self.stream.fileno())


def rearchive(jira, project, audit):
    """Archive a restored project again, rechecking state before every attempt."""
    record = project_record(project)
    for attempt in range(1, ARCHIVE_ATTEMPTS + 1):
        live = jira.projects("live", record["id"])
        archived = jira.projects("archived", record["id"])
        if archived == [record] and not live:
            if attempt > 1:
                audit.write("archive_confirmed", project, attempt=attempt)
                progress(f"{project['key']}: archive status confirmed")
            return
        if live != [record] or archived:
            raise SafetyError(
                f"{project['key']} is in an unexpected state; it was NOT re-archived. "
                "Inspect the sandbox manually."
            )
        progress(f"{project['key']}: archiving again (attempt {attempt}/{ARCHIVE_ATTEMPTS})")
        audit.write("archive_requested", project, attempt=attempt)
        try:
            jira.request("POST", f"/rest/api/3/project/{record['id']}/archive")
            jira.require_archived(record)
        except SafetyError as exc:
            if attempt == ARCHIVE_ATTEMPTS:
                raise
            delay = 2 ** attempt
            audit.write("archive_retry", project, attempt=attempt, error=str(exc))
            progress(f"{project['key']}: archive not confirmed ({exc}); rechecking in {delay}s")
            time.sleep(delay)
            continue
        audit.write("archive_confirmed", project, attempt=attempt)
        progress(f"{project['key']}: archive status confirmed")
        return


def process_project(jira, project, users, audit):
    """Returns (result, error). Raises only when sandbox state may be unsafe."""
    record = project_record(project)
    restored = False
    if project["status"] == "archived":
        jira.require_archived(record)
        audit.write("restore_requested", project)
        progress(f"{project['key']}: restoring archived project")
        restored = True
        try:
            jira.request("POST", f"/rest/api/3/project/{record['id']}/restore")
            jira.require_live(record)
        except BaseException:
            rearchive(jira, project, audit)
            raise
        audit.write("restore_confirmed", project)
    else:
        jira.require_live(record)
    try:
        try:
            result = collect(jira, project, users)
        except SafetyError as exc:
            audit.write("collect_error", project, error=str(exc))
            progress(f"{project['key']}: ERROR collecting data: {exc}")
            return None, str(exc)
        audit.write("contacts_collected", project, contacts=result)
        progress(
            f"{project['key']}: lead={result['lead']['name'] if result['lead'] else '-'}, "
            f"administrators={len(result['admins'])}"
        )
        return result, None
    finally:
        if restored:
            rearchive(jira, project, audit)


def run_all(projects, task, workers, client_factory, results, fatal):
    """Run task concurrently, filling results/fatal in place.

    On a fatal error or Ctrl+C no new projects are started; in-flight projects finish
    (including re-archiving).
    """
    local = threading.local()
    stop = threading.Event()
    total = len(projects)

    def run(index, project):
        if stop.is_set():
            return None
        if not hasattr(local, "jira"):
            local.jira = client_factory()
        progress(f"PROJECT {index}/{total}: {project['key']} ({project['status']})")
        return task(local.jira, project)

    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {
            executor.submit(run, index, project): project
            for index, project in enumerate(projects, 1)
        }
        for future in as_completed(futures):
            project = futures[future]
            try:
                outcome = future.result()
            except Exception as exc:
                if not fatal:
                    progress("FATAL error; finishing in-flight projects, not starting new ones")
                stop.set()
                fatal.append((project["key"], exc))
                progress(f"FATAL {project['key']}: {exc}")
                continue
            if outcome is not None:
                results[project["id"]] = outcome
    except BaseException:
        stop.set()
        progress("Interrupted; waiting for in-flight projects to be re-archived")
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


def rows(projects, results):
    summary = []
    contacts = []
    for project in projects:
        outcome = results.get(project["id"])
        if outcome is None:
            continue
        result, error = outcome
        if result is None:
            summary.append({
                "Project key": project["key"],
                "Project name": project["name"],
                "Sandbox status": project["status"],
                "Error": error,
            })
            continue
        lead = result["lead"]
        admins = result["admins"]
        summary.append({
            "Project key": result["key"],
            "Project name": result["name"],
            "Sandbox status": project["status"],
            "Project lead": lead["name"] if lead else "",
            "Project lead email": lead["email"] if lead else "",
            "Project lead active": ("yes" if lead["active"] else "no") if lead else "",
            "Administrators": "; ".join(
                f"{a['name']} <{a['email']}>" if a["email"] else a["name"] for a in admins
            ),
            "Administrator count": len(admins),
            "Error": "",
        })
        people = ([("Project lead", lead)] if lead else []) + [
            ("Administrator", admin) for admin in admins
        ]
        for role, entry in people:
            contacts.append({
                "Project key": result["key"],
                "Project name": result["name"],
                "Role": role,
                "Name": entry["name"],
                "Email": entry["email"],
                "Active": "yes" if entry["active"] else "no",
            })
    return summary, contacts


def output_paths(csv_path):
    return {
        "summary": csv_path,
        "contacts": csv_path.with_name(f"{csv_path.stem}-contacts{csv_path.suffix}"),
        "json": csv_path.with_suffix(".json"),
    }


def write_outputs(paths, projects, results):
    summary, contacts = rows(projects, results)
    for path, columns, data in (
        (paths["summary"], SUMMARY_COLUMNS, summary),
        (paths["contacts"], CONTACT_COLUMNS, contacts),
    ):
        # utf-8-sig so Excel detects the encoding of non-ASCII names.
        with path.open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, restval="")
            writer.writeheader()
            writer.writerows(data)
    detailed = [
        {
            "project": project,
            "contacts": results[project["id"]][0],
            "error": results[project["id"]][1],
        }
        for project in projects
        if project["id"] in results
    ]
    with paths["json"].open("x", encoding="utf-8") as stream:
        json.dump(
            {"site": SANDBOX_SITE, "created_at": now().isoformat(), "projects": detailed},
            stream,
            ensure_ascii=False,
            indent=2,
        )
        stream.write("\n")
    return len(summary), len(contacts)


def run(jira, list_path, csv_path, log_path, workers, client_factory):
    validate_workers(workers)
    paths = output_paths(csv_path)
    existing = [str(p) for p in [*paths.values(), log_path] if p.exists()]
    if existing:
        raise SafetyError("Output files already exist: " + ", ".join(existing))

    listed = read_project_list(list_path)
    progress(f"Project list: {len(listed)} projects from {list_path}")
    jira.require_admin()
    projects = current_states(jira, listed)
    archived = [p for p in projects if p["status"] == "archived"]
    progress(
        f"PREFLIGHT complete: {len(projects) - len(archived)} live, "
        f"{len(archived)} archived (will be restored and archived again)"
    )
    if archived:
        phrase = (
            f"RESTORE AND REARCHIVE {len(archived)} PROJECTS "
            "ON cotality-h2-sandbox.atlassian.net"
        )
        print(f"Type exactly: {phrase}", flush=True)
        if input("> ").strip() != phrase:
            raise SafetyError("Confirmation did not match; no projects were changed.")

    users = UserCache()
    results = {}
    fatal = []
    with log_path.open("x", encoding="utf-8") as stream:
        audit = Audit(stream)
        audit.write("started", workers=workers, projects=len(projects))
        finished = False
        try:
            run_all(
                projects,
                lambda worker_jira, project: process_project(
                    worker_jira, project, users, audit
                ),
                workers,
                client_factory,
                results,
                fatal,
            )
            finished = True
        finally:
            audit.write(
                "completed" if finished and not fatal else "stopped",
                processed=len(results),
                fatal=[{"key": key, "error": str(exc)} for key, exc in fatal],
            )
            written, contact_rows = write_outputs(paths, projects, results)
            progress(
                f"Wrote {written} project rows to {paths['summary']}, "
                f"{contact_rows} contact rows to {paths['contacts']}, "
                f"details to {paths['json']}"
            )
    errors = [p["key"] for p in projects if results.get(p["id"], (None, None))[1]]
    not_processed = [p["key"] for p in projects if p["id"] not in results]
    progress(
        f"Done: processed={len(results)}/{len(projects)}, collection_errors={len(errors)}"
        + (f" ({', '.join(errors)})" if errors else "")
    )
    if fatal or not_processed:
        raise SafetyError(
            f"{len(fatal)} fatal error(s) ({', '.join(k for k, _ in fatal)}); "
            f"{len(not_processed)} project(s) not processed. Inspect the audit log and "
            "the archive status of these projects on the sandbox."
        ) from (fatal[0][1] if fatal else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects", type=Path, required=True,
                        help="verify_restored_projects plan with the project list.")
    parser.add_argument("--out", type=Path, required=True,
                        help="Summary CSV; -contacts.csv and .json are written next to it.")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS,
        help=f"Concurrent projects (1-{MAX_WORKERS}; default: {DEFAULT_WORKERS}).",
    )
    args = parser.parse_args()
    try:
        validate_workers(args.workers)
        progress(f"Site: {SANDBOX_SITE}; workers: {args.workers}")

        def client_factory():
            return Jira(SANDBOX_SITE, progress=progress, read_retries=READ_RETRIES)

        run(client_factory(), args.projects, args.out, args.log, args.workers, client_factory)
    except (SafetyError, OSError, ValueError, EOFError) as exc:
        progress(f"STOPPED: {type(exc).__name__}; see error below")
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print(
            "\nInterrupted. Check the audit log for restore_confirmed without "
            "archive_confirmed and re-archive those projects.",
            file=sys.stderr,
        )
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
