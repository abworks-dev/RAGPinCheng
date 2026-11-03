"""Supported opaque attachments; never eligible for text/RAG indexing."""
ATTACHMENT_TYPES = frozenset({'jpg', 'jpeg', 'png', 'dwg', 'dxf', 'rvt', 'che', 'nwd', 'nwc', 'ifc', 'skp', 'max'})
ATTACHMENT_EXTENSIONS = {f'.{kind}': kind for kind in ATTACHMENT_TYPES}
