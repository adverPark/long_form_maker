from django.db import migrations


def add_tts_generator_step(apps, schema_editor):
    """TTS 생성 단계 추가"""
    PipelineStep = apps.get_model('pipeline', 'PipelineStep')

    # tts_generator 단계가 없으면 추가
    if not PipelineStep.objects.filter(name='tts_generator').exists():
        PipelineStep.objects.create(
            name='tts_generator',
            display_name='TTS 생성',
            description='Fish Speech API를 사용하여 씬별 TTS 음성 생성',
            order=65,  # scene_generator(60) 다음, video_generator(70) 이전
            can_run_independently=True,
            manual_input_description='씬이 있어야 TTS 생성 가능'
        )


def remove_tts_generator_step(apps, schema_editor):
    """TTS 생성 단계 제거"""
    PipelineStep = apps.get_model('pipeline', 'PipelineStep')
    PipelineStep.objects.filter(name='tts_generator').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('pipeline', '0010_add_user_to_presets'),
    ]

    operations = [
        migrations.RunPython(add_tts_generator_step, remove_tts_generator_step),
    ]
