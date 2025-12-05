from __future__ import annotations

from pathlib import Path

from setuptools import setup


def read_requirements() -> list[str]:
    """Read runtime requirements from requirements.txt, if present."""
    req_file = Path(__file__).parent / "requirements.txt"
    if not req_file.exists():
        return []
    lines: list[str] = []
    for line in req_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


setup(
    name="next-pass",
    version="0.1.0",
    description=(
        "Predict the next satellite overpass (S1/S2/L8/L9) over an AOI "
        "and query OPERA products."
    ),
    author="Emre Havazli, Ines Fenni, and OPERA Disaster Response Team",
    url="https://github.com/OPERA-Cal-Val/next_pass",
    license="Apache-2.0",
    python_requires=">=3.10",
    # Top-level script module
    py_modules=["next_pass"],
    # Package with helpers (utils/*.py)
    packages=["utils"],
    # Keep deps in requirements.txt as the single source of truth
    install_requires=read_requirements(),
    entry_points={
        "console_scripts": [
            "next-pass=next_pass:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: GIS",
    ],
)
