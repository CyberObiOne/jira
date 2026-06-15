import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ==========================================================
# CONFIGURATION
# ==========================================================
BASE_URL = "https://example.atlassian.net"
AUTH = ("mail", "token")
HEADERS = {"Accept": "application/json"}

REQUEST_TIMEOUT = 30
MAX_WORKERS = 5
APPLY = False          # ❗ SET True TO DELETE
LOG_FILE = "screen_scheme_cleanup.log"

# ==========================================================
# LOGGING
# ==========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==========================================================
# SESSION WITH RETRY / BACKOFF
# ==========================================================
retry = Retry(
    total=5,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "DELETE"]
)
adapter = HTTPAdapter(max_retries=retry)

session = requests.Session()
session.auth = AUTH
session.headers.update(HEADERS)
session.mount("https://", adapter)

# ==========================================================
# GET USED SCREEN SCHEME IDS (MAPPING — PAGINATED ✅)
# ==========================================================
def get_used_screen_scheme_ids():
    used_ids = set()
    start_at = 0
    max_results = 50

    while True:
        r = session.get(
            f"{BASE_URL}/rest/api/3/issuetypescreenscheme/mapping",
            params={"startAt": start_at, "maxResults": max_results},
            timeout=REQUEST_TIMEOUT
        )
        r.raise_for_status()

        data = r.json()
        values = data.get("values", [])

        for m in values:
            # ✅ normalize to string
            used_ids.add(str(m["screenSchemeId"]))

        if start_at + max_results >= data.get("total", 0):
            break

        start_at += max_results

    return used_ids

# ==========================================================
# GET ALL SCREEN SCHEMES
# ==========================================================
def get_all_screen_schemes():
    schemes = []
    start_at = 0
    max_results = 50

    while True:
        r = session.get(
            f"{BASE_URL}/rest/api/3/screenscheme",
            params={"startAt": start_at, "maxResults": max_results},
            timeout=REQUEST_TIMEOUT
        )
        r.raise_for_status()

        data = r.json()
        schemes.extend(data.get("values", []))

        if start_at + max_results >= data.get("total", 0):
            break

        start_at += max_results

    return schemes

# ==========================================================
# DELETE / DRY‑RUN
# ==========================================================
def delete_screen_scheme(scheme):
    sid = scheme["id"]
    name = scheme["name"]

    if not APPLY:
        logger.info(f"[DRY-RUN] Would delete Screen Scheme: {name} - {sid}")
        return True

    r = session.delete(
        f"{BASE_URL}/rest/api/3/screenscheme/{sid}",
        timeout=REQUEST_TIMEOUT
    )

    if r.status_code == 204:
        logger.info(f"[DELETED] {name} - {sid}")
        return True
    else:
        logger.error(f"[FAILED] {name} - {sid} (HTTP {r.status_code})")
        return False

# ==========================================================
# MAIN
# ==========================================================
def main():
    logger.info("Starting Screen Scheme cleanup")
    logger.info(f"APPLY mode: {APPLY}")

    used_scheme_ids = get_used_screen_scheme_ids()
    all_schemes = get_all_screen_schemes()

    logger.info(f"Used Screen Schemes (mapping): {len(used_scheme_ids)}")
    logger.info(f"Total Screen Schemes: {len(all_schemes)}")

    orphaned = [
        s for s in all_schemes
        if str(s["id"]) not in used_scheme_ids
    ]

    logger.info(f"Orphaned Screen Schemes found: {len(orphaned)}")

    # ======================================================
    # DELETE WITH PROGRESS BAR
    # ======================================================
    completed = 0
    total = len(orphaned)

    logger.info("Starting deletion phase")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(delete_screen_scheme, s): s
            for s in orphaned
        }

        for future in as_completed(futures):
            completed += 1
            future.result()

            if completed % 10 == 0 or completed == total:
                logger.info(f"Progress: {completed}/{total} screen schemes processed")

    logger.info("Screen Scheme cleanup finished")

# ==========================================================
# ENTRY POINT
# ==========================================================
if __name__ == "__main__":
    main()
