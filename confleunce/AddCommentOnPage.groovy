def pageId = '<YOUR_PAGE_ID>'
def comment = 'This is an automated comment by ScriptRunner!'
def rest = '/api/v2/footer-comments/'

post("/wiki${rest}")
    .header('Content-Type', 'application/json')
    .body([
        "pageId": pageId,
        "body": [
            "representation": "storage",
            "value": comment
        ]
    ])
    .asObject(Map)
    .body

logger.info ("Comment added.")
