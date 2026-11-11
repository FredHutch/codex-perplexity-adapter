"""Inject experimental checkpoint strategies without editing production modules."""
from contextlib import contextmanager
from unittest.mock import patch

from codex_perplexity_adapter import app as production
from codex_perplexity_adapter.continuation import (ContinuationManager, Checkpoint,
    has_local_tools, outstanding_calls)


class ResearchManager(ContinuationManager):
    def __init__(self, *args, strategy, **kwargs):
        super().__init__(*args, **kwargs)
        self.strategy = strategy

    def select(self, key, payload, fingerprints):
        if self.strategy == 'replay':
            selected = super().select(key, payload, fingerprints)
            if selected is not payload and has_local_tools(selected['input']):
                self.invalidate(key)
                return payload
            return selected
        self.expire()
        entry = self.entries.get(key)
        items, config, instructions = fingerprints
        if (entry and entry.config == config and entry.instructions == instructions
                and len(items) > len(entry.items) and items[:len(entry.items)] == entry.items):
            self.entries.move_to_end(key)
            return {**payload, 'previous_response_id': entry.response_id,
                    'input': payload['input'][len(entry.items):]}
        self.invalidate(key)
        return payload

    def accept(self, key, response, fingerprints, output, input_items):
        if self.strategy == 'replay':
            self.invalidate(key)
            if not outstanding_calls(input_items + output):
                self.commit(key, response, fingerprints, output)
            return
        if self.strategy == 'anchor' and (has_local_tools(output) or outstanding_calls(input_items + output)):
            # Preserve only an existing anchor matched during selection. Never
            # replace it with a response dispatching tools.
            return
        self.invalidate(key)
        ident = response.get('id')
        if (response.get('status') != 'completed' or not isinstance(ident, str)
                or not 0 < len(ident) <= 256):
            return
        items, config, instructions = fingerprints
        from codex_perplexity_adapter.continuation import canonical_item
        entry = Checkpoint(ident, items + tuple(self.digest(canonical_item(i)) for i in output),
                           config, instructions, self.clock())
        self.expire()
        if entry.size > self.max_bytes:
            return
        while self.entries and (len(self.entries) >= self.max_chats or self.cache_bytes + entry.size > self.max_bytes):
            self.invalidate(next(iter(self.entries)))
        self.entries[key] = entry
        self.cache_bytes += entry.size


class ResearchCapture(production.ContinuationCapture):
    def finalize(self, delivered):
        if not delivered or self.invalid or self.pending is None:
            if self.key is not None:
                self.manager.invalidate(self.key)
            return
        response, output = self.pending
        prepared = self.image_prepared
        if self.budget_status is not None and prepared is not None and prepared.resized_images:
            self.budget_status.image_requests += 1
            self.budget_status.resized_images += prepared.resized_images
            self.budget_status.image_saved_bytes += prepared.saved_bytes
        if self.key is not None:
            self.manager.saved_bytes += self.saved_bytes
            self.manager.accept(self.key, response, self.fingerprints, output, self.input_items)


@contextmanager
def experiment(strategy):
    if strategy not in {'replay', 'direct', 'anchor'}:
        raise ValueError('Unknown qualification strategy')
    def manager(*args, **kwargs):
        return ResearchManager(*args, strategy=strategy, **kwargs)
    with patch.object(production, 'ContinuationManager', manager), \
         patch.object(production, 'ContinuationCapture', ResearchCapture), \
         patch.object(production, 'RATE_LIMIT_RETRIES', 0):
        yield


def create_research_app(strategy, settings, transport):
    # Keep the context active while serving, because request handlers resolve
    # the capture class at request time. Use one strategy per process/context.
    return production.create_app(settings, transport)
