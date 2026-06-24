"""WuYou（一坞邮）应用配置模块。

通过 pydantic-settings 从环境变量（前缀 ``WUYOU_``）和 ``.env`` 文件中加载所有运行
时配置。每个配置项都有默认值，生产环境可通过环境变量覆盖敏感信息（如 OAuth
密钥、SMTP 密码等）。

``Settings`` 类同时提供派生的路径属性（``database_path``、``secret_key_path``、
``installed_plugins_dir``），确保使用方无需自行拼接路径。

模块级函数 ``get_settings()`` 是带 lru_cache 的单例工厂：
第一次调用时实例化 ``Settings`` 并自动创建必需的目录，后续调用返回同一实例。
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目仓库根目录（backend/app/core/config.py -> 向上 3 级）
REPO_ROOT = Path(__file__).resolve().parents[3]
# backend 根目录（backend/app/core/config.py -> 向上 2 级）
BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """WuYou 运行时配置。

    所有字段均可以通过 ``WUYOU_<FIELD_NAME>`` 环境变量或 ``.env`` 文件设置。
    未设置的字段使用下方默认值。
    """

    # ``env_prefix`` 让所有环境变量自动加上 WUYOU_ 前缀，
    # ``extra="ignore"`` 忽略 .env 中的未知字段，避免启动报错。
    model_config = SettingsConfigDict(env_prefix="WUYOU_", env_file=".env", extra="ignore")

    # ── 基础 ──
    app_name: str = "WuYou"
    """应用名称（显示在 swagger / 页面标题中）。"""
    environment: Literal["development", "production", "test"] = "development"
    """运行环境。非 production 时验证码会以 dev_code 字段返回方便调试。"""
    data_dir: Path = Field(default=BACKEND_ROOT / "app" / "data")
    """持久化数据根目录（数据库、密钥文件、附件、插件均放于此）。"""
    database_name: str = "wuyou.sqlite3"
    """SQLite 数据库文件名（相对于 data_dir）。"""
    session_days: int = 14
    """登录 session 有效期（天）。"""
    allow_origins: str = "*"
    """CORS 允许的来源，逗号分隔或单个星号。"""

    # ── 插件 ──
    local_plugin_community_dir: Path = Field(default=REPO_ROOT / "plugin-community" / "local")
    """本地插件社区仓库路径。"""

    # ── 远程同步 ──
    default_remote_sync_endpoint: str = "http://localhost:8787/wuyou"
    """默认的远程同步 HTTP 端点地址。"""
    default_translation_provider: str = "mymemory"
    """默认翻译服务提供商标识。"""

    # ── 遥测 ──
    telemetry_enabled_default: bool = False
    """新用户的遥测开关默认值（false=默认关闭）。"""
    telemetry_remote_url: str = ""
    """遥测事件远程上传端点 URL（留空则不上传）。"""

    # ── 邮件同步 ──
    max_mail_fetch: int = 50
    """单次同步最多拉取的邮件数量（用于全量同步时的 backfill）。"""
    request_timeout_seconds: int = 20
    """HTTP 请求通用超时秒数。"""
    sync_mode: Literal["inprocess", "worker"] = "inprocess"
    """同步模式：inprocess=内置线程调度，worker=外部 worker 触发。"""
    sync_interval_minutes: int = 30
    """全量扫描调度间隔（分钟）。"""
    sync_remote_interval_minutes: int = 15
    """远程同步（device-to-device）调度间隔（分钟）。"""
    sync_concurrency: int = 2
    """inprocess 模式下同时执行的同步任务数上限。"""
    sync_folders_default: list[str] = Field(
        default_factory=lambda: ["inbox", "sent", "trash", "archive", "junk"]
    )
    """用户未手动选择文件夹时默认同步的角色列表。"""

    # ── 热更新 ──
    hot_reload_enabled: bool = True
    """是否启用静态资源热更新 watcher。"""
    hot_reload_interval_seconds: int = 5
    """热更新扫描间隔（秒）。"""

    # ── 系统 SMTP（用于发送验证码邮件） ──
    system_smtp_host: str = ""
    """系统级 SMTP 服务器地址。"""
    system_smtp_port: int = 465
    """系统级 SMTP 端口。"""
    system_smtp_ssl: bool = True
    """系统级 SMTP 是否使用 SSL（True=SMTP_SSL，False=STARTTLS）。"""
    system_smtp_username: str = ""
    """系统级 SMTP 登录用户名。"""
    system_smtp_password: str = ""
    """系统级 SMTP 登录密码（注意：此为敏感信息，生产环境务必通过环境变量注入）。"""
    system_from_address: str = "noreply@wuyou.local"
    """系统发出的邮件的 From 地址。"""

    # ── SMS ──
    sms_provider: str = ""
    """短信服务商：""=禁用 / "console"=控制台打印 / "custom"=自定义 HTTP 回调。"""
    sms_api_key: str = ""
    """短信 API Key（aliyun/tencent 待实现）。"""
    sms_api_secret: str = ""
    """短信 API Secret（注意：生产环境务必通过环境变量注入）。"""
    sms_sign_name: str = "WuYou"
    """短信签名名称。"""
    sms_template_id: str = ""
    """短信模板 ID。"""
    sms_custom_url: str = ""
    """自定义短信回调 URL（sms_provider=custom 时生效）。"""

    # ── OAuth2 ──
    oauth_client_id: str = ""
    """通用 OAuth client_id（保留）。"""
    oauth_client_secret: str = ""
    """通用 OAuth client_secret（保留）。"""
    oauth_redirect_uri: str = "http://localhost:8000/api/auth/oauth/callback"
    """OAuth 回调地址，必须与各服务商控制台配置一致。"""
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = ""
    oauth_ms_client_id: str = ""
    oauth_ms_client_secret: str = ""
    oauth_qq_client_id: str = ""
    oauth_qq_client_secret: str = ""
    oauth_yahoo_client_id: str = ""
    oauth_yahoo_client_secret: str = ""
    oauth_zoho_client_id: str = ""
    oauth_zoho_client_secret: str = ""

    # ── 派生属性 ──
    @property
    def database_path(self) -> Path:
        """SQLite 数据库文件的完整路径（= data_dir / database_name）。"""
        return self.data_dir / self.database_name

    @property
    def secret_key_path(self) -> Path:
        """本地加密密钥文件路径（= data_dir / secret.key），用于 Fernet 加密邮箱密码等敏感字段。"""
        return self.data_dir / "secret.key"

    @property
    def installed_plugins_dir(self) -> Path:
        """已安装插件的存放目录（= data_dir / plugins / installed）。"""
        return self.data_dir / "plugins" / "installed"


@lru_cache
def get_settings() -> Settings:
    """返回 Settings 单例。

    第一次调用时实例化并确保 data_dir、installed_plugins_dir 目录存在。
    后续调用返回同一个缓存的 Settings 实例。
    """
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.installed_plugins_dir.mkdir(parents=True, exist_ok=True)
    return settings
