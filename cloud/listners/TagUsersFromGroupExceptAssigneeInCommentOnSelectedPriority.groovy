final GROUP_NAMES = ["jira-project-leads"]

def issueKey = issue.key
def assignee = issue.fields.assignee

def priorityChange = changelog?.items.find { it['field'] == 'priority' }

if (!priorityChange) {
    logger.info("Priority was not updated")
    return
}
logger.info("Priority changed from {} to {}", priorityChange.fromString, priorityChange.toString)

if (priorityChange.toString == "Highest") {
    def userListFromGroup = []
    GROUP_NAMES.each { groupName ->
        def groupMemberResp = get("/rest/api/3/group/member")
            .queryString('groupname', "${groupName}")
            .header("Content-Type", "application/json")
            .asObject(Map)

        assert groupMemberResp.status == 200
        userListFromGroup.addAll(groupMemberResp.body.values)
    }

    def tags = userListFromGroup.findAll { user -> user.accountId != assignee.accountId }.collect { user ->
        [
            "type": "mention",
            "attrs": [
                "id": user.accountId,
                "text": "@" + user.displayName,
                "accessLevel": ""
            ]
        ]
    }

    def body = [ "body": [
        "version": 1,
        "type": "doc",
        "content": [
            [
                "type": "paragraph",
                "content": tags + [
                    [
                        "type": "text",
                        "text": " This issue requires your attentions."
                    ]
                ]
            ]
        ]
    ]]

    def postCommentResp = post("/rest/api/3/issue/${issueKey}/comment")
        .header('Content-Type', 'application/json')
        .body(body)
        .asObject(Map)

    assert postCommentResp.status == 201
}
