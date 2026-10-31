# Spec: kogu läbimine ja kinnitustega kaskaad

Classification: feature. Haru: `feature/library-cascade`.

## Eesmärk
Kõikidel võõrkeelsetel filmidel on `Film.et.srt`. Eestikeelsetele filmidele ei tehta midagi.

## Kaskaad (kulu kasvab)
1. **Tasuta, automaatne:** kohalik eestikeelne SRT, siis internetist (OpenSubtitles) eestikeelne SRT.
2. **Tõlge (DeepL + Claude):** inglise lähtetekstist (kohalik, videosse põimitud, internetist). Ainult kinnitusel.
3. **Kõne tuvastus (Whisper):** viimane variant. **Ainult selge kinnitusega, filmi kaupa**, kinnituse ees näidatakse kestust ja hinnangulist aega. Ei käivitu kunagi automaatselt.

## Käitumine
- `estsub.py <video|kaust>` (paremklõps): kaskaad 1, siis 2; kui allikat pole, küsib Whisperi kohta (`input()`, vaikimisi ei). Mitte-interaktiivses keskkonnas (stdin pole TTY) Whisperit ei käivitata.
- `estsub.py --library JUUR [JUUR...]` (uus), kolm faasi:
  - Faas 1: kõik videod (rekursiivselt), tasuta samm. Vahele jäetakse: `.et.srt` olemas, helikeel `est`, `--exclude` kaustad.
  - Faas 2: puudujääkide nimekiri, **üks kinnitus** kogu partii tõlkeks. Vastus `n` = ei tõlgita.
  - Faas 3: filmid, millel pole mingit allikat, Whisperi jaoks **filmi kaupa** kinnitus.
- `--yes-translate` (faas 2 automaatselt jah) on lubatud. Whisperile **sellist lippu ei ole**.

## Vastuvõtukriteeriumid
- AC1: ilma kinnituseta ei kutsuta `source.transcribe` kunagi (ühikutest).
- AC2: `get_source(..., allow_whisper=False)` tõstab `WhisperNeeded`, kui allikat pole.
- AC3: faas 3 jätab filmi vahele vastuse `n`/tühja või mitte-TTY korral.
- AC4: eestikeelse helirajaga video jäetakse vahele.
- AC5: faas 2 `n` korral ei kutsuta tõlget.

## Skoop
`source.py`, `estsub.py`, uus `library.py`, `tests/`, README.
