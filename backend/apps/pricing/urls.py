from django.urls import path

from . import views

app_name = "pricing"

urlpatterns = [
    path("fees/quote", views.FeeQuoteView.as_view(), name="fee-quote"),
]
