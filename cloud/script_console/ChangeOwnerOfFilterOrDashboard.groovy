// Enter the Id of the filter to update here
def filterId = ''

// Enter the Id of the dashboard to update here
def dashboardId = ''

// Enter the accountId of the new owner here
def newOwnerAccountId = ''

if (filterId.size() == 0 && newOwnerAccountId.size() == 0 || dashboardId.size() == 0 && newOwnerAccountId.size() == 0) {
        return "You must specify a filter id or dashboard id and the accountId for the new user to be able to change a filter or dashboards ownership"
}

if (filterId.size() > 0) {
    def updateFilterOwner = put("/rest/api/3/filter/${filterId}/owner")
            .header('Content-Type', 'application/json')
            .body([
                    accountId: newOwnerAccountId
            ])
            .asString()

    assert updateFilterOwner.status >= 200 && updateFilterOwner.status < 300

    logger.info("The filter with the Id: ${filterId} was updated to be owned by the user with the accountId of ${newOwnerAccountId}.")
}

if (dashboardId.size() > 0) {
    def updateDashboardOwner = put('/rest/api/3/dashboard/bulk/edit')
            .header('Content-Type', 'application/json')
            .body([
                    action            : 'changeOwner',
                    changeOwnerDetails: [
                            autofixName: false,
                            newOwner   : newOwnerAccountId
                    ],
                    entityIds         : [dashboardId.toInteger()],
                    permissionDetails : [
                            sharePermissions: [
                                    [
                                            user: [
                                                    accountId: newOwnerAccountId,
                                            ],
                                            id  : dashboardId.toInteger(),
                                            type: "user"
                                    ]
                            ]
                    ]
            ])
            .asString()

    assert updateDashboardOwner.status >= 200 && updateDashboardOwner.status < 300

    logger.info("The dashboard with the Id: ${dashboardId} was updated to be owned by the user with the accountId of ${newOwnerAccountId}.")
}
