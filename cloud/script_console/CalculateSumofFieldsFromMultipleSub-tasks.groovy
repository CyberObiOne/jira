// sum up the values of this custom field
final customFieldName = 'Amount Paid'
final parentIssueKey = 'Parent Issue Key'
final customFieldId = getFieldIdByName(customFieldName)
def subTasksKey = getSubTasksKeyByIssue(parentIssueKey) as List
// if the issue doesn't have any sub-tasks or is a subtask itself then no need for action
if (subTasksKey.empty) {
    return
}
def firstSubTask = getIssueByKey(subTasksKey.first() as String) as Map
if (!existFieldInIssue(firstSubTask.key as String, customFieldId)) {
    def fields = firstSubTask.fields as Map
    def issueType = fields.issuetype as Map
    def project = fields.project as Map
    logger.info "Custom field with name $customFieldName is not configured for issue type ${issueType.name} and project ${project.key}"
    return
}
def sum = subTasksKey.sum { subTaskKey ->
    def subtask = getIssueByKey(subTaskKey as String) as Map
    subtask.fields[customFieldId]
}
def result = put("rest/api/2/issue/$parentIssueKey")
        .header('Content-Type', 'application/json')
        .body([
                fields: [(customFieldId): sum]])
        .asString()
assert result.status == 204
String getFieldIdByName(String fieldName) {
    def customFieldObject = get('/rest/api/2/field')
            .asObject(List)
            .body
            .find { (it as Map).name == fieldName }
    (customFieldObject as Map).id
}
List getSubTasksKeyByIssue(String parentIssueKey) {
    def parentIssue = getIssueByKey(parentIssueKey) as Map
    def fields = parentIssue.fields as Map
    def subtasks = fields.subtasks as List<Map>
    subtasks*.id
}
Map getIssueByKey(String issueKey) {
    def result = get("rest/api/2/issue/$issueKey")
            .header('Content-Type', 'application/json')
            .asObject(Map)
    assert result.status == 200: result.body
    result.body
}
Boolean existFieldInIssue(String issueKey, String fieldId) {
    def issue = getIssueByKey(issueKey)
    def issueFields = issue.fields as Map
    issueFields.containsKey(fieldId)
}
