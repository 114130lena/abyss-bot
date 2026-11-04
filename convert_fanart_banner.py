"""Convert approved 16:9 or 16:10 fanart into Discord server-banner JPEG.

Usage: python convert_fanart_banner.py input.png -o output.jpg
Requires Pillow only; never sends a Discord API request.
"""
import argparse
from pathlib import Path
from PIL import Image, ImageOps, ImageFilter
import io

SIZE = (960, 540)

def ratio_type(width, height):
    if width < 960 or height < 540:
        return None
    ratio = width / height
    if abs(ratio - 16 / 9) <= 0.035:
        return "16:9"
    if abs(ratio - 16 / 10) <= 0.03:
        return "16:10"
    return None


def render(source_path: Path) -> bytes:
    with Image.open(source_path) as im:
        if getattr(im, "is_animated", False):
            raise ValueError("움직이는 이미지는 지원하지 않습니다.")
        im = ImageOps.exif_transpose(im)
        kind = ratio_type(*im.size)
        if not kind:
            raise ValueError("16:9 또는 16:10 이미지만 지원합니다.")
        art = im.convert("RGB")
        if kind == "16:9":
            output = ImageOps.fit(art, SIZE, method=Image.Resampling.LANCZOS)
        else:
            background = ImageOps.fit(art, SIZE, method=Image.Resampling.LANCZOS)
            background = background.filter(ImageFilter.GaussianBlur(24))
            foreground = ImageOps.contain(art, SIZE, method=Image.Resampling.LANCZOS)
            background.paste(foreground, ((SIZE[0] - foreground.width) // 2,
                                          (SIZE[1] - foreground.height) // 2))
            output = background
        data = io.BytesIO()
        output.save(data, format="JPEG", quality=89, optimize=True)
        return data.getvalue()


def convert(source: Path, output: Path) -> str:
    if not source.is_file():
        raise FileNotFoundError(f"원본 파일을 찾을 수 없습니다: {source}")
    with Image.open(source) as im:
        if getattr(im, 'is_animated', False):
            raise ValueError('움직이는 이미지는 지원하지 않습니다.')
        width, height = ImageOps.exif_transpose(im).size
    ratio = ratio_type(width, height)
    if ratio is None:
        raise ValueError(f'배너 대상이 아닙니다: {width}x{height}. 가로 960px 이상인 16:9 또는 16:10만 지원합니다.')
    converted = render(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(converted)
    return ratio


def main():
    parser = argparse.ArgumentParser(description='히요리 팬아트를 16:9 서버 배너로 변환')
    parser.add_argument('input', type=Path, help='사용 허가된 팬아트 파일')
    parser.add_argument('-o', '--output', type=Path, help='출력 JPG 경로')
    args = parser.parse_args()
    target = args.output or args.input.with_name(args.input.stem + '_banner.jpg')
    if args.input.resolve() == target.resolve():
        parser.error('원본 파일을 덮어쓸 수 없습니다.')
    kind = convert(args.input, target)
    print(f'변환 완료: {target} | 원본 {kind} → {SIZE[0]}x{SIZE[1]} (16:9)')


if __name__ == '__main__':
    main()
