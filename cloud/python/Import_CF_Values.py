import requests

JIRA_URL = "https://example.atlassian.net/"
EMAIL = "username@example.com"
API_TOKEN = "API_TOKEN"

FIELD_ID = "customfield_21570"
CONTEXT_ID = "80570"

# --- SETTINGS ---
DRY_RUN = False
BATCH_SIZE = 100

# --- VALUES (YOUR LIST) ---
values = [
    "Accredible","Adobe","Google","AG Grid","Anaconda","Aha","Alfresco","AWS","Apache","Arcade"
]

# --- HELPERS ---
def chunks(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]

# --- API ---
url = f"{JIRA_URL}/rest/api/3/field/{FIELD_ID}/context/{CONTEXT_ID}/option"
auth = (EMAIL, API_TOKEN)
headers = {
    "Accept": "application/json",
    "Content-Type": "application/json"
}

# --- EXECUTION ---
total = len(values)
print(f"Total values: {total}")

for i, batch in enumerate(chunks(values, BATCH_SIZE), 1):
    payload = {
        "options": [{"value": v.strip()} for v in batch]
    }

    print(f"[Batch {i}] Sending {len(batch)} values")

    if DRY_RUN:
        print(payload)
        continue

    response = requests.post(url, headers=headers, auth=auth, json=payload)

    if response.status_code in (200, 201):
        print(f"[Batch {i}] ✅ Success")
    else:
        print(f"[Batch {i}] ❌ Failed: {response.status_code}")
        print(response.text)
        break
