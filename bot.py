import io
import json
import os
import re
import sys
import datetime as dt
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

API_KEY = os.environ["GEMINI_API_KEY"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]  # one id, or several separated by commas
HISTORY_FILE = "history.json"
BASE = "https://generativelanguage.googleapis.com/v1beta"
WAT = ZoneInfo("Africa/Lagos")

# Which posts come with a picture card. Remove a name to make that post text-only.
PICTURE_SLOTS = {"morning", "afternoon", "wisdom", "evening"}
BRAND = "MINDSET SHIFT"

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

# Picture colours (top, bottom, glow) and the small label for each post.
THEMES = {
    "morning": ((24, 18, 96), (255, 170, 70), (255, 224, 140), "GOOD MORNING"),
    "afternoon": ((8, 70, 96), (60, 190, 170), (190, 255, 230), "MIDDAY CHALLENGE"),
    "wisdom": ((16, 28, 82), (120, 70, 150), (255, 214, 120), "WISDOM"),
    "evening": ((14, 12, 56), (70, 60, 140), (200, 190, 255), "EVENING REFLECTION"),
}

SYSTEM = (
    "You write content for a Telegram community called 'Mindset Shift' for Nigerian members. "
    "Tone: warm, uplifting, grounded, practical. Length: 70-150 words. Use a few fitting emojis. "
    "Plain text only: no markdown symbols like ** or #. Never repeat themes, openings, or "
    "quotes from the 'recent posts' list. "
    "OUTPUT FORMAT, exactly: the first line is 'CARD: ' followed by ONE short, powerful, "
    "original line of at most 14 words, taken from the heart of the message, to print on a picture "
    "(no emojis, no hashtags). Then a blank line. Then the full message. "
    "Output nothing else."
)


# ---------------------------------------------------------------- history
def load_history():
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(h):
    with open(HISTORY_FILE, "w") as f:
        json.dump(h[-80:], f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- AI
def version(name):
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


def candidate_models():
    """Ask Google which models exist right now, so a retired model name never breaks the bot."""
    if os.environ.get("MODEL"):
        return [os.environ["MODEL"]]
    try:
        r = requests.get(
            f"{BASE}/models?pageSize=200", headers={"x-goog-api-key": API_KEY}, timeout=20
        )
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        print("Model list problem:", type(e).__name__)
        return ["gemini-2.5-flash-lite", "gemini-2.5-flash"]
    names = [
        m["name"].split("/")[-1]
        for m in r.json().get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    flash = sorted(
        (n for n in names if re.fullmatch(r"gemini-[\d.]+-flash", n)), key=version, reverse=True
    )
    lite = sorted(
        (n for n in names if re.fullmatch(r"gemini-[\d.]+-flash-lite", n)),
        key=version,
        reverse=True,
    )
    others = [n for n in names if "flash" in n and n not in flash and n not in lite]
    result = lite + flash + others
    if not result:
        raise RuntimeError(f"No usable Gemini models found. Models seen: {names[:20]}")
    return result


def split_card(raw):
    """Return (card_line, message). Works even if the AI forgets the CARD line."""
    m = re.match(r"\s*CARD:\s*(.+?)\s*\n+", raw)
    if m:
        card, body = m.group(1).strip().strip('"\u201c\u201d'), raw[m.end():].strip()
        if len(body) >= 40 and 3 <= len(card.split()) <= 24:
            return card, body
    body = re.sub(r"^\s*CARD:.*\n+", "", raw).strip()
    sentences = re.split(r"(?<=[.!?])\s", body.replace("\n", " "))
    first = next((x.strip() for x in sentences if len(x.strip()) >= 30 and not re.match(r"(?i)(good morning|good evening|hello|hi)\b", x.strip())), "")
    return (first[:110] if len(first) > 10 else ""), body


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
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 1.0, "maxOutputTokens": 2500},
    }

    last_error = ""
    for model in candidate_models()[:6]:
        try:
            r = requests.post(
                f"{BASE}/models/{model}:generateContent",
                headers={"x-goog-api-key": API_KEY, "content-type": "application/json"},
                json=body,
                timeout=40,
            )
        except requests.exceptions.RequestException as e:
            last_error = f"{model}: {type(e).__name__}"
            print("Skipping", last_error)
            continue
        if r.status_code in (403, 404, 429, 500, 503):
            last_error = f"{model}: {r.status_code} {r.text[:300]}"
            print("Skipping", last_error)
            continue
        if not r.ok:
            raise RuntimeError(f"{model}: {r.status_code} {r.text[:500]}")
        candidates = r.json().get("candidates") or []
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        text = "".join(p.get("text", "") for p in parts).strip()
        if text:
            print("Used model:", model)
            return split_card(text)
        last_error = f"{model}: empty reply"
        print("Skipping", last_error)
    raise RuntimeError(f"All models failed. Last error: {last_error}")


