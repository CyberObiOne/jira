import csv
import json
import time
import threading
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.auth import HTTPBasicAuth
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# CONFIG
# ============================================================

EMAIL = "user@example.com"
API_TOKEN = "TOKEN"


CLOUD_ID = "" # https://my-site-name.atlassian.net/_edge/tenant_info
WORKSPACE_ID = ""

OBJECT_TYPE_ID = "64"

# Trigger attribute
TRIGGER_ATTRIBUTE_NAME = "Serial Number"

# Date + actor conditions
CHANGED_DATE = "2026-07-28"
CHANGED_BY_ACTOR_KEY = "712020:a5e0b39a-283a-42ab-8973-07b815cad4b0"

INPUT_FILE = "affected_objects.csv"

REPORT_FILE = "rollback_report.csv"
ERROR_FILE = "rollback_errors.csv"
RAW_MATCHES_FILE = "rollback_raw_history_matches.json"

# First run must be DRY_RUN
# MODE = "DRY_RUN"
MODE = "APPLY"

# For testing. Set to None to process all rows.
TEST_LIMIT = 5
# TEST_LIMIT = None

MAX_WORKERS = 8

REQUEST_TIMEOUT = 60
MAX_RETRIES = 6


# ============================================================
# API SETUP
# ============================================================

BASE_URL = (
    f"https://api.atlassian.com/ex/jira/{CLOUD_ID}"
    f"/jsm/assets/workspace/{WORKSPACE_ID}/v1"
)

AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)

HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}

_thread_local = threading.local()


def get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        session.auth = AUTH
        session.headers.update(HEADERS)
        _thread_local.session = session
    return _thread_local.session


# ============================================================
# HTTP HELPERS
# ============================================================

def request_with_retry(
    method: str,
    url: str,
    *,
    json_payload: Optional[Dict[str, Any]] = None,
) -> requests.Response:
    session = get_session()
    last_response = None

    for attempt in range(1, MAX_RETRIES + 1):
        response = session.request(
            method=method,
            url=url,
            json=json_payload,
            timeout=REQUEST_TIMEOUT,
        )

        last_response = response

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            sleep_for = int(retry_after) if retry_after and retry_after.isdigit() else attempt * 10
            print(f"[429] Rate limited. Sleeping {sleep_for}s...")
            time.sleep(sleep_for)
            continue

        if response.status_code in (500, 502, 503, 504):
            sleep_for = attempt * 5
            print(
                f"[{response.status_code}] Temporary error. "
                f"Attempt {attempt}/{MAX_RETRIES}. Sleeping {sleep_for}s..."
            )
            time.sleep(sleep_for)
            continue

        if response.status_code >= 400:
            raise requests.HTTPError(
                f"{response.status_code} error for {url}: {response.text}",
                response=response,
            )

        return response

    if last_response is not None:
        last_response.raise_for_status()

    raise RuntimeError(f"Request failed: {method} {url}")


# ============================================================
# GENERIC HELPERS
# ============================================================

def as_list(payload: Any) -> List[Any]:
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in ("values", "objectEntries", "entries", "results", "objects"):
            value = payload.get(key)
            if isinstance(value, list):
                return value

    return []


def parse_dt(value: str) -> datetime:
    value = value.replace("Z", "+00:00")
    return datetime.fromisoformat(value)


def normalize_value(value: Any) -> Optional:
    if value is None:
        return None

    if isinstance(value, str):
        if value.strip() == "":
            return None
        if value.strip().lower() in ("none", "null"):
            return None
        return value

    if isinstance(value, dict):
        for key in ("value", "displayValue", "searchValue", "label", "name", "text"):
            if value.get(key) not in (None, ""):
                return str(value.get(key))

        return json.dumps(value, ensure_ascii=False)

    if isinstance(value, list):
        if not value:
            return None

        if len(value) == 1:
            return normalize_value(value[0])

        values = [normalize_value(v) for v in value]
        values = [v for v in values if v not in (None, "")]
        return "; ".join(values) if values else None

    return str(value)


def is_null_rollback_value(value: Any) -> bool:
    if value is None:
        return True

    if isinstance(value, str) and value.strip().lower() in ("", "none", "null"):
        return True

    return False


def object_id_from_key(object_key: str) -> Optional:
    if not object_key:
        return None

    candidate = object_key.split("-")[-1].strip()

    if candidate.isdigit():
        return candidate

    return None


