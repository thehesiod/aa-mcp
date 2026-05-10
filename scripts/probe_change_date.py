"""Query /reshop/cheapest for an arbitrary departure date and print options."""

import argparse
import json
import pathlib
import uuid
from urllib.parse import parse_qs, urlparse

from aa_mcp_server.api import _IMPERSONATE, _USER_AGENT, BASE, AAAPI
from aa_mcp_server.auth import AASession
from curl_cffi import requests as curl_requests
from yarl import URL


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="default")
    ap.add_argument("--locator", required=True)
    ap.add_argument("--first", required=True)
    ap.add_argument("--last", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--origin", required=True, help="3-letter origin code")
    ap.add_argument("--dest", required=True, help="3-letter destination code")
    ap.add_argument("--slice-index", type=int, default=0)
    ap.add_argument("--out", default=None, help="Save full response JSON here")
    args = ap.parse_args()

    sess = AASession(args.account)
    api = AAAPI(sess)
    res = api.reservation_detail(args.locator, args.first, args.last)
    change_url = next(p["outletUrl"] for p in res["eligibleProducts"] if p["name"] == "CHANGE")
    data_blob = parse_qs(urlparse(change_url).query)["data"][0]

    cookies = sess.cookies_dict()
    url = str(URL(BASE) / "manage-reservation/reshop/api/reshop/cheapest")
    referer = str(
        (URL(BASE) / "manage-reservation/reshop/v2/change-flights").with_query(
            {"recordLocator": args.locator, "data": data_blob, "from": "change_res"}
        )
    )
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": BASE,
        "Referer": referer,
        "User-Agent": _USER_AGENT,
        "X-XSRF-TOKEN": cookies.get("XSRF-TOKEN", ""),
    }
    body = {
        "transactionId": str(uuid.uuid4()).upper(),
        "clientId": "AACOM_ChangeRes",
        "searchCriteria": {
            "flightDetails": [
                {
                    "departureDate": args.date,
                    "originAirportCode": args.origin,
                    "destinationAirportCode": args.dest,
                    "selectedSliceForChange": True,
                    "sliceIndex": args.slice_index,
                    "flown": False,
                }
            ]
        },
        "currentSearchSlice": 0,
        "originalReservationTotalSlices": "1",
        "recordLocator": args.locator,
        "tripType": "ONEWAY",
        "data": data_blob,
        "carouselDays": True,
    }
    d = curl_requests.post(
        url, json=body, headers=headers, cookies=cookies, impersonate=_IMPERSONATE, timeout=30
    ).json()

    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(d, indent=2))

    n_pax = d["priceHeader"]["numOfPassengers"]
    prev_total = d["priceHeader"]["previousTripTotal"]["amount"]
    print(f"# {args.origin}->{args.dest} on {args.date} ({n_pax} pax, previously paid ${prev_total})")
    print()
    print("# Date carousel (delta vs previously paid):")
    for c in d.get("carouselDays", []):
        flag = "  <- target" if c["date"] == args.date else (" <- current" if c.get("currentReservationDate") else "")
        print(f"  {c['date']}  ${c['minPrice']:>5}{flag}")
    print()

    options = []
    for r in d["flightRows"]:
        info = r["sliceInfo"]
        if not info["departureDate"].startswith(args.date):
            continue
        for c in r.get("flightCells", []):
            if not isinstance(c, dict) or not c.get("fare"):
                continue
            options.append({
                "depart": info["departureTime"],
                "arrive": info["arrivalTime"],
                "duration": info["totalDurationMinutes"],
                "stops": len(info["segments"]) - 1,
                "via": "->".join(s["destination"]["airportCode"] for s in info["segments"][:-1]) or "-",
                "flights": "/".join(s["aircraft"]["flightNumber"] for s in info["segments"]),
                "cabin": c["cabinType"],
                "fareType": c.get("fareType"),
                "fare": c["fare"]["amount"],
                "netPrice": c.get("netPrice", {}).get("amount"),
                "changeType": c.get("changeType"),
                "feeWaived": c.get("changeFeeWaived"),
                "seatsLeft": c.get("seatsLeft"),
            })
    options.sort(key=lambda x: x["netPrice"] if x["netPrice"] is not None else 9e9)
    print(f"# {len(options)} cells for {args.date}, cheapest 20:")
    print(f'{"net":>7} {"fare":>6} {"cabin":15} {"depart":>9} {"arrive":>9} {"dur":>5} stops via               flights        seats fareType')
    for o in options[:20]:
        print(
            f'  ${o["netPrice"]:>5} ${o["fare"]:>5} {o["cabin"]:15} {o["depart"]:>9} {o["arrive"]:>9} {o["duration"]:>4}m  '
            f'{o["stops"]} {o["via"]:14}    {o["flights"]:14} {o["seatsLeft"]} {o["fareType"]}'
        )


if __name__ == "__main__":
    main()
