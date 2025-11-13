import com.atlassian.jira.component.ComponentAccessor
import com.atlassian.jira.project.Project
import com.atlassian.jira.project.UpdateProjectParameters
import com.atlassian.jira.user.ApplicationUser
import com.onresolve.scriptrunner.canned.util.OutputFormatter
import com.onresolve.scriptrunner.parameters.annotation.ProjectPicker
import com.onresolve.scriptrunner.parameters.annotation.UserPicker

@ProjectPicker(label = 'Project Key', description = 'Select project(s) to update', multiple = true, includeArchived = true)
List<Project> projects

@UserPicker(label = 'New project lead', description = 'Enter a project lead to set for this project')
ApplicationUser newProjectLead

def projectManager = ComponentAccessor.projectManager

assert projects: 'Please select at least one project'
assert newProjectLead: 'Please select a project lead to set'

projects.each {
    def params = UpdateProjectParameters.forProject(it.id).leadUserKey(newProjectLead.key)
    projectManager.updateProject(params)
}

OutputFormatter.markupBuilder {
    projects.each { project ->
        p {
            mkp.yield("Updated ${project.key} project lead to ${newProjectLead.name}")
        }
    }
}
