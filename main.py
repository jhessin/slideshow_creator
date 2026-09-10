import json
import subprocess
import sys
from pathlib import Path
from typing import Dict

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent
AUDIO_DIR = PROJECT_DIR / 'audio'
IMAGE_DIR = PROJECT_DIR / 'images'
OUTPUT_DIR = PROJECT_DIR / 'output'
CONFIG_FILE = PROJECT_DIR / 'config.json'


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def load_config() -> Dict:
    if not CONFIG_FILE.exists():
        raise RuntimeError(f'Configuration file not found:\n{CONFIG_FILE}')

    with CONFIG_FILE.open('r', encoding='utf-8') as file:
        return json.load(file)


# ---------------------------------------------------------------------------
# Find media
# ---------------------------------------------------------------------------


def find_audio() -> Path:
    supported_extensions = {
        '.mp3',
        '.wav',
        '.m4a',
        '.aac',
        '.flac',
        '.ogg',
    }

    files = sorted(
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
    supported_extensions = {
        '.jpg',
        '.jpeg',
        '.png',
        '.webp',
        '.bmp',
    }

    images = sorted(
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
        result = subprocess.run(
            ['ffmpeg', '-version'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )

        first_line = result.stdout.splitlines()[0]

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
# Build video filter
# ---------------------------------------------------------------------------


def build_filter(images, config) -> str:
    width = int(config['width'])
    height = int(config['height'])
    fps = int(config['fps'])
    image_duration = float(config['image_duration_seconds'])

    filter_parts = []

    for index in range(len(images)):
        filter_parts.append(
            f'[{index + 1}:v]'
            f'scale={width}:{height}:'
            f'force_original_aspect_ratio=decrease,'
            f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,'
            f'setsar=1,'
            f'fps={fps},'
            f'trim=duration={image_duration},'
            f'setpts=PTS-STARTPTS'
            f'[v{index}]'
        )

    input_streams = ''.join(f'[v{index}]' for index in range(len(images)))

    filter_parts.append(
        f'{input_streams}concat=n={len(images)}:v=1:a=0,setpts=PTS-STARTPTS[video]'
    )

    return ';'.join(filter_parts)


# ---------------------------------------------------------------------------
# Build FFmpeg command
# ---------------------------------------------------------------------------


def build_command(audio_file, images, config, output_file) -> list[str]:
    duration_seconds = float(config['duration_hours']) * 3600

    command = [
        'ffmpeg',
        '-y',
        # -------------------------------------------------------------------
        # Audio
        # -------------------------------------------------------------------
        '-stream_loop',
        '-1',
        '-i',
        str(audio_file),
        # -------------------------------------------------------------------
        # Images
        #
        # Each image becomes a normal FFmpeg input.
        # -loop 1 makes FFmpeg continuously generate frames from the image.
        # -------------------------------------------------------------------
    ]

    for image in images:
        command.extend([
            '-loop',
            '1',
            '-i',
            str(image),
        ])

    # -----------------------------------------------------------------------
    # Filter graph
    # -----------------------------------------------------------------------

    filter_complex = build_filter(images, config)

    command.extend([
        '-filter_complex',
        filter_complex,
        # Video from our filter graph.
        '-map',
        '[video]',
        # Audio from input 0.
        '-map',
        '0:a',
        # Exact output duration.
        '-t',
        str(duration_seconds),
        # -------------------------------------------------------------------
        # Video encoding
        # -------------------------------------------------------------------
        '-c:v',
        'libx264',
        '-preset',
        config.get('preset', 'medium'),
        '-crf',
        str(config.get('crf', 20)),
        '-pix_fmt',
        'yuv420p',
        # -------------------------------------------------------------------
        # Audio encoding
        # -------------------------------------------------------------------
        '-c:a',
        'aac',
        '-b:a',
        config.get('audio_bitrate', '192k'),
        # -------------------------------------------------------------------
        # MP4 optimization
        # -------------------------------------------------------------------
        '-movflags',
        '+faststart',
        str(output_file),
    ])

    return command


# ---------------------------------------------------------------------------
# Display information
# ---------------------------------------------------------------------------


def print_summary(
    audio_file: Path, images: list[Path], config: Dict, output_file: Path
):
    duration_hours = float(config['duration_hours'])
    duration_seconds = duration_hours * 3600

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


def main():
    try:
        print('Checking FFmpeg...')
        check_ffmpeg()

        print('Loading configuration...')
        config: Dict = load_config()

        print('Finding audio...')
        audio_file: Path = find_audio()

        print('Finding images...')
        images = find_images()

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        output_file = OUTPUT_DIR / config['output_filename']

        print_summary(
            audio_file,
            images,
            config,
            output_file,
        )

        command = build_command(
            audio_file,
            images,
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
