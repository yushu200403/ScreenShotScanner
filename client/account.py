import requests

from connection import ClientConnection
from version import APP_VERSION


class AccountError(RuntimeError):
    def __init__(self, message, code="request_failed"):
        super().__init__(message)
        self.code = code


class AccountClient:
    def __init__(self, server_url, token=""):
        self.server_url = ClientConnection.validate_server_url(server_url)
        self.token = token

    def request(self, method, path, payload=None):
        headers = {"X-Client-Version": APP_VERSION}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        try:
            with requests.request(method, self.server_url + "/api/desktop/" + path,
                                  json=payload, headers=headers, timeout=(10, 25), allow_redirects=False) as response:
                if response.headers.get("X-Server-Version") != APP_VERSION:
                    raise AccountError("客户端与服务器版本不同，请更新后再登录", "version_mismatch")
                try:
                    data = response.json()
                except ValueError:
                    raise AccountError("服务器没有正确回应，请检查服务器地址")
                if not isinstance(data, dict):
                    raise AccountError("服务器没有正确回应，请稍后重试")
                if not 200 <= response.status_code < 300:
                    detail = data.get("error", {})
                    raise AccountError(detail.get("message", "操作未成功，请稍后重试"), detail.get("code", "request_failed"))
                return data
        except requests.RequestException as exc:
            raise AccountError("暂时连不上服务器，请检查网络和服务器地址后重试", "network_error") from exc

    def login(self, username, password, device_id, device_name):
        data = self.request("POST", "login", {"username": username, "password": password,
                                               "device_id": device_id, "device_name": device_name})
        self.token = data["session_token"]
        return data

    def me(self):
        return self.request("GET", "me")

    def presets(self):
        return self.request("GET", "presets")["items"]

    def save_settings(self, name, preset_id):
        return self.request("PUT", "settings", {"name": name, "preset_id": preset_id})

    def logout(self):
        return self.request("POST", "logout")
