#!/usr/bin/env python

'''
Usage: bot.py [<options>]

Most options override settings from the configuration file.
   
Options:
    -h, --help
          Show this text.

    -u,  --username=USERNAME
           Override username from configuration file.

    -n,  --nick=NICK
           Override nick from configuration file.

    -p,  --password=PASSWORD
           Override password from configuration file.

    -P,  --port=PORT
           Override port from configuration file.

    -d,  --domain=DOMAIN
           Override domain from configuration file.

    -r,  --resource=RESOURCE
           Override resource from configuration file.

    -m,  --muc-domain=MUCDOMAIN
           Override muc-domain from configuration file.

    -s,  --room-pool-size=ROOMPOOLSIZE
           Override room-pool-size from configuration file.

    -l,  --log-level=LOGLEVEL
           Override log-level from configuration file.

    -y,  --log-level-pyxmpp=LOGLEVEL
           Override log-level for pymxpp logging from configuration file.

    -t,  --log-destination=LOGDESTINATION
           Override log-destination from configuration file.

'''

import sys
import os
import logging
import getopt
import socket
import traceback

from HelpIM.paths import botConfig, sitesDir
from HelpIM.utils import newHash
from HelpIM.xmlconfig import Config


from time import sleep
from signal import signal, alarm, SIGALRM

from cStringIO import StringIO

from sqlalchemy.orm import sessionmaker
import sqlalchemy

from pyxmpp.jabber.client import JabberClient
from pyxmpp.jid import JID
from pyxmpp.message import Message
from pyxmpp.presence import Presence
from pyxmpp.jabber.muc import MucRoomManager, MucRoomHandler, MucRoomUser
from pyxmpp.jabber.muccore import MucPresence, MucIq, MucAdminQuery, MucItem

import HelpIM.base.xmlObject as xmlObject
import HelpIM.databaseMetadata
import HelpIM.rooms

# initiate the HelpIM db services
import HelpIM.chat.db.services
services = HelpIM.chat.db.services.DatabaseServices()

import HelpIM.chatgroup.db.services
groupServices = HelpIM.chatgroup.db.services.DatabaseServices()

SiteConfigPath = "/etc/HelpIM/sites"

class Site:
    def __init__(self, name, metadata):
        """Initiates a new site, performing the following actions:
           - Reading its settings
           - Opening its database connection
           - Creating its rooms-object
           etc.."""
        self.name = name
        confFile = SiteConfigPath + "/" + name + '.xml'
        if os.path.isfile(confFile):
            siteConfig = xmlObject.readXmlFile(fileName=confFile)
            self.metadata = metadata
            self.engine = sqlalchemy.create_engine(siteConfig.databaseuri)
            Session = sessionmaker(bind=self.engine, autocommit=True)
            self.session = Session()
            self.rooms = HelpIM.rooms.Rooms(self.metadata, self.engine)
            self.groupRooms = HelpIM.rooms.GroupRooms(self.metadata, self.engine)
        else:
            raise IOError(confFile+' is not a file')

def getSites():
    metadata = sqlalchemy.MetaData()
    metadata = HelpIM.databaseMetadata.getAllMetadata(metadata)
    sites = {}
    for confFile in os.listdir(SiteConfigPath):
        try:
            name, ext = confFile.rsplit('.', 1)
        except ValueError:
            ext = None
        if os.path.isfile(SiteConfigPath + "/" + confFile) and ext == 'xml':
            sites[name] = Site(name, metadata)
    return sites

def str2roomjid(jidstr):
    tmp = jidstr.split('@')
    node = tmp[0]
    domain = tmp[1].split('/')[0]
    roomjid = JID(node, domain)
    return roomjid

class LogError(Exception):
    def __init__(self, msg):
        self.msg = msg
    def __str__(self):
        return repr(self.msg)

class BotError(Exception):
    def __init__(self, msg):
        self.msg = msg
    def __str__(self):
        return repr(self.msg)

class Stats:
    pass

class Log:
    def __init__(self):
        logging.addLevelName(25, "NOTICE")
        self.__log = logging.getLogger()
        self.__helpimlog = logging.getLogger('HelpIM3.bot')
        self.__pyxmpplog = logging.getLogger('pyxmpp')
        self.__pyxmpplog.setLevel(logging.DEBUG)
        self.__logHandler = None

    def form(self, form):
        self.debug("MUC-room configuration form ==BEGIN==")
        for field in form:
            self.debug("  Field '%s':" % field.name)
            self.debug("    Label = %s" % field.label)
            self.debug("    Description = %s" % field.desc)
            self.debug("    Type = %s" % field.type)
            self.debug("    Required = %s" % field.required)
            self.debug("    Value = %s" % field.value)
            self.debug("    Options:")
            for option in field.options:
                self.debug("      Label = %s" % option.label)
                self.debug("      Values = %s" % option.values)
            self.debug("    Values = %s" % field.values)
        self.debug("MUC-room confugration form ==END==")

    def user(self, user):
        self.debug("User log ==BEGIN==")
        if user.real_jid:
            self.debug("  Real JID = %s" % user.real_jid.as_unicode())
        self.debug("  Room JID = %s" % user.room_jid.as_unicode())
        self.debug("  Nick = %s" % user.nick)
        self.debug("  Affiliation = %s" % user.affiliation)
        self.debug("  Role = %s" % user.role)            
        self.debug("User log ==END==")

    def stanza(self, stanza):
        stanzaType = stanza.get_stanza_type()
        objectType = None
        if isinstance(stanza, MucPresence):
            objectType = "MucPresence"
        elif isinstance(stanza, Presence):
            objectType = "Presence"
        elif isinstance(stanza, Message):
            objectType = "Message"
        self.debug("Stanza log ==BEGIN==")
        self.debug("  Stanza type = %s" % stanzaType)
        self.debug("  XMPP object type = %s" % objectType)
        self.debug("  From = %s" % stanza.get_from().as_unicode())
        self.debug("  To   = %s" % stanza.get_from().as_unicode())
        if objectType == "Message":
            self.debug("  Subject = %s" % stanza.get_subject())
            self.debug("  Body = %s" % stanza.get_body())
        elif objectType == "Presence" or objectType == "MucPresence":
            self.debug("  Priority = %s" % stanza.get_priority())
            self.debug("  Status = %s" % stanza.get_status())
            self.debug("  Show = %s" % stanza.get_show())
        elif objectType == "MucPresence":
            joininfo = stanza.get_join_info()
            self.debug("  Password = %s" % joininfo.get_password())
            self.debug("  History = %s" % joininfo.get_history())
            mucchild = stanza.get_muc_child()
            self.debug("MUC child = %s" % mucchild)
        self.debug("Stanza log ==END==")

    # set_... methods: Methods to change settings that can be changed at runtime.
    #
    # Note: The set_... methods below return empty string on success, and error-string on failure.
    #       So using the returned value in conditional expressions may opposite of what you might expect.
    #
    def set_Destination(self, dest):
        '''Sets logging destination -> empty string on success. error-string on failure.

        Arguments:
        dest - A string: either "stdout", "stderr" or a string starting with "file:" followed
               by a valid file path.
               An empty string has the same effect as "stderr".
               Leading or railing whitespace is ignored.

        '''
        dest = dest.strip()
        if not dest or dest == "stderr":
             newhandler = logging.StreamHandler(sys.stderr)
        elif dest == "stdout":
            newhandler = logging.StreamHandler(sys.stdout)
        elif dest.split(':')[0] == 'file':
            filepath = dest.split(':')[1].strip()
            if not filepath:
                return "No file path specified in logging destination"
            try:
                logfile = open(filepath, 'a')
            except IOError, e:
                return "Could not open %s for writing: %s" % (e.filename, e.args[1])
            newhandler = logging.StreamHandler(logfile)
        else:
            return "Invalid log destination '%s'" % dest
        if self.__logHandler is not None:
            self.__log.removeHandler(self.__logHandler)
        self.__helpimlog.info("Setting log destination to '%s'" % dest)
        formatter = logging.Formatter('%(asctime)s %(name)s %(levelname)-8s %(message)s')
        newhandler.setFormatter(formatter)
        self.__logHandler = newhandler
        self.__log.addHandler(newhandler)
        self.__helpimlog.info("Log destination now set to '%s'" % dest)
        return str()

    # Actual logging methods
    #
    def critical(self, msg): self.__helpimlog.critical(msg)
    def error(self, msg): self.__helpimlog.error(msg)
    def warning(self, msg): self.__helpimlog.warning(msg)
    def notice(self, msg): self.__helpimlog.log(25, msg)
    def info(self, msg): self.__helpimlog.info(msg)
    def debug(self, msg): self.__helpimlog.debug(msg)

    def set_Level(self, level, loggername=''):
        '''Sets log level --> empty string on success. error-string on failure.

        Arguments:
        level -  One of the following strings: "critical", "error", "warning", "notice",
                 "info" or "debug". 
                 An empty string has the same effect as "notice".
                 Leading or railing whitespace is ignored.

        '''
        level = level.strip()
        if not level:             newlevel = 25
        elif level == "critical": newlevel = 50
        elif level == "error":    newlevel = 40
        elif level == "warning":  newlevel = 30
        elif level == "notice":   newlevel = 25
        elif level == "info":     newlevel = 20
        elif level == "debug":    newlevel = 10
        else:
            return "Invalid log level '%s'." % level
        level = level.upper()
        logger = logging.getLogger(loggername)
        logger.setLevel(newlevel)
        self.__helpimlog.info("Log level now set to '%s'" % level)
        return str()

