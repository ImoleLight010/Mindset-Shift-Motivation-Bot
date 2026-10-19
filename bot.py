import json
import os
import sys
import datetime as dt
from zoneinfo import ZoneInfo

import requests

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")
API_KEY = os.environ["GEMINI_API_KEY"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
HISTORY_FILE = "history.json"

WAT = ZoneInfo("Africa/Lagos")

SLOTS = {
    "morning": (
        "Write an ORIGINAL morning motivational message for the 'Mindset Shift' community "
        "(7:00 AM). Start with a warm greeting and a fresh angle on mindset, discipline, "
        "gratitude, courage, or purpose. Give one practical idea for today. "
        "End with a short prompt inviting members to reply with their goal for the day."
    ),
    "afternoon": (
        "Write an ORIGINAL midday challenge for the 'Mindset Shift' community (12:00 PM). "
        "Give ONE small, specific, doable challenge members can complete before the day ends "
        "(mindset, habits, kindness, focus, health, or growth). Explain why it matters in "
        "1-2 lines. Ask members to reply 'Done' with how it went."
    ),
    "wisdom": (
        "Write an ORIGINAL wisdom post for the 'Mindset Shift' community (4:00 PM). "
        "Today's source: {source}. Share one idea from it, explain it simply, and show how to "
        "apply it today. Include a clear reference (e.g. book, chapter:verse for scripture; "
        "researcher/study name for science; philosopher and work for philosophy). "
        "ACCURACY RULES: only quote a verse or text word-for-word if you are certain it is "
        "exact; otherwise paraphrase and say 'paraphrased'. Never invent references or studies. "
        "Be respectful of all faiths. End with a question inviting members to share thoughts."
    ),
    "evening": (
        "Write an ORIGINAL evening reflection for the 'Mindset Shift' community (8:00 PM). "
        "Help members look back on their day with calm and honesty. Finish with ONE engaging, "
        "open-ended question that makes members want to reply and discuss with each other."
    ),
}

SOURCES = ["the Bible", "the Quran", "Science", "Philosophy"]

SYSTEM = (
    "You write content for a Telegram community called 'Mindset Shift' for Nigerian members. "
    "Tone: warm, uplifting, grounded, practical. Length: 70-150 words. Use a few fitting emojis. "
    "Plain text only: no markdown symbols like ** or #. Never repeat themes, openings, or "
    "quotes from the 'recent posts' list. Output ONLY the message text, nothing else."
)


def load_history():
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(h):
    with open(HISTORY_FILE, "w") as f:
        json.dump(h[-80:], f, ensure_ascii=False, indent=1)


def generate(slot, history):
    now = dt.datetime.now(WAT)
    instruction = SLOTS[slot]
    if slot == "wisdom":
        source = SOURCES[now.toordinal() % len(SOURCES)]
        instruction = instruction.format(source=source)

    recent = [h["text"] for h in history if h["slot"] == slot][-12:]
    recent_block = "\n---\n".join(r[:300] for r in recent) or "(none yet)"
    prompt = (
        f"Today is {now:%A, %d %B %Y} (Nigeria).\n\n{instruction}\n\n"
        f"Recent posts to avoid repeating:\n{recent_block}"
    )

    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        headers={"x-goog-api-key": API_KEY, "content-type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 1.0, "maxOutputTokens": 1500},
        },
        timeout=90,
    )
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts).strip()


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text[:4096]},
        timeout=30,
    )
    r.raise_for_status()


def main():
    slot = sys.argv[1] if len(sys.argv) > 1 else ""
    if slot not in SLOTS:
        sys.exit(f"Slot must be one of {list(SLOTS)}")
    history = load_history()
    text = generate(slot, history)
    send(text)
    history.append(
        {"date": dt.datetime.now(WAT).isoformat(timespec="minutes"), "slot": slot, "text": text}
    )
    save_history(history)
    print("Sent:", slot)


if __name__ == "__main__":
    main()
