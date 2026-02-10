final issueKey = issue.key

def result = get('/rest/api/2/user/assignable/search')
    .queryString('issueKey', "${issueKey}")
    .header('Content-Type', 'application/json')
    .asObject(List)

assert result.status == 200

def usersAssignableToIssue = result.body as List<Map>

// A valid user name and an invalid one will try to be added
def userNames = ['valid-user-name', 'not-user-name']

usersAssignableToIssue.forEach { Map user ->
    def displayName = user.displayName as String
    if (displayName in userNames) {
        def accountId = user.accountId
        def watcherResp = post("/rest/api/2/issue/${issueKey}/watchers")
            .header('Content-Type', 'application/json')
            .body("\"${accountId}\"")
            .asObject(List)

        if (watcherResp.status == 204) {
            logger.info("Successfully added ${displayName} as watcher of ${issueKey}")
        } else {
            logger.error("Error adding watcher: ${watcherResp.body}")
        }
    } else {
        logger.error("The ${displayName} user has not been added as a watcher")
    }
}
