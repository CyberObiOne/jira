// Specify the IssueKey
def issue = Issues.getByKey("DEMO-1");
String[] labels = issue.labels.collect { it.toString() }.toArray(new String[0]);

// Loop linked issue, outward links specifically
def successStatusByIssueKey = issue.getSubTaskObjects().collect { subtaskIssue ->
    subtaskIssue.update {
        setLabels (labels)
    }
    subtaskIssue.key
}

successStatusByIssueKey ? "Labels successfully copied to issues: ${successStatusByIssueKey}. \nPlease see the 'Logs' tab for more information on what issues were updated." :
        "No subtask found. No labels copied."
