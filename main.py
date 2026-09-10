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
# Build Video Filter
# ---------------------------------------------------------------------------


def build_video_filter(
    image_count: int,
    image_duration: float,
    transition_duration: float,
    fps: int,
) -> str:
    if image_count < 2:
        raise RuntimeError('At least two images are required for transitions.')

    if transition_duration <= 0:
        raise RuntimeError('transition_seconds must be greater than 0.')

    if transition_duration >= image_duration:
        raise RuntimeError(
            'transition_seconds must be shorter than image_duration_seconds.'
        )

    filters: list[str] = []

    for index in range(image_count):
        filters.append(
            f'[{index}:v]'
            f'fps={fps},'
            f'settb=AVTB,'
            f'format=yuv420p,'
            f'setpts=PTS-STARTPTS'
            f'[v{index}]'
        )

    offset: float = image_duration - transition_duration

    filters.append(
        f'[v0][v1]'
        f'xfade=transition=fade:duration={transition_duration}:offset={offset}'
        f'[x1]'
    )

    for index in range(2, image_count):
        offset += image_duration - transition_duration

        filters.append(
            f'[x{index - 1}][v{index}]'
            f'xfade=transition=fade:duration={transition_duration}:offset={offset}'
            f'[x{index}]'
        )

    final_stream: str = f'x{image_count - 1}'

    filters.append(f'[{final_stream}]format=yuv420p[vout]')

    return ';'.join(filters)


# ---------------------------------------------------------------------------
# Build FFmpeg command
# ---------------------------------------------------------------------------


def build_command(
    images: list[Path],
    audio_file: Path,
    config: dict[str, int | str],
    output_file: Path,
) -> list[str]:
    duration_seconds: float = float(config['duration_hours']) * 3600
    image_duration: float = float(config['image_duration_seconds'])
    transition_duration: float = float(config['transition_seconds'])
    fps: int = int(config['fps'])

    image_step: float = image_duration - transition_duration

    required_image_count: int = int(duration_seconds / image_step) + 1

    repeated_images: list[Path] = [
        images[index % len(images)] for index in range(required_image_count)
    ]

    video_filter: str = build_video_filter(
        image_count=len(repeated_images),
        image_duration=image_duration,
        transition_duration=transition_duration,
        fps=fps,
    )

    command: list[str] = [
        'ffmpeg',
        '-y',
    ]

    for image in repeated_images:
        command.extend([
            '-loop',
            '1',
            '-t',
            str(image_duration),
            '-i',
            str(image),
        ])

    command.extend([
        '-stream_loop',
        '-1',
        '-i',
        str(audio_file),
    ])

    command.extend([
        '-filter_complex',
        video_filter,
        '-map',
        '[vout]',
        '-map',
        f'{len(repeated_images)}:a',
        '-t',
        str(duration_seconds),
    ])

    command.extend([
        '-c:v',
        'libx264',
        '-preset',
        str(config.get('preset', 'medium')),
        '-crf',
        str(config.get('crf', 20)),
        '-pix_fmt',
        'yuv420p',
    ])

    command.extend([
        '-c:a',
        'aac',
        '-b:a',
        str(config.get('audio_bitrate', '192k')),
        '-movflags',
        '+faststart',
        str(output_file),
    ])

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

        images = normalize_images(images, config)

        print_summary(
            audio_file,
            images,
            config,
            output_file,
        )

        command: list[str] = build_command(
            images,
            audio_file,
            config,
            output_file,
        )

        print('Starting FFmpeg...')
        print()

        subprocess.run(
            command,
            check=True,
        )

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
