# Raumklima

A Home Assistant integration that provides room-by-room ventilation advice. It compares indoor and outdoor air, can read the VTherm window contact, and estimates humidity at a cold wall location.

## Features

- Advice on when to ventilate and close windows
- Temperature, relative and absolute humidity, and estimated wall humidity sensors
- Warnings for sustained high room or wall humidity
- Built-in Lovelace card with optional VTherm climate controls

Wall humidity is a model estimate, not a direct surface measurement.

## Installation and setup

### Install with HACS

1. In HACS, open the three-dot menu and select **Custom repositories**.
2. Add `https://github.com/Schamoo/Raumklima` and choose **Integration** as the category.
3. Add the repository, find **Raumklima** in HACS, and download it.
4. Restart Home Assistant and add **Raumklima** under **Settings → Devices & services**.

### Manual installation

Copy `custom_components/raumklima` to `<config>/custom_components/raumklima`, then restart Home Assistant.

### Setup

On initial setup, configure the global thresholds and outdoor sensors. Add another entry for each room and select a VTherm entity or a room temperature and humidity sensor.

For VTherm, the integration reads its configured window contact automatically. Set the wall-humidity model's calibration factor in that room's options.

An optional Home Assistant automation example for ventilation, mold, and window-closing notifications is available in [`examples/automation_raumklima.yaml`](examples/automation_raumklima.yaml). Adapt its notification script and presence entity to your setup.

## Lovelace card

The integration serves the card and registers it automatically in storage-mode dashboards. Add it from the card picker and select the room's Raumklima sensor.

Required frontend cards: `stack-in-card`, Mushroom, and `card-mod`. For YAML-managed resources, add the card manually:

```yaml
lovelace:
  resources:
    - url: /raumklima/raumklima-card.js?v=1.0.6
      type: module
```

The preset control switches between `eco` and `comfort` using the `raumklima.toggle_climate_preset` service.

## Build

No separate build step is required. Home Assistant loads the Python integration and JavaScript card directly. The storage-mode card resource uses the integration version from `manifest.json` for cache busting. Update the version query in YAML-managed resource URLs when upgrading the integration.

## Tests

Python 3.14 is required. Install the test dependencies and run the tests:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements_test.txt
python -m pytest -q -p no:cacheprovider
```

On Linux or macOS, activate the environment with `source .venv/bin/activate`.
