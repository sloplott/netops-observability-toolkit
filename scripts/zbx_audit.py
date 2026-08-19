#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zbx_audit.py — check whether a Grafana dashboard will actually fill up,
before you spend an afternoon wondering why every panel says "No data".

A Grafana panel backed by the Zabbix datasource matches items by NAME.
If the regex in the panel does not match the real item name, the panel is
silently empty — no error, no warning, nothing in the logs. This script
runs the dashboard's own regexes against the Zabbix API and reports,
panel by panel, how many items matched and how many have recent data.

The most valuable section is the last one: items that ARE collected but
that no panel uses. That is where you find the real item names.

Usage:
    export ZBX_URL="https://zabbix.example.net/zabbix/api_jsonrpc.php"
    export ZBX_TOKEN="<read-only API token>"

    python3 zbx_audit.py --profile huawei-mpls --host CORE-SWITCH-01
    python3 zbx_audit.py --profile mikrotik-bras --host 10.0.0.1
    python3 zbx_audit.py --profile olt --host OLT-01 --json report.json
    python3 zbx_audit.py --list-profiles

Create the token in Zabbix under Users -> API tokens. Read-only is enough:
the script only calls host.get, item.get and history.get.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

# --------------------------------------------------------------------------
# Profiles: (panel label, item-name regex, fetch last value?)
#
# These mirror the regexes used by the dashboards in ../dashboards.
# Dashboard variables ($interface, $gbic, ...) are replaced by ".*" so the
# audit counts the whole family instead of a single selection.
# --------------------------------------------------------------------------
IVL = r"( \[[0-9]+[smh]\])?"          # some templates carry the interval in the name

