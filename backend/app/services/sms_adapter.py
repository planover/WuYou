"""WuYou 短信适配器抽象层。

通过策略模式封装多种短信发送后端：
- ``ConsoleSmsAdapter`` — 控制台打印（开发/调试用）
- ``CustomSmsAdapter`` — 自定义 HTTP POST 回调

未来可扩展 aliyun / tencent 等云服务商适配器。

工厂函数 ``get_sms_adapter(settings)`` 根据 ``sms_provider`` 配置返回对应适配器实例。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx


class SmsAdapter(ABC):
    """Abstract base for SMS delivery backends."""

    @abstractmethod
    async def send(self, phone: str, code: str) -> bool:
        """Deliver *code* to *phone*.  Returns True on success."""
        ...


class ConsoleSmsAdapter(SmsAdapter):
    """开发/调试用适配器：将验证码打印到标准输出。"""

    async def send(self, phone: str, code: str) -> bool:
        print(f"[SMS] To: {phone}  Code: {code}")
        return True


class CustomSmsAdapter(SmsAdapter):
    """自定义 HTTP 回调适配器：POST JSON ``{phone, code}`` 到用户指定的 URL。

    Args:
        url: 回调 HTTP 端点地址。
    """

    def __init__(self, url: str) -> None:
        self._url = url

    async def send(self, phone: str, code: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    self._url,
                    json={"phone": phone, "code": code},
                )
                return resp.is_success
        except Exception:
            return False


def get_sms_adapter(settings) -> SmsAdapter:
    """根据 settings.sms_provider 返回对应的 SMS 适配器。

    支持的 provider 值：
    - ``"custom"`` → CustomSmsAdapter（HTTP POST 到 sms_custom_url）
    - ``"console"`` / ``""`` / 未知值 → ConsoleSmsAdapter（打印到 stdout）

    Args:
        settings: Settings 实例。

    Returns:
        SmsAdapter 子类实例。
    """
    provider = getattr(settings, "sms_provider", "") or ""

    if provider == "custom":
        url = getattr(settings, "sms_custom_url", "")
        if url:
            return CustomSmsAdapter(url)
        return ConsoleSmsAdapter()

    # console, empty string, or any unrecognised value → fallback to console
    return ConsoleSmsAdapter()
