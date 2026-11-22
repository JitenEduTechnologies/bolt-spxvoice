from contextvars import ContextVar

run_id_var: ContextVar[int | None] = ContextVar("run_id", default=None)
org_id_var: ContextVar[int | None] = ContextVar("org_id", default=None)
turn_var: ContextVar[str | None] = ContextVar("turn", default=None)
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_current_run_id(run_id: int | None):
    return run_id_var.set(run_id)


def get_current_run_id() -> int | None:
    return run_id_var.get()


def set_current_org_id(org_id: int | None):
    return org_id_var.set(org_id)


def get_current_org_id() -> int | None:
    return org_id_var.get()


def set_current_request_id(request_id: str | None):
    return request_id_var.set(request_id)


def get_current_request_id() -> str | None:
    return request_id_var.get()

