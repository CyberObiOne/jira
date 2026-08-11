import csv
import json
import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.auth import HTTPBasicAuth
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# CONFIG
# ============================================================

EMAIL = "user@example.com"
API_TOKEN = "TOKEN"


CLOUD_ID = "cloud_id"  # https://my-site-name.atlassian.net/_edge/tenant_info
WORKSPACE_ID = "workspace_id"

# Here is some attributes we will use to limit objects
OBJECT_SCHEMA_ID = "9"
OBJECT_TYPE_ID = "64"

SERIAL_ATTR_ID = "918"
SERIAL_ATTR_HISTORY_NAME = "Serial Number"

CHANGED_DATE = "2026-07-28"
CHANGED_BY_ACTOR_KEY = ""

# First run: REPORT_ONLY
# After CSV validation: APPLY
MODE = "REPORT_ONLY"
# MODE = "APPLY"

# Your tenant returns 25 objects and ignores startAt.
AQL_FETCH_LIMIT = 25

# Based on your tests. Widen if collected count != 43435.
OBJECT_ID_MIN = 0
OBJECT_ID_MAX = 1000000

# Range splitting.
INITIAL_BUCKET_SIZE = 10000

# Parallelism.
RANGE_COUNT_WORKERS = 24
OBJECT_FETCH_WORKERS = 16
HISTORY_WORKERS = 10

REQUEST_TIMEOUT = 60
MAX_RETRIES = 6

REPORT_FILE = "assets_serial_number_affected_objects.csv"
ERROR_FILE = "assets_serial_number_errors.csv"
RAW_MATCHES_FILE = "assets_serial_number_raw_matching_history.json"
ALL_OBJECTS_FILE = "assets_type64_all_objects.csv"


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
        s = requests.Session()
        s.auth = AUTH
        s.headers.update(HEADERS)
        _thread_local.session = s
    return _thread_local.session


# ============================================================
# HTTP
# ============================================================

def request_with_retry(method: str, url: str, json_payload: Optional[Dict[str, Any]] = None) -> requests.Response:
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
            sleep_for = int(retry_after) if retry_after and retry_after.isdigit() else attempt * 5
            print(f"[429] Sleeping {sleep_for}s")
            time.sleep(sleep_for)
            continue

        if response.status_code in (500, 502, 503, 504):
            sleep_for = attempt * 3
            print(f"[{response.status_code}] Sleeping {sleep_for}s")
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
# HELPERS
# ============================================================

def as_list(payload: Any) -> List[Any]:
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in ("values", "objectEntries", "entries", "results", "objects"):
            v = payload.get(key)
            if isinstance(v, list):
                return v

    return []


