import pytest

from rark.core.task import Task
from rark.core.transitions import LifecycleState


def test_initial_state():
    task = Task(name="test", priority=5)
    assert task.state == LifecycleState.PENDING


def test_pending_to_active():
    task = Task(name="test", priority=5)
    task.transition(LifecycleState.ACTIVE)
    assert task.state == LifecycleState.ACTIVE


def test_active_to_paused_to_active():
    task = Task(name="test", priority=5)
    task.transition(LifecycleState.ACTIVE)
    task.transition(LifecycleState.PAUSED)
    assert task.state == LifecycleState.PAUSED
    task.transition(LifecycleState.ACTIVE)
    assert task.state == LifecycleState.ACTIVE


def test_active_to_completed():
    task = Task(name="test", priority=5)
    task.transition(LifecycleState.ACTIVE)
    task.transition(LifecycleState.COMPLETED)
    assert task.state == LifecycleState.COMPLETED


def test_active_to_failed():
    task = Task(name="test", priority=5)
    task.transition(LifecycleState.ACTIVE)
    task.transition(LifecycleState.FAILED)
    assert task.state == LifecycleState.FAILED


def test_pending_to_cancelled():
    task = Task(name="test", priority=5)
    task.transition(LifecycleState.CANCELLED)
    assert task.state == LifecycleState.CANCELLED


def test_invalid_pending_to_completed():
    task = Task(name="test", priority=5)
    with pytest.raises(ValueError):
        task.transition(LifecycleState.COMPLETED)


def test_invalid_pending_to_paused():
    task = Task(name="test", priority=5)
    with pytest.raises(ValueError):
        task.transition(LifecycleState.PAUSED)


def test_terminal_state_no_transition():
    task = Task(name="test", priority=5)
    task.transition(LifecycleState.ACTIVE)
    task.transition(LifecycleState.COMPLETED)
    with pytest.raises(ValueError):
        task.transition(LifecycleState.ACTIVE)


def test_updated_at_changes_on_transition():
    task = Task(name="test", priority=5)
    original = task.updated_at
    task.transition(LifecycleState.ACTIVE)
    assert task.updated_at >= original
