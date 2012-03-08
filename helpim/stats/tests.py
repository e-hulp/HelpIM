from datetime import date, datetime

from django.contrib.auth.models import ContentType, Permission, User
from django.core.urlresolvers import resolve, Resolver404, reverse
from django.test import TestCase
from django.test.client import Client
from django.utils.translation import ugettext as _

from helpim.common.models import BranchOffice
from helpim.conversations.models import Chat
from helpim.stats.models import BranchReportVariable, Report, ReportVariable, WeekdayReportVariable


class UrlPatternsTestCase(TestCase):
    '''Test url design of stats app'''
    
    # base url where stats app runs
    base_url = "/admin/stats/"

    def setUp(self):
        super(UrlPatternsTestCase, self).setUp()

        self.c = Client()
        self.user = User.objects.create_user('testuser', 'test@example.com', 'test')
        c, created = ContentType.objects.get_or_create(model='', app_label='stats',
                                                       defaults={'name': 'stats'})
        p, created = Permission.objects.get_or_create(codename='can_view_stats', content_type=c,
                                                      defaults={'name': 'Can view Stats', 'content_type': c})
        self.user.user_permissions.add(p)
        self.assertTrue(self.c.login(username=self.user.username, password='test'), 'Could not login')


    def _assertUrlMapping(self, url, action, params={}, follow=True):
        '''assert that when `url` is accessed, the view `action` is invoked with parameters dictionary `params`'''
        
        response = self.c.get(self.base_url + url, follow=follow)
        self.assertTrue(response.status_code != 404, 'URL not found')

        try:
            info = resolve(response.request["PATH_INFO"])
        except Resolver404:
            self.fail("Could not resolve '%s'" % (response.request["PATH_INFO"]))

        self.assertEqual(info.url_name, action, "view name is '%s', but '%s' was expected" % (info.url_name, action))
        self.assertEqual(len(info.kwargs), len(params), 'Number of parameters does not match: expected: %s -- got: %s' % (params, info.kwargs))

        for key, value in params.items():
            self.assertTrue(key in info.kwargs, 'Expected parameter "%s" not found' % (key))
            self.assertEqual(info.kwargs[key], value, 'Values for parameter "%s" do not match: "%s" != "%s"' % (key, info.kwargs[key], value))


    def testStatsUrlMappings(self):
        '''test url mappings for general stats functionality'''

        self._assertUrlMapping('', 'stats_index')

        self._assertUrlMapping('chat', 'stats_overview', {'keyword': 'chat'})
        self._assertUrlMapping('chat/', 'stats_overview', {'keyword': 'chat'})

        self._assertUrlMapping('chat/1999', 'stats_overview', {'keyword': 'chat', 'year': '1999'})
        self._assertUrlMapping('chat/1999/', 'stats_overview', {'keyword': 'chat', 'year': '1999'})

        self._assertUrlMapping('chat/2011/csv', 'stats_overview', {'keyword': 'chat', 'year': '2011', 'format': 'csv'})
        self._assertUrlMapping('chat/2011/csv/', 'stats_overview', {'keyword': 'chat', 'year': '2011', 'format': 'csv'})

        self.assertRaisesRegexp(AssertionError, 'URL not found',
                                lambda: self._assertUrlMapping('keyworddoesntexist', 'stats_overview'))


    def testReportsUrlMappings(self):
        '''test url mappings for reports functionality'''
        
        # create Report with specific id to be used throughout test
        r = Report(period_start=date(2000,1,1), period_end=date(2000,1,1), variable1='weekday', variable2='branch')
        r.save()
        r.id = 4143
        r.save()

        self._assertUrlMapping('reports/new/', 'report_new')
        self._assertUrlMapping('reports/4143/', 'report_show', {'id': '4143'})
        self._assertUrlMapping('reports/4143/delete/', 'report_delete', {'id': '4143'}, follow=False)


    def testPermission(self):
        # access allowed for privileged user
        response = self.c.get(reverse('stats_index'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'stats/stats_index.html')


        # test access to stats with unprivileged user
        self.c = Client()
        unprivilegedUser = User.objects.create_user('bob', 'me@bob.com', 'bob')
        self.assertTrue(self.c.login(username=unprivilegedUser.username, password='bob'), 'Bob could not login')

        response = self.c.get(reverse('stats_index'))
        self.assertNotEqual(response.status_code, 200)
        self.assertTemplateNotUsed(response, 'stats/stats_index.html')


class ReportTestCase(TestCase):
    fixtures = ['reports-test.json']

    def test_matching_chats(self):
        r = Report.objects.get(pk=1)

        chats = r.matching_chats()
        self.assertItemsEqual(Chat.objects.filter(id__in=[1]), chats)

        # remove lower bound
        r.period_start = None
        chats = r.matching_chats()
        self.assertItemsEqual(Chat.objects.filter(id__in=[1, 2]), chats)

        # remove upper bound
        r.period_end = None
        chats = r.matching_chats()
        self.assertItemsEqual(Chat.objects.all(), chats)

        # set careworker only
        r.careworker = User.objects.get(pk=55)
        chats = r.matching_chats()
        self.assertItemsEqual(Chat.objects.filter(id__in=[2]), chats)

        # set branch office only
        r.careworker = None
        r.branch = BranchOffice.objects.get(pk=1)
        chats = r.matching_chats()
        self.assertItemsEqual(Chat.objects.filter(id__in=[2, 3]), chats)

        # set branch and careworker
        r.careworker = User.objects.get(pk=22)
        chats = r.matching_chats()
        self.assertItemsEqual(Chat.objects.filter(id__in=[3]), chats)

    def test_variable_samples(self):
        r = Report.objects.get(pk=1)
        
        # empty variable -> only contains 'Total' to sum up results in columns
        result = list(r.variable_samples(None)) 
        self.assertEqual(1, len(result))
        self.assertItemsEqual([_('Total')], result)
        
        # normal, successful lookup
        result = list(r.variable_samples('weekday'))
        self.assertEqual(len(WeekdayReportVariable.values()) + 2, len(result))
        self.assertTrue(_('Other') in result)
        self.assertTrue(_('Total') in result)
        
        # failed lookup for variable that does not exist -> all values will go to 'Other'
        result = list(r.variable_samples('doesntexist'))
        self.assertEqual(2, len(result))
        self.assertItemsEqual([_('Other'), _('Total')], result)


class ReportVariableTestCase(TestCase):
    def setUp(self):
        super(ReportVariableTestCase, self).setUp()

        ReportVariable.all_variables()

    def test_register_variable(self):
        # clear state, might have been set by previous tests
        ReportVariable.known_variables = {}

        self.assertEqual(0, len(ReportVariable.known_variables), "No variables should be registered")

        # calling all_variables() triggers auto-discovery and addition of variables
        self.assertTrue(WeekdayReportVariable in ReportVariable.all_variables(), "Weekday variable should be registered")
        self.assertTrue(len(ReportVariable.known_variables) > 0, "No variables should be registered")

    def test_find(self):
        self.assertEqual(WeekdayReportVariable, ReportVariable.find_variable('weekday'))
        self.assertEqual(('weekday', _('Weekday')), ReportVariable.find_variable('weekday').get_choices_tuple())

        self.assertEqual(None, ReportVariable.find_variable('doesntexist'))


class WeekdayReportVariableTestCase(TestCase):
    fixtures = ['reports-test.json']

    def test_values(self):
        self.assertEqual(7, len(WeekdayReportVariable.values()))

    def test_extract(self):
        c1 = Chat.objects.get(pk=1)
        c2 = Chat.objects.get(pk=2)
        c3 = Chat.objects.get(pk=3)

        self.assertEqual(_('Friday'), WeekdayReportVariable.extract_value(c1))
        self.assertEqual(_('Thursday'), WeekdayReportVariable.extract_value(c2))
        self.assertEqual(_('Saturday'), WeekdayReportVariable.extract_value(c3))

class BranchReportVariableTestCase(TestCase):
    fixtures = ['reports-test.json']

    def test_values(self):
        self.assertEqual(len(BranchOffice.objects.all()), len(list(BranchReportVariable.values())))

        self.assertTrue('Office Amsterdam' in BranchReportVariable.values())
        self.assertTrue('Office Rotterdam' in BranchReportVariable.values())

    def test_extract(self):
        c1 = Chat.objects.get(pk=1)
        c2 = Chat.objects.get(pk=2)
        c3 = Chat.objects.get(pk=3)

        self.assertEqual(_('Other'), BranchReportVariable.extract_value(c1))
        self.assertEqual('Office Amsterdam', BranchReportVariable.extract_value(c2))
        self.assertEqual('Office Amsterdam', BranchReportVariable.extract_value(c3))
