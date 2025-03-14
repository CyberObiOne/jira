def currentIssue = Issues.getByKey(issue.key as String)
def issueSubtasks = currentIssue.subtasks

issueSubtasks.forEach { subtask ->
    if (subtask.getStatus().name == "Done") {
        return
    }
    subtask.addComment("""Parent task ${issue.key} is resolved and has status: '${(currentIssue.status as Map).name}'.
        Please change status of this issue.""")
}
