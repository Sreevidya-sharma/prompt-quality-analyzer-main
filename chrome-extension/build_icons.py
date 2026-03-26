from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent

INPUT = ROOT / "logo.png"
SIZES = (16, 32, 48, 128)

def main():
    if not INPUT.exists():
        raise FileNotFoundError(f"{INPUT} not found")

    img = Image.open(INPUT).convert("RGBA")

    for size in SIZES:
        resized = img.resize((size, size), Image.Resampling.LANCZOS)
        output_path = ROOT / f"logo-{size}.png"
        resized.save(output_path, format="PNG", optimize=True)
        print(f"Generated {output_path}")

if __name__ == "__main__":
    main()