from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    path("", views.index, name="index"),
    path("c/<uuid:pk>/", views.index, name="conversation"),
    path("c/<uuid:pk>/export.<str:fmt>", views.export, name="export"),
    path("s/<str:token>/", views.shared, name="shared"),
    path("attachments/<uuid:pk>/", views.attachment, name="attachment"),
    path("api/conversations/", views.conversation_list, name="conversation_list"),
    path("api/conversations/<uuid:pk>/", views.conversation_detail, name="conversation_detail"),
    path("api/conversations/<uuid:pk>/regenerate/", views.regenerate, name="regenerate"),
    path("api/conversations/<uuid:pk>/share/", views.share, name="share"),
    path("api/attachments/", views.attachment_upload, name="attachment_upload"),
    path("api/chat/", views.send_message, name="send_message"),
]
