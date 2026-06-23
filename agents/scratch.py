"""
ScheduleScratch — the run-scoped working copy the agent mutates (Phase: 核心闭环 S2).

Design (from plan's 状态感知 + 分级 commit sections):
- The agent NEVER touches live `schedule_store`. It works on a ScheduleScratch
  that is a frozen copy of `schedule_store[date]` taken at run start (+ its version).
- Every mutation tool changes the scratch and returns the *rendered projection*
  so the LLM always reads current state (no drift).
- At run end, `scratch.diff()` vs the frozen base feeds `classify_impact()` —
  the DETERMINISTIC gate (agent can't touch it) that routes minor→auto-commit,
  major→Proposal.

Blocks are addressed by a stable scratch-local id ("b1","b2"…) assigned at load
by start-time order, so move/remove can target a block unambiguously even after
its time changes.
"""
from datetime import date, datetime, timedelta

from pydantic import BaseModel

from models.schedule import BlockType, TimeBlock


class BlockChange(BaseModel):
    op: str            # "move" | "remove" | "add"
    scratch_id: str
    title: str
    block_type: str
    cross_day: bool = False
    touches_synced: bool = False
    # Human-readable before/after for the UI. move→both; remove→from; add→to.
    # Includes the date prefix only for cross-day moves (else just HH:MM).
    from_time: str | None = None
    to_time: str | None = None


class ScheduleDiff(BaseModel):
    changes: list[BlockChange]

    @property
    def moved(self) -> list[BlockChange]:
        return [c for c in self.changes if c.op == "move"]

    @property
    def removed(self) -> list[BlockChange]:
        return [c for c in self.changes if c.op == "remove"]

    @property
    def added(self) -> list[BlockChange]:
        return [c for c in self.changes if c.op == "add"]

    @property
    def is_empty(self) -> bool:
        return not self.changes


# Block types the agent is allowed to move/remove. Fixed/meal are anchors.
_MOVABLE = {BlockType.scheduled, BlockType.suggested}


