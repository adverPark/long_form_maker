from django.db import migrations, models


def create_upload_info_step(apps, schema_editor):
    PipelineStep = apps.get_model('pipeline', 'PipelineStep')
    PipelineStep.objects.get_or_create(
        name='upload_info_generator',
        defaults={
            'display_name': '업로드 정보 생성',
            'order': 90,
        }
    )


def remove_upload_info_step(apps, schema_editor):
    PipelineStep = apps.get_model('pipeline', 'PipelineStep')
    PipelineStep.objects.filter(name='upload_info_generator').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('pipeline', '0025_allow_blank_prompts'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pipelinestep',
            name='name',
            field=models.CharField(
                choices=[
                    ('topic_finder', '주제 찾기'),
                    ('youtube_collector', 'YouTube 수집'),
                    ('content_analyzer', '콘텐츠 분석'),
                    ('researcher', '리서치'),
                    ('script_writer', '대본 작성'),
                    ('scene_planner', '씬 분할'),
                    ('image_prompter', '이미지 프롬프트'),
                    ('scene_generator', '이미지 생성'),
                    ('tts_generator', 'TTS 생성'),
                    ('video_generator', '동영상 생성'),
                    ('video_composer', '영상 편집'),
                    ('thumbnail_generator', '썸네일 생성'),
                    ('upload_info_generator', '업로드 정보 생성'),
                ],
                max_length=50,
                unique=True,
                verbose_name='단계명',
            ),
        ),
        migrations.RunPython(create_upload_info_step, remove_upload_info_step),
    ]
