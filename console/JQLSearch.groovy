Issues.search('project = SR and reporter = currentUser()').each { issue ->
    // do something with `issue`
}

// if you just need a count use `.count()
Issues.count('project = SR and reporter = currentUser()')
