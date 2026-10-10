"""Instrument API URLs."""

from django.urls import path

from instruments.views import InstrumentListView

app_name = "instruments"

urlpatterns = [
    path("", InstrumentListView.as_view(), name="instrument-list"),
]
