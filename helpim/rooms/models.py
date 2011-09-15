import datetime

from hashlib import md5

from django.conf import settings
from django.db import models
from django.db import transaction
from django.utils.translation import ugettext as _
from django.core.urlresolvers import reverse
from django.contrib.auth.models import User

from helpim.conversations.models import Chat, Participant
from helpim.utils import newHash

class Site:
    def __init__(self, name):
        self.name = name
        """ [FIXME] will it blend? """
        self.rooms = One2OneRoom.objects
        self.groupRooms = GroupRoom.objects

def getSites():
    """ this is a fake sites dict as we're not using real sites right now """
    return {'helpim': Site('helpim')}

class RoomManager(models.Manager):
    def newRoom(self, jid, password):
        """Adds a new room to the rooms, returns the new room object"""
        room = self.model(jid=jid, password=password)
        room.save()
        return room

    def getToDestroy(self):
        """Returns a list with all rooms with status 'toDestroy'"""
        return self.getByStatus("toDestroy")

    def getAvailable(self):
        """Returns a list with all rooms with status 'available'"""
        return self.getByStatus("available")

    def getChatting(self):
        """Returns a list with all rooms with status 'chatting'"""
        return self.getByStatus("chatting")

    def getAbandoned(self):
        """Returns a list with all rooms with status 'abandoned'"""
        return self.getByStatus("abandoned")

    def getAll(self):
        """Returns a list with all room-objects"""
        return list(self.all())

    def getByStatus(self, status):
        """Returns a list with room-objects with status 'status'

           Keyword arguments:
           status -- string, the status to select the rooms on
           """
        return list(self.filter(status=status))

    def getStatusById(self, roomId):
        """Returns a string with the status of the room with the given
           id.
           Keyword arguments:
           id -- int, the id of the room the status of is requested
           """
        return self.get(pk=roomId).status

    def getTimedOut(self, status, timeout):
        """Returns a list with room-objects with status 'status' that
           have that status longer then 'timeout'

           Keyword arguments:
           status -- string, the status to select the rooms on
           timeout -- timeout in seconds
           """
        cutOffTime=datetime.datetime.now()-datetime.timedelta(seconds=timeout)
        return list(self.filter(status=status).filter(status_timestamp__lte=cutOffTime))

    def getNotDestroyed(self):
        """Returns a list with room-objects that are not destroyed."""
        return list(self.exclude(status='destroyed'))

    def getByJid(self, jid):
        """Returns the room-objects with given jid

           Keyword arguments:
           jid -- string, jid to select the rooms on
           """
        return self.get(jid=jid)

    def getByPassword(self, password):
        """Returns the room-objects with given password

           Keyword arguments:
           password -- string, password to select the room on
           """
        return self.get(password=password)

    def deleteClosed(self):
        """Deletes records with the status 'destroyed'"""
        self.filter(status='destroyed').delete()

