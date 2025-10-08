final issueKey = issue.key
def currentUser = Users.getLoggedInUser()

def watcherResp = post("/rest/api/2/issue/${issueKey}/watchers")
    .header('Content-Type', 'application/json')
    .body("\"${currentUser.accountId}\"")
    .asObject(List)

if (watcherResp.status == 204) {
    logger.info("Successfully added ${currentUser.displayName} as watcher of ${issueKey}")
} else {
    logger.error("Error adding watcher: ${watcherResp.body}")
}
