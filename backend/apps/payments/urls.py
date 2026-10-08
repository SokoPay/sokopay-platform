from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("billers", views.BillerListView.as_view(), name="billers"),
    path("billers/<str:code>/lookup", views.BillerLookupView.as_view(), name="biller-lookup"),
    path("telcos/<str:telco>/bundles", views.BundleListView.as_view(), name="bundles"),
    # Fixed paths must come before payments/<reference>, which matches anything.
    path("payments/bill", views.BillPaymentView.as_view(), name="bill"),
    path("payments/airtime", views.AirtimeView.as_view(), name="airtime"),
    path("payments/data", views.DataBundleView.as_view(), name="data"),
    path("payments/<str:reference>", views.PaymentStatusView.as_view(), name="status"),
    # Inbound rail callbacks, e.g. /api/v1/rails/mock/webhook
    path("rails/<str:provider>/webhook", views.RailWebhookView.as_view(), name="rail-webhook"),
]
