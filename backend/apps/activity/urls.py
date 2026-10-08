from django.urls import path

from . import statement_views, views

app_name = "activity"

urlpatterns = [
    path("activity", views.ActivityView.as_view(), name="feed"),
    path("statements", statement_views.CustomerStatementView.as_view(), name="statement"),
    path("statements/link", statement_views.StatementLinkView.as_view(), name="statement-link"),
    path("statements/download", statement_views.StatementDownloadView.as_view(), name="statement-download"),
    path("merchant-app/statements", statement_views.MerchantStatementView.as_view(), name="merchant-statement"),
]
