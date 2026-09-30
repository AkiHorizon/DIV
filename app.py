"""
Stellar Wallet Assets & Dividenden Visualizer
Bereit für Streamlit Community Cloud / GitHub
"""

import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import networkx as nx
from pyvis.network import Network
import streamlit.components.v1 as components
import json
import tempfile
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ============================================================
# Konfiguration
# ============================================================
HORIZON = "https://horizon.stellar.org"
PAGE_TITLE = "Stellar Wallet Assets & Dividenden"
MAX_PAYMENTS_SCAN = 200          # wie weit zurück nach dem letzten Eingang gesucht wird

st.set_page_config(
    page_title=PAGE_TITLE,
    page_icon="🌟",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================================
# Hilfsfunktionen – Horizon API
# ============================================================

def asset_key(bal: dict) -> str:
    """Eindeutiger Schlüssel für ein Asset."""
    if bal.get("asset_type") == "native":
        return "XLM"
    return f"{bal.get('asset_code')}:{bal.get('asset_issuer')}"

def asset_display(key: str) -> str:
    """Schöne Anzeige."""
    if key == "XLM":
        return "XLM (native)"
    code, issuer = key.split(":", 1)
    return f"{code} ({issuer[:4]}…{issuer[-4:]})"

@st.cache_data(ttl=60, show_spinner=False)
def fetch_account(address: str) -> Optional[dict]:
    """Holt Account-Details inkl. Balances."""
    try:
        r = requests.get(f"{HORIZON}/accounts/{address}", timeout=15)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Fehler beim Laden des Accounts: {e}")
        return None

@st.cache_data(ttl=120, show_spinner=False)
def fetch_last_incoming(address: str, asset_key_str: str) -> Optional[dict]:
    """
    Sucht den letzten eingehenden Payment für ein bestimmtes Asset.
    Gibt dict mit amount, from, created_at, type zurück oder None.
    """
    try:
        url = f"{HORIZON}/accounts/{address}/payments"
        params = {"order": "desc", "limit": MAX_PAYMENTS_SCAN}
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        records = r.json().get("_embedded", {}).get("records", [])

        for op in records:
            op_type = op.get("type")
            # Nur eingehende Zahlungen betrachten
            to_addr = op.get("to") or op.get("account")  # create_account hat "account"
            if to_addr != address:
                continue

            # Asset bestimmen
            if op_type == "create_account":
                # create_account ist immer XLM
                if asset_key_str == "XLM":
                    return {
                        "amount": op.get("starting_balance"),
                        "from": op.get("funder") or op.get("source_account"),
                        "created_at": op.get("created_at"),
                        "type": "create_account",
                        "tx": op.get("transaction_hash")
                    }
            elif op_type in ("payment", "path_payment_strict_receive", "path_payment_strict_send"):
                if op.get("asset_type") == "native":
                    key = "XLM"
                else:
                    key = f"{op.get('asset_code')}:{op.get('asset_issuer')}"
                if key == asset_key_str:
                    return {
                        "amount": op.get("amount"),
                        "from": op.get("from") or op.get("source_account"),
                        "created_at": op.get("created_at"),
                        "type": op_type,
                        "tx": op.get("transaction_hash")
                    }
        return None
    except Exception:
        return None

def balances_to_dataframe(account: dict) -> pd.DataFrame:
    """Wandelt balances in ein übersichtliches DataFrame um."""
    rows = []
    for b in account.get("balances", []):
        key = asset_key(b)
        rows.append({
            "Asset-Key": key,
            "Asset": asset_display(key),
            "Balance": float(b.get("balance", 0)),
            "Limit": b.get("limit"),
            "Authorized": b.get("is_authorized", True) if b.get("asset_type") != "native" else True,
            "Asset Type": b.get("asset_type"),
            "Code": b.get("asset_code") if b.get("asset_type") != "native" else "XLM",
            "Issuer": b.get("asset_issuer") if b.get("asset_type") != "native" else None,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Balance", ascending=False).reset_index(drop=True)
    return df

# ============================================================
# Session-State Initialisierung
# ============================================================

def init_state():
    defaults = {
        "address": "",
        "account_data": None,
        "df_balances": pd.DataFrame(),
        "main_assets": {},          # {main_key: [div_key1, div_key2, ...]}
        "last_incomings": {},       # {asset_key: last_payment_dict}
        "colors": {
            "main": "#e63946",
            "dividend": "#457b9d",
            "xlm": "#2a9d8f",
            "background": "#1a1a2e",
            "text": "#f1faee",
            "edge": "#a8dadc",
        },
        "chart_theme": "plotly_dark",
        "node_size_main": 35,
        "node_size_div": 22,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

# ============================================================
# Sidebar – Einstellungen
# ============================================================

with st.sidebar:
    st.header("⚙️ Einstellungen")

    st.subheader("Farben")
    st.session_state.colors["main"] = st.color_picker("Main-Asset Farbe", st.session_state.colors["main"])
    st.session_state.colors["dividend"] = st.color_picker("Dividenden Farbe", st.session_state.colors["dividend"])
    st.session_state.colors["xlm"] = st.color_picker("XLM Farbe", st.session_state.colors["xlm"])
    st.session_state.colors["edge"] = st.color_picker("Kanten Farbe (Graph)", st.session_state.colors["edge"])

    st.subheader("Graph-Optionen")
    st.session_state.node_size_main = st.slider("Knotengröße Main", 15, 60, st.session_state.node_size_main)
    st.session_state.node_size_div = st.slider("Knotengröße Dividende", 10, 40, st.session_state.node_size_div)
    st.session_state.chart_theme = st.selectbox(
        "Plotly Theme",
        ["plotly_dark", "plotly", "plotly_white", "ggplot2", "seaborn"],
        index=0
    )

    st.divider()
    st.subheader("Konfiguration speichern / laden")

    # Download aktuelle Config
    config = {
        "main_assets": st.session_state.main_assets,
        "colors": st.session_state.colors,
        "node_size_main": st.session_state.node_size_main,
        "node_size_div": st.session_state.node_size_div,
        "chart_theme": st.session_state.chart_theme,
    }
    st.download_button(
        "⬇️ Config als JSON herunterladen",
        data=json.dumps(config, indent=2),
        file_name="stellar_wallet_config.json",
        mime="application/json"
    )

    uploaded = st.file_uploader("Config hochladen", type=["json"])
    if uploaded is not None:
        try:
            cfg = json.load(uploaded)
            st.session_state.main_assets = cfg.get("main_assets", {})
            st.session_state.colors.update(cfg.get("colors", {}))
            st.session_state.node_size_main = cfg.get("node_size_main", 35)
            st.session_state.node_size_div = cfg.get("node_size_div", 22)
            st.session_state.chart_theme = cfg.get("chart_theme", "plotly_dark")
            st.success("Config geladen!")
            st.rerun()
        except Exception as e:
            st.error(f"Fehler beim Laden: {e}")

# ============================================================
# Hauptbereich
# ============================================================

st.title("🌟 Stellar Wallet Assets & Dividenden")
st.markdown(
    "Gib eine öffentliche Stellar-Adresse ein. Markiere **Main-Assets** und weise ihnen "
    "**Dividenden** (andere Trustlines) zu. Der letzte Eingang jeder Dividende wird angezeigt."
)

col1, col2 = st.columns([3, 1])
with col1:
    address_input = st.text_input(
        "Öffentliche Adresse (G…)",
        value=st.session_state.address,
        placeholder="GXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    )
with col2:
    st.write("")  # Abstand
    load_btn = st.button("Wallet laden", type="primary", use_container_width=True)

if load_btn and address_input:
    address_input = address_input.strip()
    if not (address_input.startswith("G") and len(address_input) == 56):
        st.error("Ungültige Stellar-Adresse (muss mit G beginnen und 56 Zeichen lang sein).")
    else:
        with st.spinner("Lade Account von Horizon…"):
            acc = fetch_account(address_input)
            if acc is None:
                st.error("Account nicht gefunden oder noch nicht aktiviert.")
            else:
                st.session_state.address = address_input
                st.session_state.account_data = acc
                st.session_state.df_balances = balances_to_dataframe(acc)
                st.session_state.last_incomings = {}  # Cache zurücksetzen
                st.success(f"Account geladen – {len(st.session_state.df_balances)} Assets gefunden.")

# ---------- Wenn Daten vorhanden ----------
if st.session_state.account_data is not None and not st.session_state.df_balances.empty:

    df = st.session_state.df_balances.copy()
    asset_keys = df["Asset-Key"].tolist()

    # -------------------------------------------------------
    # 1. Tabelle + Main-Asset / Dividenden-Zuweisung
    # -------------------------------------------------------
    st.header("1. Assets der Wallet")

    # Anzeige-Tabelle
    display_df = df[["Asset", "Balance", "Authorized", "Asset Type"]].copy()
    display_df["Balance"] = display_df["Balance"].map(lambda x: f"{x:,.7f}".rstrip("0").rstrip("."))
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.subheader("Main-Assets & Dividenden zuweisen")

    # Auswahl Main-Asset
    main_options = ["— keines —"] + [asset_display(k) for k in asset_keys]
    main_display_to_key = {asset_display(k): k for k in asset_keys}

    selected_main_display = st.selectbox(
        "Main-Asset auswählen (zum Bearbeiten)",
        options=main_options,
        key="main_select"
    )

    if selected_main_display != "— keines —":
        main_key = main_display_to_key[selected_main_display]

        # Aktuelle Dividenden dieses Main-Assets
        current_divs = st.session_state.main_assets.get(main_key, [])

        # Multiselect für Dividenden (alle außer dem Main selbst)
        possible_divs = [k for k in asset_keys if k != main_key]
        div_display = [asset_display(k) for k in possible_divs]
        div_display_to_key = {asset_display(k): k for k in possible_divs}

        default_div_display = [asset_display(k) for k in current_divs if k in possible_divs]

        selected_divs_display = st.multiselect(
            f"Dividenden für **{selected_main_display}** zuweisen",
            options=div_display,
            default=default_div_display,
            key=f"divs_{main_key}"
        )

        # Speichern
        new_divs = [div_display_to_key[d] for d in selected_divs_display]
        st.session_state.main_assets[main_key] = new_divs

        # Optional: Main-Asset entfernen
        if st.button("Dieses Main-Asset entfernen"):
            if main_key in st.session_state.main_assets:
                del st.session_state.main_assets[main_key]
            st.rerun()

    # Übersicht der aktuellen Zuordnungen
    if st.session_state.main_assets:
        st.markdown("**Aktuelle Zuordnungen:**")
        for m, divs in st.session_state.main_assets.items():
            div_str = ", ".join(asset_display(d) for d in divs) if divs else "—"
            st.markdown(f"- **{asset_display(m)}** → {div_str}")

    # -------------------------------------------------------
    # 2. Letzte Eingänge für Dividenden laden
    # -------------------------------------------------------
    st.header("2. Letzte Eingänge der Dividenden")

    all_div_keys = set()
    for divs in st.session_state.main_assets.values():
        all_div_keys.update(divs)

    if all_div_keys:
        if st.button("Letzte Eingänge jetzt abrufen", type="secondary"):
            progress = st.progress(0)
            for i, key in enumerate(all_div_keys):
                with st.spinner(f"Suche letzten Eingang für {asset_display(key)}…"):
                    last = fetch_last_incoming(st.session_state.address, key)
                    st.session_state.last_incomings[key] = last
                progress.progress((i + 1) / len(all_div_keys))
            st.success("Fertig!")
            st.rerun()

        # Tabelle der letzten Eingänge
        rows = []
        for key in sorted(all_div_keys):
            last = st.session_state.last_incomings.get(key)
            if last:
                rows.append({
                    "Dividende": asset_display(key),
                    "Betrag": last.get("amount"),
                    "Von": last.get("from", "")[:8] + "…" if last.get("from") else "",
                    "Datum": last.get("created_at", "")[:19].replace("T", " "),
                    "Typ": last.get("type"),
                })
            else:
                rows.append({
                    "Dividende": asset_display(key),
                    "Betrag": "— noch nicht geladen / kein Eingang gefunden",
                    "Von": "",
                    "Datum": "",
                    "Typ": "",
                })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("Noch keine Dividenden zugewiesen.")

    # -------------------------------------------------------
    # 3. Grafische Aufbereitungen
    # -------------------------------------------------------
    st.header("3. Grafische Aufbereitungen")

    chart_type = st.selectbox(
        "Darstellung wählen",
        [
            "Tortendiagramm (Balances)",
            "Balkendiagramm (Balances)",
            "Treemap",
            "Netzwerk-Graph (Main → Dividenden)",
            "Sunburst (Main + Dividenden)",
        ]
    )

    colors = st.session_state.colors
    theme = st.session_state.chart_theme

    # ---- Tortendiagramm ----
    if chart_type == "Tortendiagramm (Balances)":
        fig = px.pie(
            df,
            values="Balance",
            names="Asset",
            title="Asset-Verteilung (Balance)",
            color_discrete_sequence=px.colors.qualitative.Set3,
            template=theme
        )
        fig.update_traces(textposition="inside", textinfo="percent+label")
        st.plotly_chart(fig, use_container_width=True)

    # ---- Balkendiagramm ----
    elif chart_type == "Balkendiagramm (Balances)":
        fig = px.bar(
            df,
            x="Asset",
            y="Balance",
            title="Balances der Assets",
            color="Balance",
            color_continuous_scale="Viridis",
            template=theme
        )
        fig.update_layout(xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)

    # ---- Treemap ----
    elif chart_type == "Treemap":
        fig = px.treemap(
            df,
            path=["Asset"],
            values="Balance",
            title="Treemap der Asset-Balances",
            color="Balance",
            color_continuous_scale="Blues",
            template=theme
        )
        st.plotly_chart(fig, use_container_width=True)

    # ---- Netzwerk-Graph ----
    elif chart_type == "Netzwerk-Graph (Main → Dividenden)":
        if not st.session_state.main_assets:
            st.warning("Bitte zuerst Main-Assets und Dividenden zuweisen.")
        else:
            G = nx.DiGraph()
            # Nodes
            for main_k, divs in st.session_state.main_assets.items():
                G.add_node(main_k, typ="main", label=asset_display(main_k))
                for d in divs:
                    G.add_node(d, typ="div", label=asset_display(d))
                    G.add_edge(main_k, d)

            # pyvis
            net = Network(height="650px", width="100%", directed=True, bgcolor=colors["background"], font_color=colors["text"])
            net.from_nx(G)

            for node in net.nodes:
                n_id = node["id"]
                typ = G.nodes[n_id].get("typ", "div")
                if typ == "main":
                    node["color"] = colors["main"]
                    node["size"] = st.session_state.node_size_main
                else:
                    node["color"] = colors["dividend"]
                    node["size"] = st.session_state.node_size_div
                node["label"] = G.nodes[n_id].get("label", n_id)
                # Tooltip mit letztem Eingang
                last = st.session_state.last_incomings.get(n_id)
                if last:
                    node["title"] = f"{node['label']}<br>Letzter Eingang: {last.get('amount')} am {last.get('created_at', '')[:10]}"
                else:
                    node["title"] = node["label"]

            for edge in net.edges:
                edge["color"] = colors["edge"]

            net.set_options("""
            {
              "physics": {
                "forceAtlas2Based": {
                  "gravitationalConstant": -70,
                  "centralGravity": 0.01,
                  "springLength": 140,
                  "springConstant": 0.08
                },
                "minVelocity": 0.75,
                "solver": "forceAtlas2Based"
              }
            }
            """)

            with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp:
                net.save_graph(tmp.name)
                html = open(tmp.name, encoding="utf-8").read()
                os.unlink(tmp.name)
            components.html(html, height=670, scrolling=True)

    # ---- Sunburst ----
    elif chart_type == "Sunburst (Main + Dividenden)":
        if not st.session_state.main_assets:
            st.warning("Bitte zuerst Main-Assets und Dividenden zuweisen.")
        else:
            # Daten für Sunburst vorbereiten
            sun_rows = []
            for main_k, divs in st.session_state.main_assets.items():
                main_bal = df.loc[df["Asset-Key"] == main_k, "Balance"]
                main_bal = float(main_bal.iloc[0]) if not main_bal.empty else 0.0
                sun_rows.append({"ids": main_k, "labels": asset_display(main_k), "parents": "", "values": main_bal})
                for d in divs:
                    d_bal = df.loc[df["Asset-Key"] == d, "Balance"]
                    d_bal = float(d_bal.iloc[0]) if not d_bal.empty else 0.0
                    sun_rows.append({"ids": d, "labels": asset_display(d), "parents": main_k, "values": d_bal})

            sun_df = pd.DataFrame(sun_rows)
            fig = px.sunburst(
                sun_df,
                ids="ids",
                names="labels",
                parents="parents",
                values="values",
                title="Sunburst: Main-Assets und ihre Dividenden",
                template=theme
            )
            st.plotly_chart(fig, use_container_width=True)

# ============================================================
# Footer
# ============================================================
st.divider()
st.caption(
    f"Datenquelle: Stellar Horizon ({HORIZON}) · "
    f"Historie begrenzt auf ca. 1 Jahr · "
    f"Rate-Limit beachten · Stand: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
)
