const INNER_CARD_STYLE = `
  ha-card {
    background: none !important;
    border: none !important;
    box-shadow: none !important;
  }
`;

const CARD_TRANSLATIONS = {
  en: {
    roomLabel: "Room to display",
    selectRoom: "Select a room",
    noRooms: "No Raumklima rooms have been configured yet.",
    iconLabel: "Room icon (optional, for example mdi:sofa)",
    invalidSensor: "Please select a Raumklima sensor.",
    dataUnavailable: "Raumklima data is unavailable.",
    chooseRoom: "Select a room in the card editor.",
    missingDependencies:
      "The stack-in-card, Mushroom, and card-mod cards must be available in Home Assistant.",
  },
  de: {
    roomLabel: "Anzuzeigender Raum",
    selectRoom: "Raum auswählen",
    noRooms: "Es sind noch keine Raumklima-Räume eingerichtet.",
    iconLabel: "Raum-Icon (optional, zum Beispiel mdi:sofa)",
    invalidSensor: "Bitte einen Raumklima-Sensor auswählen.",
    dataUnavailable: "Raumklima-Daten sind nicht verfügbar.",
    chooseRoom: "Bitte im Karteneditor einen Raum auswählen.",
    missingDependencies:
      "Die Karten stack-in-card, Mushroom und card-mod müssen in Home Assistant verfügbar sein.",
  },
};

function cardText(hass, key) {
  const language = (hass?.language || hass?.locale?.language || "en")
    .toLowerCase()
    .split(/[-_]/)[0];
  return CARD_TRANSLATIONS[language]?.[key] || CARD_TRANSLATIONS.en[key];
}

class RaumklimaCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._roomsSignature = undefined;
  }

  set hass(hass) {
    this._hass = hass;
    const rooms = this._roomEntities();
    const signature = JSON.stringify({
      language: this._hass?.language || this._hass?.locale?.language || "en",
      rooms: rooms.map(([entityId, state]) => [
        entityId,
        state.attributes.room_name,
        state.attributes.friendly_name,
      ]),
    });
    if (signature === this._roomsSignature) return;
    this._roomsSignature = signature;
    this._render(rooms);
  }

  setConfig(config) {
    this._config = config;
    this._render();
  }

  _roomEntities() {
    return Object.entries(this._hass?.states || {})
      .filter(
        ([entityId, state]) =>
          entityId.startsWith("sensor.") && state.attributes?.room_entities,
      )
      .sort(([, left], [, right]) =>
        (left.attributes.room_name || "").localeCompare(
          right.attributes.room_name || "",
        ),
      );
  }

  _render(rooms = this._roomEntities()) {
    if (!this.shadowRoot) return;
    const options = rooms
      .map(([entityId, state]) => {
        const selected = entityId === this._config.entity ? " selected" : "";
        const label = state.attributes.room_name || state.attributes.friendly_name || entityId;
        return `<option value="${this._escape(entityId)}"${selected}>${this._escape(label)}</option>`;
      })
      .join("");

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; padding: 12px 0; }
        label { display: block; margin: 0 0 8px; font-weight: 500; }
        select, input { box-sizing: border-box; width: 100%; padding: 10px; border: 1px solid var(--divider-color); border-radius: 8px; background: var(--card-background-color); color: var(--primary-text-color); font: inherit; }
        .field + .field { margin-top: 16px; }
        p { color: var(--secondary-text-color); font-size: 13px; }
      </style>
      <div class="field">
        <label for="room">${this._escape(cardText(this._hass, "roomLabel"))}</label>
        <select id="room">
          <option value="">${this._escape(cardText(this._hass, "selectRoom"))}</option>
          ${options}
        </select>
        ${rooms.length ? "" : `<p>${this._escape(cardText(this._hass, "noRooms"))}</p>`}
      </div>
      <div class="field">
        <label for="icon">${this._escape(cardText(this._hass, "iconLabel"))}</label>
        <input id="icon" value="${this._escape(this._config.icon || "")}" placeholder="mdi:home-thermometer">
      </div>
    `;

    this.shadowRoot.querySelector("select")?.addEventListener("change", (event) => {
      this._emitConfig({ entity: event.target.value });
    });
    this.shadowRoot.querySelector("#icon")?.addEventListener("change", (event) => {
      this._emitConfig({ icon: event.target.value });
    });
  }

  _emitConfig(changes) {
    this._config = { ...this._config, ...changes };
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this._config },
        bubbles: true,
        composed: true,
      }),
    );
  }

  _escape(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        character
      ],
    );
  }
}

class RaumklimaCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._helpersPromise = undefined;
    this._stackCard = undefined;
    this._cardSignature = undefined;
    this._pendingSignature = undefined;
    this._renderRequest = 0;
  }

  static getConfigElement() {
    return document.createElement("raumklima-card-editor");
  }

  static getStubConfig() {
    return { entity: "" };
  }

  setConfig(config) {
    if (config.entity && !config.entity.startsWith("sensor.")) {
      throw new Error(cardText(this._hass, "invalidSensor"));
    }
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 4;
  }

  _state(entityId) {
    return entityId ? this._hass?.states?.[entityId] : undefined;
  }

  _signature(roomState, entities) {
    // Fensterzustand ist Teil der Signatur, damit die Karte bei jedem
    // Wechsel komplett neu gebaut wird und die card-mod-Templates (Blinken)
    // frisch ausgewertet werden (Workaround für Android Companion App).
    const windowEntityState = entities.window_open
      ? this._hass?.states?.[entities.window_open]?.state
      : undefined;
    return JSON.stringify({
      entity: this._config.entity,
      roomName: roomState.attributes.room_name,
      icon: this._config.icon,
      roomEntities: entities,
      status: roomState.state,
      windowOpen: roomState.attributes.window_open,
      closeRequired: roomState.attributes.close_required,
      windowEntityState,
    });
  }

  async _loadHelpers() {
    this._helpersPromise ||= window.loadCardHelpers();
    return this._helpersPromise;
  }

  async _render() {
    if (!this.shadowRoot || !this._hass) return;
    const roomState = this._state(this._config.entity);
    const entities = roomState?.attributes?.room_entities;
    if (!entities) {
      if (!this._stackCard) {
        this._showMessage(
          this._config.entity
            ? cardText(this._hass, "dataUnavailable")
            : cardText(this._hass, "chooseRoom"),
        );
      }
      return;
    }

    const signature = this._signature(roomState, entities);
    if (signature === this._cardSignature && this._stackCard) {
      this._stackCard.hass = this._hass;
      return;
    }
    if (signature === this._pendingSignature) return;

    this._pendingSignature = signature;
    const request = ++this._renderRequest;

    try {
      const helpers = await this._loadHelpers();
      const latestRoomState = this._state(this._config.entity);
      const latestEntities = latestRoomState?.attributes?.room_entities;
      if (!latestRoomState || !latestEntities || request !== this._renderRequest) return;

      const latestSignature = this._signature(latestRoomState, latestEntities);
      if (latestSignature !== signature) {
        this._pendingSignature = undefined;
        this._render();
        return;
      }

      const stackCard = helpers.createCardElement(
        this._makeStackConfig(latestRoomState, latestEntities),
      );
      stackCard.hass = this._hass;
      this._stackCard = stackCard;
      this._cardSignature = signature;
      this.shadowRoot.replaceChildren(stackCard);
    } catch (error) {
      console.error(error);
      if (!this._stackCard) {
        this._showMessage(
          cardText(this._hass, "missingDependencies"),
        );
      }
    } finally {
      if (this._pendingSignature === signature) this._pendingSignature = undefined;
    }
  }

  _showMessage(message) {
    this._stackCard = undefined;
    this._cardSignature = undefined;
    this.shadowRoot.innerHTML = `
      <style>ha-card { padding: 20px; color: var(--secondary-text-color); }</style>
      <ha-card>${message}</ha-card>
    `;
  }

  _makeStackConfig(roomState, entities) {
    const roomEntity = this._config.entity;
    const climateEntity = entities.climate;
    const cards = [];

    if (climateEntity) {
      cards.push({
        type: "custom:mushroom-chips-card",
        card_mod: {
          style: `
            ha-card {
              background: none !important;
              border: none !important;
              box-shadow: none !important;
              --chip-background: rgba(255,255,255,0.08) !important;
              --chip-border-width: 1px !important;
              --chip-border-color: rgba(255,255,255,0.12) !important;
              --chip-box-shadow: none !important;
              position: absolute;
              right: 10px;
              top: 10px;
              z-index: 10;
              pointer-events: auto;
            }
          `,
        },
        chips: [
          {
            type: "template",
            entity: climateEntity,
            icon: `{% set mode = state_attr('${climateEntity}', 'preset_mode') %} {% if mode == 'eco' %} mdi:leaf {% elif mode == 'boost' %} mdi:rocket-launch {% elif mode == 'frost' %} mdi:snowflake {% else %} mdi:sofa-outline {% endif %}`,
            icon_color: `{% set mode = state_attr('${climateEntity}', 'preset_mode') %} {% if mode == 'eco' %} green {% elif mode == 'boost' %} red {% elif mode == 'frost' %} blue {% else %} orange {% endif %}`,
            tap_action: {
              action: "call-service",
              service: "raumklima.toggle_climate_preset",
              data: { climate_entity: climateEntity },
            },
            hold_action: {
              action: "call-service",
              service: "climate.set_preset_mode",
              data: { preset_mode: "boost" },
              target: { entity_id: climateEntity },
            },
          },
        ],
      });

      cards.push({
        type: "custom:mushroom-climate-card",
        entity: climateEntity,
        name: roomState.attributes.room_name,
        icon: this._config.icon || "mdi:home-thermometer",
        layout: "vertical",
        secondary_info: "none",
        show_temperature_control: true,
        tap_action: { action: "more-info" },
        card_mod: { style: INNER_CARD_STYLE },
      });
    } else {
      cards.push({
        type: "custom:mushroom-template-card",
        primary: roomState.attributes.room_name || "Raumklima",
        icon: this._config.icon || "mdi:home-thermometer",
        icon_color: "orange",
        layout: "vertical",
        tap_action: { action: "more-info", entity: roomEntity },
        card_mod: {
          style: `${INNER_CARD_STYLE}
            ha-card {
              min-height: 112px;
              --mush-card-primary-font-size: 15px;
              --mush-card-primary-font-weight: 600;
            }
          `,
        },
      });
    }

    cards.push({
      type: "custom:mushroom-chips-card",
      alignment: "center",
      card_mod: {
        style: `
          ha-card {
            background: none !important;
            border: none !important;
            box-shadow: none !important;
            --chip-background: transparent !important;
            --chip-border-width: 0px !important;
            --chip-border-color: transparent !important;
            --chip-box-shadow: none !important;
            --chip-font-size: 12px !important;
            --chip-font-weight: 600 !important;
            --chip-icon-size: 16px !important;
            --chip-spacing: 4px !important;
            --chip-padding: 0 8px !important;
            padding: 2px 0 14px !important;
          }
          .chip-container { flex-wrap: nowrap !important; }
        `,
      },
      chips: [
        {
          type: "template",
          entity: entities.window_open || roomEntity,
          icon: `{% set close_required = state_attr('${roomEntity}', 'close_required') %} {% set window_open = state_attr('${roomEntity}', 'window_open') %} {% if close_required in [true, 'true'] %} mdi:check-circle {% elif window_open in [true, 'true'] %} mdi:window-open-variant {% else %} mdi:window-closed-variant {% endif %}`,
          icon_color: `{% set status = states('${roomEntity}') %} {% set close_required = state_attr('${roomEntity}', 'close_required') %} {% if status == 'ventilate' %} red {% elif status == 'ventilation_possible' %} amber {% elif close_required in [true, 'true'] or status == 'ok' %} green {% else %} disabled {% endif %}`,
          tap_action: { action: "more-info", entity: roomEntity },
          card_mod: {
            style: `
              @keyframes window-pulse {
                0%, 100% { opacity: 1; filter: brightness(1.5) drop-shadow(0 0 5px currentColor); transform: scale(1.12); }
                50% { opacity: 0.55; filter: brightness(1); transform: scale(1); }
              }
              ha-state-icon {
                {% if is_state('${entities.window_open || roomEntity}', 'on') or state_attr('${roomEntity}', 'window_open') in [true, 'true'] %}
                animation: window-pulse 1.5s ease-in-out infinite !important;
                {% else %}
                animation: none !important;
                {% endif %}
              }
            `,
          },
        },
        {
          type: "template",
          entity: entities.temperature,
          icon: "mdi:thermometer",
          content: `{{ states('${entities.temperature}') | float(0) | round(1) }}°`,
          tap_action: { action: "more-info" },
        },
        {
          type: "template",
          entity: entities.relative_humidity,
          icon: "mdi:water-percent",
          content: `{{ states('${entities.relative_humidity}') | float(0) | round(0) }}%`,
          tap_action: { action: "more-info" },
        },
      ],
    });

    return {
      type: "custom:stack-in-card",
      mode: "vertical",
      card_mod: {
        style: `
          ha-card {
            background: linear-gradient(160deg, rgba(255,255,255,0.07) 0%, rgba(255,255,255,0.02) 100%) !important;
            border: 1px solid rgba(255,255,255,0.09) !important;
            border-radius: 28px !important;
            box-shadow: 0 8px 32px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.08) !important;
            backdrop-filter: blur(20px) !important;
            overflow: hidden !important;
            position: relative;
            transition: transform 0.2s ease, box-shadow 0.2s ease !important;
          }
          ha-card:hover {
            transform: translateY(-2px) !important;
            box-shadow: 0 12px 40px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.1) !important;
          }
        `,
      },
      cards,
    };
  }
}

if (!customElements.get("raumklima-card-editor")) {
  customElements.define("raumklima-card-editor", RaumklimaCardEditor);
}
if (!customElements.get("raumklima-card")) {
  customElements.define("raumklima-card", RaumklimaCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "raumklima-card")) {
  window.customCards.push({
    type: "raumklima-card",
    name: "Raumklima",
    preview: true,
  });
}