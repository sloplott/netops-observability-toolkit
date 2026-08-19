# Auditing a Grafana + Zabbix dashboard

## The failure mode

The Grafana Zabbix datasource matches items **by name**, usually with a regex.
When the regex does not match, the panel renders "No data". No error, no log
line, no red badge — it just looks like the device is not collecting.

Which means a dashboard can look finished, be reviewed, be put on a NOC wall,
and be quietly blind for months.

Real examples found this way, all in production dashboards that "worked":

| Panel searched for | Real item name | Impact |
| --- | --- | --- |
| `Estado da <interface>` | `Estado da interface <X>` | interface up/down never displayed |
| `Velocidade da interface <X>` | `Velocidade de <X>` | 69 items invisible |
| `Temperatura na Placa <N>` | `Temperatura **da** Placa <N>` | board temperature invisible |
| `Tráfego de Saída` | `Trafego de Saida` (no accents) | outbound traffic invisible |
| `... em <IF>` only | GPON ports use `... na Interface <IF>` | **129 of 138 ports hidden** |

That last one is the important one. The dashboard showed nine ethernet ports
and looked healthy. Every GPON port on the OLT — the entire point of the
device — was missing, because two naming conventions coexisted in the same
template and the regex only covered one.

## The method

**Do not guess item names.** Read them from the API.

```bash
export ZBX_URL="https://zabbix.example.net/zabbix/api_jsonrpc.php"
export ZBX_TOKEN="<read-only token>"

python3 scripts/zbx_audit.py --profile huawei-mpls --host CORE-01 --json report.json
```

Read the output in this order:

1. **Rows marked `!`** — zero items matched. Those panels will be empty.
   Either the regex is wrong or the template does not collect it. Two very
   different problems with the same symptom.
2. **`W/ DATA` column** — items matched but nothing recent. Usually a long
   `delay` on a text item, sometimes a dead poller.
3. **`UNSUPPORTED ITEMS`** — the template asks for an OID the device does not
   implement. Each one is a wasted poll on every cycle, forever.
4. **`COLLECTED BUT UNUSED BY THE DASHBOARD`** — the most valuable section.
   This is where the real item names are, and where you find data worth
   showing that nobody had put on a panel.

That last section is how `Quantidade de Clientes Conectados` (PPPoE session
count on a BRAS) and `ONUs autorizadas por PON` were discovered. Both were
being collected and neither was on any dashboard — while the dashboards did
carry a full BGP section for a device family that collects no BGP data at all.

## The rule

> A panel that has never shown data is not a panel. It is a promise.

Before shipping a dashboard, audit it. Before migrating a template in bulk,
audit it — one of these runs cancelled a 26-host template migration that had
been justified by an assumption the data disproved.
