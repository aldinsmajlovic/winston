# Contributing

Winston is Apache-2.0. Contributions are welcome under the same licence.

- Sign off every commit (`git commit -s`). That adds a `Signed-off-by:` line and means you agree to the
  [Developer Certificate of Origin](https://developercertificate.org/). No CLA.
- Read `docs/architecture.md` first. Adapters, tools and channels are plugins with small contracts; core stays small.
- Run `just lint` and `just test` (`winston check unit`, the checks that need no hardware) before opening a pull
  request. The rest of `winston check` needs a GPU and a Home Assistant.
- Keep personal data out of the repo. Everything instance-specific lives in the data directory.
- UI work follows `docs/ui-guide.md`: one component library, icons for actions, no decorative copy, no em dashes.
  `cd web && pnpm build` enforces the rules; `pnpm api` regenerates the client after an API change.

"Winston" is the name of this project. Forks are welcome; please give yours a different name.
