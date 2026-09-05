from setuptools import find_packages, setup

package_name = 'pi5_voice_pkg'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/voice_launch.py']),
        ('share/' + package_name + '/config', ['config/voice_params.yaml']),
    ],
    install_requires=[
        'setuptools',
        'numpy',
        'sounddevice',        # mic capture / speaker playback (PortAudio)
        'webrtcvad',          # VAD framing
        'faster-whisper',     # local STT provider
        'kokoro-onnx',        # local TTS provider
        'onnxruntime',        # kokoro + openwakeword inference
        'openwakeword',       # acoustic wake-word detector
        'requests',           # sarvam REST (STT/TTS/translate)
        'websockets',         # soniox STT websocket
        'python-dotenv',      # load API keys from .env
    ],
    zip_safe=True,
    maintainer='rakhi24',
    maintainer_email='sumanasomineni09@gmail.com',
    description='CPU-only STT/TTS for the Pi5 (same /voice/* wire protocol as the Jetson voice_pkg)',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'tts_node = pi5_voice_pkg.tts_node:main',
            'stt_node = pi5_voice_pkg.stt_node:main',
        ],
    },
)
