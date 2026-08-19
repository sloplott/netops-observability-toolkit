#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zbx_inventory.py — answer "what is actually in this Zabbix, and is it
monitored correctly?" without opening a single SSH session.

Built after finding, in a real ISP, that a quarter of the GPON OLTs were
linked to a *switch* template — collecting interface counters but no PON
and no board data — and that eight hosts were duplicated on the same IP,
double-polling the same equipment and raising every alarm twice.

What it reports:
  1. one line per host: model, inferred role, management IP, templates
  2. hosts sharing an IP        -> duplicate registration
  3. hosts with no template     -> registered and collecting nothing
  4. hosts with unsupported items -> SNMP partially broken
  5. template distribution      -> vendor/template mismatches stand out

Usage:
    export ZBX_URL="https://zabbix.example.net/zabbix/api_jsonrpc.php"
    export ZBX_TOKEN="<read-only API token>"

    python3 zbx_inventory.py --group OLT
    python3 zbx_inventory.py --match 'OLT[-_ ]|GPON' --json inventory.json

Beware of loose --match patterns: "OLT" also matches "vOLT" and "sOLT",
which is how a 50-host survey turned into 127 rows the first time.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict

IDENTITY = [
    ("model",  r"^(Modelo do Equipamento|Modelo|Model)$"),
    ("os",     r"^(Sistema Operacional|Vers[ãa]o d[oa] (Software|Firmware))$"),
    ("serial", r"^(Numero Serial|Numero Serial do Equipamento|Serial)$"),
]

# model / sysDescr fragment -> (role, flow export path)
ROLES = [
    (r"NE\d{2,4}|NE8000|NE40E",     "ROUTER / BNG",  "NetFlow v9"),
    (r"CloudEngine|\bCE\d{4}",      "SWITCH",        "sFlow"),
    (r"\bS\d{4}\b",                 "ACCESS SWITCH", "sFlow"),
    (r"MA5\d{3}|SmartAX",           "GPON OLT",      "does not export flow"),
    (r"RouterOS|RB\d|CCR\d",        "MIKROTIK",      "NetFlow v9 (no sampling)"),
    (r"AR\d{3}",                    "SOHO ROUTER",   "NetFlow, version permitting"),
]


def api(url, token, method, params, timeout=60):
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json-rpc", "Authorization": "Bearer %s" % token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read().decode())
    if "error" in out:
        raise SystemExit("Zabbix API error on %s: %s" % (method, out["error"]))
    return out["result"]


def role_of(text):
    for rx, role, flow in ROLES:
        if re.search(rx, text, re.I):
            return role, flow
    return "?", "?"


