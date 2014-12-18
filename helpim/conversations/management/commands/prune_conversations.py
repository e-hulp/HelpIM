from datetime import timedelta, datetime
import sys

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand

from helpim.conversations.models import Conversation, Message


class Command(BaseCommand):
    def handle(self, *args, **options):
        self.verbosity = int(options.get('verbosity', 1))

        try:
            days_to_keep = int(settings.CONVERSATION_KEEP_DAYS)
        except (ValueError, AttributeError):
            raise ImproperlyConfigured("You have not set CONVERSATION_KEEP_DAYS to a number in settings.py")
            sys.exit(1)

        up_for_deletion = datetime.utcnow() - timedelta(days=days_to_keep)

        self.__verbose('Deleting everything before %s ...' % (up_for_deletion), 2)

        conversations = Conversation.objects.filter(created_at__lt=up_for_deletion)
        messages = Message.objects.filter(conversation__in=conversations)

        self.__verbose('%d conversations, containing %d messages ...' % (conversations.count(), messages.count()), 2)

        # empty contents of messages
        for msg in messages:
            msg.body = '*****'
            msg.save()

        self.__verbose("done.", 2)

    def __verbose(self, message, verbosity=1):
        '''only print message if verbosity-level is high enough'''
        if self.verbosity >= verbosity:
            print message
