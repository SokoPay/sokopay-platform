"""
GET /api/v1/activity?filter=payments|transfers|wallet&period=today|7d|30d&cursor=<opaque>

The signed-in customer's activity across payments, transfers and wallet movements,
newest first, 30 per page. See services.py for sources, paging and privacy rules.
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services


class ActivityView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        q = request.query_params
        try:
            body = services.feed(request.user, kind_filter=q.get("filter", ""),
                                 period=q.get("period", ""), cursor=q.get("cursor") or None)
        except services.BadCursor:
            return Response({"error": "Invalid cursor."}, status=status.HTTP_400_BAD_REQUEST)
        except (ValueError, KeyError):
            return Response({"error": "Unknown filter or period."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(body)
