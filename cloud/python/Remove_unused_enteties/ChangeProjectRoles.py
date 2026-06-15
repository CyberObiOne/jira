import requests
from requests.auth import HTTPBasicAuth
import time

# ================= CONFIG =================
JIRA_URL = "https://example.atlassian.net/"
EMAIL = "mail"
API_TOKEN = "Token"

PROJECT_KEY = "TEST"



DRY_RUN = False# True = preview only, False = apply changes
VALIDATE_USERS = True
SLEEP_BETWEEN_CALLS = 1

ROLE_MAPPING = {
    11100: 10002,
    11101: 12507,
    11102: 11013

}
# ==========================================

auth = HTTPBasicAuth(EMAIL, API_TOKEN)
headers = {
    "Accept": "application/json",
    "Content-Type": "application/json"
}

user_resolution_cache = {}


def jira_get(url, params=None):
    resp = requests.get(url, headers=headers, auth=auth, params=params)
    resp.raise_for_status()
    return resp.json()


def jira_post(url, payload):
    return requests.post(url, headers=headers, auth=auth, json=payload)


def jira_delete(url, params=None):
    return requests.delete(url, headers=headers, auth=auth, params=params)


def get_project_roles():
    url = f"{JIRA_URL}/rest/api/3/project/{PROJECT_KEY}/role"
    return jira_get(url)


def get_role_details(role_url):
    return jira_get(role_url)


def get_user_by_account_id(account_id):
    cache_key = f"account:{account_id}"
    if cache_key in user_resolution_cache:
        return user_resolution_cache[cache_key]

    try:
        data = jira_get(f"{JIRA_URL}/rest/api/3/user", params={"accountId": account_id})
        user_resolution_cache[cache_key] = data
        return data
    except requests.HTTPError:
        user_resolution_cache[cache_key] = None
        return None


def search_user_by_display_name(display_name):
    cache_key = f"display:{display_name}"
    if cache_key in user_resolution_cache:
        return user_resolution_cache[cache_key]

    try:
        results = jira_get(
            f"{JIRA_URL}/rest/api/3/user/search",
            params={"query": display_name}
        )
    except requests.HTTPError:
        user_resolution_cache[cache_key] = None
        return None

    if not isinstance(results, list):
        user_resolution_cache[cache_key] = None
        return None

    # exact active match
    for u in results:
        if u.get("displayName") == display_name and u.get("active", False):
            user_resolution_cache[cache_key] = u
            return u

    # fallback: first active match
    for u in results:
        if u.get("active", False):
            user_resolution_cache[cache_key] = u
            return u

    user_resolution_cache[cache_key] = None
    return None


def normalize_actor(actor):
    actor_type = actor.get("type")
    display_name = (
        actor.get("displayName")
        or actor.get("actorUser", {}).get("displayName")
        or actor.get("name")
        or "UNKNOWN"
    )

    if actor_type == "atlassian-user-role-actor":
        actor_user = actor.get("actorUser", {})
        raw_account_id = actor_user.get("accountId")
        legacy_name = actor.get("name")

        return {
            "type": "user",
            "display": display_name,
            "raw_account_id": raw_account_id,
            "legacy_name": legacy_name,
            "actor": actor
        }

    if actor_type == "atlassian-group-role-actor":
        group_obj = actor.get("actorGroup", {})
        group_name = (
            group_obj.get("displayName")
            or group_obj.get("name")
            or actor.get("displayName")
            or actor.get("name")
            or "UNKNOWN_GROUP"
        )
        return {
            "type": "group",
            "display": group_name,
            "raw_account_id": None,
            "legacy_name": None,
            "actor": actor
        }

    return {
        "type": "unknown",
        "display": display_name,
        "raw_account_id": None,
        "legacy_name": actor.get("name"),
        "actor": actor
    }


def resolve_user_account_id(user_actor):
    raw_account_id = user_actor.get("raw_account_id")
    display = user_actor.get("display")
    legacy_name = user_actor.get("legacy_name")

    # 1) try raw accountId
    if raw_account_id:
        if not VALIDATE_USERS:
            return raw_account_id, display, "raw_account_id_no_validation"

        user_data = get_user_by_account_id(raw_account_id)
        if user_data:
            return user_data.get("accountId"), user_data.get("displayName", display), "validated_account_id"

    # 2) try by display name
    if display and display != "UNKNOWN":
        user_data = search_user_by_display_name(display)
        if user_data:
            return user_data.get("accountId"), user_data.get("displayName", display), "resolved_by_display_name"

    # 3) legacy actor cannot be safely used directly
    if legacy_name and legacy_name.startswith("JIRAUSER"):
        return None, display, "legacy_name_unresolved"

    return None, display, "unresolved"


def extract_role_members(role_data):
    members = []
    for actor in role_data.get("actors", []):
        members.append(normalize_actor(actor))
    return members


