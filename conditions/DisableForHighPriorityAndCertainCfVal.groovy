package jira.conditions

import jira.JiraUtilHelper

def insightCfVal = JiraUtilHelper.getCustomFieldValue("Infraatructure", issue) as String
if (!insightCfVal) return
return !(insightCfVal == "[QA]" && issue.priority.name in ["High"])