class ScheduleScratch:
    def __init__(
        self,
        target_date: date,
        base_blocks: list[TimeBlock],
        base_version: int,
        *,
        work_start_hour: int = 8,
        work_end_hour: int = 22,
        energy_curve: list[float] | None = None,
        synced_keys: set[str] | None = None,
    ):
        self.target_date = target_date
        self.base_version = base_version
        self.work_start_hour = work_start_hour
        self.work_end_hour = work_end_hour
        self.energy_curve = energy_curve or [0.5] * 24
        # block_key set of things already written to iCloud (touch=major).
        self.synced_keys = synced_keys or set()

        ordered = sorted(base_blocks, key=lambda b: b.start)
        self._base: dict[str, TimeBlock] = {
            f"b{i+1}": b.model_copy(deep=True) for i, b in enumerate(ordered)
        }
        self.blocks: dict[str, TimeBlock] = {
            sid: b.model_copy(deep=True) for sid, b in self._base.items()
        }
        self._next = len(self._base) + 1

    # ── projection ───────────────────────────────────────────────────────────

    def render(self) -> str:
        """Compact one-line-per-block view the LLM reads each step."""
        lines = [f"日程 ({self.target_date.isoformat()}):"]
        for sid, b in sorted(self.blocks.items(), key=lambda kv: kv[1].start):
            t = f"{b.start.strftime('%H:%M')}-{b.end.strftime('%H:%M')}"
            load = f"[{b.cognitive_load.value}/{b.task_kind.value}]" if b.cognitive_load and b.task_kind else ""
            ddl = f" ddl{b.deadline.isoformat()}" if b.deadline else ""
            synced = " ✓iCloud" if self._key(b) in self.synced_keys else ""
            lines.append(f"  [{sid}] {t} {load} {b.title}{ddl} ({b.block_type.value}){synced}")
        if len(lines) == 1:
            lines.append("  (空)")
        return "\n".join(lines)

    @staticmethod
    def _key(b: TimeBlock) -> str:
        return b.task_id and f"{b.task_id}::{b.title}" or f"{b.start.isoformat()}::{b.title}"

    def get(self, scratch_id: str) -> TimeBlock | None:
        return self.blocks.get(scratch_id)

    # ── mutations (each returns the new projection) ───────────────────────────

    def move_block(self, scratch_id: str, new_start: datetime) -> str:
        b = self.blocks.get(scratch_id)
        if b is None:
            raise ValueError(f"no block {scratch_id}")
        if b.block_type not in _MOVABLE:
            raise ValueError(f"{scratch_id} 是 {b.block_type.value}，不可移动")
        dur = b.end - b.start
        self.blocks[scratch_id] = b.model_copy(update={"start": new_start, "end": new_start + dur})
        return self.render()

    def remove_block(self, scratch_id: str) -> str:
        b = self.blocks.get(scratch_id)
        if b is None:
            raise ValueError(f"no block {scratch_id}")
        if b.block_type not in _MOVABLE:
            raise ValueError(f"{scratch_id} 是 {b.block_type.value}，不可删除")
        del self.blocks[scratch_id]
        return self.render()

    def add_fixed_event(self, title: str, start: datetime, end: datetime) -> str:
        sid = f"b{self._next}"
        self._next += 1
        self.blocks[sid] = TimeBlock(
            start=start, end=end, block_type=BlockType.fixed, title=title,
        )
        return self.render()

    def defer_block(self, scratch_id: str, new_start: datetime) -> str:
        """Move to a different day (validated as cross-day in the diff)."""
        return self.move_block(scratch_id, new_start)

    # ── diff vs frozen base ───────────────────────────────────────────────────

    def diff(self) -> ScheduleDiff:
        changes: list[BlockChange] = []
        base_ids = set(self._base)
        cur_ids = set(self.blocks)

        for sid in base_ids - cur_ids:  # removed
            b = self._base[sid]
            changes.append(BlockChange(
                op="remove", scratch_id=sid, title=b.title,
                block_type=b.block_type.value,
                touches_synced=self._key(b) in self.synced_keys,
                from_time=self._fmt(b.start, cross_day=False),
            ))
        for sid in cur_ids - base_ids:  # added
            b = self.blocks[sid]
            changes.append(BlockChange(
                op="add", scratch_id=sid, title=b.title,
                block_type=b.block_type.value,
                to_time=self._fmt(b.start, cross_day=False),
            ))
        for sid in base_ids & cur_ids:  # possibly moved
            old, new = self._base[sid], self.blocks[sid]
            if old.start != new.start or old.end != new.end:
                cross = old.start.date() != new.start.date()
                changes.append(BlockChange(
                    op="move", scratch_id=sid, title=new.title,
                    block_type=new.block_type.value,
                    cross_day=cross,
                    touches_synced=self._key(old) in self.synced_keys,
                    from_time=self._fmt(old.start, cross_day=cross),
                    to_time=self._fmt(new.start, cross_day=cross),
                ))
        return ScheduleDiff(changes=changes)

    @staticmethod
    def _fmt(dt: datetime, *, cross_day: bool) -> str:
        """HH:MM, prefixed with M/D when the change spans days."""
        return dt.strftime("%-m/%-d %H:%M") if cross_day else dt.strftime("%H:%M")

    def committed_blocks(self) -> list[TimeBlock]:
        return sorted(self.blocks.values(), key=lambda b: b.start)


def classify_impact(diff: ScheduleDiff) -> str:
    """
    DETERMINISTIC gate (agent cannot touch this). Returns "minor" | "major".

    major if: any delete / any add of a fixed event / touches a synced block /
    any cross-day move / ≥2 blocks moved. else minor (≤1 same-day move).
    """
    if diff.removed:
        return "major"
    if any(c.block_type == BlockType.fixed.value for c in diff.added):
        return "major"
    if any(c.touches_synced for c in diff.changes):
        return "major"
    if any(c.cross_day for c in diff.moved):
        return "major"
    if len(diff.moved) >= 2:
        return "major"
    return "minor"
