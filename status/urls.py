from django.urls import path

from .views import status_page_detail


app_name = 'status'

urlpatterns = [
    path('status/', status_page_detail, name='detail'),
    path('status/<slug:slug>/', status_page_detail, name='detail-by-slug'),
]
