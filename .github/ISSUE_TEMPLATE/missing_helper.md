---
name: Missing helper (gap)
about: A Fusion operation has no fusion_kit helper and you had to write raw adsk
title: "[gap] "
labels: missing-helper
assignees: ""
---

**Operation needed**
What the script had to do in Fusion (for example "extrude a profile list into a new body with label").

**Raw adsk workaround used**
The raw adsk code you wrote instead of a helper.

```python

```

**Which helper should own it**
Proposed module (`component`, `dfam`, `expr`, `features`, `holes`, `joints`, `printability`, `sketch`, `view`, ...) and signature.

**Mechanisms that hit it**
List every mechanism or part that needed this op. Two or more means it belongs in `fusion_kit`, not in a part's `parts/helpers.py`.

**Existing helper that almost fits (if any)**
Name it and say what is wrong with it.
