#Issue update

package jira.listeners

import com.atlassian.jira.component.ComponentAccessor
import com.atlassian.jira.issue.Issue
import com.atlassian.jira.issue.customfields.option.LazyLoadedOption
import com.atlassian.jira.user.ApplicationUser
import com.atlassian.jira.bc.issue.search.SearchService
import com.atlassian.jira.issue.IssueManager
import com.atlassian.jira.issue.util.DefaultIssueChangeHolder
import com.atlassian.jira.issue.ModifiedValue
import com.atlassian.jira.issue.MutableIssue

def issue = event.issue
def linkManager = ComponentAccessor.getIssueLinkManager()
def issueManager = ComponentAccessor.issueManager

def akl_assignee = ComponentAccessor.customFieldManager.getCustomFieldObject(22701L)
def assigned_sm = ComponentAccessor.customFieldManager.getCustomFieldObject(22801L)
def assigned_qa = ComponentAccessor.customFieldManager.getCustomFieldObject(11733L)
def akl_assignee_value = issue.getCustomFieldValue(akl_assignee)
def assigned_sm_value = issue.getCustomFieldValue(assigned_sm)
def assigned_qa_value = issue.getCustomFieldValue(assigned_qa)



if (issue.getIssueType().name == "Epic") {

    def epicCollection = linkManager.getOutwardLinks(issue.id)
    if (epicCollection!=null){

        epicCollection.each {

           def epicIssue = it.getDestinationObject()
		   //log.info (epicIssue)
           akl_assignee.updateValue(null, epicIssue, new ModifiedValue(issue.getCustomFieldValue(akl_assignee), akl_assignee_value), new DefaultIssueChangeHolder())
           assigned_sm.updateValue(null, epicIssue, new ModifiedValue(issue.getCustomFieldValue(assigned_sm), assigned_sm_value), new DefaultIssueChangeHolder())
           assigned_qa.updateValue(null, epicIssue, new ModifiedValue(issue.getCustomFieldValue(assigned_qa), assigned_qa_value), new DefaultIssueChangeHolder())
    	  // issue.setCustomFieldValue(customField, linkedIssueCustomFieldValue)     
            
        }
      

    }
}




