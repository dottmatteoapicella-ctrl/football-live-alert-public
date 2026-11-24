
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import api_monitor
import football_outcomes
import football_telegram_marker
api_monitor.install()

API_KEY = os.environ["BSD_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BASE_URL = "https://sports.bzzoiro.com/api/v2"
HEADERS = {"Authorization": f"Token {API_KEY}"}
STATE_FILE = Path("football_alert_state.json")

# Limite prudenziale per non richiedere troppe quote.
MAX_ODDS_REQUESTS = 30


def api_get(endpoint, params=None):
    response = requests.get(
        f"{BASE_URL}/{endpoint}",
        headers=HEADERS,
        params=params,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def send_telegram(message):
    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message},
        timeout=20,
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError("Telegram non ha confermato l'invio.")
    return result.get("result", {}).get("message_id")


def load_state():
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        state.setdefault("notified", {})
        state.setdefault("odds_cache", {})
        state.setdefault("alert_outcomes", {})
        return state

    return {"notified": {}, "odds_cache": {}, "alert_outcomes": {}}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_favorite(odds_data):
    odds = odds_data.get("odds", {})

    try:
        home = float(odds["home_win"])
        draw = float(odds["draw"])
        away = float(odds["away_win"])
    except (KeyError, TypeError, ValueError):
        return None

    if min(home, away) >= draw:
        return None

    if home < away:
        return ["Home", home]

    if away < home:
        return ["Away", away]

    return None


def upcoming_matches():
    today = datetime.now(ZoneInfo("Europe/Rome")).date()
    tomorrow = today + timedelta(days=1)

    matches = []

    for day in (today, tomorrow):
        data = api_get(
            "events/",
            {
                "status": "upcoming",
                "date_from": day.isoformat(),
                "date_to": day.isoformat(),
                "limit": 200,
            },
        )

        if isinstance(data, dict):
            matches.extend(data.get("results", []))

    return matches



def match_half(match):
    """Determina il tempo dal minuto; non invia alert se il dato è incerto."""
    status = str(match.get("status") or "").strip().lower()
    if status in {"notstarted", "not_started", "upcoming", "scheduled", "cancelled", "postponed"}:
        return None
    if status in {"ht", "half-time", "halftime", "half time", "ft", "full-time",
                  "full time", "finished", "ended", "penalties", "extra time",
                  "et", "aet"}:
        return None

    raw_minute = match.get("current_minute")
    if raw_minute is None:
        return None

    minute_text = str(raw_minute).strip()
    # Es.: 23, 45, 45+3, 90+2, 67'
    match_minute = re.fullmatch(r"(\d{1,3})(?:\s*\+\s*\d{1,2})?'?", minute_text)
    if not match_minute:
        return None

    minute = int(match_minute.group(1))
    if 1 <= minute <= 45:
        return 1
    if 46 <= minute <= 90:
        return 2
    return None


def main():
    print("=== FOOTBALL LIVE ALERT BSD ===")

    state = load_state()
    save_state(state)

    now = datetime.now(timezone.utc)
    odds_requests = 0
    new_favorites = 0
    alerts_sent = 0
    odds_missing_kickoff = 0
    odds_outside_window = 0
    odds_no_favorite = 0
    odds_failed = 0
    odds_cached = 0
    live_missing_favorite = 0
    live_missing_score = 0
    live_invalid_minute = 0
    missing_favorite_examples = []

    # FASE 1: Memorizza le favorite prima dell'inizio.
    upcoming = upcoming_matches()
    print("Partite future trovate:", len(upcoming))

    for match in upcoming:
        event_id = str(match["id"])

        if event_id in state["odds_cache"]:
            odds_cached += 1
            continue

        kickoff = match.get("event_date")

        if not kickoff:
            odds_missing_kickoff += 1
            continue

        try:
            kickoff_time = datetime.fromisoformat(
                kickoff.replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            odds_missing_kickoff += 1
            continue

        minutes_to_kickoff = (
            kickoff_time - now
        ).total_seconds() / 60

        # Memorizza le quote nelle 6 ore precedenti.
        if not 0 < minutes_to_kickoff <= 360:
            odds_outside_window += 1
            continue

        if odds_requests >= MAX_ODDS_REQUESTS:
            print("Limite richieste quote raggiunto.")
            break

        odds_requests += 1

        try:
            odds_data = api_get(f"events/{event_id}/odds/")
        except requests.RequestException as exc:
            odds_failed += 1
            print("Errore quote:", event_id, exc)
            continue

        favorite = get_favorite(odds_data)

        if not favorite:
            odds_no_favorite += 1
            continue

        state["odds_cache"][event_id] = favorite
        save_state(state)
        new_favorites += 1

        print(
            "Favorita salvata:",
            match.get("home_team"),
            "vs",
            match.get("away_team"),
            favorite,
        )

    # FASE 2: Controlla le partite LIVE.
    live_data = api_get("events/live/")
    live_matches = live_data.get("events", [])
    if not isinstance(live_matches, list):
        live_matches = []

    print("Eventi da events/live/:", len(live_matches))
    # Interroga sempre anche il catalogo: il feed live potrebbe essere parziale.
    # Accetta dal catalogo SOLO eventi con stato esplicitamente live e minuto valido.
    today = datetime.now(ZoneInfo("Europe/Rome")).date().isoformat()
    try:
        alternate = api_get("events/", {
            "status": "live", "date_from": today,
            "date_to": today, "limit": 200,
        })
        candidates = alternate.get("results", []) if isinstance(alternate, dict) else []
        if not isinstance(candidates, list):
            candidates = []
        print("Eventi da filtro alternativo status=live:", len(candidates))
        known_ids = {str(m.get("id")) for m in live_matches if isinstance(m, dict)}
        added = 0
        rejected = 0
        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("id") is None:
                rejected += 1
                continue
            status = str(candidate.get("status") or "").strip().lower()
            if status not in {"live", "inplay", "in_play", "in-progress", "in_progress"} or match_half(candidate) is None:
                rejected += 1
                continue
            event_id = str(candidate["id"])
            if event_id not in known_ids:
                live_matches.append(candidate)
                known_ids.add(event_id)
                added += 1
        print("Eventi live aggiunti dal catalogo:", added)
        print("Eventi catalogo scartati per stato/minuto:", rejected)
    except requests.RequestException as exc:
        print("Filtro live alternativo non disponibile:", type(exc).__name__)
    print("Partite con minuto valido:", sum(
        match_half(m) is not None for m in live_matches if isinstance(m, dict)
    ))

    # Aggiorna gli esiti senza cambiare le regole degli alert.
    if football_outcomes.update_halftime(state, live_matches):
        save_state(state)
    if football_outcomes.update(state, live_matches):
        save_state(state)
    # Continue looking up finished matches for recent Telegram alerts,
    # including ones already marked green, to append the verified final score.
    pending = [r for r in state["alert_outcomes"].values()
               if r.get("outcome") == "pending" and r.get("telegram_message_id")]
    if pending:
        dates = sorted({r["sent_utc"][:10] for r in pending
                        if r.get("sent_utc") and
                        (now.date() - datetime.fromisoformat(r["sent_utc"]).date()).days in (0, 1, 2)})
        for day in dates:
            try:
                completed = api_get("events/", {
                    "status": "finished", "date_from": day,
                    "date_to": day, "limit": 200,
                })
                results = completed.get("results", []) if isinstance(completed, dict) else []
                if isinstance(results, list):
                    if football_outcomes.update_halftime(state, results):
                        save_state(state)
                    if football_outcomes.update(state, results):
                        save_state(state)
            except requests.RequestException as exc:
                print("Esiti conclusi non disponibili:", day, type(exc).__name__)

    # Modifica soltanto messaggi gia' inviati, senza nuove notifiche.
    if football_telegram_marker.edit_pending(state, live_matches, TELEGRAM_TOKEN, CHAT_ID):
        save_state(state)

    for match in live_matches:
        if not isinstance(match, dict) or match.get("id") is None:
            continue
        event_id = str(match["id"])

        half = match_half(match)
        if half is None:
            live_invalid_minute += 1
            continue

        notification_key = f"{event_id}:H{half}"
        if notification_key in state["notified"]:
            continue

        # I vecchi alert senza suffisso sono trattati come primo tempo
        # per evitare notifiche duplicate dopo l'aggiornamento.
        if half == 1 and event_id in state["notified"]:
            continue

        favorite = state["odds_cache"].get(event_id)

        if not favorite:
            live_missing_favorite += 1
            if len(missing_favorite_examples) < 8:
                missing_favorite_examples.append({
                    "id": event_id, "home": match.get("home_team"),
                    "away": match.get("away_team"),
                    "minute": match.get("current_minute"),
                    "status": match.get("status"),
                })
            print("Quote pre-match non salvate:", event_id)
            continue

        home_score = match.get("home_score")
        away_score = match.get("away_score")

        if home_score is None or away_score is None:
            live_missing_score += 1
            continue

        side, odd = favorite

        losing = (
            side == "Home" and home_score < away_score
        ) or (
            side == "Away" and away_score < home_score
        )

        if not losing:
            continue

        home = match.get("home_team", "Casa")
        away = match.get("away_team", "Trasferta")
        favorite_name = home if side == "Home" else away

        message = (
            "⚽ FAVORITA IN SVANTAGGIO!\n\n"
            f"🏆 Campionato: {match.get('league_name', 'N/D')}\n"
            f"{home} {home_score}-{away_score} {away}\n"
            f"⭐ Favorita pre-match: {favorite_name}\n"
            f"📊 Quota pre-match: {odd:.2f}\n"
            f"⏱ Minuto: {match.get('current_minute', 'N/D')}'"
        )

        telegram_message_id = send_telegram(message)

        state["notified"][notification_key] = now.isoformat()
        football_outcomes.register(state, event_id, half, match, side, odd, now)
        record = state["alert_outcomes"].get(notification_key)
        if record is not None and telegram_message_id is not None:
            record["telegram_message_id"] = telegram_message_id
            record["telegram_text"] = message
        save_state(state)

        alerts_sent += 1
        print("Alert inviato:", event_id, "tempo:", half)

    print("Quote gia' in cache:", odds_cached)
    print("Quote escluse per orario fuori finestra:", odds_outside_window)
    print("Partite future senza data valida:", odds_missing_kickoff)
    print("Risposte quote senza favorita valida:", odds_no_favorite)
    print("Richieste quote fallite:", odds_failed)
    print("Eventi live esclusi per stato/minuto:", live_invalid_minute)
    print("Eventi live senza favorita pre-match:", live_missing_favorite)
    print("Eventi live senza punteggio:", live_missing_score)
    print("Esempi live senza favorita:", json.dumps(missing_favorite_examples, ensure_ascii=False, default=str))
    print("Nuove favorite salvate:", new_favorites)
    print("Richieste quote:", odds_requests)
    print("Nuovi alert Telegram:", alerts_sent)
    football_outcomes.report(state)


if __name__ == "__main__":
    main()
