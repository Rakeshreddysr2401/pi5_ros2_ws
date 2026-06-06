from setuptools import find_packages, setup

package_name = 'robot_brain'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/brain_launch.py']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='rakhi24',
    maintainer_email='rakhi24@todo.todo',
    description='Launch package for the Pi5 robot brain (micro-ROS agent + LangGraph node)',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [],
    },
)
