import json
import logging
import threading
from typing import Sequence

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ReadableSpan,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from promptflow._telemetry.mdc_exporter import MdcExporter

logger = logging.getLogger(__name__)


class MemoryTraceStore:
    def __init__(self):
        self._spans = []

    def add_spans(self, spans):
        self._spans.extend(spans)

    def get_spans_from_run_id(self, run_id: str):
        return [span for span in self._spans if span.attributes["root_run_id"] == run_id]

    def pop_spans_from_run_id(self, run_id: str):
        spans = self.get_spans_from_run_id(run_id)
        for span in spans:
            self._spans.remove(span)
        return spans

    def clear(self):
        self._spans.clear()


trace_store = MemoryTraceStore()


class MemoryExporter(SpanExporter):
    """Implementation of :class:`SpanExporter` that prints spans to the
    console.

    This class can be used for diagnostic purposes. It prints the exported
    spans to the console STDOUT.
    """

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        print("exporting", spans)
        trace_store.add_spans(spans)
        return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def spans(self) -> Sequence[ReadableSpan]:
        return [json.loads(span.to_json()) for span in self._spans]


memory_exporter = MemoryExporter()


class FileExporter(SpanExporter):
    def __init__(self, file_name="traces.json"):
        self.file_name = file_name
        # Open the file in append mode
        self.file = open(file_name, "a")

    def export(self, spans):
        # Convert spans to a format suitable for JSON serialization
        span_data = [span.to_json() for span in spans]
        # Write the JSON serialized span data to the file
        for span_json in span_data:
            self.file.write(span_json + "\n")
        self.file.flush()
        return SpanExportResult.SUCCESS

    def shutdown(self):
        # Close the file when shutting down the exporter
        self.file.close()


class TreeConsoleSpanExporter:
    def __init__(self):
        # Dictionary to keep track of parent-child relationships
        self.span_tree = {}
        # Dictionary to keep track of span start times
        self.span_start_times = {}

    def export(self, spans):
        for span in spans:
            parent_id = span.parent.span_id if span.parent else None
            # Store the start time of the span for later use
            self.span_start_times[span.context.span_id] = span.start_time

            if parent_id not in self.span_tree:
                self.span_tree[parent_id] = []
            self.span_tree[parent_id].append(span)

        self._print_tree()

    def _print_tree(self, parent_id=None, level=0):
        if parent_id not in self.span_tree:
            return

        for span in self.span_tree[parent_id]:
            indent = "  " * level
            sections_to_print = [
                span.name,
                "\ttrace_id:",
                span.context.trace_id,
                "span_id:",
                span.context.span_id,
                # span.end_time - span.start_time,
            ]
            print(f"{indent}- {' '.join(str(section) for section in sections_to_print)}")
            self._print_tree(span.context.span_id, level + 1)

    def shutdown(self):
        # Perform any cleanup if necessary
        pass


_tracer_lock = threading.Lock()
_tracer_instance = None


def get_tracer(name):
    """Get the OpenTelemetry tracer instance."""

    global _tracer_instance

    with _tracer_lock:
        if _tracer_instance is None:
            resource = Resource(
                attributes={
                    SERVICE_NAME: "promptflow",
                }
            )
            provider = TracerProvider(resource=resource)

            provider.add_span_processor(BatchSpanProcessor(MdcExporter(logger=logger)))
            # These are for test usage only. Do not use in production.
            # provider.add_span_processor(SimpleSpanProcessor(FileExporter("traces.json")))
            provider.add_span_processor(SimpleSpanProcessor(TreeConsoleSpanExporter()))
            provider.add_span_processor(SimpleSpanProcessor(memory_exporter))

            trace.set_tracer_provider(provider)

            _tracer_instance = trace.get_tracer(name, tracer_provider=provider)

    return _tracer_instance