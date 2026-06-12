from setuptools import find_packages, setup

package_name = 'ai_agent'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/config', ['config/agent_params.yaml']),
        ('share/' + package_name + '/launch', ['launch/agent.launch.py']),
    ],
    install_requires=[
        'setuptools',
        'langchain-mcp-adapters',
        'langgraph',
        'langchain-core',
        'langchain-openai',
        'langchain-anthropic',
        'langchain-google-genai',
        'langchain-ollama',
        'langchain-community',
        'opencv-python-headless',
        'typing-extensions',
        'python-dotenv',
    ],
    zip_safe=True,
    maintainer='rakhi24',
    maintainer_email='rakhi24@todo.todo',
    description='LangGraph ReAct brain for the distributed robot',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'agent_node = ai_agent.agent_node:main',
        ],
    },
)
