import pytest
import zxingcpp

from website_qr import website_qr


@pytest.mark.parametrize("address", ["https://sss.im33.xyz", "https://example.com:8443/", "https://192.168.1.20"])
@pytest.mark.parametrize("size", [160, 240, 320])
def test_mobile_scanner_reads_exact_website(address, size):
    image = website_qr(address, size)
    codes = zxingcpp.read_barcodes(image.convert("RGB"))
    assert len(codes) == 1
    assert codes[0].text == address.rstrip("/") + "/"
    assert image.width <= size


@pytest.mark.parametrize("address", ["http://localhost", "https://127.0.0.1", "https://[::1]", "https://0.0.0.0"])
def test_local_only_address_has_actionable_error(address):
    with pytest.raises(ValueError, match="手机可访问"):
        website_qr(address)


@pytest.mark.parametrize("address", ["https://user:password@example.com", "https://example.com/?token=secret", "https://example.com/#secret"])
def test_qr_does_not_accept_login_information(address):
    with pytest.raises(ValueError):
        website_qr(address)
