# Vulnerabilities

A planned extension: new vulnerabilities as they come out, and whether you have them. It never patches
anything. The parts of the extension standard it needs are built; the extension itself is not yet. The
order is in the [plan](plan.md) (Computers and vulnerabilities track).

Stories about security are the News extension's Security preset
([built-in extensions](built-in-extensions.md#news)); this is the vulnerabilities themselves.

## Where it reads them

Two sources, both free and with no key:

| Source | What it gives |
| --- | --- |
| CISA's [Known Exploited Vulnerabilities](https://www.cisa.gov/known-exploited-vulnerabilities-catalog) catalog (KEV) | One JSON file of bugs with evidence of being exploited. A few dozen are added each month. |
| ENISA's [EU Vulnerability Database](https://euvd.enisa.europa.eu) (EUVD) | Every new CVE, with its score and the vendor and product it names. Thousands a week, hundreds of them scored 9 or more. |

## What is new

- **Newly exploited**: every entry CISA adds, in the briefing: what it is, whose product, and whether
  ransomware uses it. About one a day.
- **Critical, in what you follow**: a new CVE scored 9 or more whose vendor or product is something
  you [have](#what-you-have) or a name on its Follow list ("OpenSSH", "Home Assistant",
  "WordPress"). In the briefing, at most three a day, highest score first; following everything would
  be dozens a day (Google alone had 26 in one week). The rest wait for the tool.
- **`vulns_recent`**: what was newly exploited and newly critical in the last days (7 unless asked),
  all of it or about one vendor or product. "Any new vulnerabilities?", "what got exploited this
  week?", "anything new about Chrome?".

Both kinds are pushed, one nudge per CVE, and expire after three days, as news goes stale. A CVE you
have is said as one you have, never twice.

## What you have

What you own comes from where it is already known:

- **Home Assistant's devices**: the maker, model and firmware of every device it knows, exposed or not
  (the `devices` permission).
- **Your computers**: each computer server you pick, through its `security_updates`
  ([computers](computer.md)).
- **Anything else**: a list in its settings, each entry a name, a vendor, a product and optionally a
  version, for what neither knows (a router with no Home Assistant integration, a NAS, a phone).

### How a match is made

An exploited CVE (in the catalog) matches what you have in one of two ways:

| Match | When | Level |
| --- | --- | --- |
| Sure | The CVE is among the CVE ids of a computer's waiting updates. You have it, and the update that fixes it is waiting. | 2 |
| Likely | The catalog's vendor is the device's maker (compared without Inc, Ltd, GmbH and the like), and the background model, given the catalog entry and the device's maker, model and firmware, says yes, this is that product. | 1 |

The catalog gives no versions, so a likely match can be one you already patched. A device's likely
match ends when its firmware changes.

Word matching alone is not enough: the catalog's products often read "Multiple Products" (Apple),
"Multiple Routers" (TP-Link, Netgear) or "Kernel" (Linux), and a large share of it is Microsoft's. So
a computer's CVE ids win where its package tool gives them, and elsewhere the model judges the
product. Computers whose tool gives no ids (Ubuntu's apt, Arch, macOS, Homebrew, winget) are matched
like devices, by their OS and their waiting updates.

A critical CVE that is not (yet) exploited is news, not a match: it reaches you through the Follow
kind above.

### What he says about it

- **"Exploited, and you have it"**: a checked kind, one nudge per CVE and thing. It ends by itself
  when the update is installed (the CVE leaves the computer's list), and "that's not mine" dismisses
  it for good. Sure matches come at the next talk, likely ones in the briefing; each level can be
  changed on its page. What is already in the catalog when you switch it on is listed by the tool but
  not said, unless it is a sure match.
- **`vulns_to_patch`**: everything you have with a match, sure ones first: the thing, the CVE, what it
  is (the catalog's name and description), CISA's required action, when it was listed, whether it is
  known to be used by ransomware, and for a computer the waiting update that fixes it. "Anything I
  need to patch?", "is the router affected by anything?".

## How it runs

- **Every 6 hours** it reads the catalog, asking with If-Modified-Since, so an unchanged catalog costs
  one 304. It also reads EUVD's search for CVEs published since its last read and scored 9 or more, a
  hundred at a time.
- **Every hour** it reads each picked computer's `security_updates`, and **each day** Home Assistant's
  devices.
- It keeps 14 days of new CVEs in its folder, and both tools answer from what was read, so a question
  never waits on the web.
- **Status**: the catalog's date, and how many sure and likely matches. **Entity**: the number of
  exploited vulnerabilities you have.
- EUVD's API is young, and its documentation page needs a browser to read. If it stops answering, the
  Follow kind says so on its page and the rest works on.

```toml
permissions = { network = ["www.cisa.gov", "euvdservices.enisa.europa.eu"], devices = true, llm = true }
```

## What stays home

Nothing about what you have or follow leaves the house. The catalog is downloaded whole, the search
asks only for a date and a score, and the matching happens here, by the background model where it
judges, which is the local brain unless you picked a cloud one.

Text from the catalog and EUVD is from outside: it reaches his brain only as facts marked as
information, and the model's answer about it is a yes or a no.

## What it needs from the standard

Two parts of the [extension standard](extensions.md), each for any extension, not for this one:

- **The `devices` permission** and `ctx.devices()`: every device in Home Assistant's registry, exposed
  or not, with no states ([permissions](extensions.md#permissions)). An inventory needs routers,
  cameras and storage, which are rarely exposed to a voice assistant. A firmware update reminder, a
  battery list or a warranty tracker would use the same permission.
- **Picking another extension's tool in settings**: `Extensions(tool="security_updates")`, a picker of
  the switched-on extensions and MCP servers with a read tool of that name
  ([settings](extensions.md#settings)). Picking one is consent for this extension to call it through
  `ctx.ext(id).call(...)`. The manifest's `extensions = [...]` cannot do this, since computers get
  their names when you add them. A backup monitor over each computer's `logs`, or a disk-space nudge
  over each `status`, would use the same picker.
