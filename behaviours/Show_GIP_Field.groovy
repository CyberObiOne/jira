import com.atlassian.jira.component.ComponentAccessor
import com.atlassian.jira.user.util.UserUtil
   
// Get pointers to the custom field(s) required
def area = getFieldByName("Area")
def gip = getFieldByName("GIP")
def pc = getFieldByName("Payment Cycle")
def py = getFieldByName("Performance Year")
 
// Get the Value of Select List A
def areaVal = area.getValue().toString()
 
// Hide Select Lists by default
area.setRequired(true)
gip.setHidden(true)
pc.setHidden(true)
py.setHidden(true)
 
// If option B is selected in Select List A show Select List B and make it required

if(areaVal == "Growth Incentive Program"){
    gip.setHidden(false) 
    gip.setRequired(true) 
    pc.setHidden(false)
    pc.setRequired(true)
    py.setHidden(false)
    py.setRequired(true)
}
