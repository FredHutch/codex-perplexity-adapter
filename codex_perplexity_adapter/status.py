"""Process-local numeric counters; never retain request content."""

class BudgetStatus:
    def __init__(self):
        self.active = {}
        self.next_id = 0
        self.latest = 0
        self.warnings = [0, 0, 0]
        self.failures = 0
        self.image_requests = self.resized_images = self.image_saved_bytes = 0

    def snapshot(self):
        return {
            "active_requests": len(self.active),
            "latest_completed_request_bytes": self.latest,
            "largest_active_request_bytes": max(self.active.values(), default=0),
            "warnings_10mb": self.warnings[0], "warnings_12mb": self.warnings[1],
            "warnings_15mb": self.warnings[2], "size_failures": self.failures,
            "image_resize_requests": self.image_requests, "resized_images": self.resized_images,
            "image_saved_request_bytes": self.image_saved_bytes,
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
        delivered = False
        completed = False
        async def track_delivery(message):
            nonlocal delivered
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                delivered = True
        try:
            await self.app(scope, receive, track_delivery)
            completed = True
        finally:
            size = self.status.active.pop(key)
            self.status.latest = size
            lease = state.pop("continuation_lease", None)
            outcome = state.pop("continuation_outcome", None)
            if lease is not None:
                manager, session_key = lease
                try:
                    if outcome is not None:
                        outcome.finalize(delivered and completed)
                    else:
                        manager.invalidate(session_key)
                finally:
                    manager.release(session_key)
            elif outcome is not None:
                outcome.finalize(delivered and completed)