# ---------------------------------------------------------------- picture
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/dejavu/", "/usr/share/fonts/TTF/"]


def font(name, size):
    for d in FONT_DIRS:
        try:
            return ImageFont.truetype(d + name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def clean_for_image(s):
    s = re.sub(r"[^\x20-\x7E\u2018\u2019\u201c\u201d\u2013\u2014\u2026\u00C0-\u024F]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def wrap(draw, text, fnt, max_w):
    lines, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if draw.textlength(t, font=fnt) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def make_card(slot, quote):
    top, bottom, glow, label = THEMES[slot]
    W = H = 1080
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    # soft glow
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(g)
    gd.ellipse([W // 2 - 380, 150 - 380, W // 2 + 380, 150 + 380], fill=glow + (120,))
    g = g.filter(ImageFilter.GaussianBlur(130))
    img = Image.alpha_composite(img.convert("RGBA"), g)
    d = ImageDraw.Draw(img)
    # label
    lf = font("DejaVuSans-Bold.ttf", 34)
    lw = d.textlength(label, font=lf) + 9 * (len(label) - 1)
    x = (W - lw) / 2
    for ch in label:
        d.text((x, 118), ch, font=lf, fill=glow + (255,))
        x += d.textlength(ch, font=lf) + 9
    d.line([(W // 2 - 60, 178), (W // 2 + 60, 178)], fill=glow + (255,), width=4)
    # quote, as large as fits
    quote = clean_for_image(quote)
    box_w, box_top, box_bottom = 800, 230, 900
    size, lines, qf = 92, [], None
    while size >= 40:
        qf = font("DejaVuSerif-Bold.ttf", size)
        lines = wrap(d, quote, qf, box_w)
        lh = int(size * 1.32)
        if len(lines) * lh <= box_bottom - box_top:
            break
        size -= 4
    lh = int(size * 1.32)
    y = box_top + ((box_bottom - box_top) - len(lines) * lh) / 2
    for line in lines:
        w = d.textlength(line, font=qf)
        d.text(((W - w) / 2 + 3, y + 4), line, font=qf, fill=(0, 0, 0, 110))
        d.text(((W - w) / 2, y), line, font=qf, fill=(255, 255, 255, 255))
        y += lh
    # footer
    ff = font("DejaVuSans-Bold.ttf", 36)
    fw = d.textlength(BRAND, font=ff) + 10 * (len(BRAND) - 1)
    x = (W - fw) / 2
    for ch in BRAND:
        d.text((x, 960), ch, font=ff, fill=(255, 255, 255, 235))
        x += d.textlength(ch, font=ff) + 10
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=92)
    return buf.getvalue()


# ---------------------------------------------------------------- Telegram
def send(text, photo=None):
    for chat_id in CHAT_ID.split(","):
        chat_id = chat_id.strip()
        if photo:
            caption = text if len(text) <= 1000 else ""
            r = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption},
                files={"photo": ("card.jpg", photo, "image/jpeg")},
                timeout=60,
            )
            if not r.ok:
                raise RuntimeError(f"Telegram photo error {r.status_code}: {r.text[:300]}")
            if caption:
                continue
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4096]},
            timeout=30,
        )
        if not r.ok:
            raise RuntimeError(f"Telegram error {r.status_code}: {r.text[:300]}")


def main():
    slot = sys.argv[1] if len(sys.argv) > 1 else ""
    if slot not in SLOTS:
        sys.exit(f"Slot must be one of {list(SLOTS)}")
    history = load_history()
    card, text = generate(slot, history)
    photo = None
    if slot in PICTURE_SLOTS and card:
        try:
            photo = make_card(slot, card)
        except Exception as e:  # never lose the post because of a picture problem
            print("Picture failed, sending text only:", type(e).__name__, e)
    send(text, photo)
    history.append(
        {"date": dt.datetime.now(WAT).isoformat(timespec="minutes"), "slot": slot, "text": text}
    )
    save_history(history)
    print("Sent:", slot, "(with picture)" if photo else "(text only)")


if __name__ == "__main__":
    main()
