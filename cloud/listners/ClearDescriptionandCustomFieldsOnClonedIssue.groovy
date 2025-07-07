if (issueLink.issueLinkType.name == "Cloners") {
    logger.info("The issue has been cloned, clearing specified fields on the source issue")
    if (issueLink.sourceIssueId) {
        Issues.getByKey(issueLink.sourceIssueId as String).update {
            setDescription("")
            setCustomFieldValue("<SpecifyTheSingleLineTextFieldNameHere>", "")
            setCustomFieldValue("<SpecifyTheNumberCustomFieldNameHere>", ([null] as Long[]))
            setCustomFieldValue("<SpecifyTheCheckBoxFieldNameHere>", [] as String[])
            setCustomFieldValue("<SpecifyTheSprintFieldNameHere>", ([null] as Long[]))
            setCustomFieldValue("<SpecifyTheSingleSelectListFieldNameHere>", ([null] as Object[]))
            setCustomFieldValue("<SpecifyTheCascadingSelectListFieldNameHere>", ([null] as Object[]))
            setCustomFieldValue("<SpecifyTheDatePickerFieldNameHere>", ([null] as Object[]))
        }
    }
    else {
        logger.warn("Could not find the source issue with ID: ${issueLink.sourceIssueId}")
    }
} else {
    logger.info("The issue has not been cloned so do nothing")
}
