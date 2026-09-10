import subprocess
import sys
from pathlib import Path
from typing import Dict

import json5

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_DIR: Path = Path(__file__).resolve().parent

AUDIO_DIR: Path = PROJECT_DIR / 'audio'
IMAGE_DIR: Path = PROJECT_DIR / 'images'

TEMP_DIR: Path = PROJECT_DIR / '.temp'
OUTPUT_DIR: Path = PROJECT_DIR / 'output'

CONFIG_FILE: Path = PROJECT_DIR / 'config.jsonc'


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def load_config() -> Dict:
    if not CONFIG_FILE.exists():
        raise RuntimeError(f'Configuration file not found:\n{CONFIG_FILE}')

    with CONFIG_FILE.open('r', encoding='utf-8') as file:
        return json5.load(file)


# ---------------------------------------------------------------------------
# Find media
# ---------------------------------------------------------------------------


def find_audio() -> Path:
    supported_extensions: set[str] = {
        '.mp3',
        '.wav',
        '.m4a',
        '.aac',
        '.flac',
        '.ogg',
    }

    files: list[Path] = sorted(
        [
            file
            for file in AUDIO_DIR.iterdir()
            if file.is_file() and file.suffix.lower() in supported_extensions
        ],
        key=lambda path: path.name.lower(),
    )

    if not files:
        raise RuntimeError(f'No audio file found in:\n{AUDIO_DIR}')

    if len(files) > 1:
        raise RuntimeError(
            'More than one audio file was found.\n'
            'Please put only one audio file in the audio directory.\n\n'
            + '\n'.join(f'  {file.name}' for file in files)
        )

    return files[0]


def find_images() -> list[Path]:
    supported_extensions: set[str] = {
        '.jpg',
        '.jpeg',
        '.png',
        '.webp',
        '.bmp',
    }

    images: list[Path] = sorted(
        [
            file
            for file in IMAGE_DIR.iterdir()
            if file.is_file() and file.suffix.lower() in supported_extensions
        ],
        key=lambda path: path.name.lower(),
    )

    if not images:
        raise RuntimeError(f'No images found in:\n{IMAGE_DIR}')

    return images


# ---------------------------------------------------------------------------
# FFmpeg
# ---------------------------------------------------------------------------


def check_ffmpeg() -> None:
    try:
        result: subprocess.CompletedProcess[str] = subprocess.run(
            ['ffmpeg', '-version'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )

        first_line: str = result.stdout.splitlines()[0]

        print(f'FFmpeg found: {first_line}')

    except FileNotFoundError:
        raise RuntimeError(
            'FFmpeg was not found.\n\n'
            'Make sure FFmpeg is installed and that its bin directory '
            'is included in your PATH.'
        )

    except subprocess.CalledProcessError:
        raise RuntimeError('FFmpeg was found but could not be executed.')


# ---------------------------------------------------------------------------
# Normalize Images
# ---------------------------------------------------------------------------


def normalize_images(
    images: list[Path],
    config: dict[str, int | str],
) -> list[Path]:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    width: int = int(config['width'])
    height: int = int(config['height'])

    normalized_images: list[Path] = []

    for index, image in enumerate(images):
        output_image: Path = TEMP_DIR / f'{index:04d}.png'

        command: list[str] = [
            'ffmpeg',
            '-y',
            '-i',
            str(image),
            '-vf',
            (
                f'scale={width}:{height}:'
                'force_original_aspect_ratio=decrease,'
                f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,'
                'setsar=1,'
                'format=yuv420p'
            ),
            '-frames:v',
            '1',
            str(output_image),
        ]

        subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )

        normalized_images.append(output_image)

    return normalized_images


# ---------------------------------------------------------------------------
# Build Concat File
# ---------------------------------------------------------------------------


