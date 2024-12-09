// Construct the JQL to return the issues to be archived
def jqlQuery = "updated >= '-30d'"

// Search for the issues we want to archive
def searchReq = get("/rest/api/2/search")
        .queryString("jql", jqlQuery)
        .queryString("maxResults", 500)
        .queryString("fields", "key")
        .asObject(Map)

// Verify the search completed successfully
assert searchReq.status >= 200 && searchReq.status < 300

// Save the search results as a Map
Map searchResult = searchReq.body

// Array to collect the issue keys which will be archived
def archivedIssues = []

// Loop over each issue
searchResult.issues.each { Map issue ->
    // Add issue key to archive list
    archivedIssues.push(issue.key)

    // Archive the current issue
    def archiveIssue = put("/rest/api/3/issue/archive")
            .header("Content-Type", "application/json")
            .body(
                    issueIdsOrKeys: [issue.key]
            )
            .asObject(Map)

    // Validate the issue was archived correctly
    assert archiveIssue.status >= 200 && archiveIssue.status < 300
}

logger.info("The following issues were archived succesfully: ${archivedIssues}")
