
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import api_monitor
import football_outcomes
import football_poe
import football_f1
import football_datasports
import football_telegram_marker
api_monitor.install()

API_KEY = os.environ["BSD_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()

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


def send_telegram(message, chat_id=None):
    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": chat_id or CHAT_ID, "text": message},
        timeout=20,
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError("Telegram non ha confermato l'invio.")
    message_id = result.get("result", {}).get("message_id")
    if not isinstance(message_id, int) or message_id <= 0:
        raise RuntimeError("Telegram non ha restituito un ID messaggio valido.")
    return message_id


def send_channel(record, message):
    if not CHANNEL_ID or CHANNEL_ID == CHAT_ID:
        return
    try:
        record["telegram_channel_message_id"] = send_telegram(message, CHANNEL_ID)
        print("Invio canale Telegram: 1")
    except (requests.RequestException, RuntimeError, ValueError):
        print("Invio canale Telegram fallito: 1")


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
    football_datasports.observe(state, now, save_state)
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
    live_matches = live_data.get("events") or live_data.get("results") or []
    if not isinstance(live_matches, list):
        raise ValueError("Formato feed live non valido")
    live_matches = [m for m in live_matches if isinstance(m, dict) and m.get("id") is not None]

    print("Eventi da events/live/:", len(live_matches))
    print("Diagnostica feed live events presente:", int("events" in live_data))
    print("Diagnostica feed live results presente:", int("results" in live_data))
    print("Diagnostica feed live minuto assente:", sum(isinstance(m, dict) and m.get("current_minute") is None for m in live_matches))
    print("Diagnostica feed live minuto non valido:", sum(isinstance(m, dict) and m.get("current_minute") is not None and match_half(m) is None for m in live_matches))

    # Recover incomplete primary live events before consulting the catalog.
    primary_checked = 0
    primary_recovered = 0
    for index, match in enumerate(live_matches):
        if primary_checked >= 8:
            break
        if match_half(match) is not None and football_outcomes.score(match) is not None:
            continue
        primary_checked += 1
        try:
            detail = api_get(f"events/{match['id']}/")
            if isinstance(detail, dict) and str(detail.get("id")) == str(match["id"]):
                merged = {**match, **detail}
                if match_half(merged) is not None and football_outcomes.score(merged) is not None:
                    live_matches[index] = merged
                    primary_recovered += 1
        except (requests.RequestException, ValueError):
            pass
    print("Diagnostica dettagli feed primario controllati:", primary_checked)
    print("Diagnostica dettagli feed primario recuperati:", primary_recovered)

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
        known_ids = {str(m["id"]): i for i, m in enumerate(live_matches)}
        added = 0
        rejected = 0
        statuses = {}
        for m in candidates:
            if isinstance(m, dict):
                raw = str(m.get("status") or "").strip().lower()
                status_key = raw if raw in {"live", "inprogress", "in_progress", "inplay", "notstarted", "upcoming", "finished", "cancelled", "postponed", "scheduled", "ht", "halftime"} else "other"
                statuses[status_key] = statuses.get(status_key, 0) + 1
        for status_key, count in sorted(statuses.items()):
            print(f"Diagnostica catalogo stato {status_key}: {count}")
        detail_budget = 8 - primary_checked
        detail_checked = 0
        detail_recovered = 0
        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("id") is None:
                rejected += 1
                continue
            status = str(candidate.get("status") or "").strip().lower()
            live_statuses = {"live", "inplay", "in_play", "in-progress", "in_progress", "inprogress"}
            due = False
            if status in {"notstarted", "not_started", "upcoming", "scheduled"}:
                try:
                    kickoff = datetime.fromisoformat(candidate["event_date"].replace("Z", "+00:00"))
                    due = 0 <= (now - kickoff).total_seconds() <= 7200
                except (KeyError, ValueError, TypeError, AttributeError):
                    pass
            if status not in live_statuses and not due:
                rejected += 1
                continue
            if (due or match_half(candidate) is None) and detail_checked < detail_budget:
                detail_checked += 1
                try:
                    detail = api_get(f"events/{candidate['id']}/")
                    if isinstance(detail, dict) and str(detail.get("id")) == str(candidate["id"]) and str(detail.get("status") or "").strip().lower() in {"live", "inplay", "in_play", "in-progress", "in_progress", "inprogress"} and match_half(detail) is not None:
                        candidate = {**candidate, **detail}
                        detail_recovered += 1
                except (requests.RequestException, ValueError):
                    pass
            if str(candidate.get("status") or "").strip().lower() not in live_statuses or match_half(candidate) is None:
                rejected += 1
                continue
            event_id = str(candidate["id"])
            if event_id not in known_ids:
                known_ids[event_id] = len(live_matches)
                live_matches.append(candidate)
                added += 1
            else:
                index = known_ids[event_id]
                existing = live_matches[index]
                if match_half(existing) is None or football_outcomes.score(existing) is None:
                    live_matches[index] = {**existing, **candidate}
                elif not existing.get("statistics") and isinstance(candidate.get("statistics"), dict):
                    live_matches[index] = {**existing, "statistics": candidate["statistics"]}
        print("Diagnostica dettagli live controllati:", detail_checked)
        print("Diagnostica dettagli live recuperati:", detail_recovered)
        print("Eventi live aggiunti dal catalogo:", added)
        print("Eventi catalogo scartati per stato/minuto:", rejected)
    except requests.RequestException as exc:
        print("Filtro live alternativo non disponibile:", type(exc).__name__)
    print("Partite con minuto valido:", sum(
        match_half(m) is not None for m in live_matches if isinstance(m, dict)
    ))

    ds = state.get("datasports", {})
    identity_at = football_datasports.kickoff(ds.get("identity_at"))
    if identity_at and 0 <= (now - identity_at).total_seconds() <= 1800:
        identities = ds.get("live_identity", [])
        pairs = football_datasports.associate(live_matches, identities)
        print("DataSports matched:", len(pairs), "unmatched:", len(identities) - len(pairs))
    for gid, record in state.get("poe3_signals", {}).items():
        if record.get("at") != now.isoformat() or record.get("telegram_attempted"):
            continue
        # Reserve before sending: never repeat an uncertain delivery.
        record["telegram_attempted"] = True
        save_state(state)
        message = ("⚽ POE 3.0 SPERIMENTALE\n"
                   + record["home"] + " - " + record["away"] + "\n"
                   + "Minuto: " + str(int(record["minute"])) + "\n"
                   + "Indice POE: " + str(record["rating"]) + "/100\n"
                   + "Obiettivo: gol entro 15 minuti di gioco\n"
                   + "Indice sperimentale, non probabilità di gol.")
        try:
            record["telegram_message_id"] = send_telegram(message)
            save_state(state)
            send_channel(record, message)
            save_state(state)
        except (requests.RequestException, RuntimeError, ValueError):
            print("Invio POE3 fallito")
    print("Diagnostica POE feed:", len(live_matches), "con statistiche:", sum(isinstance(m.get("statistics"), dict) and bool(m["statistics"]) for m in live_matches), "con dati completi:", sum(all(football_poe.side_stats(m, side) is not None for side in ("home", "away")) for m in live_matches))
    import football_coverage
    football_coverage.observe(state, live_matches, now, lambda m: all(football_poe.side_stats(m, side) is not None for side in ('home', 'away')))
    save_state(state)
    football_f1.settle(state, live_matches)
    football_f1.observe(state, live_matches, now, match_half)
    save_state(state)
    for eid, record in state.get('f1', {}).items():
        if record.get('delivery_attempted') or record.get('result') != 'pending':
            continue
        record['delivery_attempted'] = True
        save_state(state)
        p,t,x,red = record['stats']
        home,away = record['teams']
        a,b = record['score']
        message = (f"⚽ F1 — GOL FAVORITA SECONDO TEMPO\n{home} {a}-{b} {away}\n"
                   f"Favorita: {record['teams'][record['side']]} | Quota pre-match: {record['odd']:.2f}\n"
                   f"Minuto: {record['minute']} | Possesso: {p}% | Tiri in porta: {t} | xG: {x}\n"
                   "Pronostico sperimentale: gol della favorita entro fine partita.")
        try:
            record['telegram_message_id'] = send_telegram(message)
            record['telegram_text'] = message
            send_channel(record, message)
        except (requests.RequestException, RuntimeError, ValueError):
            print('F1 Telegram delivery failed')
        save_state(state)
    football_poe.observe(state, live_matches, now)
    save_state(state)
    for match in live_matches:
        record = state["poe"]["signals"].get(str(match["id"]))
        if not record or record.get("time") != now.isoformat() or record.get("telegram_message_id"):
            continue
        side = record["side"]
        home, away = match.get("home_team", "Casa"), match.get("away_team", "Trasferta")
        a, b = record["score"]
        try:
            message = (
                "🔥 POE - PRESSIONE OFFENSIVA ELEVATA (TEST)\n\n"
                f"{home} {a}-{b} {away}\n"
                f"Squadra in pressione: {home if side == 'home' else away}\n"
                f"Minuto: {record['minute']} | Indice: {record['rating']}/100\n"
                "Obiettivo sperimentale: gol entro 15 minuti di gioco."
            )
            record["telegram_message_id"] = send_telegram(message)
            save_state(state)
            send_channel(record, message)
            save_state(state)
        except (requests.RequestException, RuntimeError, ValueError):
            print("POE: invio Telegram non confermato")

    football_poe.observe_v2(state, live_matches, now)
    save_state(state)
    for match in live_matches:
        record = state["poe_v2"]["signals"].get(str(match.get("id")))
        if not record or record.get("time") != now.isoformat() or record.get("telegram_message_id"):
            continue
        home, away = match.get("home_team", "Casa"), match.get("away_team", "Trasferta")
        a,b=record["score"]
        h,v=record["deltas"]
        message=(
            "🔥 POE 2.0 - PRESSIONE OFFENSIVA (TEST)\n\n"
            f"{home} {a}-{b} {away}\n"
            f"Squadra dominante: {home if record['side']=='home' else away}\n"
            f"Minuto: {record['minute']} | Indice: {record['rating']}/100\n"
            f"Statistiche normalizzate a 10 minuti (finestra {record['window']}):\n"
            f"Tiri: {h[2]} - {v[2]} | In porta: {h[1]} - {v[1]}\n"
            f"xG: {h[0]} - {v[0]}\n"
            "Obiettivo: gol entro 15 minuti di gioco.\n"
        )
        record["telegram_text"]=message
        try:
            record["telegram_message_id"]=send_telegram(message+"⏳ Esito in attesa")
            save_state(state)
            send_channel(record,message+"⏳ Esito in attesa")
            save_state(state)
        except (requests.RequestException, RuntimeError, ValueError):
            print("POE 2.0: invio Telegram non confermato")
    for record in state["poe_v2"]["signals"].values():
        marker={"goal_observed":"✅ Gol osservato entro 15 minuti","no_goal_observed":"❌ Nessun gol entro 15 minuti","unknown":"⚪ Esito non verificabile"}.get(record["result"])
        if not marker or not record.get("telegram_text"):
            continue
        for chat,key in ((CHAT_ID,"telegram_message_id"),(CHANNEL_ID,"telegram_channel_message_id")):
            if not chat or not record.get(key) or record.get(key+"_marker")==marker:
                continue
            try:
                response=requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText",json={"chat_id":chat,"message_id":record[key],"text":record["telegram_text"]+marker},timeout=15)
                if response.ok and response.json().get("ok"):
                    record[key+"_marker"]=marker
                    save_state(state)
            except (requests.RequestException, ValueError):
                pass

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
                    football_f1.settle(state, results)
                    save_state(state)
                    if football_outcomes.update_halftime(state, results):
                        save_state(state)
                    if football_outcomes.update(state, results):
                        save_state(state)
            except requests.RequestException as exc:
                print("Esiti conclusi non disponibili:", day, type(exc).__name__)

    # Recover missed halftime snapshots, including already finished alerts.
    live_by_id = {str(m["id"]): m for m in live_matches if isinstance(m, dict) and m.get("id") is not None}
    checked_ht = set()
    incident_cache = {}
    for record in sorted(state["alert_outcomes"].values(), key=lambda r: r.get("sent_utc", ""), reverse=True):
        if record.get("half") != 1 or record.get("halftime_outcome", "pending") != "pending" or not record.get("telegram_message_id"):
            continue
        try:
            age = now - datetime.fromisoformat(record["sent_utc"])
        except (KeyError, ValueError, TypeError):
            continue
        if not timedelta(minutes=15) <= age <= timedelta(days=3):
            continue
        event_id = str(record["event_id"])
        match = live_by_id.get(event_id)
        if match and match_half(match) == 1:
            continue
        if event_id in checked_ht:
            continue
        if len(checked_ht) >= 8:
            break
        checked_ht.add(event_id)
        try:
            payload = api_get(f"events/{event_id}/incidents/")
            incident_cache[event_id] = payload
            ht = football_outcomes.incident_halftime(payload, event_id)
            if ht is not None and football_outcomes.update_halftime(state, [{"id": event_id, "half_time_score": ht}]):
                save_state(state)
        except (requests.RequestException, ValueError):
            continue

    # Recover official goal times, sharing the halftime incident request budget.
    if football_telegram_marker.recover_goal_minutes(
        state, live_by_id, now, api_get, incident_cache, checked_ht
    ):
        save_state(state)

    # Modifica soltanto messaggi gia' inviati, senza nuove notifiche.
    if football_telegram_marker.edit_pending(state, live_matches, TELEGRAM_TOKEN, CHAT_ID, CHANNEL_ID):
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

        scores = football_outcomes.score(match)
        if scores is None:
            live_missing_score += 1
            continue
        home_score, away_score = scores

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
        if record is not None:
            send_channel(record, message)
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
