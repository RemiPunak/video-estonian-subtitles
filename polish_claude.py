"""Claude'i järeltoimetus Claude Code tellimuse kaudu (`claude -p`), API võtit pole vaja."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from pydantic import BaseModel

DEFAULT_MODEL = "claude-sonnet-5-5"
BATCH = 60
CONTEXT_LINES = 6
TIMEOUT = 600

SYSTEM = """Sa oled kogenud eesti filmitõlkija ja keeletoimetaja. Saad filmi subtiitrid partiidena: \
iga rida sisaldab id-d, ingliskeelset originaali (en) ja masintõlke mustandit (et).

Sinu ülesanne on anda iga id kohta lõplik eestikeelne subtiiter:
- Paranda grammatika: käänded, pöörded, ühildumine, sõnajärg (eesti keeles on öeldis lause teisel kohal), \
komad ja õigekiri.
- Lause võib jätkuda mitmes järjestikuses subtiitris. Loe neid koos ja sõnasta nii, et iga tükk ühendaks \
loomulikult järgmisega, kuid jäta iga tõlge oma id juurde. Ära liida subtiitreid ega tõsta teksti ühest \
id-st teise rohkem, kui lause loogika vältimatult nõuab.
- Kõnekeel peab kõlama loomulikult, nagu eesti näitleja seda ütleks, mitte nagu tõlge. Säilita \
tähendus, toon, huumor ja ropendamise tase.
- Sina või teie: vali kõnelejate suhte järgi ja hoia valik järjepidev.
- Nimed, kohad ja pärisnimed jäta originaalkujule ja kasuta eesti käändelõppe (nt Johnile, New Yorgis).
- Subtiiter peab olema lühike: kuni 2 rida, umbes 42 märki rea kohta. Vajadusel lühenda, aga ära kaota sisu. \
Ridu ära murra, selle teeb programm.
- Kui mustand on vale (valesti mõistetud idioom või kontekst), tõlgi originaali põhjal uuesti.
- Dialoogi kriipsud ("- ") säilita originaalis olevas kohas.

Tagasta täpselt samad id-d, mis said."""


class Line(BaseModel):
    id: int
    text: str


class Batch(BaseModel):
    lines: list[Line]


def _schema() -> str:
    schema = Batch.model_json_schema()
    # Lihtne, $ref-ideta skeem
    line = schema["$defs"]["Line"]
    line["additionalProperties"] = False
    return json.dumps({
        "type": "object",
        "properties": {"lines": {"type": "array", "items": line}},
        "required": ["lines"],
        "additionalProperties": False,
    })


def _claude_cmd(model: str) -> list[str]:
    exe = shutil.which("claude")
    if not exe:
        raise SystemExit("Claude Code CLI-d ('claude') ei leitud PATH-ist.")
    return [
        exe, "-p",
        "--model", model,
        "--effort", "medium",
        "--system-prompt", SYSTEM,
        "--json-schema", _schema(),
        "--output-format", "json",
        # Minimaalne kontekst: ilma tööriistade, MCP, pluginate ja seadeteta
        "--tools", "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--setting-sources", "",
        "--no-session-persistence",
    ]


def _ask(cmd: list[str], prompt: str) -> tuple[dict | None, str]:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # sunnib kasutama Claude Code tellimust
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
            env=env, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None, "aegus"
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, (proc.stderr or proc.stdout).strip()[:300]
    if out.get("is_error") or not isinstance(out.get("structured_output"), dict):
        return None, str(out.get("result") or out.get("subtype"))[:300]
    return out, ""


def polish(
    source: dict[int, str],
    draft: dict[int, str],
    title: str,
    model: str = DEFAULT_MODEL,
) -> dict[int, str]:
    cmd = _claude_cmd(model)
    ids = list(draft)
    result = dict(draft)
    failures = 0
    for start in range(0, len(ids), BATCH):
        chunk = ids[start : start + BATCH]
        prev = ids[max(0, start - CONTEXT_LINES) : start]
        payload = {
            "eelnev_kontekst_ainult_lugemiseks": [{"en": source[i], "et": result[i]} for i in prev],
            "toimeta": [{"id": i, "en": source[i], "et": draft[i]} for i in chunk],
        }
        prompt = f"Film: {title}\n\n" + json.dumps(payload, ensure_ascii=False, indent=1)
        out, err = _ask(cmd, prompt)
        if out is None:
            out, err = _ask(cmd, prompt)  # üks kordus
        if out is None:
            failures += 1
            print(f"  Claude viga, partii {start}: {err}. Jätan DeepL-i versiooni.")
            if failures >= 3:
                print("  Liiga palju vigu järjest (tellimuse limiit?). Ülejäänud read jäävad DeepL-i kujule.")
                break
            continue
        failures = 0
        got = {}
        for ln in out["structured_output"].get("lines", []):
            try:
                if str(ln["text"]).strip():
                    got[int(ln["id"])] = str(ln["text"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
        if set(got) != set(chunk):
            print(f"  Claude tagastas vale id-de hulga, partii {start}: kasutan vaid kattuvaid ridu.")
        for i in chunk:
            if i in got:
                result[i] = got[i]
        print(f"  Claude: {min(start + BATCH, len(ids))}/{len(ids)}")
    return result
