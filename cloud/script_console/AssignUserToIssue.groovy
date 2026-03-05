def assigneeAccountId = 'assignee_account_id'
Issues.getByKey("TVP-1").update{
    setAssignee(assigneeAccountId)
}
