# Porting a Grafana 7.x dashboard to Grafana 12

Checklist built while migrating a set of Zabbix dashboards from Grafana 7.5
to 12.2. Items are ordered by how much damage they cause.

## 1. Zabbix "applications" no longer exist

Removed in **Zabbix 5.4**. Grafana 7-era dashboards filter targets with
`application: "Status" | "Sistema" | "PONS" | "GBIC" | ...`.

On a modern Zabbix that field matches nothing, so **every panel using it goes
blank**. This is by far the largest source of empty panels after a migration,
and the one most likely to be misdiagnosed as "SNMP broke".

Replace application filters with an item-name regex, and find another way to
express the grouping the application used to provide. When applications
separated PON ports from uplinks, for instance, the replacement was a regex on
the port-name prefix:

```
/^(?:Corrente na Interface )((?:GPON|EPON|PON).*)$/
```

## 2. The `graph` panel is gone

`"type": "graph"` is the Angular/flot panel, removed in Grafana 11.
Convert to `"type": "timeseries"`:

| Grafana 7 | Grafana 12 |
| --- | --- |
| `yaxes[]` | `fieldConfig.defaults.unit`, `.min`, `.max` |
| `seriesOverrides[]` | `fieldConfig.overrides[]` with `byRegexp` matchers |
| `legend: {avg, current, max}` | `options.legend.calcs[]` |
| `fill`, `linewidth`, `nullPointMode` | `fieldConfig.defaults.custom.*` |

## 3. Value mappings changed shape

```jsonc
// Grafana 7
"mappings": [{ "id": 0, "op": "=", "text": "UP", "type": 1, "value": "1" }]

// Grafana 12
"mappings": [{ "type": "value", "options": { "1": { "text": "UP", "color": "green", "index": 0 } } }]
```

## 4. Datasource references

`"datasource": "Zabbix"` (a bare string) still half-works and will bite you on
the next export. Use the object form, ideally through a hidden dashboard
variable so the JSON stays portable:

```jsonc
"datasource": { "type": "alexanderzobnin-zabbix-datasource", "uid": "${DS_ZABBIX}" }
```

## 5. Threshold base step must be `null`

```jsonc
"steps": [{ "color": "green", "value": null }, { "color": "red", "value": 80 }]
```

A base step of `0` instead of `null` puts the value zero outside the range.
Grafana tolerates it silently, which is worse than failing.

## 6. Drop the dead keys

`schemaVersion` 27 → 42, and remove `style`, `gnetId`, `iteration`,
`cacheTimeout`, `interval: null`, `timeShift: null`, `height`.

## 7. Rate expressions and zoom

When computing a per-second rate from a counter, divide by the real bucket
width, not by a constant:

```sql
-- fragile: correct only while the bucket is exactly 60s
sum(Bytes * SamplingRate) * 8 / 60

-- correct at any zoom level
sum(Bytes * SamplingRate) * 8 / $__interval_s
```

A hardcoded `/ 60` is acceptable **only** when the panel also pins the bucket
(`toStartOfMinute`) and the time range (`timeFrom`). Otherwise zooming out to
seven days silently divides the result by sixty.

## 8. Check the UID before importing

Importing a dashboard whose `uid` already exists **overwrites the existing one
without warning**. Inventory your UIDs first. This checklist exists partly
because a new dashboard was nearly shipped with a UID already in use by
another one.