PROFILES = {
    "huawei-mpls": [
        ("ICMP · state",            r"^(Estado do Equipamento|ICMP ping|Status PING)" + IVL + r"$", True),
        ("ICMP · loss",             r"^(Perda de Pacotes|Percentagem de Perda de Pacotes)" + IVL + r"$", True),
        ("ICMP · response time",    r"^(Tempo de Resposta|Tempo de resposta PING)" + IVL + r"$", True),
        ("General · uptime",        r"^Tempo de Atividade$", True),
        ("General · model / OS / serial", r"^(Modelo do Equipamento|Sistema Operacional|Numero Serial)$", True),
        ("Health · CPU",            r"^(Utiliza[çc][ãa]o de CPU|Uso de CPU de .*)$", True),
        ("Health · memory",         r"^(Memoria RAM Utilizada|Uso de Memoria RAM de .*)$", True),
        ("Health · temperature",    r"^(Temperatura|Temperatura de .*)$", True),
        ("Health · fans",           r"^Estado (do Ventoinha|do Ventilador) ", True),
        ("Health · PSU",            r"^Potencia (Utilizada|M[áa]xima de Sa[íi]da|Restante) da Fonte ", True),
        ("Interface · traffic in",  r"^Tr[aá]fego de Entrada (em|na Interface) .*$", True),
        ("Interface · traffic out", r"^Tr[aá]fego de Sa[íi]da (em|na Interface) .*$", True),
        ("Interface · state",       r"^Estado da interface (?!Optica).*$", True),
        ("Interface · speed",       r"^Velocidade (de|da interface|em) .*$", True),
        ("Interface · input errors", r"^Pacotes de Entrada com Erros .*$", True),
        ("Interface · output errors", r"^Pacotes de Sa[íi]da com Erros .*$", True),
        ("Optics · RX",             r"^Sinal de Recep[çc][ãa]o no Modulo .*$", True),
        ("Optics · TX",             r"^Sinal de Transmiss[ãa]o no Modulo .*$", True),
        ("Optics · thresholds",     r"^Lim(ite|tie) de Sinal .*$", False),
        ("Optics · module temp",    r"^Temperatura no Modulo (SL )?.*$", True),
        ("Optics · vendor / type",  r"^(Nome do Vendor|Tipo|Capacidade|Distancia) d[oe] [Mm]odulo .*$", False),
        ("OSPF · neighbours",       r"^(Estado|Tempo de Vida) do Peer OSPF .*$", True),
        ("Boards",                  r"^(Estado|Temperatura|Modelo) d[ae] Placa .*$", True),
    ],
    "mikrotik-bras": [
        ("ICMP · state",            r"^Estado do Equipamento$", True),
        ("ICMP · loss",             r"^Percentagem de Perda de Pacotes$", True),
        ("ICMP · response time",    r"^Tempo de Resposta$", True),
        ("General · uptime",        r"^Tempo de Atividade$", True),
        ("General · firmware",      r"^Vers[ãa]o da Firmware$", True),
        ("PPPoE · connected clients", r"^Quantidade de Clientes Conectados$", True),
        ("System · chassis temp",   r"^Temperatura do Equipamento$", True),
        ("System · CPU temp",       r"^Temperatura de CPU$", True),
        ("System · memory %",       r"^Percentual de Uso de Memoria RAM$", True),
        ("System · disk %",         r"^Percentagem Usada em Disco .*$", True),
        ("System · CPU per core",   r"^Carga de Processamento em CPU .*$", True),
        ("System · PSU",            r"^Estado a PSU[0-9]+$", True),
        ("Interface · traffic in",  r"^Tr[aá]fego de Entrada em .*$", True),
        ("Interface · traffic out", r"^Tr[aá]fego de Sa[íi]da em .*$", True),
        ("Interface · state",       r"^Estado da interface .*$", True),
        ("Interface · speed",       r"^Velocidade de .*$", True),
        ("Optics · RX / TX",        r"^Sinal de (Recep[çc][ãa]o|Transmiss[ãa]o) em .*$", True),
        ("Optics · module temp",    r"^Temperatura em .*$", True),
        ("BGP · session state",     r"^Estado da sess[ãa]o .*$", True),
        ("Routing · route counters", r"^Total de Rotas .*$", True),
    ],
    "olt": [
        ("ICMP · state",            r"^Estado do Equipamento$", True),
        ("ICMP · loss",             r"^Perda de Pacotes$", True),
        ("ICMP · response time",    r"^Tempo de Resposta$", True),
        ("General · uptime",        r"^Tempo de Atividade$", True),
        ("General · software",      r"^Vers[ãa]o d[oa] (Software|Firmware)$", True),
        ("GPON · total ONUs",       r"^Quantidade Total de ONU/ONTs$", True),
        ("GPON · authorised ONUs per PON", r"^Quantidade de ONU.s Autorizadas em .*$", True),
        ("Health · chassis temp",   r"^Temperatura do Equipamento$", True),
        ("Boards · state",          r"^Estado da Placa .*$", True),
        ("Boards · temperature",    r"^Temperatura d[ae] Placa .*$", True),
        ("Boards · model / serial", r"^(Modelo|Numero de Serie) da Placa .*$", True),
        ("Boards · CPU",            r"^Utiliza[çc][ãa]o de CPU da Placa .*$", True),
        ("Boards · memory",         r"^Utiliza[çc][ãa]o de Mem[óo]ria da Placa .*$", True),
        ("Interface · traffic in",  r"^Tr[aá]fego de Entrada (em|na Interface) .*$", True),
        ("Interface · traffic out", r"^Tr[aá]fego de Sa[íi]da (em|na Interface) .*$", True),
        ("Interface · state",       r"^Estado da interface (Optica )?.*$", True),
        ("Interface · speed",       r"^Velocidade (da interface|de) .*$", True),
        ("Interface · errors",      r"^Pacotes de (Entrada|Sa[íi]da) com Erros .*$", True),
        ("Optics · RX / TX",        r"^(Pot[eê]ncia (Recebida|Transmitida).*|Sinal de (Recep|Transmiss).*)$", True),
        ("Power · PSU / battery",   r"^(Status|Carga|Corrente|Tens[ãa]o) .*(Fonte de Energia|Grupo de Baterias).*$", True),
    ],
}

VALUE_TYPE = {0: "float", 1: "char", 2: "log", 3: "uint", 4: "text"}


def api(url, token, method, params, timeout=60):
    body = json.dumps({"jsonrpc": "2.0", "method": method,
                       "params": params, "id": 1}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json-rpc",
        "Authorization": "Bearer %s" % token,
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read().decode())
    if "error" in out:
        raise SystemExit("Zabbix API error on %s: %s" % (method, out["error"]))
    return out["result"]