class RoomHandlerBase(MucRoomHandler):
    def __init__(self, bot, site, mucconf, nick, password, rejoining=False):
        MucRoomHandler.__init__(self)
        self.mucmanager = bot.mucmanager
        self.kick = bot.kick
        self.makeModerator = bot.makeModerator
        self.todo = bot.todo
        self.closeRooms = bot.closeRooms
        self.fillMucRoomPool = bot.fillMucRoomPool
        self.site = site
        self.mucconf = mucconf
        self.password = password
        self.nick = nick
        self.userkicked = ''
        self.closingDown = False
        self.maxUsers = 10
        self.type = "Base"
        if rejoining:
            self.rejoinCount = 0
        else:
            self.rejoinCount = None

    def affiliation_changed(self, user, old_aff, new_aff, stanza):
        log.debug("Callback: affiliation_changed(%s, %s, %s, %s)" % (user, old_aff, new_aff, stanza))
        return True

    def configuration_form_received(self, form):
        log.debug("MUC-Room callback: configuration_form_received(%s)" % (form))
        log.debug("Configuring MUC-room '%s'" % self.room_state.room_jid.as_unicode())
        
        for field in form:
            if  field.name == u'allow_query_users':
                field.value = False
            elif field.name == u'muc#roomconfig_allowinvites':
                field.value = False
            elif field.name == u'muc#roomconfig_passwordprotectedroom':
                field.value = True
            elif field.name == u'muc#roomconfig_roomsecret':
                field.value = self.password
                log.debug("Setting MUC-room password to: '%s'" % self.password)
            elif field.name == u'muc#roomconfig_roomname':
                field.value = u'HelpIM'  # FIXME: Make this the name of the site the room belongs to?
            elif field.name == u'muc#roomconfig_persistentroom':
                field.value = False
            elif field.name == u'muc#roomconfig_publicroom':
                field.value = False
            elif field.name == u'public_list':
                field.value = False
            elif field.name == u'muc#roomconfig_maxusers':
                # Find lowest available option, but at least 3.
                maxusers = 9999999
                for option in field.options:
                    try:
                        value = int(option.values[0])
                    except ValueError:
                        log.warning("Form option for 'muc#roomconfig_maxusers' does not convert to int?")
                        log.warning("Option values received from server were: %s" % option.values)
                    if value >= self.maxusers and value < maxusers:
                        maxusers = value
                if maxusers == 0:
                    log.warning("Could not configure 'muc#roomconfig_maxusers'. No usable option found in form")
                    log.warning("Continuing with this option at default value, which is: %s" % field.value)
                else:
                    log.debug("Setting maxuser to %d." % maxusers)
                    field.value = unicode(maxusers)
            elif field.name == u'muc#roomconfig_whois':
                for option in field.options:
                    if option.values[0] == self.mucconf["whoisaccess"]:
                        field.value = unicode(self.mucconf["whoisaccess"])
                        break
                else:
                    log.warning("Configuration setting 'whoisaccess=\"%s\"' not valid according to form received from server" % self.mucconf["whoisaccess"])
                    log.warning("Continuing with this option at default value, which is: %s" % field.value)
            elif field.name == u'muc#roomconfig_membersonly':
                field.value = False
            elif field.name == u'muc#roomconfig_moderatedroom':
                field.value = False
            elif field.name == u'members_by_default':
                field.value = True
            elif field.name == u'muc#roomconfig_changesubject':
                allowchangesubject = str(self.mucconf["allowchangesubject"]).lower()
                field.value = (allowchangesubject=="yes" or allowchangesubject=="1" or allowchangesubject=="true")
            elif field.name == u'allow_private_messages':
                field.value = False
            elif field.name == u'allow_query_users':
                field.value = False
            elif field.name == u'muc#roomconfig_allowinvites':
                field.value = False
        log.form(form)
        form = form.make_submit(True)
        self.room_state.configure_room(form)
        return True

    def error(self, stanza):
        # Try to log messages that make sense from this information
        #
        errnode = stanza.get_error()
        stanzaclass = stanza.__class__.__name__
        errortype = str(errnode.get_type().lower())
        errormsg = errnode.get_message()

        # If our limit of presences in MUC-rooms is exceeded
        #
        if errortype == "cancel" and stanzaclass == "Presence":
            log.error("Could not create room '%s...'. Probably server limited number of presences in MUC-rooms."
                      % self.room_state.room_jid.as_unicode().split('@')[0][:30]
                      )
            log.error("XMPP error message was: '%s: %s'." % (errortype, errormsg))
        # FIXME:
        # elif <other known combination that may occur> :
        #    log.error(<message that makes sense>)
        
        log.debug("XMPP error type: '%s'.  PyXMPP error class: '%s'.  Message: '%s'." % (errortype, stanzaclass, errormsg))
        self.room_state.leave()
        self.mucmanager.forget(self.room_state)
        return True

    def nick_change(self, user, new_nick, stanza):
        #DBG log.debug("MUC-Room callback: nick_change(%s, %s, %s)" % (user, new_nick, stanza))
        #DBG log.stanza(stanza)
        #DBG log.user(user)
        log.debug("New nick = %s" % new_nick)
        return True

    def nick_changed(self, user, old_nick, stanza):
        #DBG log.debug("MUC-Room callback: nick_changed(%s, %s, %s)" % (user, old_nick, stanza))
        #DBG log.stanza(stanza)
        #DBG log.user(user)
        log.debug("New nick = %s" % old_nick)
        return True

    def presence_changed(self, user, stanza):
        #DBG log.debug("MUC-Room callback: presence_changed(%s, %s)" % (user, stanza))
        #DBG log.stanza(stanza)
        #DBG log.user(user)
        return False

    def role_changed(self, user, old_role, new_role, stanza):
        #DBG log.debug("Role changed: Old role = %s.  New role = %s" % (old_role, new_role))
        #DBG log.stanza(stanza)
        #DBG log.user(user)
        return True

    def room_configuration_error(self, stanza):
        log.error("MUC-Room callback: room_configuration_error(%s)" % (stanza))
        return True

    def room_created(self, stanza):
        log.debug("MUC-Room '%s' created" % self.room_state.room_jid.as_unicode())
        return True

    def subject_changed(self, user, stanza):
        log.debug("MUC-Room callback: subject_changed(%s, %s)" % (user, stanza))
        #DBG log.stanza(stanza)
        #DBG log.user(user)
        return True


