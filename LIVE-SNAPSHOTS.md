# Live Snapshots — Documentație

## Ce este sistemul de Live Snapshots?

API-ul UEFA Fantasy oferă doar **totaluri cumulative** pentru goluri, assisturi, clean sheets etc. — nu are date defalcate pe matchday. Pentru a vedea ce se întâmplă **în timpul meciurilor** (cine marchează, cine asistează), am creat un sistem de **checkpoint-uri live** care salvează snapshoturi la momente cheie ale fiecărui matchday.

## Cum funcționează

### Cele 5 checkpoint-uri per matchday

| # | Label | Descriere | Moment tipic |
|---|-------|-----------|-------------|
| 1 | **HTM1** | Half Time Meci 1 | Pauza primei grupe de meciuri (~19:30) |
| 2 | **FTM1** | Full Time Meci 1 | Finalul primei grupe (~20:45) |
| 3 | **HTM2** | Half Time Meci 2 | Pauza celei de-a doua grupe (~21:45) |
| 4 | **FTM2** | Full Time Meci 2 | Finalul celei de-a doua grupe (~23:00) |
| 5 | **FINALMD** | Final Matchday | Dimineața următoare (~07:00), după ce toate datele sunt finalizate |

Orele sunt **configurabile** — se setează din UI-ul din tab-ul "Stats MD".

### Flow-ul automat

```
MD11 schedule setat cu ore →
  HTM1 fired → snapshot salvat →
  FTM1 fired → snapshot salvat →
  HTM2 fired → snapshot salvat →
  FTM2 fired → snapshot salvat →
  FINALMD fired → snapshot salvat → AUTO-ADVANCE la MD12 →
  toate checkpoint-urile resetate →
  gata pentru MD12
```

Când **FINALMD** se declanșează, sistemul:
1. Salvează ultimul checkpoint
2. Incrementează matchday-ul (MD11 → MD12)
3. Resetează toate flag-urile `fired` la `false`
4. Păstrează aceleași ore (se pot modifica manual pentru noul MD)

## Fișiere pe GitHub

### `snapshots/schedule.json`
Programul curent de checkpoint-uri. Un singur fișier, mereu pentru MD-ul activ.

```json
{
  "matchday": 11,
  "checkpoints": [
    { "time": "19:30", "label": "HTM1", "fired": false },
    { "time": "20:45", "label": "FTM1", "fired": false },
    { "time": "21:45", "label": "HTM2", "fired": true },
    { "time": "23:00", "label": "FTM2", "fired": false },
    { "time": "07:00", "label": "FINALMD", "fired": false }
  ]
}
```

### `snapshots/mdXX_live.json`
Fișierul live per matchday. Conține **toate** checkpoint-urile salvate, acumulativ.

```json
{
  "matchday": 11,
  "checkpoints": [
    {
      "label": "HTM1",
      "savedAt": "2026-03-03T19:30:45",
      "managers": [
        {
          "guid": "...",
          "username": "Dan",
          "teamName": "The D",
          "gdPoints": 12,
          "gdRank": 50000,
          "ovPoints": 845,
          "ovRank": 4800
        }
      ],
      "players": [
        {
          "id": 250076574,
          "name": "K. Mbappé",
          "fullName": "Kylian Mbappé",
          "team": "Real Madrid",
          "teamCode": "RMA",
          "posCode": "FWD",
          "totPts": 95,
          "curGDPts": 13,
          "goals": 14,
          "assists": 1,
          "cleanSheets": 0,
          "momCount": 4,
          "yellowCards": 1,
          "redCards": 0,
          "value": 11.2,
          "selPer": 75
        }
      ]
    }
  ]
}
```

### `snapshots/mdXX.json` (existent, neschimbat)
Snapshotul **principal** de pre-matchday. Se salvează manual înainte de începutul MD-ului. Folosit pentru calculul general MD vs MD.

## Cum se calculează diferențele

### Între checkpoint-uri (live)
```
Goluri în HT1 = checkpoint[HTM1].player.goals - md10.player.goals
Goluri în FT1 = checkpoint[FTM1].player.goals - checkpoint[HTM1].player.goals
```

Primul checkpoint se compară cu snapshotul MD anterior (`mdXX.json`).
Checkpoint-urile următoare se compară cu primul checkpoint din sesiune.

### Între matchday-uri (principal)
```
Goluri în MD11 = md11.player.goals - md10.player.goals
```
Acest calcul rămâne neschimbat.

## API Endpoints

| Endpoint | Metodă | Descriere |
|----------|--------|-----------|
| `/api/live-schedule` | GET | Returnează schedule-ul curent |
| `/api/live-schedule` | POST | Setează schedule (body: `{matchday, checkpoints}`) |
| `/api/live-snapshot/load?md=11` | GET | Încarcă `md11_live.json` |
| `/api/live-snapshot/fire?md=11&label=HTM1` | POST | Declanșează manual un checkpoint |

## Cum se folosește

### 1. Configurarea programului
1. Mergi la tab-ul **Stats MD**
2. În secțiunea **Auto-Snapshot Schedule**, setează orele pentru fiecare checkpoint
3. Apasă **Salvează program**

### 2. Fire manual
Dacă vrei să salvezi un checkpoint imediat (fără a aștepta ora programată):
1. Selectează label-ul din dropdown (HTM1, FTM1, etc.)
2. Apasă **Fire manual**

### 3. Vizualizarea checkpoint-urilor
1. Secțiunea **Live Checkpoints** arată pills-uri pentru fiecare checkpoint salvat
2. Click pe un pill pentru a vedea diferențele de goluri/assisturi/puncte
3. Se afișează atât progresul managerilor cât și al jucătorilor

### 4. Scheduler automat
Serverul verifică la fiecare 30 de secunde dacă ora curentă se potrivește cu un checkpoint neprogramat. Dacă da, salvează automat. După FINALMD, avansează la MD-ul următor.

## Troubleshooting

- **Schedule nu se salvează**: Verifică `GITHUB_TOKEN` în variabilele de mediu pe Render
- **Checkpoint-uri nu se declanșează automat**: Verifică fusul orar al serverului Render (UTC) — orele din schedule trebuie să fie în timezone-ul serverului
- **Datele nu se actualizează**: API-ul UEFA poate avea întârzieri de câteva minute — checkpoint-ul la pauza ar trebui pus la ~min 50, nu min 45
- **FINALMD nu avansează**: Verifică logs-urile pe Render pentru erori la `advance_to_next_md()`

## Notă despre timezone
Serverul pe Render rulează în **UTC**. Când setezi orele, ține cont de diferența față de ora locală (România = UTC+2 iarna, UTC+3 vara).

Exemplu pentru meciuri cu kickoff 19:45 și 22:00 ora României (UTC+2):
- HTM1: 18:30 (UTC) = 20:30 (RO)
- FTM1: 19:45 (UTC) = 21:45 (RO)
- HTM2: 20:45 (UTC) = 22:45 (RO)
- FTM2: 22:00 (UTC) = 00:00 (RO)
- FINALMD: 05:00 (UTC) = 07:00 (RO)
