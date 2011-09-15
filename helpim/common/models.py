from django.contrib.auth.models import User
from django.db import models
from django.utils.translation import ugettext as _

class BranchOffice(models.Model):

    name = models.CharField(max_length=50)

    def __unicode__(self):
        return self.name

    class Meta:
        verbose_name = _("Branch office")
        verbose_name_plural = _("Branch offices")

        permissions = (
            # Default: Can only view own conversations
            ("view_conversations_of_own_branch_office",
                _("View all conversations in own branch office")),

            ("view_conversations_of_all_branch_offices",
                _("View all conversations in all branch offices")),
        )

class AdditionalUserInformation(models.Model):
    def __unicode__(self):
        return _("Additional information about user %s" % self.user.username)

    class Meta:
        verbose_name = _("Additional user information")
        verbose_name_plural = _("Additional user information")

    user = models.OneToOneField(User, unique=True)

    branch_office = models.ForeignKey(BranchOffice, null=False)

