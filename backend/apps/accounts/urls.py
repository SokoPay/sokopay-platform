from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("auth/otp/request", views.OtpRequestView.as_view(), name="otp-request"),
    path("auth/otp/verify", views.OtpVerifyView.as_view(), name="otp-verify"),
    path("auth/pin/set", views.PinSetView.as_view(), name="pin-set"),
    path("auth/pin/change", views.PinChangeView.as_view(), name="pin-change"),
    path("auth/pin/forgot", views.PinForgotView.as_view(), name="pin-forgot"),
    path("auth/pin/reset", views.PinResetView.as_view(), name="pin-reset"),
    path("auth/profile", views.ProfileView.as_view(), name="profile"),
    path("auth/close", views.CloseAccountView.as_view(), name="close"),
    path("auth/login", views.PinLoginView.as_view(), name="login"),
    path("auth/refresh", views.RefreshView.as_view(), name="refresh"),
    path("auth/logout", views.LogoutView.as_view(), name="logout"),
    path("auth/logout-all", views.LogoutAllView.as_view(), name="logout-all"),
    path("auth/me", views.MeView.as_view(), name="me"),
]
