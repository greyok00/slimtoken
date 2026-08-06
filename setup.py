"""setup.py — slimtoken. Pure Python, no compilation step."""
from setuptools import setup

setup(
    package_dir={"": "src"},
    packages=["slimtoken"],
    zip_safe=False,
)