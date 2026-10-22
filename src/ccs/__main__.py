"""允许 `python -m ccs`。"""

from __future__ import annotations

from ccs.cli import main

raise SystemExit(main())
