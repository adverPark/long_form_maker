from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from django.views.decorators.http import require_POST
from .models import APIKey
from apps.pipeline.models import ImageStylePreset, StyleSampleImage, CharacterPreset, VoicePreset, ThumbnailStylePreset


def login_view(request):
    """로그인 페이지"""
    if request.user.is_authenticated:
        return redirect('pipeline:dashboard')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            next_url = request.GET.get('next', 'pipeline:dashboard')
            return redirect(next_url)
    else:
        form = AuthenticationForm()

    return render(request, 'accounts/login.html', {'form': form})


def logout_view(request):
    """로그아웃"""
    logout(request)
    return redirect('accounts:login')


@login_required
def settings_view(request):
    """사용자 설정 페이지"""
    from .models import User

    api_keys = APIKey.objects.filter(user=request.user)

    # API 키를 서비스별로 그룹화
    gemini_keys = api_keys.filter(service='gemini')
    replicate_keys = api_keys.filter(service='replicate')

    # 프리셋들
    image_styles = ImageStylePreset.objects.filter(user=request.user)
    characters = CharacterPreset.objects.filter(user=request.user)
    voices = VoicePreset.objects.filter(user=request.user)
    thumbnail_styles = ThumbnailStylePreset.objects.filter(user=request.user)

    context = {
        'gemini_keys': gemini_keys,
        'replicate_keys': replicate_keys,
        'services': APIKey.SERVICE_CHOICES,
        'gemini_model_choices': User.GEMINI_MODEL_CHOICES,
        'current_gemini_model': request.user.gemini_model,
        # 프리셋들
        'image_styles': image_styles,
        'characters': characters,
        'voices': voices,
        'thumbnail_styles': thumbnail_styles,
    }
    return render(request, 'accounts/settings.html', context)


