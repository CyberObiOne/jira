"""Delete Done issues and attachments on non-Done issues in selected projects."""

import csv
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from threading import Event

from RestoreAndDelete import CleanupError, OUTPUT_DIR, client_from_env


PROJECT_KEYS = ["AIRE", "CITY"]
APPLY = True
MAX_WORKERS = 5
FIELDS = "project,status,attachment,subtasks"
logger = logging.getLogger(__name__)


def numeric_id(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise CleanupError("Invalid Jira ID; stopping.")
    return value


def validate_keys(keys):
    if not keys or any(
        not isinstance(key, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
        for key in keys
    ) or len(keys) != len(set(keys)):
        raise CleanupError("PROJECT_KEYS must contain unique uppercase Jira project keys.")
    return keys


def validate_issue(issue, keys):
    try:
        numeric_id(issue["id"])
        fields = issue["fields"]
        if fields["project"]["key"] not in keys:
            raise CleanupError("Issue outside selected projects; stopping.")
        if fields["status"]["statusCategory"]["key"] not in ("new", "indeterminate", "done"):
            raise CleanupError("Unknown status category; stopping.")
        if not issue["key"] or not isinstance(fields["attachment"], list):
            raise CleanupError("Missing issue key or attachment list.")
        if not isinstance(fields["subtasks"], list):
            raise CleanupError("Missing subtasks list.")
        for attachment in fields["attachment"]:
            numeric_id(attachment["id"])
        for child in fields["subtasks"]:
            numeric_id(child["id"])
    except (KeyError, TypeError) as exc:
        raise CleanupError("Incomplete Jira issue response; stopping.") from exc
    return issue


def is_done(issue):
    return issue["fields"]["status"]["statusCategory"]["key"] == "done"


def fetch_issues(client, keys):
    issues = []
    seen_ids = set()
    seen_tokens = set()
    params = {
        "jql": "project in (" + ",".join(f'"{key}"' for key in keys) + ") ORDER BY key",
        "fields": FIELDS, "maxResults": 100, "failFast": "true",
    }
    while True:
        page = client.request("GET", "/search/jql", params)
        if page.get("warnings") or page.get("warningMessages"):
            raise CleanupError("Jira search returned warnings; results may be incomplete.")
        if not isinstance(page.get("issues"), list):
            raise CleanupError("Invalid issue search page.")
        for issue in page["issues"]:
            validate_issue(issue, keys)
            if issue["id"] in seen_ids:
                raise CleanupError("Duplicate issue in search; stopping.")
            seen_ids.add(issue["id"])
            issues.append(issue)
        token = page.get("nextPageToken")
        if not token:
            if page.get("isLast") is False:
                raise CleanupError("Search continuation token is missing.")
            return issues
        if not isinstance(token, str) or token in seen_tokens:
            raise CleanupError("Invalid or repeated search continuation token.")
        seen_tokens.add(token)
        params = dict(params, nextPageToken=token)


def build_plan(issues):
    by_id = {issue["id"]: issue for issue in issues}
    ordered = []
    blocked = []
    visiting = set()
    allowed = {}

    def visit(issue):
        issue_id = issue["id"]
        if issue_id in allowed:
            return allowed[issue_id]
        if issue_id in visiting:
            raise CleanupError("Cycle in subtask hierarchy.")
        if not is_done(issue):
            allowed[issue_id] = False
            return False
        visiting.add(issue_id)
        can_delete = True
        for child in issue["fields"]["subtasks"]:
            if child["id"] not in by_id or not visit(by_id[child["id"]]):
                can_delete = False
        visiting.remove(issue_id)
        allowed[issue_id] = can_delete
        if can_delete:
            ordered.append(issue)
        else:
            blocked.append(issue)
        return can_delete

    for issue in issues:
        visit(issue)
    attachments = [
        (issue, attachment)
        for issue in issues if not is_done(issue)
        for attachment in issue["fields"]["attachment"]
    ]
    return ordered, attachments, blocked


def fresh_issue(client, issue, keys, done):
    current = validate_issue(
        client.request("GET", f"/issue/{issue['id']}", {"fields": FIELDS}), keys,
    )
    if (
        current["id"] != issue["id"]
        or current["fields"]["project"]["key"] != issue["fields"]["project"]["key"]
        or is_done(current) != done
    ):
        raise CleanupError(f"{issue['key']}: project or status category changed; stopping.")
    return current


def save_plan(plan, output_dir):
    path = output_dir / (
        "jira_issue_cleanup_plan_"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".csv"
    )
    issues, attachments, blocked = plan
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["action", "issue_id", "issue_key", "attachment_id", "reason"])
        for issue in issues:
            writer.writerow(["DELETE_ISSUE", issue["id"], issue["key"], "", "Done"])
        for issue, attachment in attachments:
            writer.writerow([
                "DELETE_ATTACHMENT", issue["id"], issue["key"], attachment["id"], "non-Done",
            ])
        for issue in blocked:
            writer.writerow([
                "KEEP_ISSUE", issue["id"], issue["key"], "",
                "Non-Done, unavailable or protected subtask",
            ])
    return path


def run_workers(executor, items, action, stop, max_workers):
    def execute(item):
        if stop.is_set():
            return
        try:
            action(item)
        except Exception:
            stop.set()
            logger.exception("Worker failed; stopping further operations.")
            raise

    iterator = iter(items)
    while True:
        batch = []
        for _ in range(max_workers):
            try:
                batch.append(next(iterator))
            except StopIteration:
                break
        if not batch:
            return
        futures = []
        try:
            for item in batch:
                futures.append(executor.submit(execute, item))
            for future in as_completed(futures):
                future.result()
        except BaseException:
            stop.set()
            for future in futures:
                future.cancel()
            raise


def issue_levels(issues):
    depths = {}
    levels = {}
    for issue in issues:
        depth = 1 + max(
            (depths[child["id"]] for child in issue["fields"]["subtasks"]),
            default=-1,
        )
        depths[issue["id"]] = depth
        levels.setdefault(depth, []).append(issue)
    return [levels[depth] for depth in sorted(levels)]


def execute_plan(client, keys, issues, attachments, max_workers):
    stop = Event()

    def delete_issue(issue):
        current = fresh_issue(client, issue, keys, done=True)
        if current["fields"]["subtasks"]:
            raise CleanupError(f"{issue['key']}: subtasks remain; parent deletion stopped.")
        if stop.is_set():
            return
        logger.warning("DELETING ISSUE %s (%s)", issue["key"], issue["id"])
        client.request(
            "DELETE", f"/issue/{issue['id']}", {"deleteSubtasks": "false"}, expected=204,
        )
        logger.warning("DELETED ISSUE %s (%s)", issue["key"], issue["id"])

    def delete_attachments(group):
        issue, planned = group
        for attachment in planned:
            if stop.is_set():
                return
            current = fresh_issue(client, issue, keys, done=False)
            if attachment["id"] not in {a["id"] for a in current["fields"]["attachment"]}:
                raise CleanupError(f"{issue['key']}: attachment list changed; stopping.")
            if stop.is_set():
                return
            logger.warning("DELETING ATTACHMENT %s ON %s", attachment["id"], issue["key"])
            client.request("DELETE", f"/attachment/{attachment['id']}", expected=204)
            logger.warning("DELETED ATTACHMENT %s ON %s", attachment["id"], issue["key"])

    grouped = {}
    for issue, attachment in attachments:
        if issue["id"] not in grouped:
            grouped[issue["id"]] = (issue, [])
        grouped[issue["id"]][1].append(attachment)
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="jira-cleanup") as executor:
        for level in issue_levels(issues):
            run_workers(executor, level, delete_issue, stop, max_workers)
        run_workers(executor, grouped.values(), delete_attachments, stop, max_workers)


