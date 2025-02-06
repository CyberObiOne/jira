// Specify Issue Key here
def issueKey = "<IssueKeyHere>"

// Look up the custom field ID for the flagged field
def flaggedCustomField = get("/rest/api/2/field")
        .asObject(List)
        .body
        .find {
    (it as Map).untranslatedName == "Flagged"
} as Map

// Update the issue setting the flagged field
put("/rest/api/2/issue/${issueKey}")
        .header("Content-Type", "application/json")
        .body([
        fields:[
                // More information on flagging an issue can be found in the documentation at:
                // https://confluence.atlassian.com/jirasoftwarecloud/flagging-an-issue-777002748.html
                // The format below specifies the Array format for the flagged field
                (flaggedCustomField.id): [ // Initialise the Array
                                              [ // set the component value
                                                value: "Impediment",
                                              ],

                ]
        ]

])
        .asString()
