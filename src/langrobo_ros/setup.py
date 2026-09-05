from setuptools import find_packages, setup

package_name = 'langrobo_ros'

setup(
    name=package_name,
    version='1.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/config', ['config/agent_params.yaml']),
        ('share/' + package_name + '/launch', ['launch/brain_launch.py',
                                                'launch/studio_voice_launch.py']),
        ('share/' + package_name + '/systemd', [
            'systemd/langrobo-brain.service',
            'systemd/langrobo-microros.service',
            'systemd/langrobo-discovery.service',
        ]),
    ],
    install_requires=[
        'setuptools',
        # The brain itself: pip install -e src/langrobo_core (see requirements.txt)
        'langrobo-core',
    ],
    zip_safe=True,
    maintainer='rakhi24',
    maintainer_email='sumanasomineni09@gmail.com',
    description='ROS2 shim for the LangRobo brain (agent_node + bridge + launch)',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'agent_node = langrobo_ros.agent_node:main',
            'wheel_odom_relay = langrobo_ros.wheel_odom_relay:main',
            'studio_voice_node = langrobo_ros.studio_voice_node:main',
        ],
    },
)
