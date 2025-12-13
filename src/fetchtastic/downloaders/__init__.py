"""
Legacy compatibility modules.

The refactor moved the downloader implementation into `fetchtastic.download.*`.
Some older code (and a few tests) still import symbols from `fetchtastic.downloaders.*`;
these shims preserve those import paths without reintroducing the monolithic design.
"""
