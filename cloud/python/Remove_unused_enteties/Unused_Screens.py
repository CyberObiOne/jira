import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==========================================================
# CONFIGURATION
# ==========================================================
BASE_URL = "https://example.atlassian.net"
AUTH = ("mail", "token")
HEADERS = {"Accept": "application/json"}

REQUEST_TIMEOUT = 30
MAX_WORKERS = 5
APPLY = False                 # ✅ DRY-RUN ONLY
LOG_FILE = "screens_single_endpoint_cleanup.log"

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
# SESSION
# ==========================================================
session = requests.Session()
session.auth = AUTH
session.headers.update(HEADERS)

# ==========================================================
# GET ALL SCREENS WITH USAGE INFO (PAGINATED)
# ==========================================================
def get_all_screens_with_usage():
    screens = []
    start_at = 0
    max_results = 100

    while True:
        r = session.get(
            f"{BASE_URL}/rest/api/3/screens",
            params={
                "expand": "screenScheme,workflowTransitions",
                "startAt": start_at,
                "maxResults": max_results
            },
            timeout=REQUEST_TIMEOUT
        )
        r.raise_for_status()
        data = r.json()

        screens.extend(data.get("values", []))

        if start_at + max_results >= data.get("total", 0):
            break
        start_at += max_results

    return screens

# ==========================================================
# MAIN
# ==========================================================
def main():
    logger.info("Starting Screens cleanup using single endpoint logic")
    logger.info("Rule: screenSchemes.values == [] AND workflowTransitions.values == []")
    logger.info("DRY-RUN mode enabled")

    screens = get_all_screens_with_usage()
    total = len(screens)

    logger.info(f"Total screens fetched: {total}")

    orphaned = []

    # -----------------------------
    # Detect orphaned screens
    # -----------------------------
    for sc in screens:
        schemes = sc.get("screenSchemes", {}).get("values", [])
        transitions = sc.get("workflowTransitions", {}).get("values", [])

        if not schemes and not transitions:
            orphaned.append(sc)

    logger.info(f"Orphaned screens found: {len(orphaned)}")

    # -----------------------------
    # Output with progress bar
    # -----------------------------
    completed = 0
    total_orphaned = len(orphaned)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(
                lambda s: logger.info(f"{s['name']} - {s['id']}"),
                screen
            )
            for screen in orphaned
        ]

        for _ in as_completed(futures):
            completed += 1
            if completed % 25 == 0 or completed == total_orphaned:
                logger.info(f"Progress: {completed}/{total_orphaned} screens listed")

    logger.info("Screens orphan detection finished")

# ==========================================================
# ENTRY POINT
# ==========================================================
if __name__ == "__main__":
    main()
