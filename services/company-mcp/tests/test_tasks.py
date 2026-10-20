import pytest

from company_mcp.errors import Forbidden, InvalidState
from company_mcp.models import TaskStatus
from company_mcp.service import CompanyService

TASK = "TASK-CreateShipment-DEVOPS-2026-001"


def assign(service):
    return service.assign_task(
        "operations-manager", "fulfillment", "CreateShipment", "DEVOPS-2026-001", "Ship order DEVOPS-2026-001"
    )


def test_assign_and_complete(service, publisher):
    task = assign(service)
    assert (task.taskId, task.status) == (TASK, TaskStatus.ASSIGNED)
    assigned = publisher.of_type("WorkAssigned")[0]["payload"]
    assert (assigned["assignedTo"], assigned["assignedBy"]) == ("fulfillment", "operations-manager")

    service.start_task("fulfillment", TASK)
    done = service.complete_task("fulfillment", TASK, "Shipment SHP-DEVOPS-2026-001 created")
    assert done.status == TaskStatus.COMPLETED
    completed = publisher.of_type("WorkCompleted")[0]["payload"]
    assert completed["assignedBy"] == "operations-manager"  # routes back to the manager


def test_duplicate_assignment_and_completion(service, publisher):
    assign(service)
    assign(service)
    service.complete_task("fulfillment", TASK, "done")
    service.complete_task("fulfillment", TASK, "done")
    assert publisher.types() == ["WorkAssigned", "WorkCompleted"]


def test_only_direct_reports_can_be_assigned(service):
    with pytest.raises(Forbidden):
        service.assign_task("accounting-manager", "fulfillment", "CreateShipment", "DEVOPS-2026-001", "not yours")


def test_only_the_assignee_completes(service):
    assign(service)
    with pytest.raises(Forbidden):
        service.complete_task("accountant", TASK, "not mine")


def test_failed_task_can_be_retried(service, publisher):
    assign(service)
    service.fail_task("fulfillment", TASK, "CARRIER_API_TIMEOUT", "Carrier did not answer", True)
    failed = publisher.of_type("WorkFailed")[0]["payload"]
    assert (failed["errorCode"], failed["retryable"]) == ("CARRIER_API_TIMEOUT", True)

    retried = assign(service)
    assert (retried.status, retried.attempt, retried.errorCode) == (TaskStatus.ASSIGNED, 2, None)
    assert publisher.types() == ["WorkAssigned", "WorkFailed", "WorkAssigned"]


def test_completed_task_cannot_fail(service):
    assign(service)
    service.complete_task("fulfillment", TASK, "done")
    with pytest.raises(InvalidState):
        service.fail_task("fulfillment", TASK, "LATE_FAILURE", "oops", False)


def test_restarted_agent_recovers_outstanding_work(service, repo, publisher):
    """Agent restart scenario: a fresh process finds the task from durable state."""
    assign(service)
    service.start_task("fulfillment", TASK)

    restarted = CompanyService(repo, publisher, service.artifacts)
    outstanding = restarted.list_my_tasks("fulfillment")  # default: ASSIGNED + IN_PROGRESS
    assert [task.taskId for task in outstanding] == [TASK]
    assert restarted.list_my_tasks("fulfillment", [TaskStatus.ASSIGNED]) == []
    assert restarted.list_my_tasks("fulfillment", [TaskStatus.COMPLETED]) == []
