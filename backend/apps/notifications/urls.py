from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("devices", views.DeviceView.as_view(), name="devices"),
    path("devices/<str:token>", views.DeviceDeleteView.as_view(), name="device-delete"),
    path("notifications", views.InboxView.as_view(), name="inbox"),
    path("notifications/<uuid:pk>/read", views.MarkReadView.as_view(), name="read"),
]
