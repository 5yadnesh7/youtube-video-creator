import asyncio
import json
import time

import httpx
import pytest

from app.config import settings
from app.schemas import JobSettings, Scene
from app.services import llm, openrouter, tts
from app.services.llm import factory, ollama_provider
from app.services.llm.base import LLMError, LLMProvider
from app.services.llm.ollama_provider import OllamaProvider

TAGS = {
    "models": [
        {"name": "kimi-k3", "capabilities": ["completion", "vision"]},
        {"name": "clef:27b", "capabilities": ["decision", "vision"]},
        {"name": "gemma4:31b", "capabilities": ["completion", "thinking"]},
        {"name": "gpt-oss:120b", "capabilities": ["completion", "tools", "thinking"]},
    ]
}


def run(coro):
    return asyncio.run(coro)


def reply(text: str) -> dict:
    return {"model": "m", "message": {"role": "assistant", "content": text}, "done": True}


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    registry, instances = dict(factory._REGISTRY), dict(factory._INSTANCES)
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key")
    monkeypatch.setattr(settings, "ollama_api_key", "oll-key")
    monkeypatch.setattr(settings, "ollama_model", "")
    monkeypatch.setattr(settings, "llm_providers", "openrouter,ollama")
    factory._INSTANCES.clear()

    async def no_sleep(_):
        return None

    monkeypatch.setattr(ollama_provider.asyncio, "sleep", no_sleep)
    yield
    factory._REGISTRY.clear()
    factory._REGISTRY.update(registry)
    factory._INSTANCES.clear()
    factory._INSTANCES.update(instances)


def mock_ollama(monkeypatch, handler):
    seen = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        OllamaProvider, "_client",
        lambda self: httpx.AsyncClient(transport=httpx.MockTransport(wrapped)),
    )
    return seen


def is_probe(request) -> bool:
    return request.url.path == "/api/chat" and json.loads(request.content).get("options", {}).get("num_predict") == 1


def real_chats(seen):
    return [r for r in seen if r.url.path == "/api/chat" and not is_probe(r)]


