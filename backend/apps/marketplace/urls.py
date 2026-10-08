from django.urls import path

from . import views

app_name = "marketplace"

urlpatterns = [
    path("marketplace/products", views.ProductListView.as_view(), name="products"),
    path("marketplace/apply", views.ApplyView.as_view(), name="apply"),
    path("marketplace/applications", views.ApplicationListView.as_view(), name="applications"),
    path("marketplace/applications/<str:reference>/contribute", views.ContributeView.as_view(), name="contribute"),
    path("marketplace/applications/<str:reference>/withdraw", views.WithdrawView.as_view(), name="withdraw"),
    path("marketplace/applications/<str:reference>/pay-premium", views.PayPremiumView.as_view(),
         name="pay-premium"),
]