def run_cleanup(
    client, keys, apply=False, output_dir=OUTPUT_DIR, confirm=input, max_workers=MAX_WORKERS,
):
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
        raise CleanupError("MAX_WORKERS must be a positive integer.")
    keys = validate_keys(keys)
    client.authenticate()
    for key in keys:
        project = client.request("GET", f"/project/{key}")
        if project.get("key") != key or project.get("archived") or project.get("deleted"):
            raise CleanupError(f"{key}: project must be accessible and live.")
        permissions = client.request(
            "GET", "/mypermissions",
            {"projectKey": key, "permissions": "BROWSE_PROJECTS,DELETE_ISSUES,DELETE_ALL_ATTACHMENTS"},
        ).get("permissions", {})
        for permission in ("BROWSE_PROJECTS", "DELETE_ISSUES", "DELETE_ALL_ATTACHMENTS"):
            if permissions.get(permission, {}).get("havePermission") is not True:
                raise CleanupError(f"{key}: missing {permission} permission.")
    plan = build_plan(fetch_issues(client, keys))
    issues, attachments, blocked = plan
    report = save_plan(plan, output_dir)
    logger.info(
        "Projects: %s; Done issues to delete: %s; attachments on non-Done: %s; "
        "protected Done parents: %s; report: %s",
        ", ".join(keys), len(issues), len(attachments), len(blocked), report,
    )
    for issue in blocked:
        logger.warning("KEEP %s: non-Done, unavailable or protected subtask", issue["key"])
    if not apply:
        logger.info("DRY-RUN: nothing changed. Review the report before setting APPLY=True.")
        return
    if not issues and not attachments:
        logger.info("No eligible actions.")
        return
    phrase = (
        f"DELETE {len(issues)} ISSUES AND {len(attachments)} ATTACHMENTS "
        f"IN {','.join(keys)} ON {client.base_url}"
    )
    logger.warning("PERMANENT deletion: issues and attachments cannot be restored from a recycle bin.")
    if confirm(f"Type exactly '{phrase}' to continue: ").strip() != phrase:
        raise CleanupError("Confirmation did not match; nothing changed.")
    logger.info("Executing with MAX_WORKERS=%s", max_workers)
    execute_plan(client, keys, issues, attachments, max_workers)


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(OUTPUT_DIR / "jira_issue_cleanup.log", encoding="utf-8"),
        ],
    )
    try:
        run_cleanup(client_from_env(), PROJECT_KEYS, APPLY, max_workers=MAX_WORKERS)
    except (CleanupError, OSError, ValueError, EOFError) as exc:
        logger.error(
            "%s Run stopped; earlier actions are not rolled back. "
            "Check Jira and the log before retrying.", exc,
        )
        return 1
    except KeyboardInterrupt:
        logger.error("Interrupted; check Jira if a write was in progress.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
