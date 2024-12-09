// Define a JQL query to search for the issues on which you want to set the impediment flag
def query = "<JQLQueryHere>"

// Look up the custom field ID for the flagged field
def flaggedCustomField = get("/rest/api/2/field")
        .asObject(List)
        .body
        .find {
    (it as Map).name == "Flagged"
} as Map

// Search for the issues we want to update
def searchReq = get("/rest/api/2/search")
        .queryString("jql", query)
        .queryString("fields", "Flagged")
        .asObject(Map)

// Verify the search completed successfully
assert searchReq.status == 200

// Save the search results as a Map
Map searchResult = searchReq.body

// Iterate through the search results and set the Impediment flag for each issue returned
searchResult.issues.each { Map issue ->
    def result = put("/rest/api/2/issue/${issue.key}")
            .queryString("overrideScreenSecurity", Boolean.TRUE)
            .header("Content-Type", "application/json")
            .body([
            fields:[
                    // The format below specifies the Array format for the flagged field
                    // More information on flagging an issue can be found in the documentation at:
                    // https://confluence.atlassian.com/jirasoftwarecloud/flagging-an-issue-777002748.html
                    // Initialise the Array
                    (flaggedCustomField.id): [
                                                  [ // set the component value
                                                    value: "Impediment",
                                                  ],

                    ]
            ]
    ])
            .asString()

    // Log out the issues updated or which failed to update
    if (result.status == 204) {
        logger.info("The ${issue.key} issue was flagged as an Impediment.")
    } else {
        logger.warn("Failed to set the Impediment flag on the ${issue.key} issue. ${result.status}: ${result.body}")
    }
}  // end of loop

"Script Completed - Check the Logs tab for information on which issues were updated."
