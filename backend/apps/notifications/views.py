"""
Devices and inbox (JWT):
  POST   /devices                 {platform, app, token}
  DELETE /devices/<token>
  GET    /notifications           inbox (newest first)
  POST   /notifications/<id>/read
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import Device, Notification


class _DeviceSerializer(serializers.Serializer):
    platform = serializers.ChoiceField(choices=Device.Platform.values)
    app = serializers.ChoiceField(choices=Device.App.values, default=Device.App.CUSTOMER)
    token = serializers.CharField(min_length=20, max_length=512)


class DeviceView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        form = _DeviceSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        services.register_device(request.user, **form.validated_data)
        return Response({"registered": True}, status=status.HTTP_201_CREATED)


class DeviceDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, token):
        services.unregister_device(request.user, token)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InboxView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = Notification.objects.filter(user=request.user).order_by("-created_at")[:50]
        return Response([{
            "id": str(n.id), "kind": n.kind, "title": n.title, "body": n.body,
            "data": n.data, "read": n.read_at is not None, "created_at": n.created_at,
        } for n in rows])


class MarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        note = get_object_or_404(Notification, pk=pk, user=request.user)
        if note.read_at is None:
            note.read_at = timezone.now()
            note.save(update_fields=["read_at"])
        return Response({"read": True})
