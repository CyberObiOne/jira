def accountId = '123456:12345a67-bbb1-12c3-dd45-931ee99f99g0'
def groupName = 'jira-users'
def projectKey = 'Test'
def roleName = 'Developers'

def roles = get("/rest/api/2/project/${projectKey}/role")
        .asObject(Map).body

String developersUrl = roles[roleName]

assert developersUrl != null

def result = post(developersUrl)
    .header('Content-Type', 'application/json')
    .body([
            user: [accountId],
            group: [groupName]
    ])
    .asString()

assert result.status == 200
result.statusText
