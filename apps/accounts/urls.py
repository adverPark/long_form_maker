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
    path('gemini-model/', views.set_gemini_model, name='set_gemini_model'),

    # 이미지 스타일 프리셋
    path('image-style/save/', views.save_image_style, name='save_image_style'),
    path('image-style/edit/<int:pk>/', views.edit_image_style, name='edit_image_style'),
    path('image-style/delete/<int:pk>/', views.delete_image_style, name='delete_image_style'),
    path('image-style/default/<int:pk>/', views.set_default_image_style, name='set_default_image_style'),

    # 캐릭터 프리셋
    path('character/save/', views.save_character, name='save_character'),
    path('character/edit/<int:pk>/', views.edit_character, name='edit_character'),
    path('character/delete/<int:pk>/', views.delete_character, name='delete_character'),
    path('character/default/<int:pk>/', views.set_default_character, name='set_default_character'),

    # TTS 음성 프리셋
    path('voice-preset/save/', views.save_voice_preset, name='save_voice_preset'),
    path('voice-preset/edit/<int:pk>/', views.edit_voice_preset, name='edit_voice_preset'),
    path('voice-preset/delete/<int:pk>/', views.delete_voice_preset, name='delete_voice_preset'),
    path('voice-preset/default/<int:pk>/', views.set_default_voice_preset, name='set_default_voice_preset'),

    # 썸네일 스타일 프리셋
    path('thumbnail-style/save/', views.add_thumbnail_style, name='add_thumbnail_style'),
    path('thumbnail-style/edit/<int:pk>/', views.edit_thumbnail_style, name='edit_thumbnail_style'),
    path('thumbnail-style/delete/<int:pk>/', views.delete_thumbnail_style, name='delete_thumbnail_style'),
    path('thumbnail-style/default/<int:pk>/', views.set_default_thumbnail_style, name='set_default_thumbnail_style'),
]
