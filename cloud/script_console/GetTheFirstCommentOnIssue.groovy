// Get an issue by its key
def issue = Issues.getByKey("PROJECT-123")

// Get the first (oldest) comment
def firstComment = issue.getFirstComment()

if (firstComment) {
    logger.info("First comment by ${firstComment.author.displayName}: ${firstComment.body}")
} else {
    logger.info("No comments found on this issue")
}
