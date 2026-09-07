from werkzeug.serving import WSGIRequestHandler


class WebSocketRequestHandler(WSGIRequestHandler):
    def send_response(self, code, message=None):
        self.websocket_finished = code == 200 and self.headers.get("Upgrade", "").lower() == "websocket"
        super().send_response(code, message)

    def end_headers(self):
        if getattr(self, "websocket_finished", False):
            self._headers_buffer = []
            self.close_connection = True
            return
        super().end_headers()