class One2OneRoomManager(RoomManager):
    def getAvailableForInvitation(self):
        """Returns a list with all rooms with status 'availableForInvitation'"""
        return self.getByStatus("availableForInvitation")

    def getStaffWaiting(self):
        """Returns a list with all rooms with status 'staffWaiting'"""
        return self.getByStatus("staffWaiting")

    def getStaffWaitingForInvitee(self):
        """Returns a list with all rooms with status 'staffWaitingForInvitee'"""
        return self.getByStatus("staffWaitingForInvitee")

    def getHangingStaffStart(self, timeout):
        """Returns a list with room-objects with status 'status' that
           have that status longer then 'timeout'

           Keyword arguments:
           status -- string, the status to select the rooms on
           timeout -- timeout in seconds
           """
        cutOffTime=datetime.datetime.now()-datetime.timedelta(seconds=timeout)
        return list(
            self.filter(
                models.Q(status='available') | models.Q(status='availableForInvitation'),
                modified_timestamp__lte=cutOffTime).exclude(staff=None))

    def getByClientId(self, clientId):
        """Returns the room-objects with given clientId

           Keyword arguments:
           clientId -- string, client id to select the room on
           """
        client = Participant.objects.get(pk=clientId)
        return self.get(client=client)

    @transaction.commit_on_success
    def _admitStaff(self, staff_id, status=None):
        room = self.filter(staff=None).filter(status='available')[0]
        room.setStaffId(staff_id)
        if not status == None:
            room.setStatus(status)
        return room

    def admitStaff(self, staff_id):
        """Tries to bind a staff to an available room. Returns False if
           it failed, returns the room object it binded to if succeeded

           Keyword arguments:
           staff_id -- id of the staff that should be bound to a room
           """
        return self._adminStaff(staff_id)

    def admitStaffInvitation(self, staff_id):
        """Tries to bind a staff to an available room. Returns False if
           it failed, returns the room object it binded to if succeeded

           Keyword arguments:
           staff_id -- id of the staff that should be bound to a room
           """
        return self._admitStaff(staff_id, 'availableForInvitation')

    @transaction.commit_on_success
    def admitClient(self, client_id):
        """Tries to bind a client to a room with staff waiting. Returns
           False if it failed, returns the room object it binded to if
           succeeded.

           Keyword arguments:
           client_id -- id of the client that should be bound to a room
           """
        room = self.filter(client=None).filter(status='staffWaiting')[0]
        room.setClientId(client_id)
        return room

    def admintClientInvitation(self, roomId, clientId):
        """Tries to bind a client to the room where the staff with the given
           id is waiting. Returns false if the room is not available, returns
           the room object if succeeded.

           Keyword arguments:
           staff_id -- id of the staff the client should be connected to
           client_id -- id of the client that should be bound to the staff
           """
        room = self.get(pk=roomId)
        if not room.getStatus() == "staffWaitingForInvitee":
            raise Exception("Attempt to enter room where inviter should be waiting, but room has status: " + room.getStatus())
        room.setClientId(clientId)
        return room

class GroupRoomManager(RoomManager):

    @transaction.commit_on_success
    def admitToGroup(self, chatId):
        """Admits a client to the group with id 'chat_id'. If no room is
        assigend to that chat, a new room is assigned to it. Returns
        False if it failed, returns the room object it binded to if
        succeeded.

        Keyword arguments:
        chat_id -- id of the chat the client should be admitted to.
        """
        chatId=int(chatId)
        chat = Chat.objects.get(pk=chatId)
        try:
            room = self.get(chat=chat)[0]
        except Room.DoesNotExist:
            rooms = self.filter(chat=None).filter(status='available')
            if len(rooms) == 0:
                return False # this looks odd but hey, don't break the old API
            room = rooms[0]
            room.chat = chat
            room.save()
        return room

class StatusError(Exception):
    """ thrown when rooms status is set to a bad value
    this does not refer to a value that's not defined
    this refers to a value that's not intended by the defined protocol flow """
    pass

class Room(models.Model):
    """ abstract base class for all kind of rooms """
    STATUS_CHOICES = () # this must be overriden by inheriting class

    jid = models.CharField(max_length=255,
                           unique=True)
    status = models.CharField(max_length=32,
                              choices=STATUS_CHOICES,
                              default='available')
    password = models.CharField(max_length=64)
    chat = models.ForeignKey(Chat,
                             null = True)
    web_clean_exit = models.BooleanField()
    status_timestamp = models.DateTimeField(null = True)
    modified_timestamp = models.DateTimeField(null = True)

    class Meta:
        abstract = True

    def getRoomId(self):
        return self.jid[:self.jid.find('@')]

    def getRoomService(self):
        return self.jid[self.jid.find('@')+1:]

    def getStaffJoinUrl(self):
        return reverse('staff_join_specific_chat', args=[self.pk,])

    def current_status(self):
        linktext = dict(self.STATUS_CHOICES)[self.status]

        if self.status == 'available':
            return '<b style="font-size: 12px;"><a href="%(link)s">%(linktext)s</a></b>' % {
              'link': self.getStaffJoinUrl(),
              'linktext': linktext,
            }
        else:
            return linktext

    current_status.allow_tags = True
    current_status.short_description = _('Room status')

    def getStatus(self):
        return self.status

    def setStatus(self, status):
        self.status = status
        self.save()

    def setChatId(slef, chatId):
        chat = Chat.objects.get(pk=chatId)
        self.chat = chat
        self.save()

    def getCleanExit(self):
        return self.web_clean_exit

    def setCleanExit(self):
        self.web_clean_exit = True
        self.save()

    def setPassword(self, password):
        self.password = password
        self.save()

    def destroyed(self):
        self.setStatus('destroyed')

