from os.path import dirname, join, abspath

# import settings from egg:
# see: /usr/local/lib/python2.6/dist-packages/helpim/settings.py
from helpim.settings import *


# the rest are default settings for this server

TIME_ZONE = 'Europe/Amsterdam'
LANGUAGE_CODE = 'nl-NL'
BOT['language'] = 'nl-nl'
STATIC_ROOT = ''
STATIC_URL = '/static/'
ADMIN_MEDIA_PREFIX = '/static/admin/'
FORMS_BUILDER_USE_SITES = False
SERVER_EMAIL = 'root@xen9.vandervlis.nl'
SEND_BROKEN_LINK_EMAILS = True
ADMINS = (('Helpdesk', 'helpdesk@e-hulp.nl'),)
FIXTURE_DIRS = ('/usr/local/share/helpim/fixtures',)
TEMPLATE_DIRS = ('/usr/local/share/helpim/templates',)
LOCALE_PATHS = ('/usr/local/share/helpim/locale',)

# raise loglevel of bot, needed for debugging (dd 2013-03-28)
LOGGING['loggers']['helpim.rooms.bot']['level'] = "INFO"

# specific for Prosody:
CHAT['domain'] = 'anon.localhost'
CONVERSATION_KEEP_DAYS = 60