def main():
    ap = argparse.ArgumentParser(
        description="Audit a Grafana dashboard's item regexes against the Zabbix API.")
    ap.add_argument("--url", default=os.environ.get("ZBX_URL"),
                    help="API endpoint, or set ZBX_URL")
    ap.add_argument("--token", default=os.environ.get("ZBX_TOKEN"),
                    help="read-only API token, or set ZBX_TOKEN")
    ap.add_argument("--host", help="technical host name in Zabbix")
    ap.add_argument("--profile", choices=sorted(PROFILES), help="which dashboard to audit")
    ap.add_argument("--window", type=int, default=3600,
                    help="seconds to look back for a recent value (default 3600)")
    ap.add_argument("--json", metavar="FILE", help="also write the full report as JSON")
    ap.add_argument("--list-profiles", action="store_true")
    a = ap.parse_args()

    if a.list_profiles:
        for name, filters in sorted(PROFILES.items()):
            print("%-16s %d panel groups" % (name, len(filters)))
        return 0
    for need, flag in ((a.url, "--url/ZBX_URL"), (a.token, "--token/ZBX_TOKEN"),
                       (a.host, "--host"), (a.profile, "--profile")):
        if not need:
            ap.error("missing %s" % flag)

    items = api(a.url, a.token, "item.get", {
        "output": ["itemid", "name", "key_", "value_type", "units",
                   "status", "state", "error", "delay"],
        "host": a.host, "sortfield": "name"})
    if not items:
        raise SystemExit("No items found for host %r. Exact name? Token permissions?" % a.host)

    enabled = [i for i in items if i["status"] == "0"]
    unsupported = [i for i in enabled if i.get("state") == "1"]

    print("=" * 78)
    print("HOST: %s   PROFILE: %s" % (a.host, a.profile))
    print("items: %d total | %d enabled | %d unsupported" %
          (len(items), len(enabled), len(unsupported)))
    print("=" * 78)

    now = int(time.time())
    report = {"host": a.host, "profile": a.profile, "generated_at": now,
              "panels": [], "unsupported": [], "unused_items": []}
    used = set()

    print("\n%-38s %6s %9s  %s" % ("PANEL GROUP", "ITEMS", "W/ DATA", "EXAMPLE"))
    print("-" * 78)
    for label, pattern, fetch in PROFILES[a.profile]:
        rx = re.compile(pattern)
        matched = [i for i in enabled if rx.match(i["name"])]
        used.update(i["itemid"] for i in matched)

        with_data, example, sample = 0, "", matched[:8]
        if fetch and matched:
            for i in sample:
                h = api(a.url, a.token, "history.get", {
                    "output": "extend", "history": int(i["value_type"]),
                    "itemids": i["itemid"], "time_from": now - a.window,
                    "sortfield": "clock", "sortorder": "DESC", "limit": 1})
                if h:
                    with_data += 1
                    if not example:
                        example = "%s = %s" % (i["name"][:32], str(h[0]["value"])[:14])
        if not example and matched:
            example = matched[0]["name"][:48]

        print("%s%-37s %6d %9s  %s" % (
            " " if matched else "!", label[:37], len(matched),
            ("%d/%d" % (with_data, len(sample))) if fetch and matched else "-", example))

        report["panels"].append({
            "panel": label, "regex": pattern, "matched": len(matched),
            "sample_with_data": with_data, "sample_size": len(sample) if fetch else 0,
            "examples": [i["name"] for i in matched[:5]]})

    if unsupported:
        print("\n" + "=" * 78)
        print("UNSUPPORTED ITEMS (%d) — Zabbix is not collecting these:" % len(unsupported))
        print("=" * 78)
        for i in unsupported[:40]:
            print(" - %-46s %s" % (i["name"][:46], (i.get("error") or "")[:90]))
        if len(unsupported) > 40:
            print("   ... and %d more" % (len(unsupported) - 40))
        report["unsupported"] = [{"name": i["name"], "key": i["key_"],
                                  "error": i.get("error", "")} for i in unsupported]

    leftover = [i for i in enabled if i["itemid"] not in used]
    if leftover:
        print("\n" + "=" * 78)
        print("COLLECTED BUT UNUSED BY THE DASHBOARD (%d)" % len(leftover))
        print("This is where the real item names are.")
        print("=" * 78)
        for i in leftover[:60]:
            print(" - %-48s [%s]" % (i["name"][:48], i["key_"][:36]))
        if len(leftover) > 60:
            print("   ... and %d more (all of them are in the JSON report)" % (len(leftover) - 60))
        report["unused_items"] = [{"name": i["name"], "key": i["key_"],
                                   "units": i.get("units", ""),
                                   "value_type": VALUE_TYPE.get(int(i["value_type"]), "?")}
                                  for i in leftover]

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("\nWrote %s" % a.json)

    print("\nPanel groups marked with ! matched zero items — those panels will be empty.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
