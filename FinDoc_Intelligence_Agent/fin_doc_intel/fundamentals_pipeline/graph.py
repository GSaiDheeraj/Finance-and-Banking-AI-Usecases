"""Fixed order of the staged table readers.

Locate grids, drop the irrelevant, label statements, settle column scope,
read the survivors carefully, then group the drafts:

    find_tables -> filter_relevant -> classify_statement
    -> classify_consolidation -> extract_line_items -> aggregate_all -> END
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from . import nodes
from .state import PipelineState


def build_graph():
    ordered = StateGraph(PipelineState)

    ordered.add_node("find_tables", nodes.find_tables_node)
    ordered.add_node("filter_relevant", nodes.filter_relevant)
    ordered.add_node("classify_statement", nodes.classify_statement)
    ordered.add_node("classify_consolidation", nodes.classify_consolidation)
    ordered.add_node("extract_line_items", nodes.extract_line_items)
    ordered.add_node("aggregate_all", nodes.aggregate_all)

    ordered.set_entry_point("find_tables")
    ordered.add_edge("find_tables", "filter_relevant")
    ordered.add_edge("filter_relevant", "classify_statement")
    ordered.add_edge("classify_statement", "classify_consolidation")
    ordered.add_edge("classify_consolidation", "extract_line_items")
    ordered.add_edge("extract_line_items", "aggregate_all")
    ordered.add_edge("aggregate_all", END)

    return ordered.compile()


GRAPH = build_graph()