def mock_openrouter(monkeypatch, handler):
    monkeypatch.setattr(
        openrouter, "_make_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


MESSAGES = [{"role": "user", "content": "hi"}]


# ---------- Ollama provider

def test_ollama_request_matches_the_cloud_api(monkeypatch):
    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        return httpx.Response(200, json=reply("hello"))

    seen = mock_ollama(monkeypatch, handler)
    out = run(OllamaProvider().chat_text(MESSAGES, None, 0.3, 500))
    assert out == "hello"
    chat = real_chats(seen)[0]
    assert str(chat.url) == "https://ollama.com/api/chat"
    assert chat.headers["authorization"] == "Bearer oll-key"
    body = json.loads(chat.content)
    assert body == {"model": "gpt-oss:120b", "messages": MESSAGES, "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 500}}


def test_ollama_picks_a_text_model_from_the_live_list_and_caches_it(monkeypatch):
    seen = mock_ollama(monkeypatch, lambda r: httpx.Response(200, json=TAGS if r.url.path == "/api/tags" else reply("ok")))
    provider = OllamaProvider()
    run(provider.chat_text(MESSAGES, None, 0.5, None))
    run(provider.chat_text(MESSAGES, None, 0.5, None))
    assert sum(1 for r in seen if r.url.path == "/api/tags") == 1
    models = {json.loads(r.content)["model"] for r in real_chats(seen)}
    assert models == {"gpt-oss:120b"}


def test_ollama_skips_models_that_cannot_complete_text(monkeypatch):
    tags = {"models": [{"name": "clef:27b", "capabilities": ["decision", "vision"]},
                       {"name": "some-new-model", "capabilities": ["completion"]}]}
    seen = mock_ollama(monkeypatch, lambda r: httpx.Response(200, json=tags if r.url.path == "/api/tags" else reply("ok")))
    run(OllamaProvider().chat_text(MESSAGES, None, 0.5, None))
    assert json.loads(real_chats(seen)[0].content)["model"] == "some-new-model"


def test_ollama_model_setting_and_explicit_model_win(monkeypatch):
    seen = mock_ollama(monkeypatch, lambda r: httpx.Response(200, json=reply("ok")))
    monkeypatch.setattr(settings, "ollama_model", "gemma4:31b")
    run(OllamaProvider().chat_text(MESSAGES, None, 0.5, None))
    run(OllamaProvider().chat_text(MESSAGES, "gpt-oss:20b", 0.5, None))
    assert [json.loads(r.content)["model"] for r in seen] == ["gemma4:31b", "gpt-oss:20b"]
    assert not any(r.url.path == "/api/tags" for r in seen)


def test_ollama_model_list_failure_falls_back_to_the_default_name(monkeypatch):
    seen = mock_ollama(monkeypatch, lambda r: httpx.Response(500) if r.url.path == "/api/tags" else httpx.Response(200, json=reply("ok")))
    assert run(OllamaProvider().chat_text(MESSAGES, None, 0.5, None)) == "ok"
    assert json.loads([r for r in seen if r.url.path == "/api/chat"][0].content)["model"] == ollama_provider.PREFERRED_MODELS[0]


def test_ollama_rejected_key_is_a_clear_error_without_retries(monkeypatch):
    seen = mock_ollama(monkeypatch, lambda r: httpx.Response(401, json={"error": "Unauthorized"}) if r.url.path == "/api/chat" else httpx.Response(200, json=TAGS))
    with pytest.raises(LLMError, match="OLLAMA_API_KEY"):
        run(OllamaProvider().chat_text(MESSAGES, None, 0.5, None))
    assert len(real_chats(seen)) == 1


def test_ollama_retries_transient_errors_then_succeeds(monkeypatch):
    attempts = {"n": 0}

    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        if is_probe(req):
            return httpx.Response(200, json=reply(""))
        attempts["n"] += 1
        return httpx.Response(429 if attempts["n"] < 3 else 200, json=reply("finally"))

    mock_ollama(monkeypatch, handler)
    assert run(OllamaProvider().chat_text(MESSAGES, None, 0.5, None)) == "finally"
    assert attempts["n"] == 3


def test_ollama_gives_up_after_the_retry_budget(monkeypatch):
    mock_ollama(monkeypatch, lambda r: httpx.Response(200, json=TAGS) if r.url.path == "/api/tags" else httpx.Response(503))
    with pytest.raises(RuntimeError, match="request failed"):
        run(OllamaProvider().chat_text(MESSAGES, None, 0.5, None))


def test_ollama_empty_or_reasoning_only_reply_is_an_error(monkeypatch):
    mock_ollama(monkeypatch, lambda r: httpx.Response(200, json=TAGS) if r.url.path == "/api/tags" else httpx.Response(200, json={"message": {"content": "", "thinking": "hmm"}}))
    with pytest.raises(RuntimeError, match="only reasoning text"):
        run(OllamaProvider().chat_text(MESSAGES, None, 0.5, None))


def test_each_provider_only_accepts_its_own_model_ids():
    ollama, router = OllamaProvider(), llm.get_provider("openrouter")
    assert router.accepts_model("meta-llama/llama-3.3-70b-instruct:free") and not router.accepts_model("gpt-oss:120b")
    assert ollama.accepts_model("gpt-oss:120b") and not ollama.accepts_model("meta-llama/llama-3.3-70b-instruct:free")
    assert not ollama.accepts_model(None) and not router.accepts_model(None)


# ---------- factory

class FakeProvider(LLMProvider):
    def __init__(self, name, configured=True, fail=None, reply_text="ok"):
        self.name, self.label = name, name.title()
        self._configured, self._fail, self._reply = configured, fail, reply_text
        self.calls = []

    def is_configured(self):
        return self._configured

    def accepts_model(self, model):
        return model == f"{self.name}-model"

    async def chat_text(self, messages, model, temperature, max_tokens):
        self.calls.append(model)
        if self._fail:
            raise self._fail
        return self._reply


def test_factory_registers_new_providers_and_builds_the_chain_in_order(monkeypatch):
    a, b = FakeProvider("alpha"), FakeProvider("beta")
    factory.register_provider("alpha", lambda: a)
    factory.register_provider("Beta", lambda: b)
    monkeypatch.setattr(settings, "llm_providers", " beta , alpha ")
    assert llm.provider_chain() == [b, a]
    assert {"alpha", "beta", "openrouter", "ollama"} <= set(llm.known_providers())


def test_chain_skips_unconfigured_unknown_and_duplicate_providers(monkeypatch):
    on, off = FakeProvider("on"), FakeProvider("off", configured=False)
    factory.register_provider("on", lambda: on)
    factory.register_provider("off", lambda: off)
    monkeypatch.setattr(settings, "llm_providers", "off,nonsense,on,on")
    assert llm.provider_chain() == [on]


def test_unknown_provider_error_lists_the_known_ones():
    with pytest.raises(llm.UnknownProviderError, match="openrouter"):
        llm.get_provider("does-not-exist")


def test_provider_instances_are_created_once():
    assert llm.get_provider("ollama") is llm.get_provider("OLLAMA")


def test_default_chain_has_openrouter_first_and_skips_ollama_without_a_key(monkeypatch):
    assert [p.name for p in llm.provider_chain()] == ["openrouter", "ollama"]
    monkeypatch.setattr(settings, "ollama_api_key", "")
    assert [p.name for p in llm.provider_chain()] == ["openrouter"]


# ---------- fallback chain

def test_chain_falls_back_to_the_next_provider_when_one_fails(monkeypatch):
    first = FakeProvider("one", fail=RuntimeError("quota"))
    second = FakeProvider("two", reply_text="from two")
    factory.register_provider("one", lambda: first)
    factory.register_provider("two", lambda: second)
    monkeypatch.setattr(settings, "llm_providers", "one,two")
    assert run(llm.chat_text(MESSAGES)) == "from two"
    assert len(first.calls) == 1 and len(second.calls) == 1


def test_chain_stops_at_the_first_provider_that_answers(monkeypatch):
    first, second = FakeProvider("one"), FakeProvider("two")
    factory.register_provider("one", lambda: first)
    factory.register_provider("two", lambda: second)
    monkeypatch.setattr(settings, "llm_providers", "one,two")
    run(llm.chat_text(MESSAGES))
    assert second.calls == []


def test_explicit_model_is_only_passed_to_the_provider_that_owns_it(monkeypatch):
    first = FakeProvider("one", fail=RuntimeError("down"))
    second = FakeProvider("two")
    factory.register_provider("one", lambda: first)
    factory.register_provider("two", lambda: second)
    monkeypatch.setattr(settings, "llm_providers", "one,two")
    run(llm.chat_text(MESSAGES, model="one-model"))
    assert first.calls == ["one-model"] and second.calls == [None]


def test_all_providers_failing_reports_every_reason(monkeypatch):
    factory.register_provider("one", lambda: FakeProvider("one", fail=RuntimeError("quota gone")))
    factory.register_provider("two", lambda: FakeProvider("two", fail=RuntimeError("timeout")))
    monkeypatch.setattr(settings, "llm_providers", "one,two")
    with pytest.raises(LLMError) as exc:
        run(llm.chat_text(MESSAGES))
    text = str(exc.value)
    assert "One: quota gone" in text and "Two: timeout" in text and "Tip:" in text  # ollama not in the chain


def test_failure_hint_is_dropped_once_ollama_is_part_of_the_chain(monkeypatch):
    factory.register_provider("one", lambda: FakeProvider("one", fail=RuntimeError("x")))
    factory.register_provider("ollama", lambda: FakeProvider("ollama", fail=RuntimeError("y")))
    monkeypatch.setattr(settings, "llm_providers", "one,ollama")
    with pytest.raises(LLMError) as exc:
        run(llm.chat_text(MESSAGES))
    assert "Tip:" not in str(exc.value)


def test_no_configured_provider_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    monkeypatch.setattr(settings, "ollama_api_key", "")
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY or OLLAMA_API_KEY"):
        run(llm.chat_text(MESSAGES))


def test_a_stop_request_is_not_swallowed_by_the_fallback(monkeypatch):
    stopped = FakeProvider("one", fail=asyncio.CancelledError())
    after = FakeProvider("two")
    factory.register_provider("one", lambda: stopped)
    factory.register_provider("two", lambda: after)
    monkeypatch.setattr(settings, "llm_providers", "one,two")
    with pytest.raises(asyncio.CancelledError):
        run(llm.chat_text(MESSAGES))
    assert after.calls == []


def test_chat_json_nudges_once_and_then_reports_bad_output(monkeypatch):
    replies = iter(["not json", '{"ok": true}'])
    provider = FakeProvider("one")

    async def chat_text(messages, model, temperature, max_tokens):
        provider.calls.append(messages)
        return next(replies)

    provider.chat_text = chat_text
    factory.register_provider("one", lambda: provider)
    monkeypatch.setattr(settings, "llm_providers", "one")
    assert run(llm.chat_json(MESSAGES)) == {"ok": True}
    assert "valid JSON only" in provider.calls[1][-1]["content"]

    replies = iter(["nope", "still nope"])
    with pytest.raises(LLMError, match="valid JSON"):
        run(llm.chat_json(MESSAGES))


# ---------- the screenshot scenario, end to end

def test_script_is_written_by_ollama_when_openrouter_hits_its_daily_limit(monkeypatch):
    from app.services import script as script_mod

    daily_limit = httpx.Response(
        429,
        json={"error": {"message": "Rate limit exceeded: free-models-per-day", "metadata": {
            "headers": {"X-RateLimit-Reset": str(int((time.time() + 3600) * 1000))}}}},
    )

    def openrouter_handler(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return daily_limit

    payload = {"title": "T", "hook": "H", "scenes": [
        {"narration": " ".join(["word"] * 19), "visual_query": "city night", "media_type": "video", "mood": "calm"}
        for _ in range(8)]}
    seen = mock_ollama(monkeypatch, lambda r: httpx.Response(200, json=TAGS if r.url.path == "/api/tags" else reply(json.dumps(payload))))
    mock_openrouter(monkeypatch, openrouter_handler)

    s = run(script_mod.generate_script("topic", JobSettings(duration_seconds=60), "meta-llama/llama-3.3-70b-instruct:free"))
    assert len(s.scenes) == 8 and s.title == "T"
    used = json.loads(real_chats(seen)[0].content)["model"]
    assert used == "gpt-oss:120b"  # the OpenRouter model id was not forwarded to Ollama
    assert openrouter.daily_limit_error() is not None  # later calls skip OpenRouter instantly


# ---------- voice fallback to Edge

def scenes(n=3):
    return [Scene(narration=f"line {i}", visual_query="x") for i in range(n)]


def test_unavailable_openrouter_voice_redoes_the_whole_video_with_one_matching_edge_voice(monkeypatch, tmp_path):
    used = []

    async def fake_synth(scn, cfg, out_dir, model=None, on_progress=None):
        used.append(cfg.voice_id)
        if cfg.voice_id.startswith("heygen"):
            raise tts.VoiceUnavailableError("daily limit")
        return ["audio"] * len(scn)

    monkeypatch.setattr(tts, "synthesize_scenes", fake_synth)
    cfg = JobSettings(voice="male", voice_id="heygen:nadine")  # nadine is a female voice
    audio, fb = run(tts.synthesize_scenes_with_fallback(scenes(), cfg, tmp_path))
    assert used == ["heygen:nadine", "edge:en-US-JennyNeural"]
    assert fb.voice_id == "edge:en-US-JennyNeural" and fb.label == "Jenny (US)" and "daily limit" in fb.reason
    assert audio == ["audio"] * 3 and cfg.voice_id == "heygen:nadine"  # the caller's settings are untouched


@pytest.mark.parametrize("voice_id,language,gender,expected", [
    ("heygen:bram", "english", "female", "edge:en-US-GuyNeural"),
    ("heygen:nadine", "hinglish", "male", "edge:hi-IN-SwaraNeural"),
    ("fish:default", "english", "female", "edge:en-US-JennyNeural"),
    ("fish:default", "hinglish", "male", "edge:hi-IN-MadhurNeural"),
])
def test_fallback_voice_matches_gender_and_language(voice_id, language, gender, expected):
    chosen = tts.parse_voice_id(voice_id)
    assert tts.fallback_voice_id(language, chosen, gender) == expected


def test_fallback_can_be_turned_off(monkeypatch, tmp_path):
    async def fake_synth(scn, cfg, out_dir, model=None, on_progress=None):
        raise tts.VoiceUnavailableError("down")

    monkeypatch.setattr(tts, "synthesize_scenes", fake_synth)
    monkeypatch.setattr(settings, "tts_fallback_to_edge", False)
    with pytest.raises(tts.VoiceUnavailableError):
        run(tts.synthesize_scenes_with_fallback(scenes(), JobSettings(voice_id="heygen:bram"), tmp_path))


def test_a_failing_edge_voice_is_not_retried_with_another_voice(monkeypatch, tmp_path):
    calls = []

    async def fake_synth(scn, cfg, out_dir, model=None, on_progress=None):
        calls.append(cfg.voice_id)
        raise tts.VoiceUnavailableError("edge down")

    monkeypatch.setattr(tts, "synthesize_scenes", fake_synth)
    with pytest.raises(tts.VoiceUnavailableError):
        run(tts.synthesize_scenes_with_fallback(scenes(), JobSettings(voice_id="edge:en-US-GuyNeural"), tmp_path))
    assert calls == ["edge:en-US-GuyNeural"]


def test_working_voice_is_used_as_is(monkeypatch, tmp_path):
    async def fake_synth(scn, cfg, out_dir, model=None, on_progress=None):
        return ["ok"] * len(scn)

    monkeypatch.setattr(tts, "synthesize_scenes", fake_synth)
    audio, fb = run(tts.synthesize_scenes_with_fallback(scenes(), JobSettings(voice_id="heygen:bram"), tmp_path))
    assert fb is None and len(audio) == 3


def test_settings_defaults_keep_fallbacks_on_and_ollama_off():
    from app.config import Settings

    fresh = Settings(_env_file=None)
    assert fresh.llm_providers == "openrouter,ollama"
    assert fresh.ollama_api_key == "" and fresh.ollama_base_url == "https://ollama.com"
    assert fresh.tts_fallback_to_edge is True


# ---------- model ids and the grouped picker

def test_model_ids_round_trip_with_their_provider():
    from app.services.llm import encode_model, split_model

    assert encode_model("ollama", "gpt-oss:120b") == "ollama:gpt-oss:120b"
    assert split_model("ollama:gpt-oss:120b") == ("ollama", "gpt-oss:120b")
    assert split_model("openrouter:google/gemma-4-31b-it:free") == ("openrouter", "google/gemma-4-31b-it:free")
    # legacy ids without a provider prefix are left alone, even though they contain colons
    assert split_model("gpt-oss:120b") == (None, "gpt-oss:120b")
    assert split_model("google/gemma-4-31b-it:free") == (None, "google/gemma-4-31b-it:free")
    assert split_model(None) == (None, None) and split_model("") == (None, "")
    assert split_model("ollama:") == (None, "ollama:")


def test_a_picked_ollama_model_is_asked_first_and_openrouter_stays_as_fallback(monkeypatch):
    router = FakeProvider("openrouter")
    ollama = FakeProvider("ollama", reply_text="from ollama")
    factory.register_provider("openrouter", lambda: router)
    factory.register_provider("ollama", lambda: ollama)
    run(llm.chat_text(MESSAGES, model="ollama:gpt-oss:120b"))
    assert ollama.calls == ["gpt-oss:120b"] and router.calls == []

    ollama.calls.clear()
    ollama._fail = RuntimeError("down")
    assert run(llm.chat_text(MESSAGES, model="ollama:gpt-oss:120b")) == "ok"
    assert ollama.calls == ["gpt-oss:120b"] and router.calls == [None]  # the fallback uses its own default model


def test_a_picked_openrouter_model_keeps_openrouter_first(monkeypatch):
    router = FakeProvider("openrouter")
    ollama = FakeProvider("ollama")
    factory.register_provider("openrouter", lambda: router)
    factory.register_provider("ollama", lambda: ollama)
    monkeypatch.setattr(settings, "llm_providers", "ollama,openrouter")  # configured order says Ollama first
    run(llm.chat_text(MESSAGES, model="openrouter:meta-llama/llama-3.3-70b-instruct:free"))
    assert router.calls == ["meta-llama/llama-3.3-70b-instruct:free"] and ollama.calls == []


def test_a_picked_model_of_an_unconfigured_provider_falls_back_to_defaults(monkeypatch):
    router = FakeProvider("openrouter")
    factory.register_provider("openrouter", lambda: router)
    monkeypatch.setattr(settings, "ollama_api_key", "")
    run(llm.chat_text(MESSAGES, model="ollama:gpt-oss:120b"))
    assert router.calls == [None]


class CatalogProvider(FakeProvider):
    def __init__(self, name, models, available=True, configured=True, default=None, fail_list=False, hint=""):
        super().__init__(name, configured=configured)
        self._models, self._available, self._default, self._fail_list = models, available, default, fail_list
        self.setup_hint = hint

    def is_available(self):
        return self._available

    async def list_models(self):
        if self._fail_list:
            raise RuntimeError("listing failed")
        from app.schemas import ModelInfo

        return [ModelInfo(id=f"{self.name}:{m}", name=m, provider=self.name, provider_label=self.label) for m in self._models]

    async def default_model(self):
        return f"{self.name}:{self._default or self._models[0]}"


def test_catalog_groups_models_by_provider_in_configured_order(monkeypatch):
    factory.register_provider("one", lambda: CatalogProvider("one", ["a", "b"], default="b"))
    factory.register_provider("two", lambda: CatalogProvider("two", ["c"]))
    monkeypatch.setattr(settings, "llm_providers", "one,two")
    c = run(llm.catalog())
    assert [p.id for p in c.providers] == ["one", "two"]
    assert [m.id for m in c.models] == ["one:a", "one:b", "two:c"]
    assert c.default == "one:b" and all(p.configured and p.available for p in c.providers)


def test_catalog_skips_an_unavailable_provider_for_the_default_but_still_lists_it(monkeypatch):
    factory.register_provider("one", lambda: CatalogProvider("one", ["a"], available=False))
    factory.register_provider("two", lambda: CatalogProvider("two", ["c"]))
    monkeypatch.setattr(settings, "llm_providers", "one,two")
    c = run(llm.catalog())
    assert c.default == "two:c"
    one = c.providers[0]
    assert one.configured and not one.available and "quota" in one.note
    assert [m.id for m in c.models] == ["one:a", "two:c"]


def test_catalog_shows_an_unconfigured_provider_with_its_setup_hint(monkeypatch):
    factory.register_provider("one", lambda: CatalogProvider("one", ["a"]))
    factory.register_provider("two", lambda: CatalogProvider("two", ["c"], configured=False, hint="Set TWO_KEY in .env"))
    monkeypatch.setattr(settings, "llm_providers", "one,two")
    c = run(llm.catalog())
    two = c.providers[1]
    assert not two.configured and two.note == "Set TWO_KEY in .env"
    assert [m.id for m in c.models] == ["one:a"]


def test_catalog_survives_a_provider_that_cannot_list_its_models(monkeypatch):
    factory.register_provider("one", lambda: CatalogProvider("one", ["a"], fail_list=True))
    factory.register_provider("two", lambda: CatalogProvider("two", ["c"]))
    monkeypatch.setattr(settings, "llm_providers", "one,two")
    c = run(llm.catalog())
    assert c.default == "two:c" and "could not load" in c.providers[0].note


def test_catalog_with_nothing_configured_is_empty_but_explains_what_to_set(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    monkeypatch.setattr(settings, "ollama_api_key", "")
    c = run(llm.catalog())
    assert c.models == [] and c.default == ""
    assert {p.id: p.note for p in c.providers} == {
        "openrouter": "Set OPENROUTER_API_KEY in .env", "ollama": "Set OLLAMA_API_KEY in .env"}


def test_ollama_lists_only_text_models_with_preferred_ones_first(monkeypatch):
    mock_ollama(monkeypatch, lambda r: httpx.Response(200, json=TAGS))
    models = run(OllamaProvider().list_models())
    assert [m.id for m in models] == ["ollama:gpt-oss:120b", "ollama:gemma4:31b", "ollama:kimi-k3"]
    assert all(m.provider == "ollama" and m.provider_label == "Ollama cloud" for m in models)


def test_ollama_default_model_honours_the_setting(monkeypatch):
    mock_ollama(monkeypatch, lambda r: httpx.Response(200, json=TAGS))
    assert run(OllamaProvider().default_model()) == "ollama:gpt-oss:120b"
    monkeypatch.setattr(settings, "ollama_model", "gemma4:31b")
    assert run(OllamaProvider().default_model()) == "ollama:gemma4:31b"


def test_openrouter_models_are_listed_with_provider_prefixed_ids(monkeypatch):
    models_payload = {"data": [{"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "Llama", "context_length": 1000,
                                "pricing": {"prompt": "0", "completion": "0"},
                                "architecture": {"output_modalities": ["text"]}}]}
    mock_openrouter(monkeypatch, lambda r: httpx.Response(200, json=models_payload))
    openrouter._cache.update({"at": 0.0, "models": []})
    listed = run(llm.get_provider("openrouter").list_models())
    assert listed[0].id == "openrouter:meta-llama/llama-3.3-70b-instruct:free"
    assert listed[0].provider == "openrouter" and listed[0].provider_label == "OpenRouter"
    assert run(llm.get_provider("openrouter").default_model()) == listed[0].id


def test_models_endpoint_returns_grouped_catalog(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    factory.register_provider("one", lambda: CatalogProvider("one", ["a"]))
    monkeypatch.setattr(settings, "llm_providers", "one")
    with TestClient(app) as client:
        data = client.get("/api/models").json()
    assert data["default"] == "one:a"
    assert data["models"][0] == {"id": "one:a", "name": "a", "context_length": 0, "provider": "one", "provider_label": "One"}
    assert data["providers"][0]["id"] == "one" and data["providers"][0]["configured"] is True



# ---------- only models the key can use

PLAN = {"gemma4:31b": 200, "gpt-oss:120b": 402, "kimi-k3": 402}


def plan_handler(statuses, seen_probes=None):
    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        body = json.loads(req.content)
        if is_probe(req):
            if seen_probes is not None:
                seen_probes.append(body["model"])
            code = statuses.get(body["model"], 200)
            return httpx.Response(code, json=reply("x") if code == 200 else {"error": "not included in your free usage"})
        return httpx.Response(200, json=reply("answer"))

    return handler


def test_ollama_lists_only_the_models_this_key_can_use(monkeypatch):
    mock_ollama(monkeypatch, plan_handler(PLAN))
    models = run(OllamaProvider().list_models())
    assert [m.id for m in models] == ["ollama:gemma4:31b"]


def test_ollama_probe_is_a_one_token_request(monkeypatch):
    seen = mock_ollama(monkeypatch, plan_handler(PLAN))
    run(OllamaProvider().list_models())
    probes = [json.loads(r.content) for r in seen if is_probe(r)]
    assert {p["model"] for p in probes} == {"gemma4:31b", "gpt-oss:120b", "kimi-k3"}
    assert all(p["stream"] is False and p["options"] == {"num_predict": 1} for p in probes)


def test_probe_results_are_cached_and_not_repeated(monkeypatch):
    probes = []
    mock_ollama(monkeypatch, plan_handler(PLAN, probes))
    provider = OllamaProvider()
    run(provider.list_models())
    run(provider.list_models())
    run(provider.default_model())
    assert len(probes) == 3  # one per model, ever, within the cache window


def test_a_transient_failure_is_rechecked_soon_but_a_plan_refusal_is_not(monkeypatch):
    probes = []
    statuses = {"gemma4:31b": 503, "gpt-oss:120b": 402, "kimi-k3": 200}
    mock_ollama(monkeypatch, plan_handler(statuses, probes))
    provider = OllamaProvider()
    assert [m.name for m in run(provider.list_models())] == ["kimi-k3"]

    statuses["gemma4:31b"] = 200
    clock = {"now": time.time()}
    monkeypatch.setattr(ollama_provider.time, "time", lambda: clock["now"])
    clock["now"] += ollama_provider.TRANSIENT_TTL + 1
    probes.clear()
    assert [m.name for m in run(provider.list_models())] == ["gemma4:31b", "kimi-k3"]
    assert probes == ["gemma4:31b"]  # only the transient one was looked at again

    clock["now"] += ollama_provider.STABLE_TTL + 1
    probes.clear()
    run(provider.list_models())
    assert sorted(probes) == ["gemma4:31b", "gpt-oss:120b", "kimi-k3"]  # eventually everything is re-checked


def test_default_model_skips_a_preferred_model_the_key_cannot_use(monkeypatch):
    mock_ollama(monkeypatch, plan_handler(PLAN))
    assert run(OllamaProvider().default_model()) == "ollama:gemma4:31b"  # gpt-oss:120b is preferred but gated


def test_a_paid_key_sees_everything_without_any_code_change(monkeypatch):
    mock_ollama(monkeypatch, plan_handler({}))
    assert [m.name for m in run(OllamaProvider().list_models())] == ["gpt-oss:120b", "gemma4:31b", "kimi-k3"]


def test_an_explicit_ollama_model_setting_skips_probing(monkeypatch):
    probes = []
    mock_ollama(monkeypatch, plan_handler(PLAN, probes))
    monkeypatch.setattr(settings, "ollama_model", "gemma4:31b")
    run(OllamaProvider().chat_text(MESSAGES, None, 0.5, None))
    assert probes == []


def test_chatting_with_a_model_outside_the_plan_gives_a_clear_error_and_hides_it(monkeypatch):
    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        if is_probe(req):
            return httpx.Response(200, json=reply("x"))
        return httpx.Response(402, json={"error": "this model is not included in your free usage"})

    mock_ollama(monkeypatch, handler)
    provider = OllamaProvider()
    assert "ollama:kimi-k3" in [m.id for m in run(provider.list_models())]
    with pytest.raises(LLMError, match="kimi-k3 is not included in this key's plan"):
        run(provider.chat_text(MESSAGES, "kimi-k3", 0.5, None))
    assert "ollama:kimi-k3" not in [m.id for m in run(provider.list_models())]


def test_a_key_with_no_usable_models_is_explained_in_the_catalog(monkeypatch):
    mock_ollama(monkeypatch, plan_handler({"gemma4:31b": 402, "gpt-oss:120b": 402, "kimi-k3": 402}))
    monkeypatch.setattr(settings, "llm_providers", "ollama")
    c = run(llm.catalog())
    assert c.models == [] and "plan" in c.providers[0].note


def test_probing_is_limited_to_a_few_requests_at_a_time(monkeypatch):
    many = {"models": [{"name": f"m{i}", "capabilities": ["completion"]} for i in range(20)]}
    state = {"now": 0, "peak": 0}

    class Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if request.url.path == "/api/tags":
                return httpx.Response(200, json=many)
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
            loop = asyncio.get_running_loop()
            gate = loop.create_future()  # asyncio.sleep is stubbed out in these tests, so wait on a timer instead
            loop.call_later(0.01, gate.set_result, None)
            await gate
            state["now"] -= 1
            return httpx.Response(200, json=reply("x"))

    monkeypatch.setattr(OllamaProvider, "_client", lambda self: httpx.AsyncClient(transport=Slow()))
    run(OllamaProvider().warm())
    assert 1 < state["peak"] <= ollama_provider.PROBE_CONCURRENCY


def test_warm_up_prepares_every_configured_provider_and_survives_failures(monkeypatch):
    warmed = []

    class Warmable(FakeProvider):
        async def warm(self):
            warmed.append(self.name)
            if self.name == "bad":
                raise RuntimeError("boom")

    factory.register_provider("bad", lambda: Warmable("bad"))
    factory.register_provider("good", lambda: Warmable("good"))
    monkeypatch.setattr(settings, "llm_providers", "bad,good")
    run(llm.warm_up())
    assert warmed == ["bad", "good"]
