"""Bluetooth diagnostic: list the BLE devices this PC can see, and flag any Muse.

Run it (double-click "Bluetooth Check.bat") when the app can't find the
headband. It answers one question: can this computer's Bluetooth see the Muse
at all?

- If your Muse shows up here, Bluetooth is working and the headband is
  discoverable — the problem is in the connection, and the printed MAC address
  can be passed to the app to target it directly.
- If it does NOT show up, the headband isn't being discovered: it's off,
  asleep, too far away, still connected to your phone, or this PC's adapter
  doesn't do Bluetooth Low Energy.
"""

from __future__ import annotations

import asyncio

SCAN_SECONDS = 12.0


async def _scan(seconds: float = SCAN_SECONDS) -> None:
    try:
        from bleak import BleakScanner
    except ImportError:
        print("The 'bleak' Bluetooth library isn't installed in this environment.")
        print("Install it with:  pip install bleak")
        return

    print(f"Scanning for Bluetooth LE devices for {int(seconds)} seconds...")
    print("(Make sure the Muse is on and NOT connected to your phone.)\n")

    devices = await BleakScanner.discover(timeout=seconds)
    if not devices:
        print("No Bluetooth LE devices were found at all.")
        print("That usually means Bluetooth is off, or this PC's adapter does")
        print("not support Bluetooth Low Energy (older/cheaper adapters often")
        print("only do 'classic' Bluetooth, which the Muse cannot use).")
        return

    muses = [d for d in devices if "muse" in (d.name or "").lower()]

    print(f"Found {len(devices)} Bluetooth LE device(s):\n")
    for d in sorted(devices, key=lambda x: (x.name or "￿").lower()):
        name = d.name or "(unnamed)"
        flag = "   <-- this is your Muse" if d in muses else ""
        print(f"  {name:26s} {d.address}{flag}")

    print()
    if muses:
        print("=> Your Muse is visible to this PC, so Bluetooth is working.")
        print("   If the app still won't connect, it can target the headband")
        print("   directly by MAC address:")
        for d in muses:
            print(f"       {d.address}")
    else:
        print("=> No Muse headband appeared in the scan.")
        print("   Turn it on, bring it next to the PC, make sure your phone")
        print("   isn't holding the connection, power-cycle it, and re-run.")


def main() -> int:
    try:
        asyncio.run(_scan())
    except Exception as exc:  # noqa: BLE001 - diagnostic, report anything
        print(f"Scan failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
