"""Process-local numeric counters; never retain request content."""

class BudgetStatus:
    def __init__(self):
        self.active = {}
        self.next_id = 0
        self.latest = 0
        self.warnings = [0, 0, 0]
        self.failures = 0

    def snapshot(self):
        return {
            "active_requests": len(self.active),
            "latest_completed_request_bytes": self.latest,
            "largest_active_request_bytes": max(self.active.values(), default=0),
            "warnings_10mb": self.warnings[0], "warnings_12mb": self.warnings[1],
            "warnings_15mb": self.warnings[2], "size_failures": self.failures,
        }


class StatusMiddleware:
    def __init__(self, app, status):
        self.app, self.status = app, status

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope.get("path") != "/v1/responses"
                or scope.get("method", "POST") != "POST"):
            return await self.app(scope, receive, send)
        self.status.next_id += 1
        key = self.status.next_id
        self.status.active[key] = 0
        state = scope.setdefault("state", {})
        state["budget_request_id"] = key
        try:
            await self.app(scope, receive, send)
        finally:
            size = self.status.active.pop(key)
            self.status.latest = size

