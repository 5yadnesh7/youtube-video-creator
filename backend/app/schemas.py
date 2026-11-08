from typing import Literal, Optional

from pydantic import BaseModel, Field

VideoFormat = Literal["vertical", "horizontal"]
Language = Literal["english", "hinglish"]
VoiceGender = Literal["male", "female"]
MediaType = Literal["video", "image"]
JobStatus = Literal[
    "scripting", "script_ready", "rendering", "done", "failed", "cancelled"
]
StageStatus = Literal["pending", "running", "done", "failed", "cancelled"]


class JobSettings(BaseModel):
    format: VideoFormat = "vertical"
    duration_seconds: int = 60
    language: Language = "english"
    voice: VoiceGender = "male"
    model: Optional[str] = None
    voice_id: Optional[str] = None  # "<provider>:<voice>", e.g. "edge:en-US-GuyNeural"; None = auto


class ModelInfo(BaseModel):
    id: str
    name: str
    context_length: int = 0


class ModelsResponse(BaseModel):
    models: list[ModelInfo]
    default: str


class Trend(BaseModel):
    title: str
    source: str  # "google_trends" | "google_news" | "reddit"
    category: str = "general"
    angle: str = ""
    url: Optional[str] = None


class Scene(BaseModel):
    narration: str
    visual_query: str  # always English, used for Pexels/Pixabay search
    media_type: MediaType = "video"
    mood: str = "neutral"


class Script(BaseModel):
    title: str
    hook: str
    scenes: list[Scene]


class Chapter(BaseModel):
    start_seconds: float
    title: str


class Seo(BaseModel):
    title: str
    description: str
    tags: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)


class SceneAudio(BaseModel):
    path: str  # absolute path to mp3/wav
    duration: float  # seconds, measured with ffprobe


class SceneMedia(BaseModel):
    path: str  # absolute path to downloaded jpg/png/mp4
    kind: MediaType
    source: str = "pexels"  # "pexels" | "pixabay" | "fallback"


class CreateJobRequest(BaseModel):
    topic: str
    topic_url: Optional[str] = None  # link of the picked trend (if any); researched first
    topic_angle: Optional[str] = None  # trend's one-line angle (if any)
    settings: JobSettings = Field(default_factory=JobSettings)


class ResearchSource(BaseModel):
    title: str
    url: str
    publisher: str = ""
    published: str = ""  # ISO date or free text, may be empty


class Research(BaseModel):
    brief: str  # fact brief the script is written from (plain text / bullets)
    sources: list[ResearchSource] = Field(default_factory=list)
    grounded: bool = False  # False = nothing found, script falls back to model knowledge
    notes: str = ""  # e.g. why research was thin


class UpdateScriptRequest(BaseModel):
    script: Script


class RenderRequest(BaseModel):
    voice_id: Optional[str] = None  # overrides settings.voice_id for this and later renders
    reuse_media: bool = False  # skip re-downloading visuals (voice-only re-render)


class VoiceOption(BaseModel):
    id: str  # "<provider>:<voice>"
    provider: Literal["heygen", "fish", "edge"]
    provider_label: str
    name: str
    gender: Literal["male", "female", "unknown"] = "unknown"
    experimental: bool = False  # language support not verified for this provider


class VoicesResponse(BaseModel):
    voices: list[VoiceOption]
    default_id: str  # voice the "auto" mode would use for the chosen language/gender


class VoicePreviewRequest(BaseModel):
    voice_id: str
    language: Language = "english"


class Stage(BaseModel):
    key: str  # research|script|voice|visuals|clips|merge|finish|seo
    label: str
    status: StageStatus = "pending"
    done: int = 0  # finished units (scenes) when total > 0
    total: int = 0  # 0 = no countable unit
    percent: int = 0  # 0-100 progress inside this stage
    detail: str = ""  # short human text, e.g. "5 of 8 scenes voiced"
    started_at: Optional[str] = None  # ISO timestamps
    finished_at: Optional[str] = None


class Job(BaseModel):
    id: str
    topic: str
    topic_url: Optional[str] = None
    topic_angle: Optional[str] = None
    settings: JobSettings
    status: JobStatus
    step: str = ""
    progress: int = 0  # 0-100
    stages: list[Stage] = Field(default_factory=list)  # checklist of the current/last run
    script: Optional[Script] = None
    research: Optional[Research] = None
    seo: Optional[Seo] = None
    error: Optional[str] = None
    video_ready: bool = False
    created_at: str
