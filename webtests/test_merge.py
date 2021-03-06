# -*- coding: utf-8 -*-
from django.utils.unittest import skipIf
from django.conf import settings
from django.db import models
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User, Group, Permission
from django.core.urlresolvers import reverse
from django.test import TransactionTestCase
from django_dynamic_fixture import G
from django_webtest import WebTest, WebTestMixin
from adminactions.api import merge, ALL_FIELDS

from .common import BaseTestCaseMixin
from .utils import CheckSignalsMixin, SelectRowsMixin
from webtests.utils import user_grant_permission


def assert_profile(user):
    p = None
    try:
        get_profile(user)
    except ObjectDoesNotExist:
        app_label, model_name = settings.AUTH_PROFILE_MODULE.split('.')
        model = models.get_model(app_label, model_name)
        p, __ = model.objects.get_or_create(user=user)

    return p


def get_profile(user):
    app_label, model_name = settings.AUTH_PROFILE_MODULE.split('.')
    model = models.get_model(app_label, model_name)
    return model.objects.get(user=user)


class MergeTestApi(BaseTestCaseMixin, TransactionTestCase):
    urls = "adminactions.tests.urls"

    def setUp(self):
        super(MergeTestApi, self).setUp()
        self.master_pk = 2
        self.other_pk = 3

    def tearDown(self):
        super(MergeTestApi, self).tearDown()

    def test_merge_success_no_commit(self):
        master = User.objects.get(pk=self.master_pk)
        other = User.objects.get(pk=self.other_pk)
        result = merge(master, other)

        self.assertTrue(User.objects.filter(pk=master.pk).exists())
        self.assertTrue(User.objects.filter(pk=other.pk).exists())

        self.assertEqual(result.pk, master.pk)
        self.assertEqual(result.first_name, other.first_name)
        self.assertEqual(result.last_name, other.last_name)
        self.assertEqual(result.password, other.password)

    def test_merge_success_fields_no_commit(self):
        master = User.objects.get(pk=self.master_pk)
        other = User.objects.get(pk=self.other_pk)
        result = merge(master, other, ['password', 'last_login'])

        master = User.objects.get(pk=master.pk)

        self.assertTrue(User.objects.filter(pk=master.pk).exists())
        self.assertTrue(User.objects.filter(pk=other.pk).exists())

        self.assertNotEqual(result.last_login, master.last_login)
        self.assertEqual(result.last_login, other.last_login)
        self.assertEqual(result.password, other.password)

        self.assertNotEqual(result.last_name, other.last_name)

    def test_merge_success_commit(self):
        master = User.objects.get(pk=self.master_pk)
        other = User.objects.get(pk=self.other_pk)
        result = merge(master, other, commit=True)

        master = User.objects.get(pk=result.pk)  # reload
        self.assertTrue(User.objects.filter(pk=master.pk).exists())
        self.assertFalse(User.objects.filter(pk=other.pk).exists())

        self.assertEqual(result.pk, master.pk)
        self.assertEqual(master.first_name, other.first_name)
        self.assertEqual(master.last_name, other.last_name)
        self.assertEqual(master.password, other.password)

    def test_merge_success_m2m(self):
        master = User.objects.get(pk=self.master_pk)
        other = User.objects.get(pk=self.other_pk)
        group = Group.objects.get_or_create(name='G1')[0]
        other.groups.add(group)
        other.save()

        result = merge(master, other, commit=True, m2m=['groups'])
        master = User.objects.get(pk=result.pk)  # reload
        self.assertSequenceEqual(master.groups.all(), [group])

    def test_merge_success_m2m_all(self):
        master = User.objects.get(pk=self.master_pk)
        other = User.objects.get(pk=self.other_pk)
        group = Group.objects.get_or_create(name='G1')[0]
        perm = Permission.objects.all()[0]
        other.groups.add(group)
        other.user_permissions.add(perm)
        other.save()

        merge(master, other, commit=True, m2m=ALL_FIELDS)
        self.assertSequenceEqual(master.groups.all(), [group])
        self.assertSequenceEqual(master.user_permissions.all(), [perm])

    def test_merge_success_related_all(self):
        master = User.objects.get(pk=self.master_pk)
        other = User.objects.get(pk=self.other_pk)
        entry = other.logentry_set.get_or_create(object_repr='test', action_flag=1)[0]

        result = merge(master, other, commit=True, related=ALL_FIELDS)

        master = User.objects.get(pk=result.pk)  # reload
        self.assertSequenceEqual(master.logentry_set.all(), [entry])
        self.assertTrue(LogEntry.objects.filter(pk=entry.pk).exists())

    @skipIf(not hasattr(settings, 'AUTH_PROFILE_MODULE'), "")
    def test_merge_one_to_one_field(self):
        master = User.objects.get(pk=self.master_pk)
        other = User.objects.get(pk=self.other_pk)
        profile = assert_profile(other)
        if profile:
            entry = other.logentry_set.get_or_create(object_repr='test', action_flag=1)[0]

            result = merge(master, other, commit=True, related=ALL_FIELDS)

            master = User.objects.get(pk=result.pk)  # reload
            self.assertSequenceEqual(master.logentry_set.all(), [entry])
            self.assertTrue(LogEntry.objects.filter(pk=entry.pk).exists())
            self.assertEqual(get_profile(result), profile)
            # self.assertEqual(master.get_profile(), profile)

    def test_merge_ignore_related(self):
        master = User.objects.get(pk=self.master_pk)
        other = User.objects.get(pk=self.other_pk)
        entry = other.logentry_set.get_or_create(object_repr='test', action_flag=1)[0]
        result = merge(master, other, commit=True, related=None)

        master = User.objects.get(pk=result.pk)  # reload
        self.assertSequenceEqual(master.logentry_set.all(), [])
        self.assertFalse(User.objects.filter(pk=other.pk).exists())
        self.assertFalse(LogEntry.objects.filter(pk=entry.pk).exists())