def main():
    ap = argparse.ArgumentParser(description="Inventory and sanity-check Zabbix hosts.")
    ap.add_argument("--url", default=os.environ.get("ZBX_URL"))
    ap.add_argument("--token", default=os.environ.get("ZBX_TOKEN"))
    ap.add_argument("--group", action="append", default=[], help="host group; repeatable")
    ap.add_argument("--match", help="regex on host name or template name")
    ap.add_argument("--window", type=int, default=172800,
                    help="seconds to look back for identity values (default 48h)")
    ap.add_argument("--json", metavar="FILE")
    a = ap.parse_args()
    if not a.url or not a.token:
        ap.error("missing --url/ZBX_URL or --token/ZBX_TOKEN")

    params = {"output": ["hostid", "host", "name", "status"],
              "selectParentTemplates": ["name"], "selectInterfaces": ["ip"],
              "selectHostGroups": ["name"], "sortfield": "host"}
    if a.group:
        gs = api(a.url, a.token, "hostgroup.get",
                 {"output": ["groupid", "name"], "filter": {"name": a.group}})
        if not gs:
            raise SystemExit("Group(s) not found: %s" % ", ".join(a.group))
        params["groupids"] = [g["groupid"] for g in gs]

    hosts = api(a.url, a.token, "host.get", params)
    if a.match:
        rx = re.compile(a.match, re.I)
        hosts = [h for h in hosts
                 if rx.search(" ".join([h["host"], h.get("name", "")] +
                                       [t["name"] for t in h.get("parentTemplates", [])]))]
    if not hosts:
        raise SystemExit("No host matched. Adjust --group or --match.")

    now = int(time.time())
    inv = []
    print("=" * 108)
    print("ZABBIX INVENTORY — %d host(s)" % len(hosts))
    print("=" * 108)
    print("%-34s %-24s %-15s %-16s %s" % ("HOST", "MODEL", "ROLE", "IP", "TEMPLATES"))
    print("-" * 108)

    for h in hosts:
        items = api(a.url, a.token, "item.get", {
            "output": ["itemid", "name", "value_type", "state"], "hostids": h["hostid"]})
        alive = [i for i in items if i.get("state") != "1"]
        unsupported = [i for i in items if i.get("state") == "1"]

        ident = {}
        for field, pattern in IDENTITY:
            rx = re.compile(pattern)
            hit = [i for i in alive if rx.match(i["name"])]
            ident[field] = ""
            if hit:
                hs = api(a.url, a.token, "history.get", {
                    "output": "extend", "history": int(hit[0]["value_type"]),
                    "itemids": hit[0]["itemid"], "time_from": now - a.window,
                    "sortfield": "clock", "sortorder": "DESC", "limit": 1})
                if hs:
                    ident[field] = hs[0]["value"]

        tpls = [t["name"] for t in h.get("parentTemplates", [])]
        ips = [i["ip"] for i in h.get("interfaces", []) if i.get("ip")]
        role, flow = role_of(" ".join([ident["model"], ident["os"], h["host"], h.get("name", "")]))

        print("%-34s %-24s %-15s %-16s %s" % (
            h["host"][:34], (ident["model"] or "-")[:24], role[:15],
            (ips[0] if ips else "-")[:16], ", ".join(tpls)[:30]))

        inv.append({"host": h["host"], "visible_name": h.get("name", ""),
                    "groups": [g["name"] for g in h.get("hostGroups", [])],
                    "ips": sorted(set(ips)), "templates": tpls,
                    "model": ident["model"], "os": ident["os"], "serial": ident["serial"],
                    "role": role, "flow_path": flow,
                    "items_total": len(items), "items_unsupported": len(unsupported)})

    def section(title):
        print("\n" + "=" * 108); print(title); print("=" * 108)

    by_ip = defaultdict(set)
    for e in inv:
        for ip in e["ips"]:
            by_ip[ip].add(e["host"])
    dupes = {ip: sorted(hs) for ip, hs in by_ip.items() if len(hs) > 1}
    if dupes:
        section("SAME IP ON MORE THAN ONE HOST — duplicate registration, double polling, double alarms")
        for ip, hs in sorted(dupes.items()):
            print(" %-16s %s" % (ip, "  ||  ".join(hs)))

    orphans = [e["host"] for e in inv if not e["templates"]]
    if orphans:
        section("HOSTS WITH NO TEMPLATE (%d) — registered, collecting nothing" % len(orphans))
        for h in orphans:
            print(" - %s" % h)

    broken = [e for e in inv if e["items_unsupported"]]
    if broken:
        section("HOSTS WITH UNSUPPORTED ITEMS — incomplete SNMP")
        for e in sorted(broken, key=lambda x: -x["items_unsupported"]):
            print(" - %-34s %d of %d items" % (e["host"][:34], e["items_unsupported"], e["items_total"]))

    section("TEMPLATE DISTRIBUTION — vendor/template mismatches stand out here")
    dist = Counter(", ".join(sorted(e["templates"])) or "(no template)" for e in inv)
    for tpl, n in dist.most_common():
        print(" %4d  %s" % (n, tpl))

    section("FLOW EXPORT PATH")
    for e in inv:
        print(" - %-34s %-15s %s" % (e["host"][:34], e["role"], e["flow_path"]))
    print("\nReminder: the collector firewall filters on the flow packet's SOURCE address")
    print("(the export source / loopback), not the management IP listed above.")

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(inv, f, ensure_ascii=False, indent=2)
        print("\nWrote %s" % a.json)
    print("Empty model = identity item missing, or no value within the window.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
