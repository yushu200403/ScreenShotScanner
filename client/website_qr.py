"""生成手机打开服务器网站的二维码。"""

from ipaddress import ip_address
from urllib.parse import urlparse

import qrcode
from qrcode.image.pil import PilImage

from connection import ClientConnection


def website_qr(server_url, target_size=160):
    address = ClientConnection.validate_server_url(server_url)
    host = urlparse(address).hostname or ""
    local_only = host.lower().rstrip(".") == "localhost"
    try:
        local_only = local_only or ip_address(host).is_loopback or ip_address(host).is_unspecified
    except ValueError:
        pass
    if local_only:
        raise ValueError("当前地址只能在这台电脑打开，请改用手机可访问的服务器地址。")
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4,
                       image_factory=PilImage)
    qr.add_data(address + "/")
    try:
        qr.make(fit=True)
    except qrcode.exceptions.DataOverflowError as exc:
        raise ValueError("服务器地址过长，无法生成二维码，请使用较短的域名。") from exc
    qr.box_size = max(1, int(target_size) // (qr.modules_count + 8))
    return qr.make_image(fill_color="black", back_color="white").get_image()
