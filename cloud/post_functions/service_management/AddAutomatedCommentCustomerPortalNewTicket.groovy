def comment = """
                Hi, ${issue.fields.reporter.displayName} from ${issue.fields.customfield_12818.value} office,

                Thank you for creating this ticket in our service desk. You have requested a laptop replacement delivered to following destination:

                ${issue.fields.customfield_12831}

                Please make sure the address is correct. We will respond to your request shortly.

                Kindly also note if the ticket remains inactive for a period of 10 days then will automatically be closed.
            """

def addComment = post("/rest/servicedeskapi/request/${issue.key}/comment")
        .header('Content-Type', 'application/json')
        .body([
                body: comment,
                // Make comment visible in the customer portal
                public: true,
        ])
        .asObject(Map)

assert addComment.status >= 200 && addComment.status <= 300
