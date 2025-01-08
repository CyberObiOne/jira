import com.atlassian.jira.component.ComponentAccessor

def changeHistoryManager = ComponentAccessor.getChangeHistoryManager()

def changeHistories = changeHistoryManager.getChangeHistories(event.issue)
if (changeHistories) {
    def changeItem = changeHistories.last().getChangeItemBeans().find {
        // check if Source field value changed
        it.field == 'Source' && it.fromString != it.toString
    }

    if (changeItem) {
        event.issue.update {
            setCustomFieldValue('Target', changeItem.created)
        }
    }
}
