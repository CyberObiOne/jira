def projKey = '<YOUR_PROJ_KEY>'
def components = ['User Interface (UI)', 'Database', 'API', 'Security', 'Analytics', 'Messaging', 'Infrastructure', 'Company Website / Blog', 'YouTube Videos', 'Web Advertising', 'Partner Websites', 'Networking', 'Systems', 'Software', 'Hardware']

components.each { component ->
    Components.create(projKey,component)
}
