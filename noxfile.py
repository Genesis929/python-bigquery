# Copyright 2016 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import

from functools import wraps
import pathlib
import os
import re
import shutil
import nox
import time


MYPY_VERSION = "mypy==1.6.1"
PYTYPE_VERSION = "pytype==2024.9.13"
BLACK_VERSION = "black==23.7.0"
BLACK_PATHS = (
    "benchmark",
    "docs",
    "google",
    "samples",
    "samples/tests",
    "tests",
    "noxfile.py",
    "setup.py",
)

DEFAULT_PYTHON_VERSION = "3.9"
SYSTEM_TEST_PYTHON_VERSIONS = ["3.9", "3.11", "3.12", "3.13"]
UNIT_TEST_PYTHON_VERSIONS = ["3.9", "3.11", "3.12", "3.13"]
CURRENT_DIRECTORY = pathlib.Path(__file__).parent.absolute()


def _calculate_duration(func):
    """This decorator prints the execution time for the decorated function."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.monotonic()
        result = func(*args, **kwargs)
        end = time.monotonic()
        total_seconds = round(end - start)
        hours = total_seconds // 3600  # Integer division to get hours
        remaining_seconds = total_seconds % 3600  # Modulo to find remaining seconds
        minutes = remaining_seconds // 60
        seconds = remaining_seconds % 60
        human_time = f"{hours:}:{minutes:0>2}:{seconds:0>2}"
        print(f"Session ran in {total_seconds} seconds ({human_time})")
        return result

    return wrapper


# 'docfx' is excluded since it only needs to run in 'docs-presubmit'
nox.options.sessions = [
    "unit_noextras",
    "unit",
    "system",
    "snippets",
    "cover",
    "lint",
    "lint_setup_py",
    "blacken",
    "mypy",
    "mypy_samples",
    "pytype",
    "docs",
]


def default(session, install_extras=True):
    """Default unit test session.

    This is intended to be run **without** an interpreter set, so
    that the current ``python`` (on the ``PATH``) or the version of
    Python corresponding to the ``nox`` binary the ``PATH`` can
    run the tests.
    """

    constraints_path = str(
        CURRENT_DIRECTORY / "testing" / f"constraints-{session.python}.txt"
    )

    # Install all test dependencies, then install local packages in-place.
    session.install(
        "pytest",
        "google-cloud-testutils",
        "pytest-cov",
        "pytest-xdist",
        "freezegun",
        "-c",
        constraints_path,
    )
    # We have logic in the magics.py file that checks for whether 'bigquery_magics'
    # is imported OR not. If yes, we use a context object from that library.
    # If no, we use our own context object from magics.py. In order to exercise
    # that logic (and the associated tests) we avoid installing the [ipython] extra
    # which has a downstream effect of then avoiding installing bigquery_magics.
    if install_extras and session.python == UNIT_TEST_PYTHON_VERSIONS[0]:
        install_target = ".[bqstorage,pandas,ipywidgets,geopandas,matplotlib,tqdm,opentelemetry,bigquery_v2]"
    elif install_extras:  # run against all other UNIT_TEST_PYTHON_VERSIONS
        install_target = ".[all]"
    else:
        install_target = "."
    session.install("-e", install_target, "-c", constraints_path)

    # Test with some broken "extras" in case the user didn't install the extra
    # directly. For example, pandas-gbq is recommended for pandas features, but
    # we want to test that we fallback to the previous behavior. For context,
    # see internal document go/pandas-gbq-and-bigframes-redundancy.
    if session.python == UNIT_TEST_PYTHON_VERSIONS[0]:
        session.run("python", "-m", "pip", "uninstall", "pandas-gbq", "-y")

    session.run("python", "-m", "pip", "freeze")

    # Run py.test against the unit tests.
    session.run(
        "py.test",
        "-n=8",
        "--quiet",
        "-W default::PendingDeprecationWarning",
        "--cov=google/cloud/bigquery",
        "--cov=tests/unit",
        "--cov-append",
        "--cov-config=.coveragerc",
        "--cov-report=",
        "--cov-fail-under=0",
        os.path.join("tests", "unit"),
        *session.posargs,
    )


@nox.session(python=UNIT_TEST_PYTHON_VERSIONS)
@_calculate_duration
def unit(session):
    """Run the unit test suite."""

    default(session)


@nox.session(python=[UNIT_TEST_PYTHON_VERSIONS[0], UNIT_TEST_PYTHON_VERSIONS[-1]])
@_calculate_duration
def unit_noextras(session):
    """Run the unit test suite."""

    # Install optional dependencies that are out-of-date to see that
    # we fail gracefully.
    # https://github.com/googleapis/python-bigquery/issues/933
    #
    # We only install this extra package on one of the two Python versions
    # so that it continues to be an optional dependency.
    # https://github.com/googleapis/python-bigquery/issues/1877
    if session.python == UNIT_TEST_PYTHON_VERSIONS[0]:
        session.install("pyarrow==4.0.0", "numpy==1.20.2")
    default(session, install_extras=False)


@nox.session(python=DEFAULT_PYTHON_VERSION)
@_calculate_duration
def mypy(session):
    """Run type checks with mypy."""

    session.install("-e", ".[all]")
    session.install(MYPY_VERSION)

    # Just install the dependencies' type info directly, since "mypy --install-types"
    # might require an additional pass.
    session.install(
        "types-protobuf",
        "types-python-dateutil",
        "types-requests",
        "types-setuptools",
    )
    session.run("python", "-m", "pip", "freeze")
    session.run("mypy", "-p", "google", "--show-traceback")


@nox.session(python=DEFAULT_PYTHON_VERSION)
@_calculate_duration
def pytype(session):
    """Run type checks with pytype."""
    # An indirect dependecy attrs==21.1.0 breaks the check, and installing a less
    # recent version avoids the error until a possibly better fix is found.
    # https://github.com/googleapis/python-bigquery/issues/655

    session.install("attrs==20.3.0")
    session.install("-e", ".[all]")
    session.install(PYTYPE_VERSION)
    session.run("python", "-m", "pip", "freeze")
    # See https://github.com/google/pytype/issues/464
    session.run("pytype", "-P", ".", "google/cloud/bigquery")


@nox.session(python=SYSTEM_TEST_PYTHON_VERSIONS)
@_calculate_duration
def system(session):
    """Run the system test suite."""

    constraints_path = str(
        CURRENT_DIRECTORY / "testing" / f"constraints-{session.python}.txt"
    )

    # Sanity check: Only run system tests if the environment variable is set.
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""):
        session.skip("Credentials must be set via environment variable.")

    # Use pre-release gRPC for system tests.
    # Exclude version 1.49.0rc1 which has a known issue.
    # See https://github.com/grpc/grpc/pull/30642
    session.install("--pre", "grpcio!=1.49.0rc1", "-c", constraints_path)

    # Install all test dependencies, then install local packages in place.
    session.install(
        "pytest",
        "psutil",
        "pytest-xdist",
        "google-cloud-testutils",
        "-c",
        constraints_path,
    )
    if os.environ.get("GOOGLE_API_USE_CLIENT_CERTIFICATE", "") == "true":
        # mTLS test requires pyopenssl and latest google-cloud-storage
        session.install("google-cloud-storage", "pyopenssl")
    else:
        session.install("google-cloud-storage", "-c", constraints_path)

    # Data Catalog needed for the column ACL test with a real Policy Tag.
    session.install("google-cloud-datacatalog", "-c", constraints_path)

    # Resource Manager needed for test with a real Resource Tag.
    session.install("google-cloud-resource-manager", "-c", constraints_path)

    if session.python in ["3.11", "3.12"]:
        extras = "[bqstorage,ipywidgets,pandas,tqdm,opentelemetry]"
    else:
        extras = "[all]"
    session.install("-e", f".{extras}", "-c", constraints_path)

    # Test with some broken "extras" in case the user didn't install the extra
    # directly. For example, pandas-gbq is recommended for pandas features, but
    # we want to test that we fallback to the previous behavior. For context,
    # see internal document go/pandas-gbq-and-bigframes-redundancy.
    if session.python == SYSTEM_TEST_PYTHON_VERSIONS[0]:
        session.run("python", "-m", "pip", "uninstall", "pandas-gbq", "-y")

    # print versions of all dependencies
    session.run("python", "-m", "pip", "freeze")

    # Run py.test against the system tests.
    session.run(
        "py.test",
        "-n=auto",
        "--quiet",
        "-W default::PendingDeprecationWarning",
        os.path.join("tests", "system"),
        *session.posargs,
    )


@nox.session(python=DEFAULT_PYTHON_VERSION)
@_calculate_duration
def mypy_samples(session):
    """Run type checks with mypy."""

    session.install("pytest")
    for requirements_path in CURRENT_DIRECTORY.glob("samples/*/requirements.txt"):
        session.install("-r", str(requirements_path))
    session.install(MYPY_VERSION)

    # requirements.txt might include this package. Install from source so that
    # we can author samples with unreleased features.
    session.install("-e", ".[all]")

    # Just install the dependencies' type info directly, since "mypy --install-types"
    # might require an additional pass.
    session.install(
        "types-mock",
        "types-pytz",
        "types-protobuf!=4.24.0.20240106",  # This version causes an error: 'Module "google.oauth2" has no attribute "service_account"'
        "types-python-dateutil",
        "types-requests",
        "types-setuptools",
    )

    session.run("python", "-m", "pip", "freeze")

    session.run(
        "mypy",
        "--config-file",
        str(CURRENT_DIRECTORY / "samples" / "mypy.ini"),
        "--no-incremental",  # Required by warn-unused-configs from mypy.ini to work
        "samples/",
    )


@nox.session(python=SYSTEM_TEST_PYTHON_VERSIONS)
@_calculate_duration
def snippets(session):
    """Run the snippets test suite."""

    constraints_path = str(
        CURRENT_DIRECTORY / "testing" / f"constraints-{session.python}.txt"
    )

    # Install all test dependencies, then install local packages in place.
    session.install(
        "pytest", "pytest-xdist", "google-cloud-testutils", "-c", constraints_path
    )
    session.install("google-cloud-storage", "-c", constraints_path)
    session.install("grpcio", "-c", constraints_path)

    if session.python in ["3.11", "3.12"]:
        extras = (
            "[bqstorage,pandas,ipywidgets,geopandas,tqdm,opentelemetry,bigquery_v2]"
        )
    else:
        extras = "[all]"
    session.install("-e", f".{extras}", "-c", constraints_path)
    session.run("python", "-m", "pip", "freeze")

    # Run py.test against the snippets tests.
    # Skip tests in samples/snippets, as those are run in a different session
    # using the nox config from that directory.
    session.run(
        "py.test", "-n=auto", os.path.join("docs", "snippets.py"), *session.posargs
    )
    session.run(
        "py.test",
        "-n=auto",
        "samples",
        "-W default::PendingDeprecationWarning",
        "--ignore=samples/desktopapp",
        "--ignore=samples/magics",
        "--ignore=samples/geography",
        "--ignore=samples/notebooks",
        "--ignore=samples/snippets",
        *session.posargs,
    )


@nox.session(python=DEFAULT_PYTHON_VERSION)
@_calculate_duration
def cover(session):
    """Run the final coverage report.

    This outputs the coverage report aggregating coverage from the unit
    test runs (not system test runs), and then erases coverage data.
    """

    session.install("coverage", "pytest-cov")
    session.run("python", "-m", "pip", "freeze")
    session.run("coverage", "report", "--show-missing", "--fail-under=100")
    session.run("coverage", "erase")


@nox.session(python=SYSTEM_TEST_PYTHON_VERSIONS)
@_calculate_duration
def prerelease_deps(session):
    """Run all tests with prerelease versions of dependencies installed.

    https://github.com/googleapis/python-bigquery/issues/95
    """
    # Because we test minimum dependency versions on the minimum Python
    # version, the first version we test with in the unit tests sessions has a
    # constraints file containing all dependencies and extras.
    with open(
        CURRENT_DIRECTORY
        / "testing"
        / f"constraints-{UNIT_TEST_PYTHON_VERSIONS[0]}.txt",
        encoding="utf-8",
    ) as constraints_file:
        constraints_text = constraints_file.read()

    # Ignore leading whitespace and comment lines.
    deps = [
        match.group(1)
        for match in re.finditer(
            r"^\s*(\S+)(?===\S+)", constraints_text, flags=re.MULTILINE
        )
    ]

    session.install(*deps)

    session.install(
        "--pre",
        "--upgrade",
        "freezegun",
        "google-cloud-datacatalog",
        "google-cloud-resource-manager",
        "google-cloud-storage",
        "google-cloud-testutils",
        "psutil",
        "pytest",
        "pytest-xdist",
        "pytest-cov",
    )

    # PyArrow prerelease packages are published to an alternative PyPI host.
    # https://arrow.apache.org/docs/developers/python.html#installing-nightly-packages
    session.install(
        "--extra-index-url",
        "https://pypi.anaconda.org/scientific-python-nightly-wheels/simple",
        "--prefer-binary",
        "--pre",
        "--upgrade",
        "pyarrow",
    )
    session.install(
        "--pre",
        "--upgrade",
        "IPython",
        "ipykernel",
        "ipywidgets",
        "tqdm",
        "git+https://github.com/pypa/packaging.git",
        "pandas",
    )

    session.install(
        "--pre",
        "--upgrade",
        "--no-deps",
        "google-api-core",
        "google-cloud-bigquery-storage",
        "google-cloud-core",
        "google-resumable-media",
        "db-dtypes",
        "grpcio",
        "protobuf",
    )

    # Ensure that this library is installed from source
    session.install("-e", ".", "--no-deps")

    # Print out prerelease package versions.
    session.run("python", "-m", "pip", "freeze")

    # Run all tests, except a few samples tests which require extra dependencies.
    session.run(
        "py.test",
        "-n=auto",
        "tests/unit",
        "-W default::PendingDeprecationWarning",
    )

    session.run(
        "py.test",
        "-n=auto",
        "tests/system",
        "-W default::PendingDeprecationWarning",
    )

    session.run(
        "py.test",
        "-n=auto",
        "samples/tests",
        "-W default::PendingDeprecationWarning",
    )


@nox.session(python=DEFAULT_PYTHON_VERSION)
@_calculate_duration
def lint(session):
    """Run linters.

    Returns a failure if the linters find linting errors or sufficiently
    serious code quality issues.
    """

    session.install("flake8", BLACK_VERSION)
    session.install("-e", ".")
    session.run("python", "-m", "pip", "freeze")
    session.run("flake8", os.path.join("google", "cloud", "bigquery"))
    session.run("flake8", "tests")
    session.run("flake8", os.path.join("docs", "samples"))
    session.run("flake8", os.path.join("docs", "snippets.py"))
    session.run("flake8", "benchmark")
    session.run("black", "--check", *BLACK_PATHS)


@nox.session(python=DEFAULT_PYTHON_VERSION)
@_calculate_duration
def lint_setup_py(session):
    """Verify that setup.py is valid (including RST check)."""

    session.install("docutils", "Pygments")
    session.run("python", "-m", "pip", "freeze")
    session.run("python", "setup.py", "check", "--restructuredtext", "--strict")


@nox.session(python=DEFAULT_PYTHON_VERSION)
@_calculate_duration
def blacken(session):
    """Run black.
    Format code to uniform standard.
    """

    session.install(BLACK_VERSION)
    session.run("python", "-m", "pip", "freeze")
    session.run("black", *BLACK_PATHS)


@nox.session(python="3.10")
@_calculate_duration
def docs(session):
    """Build the docs."""

    session.install(
        # We need to pin to specific versions of the `sphinxcontrib-*` packages
        # which still support sphinx 4.x.
        # See https://github.com/googleapis/sphinx-docfx-yaml/issues/344
        # and https://github.com/googleapis/sphinx-docfx-yaml/issues/345.
        "sphinxcontrib-applehelp==1.0.4",
        "sphinxcontrib-devhelp==1.0.2",
        "sphinxcontrib-htmlhelp==2.0.1",
        "sphinxcontrib-qthelp==1.0.3",
        "sphinxcontrib-serializinghtml==1.1.5",
        "sphinx==4.5.0",
        "alabaster",
        "recommonmark",
    )
    session.install("google-cloud-storage")
    session.install("-e", ".[all]")

    shutil.rmtree(os.path.join("docs", "_build"), ignore_errors=True)
    session.run("python", "-m", "pip", "freeze")
    session.run(
        "sphinx-build",
        "-W",  # warnings as errors
        "-T",  # show full traceback on exception
        "-N",  # no colors
        "-b",
        "html",
        "-d",
        os.path.join("docs", "_build", "doctrees", ""),
        os.path.join("docs", ""),
        os.path.join("docs", "_build", "html", ""),
    )


@nox.session(python="3.10")
@_calculate_duration
def docfx(session):
    """Build the docfx yaml files for this library."""

    session.install("-e", ".")
    session.install(
        # We need to pin to specific versions of the `sphinxcontrib-*` packages
        # which still support sphinx 4.x.
        # See https://github.com/googleapis/sphinx-docfx-yaml/issues/344
        # and https://github.com/googleapis/sphinx-docfx-yaml/issues/345.
        "sphinxcontrib-applehelp==1.0.4",
        "sphinxcontrib-devhelp==1.0.2",
        "sphinxcontrib-htmlhelp==2.0.1",
        "sphinxcontrib-qthelp==1.0.3",
        "sphinxcontrib-serializinghtml==1.1.5",
        "gcp-sphinx-docfx-yaml",
        "alabaster",
        "recommonmark",
    )

    shutil.rmtree(os.path.join("docs", "_build"), ignore_errors=True)
    session.run("python", "-m", "pip", "freeze")
    session.run(
        "sphinx-build",
        "-T",  # show full traceback on exception
        "-N",  # no colors
        "-D",
        (
            "extensions=sphinx.ext.autodoc,"
            "sphinx.ext.autosummary,"
            "docfx_yaml.extension,"
            "sphinx.ext.intersphinx,"
            "sphinx.ext.coverage,"
            "sphinx.ext.napoleon,"
            "sphinx.ext.todo,"
            "sphinx.ext.viewcode,"
            "recommonmark"
        ),
        "-b",
        "html",
        "-d",
        os.path.join("docs", "_build", "doctrees", ""),
        os.path.join("docs", ""),
        os.path.join("docs", "_build", "html", ""),
    )