# ============================================================
# INPUT
# ============================================================

def load_input_objects() -> List[Dict[str, str]]:
    objects = []

    with open(INPUT_FILE, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            object_id = (
                row.get("object_id")
                or row.get("id")
                or row.get("Object ID")
                or row.get("ObjectId")
                or ""
            )

            object_key = (
                row.get("object_key")
                or row.get("key")
                or row.get("Object Key")
                or row.get("ObjectKey")
                or ""
            )

            object_id = str(object_id).strip()
            object_key = str(object_key).strip()

            if not object_id and object_key:
                object_id = object_id_from_key(object_key) or ""

            if not object_id:
                objects.append(
                    {
                        "object_id": "",
                        "object_key": object_key,
                        "input_error": "No object_id and could not derive id from object_key",
                    }
                )
                continue

            objects.append(
                {
                    "object_id": object_id,
                    "object_key": object_key,
                    "input_error": "",
                }
            )

    # Deduplicate by object_id
    deduped = {}
    missing_counter = 0

    for obj in objects:
        object_id = obj["object_id"]

        if object_id:
            deduped[object_id] = obj
        else:
            missing_counter += 1
            deduped[f"missing_{missing_counter}"] = obj

    result = list(deduped.values())

    if TEST_LIMIT is not None:
        result = result[:TEST_LIMIT]

    return result


# ============================================================
# ASSETS API
# ============================================================

def get_object(object_id: str) -> Dict[str, Any]:
    url = f"{BASE_URL}/object/{object_id}"
    response = request_with_retry("GET", url)
    return response.json()


def get_object_history(object_id: str) -> Any:
    url = f"{BASE_URL}/object/{object_id}/history"
    response = request_with_retry("GET", url)
    return response.json()


def update_object_attributes(object_id: str, payload_attributes: List[Dict[str, Any]]) -> None:
    url = f"{BASE_URL}/object/{object_id}"

    payload = {
        "objectTypeId": str(OBJECT_TYPE_ID),
        "attributes": payload_attributes,
    }

    request_with_retry("PUT", url, json_payload=payload)


# ============================================================
# OBJECT ATTRIBUTE METADATA
# ============================================================

def build_attribute_name_to_metadata(obj: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Builds mapping:
    attribute name -> {
        objectTypeAttributeId,
        minimumCardinality,
        maximumCardinality,
        defaultTypeName
    }
    """
    mapping = {}

    for attr in obj.get("attributes", []):
        ota = attr.get("objectTypeAttribute") or {}

        attr_name = str(ota.get("name") or "").strip()
        attr_id = (
            attr.get("objectTypeAttributeId")
            or ota.get("id")
            or ota.get("objectTypeAttributeId")
        )

        if not attr_name or not attr_id:
            continue

        default_type = ota.get("defaultType") or {}

        mapping[attr_name] = {
            "objectTypeAttributeId": str(attr_id),
            "minimumCardinality": ota.get("minimumCardinality"),
            "maximumCardinality": ota.get("maximumCardinality"),
            "defaultTypeName": default_type.get("name") or "",
            "raw": ota,
        }

    return mapping


def get_current_value_by_attribute_name(obj: Dict[str, Any], attr_name: str) -> Optional[str]:
    for attr in obj.get("attributes", []):
        ota = attr.get("objectTypeAttribute") or {}
        name = str(ota.get("name") or "").strip()

        if name != attr_name:
            continue

        values = attr.get("objectAttributeValues", [])

        if not values:
            return None

        return normalize_value(values)

    return None


# ============================================================
# HISTORY LOGIC
# ============================================================

def history_entry_matches_base_conditions(entry: Dict[str, Any]) -> bool:
    created = str(entry.get("created") or "").strip()
    actor_key = str(entry.get("actor", {}).get("key") or "").strip()

    if not created.startswith(CHANGED_DATE):
        return False

    if actor_key != CHANGED_BY_ACTOR_KEY:
        return False

    return True


def object_has_trigger_serial_change(history_entries: List[Dict[str, Any]]) -> bool:
    """
    Object is eligible for rollback only if Serial Number was changed
    by target actor on target date.
    """
    for entry in history_entries:
        if not isinstance(entry, dict):
            continue

        if not history_entry_matches_base_conditions(entry):
            continue

        affected_attribute = str(entry.get("affectedAttribute") or "").strip()

        if affected_attribute == TRIGGER_ATTRIBUTE_NAME:
            return True

    return False


def build_rollback_plan_from_history(history_payload: Any) -> Optional[Dict[str, Any]]:
    """
    Conditions:
    1. Object must have Serial Number changed on CHANGED_DATE by CHANGED_BY_ACTOR_KEY.
    2. If yes, rollback ALL attributes changed by same actor during that whole date.
    3. For each attribute, use earliest oldValue from that day.
    """
    raw_entries = as_list(history_payload)

    entries = [e for e in raw_entries if isinstance(e, dict)]

    if not object_has_trigger_serial_change(entries):
        return None

    matching_entries = []

    for entry in entries:
        if not history_entry_matches_base_conditions(entry):
            continue

        affected_attribute = str(entry.get("affectedAttribute") or "").strip()

        if not affected_attribute:
            continue

        old_value_raw = entry.get("oldValue")
        new_value_raw = entry.get("newValue")

        created = str(entry.get("created") or "").strip()

        try:
            created_dt = parse_dt(created)
        except Exception:
            created_dt = datetime.max

        matching_entries.append(
            {
                "attribute_name": affected_attribute,
                "old_value_raw": old_value_raw,
                "old_value": normalize_value(old_value_raw),
                "new_value_raw": new_value_raw,
                "new_value": normalize_value(new_value_raw),
                "created": created,
                "created_dt": created_dt,
                "history_id": entry.get("id") or "",
                "raw_entry": entry,
            }
        )

    if not matching_entries:
        return None

    # For each attribute, pick earliest oldValue for the day.
    by_attribute: Dict[str, List[Dict[str, Any]]] = {}

    for item in matching_entries:
        by_attribute.setdefault(item["attribute_name"], []).append(item)

    rollback_attributes = {}

    for attr_name, changes in by_attribute.items():
        changes.sort(key=lambda x: x["created_dt"])

        earliest = changes[0]
        latest = changes[-1]

        rollback_attributes[attr_name] = {
            "rollback_to": earliest["old_value"],
            "rollback_to_raw": earliest["old_value_raw"],
            "first_change_at": earliest["created"],
            "first_change_new_value": earliest["new_value"],
            "last_change_at": latest["created"],
            "last_change_new_value": latest["new_value"],
            "history_entries_count": len(changes),
            "history_ids": ";".join([c["history_id"] for c in changes if c["history_id"]]),
            "raw_entries": [c["raw_entry"] for c in changes],
        }

    return {
        "rollback_attributes": rollback_attributes,
        "raw_matching_entries": [m["raw_entry"] for m in matching_entries],
    }


# ============================================================
# PAYLOAD BUILDING
# ============================================================

def build_update_payload_attributes(
    obj: Dict[str, Any],
    rollback_attributes: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Returns:
    - payload attributes for PUT
    - skipped attributes with reason
    """
    attr_metadata = build_attribute_name_to_metadata(obj)

    payload_attributes: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for attr_name, rollback_info in rollback_attributes.items():
        meta = attr_metadata.get(attr_name)

        if not meta:
            skipped.append(
                {
                    "attribute_name": attr_name,
                    "reason": "Attribute exists in history but was not found in current object metadata",
                }
            )
            continue

        attr_id = meta["objectTypeAttributeId"]
        min_cardinality = meta.get("minimumCardinality")
        rollback_value = rollback_info["rollback_to"]

        # If oldValue was None, we clear the attribute.
        if is_null_rollback_value(rollback_value):
            if min_cardinality == 1:
                skipped.append(
                    {
                        "attribute_name": attr_name,
                        "reason": "Rollback value is NULL but attribute minimumCardinality=1. Skipped to avoid invalid object update.",
                    }
                )
                continue

            payload_attributes.append(
                {
                    "objectTypeAttributeId": str(attr_id),
                    "objectAttributeValues": [],
                }
            )

        else:
            payload_attributes.append(
                {
                    "objectTypeAttributeId": str(attr_id),
                    "objectAttributeValues": [
                        {
                            "value": rollback_value
                        }
                    ],
                }
            )

    return payload_attributes, skipped


# ============================================================
# WORKER
# ============================================================

def process_object(input_obj: Dict[str, str]) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Returns:
    - report rows
    - raw row
    - error rows
    """
    report_rows: List[Dict[str, Any]] = []
    error_rows: List[Dict[str, Any]] = []

    object_id = input_obj.get("object_id", "")
    input_object_key = input_obj.get("object_key", "")

    if input_obj.get("input_error"):
        error_rows.append(
            {
                "object_id": object_id,
                "object_key": input_object_key,
                "attribute_name": "",
                "error": input_obj["input_error"],
            }
        )
        return report_rows, None, error_rows

    try:
        obj = get_object(object_id)

        object_key = obj.get("objectKey") or input_object_key
        label = obj.get("label") or ""

        object_type_id = str(
            obj.get("objectType", {}).get("id")
            or obj.get("objectTypeId")
            or ""
        )

        if object_type_id and object_type_id != str(OBJECT_TYPE_ID):
            error_rows.append(
                {
                    "object_id": object_id,
                    "object_key": object_key,
                    "attribute_name": "",
                    "error": f"Object type mismatch. Expected {OBJECT_TYPE_ID}, got {object_type_id}",
                }
            )
            return report_rows, None, error_rows

        history = get_object_history(object_id)

        rollback_plan = build_rollback_plan_from_history(history)

        if not rollback_plan:
            error_rows.append(
                {
                    "object_id": object_id,
                    "object_key": object_key,
                    "attribute_name": "",
                    "error": (
                        f"No trigger Serial Number change found for "
                        f"date={CHANGED_DATE}, actor.key={CHANGED_BY_ACTOR_KEY}"
                    ),
                }
            )
            return report_rows, None, error_rows

        rollback_attributes = rollback_plan["rollback_attributes"]

        payload_attributes, skipped_attributes = build_update_payload_attributes(
            obj,
            rollback_attributes,
        )

        skipped_by_name = {
            item["attribute_name"]: item["reason"]
            for item in skipped_attributes
        }

        action = "DRY_RUN"

        if MODE.upper() == "APPLY":
            if payload_attributes:
                update_object_attributes(object_id, payload_attributes)
                action = "RESTORED"
            else:
                action = "NOTHING_TO_UPDATE"

        for attr_name, info in rollback_attributes.items():
            current_value = get_current_value_by_attribute_name(obj, attr_name)
            skip_reason = skipped_by_name.get(attr_name, "")

            if skip_reason:
                row_action = "SKIPPED"
            else:
                row_action = action

            report_rows.append(
                {
                    "object_id": object_id,
                    "object_key": object_key,
                    "label": label,
                    "object_type_id": object_type_id,
                    "attribute_name": attr_name,
                    "current_value": current_value or "",
                    "rollback_to": "" if info["rollback_to"] is None else info["rollback_to"],
                    "rollback_to_is_null": str(is_null_rollback_value(info["rollback_to"])),
                    "first_change_at": info["first_change_at"],
                    "first_change_new_value": info["first_change_new_value"] or "",
                    "last_change_at": info["last_change_at"],
                    "last_change_new_value": info["last_change_new_value"] or "",
                    "history_entries_count": info["history_entries_count"],
                    "history_ids": info["history_ids"],
                    "changed_date": CHANGED_DATE,
                    "changed_by_actor_key": CHANGED_BY_ACTOR_KEY,
                    "trigger_attribute": TRIGGER_ATTRIBUTE_NAME,
                    "action": row_action,
                    "skip_reason": skip_reason,
                }
            )

        raw_row = {
            "object_id": object_id,
            "object_key": object_key,
            "label": label,
            "raw_matching_history_entries": rollback_plan["raw_matching_entries"],
        }

        return report_rows, raw_row, error_rows

    except Exception as e:
        error_rows.append(
            {
                "object_id": object_id,
                "object_key": input_object_key,
                "attribute_name": "",
                "error": str(e),
            }
        )
        return report_rows, None, error_rows


# ============================================================
# OUTPUT
# ============================================================

def write_csv(path: str, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ============================================================
# MAIN
# ============================================================

def main():
    if MODE.upper() not in ("DRY_RUN", "APPLY"):
        raise ValueError("MODE must be DRY_RUN or APPLY")

    print("============================================================")
    print("Assets rollback by known affected objects")
    print("============================================================")
    print(f"Input file:              {INPUT_FILE}")
    print(f"Mode:                    {MODE}")
    print(f"Test limit:              {TEST_LIMIT}")
    print(f"Object Type ID:          {OBJECT_TYPE_ID}")
    print(f"Trigger Attribute:       {TRIGGER_ATTRIBUTE_NAME}")
    print(f"Changed Date:            {CHANGED_DATE}")
    print(f"Changed By actor.key:    {CHANGED_BY_ACTOR_KEY}")
    print(f"Workers:                 {MAX_WORKERS}")
    print("============================================================")
    print("")

    input_objects = load_input_objects()

    print(f"Objects loaded from input: {len(input_objects)}")
    print("Processing objects in parallel...")
    print("")

    all_report_rows: List[Dict[str, Any]] = []
    all_error_rows: List[Dict[str, Any]] = []
    raw_rows: List[Dict[str, Any]] = []

    completed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {
            executor.submit(process_object, obj): obj
            for obj in input_objects
        }

        for future in as_completed(future_map):
            completed += 1

            try:
                report_rows, raw_row, error_rows = future.result()
            except Exception as e:
                report_rows = []
                raw_row = None
                input_obj = future_map[future]
                error_rows = [
                    {
                        "object_id": input_obj.get("object_id", ""),
                        "object_key": input_obj.get("object_key", ""),
                        "attribute_name": "",
                        "error": f"Unhandled worker error: {e}",
                    }
                ]

            if report_rows:
                all_report_rows.extend(report_rows)

                object_key = report_rows[0]["object_key"]
                attr_count = len(report_rows)
                skipped_count = len([r for r in report_rows if r["action"] == "SKIPPED"])

                print(
                    f"[MATCH] {object_key} | "
                    f"attributes_to_rollback={attr_count} | "
                    f"skipped={skipped_count} | "
                    f"mode={MODE}"
                )

            if raw_row:
                raw_rows.append(raw_row)

            if error_rows:
                all_error_rows.extend(error_rows)

                for err in error_rows:
                    print(
                        f"[ERROR] object_id={err.get('object_id')} "
                        f"object_key={err.get('object_key')} "
                        f"attr={err.get('attribute_name')} "
                        f"error={err.get('error')}"
                    )

            if completed % 50 == 0 or completed == len(input_objects):
                print(
                    f"Progress: {completed}/{len(input_objects)} | "
                    f"report_rows={len(all_report_rows)} | "
                    f"errors={len(all_error_rows)}"
                )

    all_report_rows.sort(
        key=lambda r: (
            r["object_key"],
            r["attribute_name"],
            r["first_change_at"],
        )
    )

    report_fields = [
        "object_id",
        "object_key",
        "label",
        "object_type_id",
        "attribute_name",
        "current_value",
        "rollback_to",
        "rollback_to_is_null",
        "first_change_at",
        "first_change_new_value",
        "last_change_at",
        "last_change_new_value",
        "history_entries_count",
        "history_ids",
        "changed_date",
        "changed_by_actor_key",
        "trigger_attribute",
        "action",
        "skip_reason",
    ]

    error_fields = [
        "object_id",
        "object_key",
        "attribute_name",
        "error",
    ]

    write_csv(REPORT_FILE, all_report_rows, report_fields)
    write_csv(ERROR_FILE, all_error_rows, error_fields)

    with open(RAW_MATCHES_FILE, "w", encoding="utf-8") as f:
        json.dump(raw_rows, f, indent=2, ensure_ascii=False)

    print("")
    print("============================================================")
    print("Done")
    print("============================================================")
    print(f"Input objects processed: {len(input_objects)}")
    print(f"Rollback report rows:    {len(all_report_rows)}")
    print(f"Errors / skipped objs:   {len(all_error_rows)}")
    print(f"Report CSV:              {REPORT_FILE}")
    print(f"Errors CSV:              {ERROR_FILE}")
    print(f"Raw matches JSON:        {RAW_MATCHES_FILE}")

    if MODE.upper() == "DRY_RUN":
        print("")
        print("No Assets objects were updated because MODE = DRY_RUN.")
        print("Проверь rollback_report.csv.")
        print("Если всё корректно — поставь MODE = 'APPLY' и TEST_LIMIT = None.")
    else:
        print("")
        print("Rollback executed because MODE = APPLY.")


if __name__ == "__main__":
    main()
