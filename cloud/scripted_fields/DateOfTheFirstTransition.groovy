def result = get('/rest/api/3/issue/' + issue.key + '/changelog')
    .header('Content-Type', 'application/json')
    .asObject(Map)
// Replace 'In Progress' with the status name
def firstTransitionDateTime = result.body.values.find {it['items']['field'].toString().contains('status') && it['items']['toString'].toString().contains('In Progress') }
firstTransitionDateTime ? firstTransitionDateTime['created'] : "-"
