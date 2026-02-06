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
    path('project/<int:pk>/progress/<int:execution_id>/delete/', views.step_delete, name='step_delete'),
    path('project/<int:pk>/progress/<int:execution_id>/acknowledge/', views.step_acknowledge, name='step_acknowledge'),
    path('project/<int:pk>/draft/update/', views.draft_update, name='draft_update'),
    path('project/<int:pk>/research/manual-notes/', views.research_manual_notes, name='research_manual_notes'),
    path('project/<int:pk>/media/<str:media_type>/', views.download_media, name='download_media'),
    path('project/<int:pk>/media/<str:media_type>/<int:scene_id>/', views.download_media, name='download_media_scene'),
    path('project/<int:pk>/settings/', views.project_settings, name='project_settings'),
    path('project/<int:pk>/image-settings/', views.project_settings, name='image_settings'),  # 하위 호환

    # 개별 씬 생성 API
    path('project/<int:pk>/scene/<int:scene_number>/generate-image/', views.scene_generate_image, name='scene_generate_image'),
    path('project/<int:pk>/scene/<int:scene_number>/generate-tts/', views.scene_generate_tts, name='scene_generate_tts'),
    path('project/<int:pk>/scene/<int:scene_number>/edit/', views.scene_edit, name='scene_edit'),
    path('project/<int:pk>/scene/<int:scene_number>/delete/', views.scene_delete, name='scene_delete'),
    path('project/<int:pk>/scene/<int:scene_number>/convert-tts/', views.scene_convert_tts, name='scene_convert_tts'),
    path('project/<int:pk>/scene/<int:scene_number>/generate-stock-video/', views.scene_generate_stock_video, name='scene_generate_stock_video'),
    path('project/<int:pk>/convert-all-tts/', views.convert_all_tts, name='convert_all_tts'),

    # 병렬 실행
    path('project/<int:pk>/step/parallel/', views.step_execute_parallel, name='step_execute_parallel'),

    # 자동 파이프라인
    path('project/<int:pk>/auto-pipeline/', views.auto_pipeline, name='auto_pipeline'),

    # 미디어 삭제
    path('project/<int:pk>/delete-final-video/', views.delete_final_video, name='delete_final_video'),
    path('project/<int:pk>/delete-all-audio/', views.delete_all_audio, name='delete_all_audio'),
    path('project/<int:pk>/delete-all-images/', views.delete_all_images, name='delete_all_images'),
    path('project/<int:pk>/delete-all-tts-text/', views.delete_all_tts_text, name='delete_all_tts_text'),
    path('project/<int:pk>/delete-all-image-prompts/', views.delete_all_image_prompts, name='delete_all_image_prompts'),
    path('project/<int:pk>/delete-mismatch-audio/', views.delete_mismatch_audio, name='delete_mismatch_audio'),

    # 업로드 정보
    path('project/<int:pk>/upload-info/', views.upload_info, name='upload_info'),
    path('project/<int:pk>/upload-info/generate/', views.generate_upload_info, name='generate_upload_info'),
    path('project/<int:pk>/generate-thumbnail/', views.generate_thumbnail, name='generate_thumbnail'),

    # 사용자별 프롬프트
    path('prompt/<str:agent_name>/', views.user_prompt, name='user_prompt'),
    path('prompt/<str:agent_name>/reset/', views.user_prompt_reset, name='user_prompt_reset'),
]
