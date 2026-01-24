from django.urls import path
from . import views

app_name = 'api'

urlpatterns = [
    path('progress/<int:execution_id>/', views.progress, name='progress'),
    path('execution/<int:execution_id>/', views.execution_detail, name='execution_detail'),
]
