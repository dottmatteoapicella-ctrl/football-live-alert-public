"""Edit the original Telegram alert once a later goal is verified."""
import requests

FINISHED = {"ft", "full-time", "full time", "finished", "ended", "aet", "after extra time"}
HALFTIME = {"ht", "half-time", "halftime", "half time"}


def desired_marker(record, match=None):
    """Return (marker, score) or None. H1 only counts goals before HT."""
    if not record.get("telegram_message_id") or not record.get("telegram_text"):
        return None
    initial = record.get("score_at_alert")
    if not isinstance(initial, list) or len(initial) != 2:
        return None
    half = record.get("half")
    if half == 1:
        if record.get("halftime_outcome") == "verified":
            final = record["halftime_score"]
            return ("✅ GOL DOPO L'ALERT (1° TEMPO)" if sum(final) > sum(initial)
                    else "❌ NESSUN GOL ENTRO L'INTERVALLO", final)
        # A live score is usable only while still in the first half.
        if match and str(match.get("status", "")).lower() not in HALFTIME | FINISHED:
            minute = str(match.get("current_minute", "")).split("+")[0].replace("'", "")
            if minute.isdigit() and 1 <= int(minute) <= 45:
                current = _score(match)
                if current and all(current[i] >= initial[i] for i in (0, 1)) and sum(current) > sum(initial):
                    return ("✅ GOL DOPO L'ALERT (1° TEMPO)", current)
    elif half == 2:
        # A verified final score is authoritative even after the game disappears
        # from the live feed (or when that feed still contains a stale snapshot).
        if record.get("outcome") == "verified":
            final = record.get("final_score")
            if isinstance(final, list) and len(final) == 2 and all(
                isinstance(v, int) and v >= initial[i] for i, v in enumerate(final)
            ):
                return ("✅ GOL DOPO L'ALERT (2° TEMPO)" if sum(final) > sum(initial)
                        else "❌ NESSUN GOL DOPO L'ALERT", final)
        current = _score(match) if match else None
        if current and all(current[i] >= initial[i] for i in (0, 1)):
            if sum(current) > sum(initial):
                return ("✅ GOL DOPO L'ALERT (2° TEMPO)", current)
    return None


def _score(match):
    try:
        values = [int(match["home_score"]), int(match["away_score"])]
        return values if min(values) >= 0 else None
    except (KeyError, ValueError, TypeError):
        return None


def official_goal_minutes(record, match):
    """Only use explicitly labelled goal events with an official minute.

    Never infer a scoring minute from the polling time or score change.
    """
    if not isinstance(match, dict):
        return []
    events = match.get("events")
    if not isinstance(events, list):
        return []
    import re
    alert = re.fullmatch(r"(\d{1,3})(?:\s*\+\s*(\d{1,2}))?'?", str(record.get("minute", "")).strip())
    if not alert:
        return []
    alert_time = (int(alert.group(1)), int(alert.group(2) or 0))
    result = []
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("type") or "").strip().lower()
        if kind not in {"goal", "own goal", "own_goal", "penalty goal"}:
            continue
        raw = event.get("minute")
        minute = re.fullmatch(r"(\d{1,3})(?:\s*\+\s*(\d{1,2}))?'?", str(raw).strip())
        if not minute:
            continue
        t = (int(minute.group(1)), int(minute.group(2) or 0))
        if t <= alert_time or (record.get("half") == 1 and t[0] > 45) or (record.get("half") == 2 and t[0] <= 45):
            continue
        label = f"{t[0]}'" if not t[1] else f"{t[0]}+{t[1]}'"
        result.append(label)
    return sorted(set(result), key=lambda s: tuple(int(x) for x in s.rstrip("\'").split("+")))


