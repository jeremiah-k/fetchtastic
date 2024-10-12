from setuptools import setup, find_packages

setup(
    name='fetchtastic',
    version='0.1.0',
    packages=find_packages(),
    install_requires=[
        'requests',
        'pick',
        'PyYAML',
        'urllib3',
    ],
    entry_points={
        'console_scripts': [
            'fetchtastic=app.cli:main',
        ],
    },
    # Include other metadata as needed
)
