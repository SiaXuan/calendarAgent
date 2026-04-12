"""Tests for agents/scheduler_agent.py — no LLM calls, no network."""
import json
from datetime import date

import pytest

from agents.scheduler_agent import generate_schedule
from models.schedule import BlockType, FreeWindow
from models.task import CognitiveLoad, Subtask


TARGET_DATE = date(2026, 4, 11)


def _window(start_h: int, end_h: int, energy: float) -> FreeWindow:
    return FreeWindow(
        start_hour=start_h,
        end_hour=end_h,
        duration_minutes=(end_h - start_h) * 60,
        energy_score=energy,
    )


def _subtask(title: str, load: CognitiveLoad, minutes: int) -> Subtask:
    return Subtask(
        parent_id="task_test",
        title=title,
        cognitive_load=load,
        estimated_minutes=minutes,
        suggested_date=TARGET_DATE,
    )


class TestGenerateSchedule:
    def test_basic_scheduling(self):
        windows = [_window(9, 12, 0.9), _window(15, 17, 0.8)]
        subtasks = [_subtask("Write code", CognitiveLoad.deep, 60)]
        result = generate_schedule(subtasks, windows, [], TARGET_DATE)
        assert len(result.blocks) == 1
        assert result.blocks[0].title == "Write code"

    def test_deep_work_requires_high_energy(self):
        """Deep task should NOT be placed in a low-energy window."""
        windows = [_window(9, 11, 0.4)]  # energy 0.4 < threshold 0.7
        subtasks = [_subtask("Deep focus", CognitiveLoad.deep, 60)]
        result = generate_schedule(subtasks, windows, [], TARGET_DATE)
        assert len(result.blocks) == 0
        assert len(result.unscheduled) == 1

    def test_light_task_placed_in_low_energy_window(self):
        """Light task should be placed in a moderate energy window (0.3 threshold)."""
        windows = [_window(14, 15, 0.35)]
        subtasks = [_subtask("Admin", CognitiveLoad.light, 30)]
        result = generate_schedule(subtasks, windows, [], TARGET_DATE)
        assert len(result.blocks) == 1

    def test_no_block_within_1h_of_sleep(self):
        """Blocks must not be placed within 1h of sleep_start."""
        windows = [_window(22, 23, 0.8)]
        subtasks = [_subtask("Late task", CognitiveLoad.medium, 45)]
        # sleep_start = 23 → no scheduling after 22:00
        result = generate_schedule(subtasks, windows, [], TARGET_DATE, sleep_start_hour=23)
        assert len(result.blocks) == 0
        assert len(result.unscheduled) == 1

    def test_single_block_under_90_minutes(self):
        """No single block exceeds 90 minutes."""
        windows = [_window(9, 13, 0.9)]
        subtasks = [_subtask("Big task", CognitiveLoad.deep, 120)]  # 120 > 90
        result = generate_schedule(subtasks, windows, [], TARGET_DATE)
        for b in result.blocks:
            duration = (b.end - b.start).total_seconds() / 60
            assert duration <= 90

    def test_buffer_between_blocks(self):
        """Consecutive blocks should have at least 10-minute gaps."""
        windows = [_window(9, 13, 0.9)]
        subtasks = [
            _subtask("Task A", CognitiveLoad.deep, 60),
            _subtask("Task B", CognitiveLoad.deep, 60),
        ]
        result = generate_schedule(subtasks, windows, [], TARGET_DATE)
        if len(result.blocks) == 2:
            gap = (result.blocks[1].start - result.blocks[0].end).total_seconds() / 60
            assert gap >= 10

    def test_unscheduled_when_no_window_fits(self):
        """Tasks that can't fit go to unscheduled list."""
        windows = [_window(9, 10, 0.9)]  # only 60 min capacity
        subtasks = [
            _subtask("A", CognitiveLoad.deep, 50),
            _subtask("B", CognitiveLoad.deep, 50),
            _subtask("C", CognitiveLoad.deep, 50),
        ]
        result = generate_schedule(subtasks, windows, [], TARGET_DATE)
        assert len(result.unscheduled) > 0

    def test_blocks_are_sorted_by_start_time(self):
        windows = [_window(9, 12, 0.9), _window(15, 17, 0.8)]
        subtasks = [
            _subtask("Morning task", CognitiveLoad.deep, 60),
            _subtask("Afternoon task", CognitiveLoad.medium, 60),
        ]
        result = generate_schedule(subtasks, windows, [], TARGET_DATE)
        starts = [b.start for b in result.blocks]
        assert starts == sorted(starts)

    def test_mock_data_integration(self):
        """End-to-end using mock task data."""
        with open("tests/mock_data/tasks_sample.json") as f:
            raw_tasks = json.load(f)

        subtasks = []
        for t in raw_tasks:
            hours = t["estimated_hours"]
            load = CognitiveLoad(t["cognitive_load"])
            minutes = int(hours * 60)
            subtasks.append(
                Subtask(
                    parent_id=t["id"],
                    title=t["title"],
                    cognitive_load=load,
                    estimated_minutes=minutes,
                    suggested_date=TARGET_DATE,
                )
            )

        windows = [
            _window(8, 9, 0.75),
            _window(10, 12, 0.90),
            _window(14, 16, 0.80),
            _window(18, 20, 0.60),
        ]

        result = generate_schedule(subtasks, windows, [], TARGET_DATE)
        total = len(result.blocks) + len(result.unscheduled)
        assert total == len(subtasks)
        for b in result.blocks:
            assert b.block_type == BlockType.scheduled