@login_required
@require_POST
def save_api_key(request):
    """API 키 저장"""
    service = request.POST.get('service')
    name = request.POST.get('name', '').strip()
    api_key_value = request.POST.get('api_key', '').strip()
    is_default = request.POST.get('is_default') == 'on'

    if not service or not api_key_value or not name:
        messages.error(request, '모든 필드를 입력해주세요.')
        return redirect('accounts:settings')

    # 첫 번째 키면 자동으로 기본 설정
    existing_count = APIKey.objects.filter(user=request.user, service=service).count()
    if existing_count == 0:
        is_default = True

    api_key = APIKey(
        user=request.user,
        service=service,
        name=name,
        is_default=is_default
    )
    api_key.set_key(api_key_value)
    api_key.save()

    messages.success(request, f'{name} API 키가 저장되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def delete_api_key(request, pk):
    """API 키 삭제"""
    api_key = APIKey.objects.filter(user=request.user, pk=pk).first()
    if api_key:
        name = api_key.name
        was_default = api_key.is_default
        service = api_key.service
        api_key.delete()

        # 기본 키였으면 다른 키를 기본으로 설정
        if was_default:
            other_key = APIKey.objects.filter(user=request.user, service=service).first()
            if other_key:
                other_key.is_default = True
                other_key.save()

        messages.success(request, f'{name} API 키가 삭제되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def set_default_api_key(request, pk):
    """기본 API 키 설정"""
    api_key = APIKey.objects.filter(user=request.user, pk=pk).first()
    if api_key:
        api_key.is_default = True
        api_key.save()
        messages.success(request, f'{api_key.name}이(가) 기본 키로 설정되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def set_gemini_model(request):
    """Gemini 모델 설정"""
    model = request.POST.get('gemini_model')
    valid_models = ['2.5-flash', '2.5-pro', 'flash', 'pro']
    model_names = {
        '2.5-flash': 'Gemini 2.5 Flash',
        '2.5-pro': 'Gemini 2.5 Pro',
        'flash': 'Gemini 3 Flash',
        'pro': 'Gemini 3 Pro',
    }
    if model in valid_models:
        request.user.gemini_model = model
        request.user.save(update_fields=['gemini_model'])
        messages.success(request, f'{model_names[model]}으로 변경되었습니다.')
    return redirect('accounts:settings')


# =============================================
# 이미지 스타일 프리셋
# =============================================

@login_required
@require_POST
def save_image_style(request):
    """이미지 스타일 저장"""
    name = request.POST.get('name', '').strip()
    style_prompt = request.POST.get('style_prompt', '').strip()
    is_default = request.POST.get('is_default') == 'on'

    if not name:
        messages.error(request, '이름을 입력해주세요.')
        return redirect('accounts:settings')

    # 첫 번째면 자동으로 기본값
    if not ImageStylePreset.objects.filter(user=request.user).exists():
        is_default = True

    style = ImageStylePreset.objects.create(
        user=request.user,
        name=name,
        style_prompt=style_prompt,
        is_default=is_default
    )

    # 샘플 이미지 처리
    for i, img_file in enumerate(request.FILES.getlist('sample_images')):
        StyleSampleImage.objects.create(
            style=style,
            image=img_file,
            order=i
        )

    messages.success(request, f'이미지 스타일 "{name}"이(가) 저장되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def delete_image_style(request, pk):
    """이미지 스타일 삭제"""
    style = ImageStylePreset.objects.filter(user=request.user, pk=pk).first()
    if style:
        name = style.name
        style.delete()
        messages.success(request, f'이미지 스타일 "{name}"이(가) 삭제되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def set_default_image_style(request, pk):
    """기본 이미지 스타일 설정"""
    style = ImageStylePreset.objects.filter(user=request.user, pk=pk).first()
    if style:
        style.is_default = True
        style.save()
        messages.success(request, f'"{style.name}"이(가) 기본 스타일로 설정되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def edit_image_style(request, pk):
    """이미지 스타일 수정"""
    from django.http import JsonResponse

    style = ImageStylePreset.objects.filter(user=request.user, pk=pk).first()
    if not style:
        return JsonResponse({'success': False, 'message': '스타일을 찾을 수 없습니다.'})

    name = request.POST.get('name', '').strip()
    style_prompt = request.POST.get('style_prompt', '').strip()

    if name:
        style.name = name
    style.style_prompt = style_prompt  # 빈 값도 허용

    style.save()

    # 새 샘플 이미지가 있으면 기존 이미지 삭제 후 교체
    new_images = request.FILES.getlist('sample_images')
    if new_images:
        # 기존 샘플 이미지 삭제
        style.sample_images.all().delete()
        # 새 이미지 추가
        for i, img_file in enumerate(new_images):
            StyleSampleImage.objects.create(
                style=style,
                image=img_file,
                order=i
            )

    return JsonResponse({'success': True, 'message': '저장되었습니다.'})


# =============================================
# 캐릭터 프리셋
# =============================================

@login_required
@require_POST
def save_character(request):
    """캐릭터 저장"""
    name = request.POST.get('name', '').strip()
    character_prompt = request.POST.get('character_prompt', '').strip()
    image = request.FILES.get('image')
    is_default = request.POST.get('is_default') == 'on'

    if not name or not image:
        messages.error(request, '이름과 이미지를 입력해주세요.')
        return redirect('accounts:settings')

    if not CharacterPreset.objects.filter(user=request.user).exists():
        is_default = True

    CharacterPreset.objects.create(
        user=request.user,
        name=name,
        image=image,
        character_prompt=character_prompt,
        is_default=is_default
    )

    messages.success(request, f'캐릭터 "{name}"이(가) 저장되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def delete_character(request, pk):
    """캐릭터 삭제"""
    char = CharacterPreset.objects.filter(user=request.user, pk=pk).first()
    if char:
        name = char.name
        char.delete()
        messages.success(request, f'캐릭터 "{name}"이(가) 삭제되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def set_default_character(request, pk):
    """기본 캐릭터 설정"""
    char = CharacterPreset.objects.filter(user=request.user, pk=pk).first()
    if char:
        char.is_default = True
        char.save()
        messages.success(request, f'"{char.name}"이(가) 기본 캐릭터로 설정되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def edit_character(request, pk):
    """캐릭터 수정"""
    from django.http import JsonResponse

    char = CharacterPreset.objects.filter(user=request.user, pk=pk).first()
    if not char:
        return JsonResponse({'success': False, 'message': '캐릭터를 찾을 수 없습니다.'})

    name = request.POST.get('name', '').strip()
    character_prompt = request.POST.get('character_prompt', '').strip()
    image = request.FILES.get('image')

    if name:
        char.name = name
    char.character_prompt = character_prompt  # 빈 값도 허용
    if image:
        char.image = image

    char.save()
    return JsonResponse({'success': True, 'message': '저장되었습니다.'})


# =============================================
# TTS 음성 프리셋
# =============================================

@login_required
@require_POST
def save_voice_preset(request):
    """TTS 음성 프리셋 저장"""
    name = request.POST.get('name', '').strip()
    reference_audio = request.FILES.get('reference_audio')
    reference_text = request.POST.get('reference_text', '').strip()
    is_default = request.POST.get('is_default') == 'on'

    if not name or not reference_audio or not reference_text:
        messages.error(request, '이름, 참조 음성, 참조 텍스트를 모두 입력해주세요.')
        return redirect('accounts:settings')

    if not VoicePreset.objects.filter(user=request.user).exists():
        is_default = True

    # TTS 파라미터
    temperature = float(request.POST.get('temperature', 0.7))
    top_p = float(request.POST.get('top_p', 0.7))
    seed = int(request.POST.get('seed', 42))

    VoicePreset.objects.create(
        user=request.user,
        name=name,
        reference_audio=reference_audio,
        reference_text=reference_text,
        temperature=temperature,
        top_p=top_p,
        seed=seed,
        is_default=is_default
    )

    messages.success(request, f'TTS 음성 "{name}"이(가) 저장되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def delete_voice_preset(request, pk):
    """TTS 음성 프리셋 삭제"""
    voice = VoicePreset.objects.filter(user=request.user, pk=pk).first()
    if voice:
        name = voice.name
        voice.delete()
        messages.success(request, f'TTS 음성 "{name}"이(가) 삭제되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def set_default_voice_preset(request, pk):
    """기본 TTS 음성 설정"""
    voice = VoicePreset.objects.filter(user=request.user, pk=pk).first()
    if voice:
        voice.is_default = True
        voice.save()
        messages.success(request, f'"{voice.name}"이(가) 기본 음성으로 설정되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def edit_voice_preset(request, pk):
    """TTS 음성 프리셋 수정"""
    from django.http import JsonResponse

    voice = VoicePreset.objects.filter(user=request.user, pk=pk).first()
    if not voice:
        return JsonResponse({'success': False, 'message': '음성을 찾을 수 없습니다.'})

    name = request.POST.get('name', '').strip()
    reference_text = request.POST.get('reference_text', '').strip()
    reference_audio = request.FILES.get('reference_audio')

    if name:
        voice.name = name
    if reference_text:
        voice.reference_text = reference_text
    if reference_audio:
        voice.reference_audio = reference_audio

    # TTS 파라미터
    if 'temperature' in request.POST:
        voice.temperature = float(request.POST.get('temperature', 0.7))
    if 'top_p' in request.POST:
        voice.top_p = float(request.POST.get('top_p', 0.7))
    if 'seed' in request.POST:
        voice.seed = int(request.POST.get('seed', 42))

    voice.save()
    return JsonResponse({'success': True, 'message': '저장되었습니다.'})


# =============================================
# 썸네일 스타일 프리셋
# =============================================

@login_required
@require_POST
def add_thumbnail_style(request):
    """썸네일 스타일 추가"""
    name = request.POST.get('name', '').strip()
    style_type = request.POST.get('style_type', 'youtube')
    prompt_template = request.POST.get('prompt_template', '').strip()
    example_image = request.FILES.get('example_image')
    is_default = request.POST.get('is_default') == 'on'

    if not name:
        messages.error(request, '이름을 입력해주세요.')
        return redirect('accounts:settings')

    if not prompt_template:
        # 기본 템플릿
        prompt_template = """YouTube thumbnail for Korean economy video.

Main visual: {main_keyword} related scene
Korean text: '{title}' (large, bold, contrasting color)
Style: {style_type}, dramatic lighting
Emotion: curiosity, urgency

Technical requirements:
- 16:9 aspect ratio (1280x720)
- High contrast for mobile visibility
- Clean composition with focal point"""

    if not ThumbnailStylePreset.objects.filter(user=request.user).exists():
        is_default = True

    ThumbnailStylePreset.objects.create(
        user=request.user,
        name=name,
        style_type=style_type,
        prompt_template=prompt_template,
        example_image=example_image,
        is_default=is_default
    )

    messages.success(request, f'썸네일 스타일 "{name}"이(가) 저장되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def delete_thumbnail_style(request, pk):
    """썸네일 스타일 삭제"""
    style = ThumbnailStylePreset.objects.filter(user=request.user, pk=pk).first()
    if style:
        name = style.name
        style.delete()
        messages.success(request, f'썸네일 스타일 "{name}"이(가) 삭제되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def set_default_thumbnail_style(request, pk):
    """기본 썸네일 스타일 설정"""
    style = ThumbnailStylePreset.objects.filter(user=request.user, pk=pk).first()
    if style:
        style.is_default = True
        style.save()
        messages.success(request, f'"{style.name}"이(가) 기본 스타일로 설정되었습니다.')
    return redirect('accounts:settings')


@login_required
@require_POST
def edit_thumbnail_style(request, pk):
    """썸네일 스타일 수정"""
    from django.http import JsonResponse

    style = ThumbnailStylePreset.objects.filter(user=request.user, pk=pk).first()
    if not style:
        return JsonResponse({'success': False, 'message': '스타일을 찾을 수 없습니다.'})

    name = request.POST.get('name', '').strip()
    style_type = request.POST.get('style_type', '').strip()
    prompt_template = request.POST.get('prompt_template', '').strip()
    example_image = request.FILES.get('example_image')

    if name:
        style.name = name
    if style_type:
        style.style_type = style_type
    if prompt_template:
        style.prompt_template = prompt_template
    if example_image:
        style.example_image = example_image

    style.save()
    return JsonResponse({'success': True, 'message': '저장되었습니다.'})
