from django.urls import path

from . import views

app_name = "kyc"

urlpatterns = [
    path("kyc", views.KycView.as_view(), name="kyc"),
    path("kyc/upgrade", views.KycUpgradeView.as_view(), name="upgrade"),
    path("kyc/upgrade/selfie", views.KycSelfieView.as_view(), name="upgrade-selfie"),
]
