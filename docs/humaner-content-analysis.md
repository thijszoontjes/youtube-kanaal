# Analyse: meer menselijk, minder AI-slop (gratis)

## Wat ik in je huidige pipeline zie
- Je prompt stuurt al op "human, natural, unscripted" en verbiedt mechanische lijstjes, maar de structuur blijft nog streng (exact 3 facts, vaste JSON, veel constraints). Dat vergroot het risico op herkenbare AI-ritmes.
- Je Shorts mikken op ~45-90 woorden (20-35 sec). Dat is goed voor retentie, maar té strak kan monotone cadans geven.
- Je gebruikt vooral 1 stemprofiel (`KOKORO_VOICE=af_heart`) en vaste snelheid (`KOKORO_SPEED=1.05`), waardoor meerdere uploads hetzelfde klinken.
- Je duplicate guard gebruikt titel/topic-similarity; goed tegen herhaling, maar nog niet tegen "zelfde vertelvorm".

## Verbeteringen (gratis) met impact

| Prioriteit | Actie | Impact op "menselijk" | Moeite | Kosten |
|---|---|---:|---:|---:|
| 1 | Voeg 3-5 **narration styles** toe en roteer per video | **Groot (9/10)** | Laag | Gratis |
| 2 | Gebruik **eigen stemreferenties** met XTTS (korte echte spraak) | **Groot (8.5/10)** | Medium | Gratis |
| 3 | Maak intro/outro minder template-achtig via prompt-variatie | **Groot (8/10)** | Laag | Gratis |
| 4 | Varieer spreektempo en micro-pauzes per run | **Middel-groot (7.5/10)** | Laag | Gratis |
| 5 | Voeg menselijke imperfectie toe in script (lichte spreektaal) | **Middel (7/10)** | Laag | Gratis |
| 6 | Gebruik minder generieke B-roll query's, meer concrete scènewoorden | **Middel (6.5/10)** | Medium | Gratis |
| 7 | A/B-test titels op hook-type i.p.v. alleen onderwerp | **Middel (6/10)** | Medium | Gratis |

---

## Concreet uitvoerplan (zonder betaalde tools)

### 1) Roteer vertelstijl (hoogste ROI)
Maak 4 stijlen en laat per run 1 stijl kiezen:
- "Curious storyteller"
- "Fast myth-buster"
- "Calm explainer"
- "Personal discovery tone"

**Waarom:** AI-slop klinkt vaak hetzelfde qua ritme. Stijlrotatie breekt dat patroon direct.

### 2) Menselijkere voice-over
- Gebruik XTTS met 3-5 korte eigen voice clips (verschillende dagen/energie).
- Zet `XTTS_MAX_REFERENCE_CLIPS=4` of `5`.
- Houd clips schoon (weinig ruis), 8-20 sec per clip.

**Waarom:** echte micro-intonatie + ademhaling maakt het direct minder "synthetisch".

### 3) Prompt aanpassen voor natuurlijke variatie
Voeg regels toe zoals:
- "Gebruik soms een korte zelf-correctie (bijv. 'nou ja, beter gezegd...') max 1x."
- "Varieer openingsvorm: vraag, stelling, mini-scène of contrast."
- "Vermijd perfecte parallelle zinsstructuren."

**Waarom:** AI-output verraadt zich in te nette symmetrie.

### 4) Tempo per video licht randomizen
Voor Kokoro/Piper:
- random speed in kleine band (bijv. 0.97-1.08)
- sentence silence licht variëren

**Waarom:** identieke pace over tientallen video's klinkt machinematig.

### 5) B-roll minder stock-achtig laten voelen
Laat queries niet alleen topic-level zijn, maar scene-level:
- "deep sea vent" -> "underwater black smoker close-up bubbles"
- "saturn" -> "night sky telescope backyard amateur"

**Waarom:** specifieke beelden voelen minder "generic AI montage".

---

## Aanbevolen instellingen om eerst te testen

1. **Voice menselijker**
```dotenv
NARRATION_ENGINE=xtts
XTTS_MAX_REFERENCE_CLIPS=5
XTTS_REFERENCE_MAX_SECONDS=20
XTTS_FALLBACK_TO_PIPER=true
```

2. **Als je Kokoro houdt**
```dotenv
NARRATION_ENGINE=kokoro
KOKORO_SPEED=1.00
```
En varieer speed handmatig per dag tussen 0.98 en 1.08.

3. **Shorts iets losser timen**
```dotenv
MIN_SHORT_DURATION_SECONDS=22
MAX_SHORT_DURATION_SECONDS=38
```

---

## 14-dagen meetplan (gratis)
Track per upload:
- 3-sec hold rate
- average view duration
- viewed vs swiped away
- comments per 1k views

Doel na 14 dagen:
- +8% tot +20% op 3-sec hold
- +5% tot +15% op average view duration
- duidelijke daling van reacties als "AI voice" / "robotic"

---

## Volgorde die ik adviseer
1. Eerst voice (XTTS + eigen clips)
2. Dan prompt-variatie
3. Dan tempo-variatie
4. Dan scene-level visual query's
5. Dan titelhooks A/B

Als je wilt, kan ik dit in de code direct voor je inbouwen met:
- stijlrotatie in prompts,
- kleine tempo-randomizer,
- en een "humanization profile" per run.
