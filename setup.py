"""Setup script for pymarket package."""

from setuptools import setup, find_packages

setup(
    name="pymarket",
    use_scm_version=True,
    setup_requires=["setuptools_scm"],
    packages=find_packages(exclude=["tests", "docs", ".venv", ".idea"]),
    python_requires=">=3.8",
)
