import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

// Specify the issue key to update
def issueKey = "TEST-1"

// Get a future date to set as the due date
def today = LocalDateTime.now().plusDays(14)

// Create a date time formatter with the date pattern
final dateFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd")

// Update the issue
def result = put("/rest/api/2/issue/${issueKey}")
        .header("Content-Type", "application/json")
        .body([
            fields:[
                    // Set the due date to today's date
                    duedate: today.format(dateFormatter).toString()
            ]
        ]).asObject(Map)

// Validate the issue updated correctly
assert result.status == 204
