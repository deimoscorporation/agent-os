from django.urls import path

from . import views

app_name = "marketplace"

urlpatterns = [
    path("marketplace/", views.catalog, name="catalog"),
    path("marketplace/revisao/", views.review_queue, name="review_queue"),
    path("marketplace/<uuid:pk>/", views.item_detail, name="item_detail"),
    path("workspace/", views.workspace, name="workspace"),
    path("workspace/new/<str:kind>/", views.item_new, name="item_new"),
    path("workspace/items/<uuid:pk>/", views.item_edit, name="item_edit"),

    path("api/marketplace/<uuid:pk>/install/", views.install, name="install"),
    path("api/marketplace/<uuid:pk>/uninstall/", views.uninstall, name="uninstall"),
    path("api/marketplace/<uuid:pk>/review/", views.review, name="review"),
    path("api/workspace/items/<uuid:pk>/", views.installation_update, name="installation_update"),
    path("api/workspace/items/<uuid:pk>/upgrade/", views.installation_upgrade, name="installation_upgrade"),
    path("api/workspace/tools/", views.workspace_tools, name="workspace_tools"),
    path("api/workspace/agents/", views.workspace_agents, name="workspace_agents"),
    path("api/tools/generate/", views.tool_generate, name="tool_generate"),
    path("api/items/", views.item_create, name="item_create"),
    path("api/items/draft/", views.item_draft, name="item_draft"),
    path("api/items/validate/", views.item_validate, name="item_validate"),
    path("api/items/<uuid:pk>/", views.item_api, name="item_api"),
    path("api/items/<uuid:pk>/share/", views.item_share, name="item_share"),
    path("api/items/<uuid:pk>/submit/", views.item_submit, name="item_submit"),
    path("api/review/<int:pk>/", views.review_decide, name="review_decide"),
]
