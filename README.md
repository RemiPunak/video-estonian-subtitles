# estsub: eestikeelsed subtiitrid Plexi jaoks

Teeb video kõrvale faili `Film.et.srt`. Tõlke teeb DeepL partiidena, nii et lause kontekst säilib, ja seejärel parandab Claude eesti keele grammatika. Claude'i kasutatakse Claude Code tellimuse kaudu (`claude -p`), API võtit pole vaja.

## Ühekordne seadistus

```powershell
pip install -r requirements.txt
pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10"   # Whisper GPU jaoks
setx DEEPL_API_KEY "sinu-deepl-võti"
```

Pärast `setx` käsku ava uus terminal. Claude Code peab olema sisse logitud (`claude` → `/login`).

## Kasutamine

```powershell
python estsub.py "D:\Filmid\Film (2020)\Film.mkv"
python estsub.py "D:\Filmid"                          # kausta videod (alamkaustad jäävad vahele)
python estsub.py film.mkv --source film.en.srt        # konkreetne lähtefail
python estsub.py film.mkv --engine deepl              # ilma Claude'ita
python estsub.py film.mkv --keep-intermediate --force # vahefailid kausta _work/
```

**Kõigepealt otsitakse video kaustast ja alamkaustadest** (kuni 2 taset, nt `Subs/2_English.srt` või `Subs/<video nimi>/…`) eestikeelset `.srt`-faili (`est`, `estonian`, `eesti` nimes). Kui see leitakse, kopeeritakse see `Film.et.srt`-ks ja tõlkimist ei tehta.

Seejärel otsitakse internetist (OpenSubtitles) valmis eestikeelseid subtiitreid. Leitud fail sünkroonitakse automaatselt videoga: kiirus (PAL 25 fps ↔ 23,976 fps) ja nihe leitakse videos oleva subtiitriraja (ka PGS) või kõnelõikude järgi. Kui sobiv fail leitakse, tõlkimist ei tehta. Lipp `--no-online` lülitab otsingu välja.

Kui eestikeelset faili pole, valitakse tõlke lähe järgmises järjekorras:
1. Video kaustas või alamkaustas on ingliskeelne `.srt` (`Film.en.srt`, `Film.srt`, `Subs/2_English.srt`). Eelistatakse mitte-forced ja mitte-SDH faili.
2. Videos on ingliskeelne tekstipõhine subtiitrirada (SRT, ASS või mov_text). PGS/VobSub pilt-subtiitreid see ei kasuta.
3. Internetist leitud ingliskeelne subtiiter, mis sünkroonitakse samamoodi.
4. Kui midagi pole, pakub skript Whisper `large-v3` kõnetuvastust GPU peal, **ainult kinnitusega** (vaikimisi ei). Esimesel käivitusel laaditakse alla umbes 3 GB mudelit.

## Kogu läbimine (`--library`)

```powershell
python estsub.py --library "D:\Torrent\Video" "E:\TorrentE" --exclude "E:\TorrentE\Game"
```

Kolm faasi, kulu kasvab ja iga kulukas samm vajab kinnitust:

1. **Tasuta, automaatne:** kohalik või internetist eestikeelne SRT kõigile videotele. Vahele jäävad videod, millel on juba `.et.srt` või mille heli on eesti keeles.
2. **Tõlge (üks kinnitus kogu partii kohta):** näitab nimekirja ja küsib `[j/N]`. `--yes-translate` kinnitab selle automaatselt.
3. **Whisper kõnetuvastus (kinnitus filmi kaupa):** näitab kestust ja hinnangulist aega. Automaatselt **ei käivitu kunagi**, ka mitte mitte-interaktiivses keskkonnas, ja sellele pole `--yes` lippu.

Ühe filmi režiimis (paremklõps) kehtib Whisperi kohta sama kinnitus.

Testid: `python -m unittest tests.test_cascade -v`

## Ühe klikiga (Exploreri paremklikk)

Paigaldus (ühekordne, admin-õigusi pole vaja):
```powershell
powershell -ExecutionPolicy Bypass -File install_context_menu.ps1
```
Seejärel tee filmi kaustas tühjal alal, kausta peal või videofaili peal paremklikk → **Show more options** → **Tee eesti subtiitrid**. Avaneb aken, kus on näha töö käik. Eemaldamine: lisa käsule lipp `-Uninstall`.

## Plex

1. Settings → Agents (või raamatukogu seaded) → **Local Media Assets** peab olema sisse lülitatud.
2. Vali film → `…` → **Refresh Metadata**.
3. Mängimise ajal vali subtiitrite menüüst **Estonian (SRT)**.

## Kulu

- DeepL Free annab 500 000 märki kuus. Üks film võtab umbes 40 000–80 000 märki.
- Claude kulutab Claude Code tellimuse kasutuslimiiti: umbes 25 päringut filmi kohta (60 subtiitrit päringus). Kui limiit saab täis, jäävad ülejäänud read DeepL-i kujule. Mudeli saab vahetada lipuga `--model` (nt `claude-opus-5-5`).
