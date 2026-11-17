# Hardware: the Altice Labs Connected Home kit

The 7 physical devices behind the Altice `connected-home` MCP, and how each one reaches it. What we can see and change through the MCP is in [`../mcp-capabilities.md`](../mcp-capabilities.md).

## Devices
| # | Device | Model | Radio | Connects via | In the MCP as | Notes |
|---|---|---|---|---|---|---|
| 1 | IP camera | Nivian **NVS-IPC-IS2** | Wi-Fi 2.4 GHz | Directly | Camera (`camera`) | 1080p, pan/tilt, night vision, mic, speaker, microSD. Through the MCP: settings only, no video |
| 2 | Climate sensor | Nedis ZBSC10WT | **Zigbee 3.0** · CR2450 battery | Security gateway | Temperature sensor (`temperatureSensor`) | Temperature + humidity |
| 3 | Door/window sensor | Home Sécurité 852524 (SSCS-D3D) | **868 MHz** | Security gateway | Contact sensor (`contactSensor`) | Open/close detection |
| 4 | Smart plug | Altice F3S401-NF | Wi-Fi 2.4 GHz | Directly | Plug (`plug`) | 16 A relay, countdown, voltage/current/power. **Only a small lamp on it** |
| 5 | RGB smart bulb | **FSL A60 9 W RGB** (supplied as "Altice A60 9W RGB") | Wi-Fi 2.4 GHz + Bluetooth LE | Directly | Light (`bulb`) | E27; RGB + tunable white, scenes |
| 6 | Smoke alarm | ARIZA S100A-AAA-W (SSSD-763) | **868 MHz (no Wi-Fi)** | Security gateway | Smoke sensor (`smokeSensor`) | Photoelectric; very loud test button |
| 7 | Security gateway | **SFR** MS-WES-01 (852441), "Centrale de Sécurité" | Wi-Fi 2.4 GHz, Ethernet, LTE/4G, **868 MHz + Zigbee** | Ethernet to the router | Security hub (`gateway`) | Hub for devices 2, 3 and 6; 3.7 V 5000 mAh backup battery |

## How the kit reaches the MCP
```
 climate · door · smoke sensors ──(Zigbee / 868 MHz)──► security gateway ──(Ethernet)──┐
 camera · smart plug · RGB bulb ──(2.4 GHz Wi-Fi)──────────────────────────────────────┤
                                                                                       ▼
                         router ──► internet ──► Altice cloud platform ──► connected-home MCP ──► us
```
- **The three sensors only reach the cloud through the gateway.** If the gateway goes offline, all of them go silent in the MCP together, which is what the MCP showed on 14 Nov at 03:03.
- The camera, plug and bulb need a good **2.4 GHz** Wi-Fi signal where they're installed.
- Every device must stay **registered to the Altice platform**. Re-pairing it in another app takes it out of the MCP.

## Kit checklist
- Ethernet cable ×1 (gateway to router)
- E27 lamp socket (for the bulb)
- **A small low-power lamp for the smart plug: never heaters, kettles or other high loads**
- A router with 2.4 GHz Wi-Fi, a free Ethernet port and internet access

## Safety rules
- Only a small lamp on the plug, and ask before switching it.
- Warn people before pressing the smoke alarm's test button: it's very loud.
- Don't arm the alarm.
- Don't press the gateway's link/chain button: it resets its pairing.
- Don't remove, factory-reset or re-pair devices: it breaks their link to the Altice platform.
- Through the MCP, never touch the hub's `restart` or `factoryReset`.
- Never commit the MCP API key or tokens (`.mcp.json` is gitignored).

## Open items
| Item | Why it matters |
|---|---|
| ~~Gateway's SFR registration reset; devices offline in the MCP since 13–14 Nov~~ | **Resolved 17 Nov:** the kit is back on the platform and reporting. Re-pairing gave the contact and temperature sensors new IDs ([`../mcp-capabilities.md`](../mcp-capabilities.md)) |
| The smoke sensor hasn't reported since 14 Nov 03:03 UTC | Smoke can't be detected through the MCP until it's re-paired to the gateway. Ask the organisers; don't factory-reset it ourselves |
| Camera motion detection is off and can't be enabled through the MCP | Asked the organisers whether their SmartHome app can enable it, and whether a scenario can trigger on `motionDetected` |
