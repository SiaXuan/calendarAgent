"""Reminder-title resolution in api/tasks.py — pure logic, no LLM/network.

Covers the "blank/default-named reminder" filter and its robustness: a reminder
whose title is the OS default ("新提醒事项") but whose notes have real content
must be kept (using the notes), not dropped.
"""
from api.tasks import _reminder_effective_title


class TestReminderEffectiveTitle:
    def test_normal_title_kept(self):
        assert _reminder_effective_title("买牛奶", None) == "买牛奶"

    def test_placeholder_no_body_skipped(self):
        assert _reminder_effective_title("新提醒事项", None) is None       # zh default name
        assert _reminder_effective_title("新提醒事项", "   ") is None       # whitespace-only notes
        assert _reminder_effective_title("", None) is None                 # empty
        assert _reminder_effective_title("New Reminder", None) is None     # en default name

    def test_placeholder_with_body_uses_first_line(self):
        # blank title but real notes → keep it, titled from the note body
        assert (
            _reminder_effective_title("新提醒事项", "打电话给房东确认续租\n合同8月到期")
            == "打电话给房东确认续租"
        )

    def test_body_first_line_truncated_to_80(self):
        out = _reminder_effective_title("新提醒事项", "x" * 200)
        assert out is not None and len(out) == 80

    def test_whitespace_title_trimmed(self):
        assert _reminder_effective_title("  写周报  ", None) == "写周报"

    def test_english_placeholder_case_insensitive(self):
        assert _reminder_effective_title("reminder", None) is None
        assert _reminder_effective_title("REMINDER", "buy groceries") == "buy groceries"
