# graph

Helper card for `fusion_kit/graph.py`. Index: `docs/fusion_kit_api.md`.

| helper | signature | replaces raw adsk |
|---|---|---|
| graph.flag | flag(nodes) — graph.py:71 |  |
| graph.attach_skq | attach_skq(nodes, lines) — graph.py:95 |  |
| graph.render | render(nodes, title) — graph.py:122 |  |
| graph.diff | diff(prev, cur) — graph.py:146 |  |
| graph.to_json | to_json(nodes) — graph.py:180 |  |
| graph.from_json | from_json(text) — graph.py:185 |  |
| graph.write_graph | write_graph(nodes, directory, name, skq_lines, key="") — graph.py:194 | writes fk_graph_<name>_<hash>.txt/.json, diffs vs previous .json |
| graph.collect | collect(design) — graph.py:362 |  |
| graph.run | run(_context) — graph.py:429 |  |
