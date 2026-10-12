"""PDF ingestion: label a filing, then render it to Markdown-with-HTML grids.

Readable filings take the hybrid reader (text layer for plain sheets, vision
for grid/picture sheets); photographed filings take the vision reader for
every sheet. The vision backend is a local vision-language model, loaded once
and shared across readers and filings.
"""
