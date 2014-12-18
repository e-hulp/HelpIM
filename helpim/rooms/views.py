from hashlib import md5

from django.utils.simplejson import dumps
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.shortcuts import render_to_response
from django.template.loader import render_to_string
from django.http import HttpResponse

from helpim.rooms.models import AccessToken, Participant, IPBlockedException, One2OneRoom, WaitingRoom, LobbyRoom
from helpim.common.models import AdditionalUserInformation

@login_required
def get_staff_muc_nick(request):
    # for staff the muc_nick will be determined either by the settings
    # in additional profile information (chat_nick property) by a site
    # wide default or as a fallback by the username of the user object
    muc_nick = None
    try:
        if request.user.get_profile().chat_nick != '':
            muc_nick = request.user.get_profile().chat_nick
    except AdditionalUserInformation.DoesNotExist:
        pass

    if muc_nick is None or muc_nick == '':
        try:
            muc_nick = settings.CHAT['staff_muc_nick']
        except:
            pass
            
    if muc_nick is None or muc_nick == '':
        muc_nick = request.user.username

    return muc_nick

@login_required
def staff_join_chat(request, room_pk=None):
    muc_nick = get_staff_muc_nick(request)

    lobby_nick = request.user.get_full_name()
    if lobby_nick == '':
        lobby_nick = request.user.username

    return join_chat(
        request,
        dict({
            'lobby_nick': lobby_nick,
            'muc_nick': muc_nick,
            'logout_redirect': request.META.get('HTTP_REFERER') or request.build_absolute_uri('/admin/'),
            'conversation_redirect': request.build_absolute_uri('/admin/conversations/conversation/'),
            'no_block': not request.user.has_perm('conversations.change_blockedparticipant')
            }),
        Participant.ROLE_STAFF,
        request.user
        )

def client_join_chat(request):
    return join_chat(
        request,
        dict({
                'logout_redirect': '/logged_out/',
                'unavailable_redirect': '/unavailable/',
                })
        )

def join_chat(request, cfg, role=Participant.ROLE_CLIENT, user=None):
    try:
        token = AccessToken.objects.get_or_create(token=request.COOKIES.get('room_token'), role=role, ip_hash=md5(request.META.get('REMOTE_ADDR')).hexdigest(), created_by=user)

        return render_to_response(
            'rooms/join_chat.html', {
                'debug': settings.DEBUG,
                'is_staff': role is Participant.ROLE_STAFF,
                'is_one2one': True,
                'xmpptk_config': dumps(dict({
                            'logout_redirect': request.META.get('HTTP_REFERER'),
                            'bot_jid': '%s@%s/%s' % (settings.BOT['connection']['username'],
                                                     settings.BOT['connection']['domain'],
                                                     settings.BOT['connection']['resource']),
                            'bot_nick': settings.BOT['muc']['nick'],
                            'static_url': settings.STATIC_URL,
                            'emoticons_path' : settings.CHAT['emoticons_path'],
                            'is_staff': role is Participant.ROLE_STAFF,
                            'token': token.token,
                            }.items() + settings.CHAT.items() + cfg.items()), indent=2)
                })
    except IPBlockedException:
        return render_to_response('rooms/blocked.html')

def room_status(request):
    response = HttpResponse(content_type='text/xml')
    items = {}
    items['open'] = (len(LobbyRoom.objects.filter(status='chatting')) > 0)
    items['waiting'] = len(WaitingRoom.objects.filter(status='chatting'))
    items['chatting'] = len(One2OneRoom.objects.filter(status='chatting'))
    response.write(render_to_string('rooms/status.xml', {'items': items}))
    return response
