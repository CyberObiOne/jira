if (issueLink.issueLinkType.name == "Cloners") {
    logger.info("The issue has been cloned, clearing specified fields on the source issue")
    if (issueLink.sourceIssueId) {
        Issues.getByKey(issueLink.sourceIssueId as String).update {
            clearCustomField("Description")
            clearCustomField("<SpecifyOtherCustomFieldNamesHere>")
        }
    }
    else {
        logger.warn("Could not find the source issue with ID: ${issueLink.sourceIssueId}")
    }
} else {
    logger.info("The issue has not been cloned so do nothing")
}
