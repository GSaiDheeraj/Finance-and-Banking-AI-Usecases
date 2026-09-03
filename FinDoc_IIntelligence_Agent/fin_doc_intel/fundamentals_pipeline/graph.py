"""LangGraph assembly for the table-based extraction pipeline.

Ported edge-for-edge from FinDoc Pypi's `fundamentals_module/graph.py`:

    find_tables          -> filter_relevant
    filter_relevant      -> classify_statement
    classify_statement   -> classify_consolidation
    classify_consolidation -> extract_line_items
    extract_line_items   -> aggregate_all -> END
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from . import nodes
from .state import PipelineState


def build_graph():
    graph = StateGraph(PipelineState)

    graph.add_node("find_tables", nodes.find_tables_node)
    graph.add_node("filter_relevant", nodes.filter_relevant)
    graph.add_node("classify_statement", nodes.classify_statement)
    graph.add_node("classify_consolidation", nodes.classify_consolidation)
    graph.add_node("extract_line_items", nodes.extract_line_items)
    graph.add_node("aggregate_all", nodes.aggregate_all)

    graph.set_entry_point("find_tables")
    graph.add_edge("find_tables", "filter_relevant")
    graph.add_edge("filter_relevant", "classify_statement")
    graph.add_edge("classify_statement", "classify_consolidation")
    graph.add_edge("classify_consolidation", "extract_line_items")
    graph.add_edge("extract_line_items", "aggregate_all")
    graph.add_edge("aggregate_all", END)

    return graph.compile()


GRAPH = build_graph()
