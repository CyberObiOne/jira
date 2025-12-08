// NOTE: Make sure SPACE_KEY exists before running this script
final def TAG_NAME = "outdated" // The name of the label, which will be shown in the UI.
final def LAST_MODIFIED = "6m" // All pages modified before this timestamp will have the TAG_NAME on them
final def SPACE_KEY = "TestSpace"
final def CQL_QUERY = "space=${SPACE_KEY} and lastModified < now('-${LAST_MODIFIED}')"
def updatedPages = Pages.search(CQL_QUERY).take(200).toList().each {
    logger.info("Page ${it.title} - ${it.id} was updated ${LAST_MODIFIED} ago. Adding label '${TAG_NAME}' to it")
    it.addLabels("${TAG_NAME}")
    logger.info("Added label")
}
logger.info('Updated pages')
