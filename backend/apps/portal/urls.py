from django.urls import path

from . import views, views_bulk, views_compliance, views_merchant_ops, views_onboarding, views_ops

app_name = "portal"

urlpatterns = [
    # Auth
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("2fa/", views.twofa, name="twofa"),
    path("security/", views.security_page, name="security"),
    path("invite/<str:token>/", views_onboarding.invite_accept, name="invite_accept"),
    path("password/forgot/", views_onboarding.password_forgot, name="password_forgot"),
    path("password/reset/", views_onboarding.password_reset, name="password_reset"),
    path("register/", views_onboarding.register, name="register"),
    path("register/verify/", views_onboarding.register_verify, name="register_verify"),

    # Merchant portal
    path("", views.dashboard, name="dashboard"),
    path("payments/", views.payments_list, name="payments"),
    path("payments/<str:reference>/", views_merchant_ops.payment_detail, name="payment_detail"),
    path("disputes/", views_merchant_ops.disputes_page, name="disputes"),
    path("webhooks/", views_merchant_ops.webhooks_page, name="webhooks"),
    path("statements/", views_merchant_ops.statements_page, name="statements"),
    path("payment-links/", views_merchant_ops.payment_links, name="payment_links"),
    path("api-keys/", views.api_keys_view, name="api_keys"),
    path("team/", views_onboarding.team, name="team"),
    path("settlements/", views.settlements_view, name="settlements"),
    path("qr/", views.qr_page, name="qr"),
    path("qr/<str:token>/", views.qr_request, name="qr_request"),
    path("qr/<str:token>/status/", views.qr_request_status, name="qr_request_status"),
    path("bulk/", views_bulk.bulk_list, name="bulk"),
    path("bulk/sample.<str:fmt>", views_bulk.bulk_sample, name="bulk_sample"),
    path("bulk/<uuid:pk>/", views_bulk.bulk_detail, name="bulk_detail"),
    path("bulk/<uuid:pk>/progress/", views_bulk.bulk_progress, name="bulk_progress"),
    path("bulk/<uuid:pk>/results.csv", views_bulk.bulk_results, name="bulk_results"),

    # Admin / back-office portal
    path("admin/", views.admin_dashboard, name="admin_dashboard"),
    path("admin/merchants/", views.merchant_queue, name="merchant_queue"),
    path("admin/merchants/new/", views_onboarding.merchant_new, name="merchant_new"),
    path("admin/merchants/<uuid:pk>/", views.merchant_detail, name="merchant_detail"),
    path("admin/merchants/<uuid:pk>/documents/<uuid:doc>/", views_onboarding.merchant_document,
         name="merchant_document"),
    path("admin/settlements/", views.settlement_queue, name="settlement_queue"),
    path("admin/integrations/", views.integrations, name="integrations"),
    path("admin/safeguarding/", views.safeguarding, name="safeguarding"),
    # Back-office operations (role-gated)
    path("admin/agents/", views_ops.agents_list, name="ops_agents"),
    path("admin/agents/<uuid:pk>/", views_ops.agent_detail, name="ops_agent"),
    path("admin/float-topups/", views_ops.float_topups, name="ops_float"),
    path("admin/cash-outs/", views_ops.cash_outs, name="ops_cashouts"),
    path("admin/kyc/", views_ops.kyc_overview, name="ops_kyc"),
    path("admin/reconciliation/", views_ops.recon_runs, name="ops_recon"),
    path("admin/reconciliation/<uuid:pk>/", views_ops.recon_run, name="ops_recon_run"),
    path("admin/support/", views_ops.support_lookup, name="ops_support"),
    path("admin/disputes/", views_merchant_ops.staff_disputes, name="ops_disputes"),
    path("admin/regulatory/", views_ops.regulatory_return, name="ops_regulatory"),
    path("admin/pricing/", views_ops.pricing, name="ops_pricing"),
    path("admin/withdrawals/", views_ops.product_withdrawals, name="ops_withdrawals"),
    path("admin/health/", views_ops.system_health, name="ops_health"),
    path("admin/audit/", views_ops.audit_log, name="ops_audit"),
    path("admin/disputes/<uuid:pk>/", views_merchant_ops.staff_dispute_detail, name="ops_dispute"),
    # Fraud & AML (compliance role)
    path("admin/compliance/", views_compliance.queue, name="compliance"),
    path("admin/compliance/alerts/<uuid:pk>/", views_compliance.alert_detail, name="compliance_alert"),
    path("admin/compliance/screening/", views_compliance.screening_queue, name="compliance_screening"),
    path("admin/compliance/reports/", views_compliance.reports_list, name="compliance_reports"),
    path("admin/compliance/reports/large-transactions.csv", views_compliance.ltr_export, name="compliance_ltr_export"),
    path("admin/compliance/reports/<uuid:pk>/", views_compliance.str_detail, name="compliance_str"),
    path("admin/compliance/reports/<uuid:pk>/download/", views_compliance.str_download, name="compliance_str_download"),
    path("admin/compliance/watchlists/", views_compliance.watchlists_page, name="compliance_watchlists"),
]
