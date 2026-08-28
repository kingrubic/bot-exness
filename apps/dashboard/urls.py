from django.urls import path
from apps.dashboard import views

urlpatterns = [
    path('', views.home_view, name='home'),
    path('wallet/<int:wallet_id>/', views.wallet_detail_view, name='wallet_detail'),
    path('admin-panel/', views.admin_view, name='admin_panel'),
]
