#when: issue created OR issue updated

#if: 'Sprint' is not empty in openSprints() AND 'AKL Assignee' is empty

#then: send email to 'kru064hj','kru05kd7'

#subject	Assign AKL member for issue.key 
#content	issue.key issue.summary
#has no AKL Assignee. Please assign AKL member for this issue.


#Events: 

Issue created, Issue Updated

#Condition and Configuration:

(cfValues['Sprint'].any{it.isActive()}) && cfValues['Assigned QA'] == null && issue.issueType.name != 'Epic' &&  issue.issueType.name != 'Sub-task' && issue.status != 'Done' && issue.status != 'Cancelled'

#Email template:

<a href="$baseUrl/browse/$issue">$issue</a> $issue.summary </br>
has no Assigned QA. Please assign QA for this issue.



