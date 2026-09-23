"""Типизированные ошибки инструментов: агент получает код, а не traceback."""


class ToolError(Exception):
    """Базовая ошибка инструмента. code — машинный код, message — текст для аналитика."""

    code = "tool_error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        return {"type": type(self).__name__, "code": self.code, "message": self.message, **self.details}


class UnknownGidError(ToolError):
    code = "unknown_gid"

    def __init__(self, gid):
        super().__init__(f"gid {gid} нет в nodes_roles.csv: такого клиента нет в выгрузке", gid=gid)


class UnknownClusterError(ToolError):
    code = "unknown_cluster"

    def __init__(self, cluster_id):
        super().__init__(f"cluster_id {cluster_id} нет в clusters.csv", cluster_id=cluster_id)


class InvalidArgumentError(ToolError):
    code = "invalid_argument"


class ToolNotAllowedError(ToolError):
    code = "tool_not_allowed"

    def __init__(self, name):
        super().__init__(f"инструмента {name!r} нет в списке разрешённых", tool=name)


class OutputsNotFoundError(ToolError):
    code = "outputs_not_found"
