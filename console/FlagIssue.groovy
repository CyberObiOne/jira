// Specify the Issue by its key and perform the update
Issues.getByKey('<IssueKeyHere>').update {
    setCustomFieldValue("Flagged", "Impediment")
}
