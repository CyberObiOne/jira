import groovy.json.JsonOutput

def fieldId= "customfield_10083"  // Id of the custom field that needs new options
def contextId = "10193" // Context associated with the field

// List of option values to be added to the custom field
def optionValueList = ["1","2","3"]

// Payload with the options (for the POST request that will add them as field options)
optionValueList.each
        {
            def optionValue = it
            def optionPayload = [
                    options: [
                            [
                                    value: optionValue,
                                    disabled: false // Assuming the option should be enabled
                            ]
                    ]
            ]

// The POST request to add the option
def response = post("/rest/api/3/field/${fieldId}/context/${contextId}/option")
        .header("Content-Type", "application/json")
        .body(JsonOutput.toJson(optionPayload))
        .asString()
        }
