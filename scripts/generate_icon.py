"""Generate the Windows ICO used by PyInstaller and Inno Setup."""

from pathlib import Path

from PIL import Image, ImageDraw


def make_icon(size: int) -> Image.Image:
    scale = size / 256
    image = Image.new("RGBA", (size, size), (15, 20, 27, 255))
    draw = ImageDraw.Draw(image)

    def box(values: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return tuple(round(value * scale) for value in values)

    radius = max(1, round(18 * scale))
    draw.rounded_rectangle(
        box((34, 28, 222, 228)),
        radius=radius,
        fill=(10, 26, 46, 255),
        outline=(62, 128, 163, 255),
        width=max(1, round(6 * scale)),
    )
    draw.rounded_rectangle(
        box((60, 50, 151, 194)),
        radius=max(1, round(10 * scale)),
        fill=(30, 58, 95, 255),
        outline=(232, 244, 255, 255),
        width=max(1, round(5 * scale)),
    )
    draw.polygon(
        [box((126, 50, 126, 50))[:2], box((151, 75, 151, 75))[:2], box((126, 75, 126, 75))[:2]],
        fill=(62, 128, 163, 255),
    )
    draw.rounded_rectangle(
        box((111, 65, 197, 208)),
        radius=max(1, round(10 * scale)),
        fill=(18, 35, 58, 255),
        outline=(0, 191, 255, 255),
        width=max(1, round(5 * scale)),
    )
    line_width = max(1, round(7 * scale))
    for y in (103, 128, 153):
        draw.line(box((129, y, 180, y)), fill=(168, 212, 240, 255), width=line_width)

    arrow_width = max(1, round(9 * scale))
    draw.line(box((45, 213, 93, 213)), fill=(64, 224, 208, 255), width=arrow_width)
    draw.polygon(
        [box((93, 195, 93, 195))[:2], box((112, 213, 112, 213))[:2], box((93, 231, 93, 231))[:2]],
        fill=(64, 224, 208, 255),
    )
    return image


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "build" / "icon.ico"
    target.parent.mkdir(parents=True, exist_ok=True)
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = [make_icon(size) for size in sizes]
    images[-1].save(target, format="ICO", append_images=images[:-1], sizes=[(s, s) for s in sizes])
    print(target)


if __name__ == "__main__":
    main()
