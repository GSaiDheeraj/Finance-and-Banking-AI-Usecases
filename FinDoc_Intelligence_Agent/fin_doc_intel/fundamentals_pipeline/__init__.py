"""Staged grid readers: locate grids, judge them, read the survivors.

Order: find_tables -> filter_relevant -> classify_statement ->
classify_consolidation -> extract_line_items -> aggregate_all. Grid location
uses the renderer's own sheet markers and native grid detection; structured
answers come through one shared typed-chat helper.
"""