class One2OneRoomHandler(RoomHandlerBase):

    def __init__(self, bot, site, mucconf, nick, password, rejoining=False):
        RoomHandlerBase.__init__(self, bot, site, mucconf, nick, password, rejoining)
        self.maxUsers = 3
        self.type = "One2OneRoom"

    def room_configured(self):
        jidstr = self.room_state.room_jid.bare().as_unicode()
        self.site.rooms.newRoom(jidstr, self.password)
        log.debug("MUC-Room '%s' created and configured successfully" % jidstr)
        return True

    def message_received(self, user, stanza):
        room = self.get_helpim_room()
        if room is None or user is None or stanza.get_body() is None or stanza.get_body()[0:16] == "[#startuplines#]":
            return True
        if room.getStatus() == 'chatting':
            if user.nick == room.client_nick:
                try:
                    services.logCareSeekerChatMessage(conv_id=room.chat_id,
                                             messageText=stanza.get_body(),
                                             nickName=user.nick,
                                             databaseSession=self.site.session)
                except AttributeError:
                    log.error("Could not store message in database, chat id: %s, from: %s" % (str(room.chat_id), user.nick))
                else:
                    self.site.session.flush()
                    
            elif user.nick == room.staff_nick:
                try:
                    services.logCareWorkerChatMessage(conv_id=room.chat_id,
                                             messageText=stanza.get_body(),
                                             user_id=room.staff_id,
                                             nickName=room.staff_nick,
                                             databaseSession=self.site.session)
                except AttributeError:
                    log.error("Could not store message in database, chat id: %s, from: %s" % (str(room.chat_id), user.nick))
                else:
                    self.site.session.flush()
        #DBG log.debug("MUC-Room callback: message_received(). User = '%s'" % (user))
        #DBG log.stanza(stanza)
        #DBG log.user(user)
        return True

    def user_joined(self, user, stanza):
        if user.nick == self.nick:
            return True
        room = self.get_helpim_room()
        if room is None:
            return
        status = room.getStatus()
        log.debug("user with nick " + user.nick + " joined room " + room.jid + " with status: " + room.getStatus())
        if status == 'available':
            room.staffJoined()
            room.setStaffNick(user.nick)
            self.todo.append((self.fillMucRoomPool, self.site))
            log.info("Staff member entered room '%s'." % self.room_state.room_jid.as_unicode())
            self.rejoinCount = None
        elif status == 'availableForInvitation':
            room.staffJoined()
            room.setStaffNick(user.nick)
            self.todo.append((self.fillMucRoomPool, self.site))
            log.info("Staff member entered room for invitation '%s'." % self.room_state.room_jid.as_unicode())
            self.rejoinCount = None
        elif status == 'staffWaiting':
            if self.rejoinCount is None:
                room.clientJoined()
                room.setClientNick(user.nick)
                log.info("Client entered room '%s'." % self.room_state.room_jid.as_unicode())
            else:
                self.rejoinCount = None
                log.info("A user rejoined room '%s'." % self.room_state.room_jid.as_unicode())
        elif status == 'staffWaitingForInvitee':
            if self.rejoinCount is None:
                room.clientJoined()
                room.setClientNick(user.nick)
                log.info("Client entered room for invitation '%s'." % self.room_state.room_jid.as_unicode())
            else:
                # hmmm... this should happen, doesn't it?
                self.rejoinCount = None
                log.info("A user rejoined room for invitation '%s'." % self.room_state.room_jid.as_unicode())
        elif status == 'chatting':
            services.logChatEvent(conv_id=room.chat_id,
                         eventName="rejoin",
                         eventData="%s rejoind the chat" % user.nick,
                         databaseSession=self.site.session)
            self.site.session.flush()
            if self.rejoinCount is not None:
                self.rejoinCount += 1
                if self.rejoinCount == 2:
                    self.rejoinCount = None
                    log.info("The second user rejoined room '%s'." % self.room_state.room_jid.as_unicode())
        else:
            if self.rejoinCount is not None:
                log.error("User entered room '%s' while already in 'chatting' status!" % self.room_state.room_jid.as_unicode())
                log.error("Kicking user: Nick = '%s'" % user.nick)
                self.kick(self.room_state.room_jid.bare(), user.nick)
                self.userkicked = user.nick
        return False

    def user_left(self, user, stanza):
        if user.nick == self.nick:
            return False
        roomname = self.room_state.room_jid.as_unicode()
        if self.userkicked == user.nick or self.closingDown:
            self.userkicked = ''
            log.notice("Kicked user '%s' has left room '%s'." % (user.nick, roomname))
            return False
        room = self.get_helpim_room()
        roomstatus = room.getStatus()

        cleanexit = stanza.get_status()
        if cleanexit is not None and cleanexit.strip() == u"Clean Exit":
            cleanexit = True
        else:
            cleanexit = False

        if room is None:
            return False
        if roomstatus == 'staffWaiting':
            if cleanexit:
                log.notice("Staffmember waiting for chat has left room '%s' (clean exit)." % roomname)
                room.userLeftClean()
            else:
                log.notice("Staffmember waiting for chat has disappeared from room '%s' (un-clean exit)." % roomname)
                room.userLeftDirty()
        if roomstatus == 'staffWaitingForInvitee':
            if cleanexit:
                log.notice("Staffmember waiting for invitation chat has left room '%s' (clean exit)." % roomname)
                room.userLeftClean()
            else:
                log.notice("Staffmember waiting for invitation chat has disappeared from room '%s' (un-clean exit)." % roomname)
                room.userLeftDirty()

        elif roomstatus == 'chatting':
            if cleanexit:
                room.userLeftClean()
                log.info("A user left room '%s' (clean exit)." % self.room_state.room_jid.as_unicode())
                services.logChatEvent(conv_id=room.chat_id,
                             eventName="ended",
                             eventData="%s ended the chat" % user.nick,
                             databaseSession=self.site.session)
                self.site.session.flush()
            else:
                room.userLeftDirty()
                log.info("A user left room '%s' (un-clean exit)." % self.room_state.room_jid.as_unicode())
                services.logChatEvent(conv_id=room.chat_id,
                             eventName="left",
                             eventData="%s left the chat" % user.nick,
                             databaseSession=self.site.session)
                self.site.session.flush()
            log.info("User was: Nick = '%s'." % user.nick)
        elif roomstatus == 'closingChat':
            if cleanexit:
                room.userLeftClean()
                log.info("A user left room '%s' while the other user already left clean before (clean exit)." % self.room_state.room_jid.as_unicode())
            else:
                room.userLeftDirty()
                log.info("A user left room '%s' while the other user already left clean before (un-clean exit)." % self.room_state.room_jid.as_unicode())
            log.info("User was: Nick = '%s'." % user.nick)
        elif roomstatus == 'lost':
            if cleanexit:
                room.userLeftClean()
                log.info("A user left room '%s' while the other user already left unclean before (clean exit)." % self.room_state.room_jid.as_unicode())
            else:
                room.userLeftDirty()
                log.info("A user left room '%s' while the other user already left unclean before (un-clean exit)." % self.room_state.room_jid.as_unicode())
            log.info("User was: Nick = '%s'." % user.nick)
        else:
            log.warning("User left room '%s' while room was expected to be empty (roomstatus == %s)." % (roomname, roomstatus))
            log.info("User was: Nick = '%s'." % user.nick)
        return False

    def get_helpim_room(self):
        '''Return the HelpIM-API room-object which this handler handles'''
        jidstr = self.room_state.room_jid.bare().as_unicode()
        try:
            return self.site.rooms.getByJid(jidstr)
        except KeyError:
            log.error("Could not find room '%s' in database." % jidstr)
            return None


