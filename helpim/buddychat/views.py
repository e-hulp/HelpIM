from datetime import datetime
from django import forms
from django.conf import settings
from django.contrib import messages, admin
from django.contrib.auth.decorators import login_required, permission_required, user_passes_test
from django.contrib.auth.models import User
from django.contrib.sites.models import get_current_site
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect, HttpResponse
from django.shortcuts import render_to_response, get_object_or_404
from django.template import RequestContext
from django.utils.simplejson import dumps
from django.utils.translation import ugettext as _

from registration.models import RegistrationProfile
from hashlib import md5

from helpim.buddychat.models import BuddyChatProfile, QuestionnaireFormEntry
from helpim.buddychat.forms import BuddyForm
from helpim.rooms.models import AccessToken, SimpleRoomToken, Participant, SimpleRoom
from helpim.rooms.views import get_staff_muc_nick
from helpim.questionnaire.models import Questionnaire

class ConvMessageForm(forms.Form):
    body = forms.CharField(max_length=4096, widget=forms.Textarea)

class CareworkersForm(forms.Form):
    choices = [('', _('None'))]
    careworkers = User.objects.filter(groups__name='careworkers')
    for user in careworkers:
        choices.append((user.pk, user.username))
    careworker = forms.ChoiceField(choices=choices, required=False)

def welcome(request):
    if request.user.is_authenticated():
        if request.user.is_staff:
            """ staff doesn't have a profile - redirect to admin """
            return HttpResponseRedirect(reverse('admin:index'))
        else:
            return HttpResponseRedirect(reverse('buddychat_profile', args=[request.user]))
    else:
        return HttpResponseRedirect(reverse('auth_login'))

