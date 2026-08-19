# netops-observability-toolkit

Grafana dashboards, Zabbix audit tooling and runbooks for ISP network
operations — built and validated against a live multi-vendor backbone
(Huawei VRP, MikroTik RouterOS, Fiberhome and ZTE GPON OLTs, ~1500 monitored
hosts).

The dashboards are the visible part. The **audit scripts** are the point.

---

## The problem this solves

The Grafana Zabbix datasource matches items **by name**. When the regex in a
panel does not match the real item name, the panel renders `No data` — no
error, no log line, nothing. A dashboard can be reviewed, approved, put on a
NOC wall, and be quietly blind for months.

That is not hypothetical. Auditing a set of dashboards that had been in
production for a long time turned up:

| Finding | Impact |
| --- | --- |
| GPON ports used a second naming convention (`na Interface X` vs `em X`) | **129 of 138 ports invisible** on the OLT dashboard |
| Panels searched `Estado da <if>`; the item is `Estado da interface <if>` | interface up/down never displayed |
| Panels searched `Velocidade da interface`; the item is `Velocidade de` | 69 items invisible |
| A full BGP section on a device family that collects no BGP data | section had never worked |
| ICMP items marked "not collected yet" | they had always existed, under a different name |
| A 26-host template migration justified by "it does not collect multi-lane optics" | it does. Migration cancelled |
| 8 hosts duplicated on the same IP | double SNMP polling, duplicate alarms |
| A critical-temperature trigger with recovery `>62` instead of `<62` | alarm closed itself on the same evaluation — no critical temperature alert existed |

Every one of those was found by reading the API instead of trusting the
dashboard, using the scripts in `scripts/`.

---

## Repository layout

```
infra/                  flow-collector examples: nftables, DOCKER-USER, rsyslog, sysctl
docs/                   Method and runbooks
dashboards/             Grafana 12 JSON — flow analytics, DDoS, MPLS, BRAS, GPON OLT
scripts/
├── zbx_audit.py        audit a dashboard's regexes against Zabbix
└── zbx_inventory.py    inventory hosts, find duplicates and orphans
```

---

## Quick start

```bash
export ZBX_URL="https://zabbix.example.net/zabbix/api_jsonrpc.php"
export ZBX_TOKEN="<read-only API token>"     # Zabbix: Users -> API tokens

# will this dashboard actually fill up?
python3 scripts/zbx_audit.py --profile huawei-mpls --host CORE-01

# what is in this Zabbix, and is it registered correctly?
python3 scripts/zbx_inventory.py --group OLT --json inventory.json
```

Standard library only — no dependencies, no virtualenv. Both scripts are
strictly read-only (`host.get`, `item.get`, `history.get`).

### Reading `zbx_audit.py` output

| Section | What it means |
| --- | --- |
| rows marked `!` | zero items matched — the panel will be empty |
| `W/ DATA` column | matched, but nothing recent: long `delay`, or a dead poller |
| `UNSUPPORTED ITEMS` | template asks for an OID the device does not implement — a wasted poll every cycle, forever |
| `COLLECTED BUT UNUSED` | **the valuable one.** Real item names, and data worth showing that nobody put on a panel |

That last section is how a PPPoE session counter on a BRAS and an
authorised-ONUs-per-PON counter were found — both collected, neither on any
dashboard.

---

## Dashboards

Import into Grafana 12.x. Every dashboard uses hidden variables for the
datasource and host group, so the JSON is portable: set them once in
**Dashboard settings → Variables**.

| File | Datasource | Covers |
| --- | --- | --- |
| `00-home.json` | — | landing page with links to the rest |
| `01-flow-ports.json` | ClickHouse | traffic by destination port |
| `02-flow-exporters.json` | ClickHouse | aggregate per exporter and AS |
| `03-flow-top-talkers.json` | ClickHouse | source → destination conversations |
| `04-flow-ddos-analysis.json` | ClickHouse | targets, attack signature, collection health |
| `huawei-mpls-switches.json` | Zabbix | ping, health, OSPF, interfaces, optics with thresholds |
| `mikrotik-bras.json` | Zabbix | PPPoE sessions, per-core CPU, interfaces, optics |
| `05-olt-gpon.json` | Zabbix | boards, ONUs per PON, GPON interfaces |

Two design choices worth calling out:

**Panels for data that does not exist were removed, not left empty.** Where a
template genuinely does not collect something — optics and power on the OLTs,
for instance — the dashboard says so in a footer card instead of showing a
permanently blank panel. An empty panel reads as "the device is broken". A
note reads as "extend the template".

**The DDoS dashboard pairs PPS with mean packet size.** Rising PPS with
collapsing packet size is a packet flood; rising bandwidth with large packets
and few sources is amplification. You read the difference without configuring
a single threshold. See [`docs/ddos-flow-analysis.md`](docs/ddos-flow-analysis.md).

---

## Documentation

- **[Auditing a dashboard](docs/dashboard-audit-method.md)** — the method, and
  why a panel that never showed data is a promise rather than a panel
- **[Grafana 7 → 12 migration](docs/grafana-7-to-12-migration.md)** — the
  checklist, led by Zabbix applications having been removed in 5.4 and taking
  every filter that referenced them down with it
- **[DDoS analysis on NetFlow](docs/ddos-flow-analysis.md)** — queries,
  sampling limits, and why flow analytics complements a scrubbing appliance
  rather than replacing it
- **[Flow collector onboarding](docs/flow-collector-onboarding.md)** — adding
  an exporter without breaking the appliance that shares it

---

## Operational notes worth keeping

Things that cost real troubleshooting time and are easy to forget:

- **The collector firewall filters on the flow packet's source address** — the
  router's `export source` / loopback — not its management IP. Loopbacks often
  sit in a range nobody allowed, and the drop is silent.
- **Container-published ports bypass the host firewall.** They are DNAT'd in
  `PREROUTING` and traverse `FORWARD`; filter them in `DOCKER-USER`.
- **`net.core.*` is not namespaced.** Socket buffer tuning for a containerised
  collector belongs on the hypervisor.
- **Syslog over TCP without a configured queue blocks the process writing the
  log.** On a busy server, that is monitoring causing an outage.
- **Inside LXC, `imklog` cannot read the kernel log**, so firewall drops never
  reach a container-side syslog. Collect them on the hypervisor.
- **Importing a dashboard whose UID already exists overwrites it, silently.**
  Inventory your UIDs first.

---

## Compatibility

Validated against Zabbix **7.2**, Grafana **12.2**, the
`alexanderzobnin-zabbix-datasource` plugin, and
`grafana-clickhouse-datasource` for flow.

The audit profiles ship with item-name regexes from a Portuguese-language
Zabbix template set, and panel descriptions inside the dashboards are in
Portuguese for the same reason. Item names are site-specific by nature — treat
the profiles in `scripts/zbx_audit.py` as a starting point and adjust them to
your own templates. The method transfers; the strings do not.

---

## License

MIT — see [LICENSE](LICENSE).