def add_user_to_role(role_url, account_id, display_name):
    payload = {"user": [account_id]}

    if DRY_RUN:
        print(f"    [DRY-RUN][ADD] Would add: {display_name} ({account_id})")
        return True, "dry_run_add"

    resp = jira_post(role_url, payload)

    if resp.status_code in (200, 201):
        print(f"    ✅ ADDED: {display_name} ({account_id})")
        return True, "added"

    try:
        err = resp.json()
    except Exception:
        err = resp.text

    print(f"    ❌ FAILED_ADD: {display_name} ({account_id}) -> {err}")
    return False, err


def remove_user_from_role(role_url, account_id, display_name):
    params = {"user": account_id}

    if DRY_RUN:
        print(f"    [DRY-RUN][REMOVE] Would remove from source: {display_name} ({account_id})")
        return True, "dry_run_remove"

    resp = jira_delete(role_url, params=params)

    if resp.status_code in (200, 204):
        print(f"    ✅ REMOVED FROM SOURCE: {display_name} ({account_id})")
        return True, "removed"

    try:
        err = resp.json()
    except Exception:
        err = resp.text

    print(f"    ❌ FAILED_REMOVE: {display_name} ({account_id}) -> {err}")
    return False, err


def main():
    print(f"\n=== PROJECT {PROJECT_KEY} ===")
    print(f"DRY_RUN = {DRY_RUN}")
    print(f"VALIDATE_USERS = {VALIDATE_USERS}")

    roles = get_project_roles()
    role_id_to_data = {}

    for role_name, role_url in roles.items():
        role_id = int(role_url.rstrip("/").split("/")[-1])
        role_details = get_role_details(role_url)
        role_members = extract_role_members(role_details)

        role_id_to_data[role_id] = {
            "id": role_id,
            "name": role_name,
            "url": role_url,
            "members": role_members
        }

    for source_role_id, target_role_id in ROLE_MAPPING.items():
        print("\n" + "=" * 90)
        print(f"MAPPING: {source_role_id} -> {target_role_id}")

        source = role_id_to_data.get(source_role_id)
        target = role_id_to_data.get(target_role_id)

        if not source:
            print(f"❌ Source role {source_role_id} not found in project")
            continue

        if not target:
            print(f"❌ Target role {target_role_id} not found in project")
            continue

        print(f"SOURCE: {source['name']} ({source_role_id})")
        print(f"TARGET: {target['name']} ({target_role_id})")

        # current target valid user accountIds
        target_account_ids = set()
        for member in target["members"]:
            if member["type"] != "user":
                continue
            resolved_id, resolved_display, method = resolve_user_account_id(member)
            if resolved_id:
                target_account_ids.add(resolved_id)

        print(f"\nSource actors total: {len(source['members'])}")
        print(f"Target valid users total: {len(target_account_ids)}")

        stats = {
            "source_users": 0,
            "groups_skipped": 0,
            "unknown_skipped": 0,
            "already_in_target_removed_from_source": 0,
            "added_then_removed_from_source": 0,
            "unresolved_users": 0,
            "failed_add": 0,
            "failed_remove": 0
        }

        for member in source["members"]:
            if member["type"] == "group":
                stats["groups_skipped"] += 1
                print(f"  [SKIP GROUP] {member['display']}")
                continue

            if member["type"] != "user":
                stats["unknown_skipped"] += 1
                print(f"  [SKIP UNKNOWN ACTOR] {member['display']}")
                continue

            stats["source_users"] += 1

            resolved_id, resolved_display, method = resolve_user_account_id(member)

            if not resolved_id:
                stats["unresolved_users"] += 1
                print(
                    f"  [SKIP UNRESOLVED] {resolved_display} | "
                    f"method={method} | legacy={member.get('legacy_name')} | raw_account_id={member.get('raw_account_id')}"
                )
                continue

            # CASE 1: already in target -> remove from source
            if resolved_id in target_account_ids:
                print(f"  [ALREADY IN TARGET] {resolved_display} ({resolved_id})")
                ok_remove, _ = remove_user_from_role(source["url"], resolved_id, resolved_display)
                if ok_remove:
                    stats["already_in_target_removed_from_source"] += 1
                else:
                    stats["failed_remove"] += 1

                time.sleep(SLEEP_BETWEEN_CALLS)
                continue

            # CASE 2: not in target -> add first
            ok_add, _ = add_user_to_role(target["url"], resolved_id, resolved_display)
            if not ok_add:
                stats["failed_add"] += 1
                time.sleep(SLEEP_BETWEEN_CALLS)
                continue

            # add succeeded -> update in-memory target ids
            target_account_ids.add(resolved_id)

            # now safe to remove from source
            ok_remove, _ = remove_user_from_role(source["url"], resolved_id, resolved_display)
            if ok_remove:
                stats["added_then_removed_from_source"] += 1
            else:
                stats["failed_remove"] += 1

            time.sleep(SLEEP_BETWEEN_CALLS)

        print("\n--- SUMMARY ---")
        for k, v in stats.items():
            print(f"{k}: {v}")

    print("\n✅ DONE")



if __name__ == "__main__":
    main()