def recover_goal_minutes(state, live_by_id, now, api_get, cache, checked):
    """Backfill recent positive alerts with explicit incident goal minutes."""
    from datetime import datetime, timedelta
    changed = False
    for record in sorted(state.get("alert_outcomes", {}).values(),
                         key=lambda r: r.get("sent_utc", ""), reverse=True):
        try:
            age = now - datetime.fromisoformat(record["sent_utc"])
        except (KeyError, ValueError, TypeError):
            continue
        if not timedelta(0) <= age <= timedelta(days=3):
            continue
        eid = str(record.get("event_id"))
        result = desired_marker(record, live_by_id.get(eid))
        if not result or not result[0].startswith("✅"):
            continue
        expected = sum(result[1]) - sum(record["score_at_alert"])
        existing = record.get("official_goal_minutes", [])
        if len(existing) >= expected:
            continue
        if eid not in cache:
            if eid in checked or len(checked) >= 8:
                continue
            last = record.get("goal_incidents_checked_at")
            if last:
                try:
                    if now - datetime.fromisoformat(last) < timedelta(minutes=60):
                        continue
                except (ValueError, TypeError):
                    pass
            checked.add(eid)
            record["goal_incidents_checked_at"] = now.isoformat()
            changed = True
            try:
                cache[eid] = api_get(f"events/{eid}/incidents/")
            except (requests.RequestException, ValueError):
                continue
        payload = cache[eid]
        if not isinstance(payload, dict) or str(payload.get("event_id")) != eid:
            continue
        minutes = official_goal_minutes(record, {"events": payload.get("incidents")})
        if minutes:
            merged = list(dict.fromkeys(existing + minutes))
            if merged != existing:
                record["official_goal_minutes"] = merged
                changed = True
    return changed


def edit_pending(state, live_matches, token, chat_id):
    """Edit a single original message for the goal marker and later final score."""
    by_id = {str(m["id"]): m for m in live_matches if isinstance(m, dict) and m.get("id") is not None}
    changed = False
    for record in state.get("alert_outcomes", {}).values():
        if not record.get("telegram_message_id") or not record.get("telegram_text"):
            continue
        final = record.get("final_score") if record.get("outcome") == "verified" else None
        final_valid = (isinstance(final, list) and len(final) == 2 and
                       all(isinstance(v, int) and v >= 0 for v in final))
        result = desired_marker(record, by_id.get(str(record.get("event_id"))))
        marker = result[0] if result else record.get("telegram_marked")
        current = result[1] if result else None
        if not marker and not final_valid:
            continue
        original = record["telegram_text"]
        updated = original
        if marker:
            updated += "\n\n" + marker
            if current:
                updated += f"\n⚽ Punteggio: {current[0]}-{current[1]}"
        if marker and marker.startswith("✅"):
            minutes = official_goal_minutes(record, by_id.get(str(record.get("event_id"))))
            if minutes:
                record["official_goal_minutes"] = list(dict.fromkeys(record.get("official_goal_minutes", []) + minutes))
            minutes = record.get("official_goal_minutes", [])
            if minutes:
                updated += "\n⚽ Minuti gol (ufficiali): " + ", ".join(minutes)
        if final_valid:
            if not marker:
                updated += "\n\n"
            else:
                updated += "\n"
            updated += f"🏁 RISULTATO FINALE: {final[0]}-{final[1]}"
        if updated == record.get("telegram_last_text"):
            continue
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                data={"chat_id": chat_id, "message_id": record["telegram_message_id"], "text": updated},
                timeout=20,
            )
            response.raise_for_status()
            if not response.json().get("ok"):
                raise ValueError("Telegram edit not confirmed")
        except (requests.RequestException, ValueError) as exc:
            print("Modifica Telegram non riuscita:", record.get("event_id"), type(exc).__name__)
            continue
        record["telegram_last_text"] = updated
        if marker:
            record["telegram_marked"] = marker
        changed = True
        print("Messaggio Telegram aggiornato:", record.get("event_id"), marker, "finale:", final if final_valid else "non disponibile")
    return changed
