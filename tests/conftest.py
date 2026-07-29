import json
import sys
from pathlib import Path

import pytest

# Make the ``src`` layout importable without installing the package.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def member_rows():
    return load_fixture("membercouncil.json")


@pytest.fixture
def period_rows():
    return load_fixture("periods.json")


@pytest.fixture
def periods(period_rows):
    from wd_parliament.parliament import periods_from_rows

    return periods_from_rows(period_rows)


@pytest.fixture
def members(member_rows):
    from wd_parliament.parliament import members_from_rows

    return members_from_rows(member_rows, active_only=False)