class GroupRoomHandler(RoomHandlerBase):

    def __init__(self, bot, site, mucconf, nick, password, rejoining=False):
        RoomHandlerBase.__init__(self, bot, site, mucconf, nick, password, rejoining)
        self.maxUsers = 30
        self.type = "GroupRoom"

    def room_configured(self):
        jidstr = self.room_state.room_jid.bare().as_unicode()
        self.site.groupRooms.newRoom(jidstr, self.password)
        log.debug("MUC-Room for groupchat '%s' created and configured successfully" % jidstr)
        return True

    def message_received(self, user, stanza):
        #try:
        if True:
            room = self.get_helpim_room()
            if room is None or user is None or stanza.get_body() is None:
                return True
            if room.getStatus() == 'chatting':
                groupServices.logChatgroupMessage(self.site, room.chat_id, user.nick, stanza.get_body())
        #except:
        #   log.error("Could not store groupchat message in database, chat id: %s, from: %s" % (str(room.chat_id), user.nick))
        log.debug("MUC-Room for groupchat callback: message_received(). User = '%s'" % (user))
        # DBG log.debug(stanza.serialize())
        # DBG log.stanza(stanza)
        # DBG log.user(user)
        return True

    def get_helpim_room(self):
        '''Return the HelpIM-API room-object which this handler handles'''
        jidstr = self.room_state.room_jid.bare().as_unicode()
        try:
            return self.site.groupRooms.getByJid(jidstr)
        except KeyError:
            log.error("Could not find room '%s' in database." % jidstr)
            return None

    def user_joined(self, user, stanza):
        if user.nick == self.nick:
            return True
        room = self.get_helpim_room()
        if room is None:
            return
        status = room.getStatus()
        log.debug("user with nick " + user.nick + " joined group room " + room.jid + " with status: " + status)
        if status == "available":
            room.setStatus("chatting")
            log.info("User '%s' joined as first user group room '%s' for chat_id '%s'." % (user.nick, room.jid, room.chat_id))
        elif status == "abandoned":
            room.setStatus("chatting")
            log.info("User '%s' joined abandoned group room '%s' for chat_id '%s'." % (user.nick, room.jid, room.chat_id))
        elif status == "chatting":
            log.info("User '%s' joined room '%s' for chat_id '%s'." % (user.nick, room.jid, room.chat_id))
        else:
            log.warning("User '%s' joined room '%s' while not expected (roomstatus == %s)." % (user.nick, room.jid, status))
            return False

        groupMember = groupServices.getChatgroupMemberByMeetingIdAndNickname(self.site, room.chat_id, user.nick)
        if groupMember.is_admin:
            if not self.room_state.configured:
                log.warning("Should make participant moderator, but room is not configured. (Room: '%s')" % room.jid)
            if not self.room_state.me.affiliation=="admin" and not  self.room_state.me.affiliation=="owner":
                log.warning("Should make participant moderator, but bot is not admin. (Bot affiliation: '%s', Room: '%s')" % (self.room_state.me.affiliation, room.jid))
            log.info("Making user moderator: Nick = '%s'" % user.nick)
            self.makeModerator(self.room_state.room_jid.bare(), user.nick)

        #DBG log.debug("MUC-Room callback: user_joined(). User = '%s'" % (user))
        #DBG log.stanza(stanza)
        #DBG log.user(user)
        return False

    def user_left(self, user, stanza):
        if user.nick == self.nick:
            return False
        roomname = self.room_state.room_jid.as_unicode()
        room = self.get_helpim_room()

        groupServices.setChatgroupMeetingParticipantLeft(
            self.site,
            room.chat_id,
            user.nick)

        if self.userkicked == user.nick or self.closingDown:
            self.userkicked = ''
            log.notice("Kicked user '%s' has left room '%s'." % (user.nick, roomname))
            return False

        status = room.getStatus()
        nUsers = len(self.room_state.users) -1 # -1 for not counting the bot itself
        
        mucStatus = stanza.xpath_eval('d:x/d:status',
                                      {'d': 'http://jabber.org/protocol/muc#user'})

        if len(mucStatus) > 0:
            curAttr = mucStatus[0].properties
            while curAttr:
                if curAttr.name == 'code' and curAttr.content == '307':
                    """ this user must have been kicked. now we must make sure
                    to delete his access token and change the password for
                    this room
                    """
                    log.debug("############")
                    groupServices.setChatgroupMemberTokenInvalid(
                        self.site,
                        room.chat_id,
                        user.nick)
                    """ disabled resetting password as this would need to be done on xmpp level too and that's just too much work for now """
                    # password = unicode(newHash())
                    # room.setPassword(password)
                    break
                curAttr = curAttr.next
        
        cleanexit = stanza.get_status()
        if cleanexit is not None and cleanexit.strip() == u"Clean Exit":
            cleanexit = True
        else:
            cleanexit = False

        log.debug("user with nick " + user.nick + " left group room " + room.jid + " with status: " + status)
        if status == "chatting":
            if nUsers == 1:
                if cleanexit:
                    log.info("Last user '%s' left group room '%s' (clean exit, chat_id == '%s')." % (user.nick, room.jid, room.chat_id))
                    room.lastUserLeftClean()
                else:
                    log.info("Last user '%s' left group room '%s' (un-clean exit, chat_id == '%s')." % (user.nick, room.jid, room.chat_id))
                    room.lastUserLeftDirty()
            else:
                if cleanexit:
                    log.info("User '%s' left group room '%s' (clean exit, chat_id == '%s')." % (user.nick, room.jid, room.chat_id))
                else:
                    log.info("User '%s' left group room '%s' (un-clean exit, chat_id == '%s')." % (user.nick, room.jid, room.chat_id))
        else:
            log.warning("User '%s' left  room '%s' while room was expected to be empty (roomstatus == %s)." % (user.nick, room.jid, status))
            log.info("User was: Nick = '%s'." % user.nick)
        #DBG log.debug("MUC-Room callback: user_joined(). User = '%s'" % (user))
        #DBG log.stanza(stanza)
        #DBG log.user(user)
        return False


