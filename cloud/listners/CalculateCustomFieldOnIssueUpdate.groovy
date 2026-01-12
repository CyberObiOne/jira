def eventIssue = Issues.getByKey(issue.key as String)

final projectKey = 'TEST'

if (eventIssue.projectObject.key != projectKey) {
    logger.info("Wrong Project ${eventIssue.projectObject.key}")
    return
}

//get the value of each of the custom fields or use 0 as default if a value isn't set yet
def input1 = eventIssue.getCustomFieldValue("Custom Field 1") as Integer ?: 0
def input2 = eventIssue.getCustomFieldValue("Custom Field 2") as Integer ?: 0

def output = input1 + input2

//do not attempt to update the result if it is the same as the existing one.
if(eventIssue.getCustomFieldValue("Output Custom Field") == output) {
    logger.info("The reulst was the same as the existing one, no update needed.")
} else {
    eventIssue.update {
        setCustomFieldValue("Output Custom Field", output)
    }
    logger.info("Output Custom Field updated to ${output}")
}
