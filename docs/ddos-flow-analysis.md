# DDoS analysis on NetFlow (Akvorado + ClickHouse)

## Scope: this is forensics, not detection

If you already run a scrubbing appliance (WanGuard, FastNetMon, Arbor), keep
it as the detector. It baselines per prefix and mitigates in seconds. Flow
analytics does not replace that, for two structural reasons:

- **Sampling.** At 1:100, a 40 Mbit/s flood against a single /32 reaches the
  database as a handful of flows. Statistically thin.
- **Flow timeout.** `timeout active 1` closes flows every minute. Minimum
  granularity is one minute; in practice one to two minutes before the data is
  queryable.

So: the appliance sees small and reacts in seconds. Flow sees volumetric and
takes minutes. Different layers, not competitors.

What flow gives you that the appliance does not: **post-incident forensics**
(where it came from, which transit it entered through, who else was hit),
a **second opinion** on whether a mitigation was justified, and a panel the
NOC can actually watch.

## Prerequisite that blocks everything else

The collector's `networks:` configuration must describe **your** prefixes.
Until it does, the tool shows IP addresses but cannot classify customer vs
transit vs peering — and "how much is coming *into* my network" is the central
DDoS question.

Verify the ranges against your IPAM, not against documentation. In one case
the documented CGNAT range and the range actually in use differed, which would
have classified all CGNAT traffic as external and poisoned every conclusion.

## The queries

Always multiply by `SamplingRate`. Forgetting it divides your numbers by the
sampling rate and turns the analysis into fiction.

One exception: `Bytes / Packets` (mean packet size) — sampling cancels out in
the division, so do **not** multiply there.

Addresses are stored IPv6-mapped. Compare with `toIPv6('::ffff:1.2.3.4')`.
A query that returns empty while the address is obviously present is almost
always this.

### Top targets by PPS

```sql
SELECT
    IPv6NumToString(DstAddr)               AS target,
    sum(Packets * SamplingRate) / 60       AS pps,
    sum(Bytes   * SamplingRate) * 8 / 60   AS bps,
    sum(Bytes) / nullIf(sum(Packets), 0)   AS mean_packet_size,
    uniq(SrcAddr)                          AS sources
FROM flows
WHERE TimeReceived > now() - INTERVAL 1 MINUTE
GROUP BY target
ORDER BY pps DESC
LIMIT 20
```

**Mean packet size is the cheapest discriminator available.**

| Pattern | Reading |
| --- | --- |
| very high PPS + mean packet < ~100 B | packet flood (SYN / small UDP / ACK). Saturates edge CPU before bandwidth |
| high BPS + large packets + few sources on a service port | amplification / reflection. Saturates bandwidth and transit |
| many scattered sources, smooth growth, normal packet size | probably legitimate (game update, live stream). **Blackholing here takes a paying customer offline** |

### Amplification signature

```sql
SELECT SrcPort, Proto, uniq(SrcAddr) AS sources, count() AS flows,
       sum(Bytes * SamplingRate) * 8 / 300 AS bps
FROM flows
WHERE TimeReceived > now() - INTERVAL 5 MINUTE
  AND DstAddr = toIPv6('::ffff:<target>')
GROUP BY SrcPort, Proto
ORDER BY bps DESC
```

Reflection source ports worth knowing: 53 (DNS), 123 (NTP), 161 (SNMP),
389 (LDAP), 1900 (SSDP), 11211 (memcached), 19 (chargen), 111 (portmap).

### Where it entered

```sql
SELECT ExporterName, InIfName, SrcAS,
       sum(Bytes * SamplingRate) * 8 / 300 AS bps
FROM flows_1m0s
WHERE TimeReceived > now() - INTERVAL 5 MINUTE
  AND DstAddr = toIPv6('::ffff:<target>')
GROUP BY ExporterName, InIfName, SrcAS
ORDER BY bps DESC
```

Answers "which border do I filter at" and "which upstream is delivering this".

### Forwarded vs dropped

```sql
SELECT ExporterName,
       multiIf(ForwardingStatus >= 192, 'consumed',
               ForwardingStatus >= 128, 'DROPPED',
               ForwardingStatus >=  64, 'forwarded', 'unknown') AS status,
       sum(Packets * SamplingRate) AS packets
FROM flows
WHERE TimeReceived > now() - INTERVAL 5 MINUTE
GROUP BY ExporterName, status
```

A spike in DROPPED points at an ACL, uRPF, or a blackhole still in place after
the attack ended — the most common cause of "everything came back except that
one customer".

### Exporter watchdog

```sql
SELECT ExporterName, max(TimeReceived) AS last_flow,
       dateDiff('second', max(TimeReceived), now()) AS silence_seconds
FROM flows
WHERE TimeReceived > now() - INTERVAL 10 MINUTE
GROUP BY ExporterName
ORDER BY silence_seconds DESC
```

**This one matters as much as all the others combined.** A DDoS dashboard that
stopped receiving flow stays green forever. Wire this before anything else.

When flow disappears while the customer is online, check in this order —
cheapest first: export stopped on the router, sampling rate changed,
mitigation/blackhole active, asymmetric routing.

## On alerting

A fixed threshold alone produces false positives on legitimate events: game
updates, live streams, backups. Proper detection needs a per-prefix baseline,
which is real engineering, not a query.

Until that exists, the honest compromise is **a high fixed threshold AND the
packet-size discriminator**: alert only when PPS is above the threshold *and*
mean packet size is below ~150 bytes. Legitimate traffic has large packets, so
this cuts most of the noise.

And put the alert where the NOC already looks. An alert in a second system is
an alert ignored in both.
