issue.set {
    setComment("""
        The comment, which can include values from the issue, eg the assignee: ${issue.assignee?.displayName ?: 'Unassigned'}

        You can also include *markdown*.
    """)
}
