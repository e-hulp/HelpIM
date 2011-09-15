from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from helpim.common.models import AdditionalUserInformation, BranchOffice

admin.site.unregister(User)

class AdditionalUserInformationInline(admin.StackedInline):
    model = AdditionalUserInformation

class AdditionalUserInformationAdmin(UserAdmin):
    inlines = [AdditionalUserInformationInline]

admin.site.register(User, AdditionalUserInformationAdmin)

admin.site.register(BranchOffice)

