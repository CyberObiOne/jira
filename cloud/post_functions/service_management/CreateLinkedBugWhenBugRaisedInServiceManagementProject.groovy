// Specify the key of the project to create the issue in.
def projectKey = "Demo"

// Specify the name of the Bug issue type
def bugIssueType = "Bug"

def issueHapi = Issues.getByKey(issue.key as String)
// Get the value entered on the Service Desk ticket for Summary and Description
def serviceDeskIssueSummary = issueHapi.getSummary()
def serviceDeskIssueDescription =  issueHapi.getDescription()

def createdIssue =  Issues.create(projectKey, bugIssueType) {
    setSummary(serviceDeskIssueSummary)
    setDescription(serviceDeskIssueDescription)
}

// Create the issue link between both issues
issueHapi.link("relates to", createdIssue)
