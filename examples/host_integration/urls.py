"""Reference root URL wiring for the Status app."""

from django.urls import include, path


urlpatterns = [
    path('', include('apps.status.urls')),
]
