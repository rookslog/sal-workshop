from pathlib import Path

import pytest
from test_page_save import _live_page, _save_question

pytestmark = pytest.mark.browser


def test_f9_own_served_page_can_save(browser_name: str, comment_round: Path) -> None:
    with _live_page(browser_name, comment_round) as (opened, _, root):
        _save_question(opened[0], comment_round, root)
