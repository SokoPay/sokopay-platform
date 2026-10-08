from django.urls import path

from . import app_views, dispute_views, hosted, views

app_name = "merchants"

urlpatterns = [
    # Merchant mobile app (team members, JWT) — see app_views.py
    path("merchant-app/me", app_views.MeView.as_view(), name="app-me"),
    path("merchant-app/qr", app_views.StaticQrView.as_view(), name="app-qr"),
    path("merchant-app/payment-requests", app_views.PaymentRequestsView.as_view(), name="app-requests"),
    path("merchant-app/payment-requests/<str:token>", app_views.PaymentRequestDetailView.as_view(),
         name="app-request"),
    path("merchant-app/payment-requests/<str:token>/cancel",
         app_views.PaymentRequestCancelView.as_view(), name="app-request-cancel"),
    path("merchant-app/payments", app_views.PaymentsView.as_view(), name="app-payments"),
    path("merchant-app/payments/<str:reference>", app_views.PaymentDetailView.as_view(),
         name="app-payment"),
    path("merchant-app/payments/<str:reference>/refund", dispute_views.MerchantRefundView.as_view(),
         name="app-refund"),
    path("merchant-app/disputes", dispute_views.MerchantDisputesView.as_view(), name="app-disputes"),
    path("merchant-app/disputes/<uuid:pk>/respond", dispute_views.MerchantDisputeRespondView.as_view(),
         name="app-dispute-respond"),
    # Customer disputes (normal app JWT)
    path("disputes", dispute_views.CustomerDisputesView.as_view(), name="disputes"),
    path("disputes/<uuid:pk>/withdraw", dispute_views.CustomerDisputeWithdrawView.as_view(),
         name="dispute-withdraw"),
    path("merchant-app/settlements", app_views.SettlementsView.as_view(), name="app-settlements"),
    path("merchant-app/settlement-accounts", app_views.SettlementAccountsView.as_view(),
         name="app-settlement-accounts"),

    # Server-to-server merchant API (API keys)
    path("merchant/charges", views.MerchantChargeView.as_view(), name="charge"),
    path("merchant/checkout-sessions", hosted.CheckoutSessionView.as_view(), name="checkout-session"),
    path("merchant/charges/<str:reference>", views.MerchantChargeStatusView.as_view(),
         name="charge-status"),
]
