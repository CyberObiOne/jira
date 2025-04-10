// the issue key to update
def issueKey = "SR-1"

Issues.getByKey(issueKey).update {
    // set custom fields with options (select lists, checkboxes, radio buttons)
    setCustomFieldValue('SelectListA', 'BBB')
    setCustomFieldValue('MultiSelectA', 'BBB', 'CCC')
    setCustomFieldValue('RadioButtons', 'Yes')
    setCustomFieldValue('Checkboxes', 'Maybe', 'Yes')

    // cascading select
    setCustomFieldValue('CascadingSelect', 'BBB', 'B2')

    // set text fields
    setCustomFieldValue('TextFieldA', 'New Value')

    // set user fields
    setCustomFieldValue('UserPicker', 'bob')
    setCustomFieldValue('MultiUserPickerA', 'bob', 'alice')

    setCustomFieldValue('GroupPicker', 'jira-users')
    setCustomFieldValue('MultiGroupPicker', 'jira-users', 'jira-administrators')

    // set date, and date-time custom fields
    setCustomFieldValue('First DateTime', '04/Feb/12 8:47 PM')
    // setCustomFieldValue('Date', '04/Feb/12')

    // a "project picker" custom field - provide a project key
    setCustomFieldValue('ProjectPicker', 'SSPA')

    // set custom field of type version
    setCustomFieldValue('SingleVersionPicker', 'Version1')
}
