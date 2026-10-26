"""Production CAD entry: python -m cad"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    port = int(os.environ.get("PORT", "10000"))
    uvicorn.run("cad.app:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()
