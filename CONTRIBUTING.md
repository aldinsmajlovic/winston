# Contributing

Winston is Apache-2.0. Contributions are welcome under the same licence.

- Sign off every commit (`git commit -s`). That adds a `Signed-off-by:` line and means you agree to the
  [Developer Certificate of Origin](https://developercertificate.org/). No CLA.
- Read `docs/architecture.md` first. Adapters, tools and channels are plugins with small contracts; core stays small.
- Run `just lint` and `just test` before opening a pull request. Hardware checks (`winston check`) need a GPU and a Home Assistant.
- Keep personal data out of the repo. Everything instance-specific lives in the data directory.
- UI work follows `docs/ui-guide.md`: one component library, icons for actions, no decorative copy, no em dashes.

"Winston" is the name of this project. Forks are welcome; please give yours a different name.
