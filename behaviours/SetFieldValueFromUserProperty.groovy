def assigneeField = getFieldByName('Assignee').value

if (assigneeField && assigneeField != '-1') {
    def assigneeEmail = Users.getByName(assigneeField as String).emailAddress
    getFieldByName('Contact for more information').setFormValue(assigneeEmail)
}
