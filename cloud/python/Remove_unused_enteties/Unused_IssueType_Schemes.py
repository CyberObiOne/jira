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
MAX_WORKERS = 4
LOG_FILE = "itss_project_api.log"

# ==========================================================
# LOGGING
# ==========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ==========================================================
# SESSION WITH RETRY / BACKOFF
# ==========================================================
retry = Retry(
    total=5,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"]
)
adapter = HTTPAdapter(max_retries=retry)

session = requests.Session()
session.auth = AUTH
session.headers.update(HEADERS)
session.mount("https://", adapter)

# ==========================================================
# GET ALL PROJECT IDS (ACTIVE + ARCHIVED)
# ==========================================================
def get_all_project_ids():
    r = session.get(
        f"{BASE_URL}/rest/api/3/project",
        timeout=REQUEST_TIMEOUT
    )
    r.raise_for_status()
    projects = r.json()

    return [p["id"] for p in projects]

# ==========================================================
# GET ALL ISSUE TYPE SCREEN SCHEMES
# ==========================================================
def get_all_itss():
    schemes = []
    start_at = 0

    while True:
        r = session.get(
            f"{BASE_URL}/rest/api/3/issuetypescreenscheme",
            params={"startAt": start_at, "maxResults": 50},
            timeout=REQUEST_TIMEOUT
        )
        r.raise_for_status()
        data = r.json()

        schemes.extend(data.get("values", []))

        if start_at + 50 >= data.get("total", 0):
            break
        start_at += 50

    return schemes

# ==========================================================
# GET ITSS USED BY A PROJECT
# ==========================================================
def get_itss_for_project(project_id):
    used = set()
    start_at = 0

    while True:
        r = session.get(
            f"{BASE_URL}/rest/api/3/issuetypescreenscheme/project",
            params={
                "projectId": project_id,
                "startAt": start_at,
                "maxResults": 50
            },
            timeout=REQUEST_TIMEOUT
        )
        r.raise_for_status()
        data = r.json()

        for v in data.get("values", []):
            used.add(v["issueTypeScreenScheme"]["id"])

        if start_at + 50 >= data.get("total", 0):
            break
        start_at += 50

    return used

# ==========================================================
# MAIN
# ==========================================================
def main():
    logger.info("Starting ITSS discovery using /rest/api/3/project")
    logger.info("Archived projects are INCLUDED")

    project_ids = get_all_project_ids()
    all_itss = get_all_itss()

    logger.info(f"Total projects fetched: {len(project_ids)}")
    logger.info(f"Total ITSS fetched: {len(all_itss)}")

    used_itss_ids = set()
    total_projects = len(project_ids)
    completed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(get_itss_for_project, pid)
            for pid in project_ids
        ]

        for future in as_completed(futures):
            completed += 1
            used_itss_ids.update(future.result())

            if completed % 10 == 0 or completed == total_projects:
                logger.info(f"Progress: {completed}/{total_projects} projects processed")

    orphaned = [
        s for s in all_itss
        if s["id"] != "1"
        and s["id"] not in used_itss_ids
    ]

    logger.info(f"Orphaned ITSS found: {len(orphaned)}")

    for s in orphaned:
        logger.info(f"[ORPHANED] {s['id']} | {s['name']}")

    logger.info("ITSS analysis finished")

# ==========================================================
# ENTRY POINT
# ==========================================================
if __name__ == "__main__":
    main()