class One2OneRoom(Room):
    """Chatroom for having one-to-one chats.
       When created, it is just an empty object with the status:
       'available'. The room object must be created when the room is
       actually created on the jabber-server.

       A full list of possible statusses of a one-to-one room:

       available    Jid is known, room is created at jabber-server.
       availableForInvitation
                    Jid is known, room is created at the jabber-server
                    and the room is assigned for a chat by invitation.
       staffWaiting A staffmember has entered the room and is waiting
                    for a client to chat with.
       staffWatingForInvitee
                    A staffmember has entered the room and is waiting
                    for the client invited for the chat to enter.
       chatting     Both a staffmember and a client are present in the
                    room, hopefully they are chatting.
       closingChat  The staffmembor OR the client is still present, but
                    one of them initiated the closing of the chat. The
                    remaining participant(s) should leave or be kicked
                    if the participant doesn't leave before te time-out.
       toDestroy    There has been a chat and both partitipants have left.
                    There is no need anymore to keep the room at the
                    jabber-server.
       destroyed    The room is destroyed at the jabber-server.
       lost         During a chat one of the participants disapeared
                    without confirming an end of the chat.
       abandoned    During a chat both the client and the staffmember
                    disapeared without confirming an end of the chat.
                    If none of them return before the timeout, the room
                    must be destroyed.
       """


    class Meta:
        verbose_name = _('One2One Room')

    def __str__(self):
        return '<One2OneRoom:%s>' % self.status

    STATUS_CHOICES = (
        ('available', _('Available')),
        ('availableForInvitation', _('Available for Invitation')),
        ('staffWaiting', _('Staff Waiting')),
        ('staffWaitingForInvitee', _('Staff Waiting for Invitee')),
        ('chatting', _('Chatting')),
        ('closingChat', _('Closing Chat')),
        ('toDestroy', _('To Destroy')),
        ('destroyed', _('Destroyed')),
        ('lost', _('Lost')),
        ('abandoned', _('Abandoned')),
        )

    staff = models.ForeignKey(Participant, verbose_name=_('Staff member'), related_name='+', null = True, limit_choices_to={'role': 'CW'})
    staff_nick = models.CharField(_('Staff nickname'), max_length=64, null = True)

    client = models.ForeignKey(Participant, verbose_name=_('Client'), related_name='+', null = True, limit_choices_to={'role': 'CS'})
    client_nick = models.CharField(_('Client nickname'), max_length=64, null = True)
    client_allocated_at = models.DateTimeField(
        _('Allocated by client'),
        null = False,
        default = '1000-01-01 00:00:00'
        )

    objects = One2OneRoomManager()

    def setClientId(self, clientId):
        client = Participant.objects.get(pk=clientId)
        self.client = client
        self.save()

    def setStaffNick(self, nick):
        self.staff_nick = nick
        self.save()

    def setClientNick(self, nick):
        self.client_nick = nick
        self.save()

    def staffJoined(self, nick):
        """To be called after the staffmember has joined
        the room at the jabber-server.
        """

        if not self.chat:
            chat = Chat(start_time=datetime.datetime.now(), subject=_('Chat'))
            chat.save()
            self.chat = chat

        if not self.staff:
            user = User.objects.get(username=nick)
            staff = Participant(
                conversation=self.chat,
                name=nick,
                user=user,
                role=Participant.ROLE_STAFF
            )
            staff.save()
            self.staff = staff

        if self.getStatus() == "available":
            self.setStatus("staffWaiting")
        elif self.getStatus() == "availableForInvitation":
            self.setStatus("staffWaitingForInvitee")
        else:
            raise StatusError("staff joining room while not room status is not 'available' or 'availableForInvitation'")

    def clientJoined(self, nick):
        """To be called after a client has joined
           the room at the jabber-server.
           """

        if not self.chat:
            chat = Chat(start_time=datetime.datetime.now(), subject=_('Chat'))
            chat.save()
            self.chat = chat

        if not self.client:
            client = Participant(
                    conversation=self.chat, name=nick, role=Participant.ROLE_CLIENT)
            self.client = client
            try:
                # store participant to access token so that we're able to block
                accessToken = AccessToken.objects.filter(room=self).filter(role=Participant.ROLE_CLIENT)[0]
                accessToken.owner = client
                accessToken.save()
                client.ip_hash = accessToken.ip_hash
            except:
                pass
            client.save()

        if self.getStatus() in ("staffWaiting", "staffWaitingForInvitee"):
            self.setStatus("chatting")
        else:
            raise StatusError("client joining room while not room status is not 'staffWaiting' or 'staffWaitingForInvitee'")

    def userLeftDirty(self):
        """To be called when a participant (either client or staff) has left the chat."""
        status = self.getStatus()
        if status == 'lost':
            self.setStatus('abandoned')
        elif status == 'chatting':
            self.setStatus('lost')
        elif status in ('staffWaiting', 'staffWaitingForInvitee', 'closingChat'):
            self.setStatus('toDestroy')
        else:
            raise StatusError("Participant left dirty while status is '%s'." % status)

    def userLeftClean(self):
        """To be called when a participant (either client or staff) has left the chat."""
        status = self.getStatus()
        if status in ('lost', 'staffWaiting', 'staffWaitingForInvitee', 'closingChat'):
            self.setStatus('toDestroy')
        elif status == 'chatting':
            self.setStatus('closingChat')
        else:
            raise StatusError("Participant left clean while status is '%s'." % status)

