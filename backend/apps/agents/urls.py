from django.urls import path

from . import views

app_name = "agents"

urlpatterns = [
    path("agent/me", views.AgentMeView.as_view(), name="me"),
    path("agent/cash-in", views.CashInView.as_view(), name="cash-in"),
    path("agent/cash-out", views.CashOutView.as_view(), name="cash-out"),        # request only
    path("agent/cash-out/<uuid:pk>", views.CashOutStatusView.as_view(), name="cash-out-status"),
    path("agent/customer", views.CustomerLookupView.as_view(), name="customer-lookup"),
    path("wallet/cash-out/allow", views.CashOutWindowView.as_view(), name="cash-out-allow"),
    # Customer "Allow CashOut": approve (with PIN) or decline an agent's request.
    path("wallet/cash-out-requests", views.CustomerCashOutsView.as_view(), name="cash-out-requests"),
    path("wallet/cash-out-requests/<uuid:pk>/<str:decision>", views.CustomerCashOutDecisionView.as_view(),
         name="cash-out-decision"),
    path("agent/transactions", views.AgentTxnsView.as_view(), name="transactions"),
    path("agent/transactions/<uuid:pk>", views.AgentTxnDetailView.as_view(), name="transaction"),
    path("agent/history", views.AgentHistoryView.as_view(), name="history"),
]
