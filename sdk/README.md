# winston-sdk

Write extensions for [Winston](https://github.com/aldinsmajlovic/winston): tools he can call, things
he keeps an eye on, reasons to speak up, and entities for Home Assistant. The guide and the full
reference are [docs/extensions.md](https://github.com/aldinsmajlovic/winston/blob/main/docs/extensions.md).

```python
# /// script
# [tool.winston]
# id = "coin"
# name = "Coin"
# api = 1
# ///
import random

from winston_sdk import Extension

ext = Extension()


@ext.tool(tier="read", outside=False)
async def flip(ctx) -> str:
    """Flip a coin: heads or tails."""
    return random.choice(["heads", "tails"])
```
