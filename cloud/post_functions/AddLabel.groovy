List labels = issue.fields.labels ?: [] // get the labels for the current issue
labels += "newLabel"
issueInput.fields.labels = labels