def normalize_value(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None

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
        values = [v for v in values if v]
        return "; ".join(values) if values else None

    return str(value)


def get_object_id(obj: Dict[str, Any]) -> Optional[str]:
    v = obj.get("id")
    return str(v) if v not in (None, "") else None


def get_current_serial_from_object(obj: Dict[str, Any]) -> Optional[str]:
    for attr in obj.get("attributes", []):
        attr_id = (
            attr.get("objectTypeAttributeId")
            or attr.get("objectTypeAttribute", {}).get("id")
            or attr.get("objectTypeAttribute", {}).get("objectTypeAttributeId")
        )

        if str(attr_id) != str(SERIAL_ATTR_ID):
            continue

        values = attr.get("objectAttributeValues", [])
        if not values:
            return None

        return normalize_value(values[0])

    return None


# ============================================================
# AQL
# ============================================================

def aql_for_range(start_id: int, end_id: int) -> str:
    return (
        f"objectTypeId = {OBJECT_TYPE_ID} "
        f"AND objectId >= {start_id} "
        f"AND objectId <= {end_id}"
    )


def get_total_count_for_aql(aql: str) -> int:
    url = f"{BASE_URL}/object/aql/totalcount"

    r = request_with_retry(
        "POST",
        url,
        json_payload={"qlQuery": aql},
    )

    data = r.json()

    if isinstance(data, int):
        return data

    if isinstance(data, dict):
        for key in ("totalCount", "total", "count"):
            if key in data:
                return int(data[key])

    raise ValueError(f"Cannot parse totalcount response: {data}")


def fetch_objects_for_range(start_id: int, end_id: int) -> List[Dict[str, Any]]:
    url = f"{BASE_URL}/object/aql"

    payload = {
        "qlQuery": aql_for_range(start_id, end_id),
        "maxResults": AQL_FETCH_LIMIT,
        "includeAttributes": True,
    }

    r = request_with_retry("POST", url, json_payload=payload)
    return as_list(r.json())


# ============================================================
# FAST RANGE BUILDING
# ============================================================

def split_range_once(start_id: int, end_id: int, count: int) -> List[Tuple[int, int]]:
    if count <= AQL_FETCH_LIMIT:
        return [(start_id, end_id)]

    if start_id == end_id:
        return [(start_id, end_id)]

    mid = (start_id + end_id) // 2
    return [(start_id, mid), (mid + 1, end_id)]


def build_safe_ranges_parallel() -> List[Tuple[int, int]]:
    """
    Builds ranges where each range has <= 25 objects.
    Uses parallel totalcount calls.
    """
    expected_total = get_total_count_for_aql(f"objectTypeId = {OBJECT_TYPE_ID}")

    print("============================================================")
    print(f"Expected total for typeId={OBJECT_TYPE_ID}: {expected_total}")
    print(f"ObjectId scan range: {OBJECT_ID_MIN}-{OBJECT_ID_MAX}")
    print("============================================================")

    pending: List[Tuple[int, int]] = []

    for start in range(OBJECT_ID_MIN, OBJECT_ID_MAX + 1, INITIAL_BUCKET_SIZE):
        end = min(start + INITIAL_BUCKET_SIZE - 1, OBJECT_ID_MAX)
        pending.append((start, end))

    safe_ranges: List[Tuple[int, int]] = []
    round_no = 1

    while pending:
        print(f"\nRange split round {round_no} | pending ranges: {len(pending)}")

        next_pending: List[Tuple[int, int]] = []

        with ThreadPoolExecutor(max_workers=RANGE_COUNT_WORKERS) as executor:
            future_map = {
                executor.submit(get_total_count_for_aql, aql_for_range(start, end)): (start, end)
                for start, end in pending
            }

            completed = 0

            for future in as_completed(future_map):
                start, end = future_map[future]
                completed += 1

                try:
                    count = future.result()
                except Exception as e:
                    print(f"[COUNT ERROR] {start}-{end}: {e}")
                    # Retry smaller by splitting.
                    if start != end:
                        mid = (start + end) // 2
                        next_pending.append((start, mid))
                        next_pending.append((mid + 1, end))
                    continue

                if count == 0:
                    pass
                elif count <= AQL_FETCH_LIMIT:
                    safe_ranges.append((start, end))
                else:
                    next_pending.extend(split_range_once(start, end, count))

                if completed % 100 == 0 or completed == len(future_map):
                    print(
                        f"  Counted {completed}/{len(future_map)} | "
                        f"safe={len(safe_ranges)} | next={len(next_pending)}"
                    )

        pending = next_pending
        round_no += 1

    print("\n============================================================")
    print(f"Safe ranges built: {len(safe_ranges)}")
    print("============================================================")

    return safe_ranges


def collect_all_objects_fast() -> List[Dict[str, Any]]:
    safe_ranges = build_safe_ranges_parallel()

    objects_by_id: Dict[str, Dict[str, Any]] = {}

    print("\nFetching objects from safe ranges in parallel...")

    with ThreadPoolExecutor(max_workers=OBJECT_FETCH_WORKERS) as executor:
        future_map = {
            executor.submit(fetch_objects_for_range, start, end): (start, end)
            for start, end in safe_ranges
        }

        completed = 0

        for future in as_completed(future_map):
            start, end = future_map[future]
            completed += 1

            try:
                objects = future.result()
            except Exception as e:
                print(f"[FETCH ERROR] {start}-{end}: {e}")
                continue

            for obj in objects:
                object_id = get_object_id(obj)
                if object_id:
                    objects_by_id[object_id] = obj

            if completed % 100 == 0 or completed == len(future_map):
                print(
                    f"  Fetched ranges {completed}/{len(future_map)} | "
                    f"unique objects={len(objects_by_id)}"
                )

    objects = list(objects_by_id.values())

    expected_total = get_total_count_for_aql(f"objectTypeId = {OBJECT_TYPE_ID}")

    print("\n============================================================")
    print(f"Expected total:  {expected_total}")
    print(f"Collected total: {len(objects)}")
    print("============================================================")

    if len(objects) != expected_total:
        print("[WARN] Collected count does not match expected total.")
        print("       Try widening OBJECT_ID_MIN / OBJECT_ID_MAX.")
        print(f"       Current range: {OBJECT_ID_MIN}-{OBJECT_ID_MAX}")

    return objects


# ============================================================
# HISTORY + RESTORE
# ============================================================

def get_object_history(object_id: str) -> Any:
    url = f"{BASE_URL}/object/{object_id}/history"
    r = request_with_retry("GET", url)
    return r.json()


def restore_serial_number(object_id: str, previous_serial: str) -> None:
    url = f"{BASE_URL}/object/{object_id}"

    payload = {
        "objectTypeId": str(OBJECT_TYPE_ID),
        "attributes": [
            {
                "objectTypeAttributeId": str(SERIAL_ATTR_ID),
                "objectAttributeValues": [
                    {"value": previous_serial}
                ],
            }
        ],
    }

    request_with_retry("PUT", url, json_payload=payload)


def find_restore_candidate(history_payload: Any) -> Optional[Dict[str, Any]]:
    entries = as_list(history_payload)
    matches = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        affected = str(entry.get("affectedAttribute") or "").strip()
        created = str(entry.get("created") or "")
        actor_key = str(entry.get("actor", {}).get("key") or "")

        if affected != SERIAL_ATTR_HISTORY_NAME:
            continue

        if not created.startswith(CHANGED_DATE):
            continue

        if actor_key != CHANGED_BY_ACTOR_KEY:
            continue

        old_value = normalize_value(entry.get("oldValue"))
        new_value = normalize_value(entry.get("newValue"))

        if not old_value:
            continue

        matches.append(
            {
                "history_id": entry.get("id") or "",
                "changed_at": created,
                "changed_by": actor_key,
                "previous_serial": old_value,
                "changed_to_serial": new_value or "",
                "raw_entry": entry,
            }
        )

    if not matches:
        return None

    result = matches[-1]
    result["matching_history_entries"] = len(matches)
    return result


def process_object(obj: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    object_id = get_object_id(obj)

    if not object_id:
        return None, None, {
            "object_id": "",
            "object_key": "",
            "label": "",
            "error": "Missing object ID",
        }

    try:
        object_key = obj.get("objectKey") or ""
        label = obj.get("label") or ""
        current_serial = get_current_serial_from_object(obj)

        history = get_object_history(object_id)
        match = find_restore_candidate(history)

        if not match:
            return None, None, None

        previous_serial = match["previous_serial"]

        action = "REPORT_ONLY"

        if MODE.upper() == "APPLY":
            restore_serial_number(object_id, previous_serial)
            action = "RESTORED"

        row = {
            "object_schema_id": OBJECT_SCHEMA_ID,
            "object_type_id": OBJECT_TYPE_ID,
            "object_id": object_id,
            "object_key": object_key,
            "label": label,
            "serial_attribute_id": SERIAL_ATTR_ID,
            "serial_attribute_history_name": SERIAL_ATTR_HISTORY_NAME,
            "current_serial": current_serial or "",
            "previous_serial_to_restore": previous_serial,
            "serial_changed_to": match["changed_to_serial"],
            "changed_date_filter": CHANGED_DATE,
            "changed_at_detected": match["changed_at"],
            "changed_by_filter": CHANGED_BY_ACTOR_KEY,
            "changed_by_detected": match["changed_by"],
            "history_id": match["history_id"],
            "matching_history_entries": match["matching_history_entries"],
            "action": action,
        }

        raw = {
            "object_id": object_id,
            "object_key": object_key,
            "label": label,
            "raw_matching_history_entry": match["raw_entry"],
        }

        return row, raw, None

    except Exception as e:
        return None, None, {
            "object_id": object_id,
            "object_key": obj.get("objectKey") or "",
            "label": obj.get("label") or "",
            "error": str(e),
        }


# ============================================================
# CSV
# ============================================================

def write_csv(path: str, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_all_objects_csv(objects: List[Dict[str, Any]]) -> None:
    rows = []

    for obj in objects:
        rows.append(
            {
                "object_id": obj.get("id") or "",
                "object_key": obj.get("objectKey") or "",
                "label": obj.get("label") or "",
            }
        )

    write_csv(
        ALL_OBJECTS_FILE,
        rows,
        ["object_id", "object_key", "label"],
    )


# ============================================================
# MAIN
# ============================================================

def main():
    if MODE.upper() not in ("REPORT_ONLY", "APPLY"):
        raise ValueError("MODE must be REPORT_ONLY or APPLY")

    print("============================================================")
    print("FAST Assets Serial Number restore checker")
    print("============================================================")
    print(f"Mode:                 {MODE}")
    print(f"Object Type ID:       {OBJECT_TYPE_ID}")
    print(f"Serial Attr ID:       {SERIAL_ATTR_ID}")
    print(f"History Attr Name:    {SERIAL_ATTR_HISTORY_NAME}")
    print(f"Changed Date:         {CHANGED_DATE}")
    print(f"Changed By actor.key: {CHANGED_BY_ACTOR_KEY}")
    print("============================================================")

    objects = collect_all_objects_fast()
    write_all_objects_csv(objects)

    print(f"\nAll collected objects written to: {ALL_OBJECTS_FILE}")
    print(f"Objects to check history for: {len(objects)}")
    print("\nChecking object history in parallel...\n")

    affected_rows: List[Dict[str, Any]] = []
    error_rows: List[Dict[str, Any]] = []
    raw_matches: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=HISTORY_WORKERS) as executor:
        future_map = {
            executor.submit(process_object, obj): obj
            for obj in objects
        }

        completed = 0

        for future in as_completed(future_map):
            obj = future_map[future]
            completed += 1

            try:
                row, raw, error = future.result()
            except Exception as e:
                row = None
                raw = None
                error = {
                    "object_id": obj.get("id") or "",
                    "object_key": obj.get("objectKey") or "",
                    "label": obj.get("label") or "",
                    "error": f"Unhandled error: {e}",
                }

            if row:
                affected_rows.append(row)
                print(
                    f"[MATCH] {row['object_key']} | "
                    f"current='{row['current_serial']}' | "
                    f"restore_to='{row['previous_serial_to_restore']}' | "
                    f"changed_to='{row['serial_changed_to']}' | "
                    f"action={row['action']}"
                )

            if raw:
                raw_matches.append(raw)

            if error:
                error_rows.append(error)
                print(
                    f"[ERROR] object_id={error.get('object_id')} "
                    f"object_key={error.get('object_key')} "
                    f"error={error.get('error')}"
                )

            if completed % 250 == 0 or completed == len(objects):
                print(
                    f"Progress: {completed}/{len(objects)} | "
                    f"matches={len(affected_rows)} | "
                    f"errors={len(error_rows)}"
                )

    affected_rows.sort(key=lambda r: (r["object_key"], r["object_id"]))

    report_fields = [
        "object_schema_id",
        "object_type_id",
        "object_id",
        "object_key",
        "label",
        "serial_attribute_id",
        "serial_attribute_history_name",
        "current_serial",
        "previous_serial_to_restore",
        "serial_changed_to",
        "changed_date_filter",
        "changed_at_detected",
        "changed_by_filter",
        "changed_by_detected",
        "history_id",
        "matching_history_entries",
        "action",
    ]

    error_fields = [
        "object_id",
        "object_key",
        "label",
        "error",
    ]

    write_csv(REPORT_FILE, affected_rows, report_fields)
    write_csv(ERROR_FILE, error_rows, error_fields)

    with open(RAW_MATCHES_FILE, "w", encoding="utf-8") as f:
        json.dump(raw_matches, f, indent=2, ensure_ascii=False)

    print("\n============================================================")
    print("Done")
    print("============================================================")
    print(f"Objects checked:        {len(objects)}")
    print(f"Affected objects found: {len(affected_rows)}")
    print(f"Errors:                 {len(error_rows)}")
    print(f"All objects CSV:        {ALL_OBJECTS_FILE}")
    print(f"Report CSV:             {REPORT_FILE}")
    print(f"Errors CSV:             {ERROR_FILE}")
    print(f"Raw matches JSON:       {RAW_MATCHES_FILE}")

    if MODE.upper() == "REPORT_ONLY":
        print("\nNo Assets objects were updated because MODE = REPORT_ONLY.")
        print("Validate the CSV, then change MODE = 'APPLY' to restore.")
    else:
        print("\nRestore executed because MODE = APPLY.")


if __name__ == "__main__":
    main()
