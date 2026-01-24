from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('settings/', views.settings_view, name='settings'),
    path('api-key/save/', views.save_api_key, name='save_api_key'),
    path('api-key/delete/<int:pk>/', views.delete_api_key, name='delete_api_key'),
    path('api-key/default/<int:pk>/', views.set_default_api_key, name='set_default_api_key'),
    path('voice/upload/', views.upload_voice, name='upload_voice'),
    path('voice/delete/<int:pk>/', views.delete_voice, name='delete_voice'),
    path('voice/default/<int:pk>/', views.set_default_voice, name='set_default_voice'),
    path('gemini-model/', views.set_gemini_model, name='set_gemini_model'),
]
