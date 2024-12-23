// The issue key
final issueKey = 'TEST-1'
// Fetch the issue object from the key
def fields = get("/rest/agile/1.0/issue/${issueKey}")
    .header('Content-Type', 'application/json')
    .asObject(Map)
    .body
    .fields as Map
// Get sprint field from the issue fields as a Map
def sprint = fields.sprint as Map
// Get the Custom field to get the option value from
def sprintName = sprint.name // Note change .name to .id to get the ID of the sprint.
"The name of the current Sprint is '${sprintName}'"
