# winston-computer

A small MCP server for each computer Winston reaches: open an installed app, a web address or a file
from a folder you allow; say how the computer is doing; read the logs you allow; list the updates it is
waiting for. No shell, no power, nothing installed. Linux for now.

```sh
uv tool install "git+https://github.com/aldinsmajlovic/winston#subdirectory=computer"
winston-computer init               # the settings file, with nothing allowed
winston-computer token              # prints a new token once; only its hash is kept
winston-computer service install    # starts it with your desktop session
winston-computer call status        # one tool, here, as Winston would call it
```

Then add it in Winston on the Extensions page (+, its address, the token). The whole guide, the
settings and what each tool will and will not do: [docs/computer.md](../docs/computer.md).