#
class TestMerge(SelectRowsMixin, WebTestMixin, TransactionTestCase):
    fixtures = ['adminactions.json', 'demoproject.json']
    urls = 'demoproject.urls'
    sender_model = User
    action_name = 'merge'
    _selected_rows = [1, 2]

    def setUp(self):
        super(TestMerge, self).setUp()
        self.url = reverse('admin:auth_user_changelist')
        self.user = G(User, username='user', is_staff=True, is_active=True)

    def _run_action(self, steps=3, page_start=None):
        with user_grant_permission(self.user, ['auth.change_user', 'auth.adminactions_merge_user']):
            if isinstance(steps, int):
                steps = range(1, steps + 1)
                res = self.app.get('/', user='user')
                res = res.click('Users')
            else:
                res = page_start
            if 1 in steps:
                form = res.forms['changelist-form']
                form['action'] = 'merge'
                self._select_rows(form)
                res = form.submit()
            if 2 in steps:
                res.form['username'] = res.form['form-1-username'].value
                res.form['email'] = res.form['form-1-email'].value
                res.form['last_login'] = res.form['form-1-last_login'].value
                res.form['date_joined'] = res.form['form-1-date_joined'].value
                res = res.form.submit('preview')
            if 3 in steps:
                res = res.form.submit('apply')
            return res

    def test_no_permission(self):
        with user_grant_permission(self.user, ['auth.change_user']):
            res = self.app.get('/', user='user')
            res = res.click('Users')
            form = res.forms['changelist-form']
            form['action'] = 'merge'
            self._select_rows(form)
            res = form.submit().follow()
            assert 'Sorry you do not have rights to execute this action' in res.body

    def test_success(self):
        res = self._run_action(1)
        preserved = User.objects.get(pk=self._selected_values[0])
        removed = User.objects.get(pk=self._selected_values[1])

        assert preserved.email != removed.email  # sanity check

        res = self._run_action([2, 3], res)

        self.assertFalse(User.objects.filter(pk=removed.pk).exists())
        self.assertTrue(User.objects.filter(pk=preserved.pk).exists())

        preserved_after = User.objects.get(pk=self._selected_values[0])
        self.assertEqual(preserved_after.email, removed.email)
        self.assertFalse(LogEntry.objects.filter(pk=removed.pk).exists())

    def test_error_if_too_many_records(self):
        with user_grant_permission(self.user, ['auth.change_user', 'auth.adminactions_merge_user']):
            res = self.app.get('/', user='user')
            res = res.click('Users')
            form = res.forms['changelist-form']
            form['action'] = 'merge'
            self._select_rows(form, [1, 2, 3])
            res = form.submit().follow()
            self.assertContains(res, 'Please select exactly 2 records')

    def test_swap(self):
        with user_grant_permission(self.user, ['auth.change_user', 'auth.adminactions_merge_user']):
            #removed = User.objects.get(pk=self._selected_rows[0])
            #preserved = User.objects.get(pk=self._selected_rows[1])

            res = self.app.get('/', user='user')
            res = res.click('Users')
            form = res.forms['changelist-form']
            form['action'] = 'merge'
            self._select_rows(form, [1, 2])
            res = form.submit()
            removed = User.objects.get(pk=self._selected_values[0])
            preserved = User.objects.get(pk=self._selected_values[1])

            # steps = 2:
            res.form['master_pk'] = self._selected_values[1]
            res.form['other_pk'] = self._selected_values[0]

            res.form['username'] = res.form['form-0-username'].value
            res.form['email'] = res.form['form-0-email'].value
            res.form['last_login'] = res.form['form-1-last_login'].value
            res.form['date_joined'] = res.form['form-1-date_joined'].value
            res = res.form.submit('preview')
            # steps = 3:
            res = res.form.submit('apply')

            preserved_after = User.objects.get(pk=self._selected_values[1])
            self.assertFalse(User.objects.filter(pk=removed.pk).exists())
            self.assertTrue(User.objects.filter(pk=preserved.pk).exists())

            self.assertEqual(preserved_after.email, removed.email)
            self.assertFalse(LogEntry.objects.filter(pk=removed.pk).exists())
