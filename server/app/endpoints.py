from urllib.parse import urlsplit, urlunsplit


CHAT_PROVIDERS = {"openai_chat", "openai_responses", "deepseek"}


def base_endpoint(endpoint, provider):
    parsed = urlsplit(endpoint.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("模型端点需为完整的 HTTP/HTTPS 地址，例如 https://api.openai.com/v1")
    try:
        parsed.port
    except ValueError:
        raise ValueError("模型端点的端口无效")
    if provider not in CHAT_PROVIDERS:
        return endpoint.strip()
    if parsed.query:
        raise ValueError("基础端点不能包含查询参数，请将 API 密钥填写到独立字段")
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/response"):
        if path.endswith(suffix):
            path = path[:-len(suffix)].rstrip("/")
            break
    if not path.endswith("/v1"):
        path += "/v1"
    return urlunsplit(parsed._replace(path=path, query="", fragment=""))


def request_endpoint(endpoint, provider):
    if provider not in CHAT_PROVIDERS:
        return endpoint
    suffix = "/responses" if provider == "openai_responses" else "/chat/completions"
    return base_endpoint(endpoint, provider) + suffix
