def currentIssue = Issues.getByKey(issue.key as String)
def sprint = currentIssue.getCustomFieldValue("Sprint")?.find { sprint -> sprint.state == 'active' }

if (sprint) {
    String sprintStartDate = sprint.startDate.substring(0, 10)
    String sprintEndDate = sprint.endDate.substring(0, 10)

    return "Sprint starting: ${sprintStartDate} - Sprint ending: ${sprintEndDate}"

} else {
    // Return a default message if the issue is not in active sprint
    return "The ${currentIssue.key} issue is not currently in an active sprint"
}
