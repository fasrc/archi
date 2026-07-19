"""Loader coverage for git-collected code files.

`select_loader` decides which files can be parsed into text at embed time. If it
returns ``None`` for a suffix that git collection accepts, the file is collected,
stored, then marked ``failed`` as an ``Unsupported file format`` — a silent gap that
kept ~327 HPC example files (Slurm scripts, Fortran/C++/CUDA/R/MATLAB sources) out of
the knowledge base. These tests pin the collection->loader parity invariant.
"""

import json

import pytest
from langchain_community.document_loaders.notebook import NotebookLoader
from langchain_community.document_loaders.text import TextLoader

from src.data_manager.vectorstore.loader_utils import select_loader

# Scientific-computing suffixes the FASRC deployment configures on top of the shipped
# default (see `git_scraper.py` `code_suffixes`).
HPC_ADDITION_SUFFIXES = [
    ".sbatch",
    ".slurm",
    ".f90",
    ".f",
    ".f95",
    ".cu",
    ".r",
    ".rmd",
    ".m",
    ".jl",
    ".def",
]

# Drift guard: every plain-text code/script suffix that git collection accepts MUST
# be loadable, or the file is collected then silently fails at embed time. This is a
# maintained copy of `GitScraper`'s default `code_suffixes` plus the HPC additions
# above. `git_scraper.py` runs `get_global_config()` at import, so it cannot be
# imported into a unit test to read the list directly; keep this in sync by hand.
REQUIRED_LOADABLE_SUFFIXES = (
    [
        # GitScraper default code_suffixes:
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".java",
        ".go",
        ".rs",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".sh",
        ".sql",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".md",
        ".txt",
    ]
    + HPC_ADDITION_SUFFIXES
    + [
        # Jupyter notebooks: JSON with cell source/markdown, excluded from the
        # TextLoader set because they also carry execution-output blobs (issue #109).
        ".ipynb",
    ]
)


def _loader_for(tmp_path, name):
    """Write a small real file (some loaders read at construction) and select it."""
    p = tmp_path / name
    p.write_text("x = 1\n")
    return select_loader(str(p))


def test_slurm_job_scripts_are_loadable(tmp_path):
    # The single most-requested HPC example type; 157 of them were failing.
    assert isinstance(_loader_for(tmp_path, "submit.sbatch"), TextLoader)
    assert isinstance(_loader_for(tmp_path, "array.slurm"), TextLoader)


@pytest.mark.parametrize("suffix", HPC_ADDITION_SUFFIXES)
def test_hpc_addition_suffixes_are_loadable(tmp_path, suffix):
    loader = _loader_for(tmp_path, f"example{suffix}")
    assert loader is not None, f"{suffix} should be loadable, got None"
    assert isinstance(loader, TextLoader)


def test_suffix_matching_is_case_insensitive(tmp_path):
    # Fortran/R files are commonly uppercase (.F90, .R); select_loader lowercases.
    upper = _loader_for(tmp_path, "SOLVER.F90")
    lower = _loader_for(tmp_path, "solver.f90")
    assert upper is not None
    assert type(upper) is type(lower)


def test_unsupported_binary_types_still_return_none(tmp_path):
    # The change must extend the allow-list, not become a catch-all that text-loads
    # binaries into garbage embeddings.
    assert _loader_for(tmp_path, "figure.png") is None
    assert _loader_for(tmp_path, "archive.zip") is None


def test_notebook_files_are_loadable():
    loader = select_loader("analysis.ipynb")
    assert isinstance(loader, NotebookLoader)


def test_notebook_extracts_source_and_markdown_excludes_outputs(tmp_path):
    notebook = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["# Distinctive Markdown Heading\n"],
            },
            {
                "cell_type": "code",
                "execution_count": 1,
                "metadata": {},
                "outputs": [
                    {
                        "output_type": "stream",
                        "name": "stdout",
                        "text": ["DISTINCTIVE_STDOUT_OUTPUT_BLOB\n"],
                    }
                ],
                "source": ["distinctive_code_source_line = 1\n"],
            },
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    notebook_path = tmp_path / "analysis.ipynb"
    notebook_path.write_text(json.dumps(notebook))

    loader = select_loader(str(notebook_path))
    docs = loader.load()
    page_content = docs[0].page_content

    assert "Distinctive Markdown Heading" in page_content
    assert "distinctive_code_source_line = 1" in page_content
    assert "DISTINCTIVE_STDOUT_OUTPUT_BLOB" not in page_content


@pytest.mark.parametrize("suffix", REQUIRED_LOADABLE_SUFFIXES)
def test_every_collected_code_suffix_is_loadable(tmp_path, suffix):
    # Drift guard: any suffix git collection accepts MUST be loadable, or it fails
    # silently at embed time.
    assert (
        _loader_for(tmp_path, f"file{suffix}") is not None
    ), f"{suffix} is collected by git but has no loader"
