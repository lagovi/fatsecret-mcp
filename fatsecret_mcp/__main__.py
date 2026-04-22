"""Allow `python -m fatsecret_mcp ...` in addition to the installed script."""
from .cli import main
raise SystemExit(main())
