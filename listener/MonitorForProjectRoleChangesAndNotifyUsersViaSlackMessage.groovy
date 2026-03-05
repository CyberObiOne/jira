import com.atlassian.crowd.embedded.api.Group
import com.atlassian.jira.security.roles.ProjectRoleActor
import com.atlassian.jira.security.roles.actor.GroupRoleActorFactory.GroupRoleActor
import com.onresolve.scriptrunner.parameters.annotation.GroupPicker
import com.onresolve.scriptrunner.parameters.annotation.ShortTextInput
import com.onresolve.scriptrunner.slack.SlackUtil

@ShortTextInput(label = 'Group users threshold', description = 'Enter alert threshold for number of users in a group')
final String userThreshold

@GroupPicker(label = 'Groups', description = 'Select the groups you want to alert on', multiple = true)
final List<Group> groups

@ShortTextInput(label = 'Slack connection', description = 'Enter the name of your slack connection')
final String slackConnectionName

@ShortTextInput(label = 'Slack channel name', description = 'Enter the name of the slack channel to send to')
final String slackChannelName

def newGroupRoleActors = (event.roleActors.roleActors - event.originalRoleActors.roleActors).findAll {
    it.type == ProjectRoleActor.GROUP_ROLE_ACTOR_TYPE
} as List<GroupRoleActor>

newGroupRoleActors.each {
    if ((it.users.size() >= Integer.parseInt(userThreshold)) || (it.group.name in groups*.name)) {
        def slackMessage = """
            Project Role Change Violation!
            Group '${it.group.name}' was added to the '${event.projectRole.name}' role in the '${event.project.name}' project.
        """.stripIndent()

        SlackUtil.message(slackConnectionName, slackChannelName, slackMessage)
    }
}
