from django.urls import path

from . import views

app_name = "workspaces"

urlpatterns = [
    path("workspaces/switch/", views.switch, name="switch"),
    path("equipe/", views.team, name="team"),
    path("segredos/", views.secrets, name="secrets"),

    path("api/organizations/", views.organization_create, name="organization_create"),
    path("api/organization/", views.organization, name="organization"),
    path("api/organization/members/", views.member_add, name="member_add"),
    path("api/organization/members/<int:pk>/", views.member, name="member"),
    path("api/organization/invitations/<int:pk>/", views.invitation, name="invitation"),
    path("api/secrets/", views.secret_set, name="secret_set"),
    path("api/secrets/<str:name>/", views.secret_delete, name="secret_delete"),
]