@login_required(login_url='/login/')
def profile(request, username):
    client = get_object_or_404(BuddyChatProfile, user__username=username)
    params = {}

    if request.user == client.user:
        # decide if client must take CR questionnaire and redirect if necessary
        q = client.needs_questionnaire_CR()
        if not q is None:
            return HttpResponseRedirect(reverse('helpim.questionnaire.views.form_detail', args=[q.slug, client.id]))

        q = client.needs_questionnaire_recurring('CX')[0]
        if not q is None:
            params['recurring_questionnaire_url'] = reverse('helpim.questionnaire.views.form_detail', args=[q.slug, client.id])

        # mark messages for careseeker as read
        for msg in client.unread_messages_careseeker():
            msg.read = True
            msg.save()

    if request.user.has_perm('buddychat.is_coordinator') or (request.user.has_perm('buddychat.is_careworker') and request.user == client.careworker) or request.user == client.user:
        """ we need to make sure requesting user is either
        * the careseeker himself (aka the 'client')
        * the careworker associated with this client
        * a coordinator
        """
        if request.method == "POST":
            form = ConvMessageForm(request.POST)
            if form.is_valid():
                """
                POST var 'conv' decides which conversation we're acting on
                """
                conv = {
                    'careworker': client.careworker_conversation,
                    'coordinator': client.coordinator_conversation,
                    'careworker_coordinator': client.careworker_coordinator_conversation
                    }[request.POST['conv']]

                """
                check whether user is allowed to act on this conversation according to this rules
                * careseeker allowed to post to careworker and coordinator
                * careworker allowed to post to careworker and careworker_coordinator
                * coordinator allowed to post to coordinator and careworker_coordinator
                and set rcpt for email notification
                """
                rcpt = None # rcpt is None for coordinators
                if conv is client.careworker_conversation:
                    if not client.careworker:
                        return HttpResponse(_('Access Denied'))
                    elif request.user == client.user:
                        rcpt = client.careworker
                    elif request.user == client.careworker:
                        rcpt = client.user
                    else:
                        return HttpResponse(_('Access Denied'))
                elif conv is client.coordinator_conversation:
                    if request.user.has_perm('buddychat.is_coordinator'):
                        rcpt = client.user
                    elif request.user != client.user:
                        return HttpResponse(_('Access Denied'))
                elif conv is client.careworker_coordinator_conversation:
                    if request.user.has_perm('buddychat.is_coordinator'):
                        rcpt = client.careworker
                    elif request.user != client.careworker:
                        return HttpResponse(_('Access Denied'))

                conv.messages.create(
                    body = form.cleaned_data['body'],
                    sender = conv.get_or_create_participant(request.user),
                    sender_name = request.user.username,
                    created_at = datetime.now()
                    )
                """
                send email
                """
                site = get_current_site(request)

                subject = (_('There is a message at %s') % site)
                body = (_('There is a message for you at https://%(site)s/\n\nYou can read this message, with the username and password you have\nchosen while creating your account.\n\nNote: don\'t react by replying to this e-mail, but by logging in on:\nhttps://%(site)s/') %
                         {'sender' : request.user.username,
                          'client' : client.user.username,
                          'message': form.cleaned_data['body'],
                          'site'   : site
                          }
                    )

                if not rcpt is None:
                    rcpt.email_user(subject, body)
                else:
                    coordinators = User.objects.filter(groups__name='coordinators')
                    for user in coordinators:
                        user.email_user(subject, body)

                messages.success(request, _('Your message has been sent'))
                form = ConvMessageForm() # reset form
        else:
            form = ConvMessageForm()

        params['client'] = client
        params['form'] = form
        params['questionnaire_history'] = QuestionnaireFormEntry.objects.for_profile_and_user(client, request.user)

        # a coordinator accessing this profile
        if request.user.has_perm('buddychat.is_coordinator'):
            if client.careworker:
                params['careworkers_form'] = CareworkersForm(initial={'careworker': client.careworker.pk})
            else:
                params['careworkers_form'] = CareworkersForm()
                
            # mark messages for coordinator as read
            for msg in client.unread_messages_coordinator():
                msg.read = True
                msg.save()

        # it's the careworker assigned to this profile
        if request.user.has_perm('buddychat.is_careworker') and request.user == client.careworker:
            # check if SX questionnaire is necessary
            q = client.needs_questionnaire_recurring('SX')[0]
            if not q is None:
                params['recurring_questionnaire_url'] = reverse('helpim.questionnaire.views.form_detail', args=[q.slug, client.id])
            
            # mark messages for careworker as read
            for msg in client.unread_messages_careworker():
                msg.read = True
                msg.save()
        
        return render_to_response(
            'buddychat/profile.html',
            params,
            context_instance=RequestContext(request)
            )
    else:
        return HttpResponse(_('Access Denied'))

@permission_required('buddychat.is_coordinator')
def set_cw(request, username):
    """ set a careworker """
    client = get_object_or_404(BuddyChatProfile, user__username = username)
    if request.method == "POST":
        form = CareworkersForm(request.POST)
        if form.is_valid():
            try:
                careworker = User.objects.get(pk=form.cleaned_data['careworker'])
            except ValueError:
                careworker = None
            except User.DoesNotExist:
                careworker = None
                messages.info(request, _('Careworker not found'))
            if client.careworker != careworker:
                client.careworker = careworker
                if not client.careworker is None:
                    client.coupled_at = datetime.now()
                    messages.success(request, _('Careworker has been set'))
                else:
                    client.coupled_at = None
                    messages.success(request, _('Careworker has been unset'))
                client.save()

    return HttpResponseRedirect(reverse('buddychat_profile', args=[username]))

