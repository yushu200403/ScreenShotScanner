"""绘制与网页 favicon.svg 一致的截图问答标识。"""

from PIL import Image, ImageDraw


def render_icon(size=256):
    scale = 16
    image = Image.new("RGBA", (48 * scale, 48 * scale))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, 48 * scale - 1, 48 * scale - 1),
                           radius=13 * scale, fill="#147d73")
    for points in (((14, 11), (11, 11), (11, 19)),
                   ((34, 11), (37, 11), (37, 19)),
                   ((11, 30), (11, 37), (19, 37)),
                   ((37, 30), (37, 37), (29, 37))):
        draw.line([(x * scale, y * scale) for x, y in points],
                  fill="#e5f6ef", width=3 * scale)
        for x, y in (points[0], points[-1]):
            radius = 1.5 * scale
            draw.ellipse((x * scale - radius, y * scale - radius,
                          x * scale + radius, y * scale + radius), fill="#e5f6ef")
    draw.polygon([(x * scale, y * scale) for x, y in
                  ((17, 19), (31, 19), (31, 28), (23, 28), (17, 33))], fill="white")
    return image.resize((size, size), Image.Resampling.LANCZOS)


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="生成客户端 Windows 图标")
    parser.add_argument("output", type=Path, help="图标输出路径")
    output = parser.parse_args().output
    output.parent.mkdir(parents=True, exist_ok=True)
    render_icon().save(output, format="ICO", sizes=[(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
