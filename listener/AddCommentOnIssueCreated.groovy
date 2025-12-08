def eventIssue = Issues.getByKey(issue.key as String)

def author = eventIssue.getCreator().displayName
eventIssue.addComment("""Thank you ${author} for creating a support request.
We'll respond to your query within 24hrs.
In the meantime, please read our documentation: http://example.com/documentation""")
