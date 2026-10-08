from django.urls import path

from . import cross_border_views, lifestyle, saved, views

app_name = "wallet"

urlpatterns = [
    path("wallet", views.WalletView.as_view(), name="wallet"),
    path("wallet/fund", views.WalletFundView.as_view(), name="fund"),
    path("wallet/send/lookup", views.WalletSendLookupView.as_view(), name="send-lookup"),
    path("wallet/send", views.WalletSendView.as_view(), name="send"),              # SokoPay → SokoPay
    path("wallet/transfer/lookup", views.TransferLookupView.as_view(), name="transfer-lookup"),
    path("wallet/transfer", views.ExternalTransferView.as_view(), name="transfer"),  # → other institutions
    path("remittance/<str:partner>/webhook", views.RemittanceWebhookView.as_view(),
         name="remittance-webhook"),
    # Scan-to-pay
    path("pay/resolve", views.PayResolveView.as_view(), name="pay-resolve"),
    path("wallet/pay-merchant", views.PayMerchantView.as_view(), name="pay-merchant"),
    path("wallet/saved", saved.SavedRecipientsView.as_view(), name="saved"),
    path("wallet/saved/<int:pk>", saved.SavedRecipientDetailView.as_view(), name="saved-detail"),
    path("wallet/merchants/recent", saved.RecentMerchantsView.as_view(), name="recent-merchants"),
    path("lifestyle/orders", lifestyle.OrdersView.as_view(), name="lifestyle-orders"),
    path("lifestyle/<str:category>", lifestyle.CatalogueView.as_view(), name="lifestyle-catalogue"),
    path("cross-border", cross_border_views.CorridorsView.as_view(), name="xb-corridors"),
    path("cross-border/quote", cross_border_views.QuoteView.as_view(), name="xb-quote"),
    path("cross-border/send", cross_border_views.SendView.as_view(), name="xb-send"),
    path("cross-border/transfers", cross_border_views.TransfersView.as_view(), name="xb-transfers"),
    path("cross-border/transfers/<str:reference>", cross_border_views.TransferDetailView.as_view(),
         name="xb-transfer"),
]