class Bot(JabberClient):
    def __init__(self, conf):
        self.stats = Stats()
        self.__last_room_basename = None
        self.__room_name_uniqifier = 0
        self.todo = list()
        self.__lost_connection = False
        self.conf = conf
        
        c = self.conf.connection
        self.jid = JID(c.username, c.domain, c.resource)
        self.nick = c.nick.strip()
        self.password = c.password
        self.port = int(c.port)
        self.loadSites()

    def roomCleanup(self):
        for name, site in self.sites.iteritems():
            # One2OneRooms
            for room in site.rooms.getToDestroy():
                log.info("Closing room %s which was not used anymore." % room.jid)
                self.closeRoom(room)
            for status in 'lost', 'closingChat', 'abandoned':
                for room in site.rooms.getTimedOut(status, int(self.conf.mainloop.cleanup)):
                    log.notice("Closing room %s which has timed out in '%s' status." % (room.jid, status))
                    self.closeRoom(room)
            for room in site.rooms.getHangingStaffStart(int(self.conf.mainloop.cleanup)):
                log.notice("Closing room %s which is has timed out while waiting for staff to enter room" % (room.jid))
                self.closeRoom(room)
            site.rooms.deleteClosed()
            # GroupRooms
            for room in site.groupRooms.getToDestroy():
                log.info("Closing groupRoom %s which was not used anymore." % room.jid)
                self.closeRoom(room)
            for room in site.groupRooms.getTimedOut('abandoned', int(self.conf.mainloop.cleanup)):
                log.notice("Closing groupRoom %s which has timed out in '%s' status." % (room.jid, status))
                self.closeRoom(room)
            site.groupRooms.deleteClosed()
        #DBG: self.printrooms()

    def alarmHandler(self, signum, frame):
        # Assumes only to be called for alarm signal: Ignores arguments
        self.cleanup = True

    def loadSites(self):
        self.sites = getSites()

    def run(self):
        JabberClient.__init__(self, self.jid, self.password, port=self.port, disco_name="HelpIM3 chat room manager", disco_type="bot")
        self.stats.mainloopcount = 0
        self.stats.busycount = 0
        self.stats.connectionLost = 0
        self.connect() 
        self.cleanup = False
        cleanupTimeout = int(self.conf.mainloop.cleanup)
        signal(SIGALRM, self.alarmHandler)
        alarm(cleanupTimeout)
        dbg = True #DBG
        try:
            while True:
                reconnectdelay = int(self.conf.mainloop.reconnectdelay)
                eventTimeout = float(self.conf.mainloop.timeout)
                cleanupTimeout = int(self.conf.mainloop.cleanup)
                try:
                    while self.todo:
                        callme = self.todo.pop()
                        method = callme[0]
                        args = callme[1:]
                        method(*args)
                    busy = self.stream.loop_iter(eventTimeout)
                    if not busy:
                        self.stats.busycount = 0
                        self.stream.idle()
                    else:
                        self.stats.busycount += 1
                    if self.cleanup:
                        self.roomCleanup()
                        if cleanupTimeout >= 10:
                            alarm(cleanupTimeout/10) # actual alarm timeout may be off by 10%
                        else:
                            alarm(cleanupTimeout)
                        self.cleanup = False
                        
                except (AttributeError, socket.error):
                    if not dbg:
                        dbg = True
                    else:
                        raise # DBG
                    self.__lost_connection = True
                    log.critical("Lost connection. Trying to reconnect every %d seconds" % reconnectdelay)
                    reconnectcount = 1
                    self.stats.connectionLost += 1
                    while True:
                        try:
                            sleep(reconnectdelay)
                            self.connect()
                        except socket.error:
                            reconnectcount += 1
                            log.notice("Tried to reconnect %d times. Trying again in %d seconds" % (reconnectcount, reconnectdelay))
                        else:
                            log.notice("Reconnected  after %d attempts" % reconnectcount)
                            break
            self.stats.mainloopcount += 1
            if self.stats.mainloopcount >= sys.maxint:
                self.stats.mainloopcount = 0
                self.stats.busycount = 0            
                
        except KeyboardInterrupt:
            log.notice("Keyboard interrupt. Exit...")
            self.closeRooms()
            self.disconnect()

    def session_started(self):
        JabberClient.session_started(self)
        if self.__lost_connection:
            self.__lost_connection = False
            oldroomstates = self.mucmanager.rooms.values()
            for roomstate in oldroomstates:
                roomstate.leave()
                self.mucmanager.forget(roomstate)
            self.stream.idle()
            self.todo.append((self.fillMucRoomPool,))
        else:
            error = self.set_mucRoomPoolSize(self.conf.muc.poolsize)
            if error: raise BotError(error)
            self.mucmanager = MucRoomManager(self.stream)
            self.mucmanager.set_handlers(1)
        self.todo.append((self.__rejoinRooms,))   # check DB for active room and rejoin/fix them.
        self.stream.set_message_handler("normal", self.handle_message)
        self.stream.set_presence_handler("subscribe", self.handle_presence_control)

    def getMucSettings(self, site):
        '''Return dict with global MUC-settings merged with site-specific MUC-settings'''
        if isinstance(site, str):
            sitename = site
        else:
            sitename = site.name
        settings = self.conf.muc.attr.copy()
        for siteconf in self.conf.muc:
            if siteconf.tag == "site" and siteconf.name == sitename:
                for k, v in siteconf.attr.iteritems():
                    v = v.strip()
                    if v:
                        settings[k] = v
        return settings            

    def fillMucRoomPool(self, site=None):
        '''Create MUC-rooms in the pool up to configured pool size
        
        Arguments:
        site - HelpIM Site object or string with the name of the site.
               If site is None, all pools will be filled.

        '''
        if site is None:
            # Resursively do all sites
            for name in self.sites.iterkeys():
                self.fillMucRoomPool(name)
            return
        if isinstance(site, str):
            sitename = site
        else:
            sitename = site.name
        site = self.sites[sitename]
        log.info("Refilling room pool for '%s'." % sitename)
        mucconf = self.getMucSettings(sitename)
        mucdomain = mucconf["domain"]
        poolsize = int(mucconf["poolsize"])
        # FIXME: only create rooms of the type(s) needed
        # create One2OneRooms
        nAvailable = len(site.rooms.getAvailable())
        nToCreate =  poolsize - nAvailable
        log.info("Pool size for site '%s' = %d.  Currently available rooms = %d." % (sitename, poolsize, nAvailable))
        log.info("Creating %d new rooms for site '%s'." % (nToCreate, sitename))
        for tmp in range(nToCreate):
            roomname = self.newRoomName(sitename)
            password = unicode(newHash())
            log.info("Creating MUC-room '%s@%s'." % (roomname, mucdomain))
            mucstate = self.joinMucRoom(site, JID(roomname, mucdomain), password, One2OneRoomHandler)
            if mucstate:
                mucstate.request_configuration_form()
        # create GroupRooms
        nAvailable = len(site.groupRooms.getAvailable())
        nToCreate =  poolsize - nAvailable
        log.info("Pool size for site '%s' = %d.  Currently available groupRooms = %d." % (sitename, poolsize, nAvailable))
        log.info("Creating %d new groupRooms for site '%s'." % (nToCreate, sitename))
        for tmp in range(nToCreate):
            roomname = self.newRoomName(sitename)
            password = unicode(newHash())
            log.info("Creating MUC-room for groupchat '%s@%s'." % (roomname, mucdomain))
            mucstate = self.joinMucRoom(site, JID(roomname, mucdomain), password, GroupRoomHandler)
            if mucstate:
                mucstate.request_configuration_form()

    def __rejoinRooms(self):
        '''Get all room from the database where the bot should be present as owner.
           This retakes control of the rooms that are still active according to
           that database.
           Also fixes the room statusses (where possible).
        '''
        for name, site in self.sites.iteritems():
            for room in site.rooms.getNotDestroyed():
                log.notice("Re-joining room '%s'." % room.jid)
                jid = str2roomjid(room.jid)
                mucstate = self.joinMucRoom(site, jid, room.password, One2OneRoomHandler, rejoining=True)
                # FIXME: check if we are owner of the room again (otherwise log error) & reconfigure room if locked
                if mucstate:
                    self.fixroomstatus(room, mucstate)
            for room in site.groupRooms.getNotDestroyed():
                log.notice("Re-joining groupRoom '%s'." % room.jid)
                jid = str2roomjid(room.jid)
                mucstate = self.joinMucRoom(site, jid, room.password, GroupRoomHandler, rejoining=True)
                # FIXME: check if we are owner of the room again (otherwise log error) & reconfigure room if locked
                if mucstate:
                    self.fixgrouproomstatus(room, mucstate)

    def fixroomstatus(self, room, mucstate): 
        # Wait until all events are processed
        # i.e. until all presence stanzas are received so we can count 
        # the number of users in the freshly re-joined rooms
        while self.stream.loop_iter(1):
            log.debug("Looping until all pending events are processed.")

        log.notice("Checking status for room '%s'." % room.jid)
        status = room.getStatus()
        log.notice("Status is '%s' for room '%s'." % (status, room.jid))
        client_id = room.client_id
        staff_id = room.staff_id
        userexited = room.getCleanExit()
        nUsers = len(mucstate.users) - 1 # -1 for not counting the bot itself
        log.info("There are %d users in '%s'." % (nUsers, room.jid))

        if status in ("available", "availableForInvitaton"):
            if staff_id:
                if client_id:
                    log.critical("BUG: a client was send to this room while status was still 'available' or 'availableForInvitation'. Room: '%s'." % room.jid)
                    room.setStatus("toDestroy")
                else:
                    if nUsers >= 2:
                        log.error("BUG: two users in the room while only staff was expected. Room: '%s'." % room.jid)
                        room.setStatus("toDestroy")
                    elif nUsers == 1:
                        log.notice("Fixing status to 'staffWaiting'. Room: '%s'." % room.jid)
                        room.setStatus("staffWaiting")
                    else: # nUsers == 0
                        log.notice("Expected staff member not present anymore: to be destroyed. Room: '%s'." % (room.jid))
                        room.setStatus("toDestroy")
            else:
                if client_id:
                    log.critical("BUG: a client was send to room while no staff ever was: to be destroyed. Room: '%s'." % room.jid)
                    room.setStatus("toDestroy")
                else:
                    log.info("Status is correct.")

        elif status in ("staffWaiting", "staffWaitingForInvitee"):
            if staff_id:
                if client_id:
                    if userexited:
                        if nUsers >= 2:
                            log.error("Two users in the room while at least one has exited cleanly: to be destroyed. Room: '%s'." % room.jid)
                            room.setStatus("toDestroy")
                        elif nUsers == 1:
                            log.notice("Fixing status to 'closingChat'. A user has exited cleanly. Room: '%s'." % room.jid)
                            room.setStatus("closingChat")
                        else: # nUsers == 0
                            log.notice("Both users seem to have left. At least one exited cleanly: to be destroyed Room: '%s'." % room.jid)
                            room.setStatus("toDestroy")
                    else:
                        if nUsers >= 2:
                            log.error("Fixing status to 'chatting'. Two users in the room now and client was send here. Room: '%s'." % room.jid)
                            room.setStatus("chatting")
                        elif nUsers == 1:
                            log.notice("Fixing status to 'lost'. One user is missing. Room: '%s'." % room.jid)
                            room.setStatus("lost")
                        else: # nUsers == 0
                            log.notice("Fixing status to 'abandoned'. Both users missing. Room: '%s'." % room.jid)
                            room.setStatus("abandoned")
                else: # no client_id
                    if nUsers >= 2:
                        log.error("BUG: two users in the room while only staff was expected. Room: '%s'." % room.jid)
                        room.setStatus("toDestroy")
                    elif nUsers == 1:
                        log.info("Status is correct.")
                    else: # nUsers == 0
                        log.notice("Expected staff member not present anymore: to be destroyed. Room: '%s'." % room.jid)
                        room.setStatus("toDestroy")
            else: # no staff_id
                log.critical("BUG: a staff member was never send here: to be destroyed. Room: '%s'." % room.jid)
                room.setStatus("toDestroy")

        elif status == "chatting":
            if staff_id:
                if client_id:
                    if userexited:
                        if nUsers >= 2:
                            log.error("Two users in the room while at least one has exited cleanly: to be destroyed. Room: '%s'." % room.jid)
                            room.setStatus("toDestroy")
                        elif nUsers == 1:
                            log.info("One user left cleanly. Fixing status to 'closingChat'.")
                            room.setStatus("closingChat")
                        else: # nUsers == 0
                            log.notice("Both users seem to have left. At least one exited cleanly: to be destroyed Room: '%s'." % room.jid)
                            room.setStatus("toDestroy")
                    else: # no clean exit
                        if nUsers >= 2:
                            log.info("Status is correct.")
                        elif nUsers == 1:
                            log.notice("Fixing status to 'lost'. One user is missing. Room: '%s'." % room.jid)
                            room.setStatus("lost")
                        else: # nUsers == 0
                            log.notice("Fixing status to 'abandoned'. Both users missing. Room: '%s'." % room.jid)
                            room.setStatus("abandoned")
                else: # no client_id
                    log.error("Status 'chatting' invalid since no client was ever send here. Room: '%s'." % room.jid)
                    room.setStatus("toDestroy")
            else: # no staff_id
                log.error("Status 'chatting' invalid since no staff member was ever send here. Room: '%s'." % room.jid)
                room.setStatus("toDestroy")

        elif status == 'closingChat':
            if nUsers >= 2:
                log.error("Two users in room while status was already 'closingChat'. Room: '%s'." % room.jid)
                room.setStatus("toDestroy")                
            elif nUsers == 1:
                log.info("Status is correct.")
            else: # nUsers == 0
                log.notice("No user left in room. To be destroyed. Room: '%s'." % room.jid)
                room.setStatus("abandoned")

        elif status == 'toDestroy':
            if nUsers >= 1:
                log.error("Unexpected users in room: '%s'."  % room.jid)
                room.setStatus("toDestroy")                
            else: # nUsers == 0
                log.info("Status correct.")

        elif status == 'lost':
            if userexited:
                if nUsers >= 2:
                    log.error("Unexpected user in room: '%s'."  % room.jid)
                    room.setStatus("toDestroy")
                elif nUsers == 1:
                    log.notice("Only one user in room. Closing this chat. Room: '%s'." % room.jid)
                    room.setStatus("closingChat")
                else: # nUsers == 0
                    log.notice("No user has returned. To be destroyed. Room: '%s'." % room.jid)
                    room.setStatus("toDestroy")
            else:
                if nUsers >= 2:
                    log.info("Both user returned to room. Fixing status to 'chatting'. Room: '%s'."  % room.jid)
                    room.setStatus("chatting")
                elif nUsers == 1:
                    log.notice("Status is correct.")
                    room.setStatus("lost")
                else: # nUsers == 0
                    log.notice("No user has returned. Fixing status to 'abandoned'. Room: '%s'." % room.jid)
                    room.setStatus("abandoned")

        elif status == 'abandoned':
            if userexited:
                if nUsers >= 2:
                    log.error("Unexpected users in room: '%s'."  % room.jid)
                    room.setStatus("toDestroy")
                elif nUsers == 1:
                    log.notice("Only one user returned. Closing this chat. Room: '%s'." % room.jid)
                    room.setStatus("closingChat")
                else: # nUsers == 0
                    log.notice("No user has returned. To be destroyed. Room: '%s'." % room.jid)
                    room.setStatus("toDestroy")
            else:
                if nUsers >= 2:
                    log.info("Both users returned to room. Fixing status to 'chatting'. Room: '%s'."  % room.jid)
                    room.setStatus("chatting")
                elif nUsers == 1:
                    log.notice("One user has returned to room. Fixing status to 'lost'. Room: '%s'."  % room.jid)
                    room.setStatus("lost")
                else: # nUsers == 0
                    log.notice("Status is correct.")
        # Finished fixing, set rejoinCount to None
        room.rejoinCount = None


    def fixgrouproomstatus(self, room, mucstate): 
        # Wait until all events are processed
        # i.e. until all presence stanzas are received so we can count 
        # the number of users in the freshly re-joined rooms
        while self.stream.loop_iter(1):
            log.debug("Looping until all pending events are processed.")

        log.notice("Checking status for group room '%s'." % room.jid)
        status = room.getStatus()
        log.notice("Status is '%s' for group room '%s'." % (status, room.jid))
        userexited = room.getCleanExit()
        chat_id = room.chat_id
        nUsers = len(mucstate.users) - 1 # -1 for not counting the bot itself
        log.info("There are %d users in '%s'." % (nUsers, room.jid))

        if status in ("available"):
            if chat_id: # room has been assigned to a chat in meanwhile
                if nUsers >= 1:
                    log.notice("Fixing status to 'chatting'. GroupRoom: '%s'." % room.jid)
                    room.setStatus("chatting")
                else: # nUsers == 0
                    log.notice("Expected users not present: mark as abandoned. GroupRoom: '%s'." % room.jid)
                    room.setStatus("abandoned")

        elif status == "chatting":
            if chat_id:
                if nUsers >= 1:
                    log.info("Status is correct.")
                else: # nUsers == 0
                    log.notice("Fixing status to 'abandoned'. All users missing. GroupRoom: '%s'." % room.jid)
                    room.setStatus("abandoned")
            else: # no chat_id
                log.error("Status 'chatting' invalid since no chat has been assigned. GroupRoom: '%s'." % room.jid)
                room.setStatus("toDestroy")

        elif status == 'abandoned':
                if nUsers >= 1:
                    log.info("User(s) returned to group room. Fixing status to 'chatting'. GroupRoom: '%s'."  % room.jid)
                    room.setStatus("chatting")
                else: # nUsers == 0
                    log.notice("Status is correct.")
        # Finished fixing, set rejoinCount to None
        room.rejoinCount = None


    def joinMucRoom(self, site, jid, password, handlerClass, rejoining=False):
        mucconf = self.getMucSettings(site.name)
        nick = mucconf["nick"].strip() or self.nick
        muchandler = handlerClass(self, site, mucconf, nick, password, rejoining)
        log.debug("MUC-room setting: history_maxchars=%s,  history_stanzas=%s, history_seconds=%s" % (
                mucconf["history_maxchars"],
                mucconf["history_maxstanzas"],
                mucconf["history_seconds"]
                ))
        try:
            mucstate = self.mucmanager.join(jid, nick, muchandler, password,
                                            mucconf["history_maxchars"],
                                            mucconf["history_maxstanzas"],
                                            mucconf["history_seconds"]
                                            )
            muchandler.assign_state(mucstate)
            return mucstate
        except RuntimeError, e:
            log.warning("Could not join room %s: %s" % (jid.as_string(), str(e)))
            return False

    def newRoomName(self, site):
        '''Return: new unpredictable and unique name for a MUC-room

           Argument:
           site - HelpIM Site object or string with the name of the site.
        '''
        if isinstance(site, str):
            sitename = site
        else:
            sitename = site.name
        basename = "%s_%s" % (sitename.strip(), newHash())
        if basename == self.__last_room_basename:
            self.__room_name_uniqifier += 1
        else:
            self.__room_name_uniqifier = 0
        self.__last_room_basename = basename
        newname = "%s.%d" % (basename, self.__room_name_uniqifier)
        return newname

    def kick(self, roomjid, nick):
        if isinstance(roomjid, str) or isinstance(roomjid, unicode):
            roomjid = str2roomjid(roomjid)
        log.info("Kicking user with nick '%s'." % nick)
        kickIq = MucIq(to_jid=roomjid, stanza_type="set")
        kickIq.make_kick_request(nick, reason=None)
        self.stream.send(kickIq)

    def makeModerator(self, roomjid, nick):
        if isinstance(roomjid, str) or isinstance(roomjid, unicode):
            roomjid = str2roomjid(roomjid)
        log.info("Making user with nick '%s' moderator." % nick)

        xml = "<iq to='%s' type='set' id='mod'><query xmlns='http://jabber.org/protocol/muc#admin'><item role='moderator' nick='%s'/></query></iq>" % (roomjid, nick)
        log.debug(xml)
        self.stream.write_raw(xml)
        
    def closeRooms(self, roomstatus=None, site=None):
        if site is None:
            # Resursively do all sites
            for name in self.sites.iterkeys():
                self.closeRooms(roomstatus, name)
            return
        if isinstance(site, str):
            sitename = site
        else:
            sitename = site.name
        site = self.sites[sitename]

        if roomstatus is None:
            rooms = site.rooms.getNotDestroyed() + site.groupRooms.getNotDestroyed()
        else:
            rooms = site.rooms.getByStatus(roomstatus) + site.groupRooms.getByStatus(roomstatus)
        for room in rooms:
            self.closeRoom(room)

    def closeRoom(self, room):
        roomjid = str2roomjid(room.jid)
        log.info("Closing down MUC-room '%s'." % room.jid)
        roomstate = self.mucmanager.rooms[unicode(roomjid)]
        roomstate.handler.closingDown = True
        mynick = roomstate.get_nick()
        for nick in roomstate.users.iterkeys():
            if nick != mynick:
                self.kick(roomjid, nick)
        log.info("Leaving MUC-room '%s'." % room.jid)
        room.destroyed()
        roomstate.leave()

    # Configuration access methods
    #

    # set_... methods: Methods to change settings that can be changed at runtime.
    #
    # Note: The set_... methods below return empty string on success, and error-string on failure.
    #       So using the returned value in conditional expressions may opposite of what you might expect.
    #
    def set_mucRoomPoolSize(self, newSize, site=None):
        '''Sets number of available rooms --> empty string on success. error-string on failure.

        If lowering number of rooms at runtime, the extra rooms in the pool will
        not be not be lowered immediately. The number of available rooms will decrease
        to the new pool size as rooms are taken into use.

        Arguments:
        newSize - string or int representing number of rooms to keep available for use. if newSize
                  is a string, it will be converted to an integer. 

        '''
        try:
            poolsize = int(self.conf.muc.poolsize)
        except ValueError:
            return "MUC-room pool size invalid"
        self.conf.muc['poolsize'] = newSize
        log.info("MUC-room pool size set to %s" % newSize)
        self.todo.append((self.fillMucRoomPool,))
        return str()

    # XMPP handler methods
    #
    def handle_message(self, s):
        if s.get_type() == "headline":
            return True
        log.stanza(s)
        message = u"Don't call us. We call you.."
        msg = Message(None, s.get_to(), s.get_from(), s.get_type(), None, None, message)
        self.stream.send(msg)
        self.printrooms()
        # for k,v in self.sites.iteritems():
        #     print 'Available for:', k
        #     for r in v.rooms.getAvailable():
        #         print r.jid

        #self.todo.append((self.closeRooms, None, 'Sensoor'))
        return True

    def printrooms(self):    #DBG:
        print "Rooms:"
        for r,v in self.mucmanager.rooms.iteritems():
            print r, "joined:", v.joined, "configured:", v.configured
            for u in v.users:
                print "users:", u

    def handle_presence_control(self, s):
        print "Incoming presence control request:", s.get_type()
        if s.get_type() == 'subscribe':
            self.stream.send(s.make_accept_response())
            return True
        # Ignore other requests
        return True


