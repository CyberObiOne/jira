import com.atlassian.jira.component.ComponentAccessor

def customFieldName = '<customfield_name>'
def screenName = '<screen_name>'

def customFieldId = ComponentAccessor.customFieldManager.getCustomFieldObjectsByName(customFieldName).find().id

def fieldscreenManager = ComponentAccessor.fieldScreenManager
def allscreen  = fieldscreenManager.fieldScreens
def screen = allscreen.findByName(screenName)
def tab = screen.getTab(0)

//Check if the Field already exists inside the screen if not proceed to add it inside the screen.
if ( tab.isContainsField(customFieldId) ) {
    "Field <b>'$customFieldName'</b> already added inside <b>'$screen.name'</b>"
} else {
    tab.addFieldScreenLayoutItem(customFieldId)
    "Field <b>'$customFieldName'</b> added inside <b>'$screen.name'</b>"
}