@login_required(login_url='/login/')
def join_chat(request, username):
    client = get_object_or_404(BuddyChatProfile, user__username = username)

    if request.user != client.user and request.user != client.careworker:
        """ only careworkers and the client itself are allowed to access the chat """
        return HttpResponse(_('Access Denied'))
        
    is_staff = client.careworker == request.user
    if is_staff:
        role = Participant.ROLE_STAFF
        muc_nick = get_staff_muc_nick(request)
    else:
        role = Participant.ROLE_CLIENT
        muc_nick = request.user.username

    ac = AccessToken.objects.get_or_create(token=request.COOKIES.get('room_token'), role=role, ip_hash=md5(request.META.get('REMOTE_ADDR')).hexdigest(), created_by=request.user)
    try:
        if client.room is None or client.room.getStatus() not in ('waiting', 'lost'):
            client.room = SimpleRoom.objects.filter(status='available')[0]
            client.save()
    except SimpleRoom.DoesNotExist:
        """ this is meant to happen when we got a stale reference at
        client.room which points to a deleted room. this could also
        happen if there is no room available. we will check afterwards
        and redirect to some error page."""
        try:
            client.room = SimpleRoom.objects.filter(status='available')[0]
            client.save()
        except SimpleRoom.DoesNotExist:
            """ well there's really no room, probably because the bot isn't running """
            return HttpResponse(_('Service Unavailable'))

    # delete old SimpleRoomTokens
    SimpleRoomToken.objects.filter(token=ac).all().delete()

    # store room with SimpleRoomToken
    SimpleRoomToken.objects.get_or_create(token = ac, room=client.room)
    
    return render_to_response(
        'rooms/join_chat.html', {
            'debug': settings.DEBUG,
            'is_staff': is_staff,
            'is_one2one': True,
            'xmpptk_config': dumps(dict({
                'logout_redirect': request.META.get('HTTP_REFERER'),
                'bot_jid': '%s@%s/%s' % (settings.BOT['connection']['username'],
                                         settings.BOT['connection']['domain'],
                                         settings.BOT['connection']['resource']),
                'bot_nick': settings.BOT['muc']['nick'],
                'static_url': settings.STATIC_URL,
                'is_staff':is_staff,
                'token': ac.token,
                'muc_nick': muc_nick,
                'mode': 'light',
                'disable_blocking': True,
                }.items() + settings.CHAT.items()), indent=2)
            })

@user_passes_test(lambda u: u.has_perm('buddychat.is_careworker') or u.has_perm('buddychat.is_coordinator'))
def chatbuddies(request):
    if request.user.has_perm('buddychat.is_coordinator'):
        chatbuddies = BuddyChatProfile.objects.order_by('careworker__username', 'ready')
    else:
        chatbuddies = BuddyChatProfile.objects.filter(careworker=request.user).order_by('user__username')

    return render_to_response(
        'buddychat/chatbuddies.html',
        { 'chatbuddies': chatbuddies },
        context_instance=RequestContext(request)
    )

@permission_required('buddychat.add_buddychatprofile', '/admin')
def add_chatbuddy(request):
    '''display form to add a user, bypasing the normal buddy registration process'''

    if request.method == 'POST':
        form = BuddyForm(request.POST)
    else:
        form = BuddyForm()

    if form.is_valid():
        buddy_user = User.objects.create_user(
            form.cleaned_data['username'],
            form.cleaned_data['email'],
            form.cleaned_data['password1'])
        buddy_profile = BuddyChatProfile.objects.create(buddy_user, RegistrationProfile.ACTIVATED)
        if not form.cleaned_data.get("presentForm"):
            qCR, created = Questionnaire.objects.get_or_create(position='CR')
            buddy_profile.ready = True
        buddy_profile.save()
        messages.success(request, _('Buddy has been added'))
        if '_addanother' in request.POST:
            form = BuddyForm()
        else:
            # return to base
            return HttpResponseRedirect(reverse("chatbuddies_list"))

    fieldsets = [(None, {'fields': form.base_fields.keys()})]
    adminForm = admin.helpers.AdminForm(form, fieldsets, {})
    return render_to_response("buddychat/add_form.html", {
            "adminform": adminForm,
            "is_popup": "_popup" in request.REQUEST,
            "change": False,
            "add": True,
            "save_as": False,
            "has_delete_permission": False,
            "has_add_permission": True,
            "has_change_permission": False,
            "opts": User._meta,
            "root_path": "/admin/chatbuddies/",
        },
        context_instance=RequestContext(request))

