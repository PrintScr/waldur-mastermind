import pkg_resources
import json
from datetime import datetime
import mock

from django.core import mail

from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITransactionTestCase

from waldur_core.structure.images import dummy_image
from waldur_mastermind.support import models
from waldur_mastermind.support.tests import factories


class TestJiraWebHooks(APITransactionTestCase):
    JIRA_ISSUE_UPDATE_REQUEST_FILE_NAME = "jira_issue_updated_query.json"

    def setUp(self):
        self.url = reverse('web-hook-receiver')
        self.CREATED = 'jira:issue_created'
        self.UPDATED = 'jira:issue_updated'
        self.DELETED = 'jira:issue_deleted'
        jira_backend = 'waldur_mastermind.support.backend.atlassian:JiraBackend'
        settings.WALDUR_SUPPORT['ENABLED'] = True
        settings.WALDUR_SUPPORT['ACTIVE_BACKEND'] = jira_backend
        jira_request = pkg_resources.resource_stream(__name__, self.JIRA_ISSUE_UPDATE_REQUEST_FILE_NAME).read().decode()
        self.request_data = json.loads(jira_request)
        settings.CELERY_ALWAYS_EAGER = True

    def tearDown(self):
        settings.CELERY_ALWAYS_EAGER = False

    def set_issue_and_support_user(self):
        backend_id = 'SNT-101'
        issue = factories.IssueFactory(backend_id=backend_id)
        support_user = factories.SupportUserFactory(backend_id='support')
        return backend_id, issue, support_user

    def test_issue_update_callback_does_not_update_issue_link(self):
        backend_id = 'WAL-101'
        permalink = 'https://example.atlassian.net/browse/WAL-101'
        issue = factories.IssueFactory(backend_id=backend_id, link=permalink)
        self.request_data['issue']['key'] = backend_id

        # act
        response = self.client.post(self.url, self.request_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        issue.refresh_from_db()
        self.assertEqual(issue.link, permalink)

    def test_issue_update_callback_updates_issue_summary(self):
        # arrange
        expected_summary = 'Happy New Year'
        backend_id, issue, _ = self.set_issue_and_support_user()
        self.assertNotEquals(issue.summary, expected_summary)

        self.request_data['issue']['key'] = backend_id
        self.request_data['issue']['fields']['reporter']['key'] = issue.reporter.backend_id
        self.request_data['issue']['fields']['summary'] = expected_summary

        # act
        response = self.client.post(self.url, self.request_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        issue.refresh_from_db()
        self.assertEqual(issue.summary, expected_summary)

    def test_issue_update_callback_updates_issue_assignee(self):
        # arrange
        backend_id, issue, assignee = self.set_issue_and_support_user()

        self.request_data['issue']['key'] = backend_id
        self.request_data['issue']['fields']['reporter']['key'] = issue.reporter.backend_id
        self.request_data['issue']['fields']['assignee'] = {
            "key": assignee.backend_id
        }

        # act
        response = self.client.post(self.url, self.request_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        issue.refresh_from_db()
        self.assertEqual(issue.assignee.id, assignee.id)

    def test_issue_update_callback_updates_issue_reporter(self):
        # arrange
        backend_id, issue, _ = self.set_issue_and_support_user()
        reporter = factories.SupportUserFactory(backend_id='Tiffany')

        self.request_data['issue']['key'] = backend_id
        self.request_data['issue']['fields']['reporter']['key'] = issue.reporter.backend_id
        self.request_data['issue']['fields']['reporter'] = {
            "key": reporter.backend_id
        }

        # act
        response = self.client.post(self.url, self.request_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        issue.refresh_from_db()
        self.assertEqual(issue.reporter.id, reporter.id)

    def test_issue_update_callback_creates_a_comment(self):
        # arrange
        backend_id, issue, _ = self.set_issue_and_support_user()
        factories.SupportUserFactory(backend_id=backend_id)
        self.assertEqual(issue.comments.count(), 0)

        self.request_data['issue']['key'] = backend_id
        self.request_data['issue']['fields']['reporter']['key'] = issue.reporter.backend_id
        expected_comments_count = self.request_data['issue']['fields']['comment']['total']

        # act
        response = self.client.post(self.url, self.request_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        issue.refresh_from_db()
        self.assertEqual(issue.comments.count(), expected_comments_count)

    def test_issue_update_callback_updates_a_comment(self):
        # arrange
        backend_id, issue, _ = self.set_issue_and_support_user()
        expected_comment_body = 'Merry Christmas'
        comment = factories.CommentFactory(issue=issue)

        self.request_data['issue']['key'] = issue.backend_id
        self.request_data['issue']['fields']['reporter']['key'] = issue.reporter.backend_id
        self.request_data['issue']['fields']['comment']['comments'][0]['id'] = comment.backend_id
        self.request_data['issue']['fields']['comment']['comments'][0]['body'] = expected_comment_body

        # act
        response = self.client.post(self.url, self.request_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        issue.refresh_from_db()
        issue_comment = issue.comments.first()
        self.assertIsNotNone(issue_comment)
        self.assertEqual(issue_comment.description, expected_comment_body)

    def test_webhook_cleans_up_user_info_and_does_not_update_comment_if_it_is_not_changed(self):
        # arrange
        backend_id, issue, _ = self.set_issue_and_support_user()
        comment = factories.CommentFactory(issue=issue)
        expected_comment_body = comment.description
        jira_comment_body = '[Luke Skywalker 19BBY-TA-T16]: %s' % expected_comment_body

        self.request_data['issue']['key'] = issue.backend_id
        self.request_data['issue']['fields']['reporter']['key'] = issue.reporter.backend_id
        self.request_data['issue']['fields']['comment']['comments'][0]['id'] = comment.backend_id
        self.request_data['issue']['fields']['comment']['comments'][0]['body'] = jira_comment_body

        # act
        response = self.client.post(self.url, self.request_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        issue.refresh_from_db()
        issue_comment = issue.comments.first()
        self.assertIsNotNone(issue_comment)
        self.assertEqual(issue_comment.description, expected_comment_body)

    def test_issue_update_callback_creates_deletes_two_comments(self):
        # arrange
        backend_id, issue, _ = self.set_issue_and_support_user()
        initial_number_of_comments = 2
        factories.CommentFactory.create_batch(initial_number_of_comments, issue=issue)
        self.assertEqual(issue.comments.count(), initial_number_of_comments)

        self.request_data['issue']['key'] = issue.backend_id
        self.request_data['issue']['fields']['reporter']['key'] = issue.reporter.backend_id
        expected_comments_count = self.request_data['issue']['fields']['comment']['total']

        # act
        response = self.client.post(self.url, self.request_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        issue.refresh_from_db()
        self.assertEqual(issue.comments.count(), expected_comments_count)

    def test_issue_update_callback_populates_impact_field(self):
        # arrange
        impact_field = settings.WALDUR_SUPPORT['ISSUE']['impact_field']
        impact_field_value = 'Custom Value'
        backend_id, issue, _ = self.set_issue_and_support_user()

        self.request_data['issue']['key'] = issue.backend_id
        self.request_data['issue']['fields']['reporter']['key'] = issue.reporter.backend_id
        self.request_data['issue']['fields'][impact_field] = impact_field_value

        # act
        response = self.client.post(self.url, self.request_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        issue.refresh_from_db()
        self.assertEqual(issue.impact, impact_field_value)

    def test_issue_update_callback_does_not_create_issue(self):
        # arrange
        backend_id = 'SNT-102'
        reporter = factories.SupportUserFactory(backend_id=backend_id)
        self.assertEqual(models.Issue.objects.count(), 0)

        self.request_data['webhookEvent'] = self.CREATED
        self.request_data['issue']['key'] = backend_id
        self.request_data['issue']['fields']['reporter']['key'] = reporter.backend_id

        # act
        response = self.client.post(self.url, self.request_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(models.Issue.objects.count(), 0)

    def test_issue_update_callback_updates_issue_caller(self):
        # arrange
        expected_summary = 'Happy New Year'
        backend_id, issue, support_user = self.set_issue_and_support_user()

        self.request_data['issue']['key'] = backend_id
        self.request_data['issue']['fields']['reporter']['key'] = support_user.backend_id
        self.request_data['issue']['fields']['summary'] = expected_summary

        # act
        response = self.client.post(self.url, self.request_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        issue.refresh_from_db()
        self.assertEqual(issue.caller.id, support_user.user.id)

    def test_issue_update_callback_updates_first_response_sla(self):
        # arrange
        backend_id, issue, support_user = self.set_issue_and_support_user()

        self.request_data['issue']['key'] = backend_id
        self.request_data['issue']['fields']['reporter']['key'] = support_user.backend_id
        epoch_millis = self.request_data['issue']['fields']['customfield_10006']['ongoingCycle']['breachTime']['epochMillis']
        expected_first_response_sla = datetime.fromtimestamp(epoch_millis / 1000.0)

        # act
        response = self.client.post(self.url, self.request_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        issue.refresh_from_db()
        naive_issue_time = issue.first_response_sla.replace(tzinfo=None)
        self.assertEqual(naive_issue_time, expected_first_response_sla)

    def test_web_hook_raises_400_error_if_request_is_invalid(self):
        response = self.client.post(self.url, {})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_resolution_is_populated_from_jira_request(self):
        # arrange
        backend_id, issue, _ = self.set_issue_and_support_user()
        expected_resolution = 'Done'
        self.request_data['issue']['fields']['resolution']['name'] = expected_resolution
        self.request_data['issue']['key'] = backend_id

        # act
        response = self.client.post(self.url, self.request_data)

        self.assertEquals(response.status_code, status.HTTP_200_OK)
        issue.refresh_from_db()
        self.assertEqual(expected_resolution, issue.resolution)

    def test_resolution_is_empty_if_it_is_none(self):
        # arrange
        backend_id, issue, _ = self.set_issue_and_support_user()
        self.request_data['issue']['fields']['resolution'] = None
        self.request_data['issue']['key'] = backend_id

        # act
        response = self.client.post(self.url, self.request_data)

        self.assertEquals(response.status_code, status.HTTP_200_OK)
        issue.refresh_from_db()
        self.assertEqual('', issue.resolution)

    def test_status_is_populated_from_jira_request(self):
        # arrange
        backend_id, issue, _ = self.set_issue_and_support_user()
        self.request_data['issue']['key'] = backend_id
        expected_status = 'To Do'
        self.request_data['issue']['fields']['status']['name'] = expected_status

        # act
        response = self.client.post(self.url, self.request_data)

        self.assertEquals(response.status_code, status.HTTP_200_OK)
        issue.refresh_from_db()
        self.assertEqual(expected_status, issue.status)

    def test_web_hook_does_not_trigger_issue_update_email_if_the_issue_was_not_updated(self):
        fields = self.request_data['issue']['fields']
        reporter = factories.SupportUserFactory(backend_id=fields['reporter']['key'])
        assignee = factories.SupportUserFactory(backend_id=fields['assignee']['key'])
        first_response_sla = fields['customfield_10006']['ongoingCycle']['breachTime']['epochMillis'] / 1000.0

        factories.IssueFactory(
            resolution=fields['resolution']['name'],
            backend_id=self.request_data['issue']['key'],
            status=fields['status']['name'],
            link=self.request_data['issue']['self'],
            summary=fields['summary'],
            priority=fields['priority']['name'],
            description=fields['description'],
            type=fields['issuetype']['name'],
            assignee=assignee,
            reporter=reporter,
            caller=reporter.user,
            first_response_sla=datetime.fromtimestamp(first_response_sla),
        )

        response = self.client.post(self.url, self.request_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        new_comment_added_subject = render_to_string('support/notification_comment_added_subject.txt').strip()
        self.assertEqual(mail.outbox[0].subject, new_comment_added_subject)

    @mock.patch('waldur_mastermind.support.backend.atlassian.JIRA')
    def test_add_attachment(self, mock_jira):
        backend_attachment_id = 'backend_attachment_id'
        mock_jira().issue.return_value = mock.Mock(raw=self.request_data['issue'])
        backend_id, issue, support_user = self.set_issue_and_support_user()
        self.request_data['issue']['key'] = backend_id
        self.request_data['issue']['fields']['attachment'][0]['id'] = backend_attachment_id
        self.client.post(self.url, self.request_data)
        self.assertTrue(models.Attachment.objects.filter(backend_id=backend_attachment_id).exists())

    @mock.patch('waldur_mastermind.support.backend.atlassian.JIRA')
    def test_if_file_is_received_twice_create_it_once(self, mock_request):
        backend_attachment_id = 'backend_attachment_id'
        backend_id, issue, support_user = self.set_issue_and_support_user()
        self.request_data['issue']['key'] = backend_id
        self.request_data['issue']['fields']['attachment'][0]['id'] = backend_attachment_id
        self.client.post(self.url, self.request_data)
        self.client.post(self.url, self.request_data)
        self.assertEqual(models.Attachment.objects.filter(backend_id=backend_attachment_id).count(), 1)

    def test_delete_attachment(self):
        backend_id, issue, support_user = self.set_issue_and_support_user()
        self.request_data['issue']['key'] = backend_id
        attachment = factories.AttachmentFactory(issue=issue, backend_id='old_id')
        self.client.post(self.url, self.request_data)
        self.assertFalse(models.Attachment.objects.filter(pk=attachment.id).exists())
