import com.atlassian.jira.component.ComponentAccessor
import com.onresolve.jira.groovy.user.FieldBehaviours
import groovy.transform.BaseScript

@BaseScript FieldBehaviours fieldBehaviours

final singleSelectListName = 'SingleSelectList'
final multiSelectListName = 'MultiSelectList'

[singleSelectListName, multiSelectListName].each { selectFieldName ->
    // Get the select field
    def selectField = getFieldByName(selectFieldName)

    // Getting select field options
    def selectCustomField = customFieldManager.customFieldObjects.findByName(selectFieldName)
    def selectConfig = selectCustomField.getRelevantConfig(issueContext)
    def selectOptions = ComponentAccessor.optionsManager.getOptions(selectConfig)

    // Filter select available options
    final selectAvailableOptions = selectOptions.findAll { it.value in ['foo', 'bar', 'spam', 'pam'] }
    selectField.setFieldOptions(selectAvailableOptions)

    // Set the default values depending on select type
    if (selectFieldName == singleSelectListName) {
        def defaultValue = selectAvailableOptions.find { it.value == 'spam' }
        selectField.setFormValue(defaultValue.optionId)
    } else if (selectFieldName == multiSelectListName) {
        def defaultValues = selectAvailableOptions.findAll { it.value in ['foo', 'spam'] }
        selectField.setFormValue(defaultValues*.optionId)
    }
}
