#!/usr/bin/env python3
"""
OANDA v20 Account & API Capability Discovery
---------------------------------------------
READ-ONLY diagnostic: only GET requests are issued. No order, trade,
or account setting is ever created or modified.

Requires: pip install requests
"""

import getpass
import json
import sys
from datetime import datetime, timezone

import requests

ENV = {
    "1": ("Practice / Demo", "https://api-fxpractice.oanda.com"),
    "2": ("Live", "https://api-fxtrade.oanda.com"),
}


def get(session, base, path, params=None):
    """GET wrapper that never raises; returns (status_code, json_or_None, raw_text)."""
    try:
        r = session.get(
            base + path,
            params=params,
            timeout=20,
            headers={"Accept-Datetime-Format": "RFC3339"},
        )
        try:
            data = r.json()
        except ValueError:
            data = None
        return r.status_code, data, r.text[:1000]
    except requests.RequestException as e:
        return None, None, str(e)


def label(status):
    return {
        200: "OK",
        400: "400 Bad Request",
        401: "401 Unauthorized",
        403: "403 Forbidden",
        404: "404 Not Found",
        405: "405 Method Not Allowed",
        429: "429 Rate Limited",
    }.get(status, "NETWORK ERROR" if status is None else str(status))


def classify(tags, mt4):
    t = {str(x).upper() for x in (tags or [])}
    if "MT4" in t and "SPREAD_BETTING" in t:
        return "MT4 + SPREAD BETTING"
    if "MT4" in t:
        return "MT4"
    if "SPREAD_BETTING" in t:
        return "SPREAD BETTING"
    if "CFD" in t:
        return "CFD"
    if mt4 is not None:
        return "MT4 (tag not reported)"
    return "V20 / OTHER"


def compact(a):
    keys = [
        "id", "alias", "currency", "balance", "NAV", "marginUsed",
        "marginAvailable", "marginRate", "openTradeCount",
        "openPositionCount", "pendingOrderCount", "hedgingEnabled",
        "financingMode", "createdTime", "lastTransactionID",
    ]
    return {k: a.get(k) for k in keys if k in a}


def main():
    print("=" * 72)
    print("OANDA V20 ACCOUNT & API CAPABILITY DISCOVERY")
    print("=" * 72)
    print("READ-ONLY: only GET requests; no order/trade is created or changed.\n")
    print("1) Practice / Demo")
    print("2) Live")
    choice = input("Select [1/2]: ").strip()
    if choice not in ENV:
        sys.exit("Invalid selection.")
    env, base = ENV[choice]

    token = getpass.getpass(f"API Token ({env}): ").strip()
    if not token:
        sys.exit("No token supplied.")

    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "oanda-v20-account-diagnostic/2.0",
    })

    print("\n[1] Discovering accounts...")
    st, data, raw = get(s, base, "/v3/accounts")
    if st != 200:
        print("FAILED:", label(st))
        if isinstance(data, dict):
            print(data.get("errorMessage", raw))
        else:
            print(raw)
        sys.exit(1)

    accounts = data.get("accounts", [])
    print(f"Found {len(accounts)} authorized v20 account(s).")

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": env,
        "base_url": base,
        "account_count": len(accounts),
        "safety": {
            "read_only": True,
            "mutating_methods_used": [],
            "order_creation_attempted": False,
            "trade_attempted": False,
        },
        "accounts": [],
    }

    for n, props in enumerate(accounts, 1):
        aid = props.get("id")
        tags = props.get("tags", [])
        mt4 = props.get("mt4AccountID")
        typ = classify(tags, mt4)

        print("\n" + "-" * 72)
        print(f"ACCOUNT {n}/{len(accounts)}")
        print("-" * 72)
        print("Type       :", typ)
        print("Account ID :", aid)
        print("MT4        :", "YES" if mt4 is not None else "NO")
        if mt4 is not None:
            print("MT4 ID     :", mt4)
        print("Tags       :", tags)

        item = {
            "account_id": aid,
            "type": typ,
            "tags": tags,
            "mt4AccountID": mt4,
            "read_access": {},
            "summary": None,
            "details": None,
            "instrument_count": None,
            "probe_counts": {},
        }

        st, body, raw = get(s, base, f"/v3/accounts/{aid}/summary")
        item["read_access"]["summary"] = {"status": st, "label": label(st), "accessible": st == 200}
        print("  summary     ->", label(st))
        if st == 200 and isinstance(body, dict):
            a = body.get("account", {})
            item["summary"] = compact(a)
            for k in ["balance", "NAV", "marginUsed", "marginAvailable",
                      "openTradeCount", "openPositionCount", "pendingOrderCount"]:
                if k in a:
                    print(f"    {k:<20}: {a[k]}")

        st, body, raw = get(s, base, f"/v3/accounts/{aid}")
        item["read_access"]["details"] = {"status": st, "label": label(st), "accessible": st == 200}
        print("  details     ->", label(st))
        if st == 200 and isinstance(body, dict):
            item["details"] = compact(body.get("account", {}))

        st, body, raw = get(s, base, f"/v3/accounts/{aid}/instruments")
        item["read_access"]["instruments"] = {"status": st, "label": label(st), "accessible": st == 200}
        print("  instruments ->", label(st))
        if st == 200 and isinstance(body, dict):
            ins = body.get("instruments", [])
            item["instrument_count"] = len(ins)
            print("    Tradable instruments returned:", len(ins))
            names = [x.get("name") for x in ins if x.get("name")]
            if names:
                preview = ", ".join(names[:10]) + (", ..." if len(names) > 10 else "")
                print("    Preview:", preview)

        for name, path, params in [
            ("orders", f"/v3/accounts/{aid}/orders", {"count": 1}),
            ("trades", f"/v3/accounts/{aid}/trades", {"count": 1}),
            ("positions", f"/v3/accounts/{aid}/positions", None),
        ]:
            st, body, raw = get(s, base, path, params)
            item["read_access"][name] = {"status": st, "label": label(st), "accessible": st == 200}
            print(f"  {name:<12}->", label(st))
            if st == 200 and isinstance(body, dict) and isinstance(body.get(name), list):
                item["probe_counts"][name] = len(body[name])
                print("    Returned in probe:", len(body[name]))
            elif isinstance(body, dict) and body.get("errorMessage"):
                item["read_access"][name]["errorMessage"] = body["errorMessage"]

        item["trading_write_permission"] = {
            "tested": False,
            "reason": "Not tested because a real POST could create an order.",
        }
        report["accounts"].append(item)

    print("\n" + "=" * 72)
    print("ACCOUNT SUMMARY")
    print("=" * 72)
    for i, a in enumerate(report["accounts"], 1):
        ok = [k for k, v in a["read_access"].items() if v["accessible"]]
        print(f"\n[{i}] {a['type']}")
        print("    Account ID :", a["account_id"])
        print("    Tags       :", a["tags"])
        if a["mt4AccountID"] is not None:
            print("    MT4 ID     :", a["mt4AccountID"])
        print("    READ OK    :", ", ".join(ok) if ok else "NONE")
        print("    WRITE/TRADE: NOT TESTED")

    fn = "oanda_account_diagnostic_report.json"
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 72)
    print("DONE")
    print("=" * 72)
    print("JSON report:", fn)
    print("Only GET requests were used; token was not saved.")


if __name__ == "__main__":
    main()