# ------  #

defaults = '''
<helpim>
    <bot>
        <mainloop 
            timeout="1"
            reconnectdelay="5"
            cleanup="600"
            >
        </mainloop>

        <connection
            username=""
            domain=""
            resource=""
            password=""
            nick=""
            port="5222"
            >
        </connection>

        <muc
            domain="muc.localhost"
            poolsize="1"
            nick="HelpIM3"
            whoisaccess="moderators"
            allowchangesubject="yes"
            history_maxchars="2000"
            history_maxstanzas="10"
            history_seconds="120"
            >
            <site name=""
                domain=""
                poolsize=""
                nick=""
                whoisaccess=""
                allowchangesubject=""
                history_maxchars=""
                history_maxstanzas=""
                history_seconds=""
                >
            </site>
        </muc>
    </bot>
    <logging
        level="notice"
        level_pyxmpp="warning"
        destination="stderr"
        >
    </logging>
</helpim>
'''


### MAIN ###

if __name__ == "__main__":
    # Load configuration from xml file and command line arguments
    #
    username = ''
    nick = ''
    password = ''
    domain = ''
    port=''
    resource = ''
    muc_domain = ''
    room_pool_size = ''
    log_level = ''
    log_level_pyxmpp = ''
    log_destination = ''

    try:
        opts, args = getopt.getopt(sys.argv[1:], "hu:n:p:d:r:P:m:s:l:y:t:",
                                   ["help",
                                    "username=",
                                    "nick="
                                    "password=",
                                    "domain=",
                                    "resource=",
                                    "port=",
                                    "muc-domain=",
                                    "room-pool-size=",
                                    "log-level=",
                                    "log-level-pyxmpp=",
                                    "log-destination="
                                    ])
    except getopt.GetoptError:
        sys.stdout = sys.stderr
        print __doc__
        sys.stdout = sys.__stdout__
        sys.exit(2)
        
    for opt, arg in opts:
        if opt in ('-h', '--help'):
            print __doc__
            sys.exit()
        elif opt in ("-u", "--username"):
            username = arg
        elif opt in ("-n", "--nick"):
            nick = arg
        elif opt in ("-p", "--password"):
            password = arg
        elif opt in ("-d", "--domain"):
            domain = arg
        elif opt in ("-r", "--resource"):
            resource = arg
        elif opt in ("-P", "--port"):
            port = arg
        elif opt in ("-m", "--muc-domain"):
            muc_domain = arg
        elif opt in ("-s", "--room-pool-size"):
            room_pool_size = arg
        elif opt in ("-l", "--log-level"):
            log_level = arg
        elif opt in ("-y", "--log-level-pyxmpp"):
            log_level_pyxmpp = arg
        elif opt in ("-t", "--log-destination"):
            log_destination = arg
            
    if len(args) == 1:
        conf = Config(StringIO(defaults), args[0])
    elif len(args) > 1:
        sys.stdout = sys.stderr
        print __doc__
        print "==> error: only one non-option argument supported."
        sys.stdout = sys.__stdout__
        sys.exit(2)
    else:        
        conf = Config(StringIO(defaults), botConfig)

    if username:         conf.bot.connection["username"] = username
    if nick:             conf.bot.connection["nick"]     = nick
    if password:         conf.bot.connection["password"] = password
    if domain:           conf.bot.connection["domain"]   = domain
    if resource:         conf.bot.connection["resource"] = resource
    if port:             conf.bot.connection["port"]     = port
    if muc_domain:       conf.bot.muc["domain"]          = muc_domain
    if room_pool_size:   conf.bot.muc["poolsize"]        = room_pool_size
    if log_level:        conf.logging["level"]           = log_level
    if log_level_pyxmpp: conf.logging["level_pyxmpp"]    = log_level_pyxmpp
    if log_destination:  conf.logging["destination"]     = log_destination

    # Initialize logging:
    #
    log = Log()
    error = log.set_Destination(conf.logging.destination)
    if error:
        raise LogError(error)
    error = log.set_Level(conf.logging.level)
    if error:
        raise LogError(error)
    error = log.set_Level(conf.logging.level_pyxmpp, 'pyxmpp')
    if error:
        raise LogError(error)
    logger = log
    services.log = log

    # Create and start a bot instance
    #
    bot = Bot(conf.bot)
    try:
        bot.run()
    except:
        log.critical("Unhandled exception, stopping chatbot")
        trace = traceback.format_exc()
        for line in trace.split("\n"):
            log.critical(line.strip())
        