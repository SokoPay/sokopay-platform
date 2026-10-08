from django.urls import path

from . import views

app_name = "ussd"

urlpatterns = [path("ussd/callback", views.callback, name="callback")]
