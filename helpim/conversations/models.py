from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import ugettext as _
from threadedcomments.models import ThreadedComment

from datetime import datetime

class Conversation(models.Model):
    start_time = models.DateTimeField()
    subject = models.CharField(max_length=64, blank=True)

    def __unicode__(self):
        return _('"%(subject)s" at %(start_time)s') % {
          'subject': self.subject,
          'start_time': self.start_time.strftime('%c'),
        }

    class Meta:
        ordering = ['start_time']
        verbose_name = _("Conversation")
        verbose_name_plural = _("Conversations")

class Participant(models.Model):

    ROLE_CLIENT = 'C'
    ROLE_STAFF = 'S'

    conversation = models.ForeignKey(Conversation)
    name = models.CharField(max_length=64)

    user = models.OneToOneField(User, null=True)

    role = models.CharField(max_length=2, choices=(
      (ROLE_CLIENT, _('Client')),
      (ROLE_STAFF, _('Staff')),
    ), null=True)

    ip_hash = models.CharField(max_length=32, blank=True)
    blocked = models.BooleanField()
    blocked_at = models.DateTimeField(null=True, default=None)

    def save(self, *args, **kwargs):
        if self.blocked:
            self.blocked_at = datetime.now()
        super(Participant, self).save(*args, **kwargs)

    def __unicode__(self):
        return self.name

    class Meta:
        verbose_name = _("Participant")
        verbose_name_plural = _("Participants")

class Message(models.Model):
    conversation = models.ForeignKey(Conversation)
    sender = models.ForeignKey(Participant)
    sender_name = models.CharField(_("Sender"), max_length=64)
    created_at = models.DateTimeField()
    body = models.TextField()

    comments = models.ManyToManyField(ThreadedComment)

    def time_sent(self):
        return str(self.created_at.time())

    def save(self, *args, **kwargs):
        if not self.created_at:
            self.created_at = datetime.now()
        super(Message, self).save(*args, **kwargs)


class Chat(Conversation):
    pass

class ChatMessage(Message):
    EVENT_CHOICES = (
        ('message', _('Chat message')),
        ('join', _('Chat joining')),
        ('rejoin', _('Chat rejoining')),
        ('left', _('Chat exit')),
        ('ended', _('Chat end')),
    )
    event = models.CharField(max_length=10, choices=EVENT_CHOICES, default='message')
