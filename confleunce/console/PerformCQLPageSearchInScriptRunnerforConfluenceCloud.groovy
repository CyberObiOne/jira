final def SPACE_KEY = "<YOUR_SPACE_KEY>"
final def CQL_QUERY = "space=${SPACE_KEY}" // you can add your own CQL to search for pages

def pages = Pages.search(CQL_QUERY)
pages.each(page->{
    logger.info "page title is ===> ${page.title}"
})
