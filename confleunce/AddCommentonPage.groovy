def pageId = 123 // 'replace with your page ID'
def comment = 'This is an automated comment by ScriptRunner!'

Pages.getById(pageId).addComment(comment)
