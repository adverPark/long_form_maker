from .base import BaseStepService
from .topic_finder import TopicFinderService
from .researcher import ResearcherService
from .script_writer import ScriptWriterService
from .scene_planner import ScenePlannerService
from .image_prompter import ImagePrompterService
from .scene_generator import SceneGeneratorService
from .video_generator import VideoGeneratorService
from .video_composer import VideoComposerService
from .thumbnail_generator import ThumbnailGeneratorService
from .tts_generator import TTSGeneratorService
from .auto_pipeline import AutoPipelineService

# 에이전트 이름 -> 서비스 클래스 매핑
SERVICE_REGISTRY = {
    'topic_finder': TopicFinderService,
    'researcher': ResearcherService,
    'script_writer': ScriptWriterService,
    'scene_planner': ScenePlannerService,
    'image_prompter': ImagePrompterService,
    'scene_generator': SceneGeneratorService,
    'tts_generator': TTSGeneratorService,
    'video_generator': VideoGeneratorService,
    'video_composer': VideoComposerService,
    'thumbnail_generator': ThumbnailGeneratorService,
}


def get_service_class(step_name: str):
    """단계 이름으로 서비스 클래스 가져오기"""
    return SERVICE_REGISTRY.get(step_name)