def create_concat_file(
    images: list[Path],
    config: dict[str, int | str],
    concat_file: Path,
) -> None:
    duration_seconds: float = float(config['duration_hours']) * 3600
    image_duration: float = float(config['image_duration_seconds'])

    cycle_duration: float = len(images) * image_duration
    cycles: int = int(duration_seconds // cycle_duration) + 1

    with concat_file.open('w', encoding='utf-8') as file:
        file.write('ffconcat version 1.0\n')

        for _ in range(cycles):
            for image in images:
                image_path: str = image.resolve().as_posix()

                file.write(f"file '{image_path}'\n")
                file.write(f'duration {image_duration}\n')

        # FFmpeg requires the final file to be repeated so that
        # the duration of the final image is honored.
        final_image: str = images[-1].resolve().as_posix()
        file.write(f"file '{final_image}'\n")


# ---------------------------------------------------------------------------
# Build FFmpeg command
# ---------------------------------------------------------------------------


def build_command(
    audio_file: Path,
    config: dict[str, int | str],
    output_file: Path,
    concat_file: Path,
) -> list[str]:
    duration_seconds: float = float(config['duration_hours']) * 3600

    command: list[str] = [
        'ffmpeg',
        '-y',
        # -------------------------------------------------------------------
        # Repeating slideshow input.
        # -------------------------------------------------------------------
        '-f',
        'concat',
        '-safe',
        '0',
        '-i',
        str(concat_file),
        # -------------------------------------------------------------------
        # Repeating Audio
        # -------------------------------------------------------------------
        '-stream_loop',
        '-1',
        '-i',
        str(audio_file),
        # -------------------------------------------------------------------
        # Video and audio streams.
        # -------------------------------------------------------------------
        '-map',
        '0:v',
        '-map',
        '1:a',
        # -------------------------------------------------------------------
        # Stop the final output at the configured duration
        # -------------------------------------------------------------------
        '-t',
        str(duration_seconds),
        # -------------------------------------------------------------------
        # Video Encoding
        # -------------------------------------------------------------------
        '-c:v',
        'libx264',
        '-preset',
        str(config.get('preset', 'medium')),
        '-crf',
        str(config.get('crf', 20)),
        '-pix_fmt',
        'yuv420p',
        # -------------------------------------------------------------------
        # Audio Encoding
        # -------------------------------------------------------------------
        '-c:a',
        'aac',
        '-b:a',
        str(config.get('audio_bitrate', '192k')),
        '-movflags',
        '+faststart',
        str(output_file),
    ]

    return command


# ---------------------------------------------------------------------------
# Display information
# ---------------------------------------------------------------------------


def print_summary(
    audio_file: Path,
    images: list[Path],
    config: Dict,
    output_file: Path,
) -> None:
    duration_hours: float = float(config['duration_hours'])
    duration_seconds: float = duration_hours * 3600

    print()
    print('=' * 60)
    print('VIDEO BUILD')
    print('=' * 60)
    print()

    print(f'Audio:       {audio_file.name}')
    print(f'Images:      {len(images)}')
    print(f'Resolution:  {config["width"]}x{config["height"]}')
    print(f'FPS:         {config["fps"]}')
    print(f'Image time:  {config["image_duration_seconds"]} seconds')
    print(f'Duration:    {duration_hours:g} hours')
    print(f'Duration:    {duration_seconds:g} seconds')
    print(f'Output:      {output_file}')

    print()
    print('Image order:')

    for index, image in enumerate(images, start=1):
        print(f'  {index:02d}. {image.name}')

    print()
    print('=' * 60)
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    try:
        print('Checking FFmpeg...')
        check_ffmpeg()

        print('Loading configuration...')
        config: Dict = load_config()

        print('Finding audio...')
        audio_file: Path = find_audio()

        print('Finding images...')
        images: list[Path] = find_images()

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        output_file: Path = OUTPUT_DIR / config['output_filename']
        concat_file: Path = TEMP_DIR / '.slideshow_concat.txt'

        images = normalize_images(images, config)

        create_concat_file(images, config, concat_file)

        print_summary(
            audio_file,
            images,
            config,
            output_file,
        )

        command: list[str] = build_command(
            audio_file,
            config,
            output_file,
            concat_file,
        )

        print('Starting FFmpeg...')
        print()

        subprocess.run(
            command,
            check=True,
        )

        if concat_file.exists():
            concat_file.unlink()

        print()
        print('=' * 60)
        print('VIDEO COMPLETE')
        print('=' * 60)
        print()
        print('Output file:')
        print(output_file)
        print()

    except subprocess.CalledProcessError as error:
        print()
        print('=' * 60)
        print('FFMPEG FAILED')
        print('=' * 60)
        print()
        print(f'Exit code: {error.returncode}')
        print()

        sys.exit(error.returncode)

    except Exception as error:
        print()
        print('=' * 60)
        print('ERROR')
        print('=' * 60)
        print()
        print(error)
        print()

        sys.exit(1)


if __name__ == '__main__':
    main()
