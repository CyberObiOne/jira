def comments = issue.comments
if (comments) {
    comments.last().authorApplicationUser?.emailAddress
}