class GroupRoom(Room):
    """Chatroom for having groupchats.
       When created, it is just an empty object with the status:
       'available'. The room object must be created when the room is
       actually created on the jabber-server.

       A full list of possible statusses of a one-to-one room:

       available    Jid is known, room is created at jabber-server.
       chatting     At least one participant is present.
       toDestroy    There has been a groupchat, all partitipants have
                    left and the last participent has left cleanly..
                    There is no need anymore to keep the room at the
                    jabber-server.
       destroyed    The room is destroyed at the jabber-server.
       abandoned    There has been a groupchat, all partitipants have
                    left and the last participent has left dirty.
                    If none of the participants return before the
                    timeout, the room must be destroyed.
       """

    STATUS_CHOICES = (
        ('available', _('Available')),
        ('chatting', _('Chatting')),
        ('toDestroy', _('To Destroy')),
        ('destroyed', _('Destroyed')),
        ('abandoned', _('Abandoned')),
        )

    objects = GroupRoomManager()

    def userJoined(self):
        """To be called after a client has joined
           the room at the jabber-server.
           """
        status = self.getStatus()
        if status in ("available", "abandoned"):
            self.setStatus("chatting")
        elif status == "chatting":
            pass
        else:
            raise StatusError("client joining room while not room status is not 'available' or 'chatting'")

    # ToDo: these functions need to be adapted!!!!!!!

    def lastUserLeftDirty(self):
        """To be called when the last participant has left the chat dirty."""
        if not self.getStatus() == "chatting":
            raise StatusError("Participant left dirty while status was not chatting")
        self.setStatus('abandoned')

    def lastUserLeftClean(self):
        """To be called when the last participant has left the chat clean."""
        if not self.getStatus() == "chatting":
            raise StatusError("Participant left clean while status was not chatting")
        self.setStatus('toDestroy')

class AccessToken(models.Model):
    token = models.CharField(max_length=64, unique=True)
    role = models.CharField(max_length=2,
                            choices=(
                                (Participant.ROLE_CLIENT, _('Client')),
                                (Participant.ROLE_STAFF, _('Staff')),
                                ))
    room = models.ForeignKey(One2OneRoom, null=True)
    owner = models.ForeignKey(Participant, null=True)
    ip_hash = models.CharField(max_length=32, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @staticmethod
    def get_or_create(ip, role=Participant.ROLE_CLIENT, token=None):
        # delete outdated tokens
        AccessToken.objects.filter(created_at__lte=datetime.datetime.now()-datetime.timedelta(seconds=settings.ROOMS['access_token_timeout'])).delete()

        # check if remote IP is blocked
        ip_hash = md5(ip).hexdigest()
        if role is Participant.ROLE_CLIENT and Participant.objects.filter(ip_hash=ip_hash).filter(blocked=True).count() is not 0:
            # this user is blocked
            return None

        if token is not None:
            try:
                return AccessToken.objects.get(token=token)
            except:
                pass

        at = AccessToken(token=newHash(), role=role, ip_hash=ip_hash)
        at.save()
        return at

    def __unicode__(self):
        return self.token
