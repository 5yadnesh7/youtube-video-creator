import asyncio
import json

import httpx
import pytest

from app.schemas import JobSettings, Scene, Script
from app.services import openrouter, script as script_mod, seo as seo_mod

MODELS = {
    "data": [
        {"id": "google/gemma-2-9b-it:free", "name": "Gemma", "context_length": 8000,
         "pricing": {"prompt": "0", "completion": "0"}, "architecture": {"output_modalities": ["text"]}},
        {"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "Llama", "context_length": 65000,
         "pricing": {"prompt": "0", "completion": "0"}, "architecture": {"output_modalities": ["text"]}},
        {"id": "paid/model", "name": "Paid", "context_length": 100000,
         "pricing": {"prompt": "0.001", "completion": "0.002"}, "architecture": {"output_modalities": ["text"]}},
        {"id": "free/image-gen", "name": "Img", "context_length": 1000,
         "pricing": {"prompt": "0", "completion": "0"}, "architecture": {"output_modalities": ["image"]}},
    ]
}


def completion(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


@pytest.fixture(autouse=True)
def fake_net(monkeypatch):
    state = {"chat_handler": None, "calls": [], "seen_headers": None}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=MODELS)
        body = json.loads(request.content)
        state["calls"].append(body)
        state["seen_headers"] = request.headers
        return state["chat_handler"](body)

    monkeypatch.setattr(
        openrouter, "_make_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    async def no_sleep(_):
        return None

    monkeypatch.setattr(openrouter.asyncio, "sleep", no_sleep)
    openrouter._cache.update({"at": 0.0, "models": []})
    return state


def run(coro):
    return asyncio.run(coro)


def test_list_free_models_filters_and_sorts(fake_net):
    models = run(openrouter.list_free_models())
    ids = [m.id for m in models]
    assert ids == ["meta-llama/llama-3.3-70b-instruct:free", "google/gemma-2-9b-it:free"]
    assert run(openrouter.default_model()) == ids[0]


def test_default_model_fallback_on_failure(monkeypatch):
    monkeypatch.setattr(
        openrouter, "_make_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    assert run(openrouter.default_model()) == openrouter.FALLBACK_MODEL


def test_headers_omit_authorization_without_key(monkeypatch):
    monkeypatch.setattr(openrouter.settings, "openrouter_api_key", "")
    assert "Authorization" not in openrouter._headers()


def test_chat_text_retries_then_succeeds(fake_net, monkeypatch):
    monkeypatch.setattr(openrouter.settings, "openrouter_api_key", "test-key")
    n = {"i": 0}

    def h(body):
        n["i"] += 1
        return httpx.Response(429) if n["i"] < 3 else httpx.Response(200, json=completion("hello"))

    fake_net["chat_handler"] = h
    assert run(openrouter.chat_text([{"role": "user", "content": "hi"}])) == "hello"
    assert fake_net["seen_headers"]["x-title"]
    assert fake_net["seen_headers"]["http-referer"]
    assert fake_net["seen_headers"]["authorization"].startswith("Bearer ")


def test_chat_text_falls_back_to_next_model(fake_net):
    def h(body):
        if body["model"] == "meta-llama/llama-3.3-70b-instruct:free":
            return httpx.Response(503)
        return httpx.Response(200, json=completion("from fallback"))

    fake_net["chat_handler"] = h
    assert run(openrouter.chat_text([{"role": "user", "content": "x"}])) == "from fallback"


def test_chat_text_all_fail_raises(fake_net):
    fake_net["chat_handler"] = lambda body: httpx.Response(500)
    with pytest.raises(RuntimeError, match="All OpenRouter models failed"):
        run(openrouter.chat_text([{"role": "user", "content": "x"}]))


def test_extract_json_variants():
    assert openrouter.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert openrouter.extract_json('Sure! {"a": {"b": "}"}} thanks') == {"a": {"b": "}"}}
    with pytest.raises(ValueError):
        openrouter.extract_json("nothing here")


def test_chat_json_retries_once(fake_net):
    replies = iter(["not json", '{"ok": true}'])
    fake_net["chat_handler"] = lambda body: httpx.Response(200, json=completion(next(replies)))
    assert run(openrouter.chat_json([{"role": "user", "content": "x"}])) == {"ok": True}
    assert len(fake_net["calls"]) == 2
    assert "valid JSON only" in fake_net["calls"][1]["messages"][-1]["content"]


def make_script_payload(n_scenes: int, words_per_scene: int) -> dict:
    return {
        "title": "Test",
        "hook": "Hook",
        "scenes": [
            {"narration": " ".join(["word"] * words_per_scene), "visual_query": "city skyline night time lapse",
             "media_type": "video" if i % 4 else "image", "mood": "Calm"}
            for i in range(n_scenes)
        ],
    }


def test_generate_script_ok(fake_net):
    payload = make_script_payload(8, 19)
    fake_net["chat_handler"] = lambda body: httpx.Response(200, json=completion(json.dumps(payload)))
    s = run(script_mod.generate_script("topic", JobSettings(duration_seconds=60)))
    assert len(s.scenes) == 8
    assert s.scenes[0].visual_query == "city skyline night time"
    assert s.scenes[0].mood == "calm"


def test_generate_script_retries_then_clamps(fake_net):
    payload = make_script_payload(30, 20)
    fake_net["chat_handler"] = lambda body: httpx.Response(200, json=completion(json.dumps(payload)))
    s = run(script_mod.generate_script("topic", JobSettings(duration_seconds=60)))
    total = sum(len(x.narration.split()) for x in s.scenes)
    assert total <= 150 * 1.25 + 1
    assert len(s.scenes) <= 10
    assert len(fake_net["calls"]) == script_mod.MAX_ATTEMPTS


def test_to_devanagari(fake_net):
    fake_net["chat_handler"] = lambda body: httpx.Response(200, json=completion('"आज हम बात करेंगे"'))
    assert run(script_mod.to_devanagari("Aaj hum baat karenge")) == "आज हम बात करेंगे"
    fake_net["chat_handler"] = lambda body: httpx.Response(500)
    assert run(script_mod.to_devanagari("Aaj hum baat karenge")) == "Aaj hum baat karenge"


SCRIPT = Script(
    title="How AI Works",
    hook="AI is everywhere.",
    scenes=[Scene(narration=f"Scene {i} narration.", visual_query="robot hand") for i in range(20)],
)

SEO_REPLY = {
    "title": "AI Explained: " + "very long " * 12,
    "summary": "Summary.",
    "hook_line": "Hook line.",
    "tags": [f"tag{i}" for i in range(20)],
    "hashtags": ["AI", "#Tech", "Learn"],
    "chapter_titles": [f"Chapter {i}" for i in range(8)],
}


def test_seo_horizontal_chapters(fake_net):
    fake_net["chat_handler"] = lambda body: httpx.Response(200, json=completion(json.dumps(SEO_REPLY)))
    starts = [i * 10.0 for i in range(20)]
    seo = run(seo_mod.generate_seo(SCRIPT, JobSettings(format="horizontal", duration_seconds=200), starts))
    assert len(seo.title) <= 70
    assert 10 <= len(seo.tags) <= 15
    assert len(seo.chapters) <= 8 and seo.chapters[0].start_seconds == 0
    assert "00:00 Chapter 0" in seo.description
    assert all(h.startswith("#") for h in seo.hashtags) and 3 <= len(seo.hashtags) <= 5
    assert "#Shorts" not in seo.hashtags
    assert len(seo.description) <= 2000


def test_seo_vertical(fake_net):
    fake_net["chat_handler"] = lambda body: httpx.Response(200, json=completion(json.dumps(SEO_REPLY)))
    seo = run(seo_mod.generate_seo(SCRIPT, JobSettings(format="vertical"), [0.0, 5.0]))
    assert "#Shorts" in seo.hashtags
    assert seo.chapters == []
    assert "00:00" not in seo.description


def test_seo_llm_failure_still_returns(fake_net):
    fake_net["chat_handler"] = lambda body: httpx.Response(500)
    seo = run(seo_mod.generate_seo(SCRIPT, JobSettings(format="vertical")))
    assert seo.title and "#Shorts" in seo.hashtags


def _prompt_text(research, duration=300, language="english"):
    from app.schemas import JobSettings
    from app.services import script as script_svc

    s = JobSettings(duration_seconds=duration, language=language, format="horizontal")
    words, scenes = script_svc._targets(s)
    msgs = script_svc._build_prompt("Some topic", s, words, scenes, research)
    return msgs[1]["content"]


def test_prompt_includes_research_and_duration():
    from app.schemas import Research, ResearchSource

    r = Research(
        brief="- The launch happened on May 3 [1]",
        sources=[ResearchSource(title="Launch news", url="https://x.test/a", publisher="Reuters")],
        grounded=True,
    )
    text = _prompt_text(r, duration=300)
    assert "The launch happened on May 3" in text
    assert "[1] Launch news (Reuters)" in text
    assert "ONLY source of facts" in text
    assert "about 5 minutes" in text


def test_prompt_warns_when_research_missing_or_ungrounded():
    from app.schemas import Research

    for r in (None, Research(brief="", grounded=False)):
        text = _prompt_text(r)
        assert "No verified research" in text
        assert "RESEARCH (" not in text
