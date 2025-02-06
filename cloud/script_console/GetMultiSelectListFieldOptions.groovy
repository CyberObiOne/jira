// The issue key
def issueKey = '<IssueKeyHere>'

// Fetch the issue object from the key
def issue = get("/rest/api/2/issue/${issueKey}")
        .header('Content-Type', 'application/json')
        .asObject(Map)
        .body

// Get all the fields from the issue as a Map
def fields = issue.fields as Map

// Get the Custom field to get the option value from
def customField = get("/rest/api/2/field")
        .asObject(List)
        .body
        .find {
    (it as Map).name == '<CustomFieldNameHere>'
} as Map

// Extract and store the option from the custom field
def values = fields[customField.id] as List<Map>

// Get the values from the multi select list field and return them
values*.value
