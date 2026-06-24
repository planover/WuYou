"""Translation provider adapters."""

from __future__ import annotations

import httpx

from app.models import TranslationRequest, TranslationResponse


_HTTP_CLIENT: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(timeout=httpx.Timeout(20.0))
    return _HTTP_CLIENT


PROVIDER_OPTIONS = [
    {
        "id": "mymemory",
        "name": "MyMemory",
        "kind": "free-web",
        "note": "无需密钥，适合轻量翻译，公共服务可能有频率限制。",
    },
    {
        "id": "libretranslate",
        "name": "LibreTranslate",
        "kind": "free-or-self-hosted",
        "note": "可使用公开实例，也可改为自建 LibreTranslate 服务。",
    },
    {
        "id": "lingva",
        "name": "Lingva Translate",
        "kind": "free-web",
        "note": "公共实例可用性取决于第三方服务状态。",
    },
    {
        "id": "openai_compatible",
        "name": "OpenAI 兼容大模型接口",
        "kind": "ai",
        "note": "填写兼容 Chat Completions 的接口地址和密钥即可接入。",
    },
    {
        "id": "custom",
        "name": "自定义翻译 API",
        "kind": "custom",
        "note": "POST JSON 到用户提供的地址，适合本地翻译服务。",
    },
]


async def translate(request: TranslationRequest, timeout: int = 20) -> TranslationResponse:
    providers = [request.provider]
    if request.provider == "auto":
        providers = ["mymemory", "lingva", "libretranslate"]

    errors: list[str] = []
    for provider in providers:
        try:
            text = await _translate_one(provider, request, timeout)
            return TranslationResponse(provider=provider, translated_text=text)
        except Exception as exc:
            errors.append(f"{provider}: {exc}")
    raise RuntimeError("翻译失败：" + "；".join(errors))


async def _translate_one(provider: str, request: TranslationRequest, timeout: int) -> str:
    client = _get_http_client()
    req_timeout = httpx.Timeout(timeout)
    if provider == "mymemory":
        response = await client.get(
            "https://api.mymemory.translated.net/get",
            params={
                "q": request.text,
                "langpair": f"{request.source_lang}|{request.target_lang}",
            },
            timeout=req_timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("responseData", {}).get("translatedText") or ""

    if provider == "lingva":
        response = await client.get(
            f"https://lingva.ml/api/v1/{request.source_lang}/{request.target_lang}/{request.text}",
            timeout=req_timeout,
        )
        response.raise_for_status()
        return response.json().get("translation", "")

    if provider == "libretranslate":
        url = request.custom_url or "https://libretranslate.com/translate"
        response = await client.post(
            url,
            json={
                "q": request.text,
                "source": request.source_lang,
                "target": request.target_lang,
                "format": "text",
                "api_key": request.api_key,
            },
            timeout=req_timeout,
        )
        response.raise_for_status()
        return response.json().get("translatedText", "")

    if provider == "openai_compatible":
        if not request.custom_url:
            raise ValueError("请填写 OpenAI 兼容接口地址。")
        headers = {"Authorization": f"Bearer {request.api_key}"} if request.api_key else {}
        response = await client.post(
            request.custom_url,
            headers=headers,
            json={
                "model": "gpt-4.1-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": f"Translate into {request.target_lang}. Keep formatting.",
                    },
                    {"role": "user", "content": request.text},
                ],
            },
            timeout=req_timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    if provider == "custom":
        if not request.custom_url:
            raise ValueError("请填写自定义翻译 API 地址。")
        response = await client.post(
            request.custom_url,
            headers={"Authorization": f"Bearer {request.api_key}"} if request.api_key else {},
            json=request.model_dump(),
            timeout=req_timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("translated_text") or data.get("translatedText") or data.get("text") or ""

    raise ValueError(f"未知翻译服务：{provider}")

