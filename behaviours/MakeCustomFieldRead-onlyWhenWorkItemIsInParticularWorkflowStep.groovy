// Retrieve the current context and issue key
const context = await getContext();
const issueKey = context.extension.issue.key;

// Access the business justification field. Replace this with your own custom field Id.
const businessJustification = getFieldById("customfield_10050");

// Attempt to fetch the current issue data
try {
    const res = await makeRequest(`/rest/api/3/issue/${issueKey}`);
    const currentStatus = res.body.fields.status.name;

    // Determine if the business justification field should be read-only
    const restrictBusinessJustificationEditing = currentStatus === "Approved";

    // Set the field to read-only based on the status
    businessJustification.setReadOnly(restrictBusinessJustificationEditing);
} catch (error) {
    // Log any errors encountered during the request
    logger.error(`Failed to retrieve issue data for ${issueKey}: ${error}`);
}
