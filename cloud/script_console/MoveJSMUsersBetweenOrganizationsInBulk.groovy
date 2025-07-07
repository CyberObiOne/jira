/********** User Input Start **********/

final USERNAME = BOT_USER_EMAIL           // Authentication details can be saved as Script Variables:
final API_TOKEN = BOT_USER_API_TOKEN      // https://docs.adaptavist.com/sr4jc/latest/features/script-variables
final ORG_ID = YOUR_ORG_ID                // Use empty string, "", if not needed, ie. no internal customers
final ORG_API_KEY = YOUR_ORG_API_KEY      // Use empty string, "", if not needed, ie. no internal customers
final DOMAINS_TO_MOVE = ["@example.com",] // An empty string, "", means move all
final SOURCE_ORG_NAME = "Just Org"
final TARGET_ORG_NAME = "Another Org"

/********** User Input End **********/

def getAllJsmAuth = { String url ->
    def items = []
    def start = 0
    def isLastPage = false
    while (!isLastPage) {
        def authResponse = get(url).queryString('start', start)
            .basicAuth(USERNAME, API_TOKEN)
            .asObject(Map)
        assert authResponse.status >= 200 && authResponse.status <= 300
        items.addAll(authResponse.body["values"])
        isLastPage = authResponse.body['isLastPage']
        start = start + (authResponse.body['limit'] as Integer)
    }
    items
}

// Main logic
def organisations = getAllJsmAuth("rest/servicedeskapi/organization")
def sourceOrg = organisations.find { it['name'] == SOURCE_ORG_NAME }
def targetOrg = organisations.find { it['name'] == TARGET_ORG_NAME }

if (!sourceOrg) {
    logger.info "$SOURCE_ORG_NAME organization Not Found"
    return
}

if (!targetOrg) {
    logger.info "$TARGET_ORG_NAME organization Not Found"
    return
}

def sourceOrgCustomers = getAllJsmAuth("/rest/servicedeskapi/organization/${sourceOrg['id']}/user")
def sourceOrgEmailHiddenCustomers = sourceOrgCustomers.findAll { !it['emailAddress'] }

if (ORG_ID && ORG_API_KEY && sourceOrgEmailHiddenCustomers) {
    // From documentation, following API supports returning 10000 users in one call, no pagination needed:
    // https://developer.atlassian.com/cloud/admin/organization/rest/api-group-users/#api-v1-orgs-orgid-users-search-post
    def getUsersResponse = post("https://api.atlassian.com/admin/v1/orgs/$ORG_ID/users/search")
        .header('Authorization', "Bearer $ORG_API_KEY")
        .header('Content-Type', 'application/json')
        .body([
            accountIds: sourceOrgEmailHiddenCustomers.collect { it['accountId'] },
            expand: ['EMAIL']
        ])
        .asObject(Map)
    assert getUsersResponse.status >= 200 && getUsersResponse.status <= 300
    def users = getUsersResponse.body['data']
    sourceOrgCustomers.each { customer ->
        if (!customer['emailAddress']) customer['emailAddress'] = users.find { it['accountId'] == customer['accountId'] }['email']
    }
}

def domainMatchedSourceOrgCustomers = sourceOrgCustomers.findAll { user -> DOMAINS_TO_MOVE.any { user['emailAddress'].toString().endsWith(it) } }

if (!domainMatchedSourceOrgCustomers) {
    logger.info "No matched customers from $SOURCE_ORG_NAME organization found with domains: $DOMAINS_TO_MOVE"
    return
}

logger.info("Moving following customers from $SOURCE_ORG_NAME to $TARGET_ORG_NAME:")
domainMatchedSourceOrgCustomers.each { logger.info "${it['displayName']}: ${it['accountId']}" }
// Choose to logging out email addresses instead:
// domainMatchedSourceOrgCustomers.each { logger.info "${it['emailAddress']}" }

// There is no limit on how many customers can be added and deleted in one request from API reference, no pagination needed:
// https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-servicedesk/#api-rest-servicedeskapi-servicedesk-servicedeskid-customer-post
// https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-servicedesk/#api-rest-servicedeskapi-servicedesk-servicedeskid-customer-delete
// Add first, delete later, so that the customers will always be in at least one organization
def addCustomersToTargetOrgResponse = post("rest/servicedeskapi/organization/${targetOrg['id']}/user")
    .header("Content-Type", "application/json")
    .body([
        accountIds: domainMatchedSourceOrgCustomers.collect { it['accountId'] }
    ])
    .asObject(Map)
assert addCustomersToTargetOrgResponse.status >= 200 && addCustomersToTargetOrgResponse.status <= 300

def deleteCustomersFromSourceOrgResponse = delete("rest/servicedeskapi/organization/${sourceOrg['id']}/user")
    .header("Content-Type", "application/json")
    .body([
        accountIds: domainMatchedSourceOrgCustomers.collect { it['accountId'] }
    ])
    .asObject(Map)
assert deleteCustomersFromSourceOrgResponse.status >= 200 && deleteCustomersFromSourceOrgResponse.status <= 300
