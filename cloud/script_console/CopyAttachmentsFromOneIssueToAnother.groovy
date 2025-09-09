import org.apache.http.entity.ContentType

def originIssueKey = "TEST-1"
def destinationIssueKey = "TEST-2"
def sourceIssue = Issues.getByKey(originIssueKey)

// Get all the attachment details in the origin issue
def attachments = sourceIssue.fields.attachment
logger.info("${attachments}")

attachments.each{ attachment ->
    // Get the attachment as binary
    logger.info("${attachment.content}")
    def fileBody = get("${attachment.content}").asBinary().body

    // Add the attachment to the destination issue
    def result = post("/rest/api/3/issue/" + destinationIssueKey + "/attachments")
        .header('X-Atlassian-Token', 'no-check')
        .field("file", fileBody, ContentType.create(attachment.mimeType), attachment.filename)
        .asObject(List)

    if (result.status == 200){
        return result.body
    } else {
        return "Failure in adding attachment => Status: ${result.status} ${result.body}"
    }
}
