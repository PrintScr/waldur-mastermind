from __future__ import unicode_literals

from nodeconductor_assembly_waldur.support import views


def register_in(router):
    router.register(r'support-issues', views.IssueViewSet, base_name='support-issue')
    router.register(r'support-comments', views.CommentViewSet, base_name='support-comment')
    router.register(r'support-users', views.SupportUserViewSet, base_name='support-user')
    router.register(r'support-jira-webhook', views.WebHookReceiverViewSet, base_name='support-jira-webhook')
