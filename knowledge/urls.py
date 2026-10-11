from django.urls import path

from . import views

app_name = "knowledge"

urlpatterns = [
    path("conhecimento/", views.index, name="index"),
    path("conhecimento/<uuid:pk>/arquivo/", views.download, name="download"),
    path("api/knowledge/", views.upload, name="upload"),
    path("api/knowledge/search/", views.search_api, name="search"),
    path("api/knowledge/<uuid:pk>/", views.document, name="document"),
]
