# Adding an exporter to a NetFlow collector

Procedure for onboarding a router into a flow collector (written against
Akvorado, but the failure modes are generic).

Replace `<COLLECTOR>` with your collector address throughout.

## The rule that does not bend

On routers that **already export to a scrubbing appliance**, only ever *add*.
Never modify:

- `ip netstream export source` — change it and the appliance stops recognising
  the exporter
- `ip netstream sampler fix-packets` — change the rate and the appliance's
  math is wrong, so mitigation triggers at the wrong threshold
- interfaces that already have netstream enabled

NetStream sends the same copy to every configured destination. The only new
line is a second `export host`.

## Step 1 — inventory before typing

```
display ip netstream all
display current-configuration | include netstream
display snmp-agent community
```

Record: the `export source` IP (this becomes the exporter identity), the
sampler rate, how many `export host` entries already exist, which interfaces
are enabled, and which ACL guards the SNMP community.

VRP limits the number of `export host` entries per version. If two already
exist, check the manual before adding a third — exceeding the limit can be
accepted silently and simply not export.

## Step 2 — SNMP is mandatory

The collector **discards the flow** if it cannot resolve the interface name
via SNMP. Packets arrive, nothing is stored, and the dashboard shows no error.

Create a **new** ACL rather than reusing an existing one. A common trap: an
ACL used by `snmp-agent acl` is often also used by `ssh server acl`, so adding
the collector to it grants SSH as a side effect.

Verify from the collector before configuring anything else:

```
snmpget -v2c -c <COMMUNITY> -t 3 -r 1 <EXPORT-SOURCE-IP> 1.3.6.1.2.1.1.5.0
```

Must return the hostname. On timeout, stop here.

## Step 3 — collector firewall

The firewall filters on the **source address of the flow packet** — the
`export source`, normally a loopback — **not** the management IP.

This distinction cost a whole troubleshooting session once: the loopbacks were
in ranges the collector firewall did not permit, so every packet was dropped
silently while the management IPs looked perfectly reachable.

If the collector runs in Docker, published ports bypass the host firewall
(`INPUT` chain) — they are DNAT'd in `PREROUTING` and traverse `FORWARD`.
Filter them in `DOCKER-USER`, and put the `RETURN` rule **before** the `DROP`.

## Step 4 — enable export

Already exporting to the appliance:

```
system-view
 ip netstream export host <COLLECTOR> 2055
quit
```

Nothing else. Greenfield router:

```
system-view
 ip netstream timeout active 1
 ip netstream as-mode 32
 ip netstream export version 9 origin-as bgp-nexthop
 ip netstream export index-switch 32
 ip netstream export template option sampler
 ip netstream export source <LOOPBACK>
 ip netstream export host <COLLECTOR> 2055
 ip netstream sampler fix-packets 100 inbound
 ip netstream sampler fix-packets 100 outbound
quit
```

| Command | Why |
| --- | --- |
| `timeout active 1` | closes flows every minute — this is what gives per-minute granularity |
| `as-mode 32` | 32-bit ASN; without it, newer ASNs are reported wrong |
| `index-switch 32` | 32-bit interface index, matching what SNMP returns |
| `export template option sampler` | **advertises the sampling rate**; without it the collector drops everything with `sampling rate missing` |

## MikroTik: the extra step

MikroTik does **not** advertise a sampling rate — it exports 1:1. The
collector needs an explicit declaration per exporter address:

```yaml
core:
  default-sampling-rate:
    <EXPORTER-IP>/32: 1
```

Forgetting this is the second most common cause of "configured it, nothing
arrived".

And never use `interfaces=all` on a BRAS. With thousands of dynamic PPPoE
sessions that generates tens of thousands of flows per second and burns CPU.
Pick the uplink.

## Step 5 — verify in this order

Each step eliminates one layer. Do not skip to the last.

```bash
# 1. do packets arrive?
timeout 30 tcpdump -ni any -c 10 udp port 2055 and host <EXPORT-SOURCE>

# 2. received and discarded? the error= label says why
curl -s http://<collector>:8080/api/v0/metrics | grep -E 'core_(received|flows_errors)'

# 3. stored? a populated interface name means SNMP resolved
clickhouse-client -q "SELECT ExporterName, InIfName, count() FROM flows \
  WHERE TimeReceived > now() - 300 GROUP BY ExporterName, InIfName LIMIT 10"
```

## Symptom → cause

| What you see | What it is |
| --- | --- |
| tcpdump shows nothing | router not exporting, or collector firewall dropped it |
| packets arrive, database empty, `unable to GET OIDs` | SNMP not answering — ACL, community or router firewall |
| `error="sampling rate missing"` | missing `export template option sampler`, or missing `default-sampling-rate` for a MikroTik |
| interface stored as a number | SNMP partially answering — check `index-switch 32` |
| ASN shows as 23456 or zero | missing `as-mode 32`, or BMP not configured |

## Do not export from BRAS "for completeness"

Two reasons. Sampling-free MikroTik BRAS export is expensive in CPU. And the
same traffic is already counted at the border — collecting it again
**double-counts**, inflating the database and skewing every total on the
dashboards.

Border and transit are where attacks enter and where the numbers are honest.
Add BRAS only when the goal is per-subscriber or CGNAT visibility, which the
border genuinely cannot provide.
