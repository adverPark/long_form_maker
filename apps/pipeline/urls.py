from django.urls import path
from . import views

app_name = 'pipeline'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('project/create/', views.project_create, name='project_create'),
    path('project/<int:pk>/', views.project_detail, name='project_detail'),
    path('project/<int:pk>/delete/', views.project_delete, name='project_delete'),
    path('project/<int:pk>/data/', views.project_data, name='project_data'),
    path('project/<int:pk>/step/<str:step_name>/', views.step_execute, name='step_execute'),
    path('project/<int:pk>/progress/<int:execution_id>/', views.step_progress, name='step_progress'),
    path('project/<int:pk>/progress/<int:execution_id>/api/', views.step_progress_api, name='step_progress_api'),
    path('project/<int:pk>/progress/<int:execution_id>/cancel/', views.step_cancel, name='step_cancel'),
    path('project/<int:pk>/draft/update/', views.draft_update, name='draft_update'),
    path('project/<int:pk>/media/<str:media_type>/', views.download_media, name='download_media'),
    path('project/<int:pk>/media/<str:media_type>/<int:scene_id>/', views.download_media, name='download_media_scene'),
    path('project/<int:pk>/settings/', views.project_settings, name='project_settings'),
    path('project/<int:pk>/image-settings/', views.project_settings, name='image_settings'),  # 하위 호환

    # 개별 씬 생성 API
    path('project/<int:pk>/scene/<int:scene_number>/generate-image/', views.scene_generate_image, name='scene_generate_image'),
    path('project/<int:pk>/scene/<int:scene_number>/generate-tts/', views.scene_generate_tts, name='scene_generate_tts'),
    path('project/<int:pk>/scene/<int:scene_number>/edit/', views.scene_edit, name='scene_edit'),
    path('project/<int:pk>/scene/<int:scene_number>/delete/', views.scene_delete, name='scene_delete'),

    # 병렬 실행
    path('project/<int:pk>/step/parallel/', views.step_execute_parallel, name='step_execute_parallel'),

    # 자동 파이프라인
    path('project/<int:pk>/auto-pipeline/', views.auto_pipeline, name='auto_pipeline'),
]
