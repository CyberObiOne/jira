// Set your target parent issue type (e.g., "Task", "Epic")
final String TARGET_ISSUE_TYPE = "Epic"
// Set how far up the hierarchy to check
final int MAX_DEPTH = 10

Map findParentOfType(String currentIssueKey, String targetType, int maxDepth, int depth = 0) {
    if (depth >= maxDepth) {
        return null
    }

    def issueResp = get("/rest/api/3/issue/${currentIssueKey}")
            .queryString('fields', 'parent,issuetype,summary,key')
            .asObject(Map)

    if (issueResp.status != 200) {
        return null
    }

    def currentIssue = issueResp.body as Map
    def fields = currentIssue.fields as Map

    // Only check the current issue's type if we're not on the first call (depth > 0)
    // This ensures we never return the starting issue, only its parents/ancestors
    if (depth > 0) {
        def issueType = fields.issuetype as Map

        if (issueType.name == targetType) {
            return [
                    key    : currentIssue.key,
                    summary: fields.summary
            ]
        }
    }

    // Check for parent
    def parent = fields.parent as Map

    if (!parent) {
        return null
    }

    // Recurse with the parent's key
    return findParentOfType(parent.key as String, targetType, maxDepth, depth + 1)
}

// Use the current issue from the scripted field binding
def parentIssue = findParentOfType(issue.key as String, TARGET_ISSUE_TYPE, MAX_DEPTH)

if (parentIssue) {
    def parentKey = parentIssue.key
    def parentSummary = parentIssue.summary as String

    return [
            version: 1,
            type   : "doc",
            content: [
                    [
                            type   : "paragraph",
                            content: [
                                    [
                                            type : "text",
                                            text : "${parentKey} - ${parentSummary}",
                                            marks: [
                                                    [
                                                            type : "link",
                                                            attrs: [
                                                                    href: "${baseUrl}/browse/${parentKey}"
                                                            ]
                                                    ]
                                            ]
                                    ]
                            ]
                    ]
            ]
    ]
} else {
    return [
            version: 1,
            type   : "doc",
            content: [
                    [
                            type   : "paragraph",
                            content: [
                                    [
                                            type: "text",
                                            text: "No Parent Found"
                                    ]
                            ]
                    ]
            ]
    ]
}
